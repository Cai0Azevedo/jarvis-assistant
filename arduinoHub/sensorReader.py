import logging
import threading
import time
from typing import Any, Optional

from aiCore.eventBus import EventBus, eventBus
from appConfig.loggingConfig import getLogger
from appConfig.settings import Settings, settings
from arduinoHub.serialConnection import serialConnection
from arduinoHub.serialProtocol import SerialProtocol

logger: logging.Logger = getLogger("jarvis.sensorReader")


class SensorReader:
    """Leitor contínuo de sensores/eventos (PIR, botões) do Arduino/ESP-01 com máquina de estados de presença."""

    DEFAULT_POLL_INTERVAL_SECONDS: float = 0.2

    def __init__(
        self,
        connection: Optional[Any] = None,
        bus: Optional[EventBus] = None,
        appSettings: Optional[Settings] = None,
        presenceTimeoutSeconds: Optional[float] = None,
    ) -> None:
        self.connection: Any = connection or serialConnection
        self.eventBus: EventBus = bus or eventBus
        self.settings: Settings = appSettings or settings

        self._sensorsLock: threading.Lock = threading.Lock()
        self._latestSensors: dict[str, Any] = {}

        # Máquina de estados de presença do usuário (Sensor PIR)
        self.isUserPresent: bool = False
        self.lastMovementTimestamp: float = 0.0
        self.presenceTimeoutSeconds: float = (
            float(presenceTimeoutSeconds)
            if presenceTimeoutSeconds is not None
            else float(self.settings.presenceTimeoutSeconds)
        )

        self._stopEvent: threading.Event = threading.Event()
        self._readerThread: Optional[threading.Thread] = None

    def start(self, pollIntervalSeconds: float = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
        """Inicia a thread daemon de leitura contínua de pacotes do Arduino/ESP-01."""
        if self._readerThread is not None and self._readerThread.is_alive():
            return

        self._stopEvent.clear()
        self._readerThread = threading.Thread(
            target=self.readLoop,
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

    def checkPresenceTimeout(self, currentTime: Optional[float] = None) -> bool:
        """Reseta ``isUserPresent = False`` caso o tempo sem movimento exceda ``presenceTimeoutSeconds``."""
        now: float = currentTime if currentTime is not None else time.time()
        with self._sensorsLock:
            if (
                self.isUserPresent
                and self.lastMovementTimestamp > 0.0
                and now > (self.lastMovementTimestamp + self.presenceTimeoutSeconds)
            ):
                self.isUserPresent = False
                logger.debug(
                    "[PIR] Sessão de presença expirada após %.1fs sem movimento. Estado resetado para ausente.",
                    self.presenceTimeoutSeconds,
                )
                return True
        return False

    def resetPresenceState(self) -> None:
        """Reinicia manualmente o estado de presença do usuário."""
        with self._sensorsLock:
            self.isUserPresent = False
            self.lastMovementTimestamp = 0.0

    def _handlePresenceDetected(self, currentTime: Optional[float] = None) -> bool:
        """Processa a transição da máquina de estados do PIR; retorna True apenas na chegada inicial."""
        now: float = currentTime if currentTime is not None else time.time()
        self.checkPresenceTimeout(currentTime=now)

        with self._sensorsLock:
            if not self.isUserPresent:
                self.isUserPresent = True
                self.lastMovementTimestamp = now
                isNewArrival: bool = True
            else:
                self.lastMovementTimestamp = now
                isNewArrival = False

        if isNewArrival:
            logger.debug("[PIR] Presença física detectada na bancada (usuário acabou de chegar)!")
            if self.settings.enablePresenceDetection:
                self.eventBus.publish(
                    "userPresenceDetected",
                    {"source": "pir_sensor", "timestamp": now},
                )
            return True

        logger.debug(
            "[PIR] Movimento contínuo detectado; usuário já presente (lastMovementTimestamp atualizado)."
        )
        return False

    def pollOnce(self) -> Optional[dict[str, Any]]:
        """Lê e processa um pacote disponível (Serial ou Wi-Fi), publicando no EventBus."""
        self.checkPresenceTimeout()

        rawLine: Optional[str] = (
            self.connection.readLine()
            if hasattr(self.connection, "readLine")
            else self.connection.read()
        )
        if not rawLine:
            return None

        cleanedLine: str = rawLine.strip()
        packet: Optional[dict[str, Any]] = SerialProtocol.parsePacket(cleanedLine)
        if not packet:
            return None

        packetType: str = str(packet.get("type", ""))

        if packetType == "SEN":
            sensorName: str = str(packet["name"])
            sensorValue: Any = packet["value"]
            with self._sensorsLock:
                self._latestSensors[sensorName] = sensorValue

            logger.debug("[SEN] Sensor %s atualizado: %s", sensorName, sensorValue)
            self.eventBus.publish(
                "arduinoSensorUpdated",
                {
                    "name": sensorName,
                    "value": sensorValue,
                    "raw": packet.get("raw", cleanedLine),
                },
            )
        elif packetType == "BTN":
            logger.debug("[BTN] Botão %s acionado (%s)", packet.get("button"), packet.get("state"))
            self.eventBus.publish(
                "arduinoButtonPressed",
                {
                    "button": packet.get("button"),
                    "state": packet.get("state"),
                    "raw": packet.get("raw", cleanedLine),
                },
            )
        elif packetType == "EVENT" or cleanedLine.startswith("<EVENT:"):
            eventName: str = str(packet.get("name", "")).upper()
            if eventName == "PRESENCE_DETECTED":
                self._handlePresenceDetected()

            self.eventBus.publish(
                "arduinoEventReceived",
                {
                    "name": eventName,
                    "value": packet.get("value"),
                    "raw": packet.get("raw", cleanedLine),
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

    def injectMockEvent(
        self,
        eventName: str,
        value: Optional[Any] = None,
    ) -> Optional[dict[str, Any]]:
        """Simula a chegada de um evento (ex: ``PRESENCE_DETECTED``) em modo Mock."""
        packet: str = SerialProtocol.encodeEvent(eventName, value)
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

    def readLoop(self, pollIntervalSeconds: float = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
        """Loop de leitura contínua e resiliente executado em thread daemon."""
        while not self._stopEvent.is_set():
            try:
                self.checkPresenceTimeout()
                while self.pollOnce() is not None:
                    if self._stopEvent.is_set():
                        break
            except Exception as error:
                logger.debug("Erro ao processar leitura do Arduino: %s", error)
            self._stopEvent.wait(pollIntervalSeconds)

    def run(self, pollIntervalSeconds: float = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
        """Alias compatível para ``readLoop()``."""
        self.readLoop(pollIntervalSeconds=pollIntervalSeconds)

    def _readLoop(self, pollIntervalSeconds: float = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
        """Alias interno compatível para ``readLoop()``."""
        self.readLoop(pollIntervalSeconds=pollIntervalSeconds)


sensorReader: SensorReader = SensorReader()
