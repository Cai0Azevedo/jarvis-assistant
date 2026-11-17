import threading
from typing import Any, Optional

from aiCore.eventBus import EventBus, eventBus
from arduinoHub.serialConnection import SerialConnection, serialConnection
from arduinoHub.serialProtocol import SerialProtocol


class SensorReader:
    """Leitor contínuo de sensores/botões vindos do Arduino UNO e envio de comandos <CMD:...>."""

    DEFAULT_POLL_INTERVAL_SECONDS: float = 0.2

    def __init__(
        self,
        connection: Optional[SerialConnection] = None,
        bus: Optional[EventBus] = None,
    ) -> None:
        self.connection: SerialConnection = connection or serialConnection
        self.eventBus: EventBus = bus or eventBus

        self._sensorsLock: threading.Lock = threading.Lock()
        self._latestSensors: dict[str, Any] = {}

        self._stopEvent: threading.Event = threading.Event()
        self._readerThread: Optional[threading.Thread] = None

    def start(self, pollIntervalSeconds: float = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
        """Inicia a thread daemon de leitura contínua de pacotes seriais do Arduino."""
        if self._readerThread is not None and self._readerThread.is_alive():
            return

        self._stopEvent.clear()
        self._readerThread = threading.Thread(
            target=self._readLoop,
            args=(max(0.05, pollIntervalSeconds),),
            name="jarvisArduinoSensorReader",
            daemon=True,
        )
        self._readerThread.start()

    def stop(self) -> None:
        """Interrompe a thread de leitura de sensores com segurança."""
        self._stopEvent.set()
        if self._readerThread is not None and self._readerThread.is_alive():
            self._readerThread.join(timeout=2.0)
        self._readerThread = None

    def pollOnce(self) -> Optional[dict[str, Any]]:
        """Lê e processa um pacote serial disponível, publicando sensores e botões no EventBus."""
        rawLine: Optional[str] = self.connection.readLine()
        if not rawLine:
            return None

        packet: Optional[dict[str, Any]] = SerialProtocol.parsePacket(rawLine)
        if not packet:
            return None

        packetType: str = str(packet.get("type", ""))

        if packetType == "SEN":
            sensorName: str = str(packet["name"])
            sensorValue: Any = packet["value"]
            with self._sensorsLock:
                self._latestSensors[sensorName] = sensorValue

            self.eventBus.publish(
                "arduinoSensorUpdated",
                {
                    "name": sensorName,
                    "value": sensorValue,
                    "raw": packet.get("raw", rawLine),
                },
            )
        elif packetType == "BTN":
            self.eventBus.publish(
                "arduinoButtonPressed",
                {
                    "button": packet.get("button"),
                    "state": packet.get("state"),
                    "raw": packet.get("raw", rawLine),
                },
            )

        return packet

    def sendCommand(self, action: str, value: Any) -> bool:
        """Envia um comando de atuador/relé para o Arduino no formato ``<CMD:ACAO:VALOR>\\n``."""
        packet: str = SerialProtocol.encodeCommand(action, value)
        return self.connection.send(packet)

    def injectMockSensor(self, sensorName: str, value: Any) -> Optional[dict[str, Any]]:
        """Simula a chegada de uma leitura de sensor do Arduino sem hardware conectado."""
        packet: str = SerialProtocol.encodeSensor(sensorName, value)
        self.connection.enqueueMockInput(packet)
        return self.pollOnce()

    def getSensorValue(self, sensorName: str) -> Optional[Any]:
        """Retorna a última leitura em cache de um sensor específico."""
        key: str = sensorName.strip().upper()
        with self._sensorsLock:
            return self._latestSensors.get(key)

    def getLatestSensors(self) -> dict[str, Any]:
        """Retorna uma cópia de todas as últimas leituras de sensores recebidas."""
        with self._sensorsLock:
            return dict(self._latestSensors)

    def _readLoop(self, pollIntervalSeconds: float) -> None:
        """Loop de leitura resiliente executado em thread daemon."""
        while not self._stopEvent.is_set():
            try:
                while self.pollOnce() is not None:
                    if self._stopEvent.is_set():
                        break
            except Exception as error:
                print(f"[AVISO][SensorReader] Erro ao processar leitura serial: {error}")
            self._stopEvent.wait(pollIntervalSeconds)


sensorReader: SensorReader = SensorReader()
