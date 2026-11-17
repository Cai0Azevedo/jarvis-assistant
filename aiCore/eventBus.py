import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

EventCallback = Callable[[Any], None]


class EventBus:
    """Barramento Pub-Sub assíncrono (Observer Pattern) para desacoplamento entre módulos."""

    _instance: Optional["EventBus"] = None
    _lock: threading.Lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls) -> "EventBus":
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
            self._subscribers: dict[str, list[EventCallback]] = {}
            self._subscribersLock: threading.Lock = threading.Lock()
            self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
                max_workers=4,
                thread_name_prefix="jarvisEventBus",
            )
            self._initialized = True

    def subscribe(self, eventName: str, callback: EventCallback) -> None:
        """Assina um evento específico no barramento."""
        with self._subscribersLock:
            if eventName not in self._subscribers:
                self._subscribers[eventName] = []
            if callback not in self._subscribers[eventName]:
                self._subscribers[eventName].append(callback)

    def unsubscribe(self, eventName: str, callback: EventCallback) -> None:
        """Remove a assinatura de um callback para um evento."""
        with self._subscribersLock:
            if eventName in self._subscribers and callback in self._subscribers[eventName]:
                self._subscribers[eventName].remove(callback)

    def publish(self, eventName: str, payload: Any = None, asyncDispatch: bool = True) -> None:
        """Publica um evento no barramento de forma assíncrona (ou síncrona se especificado)."""
        with self._subscribersLock:
            callbacks: list[EventCallback] = list(self._subscribers.get(eventName, []))

        for callback in callbacks:
            if asyncDispatch:
                self._executor.submit(self._safeInvoke, eventName, callback, payload)
            else:
                self._safeInvoke(eventName, callback, payload)

    def _safeInvoke(self, eventName: str, callback: EventCallback, payload: Any) -> None:
        """Executa o callback protegendo o barramento contra exceções não tratadas."""
        try:
            callback(payload)
        except Exception as error:
            print(f"[ERRO][EventBus] Falha ao processar evento '{eventName}': {error}")

    def getSubscribersCount(self, eventName: str) -> int:
        """Retorna a quantidade de assinantes registrados para um evento."""
        with self._subscribersLock:
            return len(self._subscribers.get(eventName, []))

    def clearSubscribers(self, eventName: Optional[str] = None) -> None:
        """Remove assinantes de um evento específico ou de todo o barramento."""
        with self._subscribersLock:
            if eventName is None:
                self._subscribers.clear()
            else:
                self._subscribers.pop(eventName, None)

    def shutdown(self) -> None:
        """Encerra o pool de threads do barramento de eventos."""
        try:
            self._executor.shutdown(wait=False)
        except Exception:
            pass


eventBus: EventBus = EventBus()
