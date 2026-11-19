import array
import io
import itertools
import logging
import math
import queue
import threading
import wave
from enum import IntEnum
from typing import Any, Callable, Optional

from aiCore.eventBus import EventBus, eventBus
from appConfig.loggingConfig import getLogger
from appConfig.settings import Settings, settings

try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    import pygame
except ImportError:
    pygame = None

try:
    import winsound
except ImportError:
    winsound = None

logger: logging.Logger = getLogger("jarvis.audioQueue")


class Priority(IntEnum):
    """Níveis de prioridade da fila de reprodução de voz do JARVIS."""

    ALERTA = 1
    RESPOSTA = 2
    TELEMETRIA = 3


AudioPriority = Priority
ALERTA: int = int(Priority.ALERTA)
RESPOSTA: int = int(Priority.RESPOSTA)
TELEMETRIA: int = int(Priority.TELEMETRIA)

AudioPostProcessor = Callable[[bytes, int], bytes]


class AudioQueue:
    """Gerenciador thread-safe de reprodução de áudio com fila de prioridades e filtro DSP em CPU."""

    DEFAULT_SAMPLE_RATE: int = 22050
    DEFAULT_CHANNELS: int = 1
    DEFAULT_SAMPLE_WIDTH: int = 2  # 16-bit PCM

    def __init__(
        self,
        bus: Optional[EventBus] = None,
        appSettings: Optional[Settings] = None,
        sampleRate: Optional[int] = None,
        enableHelmetEffect: Optional[bool] = None,
        playbackHandler: Optional[Callable[[bytes], None]] = None,
        autoStart: bool = True,
    ) -> None:
        self.eventBus: EventBus = bus or eventBus
        self.settings: Settings = appSettings or settings
        self.sampleRate: int = int(sampleRate or self.settings.piperSampleRate or self.DEFAULT_SAMPLE_RATE)
        self.enableHelmetEffect: bool = (
            enableHelmetEffect
            if enableHelmetEffect is not None
            else self.settings.voiceHelmetEffectEnabled
        )
        self.playbackHandler: Optional[Callable[[bytes], None]] = playbackHandler
        self.autoStart: bool = autoStart

        self._queue: queue.PriorityQueue[tuple[int, int, bytes, dict[str, Any]]] = (
            queue.PriorityQueue()
        )
        self._sequenceCounter = itertools.count(1)
        self._stateLock: threading.Lock = threading.Lock()
        self._playbackLock: threading.Lock = threading.Lock()

        self._speakingEvent: threading.Event = threading.Event()
        self._mutedEvent: threading.Event = threading.Event()
        self._abortCurrentEvent: threading.Event = threading.Event()
        self._stopWorkerEvent: threading.Event = threading.Event()

        self._currentPriority: Optional[int] = None
        self._workerThread: Optional[threading.Thread] = None
        self._postProcessors: list[AudioPostProcessor] = []
        self._pygameInitialized: bool = False

    @property
    def isSpeaking(self) -> bool:
        """Retorna True enquanto o assistente estiver reproduzindo voz (inibe captura do microfone)."""
        return self._speakingEvent.is_set()

    @isSpeaking.setter
    def isSpeaking(self, value: bool) -> None:
        if value:
            self._speakingEvent.set()
            self._mutedEvent.set()
        else:
            self._speakingEvent.clear()
            self._mutedEvent.clear()

    @property
    def isMuted(self) -> bool:
        """Retorna True se o microfone deve permanecer silenciado para prevenção de eco acústico."""
        return self._mutedEvent.is_set() or self._speakingEvent.is_set()

    @isMuted.setter
    def isMuted(self, value: bool) -> None:
        if value:
            self._mutedEvent.set()
        else:
            self._mutedEvent.clear()

    def start(self) -> None:
        """Inicia a thread daemon contínua de reprodução de áudio."""
        with self._stateLock:
            if self._workerThread is not None and self._workerThread.is_alive():
                return
            self._stopWorkerEvent.clear()
            self._workerThread = threading.Thread(
                target=self._playbackLoop,
                name="jarvisAudioQueueWorker",
                daemon=True,
            )
            self._workerThread.start()

    def stop(self) -> None:
        """Interrompe a reprodução atual, limpa a fila e encerra a thread worker."""
        self._stopWorkerEvent.set()
        self.stopCurrent()
        self.clearQueue()

        worker: Optional[threading.Thread] = self._workerThread
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=2.0)
        with self._stateLock:
            self._workerThread = None
            self.isSpeaking = False

    def addPostProcessor(self, processor: AudioPostProcessor) -> None:
        """Adiciona um estágio customizado de pós-processamento de áudio PCM/WAV."""
        with self._stateLock:
            if processor not in self._postProcessors:
                self._postProcessors.append(processor)

    def clearPostProcessors(self) -> None:
        """Remove pós-processadores customizados registrados."""
        with self._stateLock:
            self._postProcessors.clear()

    def enqueue(
        self,
        audioData: bytes,
        priority: int = int(Priority.RESPOSTA),
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Adiciona um buffer de áudio PCM/WAV na fila de prioridades (preemptando se for ALERTA)."""
        if not audioData:
            return False

        normalizedPriority: int = int(priority)
        itemMeta: dict[str, Any] = dict(metadata or {})

        with self._stateLock:
            seq: int = next(self._sequenceCounter)
            currentPrio: Optional[int] = self._currentPriority
            self._queue.put((normalizedPriority, seq, audioData, itemMeta))

        # Se chegar um ALERTA crítico enquanto um áudio de menor prioridade toca, interrompe o atual
        if (
            normalizedPriority == int(Priority.ALERTA)
            and self.isSpeaking
            and currentPrio is not None
            and currentPrio > int(Priority.ALERTA)
        ):
            logger.debug("Preemptando áudio de prioridade %s para tocar ALERTA imediato.", currentPrio)
            self.stopCurrent()

        if self.autoStart:
            self.start()

        return True

    def stopCurrent(self) -> None:
        """Interrompe imediatamente a reprodução do áudio em andamento."""
        self._abortCurrentEvent.set()
        try:
            if sd is not None:
                sd.stop()
        except Exception:
            pass
        try:
            if pygame is not None and self._pygameInitialized and pygame.mixer.get_init():
                pygame.mixer.stop()
        except Exception:
            pass
        try:
            if winsound is not None:
                winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    def clearQueue(self) -> None:
        """Esvazia todos os itens pendentes na fila de prioridade."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break

    def getPendingCount(self) -> int:
        """Retorna a quantidade de áudios aguardando na fila."""
        return self._queue.qsize()

    def processNextSync(self) -> bool:
        """Processa sincronicamente o próximo item da fila (útil para testes determinísticos)."""
        try:
            priority, _seq, rawAudio, metadata = self._queue.get_nowait()
        except queue.Empty:
            return False

        try:
            self._playSingleItem(priority, rawAudio, metadata)
        finally:
            self._queue.task_done()
        return True

    @classmethod
    def ensureWavBytes(
        cls,
        audioData: bytes,
        sampleRate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        sampleWidth: int = DEFAULT_SAMPLE_WIDTH,
    ) -> bytes:
        """Garante que o buffer esteja encapsulado em um cabeçalho RIFF/WAV válido em memória."""
        if len(audioData) >= 12 and audioData[:4] == b"RIFF" and audioData[8:12] == b"WAVE":
            return audioData

        pcmBytes: bytes = audioData
        if len(pcmBytes) % 2 != 0:
            pcmBytes = pcmBytes[:-1]

        outBuffer = io.BytesIO()
        with wave.open(outBuffer, "wb") as wavFile:
            wavFile.setnchannels(channels)
            wavFile.setsampwidth(sampleWidth)
            wavFile.setframerate(sampleRate)
            wavFile.writeframes(pcmBytes)
        return outBuffer.getvalue()

    @classmethod
    def extractPcmAndFormat(
        cls,
        audioData: bytes,
        defaultSampleRate: int = DEFAULT_SAMPLE_RATE,
    ) -> tuple[bytes, int, int, int]:
        """Extrai (pcmBytes, sampleRate, channels, sampleWidth) de um buffer WAV ou PCM bruto."""
        if len(audioData) >= 12 and audioData[:4] == b"RIFF" and audioData[8:12] == b"WAVE":
            try:
                with wave.open(io.BytesIO(audioData), "rb") as wavReader:
                    channels: int = wavReader.getnchannels()
                    sampleWidth: int = wavReader.getsampwidth()
                    sampleRate: int = wavReader.getframerate()
                    pcmBytes: bytes = wavReader.readframes(wavReader.getnframes())
                    return (pcmBytes, sampleRate, channels, sampleWidth)
            except Exception as error:
                logger.debug("Falha ao ler cabeçalho WAV (%s); tratando como PCM bruto.", error)

        cleanPcm: bytes = audioData if len(audioData) % 2 == 0 else audioData[:-1]
        return (cleanPcm, defaultSampleRate, cls.DEFAULT_CHANNELS, cls.DEFAULT_SAMPLE_WIDTH)

    @classmethod
    def applyHelmetEffect(
        cls,
        audioData: bytes,
        defaultSampleRate: int = DEFAULT_SAMPLE_RATE,
    ) -> bytes:
        """Aplica filtro passa-faixa + ressonância sutil de capacete/comunicador (<5ms em CPU)."""
        pcmBytes, sampleRate, channels, sampleWidth = cls.extractPcmAndFormat(
            audioData,
            defaultSampleRate=defaultSampleRate,
        )
        if sampleWidth != 2 or len(pcmBytes) < 4:
            return cls.ensureWavBytes(pcmBytes, sampleRate, channels, sampleWidth)

        samples = array.array("h")
        samples.frombytes(pcmBytes)

        # Coeficientes IIR de 1ª ordem para passa-alta (~260 Hz) e passa-baixa (~3500 Hz)
        dt: float = 1.0 / max(8000, sampleRate)
        rcHigh: float = 1.0 / (2.0 * math.pi * 260.0)
        alphaHigh: float = rcHigh / (rcHigh + dt)

        rcLow: float = 1.0 / (2.0 * math.pi * 3500.0)
        alphaLow: float = dt / (rcLow + dt)

        # Delay curto (~1.3 ms) para ressonância metálica sutil estilo capacete Mark III
        combDelaySamples: int = max(1, int(sampleRate * 0.0013))
        combMix: float = 0.16
        presenceGain: float = 1.12

        prevIn: float = 0.0
        highState: float = 0.0
        lowState: float = 0.0
        totalSamples: int = len(samples)

        output = array.array("h", samples)
        for idx in range(totalSamples):
            x: float = float(samples[idx])
            # Passa-alta (remove graves profundos abaixo de 260 Hz)
            highState = alphaHigh * (highState + x - prevIn)
            prevIn = x
            # Passa-baixa (suaviza agudos acima de 3.5 kHz)
            lowState = lowState + alphaLow * (highState - lowState)

            # Ressonância curta de comunicador/capacete
            delayed: float = float(output[idx - combDelaySamples]) if idx >= combDelaySamples else 0.0
            voiced: float = (lowState + (combMix * delayed)) * presenceGain

            # Soft-clipping simétrico dentro de 16-bit signed
            if voiced > 32767.0:
                voiced = 32767.0
            elif voiced < -32768.0:
                voiced = -32768.0
            output[idx] = int(voiced)

        return cls.ensureWavBytes(output.tobytes(), sampleRate, channels, sampleWidth)

    def _applyPostProcessingPipeline(self, audioData: bytes) -> bytes:
        """Executa o estágio de pós-processamento (efeito capacete + filtros customizados)."""
        processed: bytes = self.ensureWavBytes(audioData, sampleRate=self.sampleRate)
        if self.enableHelmetEffect:
            try:
                processed = self.applyHelmetEffect(processed, defaultSampleRate=self.sampleRate)
            except Exception as error:
                logger.debug("Erro no filtro de capacete DSP (%s); mantendo áudio original.", error)

        with self._stateLock:
            customProcessors: list[AudioPostProcessor] = list(self._postProcessors)

        for processor in customProcessors:
            try:
                processed = processor(processed, self.sampleRate)
            except Exception as error:
                logger.debug("Erro em pós-processador de áudio customizado: %s", error)

        return self.ensureWavBytes(processed, sampleRate=self.sampleRate)

    def _playSingleItem(
        self,
        priority: int,
        rawAudio: bytes,
        metadata: dict[str, Any],
    ) -> None:
        """Executa o ciclo completo de reprodução de um item com publicação no EventBus."""
        with self._playbackLock:
            self._abortCurrentEvent.clear()
            with self._stateLock:
                self._currentPriority = priority
                self.isSpeaking = True

            processedWav: bytes = self._applyPostProcessingPipeline(rawAudio)
            startPayload: dict[str, Any] = {
                "priority": priority,
                "byteLength": len(processedWav),
                "text": metadata.get("text", ""),
                "isSpeaking": True,
            }
            self.eventBus.publish("voicePlaybackStarted", startPayload)

            try:
                if not self._abortCurrentEvent.is_set():
                    self._dispatchToAudioBackend(processedWav)
            except Exception as error:
                logger.debug("Falha durante reprodução de áudio: %s", error)
            finally:
                with self._stateLock:
                    self._currentPriority = None
                    self.isSpeaking = False

                finishPayload: dict[str, Any] = {
                    "priority": priority,
                    "text": metadata.get("text", ""),
                    "aborted": self._abortCurrentEvent.is_set(),
                    "isSpeaking": False,
                }
                self.eventBus.publish("voicePlaybackFinished", finishPayload)

    def _dispatchToAudioBackend(self, wavBytes: bytes) -> None:
        """Envia o buffer WAV para o backend de áudio configurado (100% em CPU)."""
        if self.playbackHandler is not None:
            self.playbackHandler(wavBytes)
            return

        # 1. Tenta sounddevice (baixa latência via PortAudio em CPU)
        if sd is not None:
            try:
                pcmBytes, sampleRate, channels, _width = self.extractPcmAndFormat(
                    wavBytes,
                    defaultSampleRate=self.sampleRate,
                )
                samples = array.array("h")
                samples.frombytes(pcmBytes)
                with sd.RawOutputStream(
                    samplerate=sampleRate,
                    channels=channels,
                    dtype="int16",
                ) as stream:
                    chunkSize: int = 2048 * channels
                    rawView = memoryview(samples.tobytes())
                    for offset in range(0, len(rawView), chunkSize):
                        if self._abortCurrentEvent.is_set():
                            break
                        stream.write(rawView[offset : offset + chunkSize])
                return
            except Exception as error:
                logger.debug("Fallback do sounddevice para próximo backend (%s).", error)

        # 2. Tenta pygame.mixer
        if pygame is not None:
            try:
                if not self._pygameInitialized or not pygame.mixer.get_init():
                    pygame.mixer.init(frequency=self.sampleRate, size=-16, channels=1, buffer=512)
                    self._pygameInitialized = True
                soundObj = pygame.mixer.Sound(buffer=wavBytes)
                channel = soundObj.play()
                while channel is not None and channel.get_busy():
                    if self._abortCurrentEvent.is_set():
                        channel.stop()
                        break
                    self._abortCurrentEvent.wait(0.02)
                return
            except Exception as error:
                logger.debug("Fallback do pygame.mixer para winsound (%s).", error)

        # 3. Fallback nativo do Windows (winsound em memória, zero dependências externas)
        if winsound is not None:
            try:
                winsound.PlaySound(
                    wavBytes,
                    winsound.SND_MEMORY | winsound.SND_NODEFAULT,
                )
                return
            except Exception as error:
                logger.debug("winsound indisponível ou interrompido: %s", error)

    def _playbackLoop(self) -> None:
        """Loop contínuo da thread daemon que consome a fila de prioridades."""
        while not self._stopWorkerEvent.is_set():
            try:
                priority, _seq, rawAudio, metadata = self._queue.get(timeout=0.15)
            except queue.Empty:
                continue

            try:
                if not self._stopWorkerEvent.is_set():
                    self._playSingleItem(priority, rawAudio, metadata)
            except Exception as error:
                logger.debug("Erro inesperado no worker da AudioQueue: %s", error)
            finally:
                self._queue.task_done()


audioQueue: AudioQueue = AudioQueue(autoStart=False)
