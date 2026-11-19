import logging
import threading
import time
from typing import Any, Optional

from aiCore.eventBus import EventBus, eventBus
from appConfig.loggingConfig import getLogger
from appConfig.settings import Settings, settings

logger: logging.Logger = getLogger("jarvis.presenceTracker")


class PresenceTracker:
    """Máquina de estados de ocupação da bancada (PRESENT / AWAY) com timeout de ausência."""

    STATE_PRESENT: str = "PRESENT"
    STATE_AWAY: str = "AWAY"
    DEFAULT_CHECK_INTERVAL_SECONDS: float = 5.0

    def __init__(
        self,
        bus: Optional[EventBus] = None,
        appSettings: Optional[Settings] = None,
        awayTimeoutSeconds: Optional[float] = None,
        checkIntervalSeconds: float = DEFAULT_CHECK_INTERVAL_SECONDS,
        initialState: str = STATE_AWAY,
        autoSubscribe: bool = False,
    ) -> None:
        self.eventBus: EventBus = bus or eventBus
        self.settings: Settings = appSettings or settings

        self.awayTimeoutSeconds: float = (
            float(awayTimeoutSeconds)
            if awayTimeoutSeconds is not None
            else float(self.settings.presenceAwayTimeoutSeconds)
        )
        self.checkIntervalSeconds: float = max(0.05, float(checkIntervalSeconds))

        self._lock: threading.Lock = threading.Lock()
        self.state: str = (
            initialState
            if initialState in (self.STATE_PRESENT, self.STATE_AWAY)
            else self.STATE_AWAY
        )
        now: float = time.time()
        self.lastSeen: float = now if self.state == self.STATE_PRESENT else 0.0
        self.awaySince: float = now

        self._isSubscribed: bool = False
        self._stopEvent: threading.Event = threading.Event()
        self._workerThread: Optional[threading.Thread] = None

        if autoSubscribe:
            self.subscribeToEvents()

    def subscribeToEvents(self) -> None:
        """Assina eventos de hardware no EventBus para reagir a <EVENT:PRESENCE_DETECTED>."""
        with self._lock:
            if self._isSubscribed:
                return
            self.eventBus.subscribe("arduinoEventReceived", self._onArduinoEventReceived)
            self._isSubscribed = True

    def unsubscribeFromEvents(self) -> None:
        """Remove a assinatura do monitor de presença no EventBus."""
        with self._lock:
            if not self._isSubscribed:
                return
            self.eventBus.unsubscribe("arduinoEventReceived", self._onArduinoEventReceived)
            self._isSubscribed = False

    def start(self, checkIntervalSeconds: Optional[float] = None) -> None:
        """Inicia o worker daemon em segundo plano para monitorar inatividade (5s-10s)."""
        if self._workerThread is not None and self._workerThread.is_alive():
            return

        interval: float = (
            max(0.05, float(checkIntervalSeconds))
            if checkIntervalSeconds is not None
            else self.checkIntervalSeconds
        )
        self.subscribeToEvents()
        self._stopEvent.clear()
        self._workerThread = threading.Thread(
            target=self._monitorLoop,
            args=(interval,),
            name="jarvisPresenceTracker",
            daemon=True,
        )
        self._workerThread.start()

    def stop(self) -> None:
        """Encerra o worker de monitoramento de ausência e limpa assinaturas."""
        self._stopEvent.set()
        if self._workerThread is not None and self._workerThread.is_alive():
            self._workerThread.join(timeout=2.0)
        self._workerThread = None
        self.unsubscribeFromEvents()

    def reset(self, initialState: str = STATE_AWAY, currentTime: Optional[float] = None) -> None:
        """Reinicia o estado da máquina de presença (útil em testes unitários)."""
        now: float = currentTime if currentTime is not None else time.time()
        with self._lock:
            self.state = initialState
            self.lastSeen = now if initialState == self.STATE_PRESENT else 0.0
            self.awaySince = now

    def recordPresence(self, currentTime: Optional[float] = None) -> bool:
        """Processa <EVENT:PRESENCE_DETECTED>; retorna True apenas se houve retorno após AWAY."""
        if not self.settings.enablePresenceDetection:
            return False

        now: float = currentTime if currentTime is not None else time.time()
        returnedFromAway: bool = False
        absenceDuration: float = 0.0

        with self._lock:
            if self.state == self.STATE_AWAY:
                absenceDuration = round(max(0.0, now - self.awaySince), 2)
                self.state = self.STATE_PRESENT
                returnedFromAway = True
            self.lastSeen = now

        if returnedFromAway:
            msg: str = "[PRESENCE] Usuario retornou a bancada apos periodo de ausencia (>10 min)."
            logger.info("%s (ausencia=%.2fs)", msg, absenceDuration)
            self.eventBus.publish(
                "userReturned",
                {
                    "source": "pir_sensor",
                    "timestamp": now,
                    "absenceDuration": absenceDuration,
                },
            )
            return True

        logger.debug("[PRESENCE] Movimento continuo registrado; estado permanece PRESENT.")
        return False

    def checkInactivity(self, currentTime: Optional[float] = None) -> bool:
        """Verifica se o tempo sem movimento ultrapassou ``awayTimeoutSeconds`` e transiciona para AWAY."""
        now: float = currentTime if currentTime is not None else time.time()
        transitionedToAway: bool = False
        inactiveSeconds: float = 0.0

        with self._lock:
            if (
                self.state == self.STATE_PRESENT
                and self.lastSeen > 0.0
                and (now - self.lastSeen) > self.awayTimeoutSeconds
            ):
                inactiveSeconds = round(now - self.lastSeen, 2)
                self.state = self.STATE_AWAY
                self.awaySince = now
                transitionedToAway = True

        if transitionedToAway:
            msg: str = "[PRESENCE] Usuario ausente da bancada (inatividade > 10 min)."
            logger.info("%s (inativo=%.2fs)", msg, inactiveSeconds)
            self.eventBus.publish(
                "userAway",
                {
                    "source": "presence_tracker",
                    "timestamp": now,
                    "inactiveSeconds": inactiveSeconds,
                },
            )
            return True

        return False

    def getStatus(self) -> dict[str, Any]:
        """Retorna um resumo atual do estado de ocupação da bancada."""
        with self._lock:
            return {
                "state": self.state,
                "lastSeen": self.lastSeen,
                "awaySince": self.awaySince,
                "awayTimeoutSeconds": self.awayTimeoutSeconds,
            }

    def _onArduinoEventReceived(self, payload: Any) -> None:
        if isinstance(payload, dict):
            eventName: str = str(payload.get("name", "")).strip().upper()
            if eventName == "PRESENCE_DETECTED":
                self.recordPresence()

    def _monitorLoop(self, intervalSeconds: float) -> None:
        """Worker periódico em segundo plano para checagem do timeout de ausência."""
        while not self._stopEvent.wait(intervalSeconds):
            try:
                self.checkInactivity()
            except Exception as error:
                logger.debug("Erro no worker do PresenceTracker: %s", error)


presenceTracker: PresenceTracker = PresenceTracker()
