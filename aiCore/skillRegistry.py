import inspect
import threading
from typing import Any, Callable, Optional, TypeVar, Union, overload

SkillFunc = TypeVar("SkillFunc", bound=Callable[..., Any])


class SkillRegistry:
    """Registro central de ferramentas (skills) expostas ao Tool Calling do Qwen."""

    _instance: Optional["SkillRegistry"] = None
    _lock: threading.Lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls) -> "SkillRegistry":
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
            self._skillsMap: dict[str, Callable[..., Any]] = {}
            self._toolsList: list[Callable[..., Any]] = []
            self._registryLock: threading.Lock = threading.Lock()
            self._initialized = True

    def register(self, func: SkillFunc) -> SkillFunc:
        """Registra uma função Python tipada no dicionário de execução e na lista de tools do Ollama."""
        skillName: str = func.__name__
        with self._registryLock:
            self._skillsMap[skillName] = func
            if func not in self._toolsList:
                # Substitui caso já exista função com mesmo nome na lista
                self._toolsList = [
                    existingFunc
                    for existingFunc in self._toolsList
                    if existingFunc.__name__ != skillName
                ]
                self._toolsList.append(func)
        return func

    def getToolsList(self) -> list[Callable[..., Any]]:
        """Retorna a lista de funções para o parâmetro tools do ollama.chat()."""
        with self._registryLock:
            return list(self._toolsList)

    def getSkillsMap(self) -> dict[str, Callable[..., Any]]:
        """Retorna o dicionário de execução mapeando nome da função para o callable."""
        with self._registryLock:
            return dict(self._skillsMap)

    def getSkill(self, skillName: str) -> Optional[Callable[..., Any]]:
        """Busca uma skill específica pelo nome."""
        with self._registryLock:
            return self._skillsMap.get(skillName)

    def listSkillNames(self) -> list[str]:
        """Lista os nomes de todas as skills registradas."""
        with self._registryLock:
            return list(self._skillsMap.keys())

    def executeSkill(self, skillName: str, arguments: Optional[dict[str, Any]] = None) -> str:
        """Executa uma skill registrada de forma resiliente e retorna o resultado como string."""
        skillFunc: Optional[Callable[..., Any]] = self.getSkill(skillName)
        if skillFunc is None:
            return f"Erro: A ferramenta '{skillName}' não está registrada no sistema."

        safeArgs: dict[str, Any] = dict(arguments or {})
        try:
            sig = inspect.signature(skillFunc)
            validArgs: dict[str, Any] = {
                key: value for key, value in safeArgs.items() if key in sig.parameters
            }
            result: Any = skillFunc(**validArgs)
            return str(result)
        except Exception as error:
            errorMsg: str = f"Erro ao executar a ferramenta '{skillName}': {error}"
            print(f"[ERRO][SkillRegistry] {errorMsg}")
            return errorMsg


skillRegistry: SkillRegistry = SkillRegistry()


@overload
def registerSkill(func: SkillFunc) -> SkillFunc: ...


@overload
def registerSkill(func: None = None) -> Callable[[SkillFunc], SkillFunc]: ...


def registerSkill(
    func: Optional[SkillFunc] = None,
) -> Union[SkillFunc, Callable[[SkillFunc], SkillFunc]]:
    """Decorador @registerSkill para auto-registro de ferramentas da IA."""
    if func is not None and callable(func):
        return skillRegistry.register(func)

    def decorator(innerFunc: SkillFunc) -> SkillFunc:
        return skillRegistry.register(innerFunc)

    return decorator
