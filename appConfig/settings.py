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

            # Limites de Hardware e Modo Jogo (RTX 4060 Ti / Ryzen 5 5600)
            self.gpuGamingThreshold: float = float(os.getenv("GPU_GAMING_THRESHOLD", "85.0"))
            self.gpuTempAlertThreshold: float = float(os.getenv("GPU_TEMP_ALERT_THRESHOLD", "80.0"))
            self.cpuTempAlertThreshold: float = float(os.getenv("CPU_TEMP_ALERT_THRESHOLD", "85.0"))
            self.telemetryIntervalSeconds: float = float(os.getenv("TELEMETRY_INTERVAL_SECONDS", "2.0"))

            # Configurações do Arduino UNO (Porta Serial)
            self.comPort: str = os.getenv("COM_PORT", "COM3")
            self.baudRate: int = int(os.getenv("BAUD_RATE", "9600"))
            self.serialTimeout: float = float(os.getenv("SERIAL_TIMEOUT", "1.0"))

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
