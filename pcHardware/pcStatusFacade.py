import threading
import time
from typing import Any, Optional

from aiCore.eventBus import EventBus, eventBus
from appConfig.settings import Settings, settings
from pcHardware.cpuRamMonitor import CpuRamMonitor
from pcHardware.gpuMonitor import GpuMonitor


class PcStatusFacade:
    """Facade de telemetria que unifica métricas de CPU, GPU e RAM com histerese no Modo Jogo."""

    _instance: Optional["PcStatusFacade"] = None
    _lock: threading.Lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls, *args: Any, **kwargs: Any) -> "PcStatusFacade":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        gpuMonitor: Optional[GpuMonitor] = None,
        cpuRamMonitor: Optional[CpuRamMonitor] = None,
        bus: Optional[EventBus] = None,
        appSettings: Optional[Settings] = None,
    ) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self.gpuMonitor: GpuMonitor = gpuMonitor or GpuMonitor()
            self.cpuRamMonitor: CpuRamMonitor = cpuRamMonitor or CpuRamMonitor()
            self.eventBus: EventBus = bus or eventBus
            self.settings: Settings = appSettings or settings

            self._stateLock: threading.Lock = threading.Lock()
            self.isGamingModeActive: bool = False
            self._lastGamingModeState: bool = False
            self.highGpuStartTime: Optional[float] = None
            self.lowGpuStartTime: Optional[float] = None
            self.lastTransitionTime: Optional[float] = None
            self.lastExitTime: Optional[float] = None

            self._gpuTempAlertActive: bool = False
            self._cpuTempAlertActive: bool = False
            self._initialized = True

    def resetGamingModeState(self) -> None:
        """Reinicia os contadores de histerese e o estado do Modo Jogo (útil em testes)."""
        with self._stateLock:
            self.isGamingModeActive = False
            self._lastGamingModeState = False
            self.highGpuStartTime = None
            self.lowGpuStartTime = None
            self.lastTransitionTime = None
            self.lastExitTime = None
            self._gpuTempAlertActive = False
            self._cpuTempAlertActive = False

    def evaluateGamingMode(
        self,
        gpuUsage: float,
        currentTime: Optional[float] = None,
    ) -> bool:
        """Avalia a histerese e cooldown do Modo Jogo com base no uso contínuo da GPU."""
        now: float = currentTime if currentTime is not None else time.monotonic()
        threshold: float = float(self.settings.gameModeGpuThreshold)
        enterDuration: float = float(self.settings.gameModeEnterDuration)
        exitDuration: float = float(self.settings.gameModeExitDuration)
        cooldown: float = float(self.settings.gameModeCooldown)

        transitioned: bool = False
        newState: bool = False

        with self._stateLock:
            if not self.isGamingModeActive:
                if gpuUsage > threshold:
                    self.lowGpuStartTime = None
                    if self.highGpuStartTime is None:
                        self.highGpuStartTime = now

                    elapsedHigh: float = now - self.highGpuStartTime
                    inCooldown: bool = (
                        self.lastExitTime is not None
                        and (now - self.lastExitTime) < cooldown
                    )
                    if elapsedHigh >= enterDuration and not inCooldown:
                        self.isGamingModeActive = True
                        self._lastGamingModeState = True
                        self.lastTransitionTime = now
                        self.highGpuStartTime = None
                        self.lowGpuStartTime = None
                        transitioned = True
                        newState = True
                else:
                    self.highGpuStartTime = None
            else:
                if gpuUsage < threshold:
                    self.highGpuStartTime = None
                    if self.lowGpuStartTime is None:
                        self.lowGpuStartTime = now

                    elapsedLow: float = now - self.lowGpuStartTime
                    if elapsedLow >= exitDuration:
                        self.isGamingModeActive = False
                        self._lastGamingModeState = False
                        self.lastTransitionTime = now
                        self.lastExitTime = now
                        self.lowGpuStartTime = None
                        self.highGpuStartTime = None
                        transitioned = True
                        newState = False
                else:
                    self.lowGpuStartTime = None

            activeState: bool = self.isGamingModeActive

        if transitioned:
            self.eventBus.publish(
                "gamingModeChanged",
                {
                    "active": newState,
                    "isGamingMode": newState,
                    "gpuUsage": gpuUsage,
                },
            )

        return activeState

    def getSnapshot(self, currentTime: Optional[float] = None) -> dict[str, Any]:
        """Unifica dados de CPU, RAM e GPU em um único dicionário e dispara eventos de alerta."""
        cpuRamData: dict[str, Any] = self.cpuRamMonitor.getCpuRamMetrics()
        gpuData: dict[str, Any] = self.gpuMonitor.getGpuMetrics()

        gpuUsage: float = float(gpuData.get("gpuUsage", 0.0))
        gpuTemp: float = float(gpuData.get("gpuTemp", 0.0))
        cpuTemp: Optional[float] = cpuRamData.get("cpuTemp")

        isGamingMode: bool = self.evaluateGamingMode(
            gpuUsage=gpuUsage,
            currentTime=currentTime,
        )

        snapshot: dict[str, Any] = {
            **cpuRamData,
            **gpuData,
            "isGamingMode": isGamingMode,
        }

        self._evaluateThermalAlerts(
            snapshot=snapshot,
            gpuTemp=gpuTemp,
            cpuTemp=cpuTemp,
        )

        return snapshot

    def _evaluateThermalAlerts(
        self,
        snapshot: dict[str, Any],
        gpuTemp: float,
        cpuTemp: Optional[float],
    ) -> None:
        """Publica eventos no EventBus caso limites térmicos da GPU ou CPU sejam atingidos."""
        if gpuTemp > self.settings.gpuTempAlertThreshold:
            if not self._gpuTempAlertActive:
                self._gpuTempAlertActive = True
                self.eventBus.publish(
                    "gpuHighTempAlert",
                    {
                        "gpuTemp": gpuTemp,
                        "threshold": self.settings.gpuTempAlertThreshold,
                        "gpuName": snapshot.get("gpuName", "RTX 4060 Ti"),
                    },
                )
        else:
            self._gpuTempAlertActive = False

        if cpuTemp is not None and cpuTemp > self.settings.cpuTempAlertThreshold:
            if not self._cpuTempAlertActive:
                self._cpuTempAlertActive = True
                self.eventBus.publish(
                    "cpuHighTempAlert",
                    {
                        "cpuTemp": cpuTemp,
                        "threshold": self.settings.cpuTempAlertThreshold,
                    },
                )
        else:
            self._cpuTempAlertActive = False

    def formatSummary(self, snapshot: Optional[dict[str, Any]] = None) -> str:
        """Formata um resumo legível em português com todos os dados de telemetria do PC."""
        data: dict[str, Any] = snapshot if snapshot is not None else self.getSnapshot()

        cpuUsage: float = data.get("cpuUsage", 0.0)
        cpuTemp: Optional[float] = data.get("cpuTemp")
        cpuTempText: str = f"{cpuTemp}°C" if cpuTemp is not None else "N/D (Afterburner fechado)"

        ramUsed: float = data.get("ramUsedGb", 0.0)
        ramTotal: float = data.get("ramTotalGb", 0.0)
        ramPercent: float = data.get("ramPercent", 0.0)

        gpuName: str = data.get("gpuName", "RTX 4060 Ti")
        gpuUsage: float = data.get("gpuUsage", 0.0)
        gpuTemp: float = data.get("gpuTemp", 0.0)
        vramUsed: float = data.get("vramUsedGb", 0.0)
        vramTotal: float = data.get("vramTotalGb", 0.0)
        vramPercent: float = data.get("vramPercent", 0.0)
        gamingStatus: str = "ATIVO (Tarefas pesadas pausadas)" if data.get("isGamingMode") else "Inativo"

        return (
            f"CPU (Ryzen 5 5600): {cpuUsage}% | Temp CPU: {cpuTempText} | "
            f"RAM: {ramUsed}/{ramTotal} GB ({ramPercent}%) | "
            f"GPU ({gpuName}): {gpuUsage}% ({gpuTemp}°C) | "
            f"VRAM: {vramUsed}/{vramTotal} GB ({vramPercent}%) | "
            f"Modo Jogo: {gamingStatus}"
        )

    def shutdown(self) -> None:
        """Libera recursos dos monitores de hardware."""
        self.gpuMonitor.shutdown()


pcStatusFacade: PcStatusFacade = PcStatusFacade()
