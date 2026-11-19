import signal
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from aiCore.eventBus import EventBus, eventBus
from aiCore.jarvisBrain import JarvisBrain
from aiCore.memoryStore import memoryStore
from aiCore.skillRegistry import registerSkill, skillRegistry
from appConfig.loggingConfig import getLogger, setupLogging
from appConfig.settings import Settings, settings
from arduinoHub import (
    ArduinoConnection,
    LcdDisplay,
    NetworkConnection,
    SensorReader,
    SerialConnection,
    lcdDisplay,
    sensorReader,
)
from pcHardware.pcStatusFacade import PcStatusFacade, pcStatusFacade


@dataclass
class JarvisContainer:
    """Contêiner central de dependências dos subsistemas do JARVIS."""

    appSettings: Settings
    bus: EventBus
    hardwareFacade: PcStatusFacade
    serialConn: ArduinoConnection
    display: LcdDisplay
    sensors: SensorReader
    brain: JarvisBrain

    @property
    def arduinoConn(self) -> ArduinoConnection:
        """Retorna o provedor de conexão ativo do Arduino (Wi-Fi ou Serial)."""
        return self.serialConn


_consoleLock: threading.Lock = threading.Lock()


def printAssistantNotice(message: str, currentInputBuffer: str = "") -> None:
    """Exibe falas assíncronas do JARVIS limpando a linha atual e redesenhando o prompt 'Você: '."""
    cleanMessage: str = message.strip()
    if not cleanMessage:
        return
    with _consoleLock:
        sys.stdout.write(f"\r\033[K\nJARVIS: {cleanMessage}\nVocê: {currentInputBuffer}")
        sys.stdout.flush()


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
        printAssistantNotice(
            f"Alerta térmico: temperatura elevada na {gpuName} ({gpuTemp}°C)."
        )


def onCpuHighTempAlert(payload: Any) -> None:
    """Callback do EventBus para alerta de temperatura elevada da CPU."""
    if isinstance(payload, dict):
        cpuTemp: Any = payload.get("cpuTemp")
        printAssistantNotice(
            f"Alerta térmico: temperatura elevada no processador Ryzen 5 5600 ({cpuTemp}°C)."
        )


def onGamingModeChanged(payload: Any) -> None:
    """Callback do EventBus para mudança de estado do Modo Jogo (>85% de uso da GPU)."""
    if isinstance(payload, dict):
        active: bool = bool(
            payload.get("active", payload.get("isGamingMode", False))
        )
        if active:
            printAssistantNotice(
                "Modo Jogo ativado. Suspendendo tarefas de fundo para priorizar a performance gráfica."
            )
        else:
            printAssistantNotice(
                "Modo Jogo desativado. Retomando processamento padrão do assistente."
            )


def onUserPresenceDetected(_payload: Any = None) -> None:
    """Callback do EventBus para saudação formal no console quando o sensor PIR detecta presença."""
    printAssistantNotice(
        "Bem-vindo de volta, senhor. Todos os sistemas operacionais e prontos."
    )


def onArduinoConnectionChanged(payload: Any) -> None:
    """Callback do EventBus para mudanças no estado da conexão (registrado em jarvis.log)."""
    if isinstance(payload, dict):
        hwLogger = getLogger("jarvis.hardware")
        endpoint: str = str(
            payload.get("endpoint")
            or payload.get("comPort")
            or f"{settings.arduinoHost}:{settings.arduinoPort}"
        )
        isConnected: bool = bool(payload.get("isConnected", False))
        if isConnected:
            hwLogger.info("Conexão restabelecida em %s.", endpoint)
        else:
            hwLogger.warning(
                "Conexão perdida em %s. Fallback para Modo Simulação (Mock) ativo.",
                endpoint,
            )


def onArduinoButtonPressed(payload: Any) -> None:
    """Callback do EventBus para eventos de botão físico (registrado em jarvis.log)."""
    if isinstance(payload, dict):
        hwLogger = getLogger("jarvis.hardware")
        buttonId: Any = payload.get("button")
        state: Any = payload.get("state", "PRESSED")
        hwLogger.info("Botão %s acionado (%s).", buttonId, state)


def setupEventListeners(bus: EventBus = eventBus) -> None:
    """Registra os ouvintes de console no EventBus para monitorar hardware e ArduinoHub."""
    bus.subscribe("gpuHighTempAlert", onGpuHighTempAlert)
    bus.subscribe("cpuHighTempAlert", onCpuHighTempAlert)
    bus.subscribe("gamingModeChanged", onGamingModeChanged)
    bus.subscribe("userPresenceDetected", onUserPresenceDetected)
    bus.subscribe("arduinoConnectionChanged", onArduinoConnectionChanged)
    bus.subscribe("arduinoButtonPressed", onArduinoButtonPressed)


def teardownEventListeners(bus: EventBus = eventBus) -> None:
    """Remove os ouvintes de console do EventBus durante o encerramento."""
    bus.unsubscribe("gpuHighTempAlert", onGpuHighTempAlert)
    bus.unsubscribe("cpuHighTempAlert", onCpuHighTempAlert)
    bus.unsubscribe("gamingModeChanged", onGamingModeChanged)
    bus.unsubscribe("userPresenceDetected", onUserPresenceDetected)
    bus.unsubscribe("arduinoConnectionChanged", onArduinoConnectionChanged)
    bus.unsubscribe("arduinoButtonPressed", onArduinoButtonPressed)


def formatArduinoStatus(connStatus: dict[str, Any]) -> str:
    """Formata o estado atual da conexão do Arduino (Wi-Fi, Serial ou Modo Simulação Mock)."""
    connType: str = str(connStatus.get("connectionType", settings.arduinoConnectionType)).lower()
    if connType == "wifi":
        host: str = str(connStatus.get("host", settings.arduinoHost))
        port: Any = connStatus.get("port", settings.arduinoPort)
        targetDesc: str = f"Wi-Fi ({host}:{port})"
    else:
        comPort: str = str(connStatus.get("comPort", settings.comPort))
        targetDesc = f"Serial ({comPort})"

    if connStatus.get("isOpen"):
        return f"Conectado via {targetDesc}"
    return f"Modo Simulação (Mock) [{targetDesc}]"


def initializeRuntime(
    startBackgroundWorkers: bool = True,
    connectionType: Optional[str] = None,
) -> JarvisContainer:
    """Inicializa e registra todos os subsistemas no contêiner central da aplicação."""
    setupLogging()
    setupEventListeners(eventBus)

    selectedType: str = (
        connectionType or settings.arduinoConnectionType or "wifi"
    ).strip().lower()

    if selectedType == "wifi":
        arduinoConn: ArduinoConnection = NetworkConnection(
            host=settings.arduinoHost,
            port=settings.arduinoPort,
            timeout=settings.arduinoSocketTimeout,
        )
    else:
        arduinoConn = SerialConnection(
            port=settings.comPort,
            baudRate=settings.baudRate,
            timeout=settings.serialTimeout,
        )

    # Garante inicialização resiliente (fallback automático para Mock se falhar)
    try:
        arduinoConn.connect()
    except Exception as error:
        print(f"[AVISO][Main] Falha ao iniciar conexão Arduino ({error}). Seguindo em modo Mock.")
        arduinoConn.isMockMode = True

    # Injeta o provedor de conexão (Wi-Fi ou Serial) no LcdDisplay e no SensorReader
    lcdDisplay.connection = arduinoConn
    lcdDisplay.telemetryFacade = pcStatusFacade
    lcdDisplay.snapshotProvider = pcStatusFacade.getSnapshot
    lcdDisplay.subscribeToEvents()

    sensorReader.connection = arduinoConn

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
        serialConn=arduinoConn,
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
    """Encerra workers, envia mensagem de despedida ao LCD e fecha a conexão com segurança."""
    print("[INFO] Encerrando subsistemas e display LCD...")
    try:
        container.sensors.stop()
    except Exception as error:
        print(f"[AVISO][Main] Erro ao parar SensorReader: {error}")

    try:
        container.display.showShutdown(detail="ATE LOGO!")
        if container.arduinoConn.isOpen() and goodbyeDelaySeconds > 0:
            time.sleep(goodbyeDelaySeconds)
    except Exception as error:
        print(f"[AVISO][Main] Erro ao enviar mensagem de encerramento ao LCD: {error}")

    try:
        container.display.shutdown(sendGoodbye=False)
    except Exception as error:
        print(f"[AVISO][Main] Erro ao encerrar LcdDisplay: {error}")

    try:
        container.arduinoConn.disconnect()
    except Exception as error:
        print(f"[AVISO][Main] Erro ao fechar conexão do Arduino: {error}")

    try:
        container.hardwareFacade.shutdown()
    except Exception:
        pass

    try:
        teardownEventListeners(container.bus)
        container.bus.shutdown()
    except Exception:
        pass


def printBootHeader(container: JarvisContainer) -> None:
    """Exibe o cabeçalho de inicialização e o diagnóstico de hardware, IA e ArduinoHub."""
    connStatus: dict[str, Any] = container.arduinoConn.getStatus()
    arduinoModeText: str = formatArduinoStatus(connStatus)

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
    print("  /sair    -> Encerra o assistente com limpeza da conexão Arduino")
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
                connStatus: dict[str, Any] = container.arduinoConn.getStatus()
                print(f"\n[TELEMETRIA] {container.hardwareFacade.formatSummary(currentSnapshot)}")
                print(
                    f"[ARDUINO] {formatArduinoStatus(connStatus)} | "
                    f"Conectado={connStatus['isOpen']} | Mock={connStatus['isMockMode']}"
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
