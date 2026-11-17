import threading
from collections import deque
from typing import Any, Optional

from appConfig.settings import settings


class MemoryStore:
    """Gerencia o histórico de conversa em janela deslizante e o cache de IDs de e-mails lidos."""

    _instance: Optional["MemoryStore"] = None
    _lock: threading.Lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls, *args: Any, **kwargs: Any) -> "MemoryStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        maxMessages: Optional[int] = None,
        systemPrompt: Optional[str] = None,
        maxEmailCacheSize: int = 1000,
    ) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self.maxMessages: int = maxMessages if maxMessages is not None else settings.maxMemoryMessages
            self.systemPrompt: str = systemPrompt if systemPrompt is not None else settings.systemPrompt
            self.maxEmailCacheSize: int = maxEmailCacheSize

            self._history: deque[dict[str, Any]] = deque()
            self._emailOrder: deque[str] = deque()
            self._emailSet: set[str] = set()
            self._storeLock: threading.Lock = threading.Lock()
            self._initialized = True

    def setSystemPrompt(self, systemPrompt: str) -> None:
        """Atualiza o prompt de sistema principal do assistente."""
        with self._storeLock:
            self.systemPrompt = systemPrompt

    def addMessage(
        self,
        role: str,
        content: str,
        toolCalls: Optional[list[dict[str, Any]]] = None,
        toolName: Optional[str] = None,
    ) -> None:
        """Adiciona uma mensagem ao histórico respeitando o limite da janela deslizante."""
        message: dict[str, Any] = {"role": role, "content": content}
        if toolCalls is not None:
            message["tool_calls"] = toolCalls
        if toolName is not None:
            message["tool_name"] = toolName
        self.addRawMessage(message)

    def addRawMessage(self, message: dict[str, Any]) -> None:
        """Adiciona um dicionário de mensagem diretamente ao histórico com poda automática."""
        with self._storeLock:
            self._history.append(dict(message))
            self._trimHistory()

    def _trimHistory(self) -> None:
        """Mantém apenas as últimas N mensagens e evita iniciar a janela com resposta órfã de tool."""
        while len(self._history) > self.maxMessages:
            self._history.popleft()

        while self._history and self._history[0].get("role") == "tool":
            self._history.popleft()

    def getMessages(self) -> list[dict[str, Any]]:
        """Retorna a lista completa de mensagens (incluindo o system prompt) pronta para o Ollama."""
        with self._storeLock:
            messages: list[dict[str, Any]] = []
            if self.systemPrompt:
                messages.append({"role": "system", "content": self.systemPrompt})
            messages.extend(list(self._history))
            return messages

    def clearHistory(self) -> None:
        """Limpa o histórico de conversas mantendo o prompt de sistema e o cache de e-mails."""
        with self._storeLock:
            self._history.clear()

    def markEmailAsRead(self, emailId: str) -> None:
        """Registra um ID de e-mail como já processado no cache em memória."""
        cleanId: str = str(emailId).strip()
        if not cleanId:
            return
        with self._storeLock:
            if cleanId in self._emailSet:
                return
            if len(self._emailOrder) >= self.maxEmailCacheSize:
                oldestId: str = self._emailOrder.popleft()
                self._emailSet.discard(oldestId)
            self._emailOrder.append(cleanId)
            self._emailSet.add(cleanId)

    def isEmailRead(self, emailId: str) -> bool:
        """Verifica se um ID de e-mail já foi processado anteriormente."""
        cleanId: str = str(emailId).strip()
        with self._storeLock:
            return cleanId in self._emailSet

    def getReadEmailsCount(self) -> int:
        """Retorna a quantidade de IDs de e-mail armazenados no cache."""
        with self._storeLock:
            return len(self._emailSet)

    def clearEmailCache(self) -> None:
        """Limpa o cache de IDs de e-mails lidos."""
        with self._storeLock:
            self._emailOrder.clear()
            self._emailSet.clear()


memoryStore: MemoryStore = MemoryStore()
