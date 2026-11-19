import contextlib
import io
import sys
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main as jarvisMain
from aiCore.eventBus import EventBus
from appConfig.settings import settings
from pcHardware.pcStatusFacade import PcStatusFacade, pcStatusFacade


class TestPcHardware(unittest.TestCase):
    """Testes unitários de histerese/cooldown do Modo Jogo e falas padronizadas no console."""

    def setUp(self) -> None:
        self.bus: EventBus = EventBus()
        self.facade: PcStatusFacade = pcStatusFacade
        self.facade.resetGamingModeState()
        self.capturedGamingEvents: list[dict[str, Any]] = []
        self.bus.subscribe("gamingModeChanged", self._onGamingModeEvent)

    def tearDown(self) -> None:
        self.bus.unsubscribe("gamingModeChanged", self._onGamingModeEvent)
        self.facade.resetGamingModeState()

    def _onGamingModeEvent(self, payload: Any) -> None:
        if isinstance(payload, dict):
            self.capturedGamingEvents.append(payload)

    def _simulateGpuSnapshot(self, gpuUsage: float, currentTime: float) -> dict[str, Any]:
        mockCpuRam: dict[str, Any] = {
            "cpuUsage": 35.0,
            "cpuTemp": 55.0,
            "afterburnerOnline": True,
            "ramUsedGb": 12.0,
            "ramTotalGb": 32.0,
            "ramPercent": 37.5,
        }
        mockGpu: dict[str, Any] = {
            "gpuName": "NVIDIA GeForce RTX 4060 Ti",
            "gpuUsage": gpuUsage,
            "gpuTemp": 62.0,
            "vramUsedGb": 3.5,
            "vramTotalGb": 8.0,
            "vramPercent": 43.8,
            "gpuAvailable": True,
        }
        with (
            patch.object(self.facade.cpuRamMonitor, "getCpuRamMetrics", return_value=mockCpuRam),
            patch.object(self.facade.gpuMonitor, "getGpuMetrics", return_value=mockGpu),
        ):
            return self.facade.getSnapshot(currentTime=currentTime)

    def testTransientGpuSpikeDoesNotTriggerGamingMode(self) -> None:
        # Pico passageiro de 2 segundos a 90% (< gameModeEnterDuration = 4.0s)
        snap0: dict[str, Any] = self._simulateGpuSnapshot(gpuUsage=90.0, currentTime=100.0)
        self.assertFalse(snap0["isGamingMode"])
        self.assertFalse(self.facade.isGamingModeActive)

        snap1: dict[str, Any] = self._simulateGpuSnapshot(gpuUsage=90.0, currentTime=102.0)
        self.assertFalse(snap1["isGamingMode"])
        self.assertFalse(self.facade.isGamingModeActive)

        # Carga volta ao normal (30%) antes de completar 4s -> timer de entrada é resetado
        snap2: dict[str, Any] = self._simulateGpuSnapshot(gpuUsage=30.0, currentTime=103.0)
        self.assertFalse(snap2["isGamingMode"])
        self.assertIsNone(self.facade.highGpuStartTime)

        # Novo pico isolado de 2s em t=106..108 continua não disparando
        self._simulateGpuSnapshot(gpuUsage=92.0, currentTime=106.0)
        snap3: dict[str, Any] = self._simulateGpuSnapshot(gpuUsage=92.0, currentTime=108.0)
        self.assertFalse(snap3["isGamingMode"])
        time.sleep(0.05)
        self.assertEqual(len(self.capturedGamingEvents), 0)

    def testSustainedHighGpuLoadActivatesGamingMode(self) -> None:
        # Carga sustentada a 90% por 5 segundos (>= gameModeEnterDuration = 4.0s)
        self._simulateGpuSnapshot(gpuUsage=90.0, currentTime=200.0)
        self.assertFalse(self.facade.isGamingModeActive)

        self._simulateGpuSnapshot(gpuUsage=91.0, currentTime=202.5)
        self.assertFalse(self.facade.isGamingModeActive)

        snapActive: dict[str, Any] = self._simulateGpuSnapshot(gpuUsage=90.0, currentTime=205.0)
        self.assertTrue(snapActive["isGamingMode"])
        self.assertTrue(self.facade.isGamingModeActive)

        time.sleep(0.05)
        self.assertEqual(len(self.capturedGamingEvents), 1)
        self.assertIs(self.capturedGamingEvents[0]["active"], True)

    def testSustainedNormalGpuLoadDeactivatesGamingModeAndRespectsCooldown(self) -> None:
        # 1) Ativa Modo Jogo com 5s a 90% (t=300 -> t=305)
        self._simulateGpuSnapshot(gpuUsage=90.0, currentTime=300.0)
        self._simulateGpuSnapshot(gpuUsage=90.0, currentTime=305.0)
        self.assertTrue(self.facade.isGamingModeActive)

        # 2) Queda breve de 3s a 20% (< gameModeExitDuration = 8.0s) NÃO deve sair do Modo Jogo
        self._simulateGpuSnapshot(gpuUsage=20.0, currentTime=306.0)
        self._simulateGpuSnapshot(gpuUsage=20.0, currentTime=309.0)
        self.assertTrue(self.facade.isGamingModeActive)

        # Retorno a 90% em t=310 zera o contador de saída
        self._simulateGpuSnapshot(gpuUsage=90.0, currentTime=310.0)
        self.assertIsNone(self.facade.lowGpuStartTime)
        self.assertTrue(self.facade.isGamingModeActive)

        # 3) Retorno sustentado à carga normal (20%) por 9s (t=312 -> t=321 >= 8.0s) DEVE desativar
        self._simulateGpuSnapshot(gpuUsage=20.0, currentTime=312.0)
        self.assertTrue(self.facade.isGamingModeActive)

        snapInactive: dict[str, Any] = self._simulateGpuSnapshot(gpuUsage=20.0, currentTime=321.0)
        self.assertFalse(snapInactive["isGamingMode"])
        self.assertFalse(self.facade.isGamingModeActive)

        time.sleep(0.05)
        self.assertEqual(len(self.capturedGamingEvents), 2)
        self.assertIs(self.capturedGamingEvents[1]["active"], False)

        # 4) Cooldown (15.0s após saída em t=321): carga alta em t=322..328 permanece bloqueada
        self._simulateGpuSnapshot(gpuUsage=95.0, currentTime=322.0)
        self._simulateGpuSnapshot(gpuUsage=95.0, currentTime=328.0)
        self.assertFalse(self.facade.isGamingModeActive)

        # Após expirar o cooldown de 15s (t=337 >= 321 + 15), o Modo Jogo pode reativar
        self._simulateGpuSnapshot(gpuUsage=95.0, currentTime=337.0)
        self.assertTrue(self.facade.isGamingModeActive)

    def testConsoleAssistantNoticesFormattingAndPromptShield(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            jarvisMain.onUserPresenceDetected({"source": "pir_sensor"})
        presenceOutput: str = buf.getvalue()
        self.assertIn(
            "\r\033[K\nJARVIS: Bem-vindo de volta, senhor. Todos os sistemas operacionais e prontos.\nVocê: ",
            presenceOutput,
        )

        bufEnter = io.StringIO()
        with contextlib.redirect_stdout(bufEnter):
            jarvisMain.onGamingModeChanged({"active": True})
        self.assertIn(
            "JARVIS: Modo Jogo ativado. Suspendendo tarefas de fundo para priorizar a performance gráfica.\nVocê: ",
            bufEnter.getvalue(),
        )

        bufExit = io.StringIO()
        with contextlib.redirect_stdout(bufExit):
            jarvisMain.onGamingModeChanged({"active": False})
        self.assertIn(
            "JARVIS: Modo Jogo desativado. Retomando processamento padrão do assistente.\nVocê: ",
            bufExit.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
