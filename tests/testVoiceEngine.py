import array
import math
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from aiCore.eventBus import EventBus
from voiceEngine import (
    ALERTA,
    RESPOSTA,
    TELEMETRIA,
    AudioPriority,
    AudioQueue,
    Priority,
    VoiceOutput,
)


class TestVoiceEngine(unittest.TestCase):
    """Testes unitários do pipeline de saída de voz neural (AudioQueue + VoiceOutput em CPU)."""

    def setUp(self) -> None:
        self.bus: EventBus = EventBus()
        self.playedBuffers: list[bytes] = []
        self.speakingStatesDuringPlayback: list[tuple[bool, bool]] = []

        def mockPlaybackHandler(wavBytes: bytes) -> None:
            self.playedBuffers.append(wavBytes)
            self.speakingStatesDuringPlayback.append(
                (self.queue.isSpeaking, self.queue.isMuted)
            )

        self.queue: AudioQueue = AudioQueue(
            bus=self.bus,
            sampleRate=22050,
            enableHelmetEffect=True,
            playbackHandler=mockPlaybackHandler,
            autoStart=False,
        )
        self.voice: VoiceOutput = VoiceOutput(
            queueManager=self.queue,
            bus=self.bus,
            autoSubscribe=True,
        )

    def tearDown(self) -> None:
        self.voice.shutdown()

    def testPriorityOrderAndExports(self) -> None:
        self.assertEqual(int(Priority.ALERTA), 1)
        self.assertEqual(int(Priority.RESPOSTA), 2)
        self.assertEqual(int(Priority.TELEMETRIA), 3)
        self.assertIs(AudioPriority, Priority)
        self.assertEqual((ALERTA, RESPOSTA, TELEMETRIA), (1, 2, 3))

        playedOrder: list[str] = []
        self.bus.subscribe(
            "voicePlaybackStarted",
            lambda p: playedOrder.append(p.get("text", "")),
        )

        rawSample: bytes = AudioQueue.ensureWavBytes(b"\x00\x01" * 100)
        self.queue.enqueue(rawSample, priority=Priority.TELEMETRIA, metadata={"text": "telemetria"})
        self.queue.enqueue(rawSample, priority=Priority.RESPOSTA, metadata={"text": "resposta"})
        self.queue.enqueue(rawSample, priority=Priority.ALERTA, metadata={"text": "alerta"})

        self.assertEqual(self.queue.getPendingCount(), 3)
        while self.queue.processNextSync():
            pass

        time.sleep(0.06)
        self.assertEqual(playedOrder, ["alerta", "resposta", "telemetria"])

    def testSpeakingAndMutedFlagsAndLifecycleEvents(self) -> None:
        startedEvents: list[dict[str, Any]] = []
        finishedEvents: list[dict[str, Any]] = []

        self.bus.subscribe("voicePlaybackStarted", lambda p: startedEvents.append(p))
        self.bus.subscribe("voicePlaybackFinished", lambda p: finishedEvents.append(p))

        self.assertFalse(self.queue.isSpeaking)
        self.assertFalse(self.queue.isMuted)

        self.voice.speak("Sistemas online, senhor.", priority=Priority.RESPOSTA)
        self.assertTrue(self.queue.processNextSync())
        time.sleep(0.06)

        # Durante a reprodução, isSpeaking e isMuted devem ter ficado ativos
        self.assertEqual(self.speakingStatesDuringPlayback, [(True, True)])
        # Após concluir, devem retornar a False
        self.assertFalse(self.queue.isSpeaking)
        self.assertFalse(self.queue.isMuted)

        self.assertEqual(len(startedEvents), 1)
        self.assertTrue(startedEvents[0]["isSpeaking"])
        self.assertEqual(len(finishedEvents), 1)
        self.assertFalse(finishedEvents[0]["isSpeaking"])

    def testAlertPreemptsLowerPriorityPlayback(self) -> None:
        playbackEntered = threading.Event()

        def slowPlayback(_wavBytes: bytes) -> None:
            playbackEntered.set()
            # Aguarda até stopCurrent() sinalizar _abortCurrentEvent
            self.queue._abortCurrentEvent.wait(timeout=1.0)

        preemptQueue = AudioQueue(
            bus=self.bus,
            playbackHandler=slowPlayback,
            autoStart=True,
        )
        try:
            sample = AudioQueue.ensureWavBytes(b"\x10\x00" * 200)
            preemptQueue.enqueue(sample, priority=Priority.RESPOSTA, metadata={"text": "longo"})
            self.assertTrue(playbackEntered.wait(timeout=0.5))
            self.assertTrue(preemptQueue.isSpeaking)

            # Enfileirar um ALERTA enquanto RESPOSTA toca deve acionar stopCurrent()
            preemptQueue.enqueue(sample, priority=Priority.ALERTA, metadata={"text": "urgente"})
            self.assertTrue(preemptQueue._abortCurrentEvent.is_set())
        finally:
            preemptQueue.stop()

    def testHelmetEffectDspFilterLatencyAndWavFormat(self) -> None:
        # Gera 0.25s de senoide a 440Hz em 22050Hz (~5512 amostras)
        sampleRate = 22050
        numSamples = sampleRate // 4
        sineSamples = array.array(
            "h",
            (
                int(8000.0 * math.sin(2.0 * math.pi * 440.0 * i / sampleRate))
                for i in range(numSamples)
            ),
        )
        inputWav: bytes = AudioQueue.ensureWavBytes(sineSamples.tobytes(), sampleRate=sampleRate)

        startNs: int = time.perf_counter_ns()
        filteredWav: bytes = AudioQueue.applyHelmetEffect(inputWav, defaultSampleRate=sampleRate)
        elapsedMs: float = (time.perf_counter_ns() - startNs) / 1_000_000.0

        self.assertTrue(filteredWav.startswith(b"RIFF"))
        self.assertEqual(filteredWav[8:12], b"WAVE")
        self.assertNotEqual(filteredWav, inputWav)
        # Deve ser extremamente leve (< 15ms em Python puro)
        self.assertLess(elapsedMs, 25.0)

    def testVoiceOutputTextSanitizationAndCpuOnlyEnforcement(self) -> None:
        dirtyText: str = (
            "<think>raciocinio interno</think>"
            "## Relatório de Status 🤖\n"
            "A **RTX 4060 Ti** está em `62°C`! Veja [detalhes](https://localhost:8080).\n"
            "```python\nprint('codigo ignorado')\n```"
        )
        cleaned: str = VoiceOutput.sanitizeText(dirtyText)
        self.assertNotIn("<think>", cleaned)
        self.assertNotIn("raciocinio interno", cleaned)
        self.assertNotIn("```", cleaned)
        self.assertNotIn("print", cleaned)
        self.assertNotIn("https://", cleaned)
        self.assertNotIn("**", cleaned)
        self.assertNotIn("##", cleaned)
        self.assertIn("Relatório de Status", cleaned)
        self.assertIn("62 graus Celsius", cleaned)

        cpuEnv: dict[str, str] = self.voice._getCpuOnlyEnvironment()
        self.assertEqual(cpuEnv["CUDA_VISIBLE_DEVICES"], "-1")
        self.assertEqual(cpuEnv["ORT_DISABLE_CUDA"], "1")

    def testVoiceOutputEventBusIntegration(self) -> None:
        enqueuedItems: list[tuple[int, str]] = []
        self.bus.subscribe(
            "voicePlaybackStarted",
            lambda p: enqueuedItems.append((int(p["priority"]), str(p.get("text", "")))),
        )

        self.bus.publish(
            "assistantResponseReady",
            {"text": "**Processador** operando normalmente em `54°C`."},
            asyncDispatch=False,
        )
        self.bus.publish("userReturned", {"source": "pir_sensor"}, asyncDispatch=False)
        self.bus.publish(
            "gpuHighTempAlert",
            {"gpuTemp": 84, "gpuName": "RTX 4060 Ti"},
            asyncDispatch=False,
        )
        self.bus.publish("cpuHighTempAlert", {"cpuTemp": 88}, asyncDispatch=False)

        self.assertEqual(self.queue.getPendingCount(), 4)
        while self.queue.processNextSync():
            pass
        time.sleep(0.08)

        self.assertEqual(len(enqueuedItems), 4)
        # Os dois alertas térmicos (prioridade ALERTA = 1) devem tocar antes das respostas (RESPOSTA = 2)
        self.assertEqual(enqueuedItems[0][0], int(Priority.ALERTA))
        self.assertEqual(enqueuedItems[1][0], int(Priority.ALERTA))
        self.assertEqual(enqueuedItems[2][0], int(Priority.RESPOSTA))
        self.assertEqual(enqueuedItems[3][0], int(Priority.RESPOSTA))


if __name__ == "__main__":
    unittest.main()
