import array
import io
import json
import logging
import math
import os
import re
import subprocess
import threading
import wave
from pathlib import Path
from typing import Any, Callable, Optional

from aiCore.eventBus import EventBus, eventBus
from appConfig.loggingConfig import getLogger
from appConfig.settings import Settings, settings
from voiceEngine.audioQueue import AudioQueue, Priority, audioQueue

try:
    from piper import PiperVoice
except ImportError:
    PiperVoice = None

logger: logging.Logger = getLogger("jarvis.voiceOutput")


class VoiceOutput:
    """Motor de síntese neural Piper TTS (100% em CPU) integrado ao EventBus e AudioQueue."""

    _THINK_BLOCK_RE: re.Pattern[str] = re.compile(r"<think>.*?</think>", flags=re.DOTALL)
    _CODE_FENCE_RE: re.Pattern[str] = re.compile(r"```.*?```", flags=re.DOTALL)
    _INLINE_CODE_RE: re.Pattern[str] = re.compile(r"`([^`]+)`")
    _MD_LINK_RE: re.Pattern[str] = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
    _URL_RE: re.Pattern[str] = re.compile(r"https?://\S+")
    _MD_SYMBOLS_RE: re.Pattern[str] = re.compile(r"[#*_~>|]+")
    _EMOJI_RE: re.Pattern[str] = re.compile(
        "["
        "\U0001F300-\U0001FAFF"
        "\U00002700-\U000027BF"
        "\U00002600-\U000026FF"
        "]+",
        flags=re.UNICODE,
    )
    _WHITESPACE_RE: re.Pattern[str] = re.compile(r"\s+")

    def __init__(
        self,
        queueManager: Optional[AudioQueue] = None,
        bus: Optional[EventBus] = None,
        appSettings: Optional[Settings] = None,
        modelPath: Optional[str] = None,
        configPath: Optional[str] = None,
        piperExecutablePath: Optional[str] = None,
        cpuThreads: Optional[int] = None,
        synthesizerFn: Optional[Callable[[str], bytes]] = None,
        autoSubscribe: bool = True,
    ) -> None:
        self.settings: Settings = appSettings or settings
        self.eventBus: EventBus = bus or eventBus
        self.audioQueue: AudioQueue = queueManager or audioQueue

        self.modelPath: Path = Path(modelPath or self.settings.piperModelPath)
        self.configPath: Path = Path(
            configPath
            or self.settings.piperConfigPath
            or f"{self.modelPath}.json"
        )
        self.piperExecutablePath: Path = Path(
            piperExecutablePath or self.settings.piperExecutablePath
        )
        self.cpuThreads: int = max(
            1,
            int(cpuThreads if cpuThreads is not None else self.settings.piperCpuThreads),
        )
        self.sampleRate: int = self._resolveSampleRate()
        self.synthesizerFn: Optional[Callable[[str], bytes]] = synthesizerFn

        self._lock: threading.Lock = threading.Lock()
        self._piperVoice: Any = None
        self._isSubscribed: bool = False
        self.isMockMode: bool = False
        self._warnedMockMode: bool = False

        self._initializeCpuRuntime()

        if autoSubscribe:
            self.subscribeToEvents()

    def _resolveSampleRate(self) -> int:
        """Lê a taxa de amostragem do arquivo .onnx.json do modelo Piper ou usa fallback."""
        if self.configPath.exists():
            try:
                with open(self.configPath, mode="r", encoding="utf-8") as cfgFile:
                    cfgData: dict[str, Any] = json.load(cfgFile)
                    audioSection: Any = cfgData.get("audio", {})
                    if isinstance(audioSection, dict) and "sample_rate" in audioSection:
                        return int(audioSection["sample_rate"])
            except Exception as error:
                logger.debug("Não foi possível ler sample_rate de %s: %s", self.configPath, error)
        return int(self.settings.piperSampleRate or AudioQueue.DEFAULT_SAMPLE_RATE)

    def _getCpuOnlyEnvironment(self) -> dict[str, str]:
        """Monta variáveis de ambiente que forçam execução 100% em CPU (zero VRAM na RTX 4060 Ti)."""
        env: dict[str, str] = dict(os.environ)
        env["CUDA_VISIBLE_DEVICES"] = "-1"
        env["OMP_NUM_THREADS"] = str(self.cpuThreads)
        env["MKL_NUM_THREADS"] = str(self.cpuThreads)
        env["ORT_DISABLE_CUDA"] = "1"
        return env

    def _initializeCpuRuntime(self) -> None:
        """Inicializa o Piper TTS estritamente em CPU ou ativa fallback seguro em modo Mock."""
        if self.synthesizerFn is not None:
            self.isMockMode = False
            return

        if not self.modelPath.exists():
            self._enterMockMode(
                f"Modelo Piper ONNX não encontrado em '{self.modelPath}'."
            )
            return

        if PiperVoice is not None:
            try:
                configArg: Optional[str] = (
                    str(self.configPath) if self.configPath.exists() else None
                )
                # Restrição crítica de hardware: use_cuda=False preserva os 8 GB da RTX 4060 Ti
                self._piperVoice = PiperVoice.load(
                    str(self.modelPath),
                    config_path=configArg,
                    use_cuda=False,
                )
                self.isMockMode = False
                logger.info(
                    "PiperVoice carregado em CPU com modelo '%s' (%s threads).",
                    self.modelPath.name,
                    self.cpuThreads,
                )
                return
            except Exception as error:
                logger.debug("Falha ao carregar biblioteca PiperVoice (%s); tentando binário CLI.", error)

        if self.piperExecutablePath.exists():
            self.isMockMode = False
            logger.info(
                "Piper CLI configurado em CPU ('%s', modelo='%s', threads=%s).",
                self.piperExecutablePath,
                self.modelPath.name,
                self.cpuThreads,
            )
            return

        self._enterMockMode(
            f"Binário Piper ('{self.piperExecutablePath}') ou biblioteca 'piper-tts' indisponível."
        )

    def _enterMockMode(self, reason: str) -> None:
        """Ativa o sintetizador em memória simulado para não quebrar o sistema sem modelo baixado."""
        self.isMockMode = True
        if not self._warnedMockMode:
            logger.warning(
                "%s VoiceOutput operando em modo simulação/mock (CPU).",
                reason,
            )
            self._warnedMockMode = True

    def subscribeToEvents(self) -> None:
        """Inscreve o motor de voz nos tópicos do EventBus."""
        with self._lock:
            if self._isSubscribed:
                return
            self.eventBus.subscribe("assistantResponseReady", self._onAssistantResponseReady)
            self.eventBus.subscribe("userReturned", self._onUserReturned)
            self.eventBus.subscribe("gpuHighTempAlert", self._onGpuHighTempAlert)
            self.eventBus.subscribe("cpuHighTempAlert", self._onCpuHighTempAlert)
            self._isSubscribed = True

    def unsubscribeFromEvents(self) -> None:
        """Remove as inscrições do motor de voz no EventBus."""
        with self._lock:
            if not self._isSubscribed:
                return
            self.eventBus.unsubscribe("assistantResponseReady", self._onAssistantResponseReady)
            self.eventBus.unsubscribe("userReturned", self._onUserReturned)
            self.eventBus.unsubscribe("gpuHighTempAlert", self._onGpuHighTempAlert)
            self.eventBus.unsubscribe("cpuHighTempAlert", self._onCpuHighTempAlert)
            self._isSubscribed = False

    @classmethod
    def sanitizeText(cls, text: str) -> str:
        """Higieniza o texto removendo Markdown, blocos de código, links e emojis antes da síntese."""
        if not text:
            return ""

        cleaned: str = cls._THINK_BLOCK_RE.sub(" ", str(text))
        cleaned = cls._CODE_FENCE_RE.sub(" ", cleaned)
        cleaned = cls._INLINE_CODE_RE.sub(r"\1", cleaned)
        cleaned = cls._MD_LINK_RE.sub(r"\1", cleaned)
        cleaned = cls._URL_RE.sub(" ", cleaned)
        cleaned = cls._EMOJI_RE.sub(" ", cleaned)
        cleaned = cls._MD_SYMBOLS_RE.sub(" ", cleaned)
        cleaned = cleaned.replace("°C", " graus Celsius ")
        cleaned = cls._WHITESPACE_RE.sub(" ", cleaned).strip()
        return cleaned

    def synthesizeToMemory(self, cleanText: str) -> bytes:
        """Gera o áudio WAV/PCM inteiramente em memória (sem I/O de disco) usando CPU."""
        if not cleanText:
            return b""

        if self.synthesizerFn is not None:
            rawOut: bytes = self.synthesizerFn(cleanText)
            return AudioQueue.ensureWavBytes(rawOut, sampleRate=self.sampleRate)

        # 1. Biblioteca PiperVoice em memória (CPU)
        if self._piperVoice is not None and not self.isMockMode:
            try:
                wavBuffer = io.BytesIO()
                with wave.open(wavBuffer, "wb") as wavFile:
                    wavFile.setnchannels(1)
                    wavFile.setsampwidth(2)
                    wavFile.setframerate(self.sampleRate)
                    if hasattr(self._piperVoice, "synthesize"):
                        self._piperVoice.synthesize(cleanText, wavFile)
                    elif hasattr(self._piperVoice, "synthesize_stream_raw"):
                        for pcmChunk in self._piperVoice.synthesize_stream_raw(cleanText):
                            wavFile.writeframes(pcmChunk)
                return wavBuffer.getvalue()
            except Exception as error:
                self._enterMockMode(f"Erro na inferência PiperVoice em CPU: {error}")

        # 2. Executável piper.exe via stdout em memória (CPU com threads limitadas)
        if (
            not self.isMockMode
            and self.piperExecutablePath.exists()
            and self.modelPath.exists()
        ):
            try:
                cmd: list[str] = [
                    str(self.piperExecutablePath),
                    "--model",
                    str(self.modelPath),
                    "--output-raw",
                ]
                if self.configPath.exists():
                    cmd.extend(["--config", str(self.configPath)])

                proc = subprocess.run(
                    cmd,
                    input=cleanText.encode("utf-8"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=self._getCpuOnlyEnvironment(),
                    timeout=10.0,
                    check=True,
                )
                if proc.stdout:
                    return AudioQueue.ensureWavBytes(proc.stdout, sampleRate=self.sampleRate)
            except Exception as error:
                self._enterMockMode(f"Falha ao executar Piper CLI em CPU: {error}")

        # 3. Fallback Mock em memória (gera pulso PCM 16-bit curto para manter pipeline funcional)
        return self._generateMockWavInMemory(cleanText)

    def _generateMockWavInMemory(self, cleanText: str) -> bytes:
        """Gera um buffer WAV PCM 16-bit sintético leve em memória para modo simulação/teste."""
        durationSec: float = min(0.06, max(0.02, len(cleanText) * 0.001))
        numSamples: int = max(64, int(self.sampleRate * durationSec))
        freqHz: float = 440.0
        pcmSamples = array.array(
            "h",
            (
                int(1200.0 * math.sin(2.0 * math.pi * freqHz * i / self.sampleRate))
                for i in range(numSamples)
            ),
        )
        return AudioQueue.ensureWavBytes(
            pcmSamples.tobytes(),
            sampleRate=self.sampleRate,
        )

    def speak(
        self,
        text: str,
        priority: int = int(Priority.RESPOSTA),
    ) -> bool:
        """Higieniza o texto, sintetiza o WAV em memória na CPU e enfileira no AudioQueue."""
        if not self.settings.voiceEnabled:
            return False

        cleanText: str = self.sanitizeText(text)
        if not cleanText:
            return False

        try:
            wavBytes: bytes = self.synthesizeToMemory(cleanText)
            if not wavBytes:
                return False

            logger.debug(
                "Voz sintetizada em memória (%s bytes, prio=%s): '%s'",
                len(wavBytes),
                priority,
                cleanText[:60],
            )
            return self.audioQueue.enqueue(
                wavBytes,
                priority=int(priority),
                metadata={"text": cleanText},
            )
        except Exception as error:
            logger.debug("Erro ao sintetizar ou enfileirar fala: %s", error)
            return False

    def shutdown(self) -> None:
        """Remove inscrições do EventBus e encerra a fila de reprodução."""
        self.unsubscribeFromEvents()
        self.audioQueue.stop()

    def _onAssistantResponseReady(self, payload: Any) -> None:
        if isinstance(payload, dict):
            text: str = str(payload.get("text", "")).strip()
        elif isinstance(payload, str):
            text = payload.strip()
        else:
            return

        if text:
            self.speak(text, priority=int(Priority.RESPOSTA))

    def _onUserReturned(self, payload: Any) -> None:
        greeting: str = "Bem-vindo de volta, senhor. Sistemas online e prontos."
        if isinstance(payload, dict) and payload.get("greeting"):
            greeting = str(payload["greeting"])
        self.speak(greeting, priority=int(Priority.RESPOSTA))

    def _onGpuHighTempAlert(self, payload: Any) -> None:
        gpuTemp: Any = 80
        gpuName: str = "placa de vídeo"
        if isinstance(payload, dict):
            gpuTemp = payload.get("gpuTemp", 80)
            gpuName = str(payload.get("gpuName", "RTX 4060 Ti"))
        alertText: str = (
            f"Alerta térmico crítico. Temperatura elevada na {gpuName}: {gpuTemp} graus Celsius."
        )
        self.speak(alertText, priority=int(Priority.ALERTA))

    def _onCpuHighTempAlert(self, payload: Any) -> None:
        cpuTemp: Any = 85
        if isinstance(payload, dict):
            cpuTemp = payload.get("cpuTemp", 85)
        alertText: str = (
            f"Alerta térmico crítico. Temperatura elevada no processador: {cpuTemp} graus Celsius."
        )
        self.speak(alertText, priority=int(Priority.ALERTA))


voiceOutput: VoiceOutput = VoiceOutput(autoSubscribe=False)
