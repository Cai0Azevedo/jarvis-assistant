import threading
import time
from collections import deque
from typing import Any, Optional

from aiCore.eventBus import EventBus, eventBus
from appConfig.settings import Settings, settings

try:
    import serial
    from serial import SerialException
except ImportError:
    serial = None

    class SerialException(OSError):
        """Fallback de exceção serial quando pyserial não está instalado."""


class SerialConnection:
    """Singleton thread-safe para comunicação USB com Arduino UNO (com fallback em modo mock)."""

    _instance: Optional["SerialConnection"] = None
    _instanceLock: threading.Lock = threading.Lock()
    _initialized: bool = False

    DEFAULT_BAUD_RATE: int = 115200
    ARDUINO_RESET_DELAY_SECONDS: float = 2.0

    def __new__(cls, *args: Any, **kwargs: Any) -> "SerialConnection":
        if cls._instance is None:
            with cls._instanceLock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        comPort: Optional[str] = None,
        baudRate: Optional[int] = None,
        timeout: Optional[float] = None,
        bus: Optional[EventBus] = None,
        appSettings: Optional[Settings] = None,
    ) -> None:
        if self._initialized:
            return
        with self._instanceLock:
            if self._initialized:
                return

            self.settings: Settings = appSettings or settings
            self.eventBus: EventBus = bus or eventBus

            self.comPort: str = comPort or self.settings.comPort
            self.baudRate: int = (
                baudRate
                if baudRate is not None
                else (self.settings.baudRate or self.DEFAULT_BAUD_RATE)
            )
            self.timeout: float = (
                timeout if timeout is not None else self.settings.serialTimeout
            )

            self._ioLock: threading.Lock = threading.Lock()
            self._serial: Any = None
            self._rxBuffer: bytearray = bytearray()
            self.isConnected: bool = False
            self.isMockMode: bool = False
            self._warnedMockMode: bool = False

            self._mockTxHistory: deque[str] = deque(maxlen=100)
            self._mockRxQueue: deque[str] = deque(maxlen=100)
            self._initialized = True

            self.connect()

    @property
    def is_mock(self) -> bool:
        """Indica se a conexão está operando em modo simulação/mock."""
        return self.isMockMode

    @is_mock.setter
    def is_mock(self, value: bool) -> None:
        self.isMockMode = bool(value)

    def _openSerialUnlocked(self) -> bool:
        """Tenta abrir a porta serial mantendo ``_ioLock`` já adquirido."""
        if serial is None:
            self._enterMockModeUnlocked(
                "Biblioteca 'pyserial' não instalada no ambiente virtual."
            )
            return False

        try:
            self._serial = serial.Serial(
                port=self.comPort,
                baudrate=self.baudRate,
                timeout=self.timeout,
                write_timeout=self.timeout,
            )
            # O Arduino UNO reinicia via DTR ao abrir a porta USB; aguarda o bootloader estabilizar.
            time.sleep(self.ARDUINO_RESET_DELAY_SECONDS)
            self._rxBuffer.clear()
            self.isConnected = True
            self.isMockMode = False
            self._warnedMockMode = False
            print(
                f"[INFO][SerialConnection] Conectado ao Arduino UNO em "
                f"{self.comPort} ({self.baudRate} baud)."
            )
            self._publishConnectionState(connected=True, mockMode=False)
            return True
        except (SerialException, OSError, Exception) as error:
            self._closeSerialHandleUnlocked()
            self._enterMockModeUnlocked(
                f"Porta {self.comPort} indisponível ({error})."
            )
            return False

    def connect(self, forceReconnect: bool = False) -> bool:
        """Tenta abrir a porta serial do Arduino UNO; entra em modo simulação/mock se falhar."""
        with self._ioLock:
            if self.isOpenUnlocked() and not forceReconnect:
                return True

            if forceReconnect and self._serial is not None:
                self._closeSerialHandleUnlocked()

            return self._openSerialUnlocked()

    def disconnect(self) -> None:
        """Fecha a porta serial com segurança e limpa os buffers internos."""
        with self._ioLock:
            self._closeSerialHandleUnlocked()
            self.isMockMode = False
            self._rxBuffer.clear()

    def close(self) -> None:
        """Alias compatível para ``disconnect()``."""
        self.disconnect()

    def isOpen(self) -> bool:
        """Retorna True se a porta serial física estiver aberta ou em modo mock ativo."""
        with self._ioLock:
            return self.isOpenUnlocked()

    def isOpenUnlocked(self) -> bool:
        """Verifica estado da porta mantendo ``_ioLock`` adquirido."""
        return (
            self.isConnected
            and self._serial is not None
            and bool(getattr(self._serial, "is_open", True))
        )

    def send(self, data: str) -> bool:
        """Envia uma mensagem pela porta serial de forma thread-safe ou simula no console."""
        formattedPacket: str = data if data.endswith("\n") else f"{data}\n"

        with self._ioLock:
            if not self.isOpenUnlocked() and not self.isMockMode:
                self._openSerialUnlocked()

            if self.isMockMode or not self.isOpenUnlocked():
                self._recordMockTxUnlocked(formattedPacket)
                return True

            try:
                payloadBytes: bytes = formattedPacket.encode("ascii", errors="replace")
                self._serial.write(payloadBytes)
                self._serial.flush()
                return True
            except (SerialException, OSError, Exception) as error:
                self._closeSerialHandleUnlocked()
                self._enterMockModeUnlocked(
                    f"Falha ao gravar na porta {self.comPort} ({error})."
                )
                self._recordMockTxUnlocked(formattedPacket)
                return True

    def sendPacket(self, packet: str) -> bool:
        """Alias compatível para ``send(data)``."""
        return self.send(packet)

    def readLine(self) -> Optional[str]:
        """Lê uma linha completa da porta serial sem bloquear escritas concorrentes."""
        with self._ioLock:
            if self.isMockMode or not self.isOpenUnlocked():
                if self._mockRxQueue:
                    return self._mockRxQueue.popleft()
                return None

            try:
                waitingBytes: int = int(getattr(self._serial, "in_waiting", 0) or 0)
                if waitingBytes > 0:
                    chunk: bytes = self._serial.read(waitingBytes)
                    if chunk:
                        self._rxBuffer.extend(chunk)

                newlineIndex: int = self._rxBuffer.find(b"\n")
                if newlineIndex == -1:
                    return None

                rawLine: bytes = bytes(self._rxBuffer[: newlineIndex + 1])
                del self._rxBuffer[: newlineIndex + 1]
                decoded: str = rawLine.decode("utf-8", errors="ignore").strip()
                return decoded if decoded else None
            except (SerialException, OSError, Exception) as error:
                self._closeSerialHandleUnlocked()
                self._enterMockModeUnlocked(
                    f"Falha ao ler da porta {self.comPort} ({error})."
                )
                return None

    def enqueueMockInput(self, packet: str) -> None:
        """Injeta um pacote simulado na fila de recepção para testes sem Arduino físico."""
        cleanPacket: str = packet.strip()
        if not cleanPacket:
            return
        with self._ioLock:
            self._mockRxQueue.append(cleanPacket)

    def getMockTxHistory(self) -> list[str]:
        """Retorna os últimos pacotes registrados em modo simulação/mock."""
        with self._ioLock:
            return list(self._mockTxHistory)

    def clearMockTxHistory(self) -> None:
        """Limpa o histórico de pacotes simulados (útil em testes unitários)."""
        with self._ioLock:
            self._mockTxHistory.clear()

    def getStatus(self) -> dict[str, Any]:
        """Retorna o estado atual da conexão serial."""
        with self._ioLock:
            return {
                "comPort": self.comPort,
                "baudRate": self.baudRate,
                "isOpen": self.isOpenUnlocked(),
                "isConnected": self.isConnected,
                "isMockMode": self.isMockMode,
                "is_mock": self.isMockMode,
                "pyserialAvailable": serial is not None,
            }

    def _recordMockTxUnlocked(self, formattedPacket: str) -> None:
        """Registra e exibe no console o pacote simulado mantendo ``_ioLock`` adquirido."""
        cleanPacket: str = formattedPacket.rstrip("\r\n")
        self._mockTxHistory.append(cleanPacket)
        print(f"[MOCK][Arduino TX] {cleanPacket}")

    def _enterMockModeUnlocked(self, reason: str) -> None:
        """Ativa o modo simulação/mock sem interromper o fluxo do assistente."""
        wasConnected: bool = self.isConnected
        self.isConnected = False
        self.isMockMode = True
        if not self._warnedMockMode:
            print(
                f"[AVISO][SerialConnection] {reason} "
                "Operando em modo simulação/mock (sem hardware físico)."
            )
            self._warnedMockMode = True
        if wasConnected:
            self._publishConnectionState(connected=False, mockMode=True)

    def _closeSerialHandleUnlocked(self) -> None:
        """Fecha o descritor serial interno mantendo o lock já adquirido."""
        if self._serial is not None:
            try:
                if getattr(self._serial, "is_open", False):
                    self._serial.close()
            except Exception:
                pass
            finally:
                self._serial = None
        self.isConnected = False

    def _publishConnectionState(self, connected: bool, mockMode: bool) -> None:
        """Notifica mudanças no estado da conexão USB pelo EventBus."""
        self.eventBus.publish(
            "arduinoConnectionChanged",
            {
                "comPort": self.comPort,
                "baudRate": self.baudRate,
                "isConnected": connected,
                "isMockMode": mockMode,
            },
        )


serialConnection: SerialConnection = SerialConnection()
