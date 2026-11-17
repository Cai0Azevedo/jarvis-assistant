from typing import Any

import pynvml


class GpuMonitor:
    """Adapter NVIDIA (pynvml) para leitura de uso, VRAM e temperatura da RTX 4060 Ti."""

    def __init__(self, deviceIndex: int = 0) -> None:
        self.deviceIndex: int = deviceIndex
        self.isInitialized: bool = False
        self._warnedOffline: bool = False
        self._initialize()

    def _initialize(self) -> bool:
        """Inicializa a biblioteca NVML da NVIDIA de forma resiliente."""
        if self.isInitialized:
            return True
        try:
            pynvml.nvmlInit()
            self.isInitialized = True
            self._warnedOffline = False
            return True
        except Exception as error:
            if not self._warnedOffline:
                print(f"[AVISO][GpuMonitor] Não foi possível inicializar a GPU NVIDIA via NVML: {error}")
                self._warnedOffline = True
            self.isInitialized = False
            return False

    def getGpuMetrics(self) -> dict[str, Any]:
        """Coleta métricas em tempo real da GPU (uso %, temperatura °C e VRAM em GB)."""
        defaultMetrics: dict[str, Any] = {
            "gpuName": "NVIDIA GeForce RTX 4060 Ti",
            "gpuUsage": 0.0,
            "gpuTemp": 0.0,
            "vramUsedGb": 0.0,
            "vramTotalGb": 8.0,
            "vramPercent": 0.0,
            "gpuAvailable": False,
        }

        if not self.isInitialized and not self._initialize():
            return defaultMetrics

        try:
            handle: Any = pynvml.nvmlDeviceGetHandleByIndex(self.deviceIndex)
            rawName: Any = pynvml.nvmlDeviceGetName(handle)
            gpuName: str = (
                rawName.decode("utf-8", errors="ignore")
                if isinstance(rawName, bytes)
                else str(rawName)
            )

            utilization: Any = pynvml.nvmlDeviceGetUtilizationRates(handle)
            gpuUsage: float = float(utilization.gpu)

            gpuTemp: float = float(
                pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            )

            memoryInfo: Any = pynvml.nvmlDeviceGetMemoryInfo(handle)
            vramUsedGb: float = round(memoryInfo.used / (1024**3), 2)
            vramTotalGb: float = round(memoryInfo.total / (1024**3), 2)
            vramPercent: float = (
                round((memoryInfo.used / memoryInfo.total) * 100.0, 1)
                if memoryInfo.total > 0
                else 0.0
            )

            return {
                "gpuName": gpuName,
                "gpuUsage": gpuUsage,
                "gpuTemp": gpuTemp,
                "vramUsedGb": vramUsedGb,
                "vramTotalGb": vramTotalGb,
                "vramPercent": vramPercent,
                "gpuAvailable": True,
            }
        except Exception as error:
            print(f"[AVISO][GpuMonitor] Falha ao ler sensores da GPU: {error}")
            self.isInitialized = False
            return defaultMetrics

    def shutdown(self) -> None:
        """Finaliza a sessão NVML com segurança."""
        if self.isInitialized:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
            finally:
                self.isInitialized = False
