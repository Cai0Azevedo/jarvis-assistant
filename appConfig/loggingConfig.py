import logging
import threading
from pathlib import Path
from typing import Optional

from appConfig.settings import settings

_loggingLock: threading.Lock = threading.Lock()
_isConfigured: bool = False


class _InteractiveConsoleFilter(logging.Filter):
    """Bloqueia logs rotineiros de sensores e hardware no console durante o chat interativo."""

    _SILENCED_PREFIXES: tuple[str, ...] = (
        "jarvis.sensorReader",
        "jarvis.lcdDisplay",
        "jarvis.networkConnection",
        "jarvis.serialConnection",
        "jarvis.hardware",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith(self._SILENCED_PREFIXES):
            return False
        return record.levelno >= logging.CRITICAL


def setupLogging(
    logFilePath: Optional[Path] = None,
    consoleLevel: int = logging.CRITICAL,
) -> logging.Logger:
    """Configura o sistema de logs gravando eventos de hardware em ``jarvis.log`` sem poluir o terminal."""
    global _isConfigured
    baseLogger: logging.Logger = logging.getLogger("jarvis")

    with _loggingLock:
        if _isConfigured and baseLogger.handlers:
            return baseLogger

        baseLogger.setLevel(logging.DEBUG)
        baseLogger.propagate = False
        baseLogger.handlers.clear()

        targetPath: Path = logFilePath or settings.logFilePath
        formatter: logging.Formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s][%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        try:
            fileHandler: logging.FileHandler = logging.FileHandler(
                targetPath,
                mode="a",
                encoding="utf-8",
            )
            fileHandler.setLevel(logging.DEBUG)
            fileHandler.setFormatter(formatter)
            baseLogger.addHandler(fileHandler)
        except Exception:
            baseLogger.addHandler(logging.NullHandler())

        consoleHandler: logging.StreamHandler = logging.StreamHandler()
        consoleHandler.setLevel(consoleLevel)
        consoleHandler.setFormatter(formatter)
        consoleHandler.addFilter(_InteractiveConsoleFilter())
        baseLogger.addHandler(consoleHandler)

        _isConfigured = True
        return baseLogger


def getLogger(name: str = "jarvis") -> logging.Logger:
    """Retorna um logger filho configurado sob a hierarquia ``jarvis``."""
    setupLogging()
    normalizedName: str = name if name.startswith("jarvis") else f"jarvis.{name}"
    return logging.getLogger(normalizedName)
