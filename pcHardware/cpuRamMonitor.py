import mmap
import struct
from typing import Any, Optional

import psutil


class CpuRamMonitor:
    """Adapter de CPU e RAM com suporte à memória compartilhada do MSI Afterburner (sem Admin)."""

    def __init__(self) -> None:
        self._warnedAfterburnerClosed: bool = False

    def readAfterburnerSensors(self) -> dict[str, float]:
        """Lê os sensores ativos na memória compartilhada MAHMSharedMemory do MSI Afterburner."""
        sensorsData: dict[str, float] = {}
        sharedMem: Optional[mmap.mmap] = None

        try:
            # Mapeia primeiro apenas o cabeçalho (20 bytes) para descobrir o tamanho exato da seção;
            # pedir um tamanho fixo maior que o criado pelo Afterburner causa WinError 5 (Acesso negado).
            sharedMem = mmap.mmap(
                -1,
                20,
                tagname="MAHMSharedMemory",
                access=mmap.ACCESS_READ,
            )

            signature, _version, headerSize, numEntries, entrySize = struct.unpack(
                "4sIIII", sharedMem[:20]
            )
            sharedMem.close()
            sharedMem = None

            # Em x86 (little-endian), o DWORD 'MAHM' do C++ fica armazenado como b'MHAM'
            if signature not in (b"MHAM", b"MAHM"):
                if not self._warnedAfterburnerClosed:
                    print(
                        "[AVISO][CpuRamMonitor] MSI Afterburner fechado ou memória compartilhada "
                        "inativa. Temperatura da CPU indisponível no momento."
                    )
                    self._warnedAfterburnerClosed = True
                return sensorsData

            self._warnedAfterburnerClosed = False

            if numEntries == 0 or entrySize < 1304:
                return sensorsData

            totalSize: int = headerSize + (numEntries * entrySize)
            sharedMem = mmap.mmap(
                -1,
                totalSize,
                tagname="MAHMSharedMemory",
                access=mmap.ACCESS_READ,
            )

            for index in range(numEntries):
                offset: int = headerSize + (index * entrySize)
                rawSensorName: bytes = sharedMem[offset : offset + 260]
                sensorName: str = rawSensorName.split(b"\x00")[0].decode(
                    "latin-1", errors="ignore"
                )

                # Valor float (4 bytes) localizado no deslocamento 1300 da entrada MAHM v2.0+
                sensorValue: float = struct.unpack(
                    "f", sharedMem[offset + 1300 : offset + 1304]
                )[0]

                if -1000.0 < sensorValue < 100000.0:
                    sensorsData[sensorName] = round(sensorValue, 1)

        except Exception as error:
            if not self._warnedAfterburnerClosed:
                print(
                    f"[AVISO][CpuRamMonitor] Não foi possível acessar o MSI Afterburner: {error}"
                )
                self._warnedAfterburnerClosed = True
        finally:
            if sharedMem is not None:
                try:
                    sharedMem.close()
                except Exception:
                    pass

        return sensorsData

    def getCpuTemperature(self) -> Optional[float]:
        """Obtém a temperatura do processador Ryzen 5 5600 via MSI Afterburner."""
        sensors: dict[str, float] = self.readAfterburnerSensors()
        cpuTemp: Optional[float] = sensors.get("CPU temperature")
        if cpuTemp is None:
            cpuTemp = sensors.get("Temperatura da CPU")
        return cpuTemp

    def getCpuRamMetrics(self) -> dict[str, Any]:
        """Retorna métricas de uso/temperatura da CPU e consumo de memória RAM."""
        cpuUsage: float = 0.0
        ramUsedGb: float = 0.0
        ramTotalGb: float = 32.0
        ramPercent: float = 0.0

        try:
            cpuUsage = float(psutil.cpu_percent(interval=0.2))
            virtualMem: Any = psutil.virtual_memory()
            ramUsedGb = round(virtualMem.used / (1024**3), 2)
            ramTotalGb = round(virtualMem.total / (1024**3), 2)
            ramPercent = float(virtualMem.percent)
        except Exception as error:
            print(f"[AVISO][CpuRamMonitor] Erro ao ler métricas do psutil: {error}")

        cpuTemp: Optional[float] = self.getCpuTemperature()

        return {
            "cpuUsage": cpuUsage,
            "cpuTemp": cpuTemp,
            "afterburnerOnline": cpuTemp is not None,
            "ramUsedGb": ramUsedGb,
            "ramTotalGb": ramTotalGb,
            "ramPercent": ramPercent,
        }
