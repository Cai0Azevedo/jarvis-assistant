from typing import Any

from aiCore.eventBus import eventBus
from aiCore.jarvisBrain import JarvisBrain
from aiCore.memoryStore import memoryStore
from aiCore.skillRegistry import registerSkill, skillRegistry
from appConfig.settings import settings
from pcHardware.pcStatusFacade import pcStatusFacade


@registerSkill
def getPcTelemetrySnapshot() -> str:
    """Consulta em tempo real o uso e a temperatura da CPU (Ryzen 5 5600), uso de memória RAM e uso, temperatura e VRAM da placa de vídeo (RTX 4060 Ti).

    Returns:
        str: Resumo detalhado com porcentagens de uso, temperaturas em graus Celsius, consumo de RAM/VRAM em GB e estado do Modo Jogo.
    """
    snapshot: dict[str, Any] = pcStatusFacade.getSnapshot()
    return pcStatusFacade.formatSummary(snapshot)


def onGpuHighTempAlert(payload: Any) -> None:
    """Callback do EventBus para alerta de temperatura elevada da GPU (>80°C)."""
    if isinstance(payload, dict):
        gpuTemp: Any = payload.get("gpuTemp")
        gpuName: Any = payload.get("gpuName", "RTX 4060 Ti")
        print(f"\n[ALERTA][EventBus] Temperatura elevada na {gpuName}: {gpuTemp}°C!")


def onCpuHighTempAlert(payload: Any) -> None:
    """Callback do EventBus para alerta de temperatura elevada da CPU."""
    if isinstance(payload, dict):
        cpuTemp: Any = payload.get("cpuTemp")
        print(f"\n[ALERTA][EventBus] Temperatura elevada no Ryzen 5 5600: {cpuTemp}°C!")


def onGamingModeChanged(payload: Any) -> None:
    """Callback do EventBus para mudança de estado do Modo Jogo (>85% de uso da GPU)."""
    if isinstance(payload, dict):
        isGaming: bool = bool(payload.get("isGamingMode", False))
        gpuUsage: Any = payload.get("gpuUsage", 0.0)
        if isGaming:
            print(
                f"\n[MODO JOGO][EventBus] Ativado (GPU em {gpuUsage}%). "
                "Tarefas pesadas de segundo plano foram pausadas."
            )
        else:
            print(
                f"\n[MODO JOGO][EventBus] Desativado (GPU em {gpuUsage}%). "
                "Tarefas de segundo plano liberadas."
            )


def setupEventListeners() -> None:
    """Registra os ouvintes de console no EventBus para monitorar eventos da Fase 1."""
    eventBus.subscribe("gpuHighTempAlert", onGpuHighTempAlert)
    eventBus.subscribe("cpuHighTempAlert", onCpuHighTempAlert)
    eventBus.subscribe("gamingModeChanged", onGamingModeChanged)


def printBootHeader() -> None:
    """Exibe o cabeçalho de inicialização e o diagnóstico inicial de hardware e IA."""
    print("=" * 75)
    print(" JARVIS - Assistente Pessoal Local de IA (Fase 1 - Modo Console)")
    print("=" * 75)
    print(
        f"[CONFIG] Modelo LLM: {settings.ollamaModel} | "
        f"Contexto (VRAM Budget): num_ctx={settings.ollamaNumCtx} | "
        f"Janela de Memória: {settings.maxMemoryMessages} msgs"
    )
    print(f"[SKILLS] Ferramentas registradas para Tool Calling: {skillRegistry.listSkillNames()}")

    initialSnapshot: dict[str, Any] = pcStatusFacade.getSnapshot()
    print(f"[HARDWARE] {pcStatusFacade.formatSummary(initialSnapshot)}")
    print("-" * 75)
    print("Comandos rápidos do console:")
    print("  /status  -> Exibe telemetria instantânea de CPU, RAM e GPU")
    print("  /limpar  -> Limpa o histórico de conversa da memória")
    print("  /sair    -> Encerra o assistente")
    print("=" * 75)


def runConsoleLoop() -> None:
    """Inicializa os módulos principais e executa o loop interativo no terminal."""
    setupEventListeners()
    brain: JarvisBrain = JarvisBrain()
    printBootHeader()

    try:
        while True:
            try:
                userInput: str = input("\nVocê: ").strip()
            except EOFError:
                print("\n[INFO] Entrada encerrada. Desligando o JARVIS...")
                break

            if not userInput:
                continue

            lowerInput: str = userInput.lower()
            if lowerInput in ("/sair", "sair", "exit", "quit"):
                print("[INFO] Encerrando o JARVIS. Até logo!")
                break

            if lowerInput in ("/status", "/hw"):
                currentSnapshot: dict[str, Any] = pcStatusFacade.getSnapshot()
                print(f"\n[TELEMETRIA] {pcStatusFacade.formatSummary(currentSnapshot)}")
                continue

            if lowerInput == "/limpar":
                memoryStore.clearHistory()
                print("\n[MEMÓRIA] Histórico de conversa reiniciado.")
                continue

            assistantReply: str = brain.processInput(userInput)
            print(f"\nJARVIS: {assistantReply}")

    except KeyboardInterrupt:
        print("\n[INFO] Interrupção detectada (Ctrl+C). Encerrando o JARVIS...")
    finally:
        pcStatusFacade.shutdown()
        eventBus.shutdown()


if __name__ == "__main__":
    runConsoleLoop()
