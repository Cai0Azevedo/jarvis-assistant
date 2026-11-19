import select
import socket
import threading
from collections import deque
from typing import Any, Optional

from aiCore.eventBus import EventBus, eventBus
from appConfig.loggingConfig import getLogger
from appConfig.settings import Settings, settings

logger = getLogger("jarvis.networkConnection")


class NetworkConnection:
    """Conexão TCP Socket thread-safe para Ponte Wi-Fi ESP-01 + Arduino UNO (Subfase 3.1)."""

    DEFAULT_HOST: str = "192.168.7.5"
    DEFAULT_PORT: int = 8080
    DEFAULT_TIMEOUT: float = 3.0
    RECV_BUFFER_SIZE: int = 1024

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        timeout: Optional[float] = None,
        bus: Optional[EventBus] = None,
        appSettings: Optional[Settings] = None,
        autoConnect: bool = False,
    ) -> None:
        self.settings: Settings = appSettings or settings
        self.eventBus: EventBus = bus or eventBus
        self.connectionType: str = "wifi"

        self.host: str = (host or self.settings.arduinoHost or self.DEFAULT_HOST).strip()
        self.port: int = (
            int(port)
            if port is not None
            else int(self.settings.arduinoPort or self.DEFAULT_PORT)
        )
        self.timeout: float = (
            float(timeout)
            if timeout is not None
            else float(self.settings.arduinoSocketTimeout or self.DEFAULT_TIMEOUT)
        )

        self._lock: threading.Lock = threading.Lock()
        self._ioLock: threading.Lock = self._lock
        self._socket: Optional[socket.socket] = None
        self._rxBuffer: bytearray = bytearray()

        self.isConnected: bool = False
        self.isMockMode: bool = False
        self._warnedMockMode: bool = False

        self._mockTxHistory: deque[str] = deque(maxlen=100)
        self._mockRxQueue: deque[str] = deque(maxlen=100)

        if autoConnect:
            self.connect()

    @property
    def is_mock(self) -> bool:
        """Indica se a conexão está operando em modo simulação/mock."""
        return self.isMockMode

    @is_mock.setter
    def is_mock(self, value: bool) -> None:
        self.isMockMode = bool(value)

    def _openSocketUnlocked(self) -> bool:
        """Estabelece o socket TCP mantendo ``_lock`` já adquirido."""
        sock: Optional[socket.socket] = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))

            self._socket = sock
            self._rxBuffer.clear()
            self.isConnected = True
            self.isMockMode = False
            self._warnedMockMode = False
            logger.info(
                "Conectado ao Arduino/ESP-01 via Wi-Fi em %s:%s (timeout=%ss).",
                self.host,
                self.port,
                self.timeout,
            )
            self._publishConnectionState(connected=True, mockMode=False)
            return True
        except (ConnectionRefusedError, TimeoutError, socket.timeout, OSError, Exception) as error:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            self._closeSocketUnlocked()
            self._enterMockModeUnlocked(
                f"Servidor TCP {self.host}:{self.port} indisponível ({error})."
            )
            return False

    def connect(self, forceReconnect: bool = False) -> bool:
        """Estabelece a conexão TCP com o ESP-01; entra em modo Mock se falhar."""
        with self._lock:
            if self.isOpenUnlocked() and not forceReconnect:
                return True

            if forceReconnect and self._socket is not None:
                self._closeSocketUnlocked()

            return self._openSocketUnlocked()

    def disconnect(self) -> None:
        """Encerra o socket TCP com segurança e reseta as flags de estado."""
        with self._lock:
            self._closeSocketUnlocked()
            self.isMockMode = False
            self._rxBuffer.clear()

    def close(self) -> None:
        """Alias compatível para ``disconnect()``."""
        self.disconnect()

    def isOpen(self) -> bool:
        """Retorna True se o socket TCP estiver conectado e ativo."""
        with self._lock:
            return self.isOpenUnlocked()

    def isOpenUnlocked(self) -> bool:
        """Verifica se o socket está aberto mantendo ``_lock`` adquirido."""
        return self.isConnected and self._socket is not None

    def send(self, message: str) -> bool:
        """Envia pacote via TCP (sendall) de forma thread-safe ou simula em modo Mock."""
        formattedPacket: str = message if message.endswith("\n") else f"{message}\n"

        with self._lock:
            if not self.isOpenUnlocked() and not self.isMockMode:
                self._openSocketUnlocked()

            if self.isMockMode or not self.isOpenUnlocked() or self._socket is None:
                self._recordMockTxUnlocked(formattedPacket)
                return True

            try:
                payloadBytes: bytes = formattedPacket.encode("ascii", errors="replace")
                self._socket.sendall(payloadBytes)
                return True
            except (
                BrokenPipeError,
                ConnectionResetError,
                ConnectionAbortedError,
                TimeoutError,
                socket.timeout,
                OSError,
                Exception,
            ) as error:
                self._closeSocketUnlocked()
                self._enterMockModeUnlocked(
                    f"Desconexão ao enviar para {self.host}:{self.port} ({error})."
                )
                self._recordMockTxUnlocked(formattedPacket)
                return True

    def sendPacket(self, packet: str) -> bool:
        """Alias compatível para ``send(message)``."""
        return self.send(packet)

    def read(self) -> Optional[str]:
        """Lê até 1024 bytes do socket TCP de forma thread-safe, retornando texto ou None."""
        with self._lock:
            if self.isMockMode or not self.isOpenUnlocked() or self._socket is None:
                if self._mockRxQueue:
                    return self._mockRxQueue.popleft()
                return None

            try:
                chunk: bytes = self._socket.recv(self.RECV_BUFFER_SIZE)
                if not chunk:
                    self._closeSocketUnlocked()
                    self._enterMockModeUnlocked(
                        f"Conexão encerrada pelo servidor {self.host}:{self.port}."
                    )
                    return None
                decoded: str = chunk.decode("utf-8", errors="ignore").strip()
                return decoded if decoded else None
            except (socket.timeout, TimeoutError, BlockingIOError):
                return None
            except (ConnectionResetError, ConnectionAbortedError, OSError, Exception) as error:
                self._closeSocketUnlocked()
                self._enterMockModeUnlocked(
                    f"Falha ao ler do servidor {self.host}:{self.port} ({error})."
                )
                return None

    def readLine(self) -> Optional[str]:
        """Lê uma linha delimitada por ``\\n`` do socket TCP (ou da fila Mock)."""
        with self._lock:
            if self.isMockMode or not self.isOpenUnlocked() or self._socket is None:
                if self._mockRxQueue:
                    return self._mockRxQueue.popleft()
                return None

            newlineIndex: int = self._rxBuffer.find(b"\n")
            if newlineIndex == -1:
                try:
                    # Evita bloquear o lock por 3s em sockets reais caso não haja dados no buffer do SO
                    if self._hasPendingSocketBytesUnlocked():
                        chunk: bytes = self._socket.recv(self.RECV_BUFFER_SIZE)
                        if not chunk:
                            self._closeSocketUnlocked()
                            self._enterMockModeUnlocked(
                                f"Conexão encerrada pelo servidor {self.host}:{self.port}."
                            )
                            return None
                        self._rxBuffer.extend(chunk)
                    else:
                        return None
                except (socket.timeout, TimeoutError, BlockingIOError):
                    return None
                except (ConnectionResetError, ConnectionAbortedError, OSError, Exception) as error:
                    self._closeSocketUnlocked()
                    self._enterMockModeUnlocked(
                        f"Falha ao ler do servidor {self.host}:{self.port} ({error})."
                    )
                    return None

            newlineIndex = self._rxBuffer.find(b"\n")
            if newlineIndex == -1:
                if self._rxBuffer:
                    rawAll: bytes = bytes(self._rxBuffer)
                    self._rxBuffer.clear()
                    decodedAll: str = rawAll.decode("utf-8", errors="ignore").strip()
                    return decodedAll if decodedAll else None
                return None

            rawLine: bytes = bytes(self._rxBuffer[: newlineIndex + 1])
            del self._rxBuffer[: newlineIndex + 1]
            decodedLine: str = rawLine.decode("utf-8", errors="ignore").strip()
            return decodedLine if decodedLine else None

    def enqueueMockInput(self, packet: str) -> None:
        """Injeta um pacote simulado na fila de recepção para testes sem hardware."""
        cleanPacket: str = packet.strip()
        if not cleanPacket:
            return
        with self._lock:
            self._mockRxQueue.append(cleanPacket)

    def getMockTxHistory(self) -> list[str]:
        """Retorna os últimos pacotes registrados em modo simulação/mock."""
        with self._lock:
            return list(self._mockTxHistory)

    def clearMockTxHistory(self) -> None:
        """Limpa o histórico de pacotes simulados."""
        with self._lock:
            self._mockTxHistory.clear()

    def getStatus(self) -> dict[str, Any]:
        """Retorna o estado atual da conexão TCP Wi-Fi."""
        with self._lock:
            return {
                "connectionType": "wifi",
                "endpoint": f"{self.host}:{self.port}",
                "host": self.host,
                "port": self.port,
                "timeout": self.timeout,
                "isOpen": self.isOpenUnlocked(),
                "isConnected": self.isConnected,
                "isMockMode": self.isMockMode,
                "is_mock": self.isMockMode,
            }

    def _hasPendingSocketBytesUnlocked(self) -> bool:
        """Verifica sem bloquear se há bytes prontos para leitura no socket TCP."""
        if self._socket is None:
            return False
        try:
            fileno: Any = self._socket.fileno()
            if isinstance(fileno, int) and fileno >= 0:
                readable, _, _ = select.select([self._socket], [], [], 0.01)
                return bool(readable)
        except Exception:
            pass
        # Em mocks de testes unitários (onde fileno não é int), permite chamada direta ao recv()
        return True

    def _recordMockTxUnlocked(self, formattedPacket: str) -> None:
        """Registra no histórico e em jarvis.log o pacote simulado sem poluir o prompt do console."""
        cleanPacket: str = formattedPacket.rstrip("\r\n")
        self._mockTxHistory.append(cleanPacket)
        logger.debug("[MOCK][Arduino TX] %s", cleanPacket)

    def _enterMockModeUnlocked(self, reason: str) -> None:
        """Ativa o modo simulação/mock sem interromper o fluxo do assistente."""
        wasConnected: bool = self.isConnected
        self.isConnected = False
        self.isMockMode = True
        if not self._warnedMockMode:
            logger.warning(
                "%s Operando em modo simulação/mock (sem hardware físico).",
                reason,
            )
            self._warnedMockMode = True
        if wasConnected:
            self._publishConnectionState(connected=False, mockMode=True)

    def _closeSocketUnlocked(self) -> None:
        """Fecha o socket TCP interno mantendo ``_lock`` adquirido."""
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            finally:
                self._socket = None
        self.isConnected = False

    def _publishConnectionState(self, connected: bool, mockMode: bool) -> None:
        """Notifica mudanças no estado da conexão Wi-Fi pelo EventBus."""
        self.eventBus.publish(
            "arduinoConnectionChanged",
            {
                "connectionType": "wifi",
                "endpoint": f"{self.host}:{self.port}",
                "host": self.host,
                "port": self.port,
                "isConnected": connected,
                "isMockMode": mockMode,
            },
        )


networkConnection: NetworkConnection = NetworkConnection(autoConnect=False)
