import re
from typing import Any, Callable, Optional

import ollama

from aiCore.eventBus import EventBus, eventBus
from aiCore.memoryStore import MemoryStore, memoryStore
from aiCore.skillRegistry import SkillRegistry, skillRegistry
from appConfig.settings import Settings, settings


class JarvisBrain:
    """Orquestrador principal do LLM local (qwen3.5:9b via Ollama) com suporte a Tool Calling."""

    def __init__(
        self,
        appSettings: Optional[Settings] = None,
        store: Optional[MemoryStore] = None,
        registry: Optional[SkillRegistry] = None,
        bus: Optional[EventBus] = None,
        maxToolIterations: int = 5,
    ) -> None:
        self.settings: Settings = appSettings or settings
        self.memoryStore: MemoryStore = store or memoryStore
        self.skillRegistry: SkillRegistry = registry or skillRegistry
        self.eventBus: EventBus = bus or eventBus
        self.maxToolIterations: int = maxToolIterations
        self.isGamingMode: bool = False

        # Assina o evento de Modo Jogo no barramento para pausar tarefas pesadas de segundo plano
        self.eventBus.subscribe("gamingModeChanged", self._onGamingModeChanged)

    def _onGamingModeChanged(self, payload: Any) -> None:
        """Atualiza o estado interno de Modo Jogo quando o uso da RTX 4060 Ti ultrapassa 85%."""
        if isinstance(payload, dict):
            self.isGamingMode = bool(payload.get("isGamingMode", False))

    def canRunBackgroundTask(self, currentGpuUsage: Optional[float] = None) -> bool:
        """Verifica se tarefas pesadas de segundo plano podem rodar sem afetar o Modo Jogo (>85% GPU)."""
        if currentGpuUsage is not None and currentGpuUsage > self.settings.gpuGamingThreshold:
            return False
        return not self.isGamingMode

    def processInput(self, userText: str) -> str:
        """Processa a entrada do usuário, executa o loop de Tool Calling e retorna a resposta final."""
        cleanInput: str = userText.strip()
        if not cleanInput:
            return "Por favor, digite ou fale um comando."

        self.memoryStore.addMessage(role="user", content=cleanInput)

        try:
            for _ in range(self.maxToolIterations):
                messages: list[dict[str, Any]] = self.memoryStore.getMessages()
                toolsList: list[Callable[..., Any]] = self.skillRegistry.getToolsList()

                # Orçamento de VRAM (8 GB): sempre utiliza options={'num_ctx': 8192}
                response: Any = ollama.chat(
                    model=self.settings.ollamaModel,
                    messages=messages,
                    tools=toolsList if toolsList else None,
                    options={"num_ctx": self.settings.ollamaNumCtx},
                    think=self.settings.ollamaThink,
                )

                assistantMessage: Any = response.message
                toolCalls: Any = getattr(assistantMessage, "tool_calls", None)

                if toolCalls:
                    serializedToolCalls: list[dict[str, Any]] = []
                    for call in toolCalls:
                        funcObj: Any = getattr(call, "function", None)
                        funcName: str = getattr(funcObj, "name", "")
                        funcArgs: dict[str, Any] = dict(getattr(funcObj, "arguments", {}) or {})
                        serializedToolCalls.append(
                            {
                                "function": {
                                    "name": funcName,
                                    "arguments": funcArgs,
                                }
                            }
                        )

                    self.memoryStore.addMessage(
                        role="assistant",
                        content=getattr(assistantMessage, "content", "") or "",
                        toolCalls=serializedToolCalls,
                    )

                    for callDict in serializedToolCalls:
                        skillName: str = callDict["function"]["name"]
                        skillArgs: dict[str, Any] = callDict["function"]["arguments"]

                        print(f"[IA][ToolCall] Executando skill '{skillName}' com argumentos: {skillArgs}")
                        self.eventBus.publish(
                            "skillExecuted",
                            {"skillName": skillName, "arguments": skillArgs},
                        )

                        toolOutput: str = self.skillRegistry.executeSkill(skillName, skillArgs)
                        self.memoryStore.addMessage(
                            role="tool",
                            content=toolOutput,
                            toolName=skillName,
                        )
                    continue

                rawContent: str = getattr(assistantMessage, "content", "") or ""
                finalContent: str = self._cleanResponseText(rawContent)

                self.memoryStore.addMessage(role="assistant", content=finalContent)
                self.eventBus.publish("assistantResponseReady", {"text": finalContent})
                return finalContent

            fallbackMsg: str = "Limite máximo de chamadas de ferramentas atingido para esta solicitação."
            self.memoryStore.addMessage(role="assistant", content=fallbackMsg)
            return fallbackMsg

        except Exception as error:
            errorMessage: str = (
                f"Não foi possível comunicar com o Ollama ({self.settings.ollamaModel}). "
                f"Verifique se o serviço do Ollama está ativo. Detalhes: {error}"
            )
            print(f"[ERRO][JarvisBrain] {errorMessage}")
            self.eventBus.publish("brainError", {"error": str(error)})
            return errorMessage

    def runBackgroundPrompt(
        self,
        prompt: str,
        systemInstruction: Optional[str] = None,
        currentGpuUsage: Optional[float] = None,
    ) -> Optional[str]:
        """Executa uma análise isolada em segundo plano, pausando automaticamente em Modo Jogo (>85% GPU)."""
        if not self.canRunBackgroundTask(currentGpuUsage=currentGpuUsage):
            print("[INFO][JarvisBrain] Modo Jogo ativo (GPU > 85%). Tarefa de segundo plano pausada.")
            return None

        messages: list[dict[str, str]] = []
        if systemInstruction:
            messages.append({"role": "system", "content": systemInstruction})
        messages.append({"role": "user", "content": prompt})

        try:
            response: Any = ollama.chat(
                model=self.settings.ollamaModel,
                messages=messages,
                options={"num_ctx": self.settings.ollamaNumCtx},
                think=self.settings.ollamaThink,
            )
            rawContent: str = getattr(response.message, "content", "") or ""
            return self._cleanResponseText(rawContent)
        except Exception as error:
            print(f"[ERRO][JarvisBrain] Falha na tarefa de segundo plano: {error}")
            return None

    def _cleanResponseText(self, text: str) -> str:
        """Remove blocos de raciocínio <think>...</think> caso presentes e limpa espaços extras."""
        cleaned: str = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        return cleaned.strip()
