import signal
import time
from dataclasses import dataclass
from typing import Any

from aiCore.eventBus import EventBus, eventBus
from aiCore.jarvisBrain import JarvisBrain
from aiCore.memoryStore import memoryStore
from aiCore.skillRegistry import registerSkill, skillRegistry
from appConfig.settings import Settings, settings
from arduinoHub.lcdDisplay import LcdDisplay, lcdDisplay
from arduinoHub.sensorReader import SensorReader, sensorReader
from arduinoHub.serialConnection import SerialConnection, serialConnection
from pcHardware.pcStatusFacade import PcStatusFacade, pcStatusFacade


@dataclass
class JarvisContainer:
    """Contêiner central de dependências dos subsistemas do JARVIS."""

    appSettings: Settings
    bus: EventBus
    hardwareFacade: PcStatusFacade
    serialConn: SerialConnection
    display: LcdDisplay
    sensors: SensorReader
    brain: JarvisBrain


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


def onArduinoConnectionChanged(payload: Any) -> None:
    """Callback do EventBus para mudanças no estado da conexão serial com o Arduino."""
    if isinstance(payload, dict):
        comPort: str = str(payload.get("comPort", settings.comPort))
        isConnected: bool = bool(payload.get("isConnected", False))
        if isConnected:
            print(f"\n[ARDUINO][EventBus] Conexão restabelecida em {comPort}.")
        else:
            print(f"\n[ARDUINO][EventBus] Conexão perdida em {comPort}. Fallback para modo Mock ativo.")


def onArduinoButtonPressed(payload: Any) -> None:
    """Callback do EventBus para eventos de botão físico recebidos do Arduino UNO."""
    if isinstance(payload, dict):
        buttonId: Any = payload.get("button")
        state: Any = payload.get("state", "PRESSED")
        print(f"\n[ARDUINO][EventBus] Botão {buttonId} acionado ({state}).")


def setupEventListeners(bus: EventBus = eventBus) -> None:
    """Registra os ouvintes de console no EventBus para monitorar hardware e ArduinoHub."""
    bus.subscribe("gpuHighTempAlert", onGpuHighTempAlert)
    bus.subscribe("cpuHighTempAlert", onCpuHighTempAlert)
    bus.subscribe("gamingModeChanged", onGamingModeChanged)
    bus.subscribe("arduinoConnectionChanged", onArduinoConnectionChanged)
    bus.subscribe("arduinoButtonPressed", onArduinoButtonPressed)


def initializeRuntime(startBackgroundWorkers: bool = True) -> JarvisContainer:
    """Inicializa e registra todos os subsistemas no contêiner central da aplicação."""
    setupEventListeners(eventBus)

    # Garante inicialização resiliente da porta serial (fallback automático para Mock se falhar)
    try:
        serialConnection.connect()
    except Exception as error:
        print(f"[AVISO][Main] Falha ao iniciar SerialConnection ({error}). Seguindo em modo Mock.")
        serialConnection.isMockMode = True

    # Garante que o display esteja inscrito no EventBus e vinculado ao PcStatusFacade
    lcdDisplay.telemetryFacade = pcStatusFacade
    lcdDisplay.snapshotProvider = pcStatusFacade.getSnapshot
    lcdDisplay.subscribeToEvents()

    brain: JarvisBrain = JarvisBrain(
        appSettings=settings,
        store=memoryStore,
        registry=skillRegistry,
        bus=eventBus,
    )

    container: JarvisContainer = JarvisContainer(
        appSettings=settings,
        bus=eventBus,
        hardwareFacade=pcStatusFacade,
        serialConn=serialConnection,
        display=lcdDisplay,
        sensors=sensorReader,
        brain=brain,
    )

    if startBackgroundWorkers:
        container.display.showOnline(detail="SISTEMA PRONTO", holdSeconds=3.0)
        container.display.startTelemetryLoop(
            intervalSeconds=settings.lcdRefreshIntervalSeconds,
            snapshotProvider=container.hardwareFacade.getSnapshot,
        )
        container.sensors.start()

    return container


def shutdownRuntime(container: JarvisContainer, goodbyeDelaySeconds: float = 0.3) -> None:
    """Encerra workers, envia mensagem de despedida ao LCD e fecha a porta serial com segurança."""
    print("[INFO] Encerrando subsistemas e display LCD...")
    try:
        container.sensors.stop()
    except Exception as error:
        print(f"[AVISO][Main] Erro ao parar SensorReader: {error}")

    try:
        container.display.showShutdown(detail="ATE LOGO!")
        if container.serialConn.isOpen() and goodbyeDelaySeconds > 0:
            # Aguarda transmissão do pacote final antes de fechar o descritor serial
            time.sleep(goodbyeDelaySeconds)
    except Exception as error:
        print(f"[AVISO][Main] Erro ao enviar mensagem de encerramento ao LCD: {error}")

    try:
        container.display.shutdown(sendGoodbye=False)
    except Exception as error:
        print(f"[AVISO][Main] Erro ao encerrar LcdDisplay: {error}")

    try:
        container.serialConn.disconnect()
    except Exception as error:
        print(f"[AVISO][Main] Erro ao fechar SerialConnection: {error}")

    try:
        container.hardwareFacade.shutdown()
    except Exception:
        pass

    try:
        container.bus.shutdown()
    except Exception:
        pass


def printBootHeader(container: JarvisContainer) -> None:
    """Exibe o cabeçalho de inicialização e o diagnóstico de hardware, IA e ArduinoHub."""
    serialStatus: dict[str, Any] = container.serialConn.getStatus()
    arduinoModeText: str = (
        f"Conectado em {serialStatus['comPort']} @ {serialStatus['baudRate']} baud"
        if serialStatus.get("isOpen")
        else f"Modo Simulação/Mock ({serialStatus['comPort']} @ {serialStatus['baudRate']} baud)"
    )

    print("=" * 75)
    print(" JARVIS - Assistente Pessoal Local de IA (Modo Console + ArduinoHub)")
    print("=" * 75)
    print(
        f"[CONFIG] Modelo LLM: {container.appSettings.ollamaModel} | "
        f"Contexto (VRAM Budget): num_ctx={container.appSettings.ollamaNumCtx} | "
        f"Janela de Memória: {container.appSettings.maxMemoryMessages} msgs"
    )
    print(
        f"[ARDUINO] {arduinoModeText} | "
        f"Atualização LCD: {container.appSettings.lcdRefreshIntervalSeconds}s"
    )
    print(f"[SKILLS] Ferramentas registradas para Tool Calling: {skillRegistry.listSkillNames()}")

    initialSnapshot: dict[str, Any] = container.hardwareFacade.getSnapshot()
    print(f"[HARDWARE] {container.hardwareFacade.formatSummary(initialSnapshot)}")
    print("-" * 75)
    print("Comandos rápidos do console:")
    print("  /status  -> Exibe telemetria instantânea de CPU, RAM, GPU e Arduino")
    print("  /limpar  -> Limpa o histórico de conversa da memória")
    print("  /sair    -> Encerra o assistente com limpeza da porta serial")
    print("=" * 75)


def _registerSignalHandlers() -> None:
    """Garante que SIGINT e SIGTERM disparem KeyboardInterrupt para teardown limpo."""

    def handleTerminationSignal(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handleTerminationSignal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handleTerminationSignal)


def runConsoleLoop() -> None:
    """Inicializa os módulos principais e executa o loop interativo no terminal."""
    _registerSignalHandlers()
    container: JarvisContainer = initializeRuntime(startBackgroundWorkers=True)
    printBootHeader(container)

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
                currentSnapshot: dict[str, Any] = container.hardwareFacade.getSnapshot()
                serialStatus: dict[str, Any] = container.serialConn.getStatus()
                print(f"\n[TELEMETRIA] {container.hardwareFacade.formatSummary(currentSnapshot)}")
                print(
                    f"[ARDUINO] Conectado={serialStatus['isOpen']} | "
                    f"Mock={serialStatus['isMockMode']} | "
                    f"Porta={serialStatus['comPort']} ({serialStatus['baudRate']} baud)"
                )
                continue

            if lowerInput == "/limpar":
                memoryStore.clearHistory()
                container.bus.publish(
                    "jarvisStatusChanged",
                    {"status": "MEMORIA LIMPA", "detail": "HISTORICO ZERADO", "holdSeconds": 2.5},
                )
                print("\n[MEMÓRIA] Histórico de conversa reiniciado.")
                continue

            # Notifica o EventBus para o LCD exibir temporariamente o comando antes de retomar telemetria
            container.bus.publish(
                "userCommandReceived",
                {"text": userInput, "holdSeconds": 3.5},
            )
            assistantReply: str = container.brain.processInput(userInput)
            print(f"\nJARVIS: {assistantReply}")

    except KeyboardInterrupt:
        print("\n[INFO] Interrupção detectada (SIGINT/Ctrl+C). Encerrando o JARVIS...")
    finally:
        shutdownRuntime(container)


if __name__ == "__main__":
    runConsoleLoop()
