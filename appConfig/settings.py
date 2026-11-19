import os
import threading
from pathlib import Path
from typing import Any, Optional


class Settings:
    """Singleton de configuração global do sistema JARVIS."""

    _instance: Optional["Settings"] = None
    _lock: threading.Lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls) -> "Settings":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return

            self.baseDir: Path = Path(__file__).resolve().parent.parent
            self.envPath: Path = self.baseDir / ".env"
            self.loadEnvFile(self.envPath)

            # Configurações do Ollama e IA
            self.ollamaModel: str = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")
            self.ollamaHost: str = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
            self.ollamaNumCtx: int = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
            self.ollamaThink: bool = os.getenv("OLLAMA_THINK", "false").strip().lower() == "true"
            self.maxMemoryMessages: int = int(os.getenv("MAX_MEMORY_MESSAGES", "20"))
            self.systemPrompt: str = os.getenv(
                "SYSTEM_PROMPT",
                (
                    "Você é o JARVIS, um assistente pessoal local de inteligência artificial "
                    "executando no Windows. Responda sempre em português claro, direto e objetivo. "
                    "Quando o usuário perguntar sobre o desempenho, temperaturas, uso de CPU, RAM "
                    "ou placa de vídeo (RTX 4060 Ti), utilize sempre as ferramentas disponíveis "
                    "para consultar os sensores reais antes de responder."
                ),
            )

            # Limites de Hardware e Histerese do Modo Jogo (RTX 4060 Ti / Ryzen 5 5600)
            self.gameModeGpuThreshold: float = float(
                os.getenv("GAME_MODE_GPU_THRESHOLD", os.getenv("GPU_GAMING_THRESHOLD", "85.0"))
            )
            self.gpuGamingThreshold: float = self.gameModeGpuThreshold
            self.gameModeEnterDuration: float = float(
                os.getenv("GAME_MODE_ENTER_DURATION", "4.0")
            )
            self.gameModeExitDuration: float = float(
                os.getenv("GAME_MODE_EXIT_DURATION", "8.0")
            )
            self.gameModeCooldown: float = float(
                os.getenv("GAME_MODE_COOLDOWN", "15.0")
            )
            self.gpuTempAlertThreshold: float = float(os.getenv("GPU_TEMP_ALERT_THRESHOLD", "80.0"))
            self.cpuTempAlertThreshold: float = float(os.getenv("CPU_TEMP_ALERT_THRESHOLD", "85.0"))
            self.telemetryIntervalSeconds: float = float(os.getenv("TELEMETRY_INTERVAL_SECONDS", "2.0"))

            # Configurações do Arduino UNO / ESP-01 (Serial USB ou Wi-Fi TCP Socket)
            self.arduinoConnectionType: str = (
                os.getenv("ARDUINO_CONNECTION_TYPE", "wifi").strip().lower()
            )
            self.arduinoHost: str = os.getenv("ARDUINO_HOST", "192.168.7.4").strip()
            self.arduinoPort: int = int(os.getenv("ARDUINO_PORT", "8080"))
            self.arduinoSocketTimeout: float = float(
                os.getenv("ARDUINO_SOCKET_TIMEOUT", "3.0")
            )
            self.comPort: str = os.getenv("COM_PORT", "COM3")
            self.baudRate: int = int(os.getenv("BAUD_RATE", "9600"))
            self.serialTimeout: float = float(os.getenv("SERIAL_TIMEOUT", "1.0"))
            self.lcdRefreshIntervalSeconds: float = float(
                os.getenv("LCD_REFRESH_INTERVAL_SECONDS", "3.5")
            )
            self.presenceGreetingTimeout: int = int(
                os.getenv("PRESENCE_GREETING_TIMEOUT", "4")
            )
            self.presenceAwayTimeoutSeconds: int = int(
                os.getenv("PRESENCE_AWAY_TIMEOUT_SECONDS", "600")
            )
            self.presenceTimeoutSeconds: int = int(
                os.getenv(
                    "PRESENCE_TIMEOUT_SECONDS",
                    str(self.presenceAwayTimeoutSeconds),
                )
            )
            self.enablePresenceDetection: bool = (
                os.getenv("ENABLE_PRESENCE_DETECTION", "true").strip().lower() == "true"
            )
            self.logFilePath: Path = self.baseDir / os.getenv(
                "LOG_FILE_NAME", "jarvis.log"
            )

            # Configurações do Motor de Voz Neural (Fase 4 - Piper TTS 100% em CPU)
            self.voiceEnabled: bool = (
                os.getenv("VOICE_ENABLED", "true").strip().lower() == "true"
            )
            self.piperExecutablePath: str = os.getenv(
                "PIPER_EXECUTABLE_PATH",
                str(self.baseDir / "voiceEngine" / "piper" / "piper.exe"),
            )
            self.piperModelPath: str = os.getenv(
                "PIPER_MODEL_PATH",
                str(self.baseDir / "voiceEngine" / "models" / "pt_BR-faber-medium.onnx"),
            )
            self.piperConfigPath: str = os.getenv(
                "PIPER_CONFIG_PATH",
                f"{self.piperModelPath}.json",
            )
            self.piperSampleRate: int = int(os.getenv("PIPER_SAMPLE_RATE", "22050"))
            self.piperCpuThreads: int = int(os.getenv("PIPER_CPU_THREADS", "4"))
            self.voiceHelmetEffectEnabled: bool = (
                os.getenv("VOICE_HELMET_EFFECT_ENABLED", "true").strip().lower() == "true"
            )

            # Configurações do Serviço de E-mail (IMAP)
            self.imapServer: str = os.getenv("IMAP_SERVER", "imap.gmail.com")
            self.imapPort: int = int(os.getenv("IMAP_PORT", "993"))
            self.emailUser: str = os.getenv("EMAIL_USER", "")
            self.emailPassword: str = os.getenv("EMAIL_PASSWORD", "")
            self.emailCheckIntervalSeconds: int = int(os.getenv("EMAIL_CHECK_INTERVAL_SECONDS", "60"))

            self._initialized = True

    def loadEnvFile(self, envFilePath: Path) -> None:
        """Carrega variáveis do arquivo .env sem depender de bibliotecas externas."""
        if not envFilePath.exists():
            return

        try:
            with open(envFilePath, mode="r", encoding="utf-8") as envFile:
                for rawLine in envFile:
                    line: str = rawLine.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    cleanKey: str = key.strip()
                    cleanValue: str = value.strip().strip("'").strip('"')
                    if cleanKey and cleanKey not in os.environ:
                        os.environ[cleanKey] = cleanValue
        except Exception as error:
            print(f"[AVISO] Falha ao ler arquivo .env em {envFilePath}: {error}")

    def getOllamaOptions(self) -> dict[str, Any]:
        """Retorna o dicionário de opções obrigatórias do Ollama respeitando o orçamento de VRAM."""
        return {"num_ctx": self.ollamaNumCtx}


settings: Settings = Settings()
