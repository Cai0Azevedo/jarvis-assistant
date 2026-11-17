import threading
import time
from typing import Any, Callable, Optional

from aiCore.eventBus import EventBus, eventBus
from arduinoHub.serialConnection import SerialConnection, serialConnection
from arduinoHub.serialProtocol import SerialProtocol
from pcHardware.pcStatusFacade import PcStatusFacade, pcStatusFacade


class LcdDisplay:
    """Controlador do display LCD I2C 16x2 que observa o EventBus e exibe telemetria alternada."""

    DEFAULT_ALERT_HOLD_SECONDS: float = 5.0
    DEFAULT_SCREEN_INTERVAL_SECONDS: float = 4.0
    TOTAL_TELEMETRY_SCREENS: int = 2

    def __init__(
        self,
        connection: Optional[SerialConnection] = None,
        bus: Optional[EventBus] = None,
        telemetryFacade: Optional[PcStatusFacade] = None,
        snapshotProvider: Optional[Callable[[], dict[str, Any]]] = None,
        autoSubscribe: bool = True,
    ) -> None:
        self.connection: SerialConnection = connection or serialConnection
        self.eventBus: EventBus = bus or eventBus
        self.telemetryFacade: PcStatusFacade = telemetryFacade or pcStatusFacade
        self.snapshotProvider: Callable[[], dict[str, Any]] = (
            snapshotProvider or self.telemetryFacade.getSnapshot
        )

        self._stateLock: threading.Lock = threading.Lock()
        self._alertHoldUntil: float = 0.0
        self._isSubscribed: bool = False
        self._currentScreenIndex: int = 0
        self._lastLine1: str = ""
        self._lastLine2: str = ""

        self._stopEvent: threading.Event = threading.Event()
        self._workerThread: Optional[threading.Thread] = None

        if autoSubscribe:
            self.subscribeToEvents()

    def subscribeToEvents(self) -> None:
        """Assina eventos do EventBus para atualizar o display LCD automaticamente."""
        with self._stateLock:
            if self._isSubscribed:
                return
            self.eventBus.subscribe("telemetryUpdated", self._onTelemetryUpdated)
            self.eventBus.subscribe("gamingModeChanged", self._onGamingModeChanged)
            self.eventBus.subscribe("gpuHighTempAlert", self._onGpuHighTempAlert)
            self.eventBus.subscribe("cpuHighTempAlert", self._onCpuHighTempAlert)
            self.eventBus.subscribe("urgentEmailReceived", self._onUrgentEmailReceived)
            self.eventBus.subscribe("jarvisStatusChanged", self._onStatusChanged)
            self.eventBus.subscribe("arduinoErrorAlert", self._onErrorAlert)
            self.eventBus.subscribe("arduinoDisplayMessage", self._onDisplayMessage)
            self._isSubscribed = True

    def unsubscribeFromEvents(self) -> None:
        """Remove todas as assinaturas do display no EventBus."""
        with self._stateLock:
            if not self._isSubscribed:
                return
            self.eventBus.unsubscribe("telemetryUpdated", self._onTelemetryUpdated)
            self.eventBus.unsubscribe("gamingModeChanged", self._onGamingModeChanged)
            self.eventBus.unsubscribe("gpuHighTempAlert", self._onGpuHighTempAlert)
            self.eventBus.unsubscribe("cpuHighTempAlert", self._onCpuHighTempAlert)
            self.eventBus.unsubscribe("urgentEmailReceived", self._onUrgentEmailReceived)
            self.eventBus.unsubscribe("jarvisStatusChanged", self._onStatusChanged)
            self.eventBus.unsubscribe("arduinoErrorAlert", self._onErrorAlert)
            self.eventBus.unsubscribe("arduinoDisplayMessage", self._onDisplayMessage)
            self._isSubscribed = False

    def showMessage(self, line1: str, line2: str = "") -> bool:
        """Formata duas linhas de 16 caracteres e envia no padrão ``<LCD:L1|L2>\\n``."""
        packet: str = SerialProtocol.formatLcdMessage(line1, line2)
        formattedLine1: str = SerialProtocol.formatLcdLine(line1)
        formattedLine2: str = SerialProtocol.formatLcdLine(line2)
        with self._stateLock:
            self._lastLine1 = formattedLine1
            self._lastLine2 = formattedLine2
        return self.connection.send(packet)

    def showStatus(self, status: str, detail: str = "") -> bool:
        """Exibe uma mensagem de status operacional (ex: 'JARVIS ONLINE', 'PROCESSANDO...')."""
        return self.showMessage(status, detail)

    def showOnline(self, detail: str = "SISTEMA PRONTO") -> bool:
        """Exibe a tela padrão de inicialização concluída."""
        return self.showStatus("JARVIS ONLINE", detail)

    def showProcessing(self, detail: str = "AGUARDE...") -> bool:
        """Exibe a tela indicando que a IA está processando uma solicitação."""
        return self.showStatus("PROCESSANDO...", detail)

    def showError(
        self,
        message: str,
        detail: str = "",
        holdSeconds: float = DEFAULT_ALERT_HOLD_SECONDS,
    ) -> bool:
        """Exibe uma mensagem de erro com retenção temporária no display."""
        title: str = f"ERRO: {message}" if detail == "" and len(message) <= 10 else "ERRO NO SISTEMA"
        body: str = detail if detail else message
        if title.startswith("ERRO: "):
            body = ""
        return self.showAlert(title, body, holdSeconds=holdSeconds)

    def showAlert(
        self,
        line1: str,
        line2: str = "",
        holdSeconds: float = DEFAULT_ALERT_HOLD_SECONDS,
    ) -> bool:
        """Exibe um alerta prioritário no LCD evitando sobrescrita imediata pela telemetria."""
        with self._stateLock:
            self._alertHoldUntil = time.monotonic() + max(0.0, holdSeconds)
        return self.showMessage(line1, line2)

    def formatTelemetryLines(
        self,
        snapshot: dict[str, Any],
        screenIndex: int = 0,
    ) -> tuple[str, str]:
        """Formata as linhas compactas para a tela de CPU/GPU (0) ou RAM/VRAM (1)."""
        normalizedIndex: int = screenIndex % self.TOTAL_TELEMETRY_SCREENS

        if normalizedIndex == 0:
            cpuUsage: int = round(float(snapshot.get("cpuUsage", 0.0)))
            cpuTemp: Any = snapshot.get("cpuTemp")
            gpuUsage: int = round(float(snapshot.get("gpuUsage", 0.0)))
            gpuTemp: int = round(float(snapshot.get("gpuTemp", 0.0)))

            cpuTempText: str = f"{round(float(cpuTemp))}C" if cpuTemp is not None else "N/D"
            line1: str = f"CPU: {cpuUsage}% {cpuTempText}"
            line2: str = f"GPU: {gpuUsage}% {gpuTemp}C"
            return (line1, line2)

        ramPercent: int = round(float(snapshot.get("ramPercent", 0.0)))
        ramUsedGb: float = float(snapshot.get("ramUsedGb", 0.0))
        vramUsedGb: float = float(snapshot.get("vramUsedGb", 0.0))
        vramTotalGb: float = float(snapshot.get("vramTotalGb", 8.0))
        isGamingMode: bool = bool(snapshot.get("isGamingMode", False))

        line1 = f"RAM: {ramPercent}% {ramUsedGb:.1f}GB"
        line2 = (
            "MODO JOGO: ATIVO"
            if isGamingMode
            else f"VRAM:{vramUsedGb:.1f}/{vramTotalGb:.0f}GB"
        )
        return (line1, line2)

    def showTelemetry(
        self,
        snapshot: Optional[dict[str, Any]] = None,
        screenIndex: Optional[int] = None,
        advanceScreen: bool = False,
    ) -> bool:
        """Coleta dados do pcStatusFacade (se necessário) e exibe a tela de telemetria."""
        with self._stateLock:
            if time.monotonic() < self._alertHoldUntil:
                return False
            activeIndex: int = (
                self._currentScreenIndex if screenIndex is None else screenIndex
            )
            if advanceScreen:
                self._currentScreenIndex = (
                    activeIndex + 1
                ) % self.TOTAL_TELEMETRY_SCREENS

        data: dict[str, Any] = (
            snapshot if snapshot is not None else self.snapshotProvider()
        )
        line1, line2 = self.formatTelemetryLines(data, screenIndex=activeIndex)
        return self.showMessage(line1, line2)

    def startTelemetryLoop(
        self,
        intervalSeconds: float = DEFAULT_SCREEN_INTERVAL_SECONDS,
        snapshotProvider: Optional[Callable[[], dict[str, Any]]] = None,
    ) -> None:
        """Inicia worker em background alternando telas de CPU/GPU e RAM/VRAM a cada 3-5s."""
        if self._workerThread is not None and self._workerThread.is_alive():
            return

        provider: Callable[[], dict[str, Any]] = (
            snapshotProvider or self.snapshotProvider
        )
        refreshInterval: float = max(0.05, float(intervalSeconds))

        self._stopEvent.clear()
        self._workerThread = threading.Thread(
            target=self._telemetryWorker,
            args=(provider, refreshInterval),
            name="jarvisLcdTelemetry",
            daemon=True,
        )
        self._workerThread.start()

    def stopTelemetryLoop(self) -> None:
        """Interrompe o worker periódico de alternância de telas do LCD."""
        self._stopEvent.set()
        if self._workerThread is not None and self._workerThread.is_alive():
            self._workerThread.join(timeout=2.0)
        self._workerThread = None

    def getLastDisplayedLines(self) -> tuple[str, str]:
        """Retorna as últimas duas linhas enviadas ao display (com exatamente 16 chars cada)."""
        with self._stateLock:
            return (self._lastLine1, self._lastLine2)

    def shutdown(self) -> None:
        """Encerra assinaturas e thread de atualização do LCD."""
        self.stopTelemetryLoop()
        self.unsubscribeFromEvents()

    def _telemetryWorker(
        self,
        snapshotProvider: Callable[[], dict[str, Any]],
        intervalSeconds: float,
    ) -> None:
        """Loop resiliente de atualização e alternância de telas no display LCD."""
        while not self._stopEvent.is_set():
            try:
                snapshot: dict[str, Any] = snapshotProvider()
                if isinstance(snapshot, dict):
                    self.showTelemetry(snapshot=snapshot, advanceScreen=True)
            except Exception as error:
                print(f"[AVISO][LcdDisplay] Erro no worker de telemetria do LCD: {error}")
            self._stopEvent.wait(intervalSeconds)

    def _onTelemetryUpdated(self, payload: Any) -> None:
        if isinstance(payload, dict):
            self.showTelemetry(snapshot=payload, advanceScreen=False)

    def _onGamingModeChanged(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        isGaming: bool = bool(payload.get("isGamingMode", False))
        gpuUsage: int = round(float(payload.get("gpuUsage", 0.0)))
        if isGaming:
            self.showAlert("MODO JOGO ATIVO", f"GPU em {gpuUsage}%")
        else:
            self.showAlert("MODO JOGO OFF", f"GPU em {gpuUsage}%")

    def _onGpuHighTempAlert(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        gpuTemp: int = round(float(payload.get("gpuTemp", 0.0)))
        self.showAlert("ALERTA TEMP GPU!", f"RTX: {gpuTemp}C")

    def _onCpuHighTempAlert(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        cpuTemp: int = round(float(payload.get("cpuTemp", 0.0)))
        self.showAlert("ALERTA TEMP CPU!", f"Ryzen: {cpuTemp}C")

    def _onUrgentEmailReceived(self, payload: Any) -> None:
        if isinstance(payload, dict):
            sender: str = str(payload.get("sender") or payload.get("subject") or "Novo email")
            self.showAlert("EMAIL URGENTE!", sender, holdSeconds=6.0)
        else:
            self.showAlert("EMAIL URGENTE!", "Verifique inbox", holdSeconds=6.0)

    def _onStatusChanged(self, payload: Any) -> None:
        if isinstance(payload, dict):
            statusText: str = str(payload.get("status", "JARVIS ONLINE"))
            detailText: str = str(payload.get("detail", ""))
            self.showStatus(statusText, detailText)
        elif isinstance(payload, str) and payload.strip():
            self.showStatus(payload)

    def _onErrorAlert(self, payload: Any) -> None:
        if isinstance(payload, dict):
            message: str = str(payload.get("message", "Falha interna"))
            detail: str = str(payload.get("detail", ""))
            self.showError(message, detail)
        elif isinstance(payload, str) and payload.strip():
            self.showError(payload)

    def _onDisplayMessage(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        line1: str = str(payload.get("line1", ""))
        line2: str = str(payload.get("line2", ""))
        holdSeconds: Optional[float] = payload.get("holdSeconds")
        if holdSeconds is not None:
            self.showAlert(line1, line2, holdSeconds=float(holdSeconds))
        else:
            self.showMessage(line1, line2)


lcdDisplay: LcdDisplay = LcdDisplay()
