import argparse
import contextlib
import io
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# Garante importação a partir da raiz do projeto mesmo executando diretamente via caminho do arquivo
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main as jarvisMain
from aiCore.eventBus import EventBus
from arduinoHub import (
    LcdDisplay,
    NetworkConnection,
    PresenceTracker,
    SensorReader,
    SerialConnection,
    SerialProtocol,
    formatLcdMessage,
    getConnectionProvider,
    lcdDisplay,
    parseIncoming,
    presenceTracker,
    sensorReader,
    serialConnection,
)
from arduinoHub.serialConnection import SerialException
from pcHardware.pcStatusFacade import pcStatusFacade


class TestArduinoHub(unittest.TestCase):
    """Testes unitários e de integração do subsistema arduinoHub (Serial e Wi-Fi TCP) e main.py."""

    def setUp(self) -> None:
        self.bus: EventBus = EventBus()
        self.conn: SerialConnection = serialConnection
        self.conn.disconnect()
        self.conn.clearMockTxHistory()
        self.conn.isMockMode = True

        self.mockSnapshot: dict[str, Any] = {
            "cpuUsage": 45.2,
            "cpuTemp": 58.4,
            "ramUsedGb": 12.4,
            "ramTotalGb": 32.0,
            "ramPercent": 38.8,
            "gpuUsage": 62.1,
            "gpuTemp": 65.0,
            "vramUsedGb": 2.4,
            "vramTotalGb": 8.0,
            "isGamingMode": False,
        }
        self.display: LcdDisplay = LcdDisplay(
            connection=self.conn,
            bus=self.bus,
            snapshotProvider=lambda: self.mockSnapshot,
            autoSubscribe=True,
        )
        self.reader: SensorReader = SensorReader(connection=self.conn, bus=self.bus)

    def tearDown(self) -> None:
        self.display.shutdown()
        self.reader.stop()

    def testFormatLcdMessageExact16CharsAndSanitization(self) -> None:
        packet: str = formatLcdMessage("ATENÇÃO: CPU>85%", "GPU|4060Ti: 49°C")
        self.assertTrue(packet.startswith("<LCD:"))
        self.assertTrue(packet.endswith(">\n"))

        body: str = packet[len("<LCD:") : -len(">\n")]
        line1, line2 = body.split("|", 1)
        self.assertEqual(len(line1), 16)
        self.assertEqual(len(line2), 16)
        self.assertEqual(line1, "ATENCAO: CPU85% ")
        self.assertEqual(line2, "GPU/4060Ti: 49C ")

        # Verifica truncamento quando excede 16 caracteres
        longPacket: str = formatLcdMessage("12345678901234567890", "OK")
        longBody: str = longPacket[len("<LCD:") : -len(">\n")]
        longL1, longL2 = longBody.split("|", 1)
        self.assertEqual(longL1, "1234567890123456")
        self.assertEqual(longL2, "OK              ")

    def testParseIncomingMessages(self) -> None:
        btn: dict[str, Any] = parseIncoming("<BTN:1>\n")
        self.assertEqual(btn["type"], "BTN")
        self.assertEqual(btn["button"], 1)
        self.assertEqual(btn["state"], "PRESSED")

        sen: dict[str, Any] = parseIncoming("<SEN:TEMP:25>\n")
        self.assertEqual(sen["type"], "SEN")
        self.assertEqual(sen["name"], "TEMP")
        self.assertEqual(sen["value"], 25)

        senFloat: dict[str, Any] = parseIncoming("<SEN:TEMP_AMB:24.5>\n")
        self.assertEqual(senFloat["value"], 24.5)

        ev: dict[str, Any] = parseIncoming("<EVENT:MOTION_DETECTED>\n")
        self.assertEqual(ev["type"], "EVENT")
        self.assertEqual(ev["name"], "MOTION_DETECTED")
        self.assertIs(ev["value"], True)

        cmd: dict[str, Any] = parseIncoming("<CMD:RELAY1:ON>\n")
        self.assertEqual(cmd["type"], "CMD")
        self.assertEqual(cmd["action"], "RELAY1")
        self.assertEqual(cmd["value"], "ON")

        invalid: dict[str, Any] = parseIncoming("ruido_sem_delimitadores")
        self.assertEqual(invalid["type"], "INVALID")

    def testSerialConnectionSingletonFallbackAndThreadSafety(self) -> None:
        secondRef: SerialConnection = SerialConnection()
        self.assertIs(secondRef, self.conn)

        # Simula falha na porta COM para validar fallback gracioso mesmo com Arduino conectado
        with patch("arduinoHub.serialConnection.serial") as mockSerialModule:
            mockSerialModule.Serial.side_effect = SerialException("COM3 ocupada ou ausente")
            connected: bool = self.conn.connect(forceReconnect=True)

        self.assertFalse(connected)
        self.assertTrue(self.conn.is_mock)
        self.assertFalse(self.conn.isOpen())

        # Envio concorrente a partir de múltiplas threads sem exceções
        errors: list[Exception] = []

        def worker(workerId: int) -> None:
            try:
                for step in range(5):
                    self.conn.send(formatLcdMessage(f"THREAD {workerId}", f"PASSO {step}"))
            except Exception as err:
                errors.append(err)

        threads: list[threading.Thread] = [
            threading.Thread(target=worker, args=(idx,)) for idx in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(self.conn.getMockTxHistory()), 20)

    def testNetworkConnectionConnectSuccessAndRead(self) -> None:
        with patch("socket.socket") as mockSocketCls:
            mockSockInstance: MagicMock = MagicMock()
            mockSockInstance.recv.return_value = b"<SEN:TEMP:25>\n"
            mockSocketCls.return_value = mockSockInstance

            netConn: NetworkConnection = NetworkConnection(
                host="192.168.7.4",
                port=8080,
                timeout=3.0,
                bus=self.bus,
            )
            connected: bool = netConn.connect()

            self.assertTrue(connected)
            self.assertTrue(netConn.isConnected)
            self.assertFalse(netConn.isMockMode)
            self.assertTrue(netConn.isOpen())
            mockSockInstance.settimeout.assert_called_once_with(3.0)
            mockSockInstance.connect.assert_called_once_with(("192.168.7.4", 8080))

            receivedText: Any = netConn.read()
            self.assertEqual(receivedText, "<SEN:TEMP:25>")

            netConn.disconnect()
            self.assertFalse(netConn.isConnected)
            self.assertFalse(netConn.isOpen())
            mockSockInstance.close.assert_called_once()

    def testNetworkConnectionFallbackToMockOnRefusedOrTimeout(self) -> None:
        # 1. ConnectionRefusedError
        with patch("socket.socket") as mockSocketCls:
            mockSockInstance: MagicMock = MagicMock()
            mockSockInstance.connect.side_effect = ConnectionRefusedError("Conexão recusada pelo ESP-01")
            mockSocketCls.return_value = mockSockInstance

            netConn: NetworkConnection = NetworkConnection(
                host="192.168.7.4",
                port=8080,
                timeout=3.0,
                bus=self.bus,
            )
            self.assertFalse(netConn.connect())
            self.assertTrue(netConn.isMockMode)
            self.assertFalse(netConn.isConnected)
            self.assertFalse(netConn.isOpen())

        # 2. socket.timeout
        with patch("socket.socket") as mockSocketCls:
            mockSockTimeout: MagicMock = MagicMock()
            mockSockTimeout.connect.side_effect = socket.timeout("Timeout de 3.0s atingido")
            mockSocketCls.return_value = mockSockTimeout

            netConnTimeout: NetworkConnection = NetworkConnection(
                host="192.168.7.4",
                port=8080,
                timeout=3.0,
                bus=self.bus,
            )
            self.assertFalse(netConnTimeout.connect())
            self.assertTrue(netConnTimeout.isMockMode)
            self.assertFalse(netConnTimeout.isConnected)

    def testNetworkConnectionSendLcdFormattingThreadSafetyAndDisconnectFallback(self) -> None:
        with patch("socket.socket") as mockSocketCls:
            mockSockInstance: MagicMock = MagicMock()
            mockSocketCls.return_value = mockSockInstance

            netConn: NetworkConnection = NetworkConnection(
                host="192.168.7.4",
                port=8080,
                timeout=3.0,
                bus=self.bus,
            )
            self.assertTrue(netConn.connect())

            packet: str = formatLcdMessage("CPU: 45% 58C", "GPU: 62% 65C")
            self.assertTrue(netConn.send(packet))
            mockSockInstance.sendall.assert_called_once_with(
                b"<LCD:CPU: 45% 58C    |GPU: 62% 65C    >\n"
            )

            # Teste de concorrência thread-safe (múltiplas threads chamando send simultaneamente)
            errors: list[Exception] = []

            def wifiWorker(workerId: int) -> None:
                try:
                    for step in range(5):
                        netConn.send(formatLcdMessage(f"WIFI {workerId}", f"PKT {step}"))
                except Exception as err:
                    errors.append(err)

            threads: list[threading.Thread] = [
                threading.Thread(target=wifiWorker, args=(idx,)) for idx in range(4)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            self.assertEqual(mockSockInstance.sendall.call_count, 21)

            # Simula queda abrupta da conexão Wi-Fi durante sendall -> deve cair para Mock sem exceção
            mockSockInstance.sendall.side_effect = ConnectionResetError("ESP-01 reiniciou")
            self.assertTrue(netConn.send(packet))
            self.assertTrue(netConn.isMockMode)
            self.assertFalse(netConn.isConnected)
            self.assertEqual(len(netConn.getMockTxHistory()), 1)
            netConn.disconnect()

    def testLcdDisplayStatusErrorAndAlternatingTelemetry(self) -> None:
        self.assertTrue(self.display.showOnline(holdSeconds=0.0))
        l1, l2 = self.display.getLastDisplayedLines()
        self.assertEqual(l1, "JARVIS ONLINE   ")
        self.assertEqual(l2, "SISTEMA PRONTO  ")

        self.assertTrue(self.display.showProcessing("LENDO EMAILS", holdSeconds=0.0))
        l1, l2 = self.display.getLastDisplayedLines()
        self.assertEqual(l1, "PROCESSANDO...  ")
        self.assertEqual(l2, "LENDO EMAILS    ")

        # Testa alternância manual das 3 telas (0=CPU/GPU, 1=RAM/VRAM, 2=Status/Hora)
        self.assertTrue(self.display.showTelemetry(screenIndex=0))
        l1, l2 = self.display.getLastDisplayedLines()
        self.assertEqual(l1, "CPU: 45% 58C    ")
        self.assertEqual(l2, "GPU: 62% 65C    ")

        self.assertTrue(self.display.showTelemetry(screenIndex=1))
        l1, l2 = self.display.getLastDisplayedLines()
        self.assertEqual(l1, "RAM: 39% 12.4GB ")
        self.assertEqual(l2, "VRAM:2.4/8GB    ")

        self.assertTrue(self.display.showTelemetry(screenIndex=2))
        l1, l2 = self.display.getLastDisplayedLines()
        self.assertEqual(l1, "JARVIS ONLINE   ")
        self.assertTrue(l2.startswith("HORA: "))

        # Testa worker de alternância automática em intervalo curto
        self.conn.clearMockTxHistory()
        self.display.startTelemetryLoop(intervalSeconds=0.05)
        time.sleep(0.24)
        self.display.stopTelemetryLoop()
        txHistory: list[str] = self.conn.getMockTxHistory()
        self.assertGreaterEqual(len(txHistory), 3)
        self.assertTrue(any("CPU: 45% 58C" in pkt for pkt in txHistory))
        self.assertTrue(any("RAM: 39% 12.4GB" in pkt for pkt in txHistory))
        self.assertTrue(any("HORA:" in pkt for pkt in txHistory))

    def testLcdDisplayObserverHoldAndResumeTelemetry(self) -> None:
        self.bus.publish(
            "voiceCommandReceived",
            {"text": "Qual temp CPU?", "holdSeconds": 0.12},
            asyncDispatch=False,
        )
        l1, l2 = self.display.getLastDisplayedLines()
        self.assertEqual(l1, "COMANDO:        ")
        self.assertEqual(l2, "Qual temp CPU?  ")

        # Durante o hold, telemetria não sobrescreve a notificação
        self.assertFalse(self.display.showTelemetry(screenIndex=0))

        # Após expirar o hold, telemetria normal é retomada
        time.sleep(0.15)
        self.assertTrue(self.display.showTelemetry(screenIndex=0))
        l1, l2 = self.display.getLastDisplayedLines()
        self.assertEqual(l1, "CPU: 45% 58C    ")

        self.bus.publish("gpuHighTempAlert", {"gpuTemp": 84.0}, asyncDispatch=False)
        l1, l2 = self.display.getLastDisplayedLines()
        self.assertEqual(l1, "ALERTA TEMP GPU!")
        self.assertEqual(l2, "RTX: 84C        ")

    def testSensorReaderParsesAndPublishesEvents(self) -> None:
        capturedSensors: list[dict[str, Any]] = []
        capturedButtons: list[dict[str, Any]] = []
        capturedEvents: list[dict[str, Any]] = []

        self.bus.subscribe("arduinoSensorUpdated", lambda p: capturedSensors.append(p))
        self.bus.subscribe("arduinoButtonPressed", lambda p: capturedButtons.append(p))
        self.bus.subscribe("arduinoEventReceived", lambda p: capturedEvents.append(p))

        self.reader.injectMockSensor("TEMP", 25)
        self.conn.enqueueMockInput("<BTN:1>\n")
        self.reader.pollOnce()
        self.conn.enqueueMockInput("<EVENT:WAKE_TRIGGER>\n")
        self.reader.pollOnce()
        time.sleep(0.08)

        self.assertEqual(self.reader.getSensorValue("TEMP"), 25)
        self.assertEqual(len(capturedSensors), 1)
        self.assertEqual(capturedSensors[0]["name"], "TEMP")
        self.assertEqual(capturedSensors[0]["value"], 25)

        self.assertEqual(len(capturedButtons), 1)
        self.assertEqual(capturedButtons[0]["button"], 1)

        self.assertEqual(len(capturedEvents), 1)
        self.assertEqual(capturedEvents[0]["name"], "WAKE_TRIGGER")

        self.assertTrue(self.reader.sendCommand("RELAY1", "ON"))
        self.assertIn("<CMD:RELAY1:ON>", self.conn.getMockTxHistory())

    def testPirPresenceDetectionInSensorReader(self) -> None:
        presenceEvents: list[dict[str, Any]] = []
        self.bus.subscribe("userPresenceDetected", lambda p: presenceEvents.append(p))

        # Configura timeout curto de ausência (0.2s) para validar ciclo completo da máquina de estados
        self.reader.presenceTimeoutSeconds = 0.20
        self.reader.resetPresenceState()
        self.assertFalse(self.reader.isUserPresent)

        # 1) Primeira detecção (usuário acabou de chegar) + verificação de stdout limpo (sem print)
        stdoutBuffer = io.StringIO()
        with contextlib.redirect_stdout(stdoutBuffer):
            self.conn.enqueueMockInput("<EVENT:PRESENCE_DETECTED>\n")
            packet: Any = self.reader.pollOnce()
        time.sleep(0.06)

        self.assertEqual(stdoutBuffer.getvalue(), "")
        self.assertIsNotNone(packet)
        self.assertEqual(packet["type"], "EVENT")
        self.assertEqual(packet["name"], "PRESENCE_DETECTED")
        self.assertTrue(self.reader.isUserPresent)
        firstTimestamp: float = self.reader.lastMovementTimestamp
        self.assertGreater(firstTimestamp, 0.0)
        self.assertEqual(len(presenceEvents), 1)
        self.assertEqual(presenceEvents[0]["source"], "pir_sensor")

        # 2) Movimento contínuo com usuário já sentado (isUserPresent == True):
        #    apenas atualiza lastMovementTimestamp, SEM republicar userPresenceDetected
        time.sleep(0.05)
        self.conn.enqueueMockInput("<EVENT:PRESENCE_DETECTED>\n")
        self.reader.pollOnce()
        time.sleep(0.05)

        self.assertTrue(self.reader.isUserPresent)
        self.assertGreater(self.reader.lastMovementTimestamp, firstTimestamp)
        self.assertEqual(len(presenceEvents), 1)

        # 3) Simula ausência superior a presenceTimeoutSeconds -> reseta isUserPresent = False
        time.sleep(0.25)
        expired: bool = self.reader.checkPresenceTimeout()
        self.assertTrue(expired)
        self.assertFalse(self.reader.isUserPresent)

        # 4) Novo retorno do usuário após ausência -> dispara boas-vindas novamente
        self.conn.enqueueMockInput("<EVENT:PRESENCE_DETECTED>\n")
        self.reader.pollOnce()
        time.sleep(0.06)
        self.assertTrue(self.reader.isUserPresent)
        self.assertEqual(len(presenceEvents), 2)

    def testPresenceTracker600sStateMachineAndUserReturnedLcdGreeting(self) -> None:
        returnedEvents: list[dict[str, Any]] = []
        awayEvents: list[dict[str, Any]] = []
        self.bus.subscribe("userReturned", lambda p: returnedEvents.append(p))
        self.bus.subscribe("userAway", lambda p: awayEvents.append(p))

        tracker: PresenceTracker = PresenceTracker(bus=self.bus, autoSubscribe=True)
        integratedReader: SensorReader = SensorReader(
            connection=self.conn,
            bus=self.bus,
            presenceTracker=tracker,
        )
        try:
            # 1) Limiar padrão oficial deve ser exatamente 600s (10 minutos)
            self.assertEqual(tracker.awayTimeoutSeconds, 600.0)
            self.assertEqual(tracker.state, PresenceTracker.STATE_AWAY)

            # 2) Primeiro pulso PIR após estado AWAY -> PRESENT e dispara "userReturned"
            t0: float = 1000.0
            tracker.reset(initialState=PresenceTracker.STATE_AWAY, currentTime=900.0)
            self.assertTrue(tracker.recordPresence(currentTime=t0))
            time.sleep(0.06)
            self.assertEqual(tracker.state, PresenceTracker.STATE_PRESENT)
            self.assertEqual(len(returnedEvents), 1)
            self.assertEqual(returnedEvents[0]["timestamp"], t0)
            self.assertAlmostEqual(returnedEvents[0]["absenceDuration"], 100.0, places=2)

            # 3) Pulsos contínuos dentro da janela de 600s mantêm PRESENT sem reenviar boas-vindas
            self.assertFalse(tracker.recordPresence(currentTime=t0 + 120.0))
            self.assertFalse(tracker.recordPresence(currentTime=t0 + 500.0))
            self.assertFalse(tracker.checkInactivity(currentTime=t0 + 550.0))
            time.sleep(0.06)
            self.assertEqual(tracker.state, PresenceTracker.STATE_PRESENT)
            self.assertEqual(len(returnedEvents), 1)
            self.assertEqual(len(awayEvents), 0)

            # 4) Inatividade > 600s após último movimento (t0 + 500 + 601 = t0 + 1101) -> AWAY
            self.assertTrue(tracker.checkInactivity(currentTime=t0 + 1101.0))
            time.sleep(0.06)
            self.assertEqual(tracker.state, PresenceTracker.STATE_AWAY)
            self.assertEqual(len(awayEvents), 1)

            # 5) Novo evento via SensorReader integrado após AWAY -> dispara "userReturned" + LCD
            self.conn.clearMockTxHistory()
            self.conn.enqueueMockInput("<EVENT:PRESENCE_DETECTED>\n")
            integratedReader.pollOnce()
            time.sleep(0.08)
            self.assertEqual(tracker.state, PresenceTracker.STATE_PRESENT)
            self.assertEqual(len(returnedEvents), 2)
            self.assertIn(
                "<LCD:BEM-VINDO SENHOR|JARVIS PRONTO   >",
                self.conn.getMockTxHistory(),
            )
            # Durante o hold padrão de 4s, telemetria fica pausada
            self.assertFalse(self.display.showTelemetry(screenIndex=0))

            # Valida retomada automática da telemetria após expirar o hold
            self.display.handleUserReturned({"holdSeconds": 0.12})
            self.assertFalse(self.display.showTelemetry(screenIndex=0))
            time.sleep(0.15)
            self.assertTrue(self.display.showTelemetry(screenIndex=0))
            self.assertEqual(
                self.display.getLastDisplayedLines(),
                ("CPU: 45% 58C    ", "GPU: 62% 65C    "),
            )
        finally:
            tracker.stop()
            integratedReader.stop()

    def testLcdDisplayHandleUserPresenceFreezesAndResumesCarousel(self) -> None:
        self.conn.clearMockTxHistory()
        # Publica userPresenceDetected com hold curto para validar congelamento e retomada
        self.bus.publish(
            "userPresenceDetected",
            {"source": "pir_sensor", "timestamp": time.time(), "holdSeconds": 0.15},
            asyncDispatch=False,
        )

        l1, l2 = self.display.getLastDisplayedLines()
        self.assertEqual(l1, "BEM-VINDO CHEFE ")
        self.assertEqual(l2, "SISTEMA PRONTO  ")
        self.assertIn(
            "<LCD:BEM-VINDO CHEFE |SISTEMA PRONTO  >",
            self.conn.getMockTxHistory(),
        )

        # Durante a janela de saudação, o carrossel de telemetria permanece congelado
        self.assertFalse(self.display.showTelemetry(screenIndex=0))
        self.assertEqual(
            self.display.getLastDisplayedLines(),
            ("BEM-VINDO CHEFE ", "SISTEMA PRONTO  "),
        )

        # Após o término da saudação, o carrossel de CPU/GPU é retomado automaticamente
        time.sleep(0.18)
        self.assertTrue(self.display.showTelemetry(screenIndex=0))
        l1After, l2After = self.display.getLastDisplayedLines()
        self.assertEqual(l1After, "CPU: 45% 58C    ")
        self.assertEqual(l2After, "GPU: 62% 65C    ")

    def testLcdDisplayImmediateSyncOnHandshakeAndReconnect(self) -> None:
        with patch("socket.socket") as mockSocketCls:
            mockSockInstance: MagicMock = MagicMock()
            mockSocketCls.return_value = mockSockInstance

            netConn: NetworkConnection = NetworkConnection(
                host="192.168.7.4",
                port=8080,
                timeout=3.0,
                bus=self.bus,
                reconnectIntervalSeconds=0.2,
            )
            wifiDisplay: LcdDisplay = LcdDisplay(
                connection=netConn,
                bus=self.bus,
                snapshotProvider=lambda: self.mockSnapshot,
                autoSubscribe=True,
            )
            try:
                # 1) Handshake inicial -> log explícito de conexão e sincronização inicial
                stdoutBuf = io.StringIO()
                with contextlib.redirect_stdout(stdoutBuf):
                    self.assertTrue(netConn.connect())
                    time.sleep(0.08)

                outputInitial: str = stdoutBuf.getvalue()
                self.assertIn(
                    "[ARDUINO HUB] Conectado com sucesso ao Hub Wi-Fi (192.168.7.4:8080).",
                    outputInitial,
                )
                self.assertIn(
                    "[LCD DISPLAY] Sincronizacao inicial enviada: <LCD:CPU: 45% 58C    |GPU: 62% 65C    >",
                    outputInitial,
                )
                self.assertGreaterEqual(mockSockInstance.sendall.call_count, 1)

                # 2) Queda abrupta -> log de perda e reconexão automática em segundo plano
                mockSockInstance.sendall.side_effect = ConnectionResetError("Queda Wi-Fi")
                dropBuf = io.StringIO()
                with contextlib.redirect_stdout(dropBuf):
                    netConn.send(formatLcdMessage("TESTE", "QUEDA"))
                    self.assertTrue(netConn.isMockMode)
                    self.assertIn(
                        "[ARDUINO HUB] Conexao perdida com o Arduino. "
                        "Iniciando tentativas de reconexao em segundo plano...",
                        dropBuf.getvalue(),
                    )

                    # Restaura socket antes da tentativa em background (0.2s)
                    mockSockInstance.sendall.side_effect = None
                    time.sleep(0.32)

                reconnectOutput: str = dropBuf.getvalue()
                self.assertIn(
                    "[ARDUINO HUB] Reconectado com sucesso ao Arduino Hub!",
                    reconnectOutput,
                )
                self.assertIn(
                    "[LCD DISPLAY] Sincronizacao inicial enviada: <LCD:",
                    reconnectOutput,
                )
                self.assertTrue(netConn.isConnected)
                self.assertFalse(netConn.isMockMode)
            finally:
                wifiDisplay.shutdown()
                netConn.disconnect()

    def testTerminalVisibilityOnFailureAndMainSubsystemStatus(self) -> None:
        with (
            patch("socket.socket") as mockSocketCls,
            patch.object(pcStatusFacade, "getSnapshot", return_value=self.mockSnapshot),
            patch.object(pcStatusFacade, "shutdown"),
            patch.object(self.bus, "shutdown"),
        ):
            mockSockInstance: MagicMock = MagicMock()
            mockSockInstance.connect.side_effect = ConnectionRefusedError("ESP-01 em boot")
            mockSocketCls.return_value = mockSockInstance

            bootBuf = io.StringIO()
            with contextlib.redirect_stdout(bootBuf):
                container: jarvisMain.JarvisContainer = jarvisMain.initializeRuntime(
                    startBackgroundWorkers=False,
                    connectionType="wifi",
                )
                jarvisMain.shutdownRuntime(container, goodbyeDelaySeconds=0.0)

            bootText: str = bootBuf.getvalue()
            self.assertIn(
                "[ARDUINO HUB] AVISO: Nao foi possivel conectar ao Arduino em 192.168.7.4:8080 "
                "(Motivo: ESP-01 em boot). Operando em modo desacoplado.",
                bootText,
            )
            self.assertIn("--- STATUS DOS SUBSISTEMAS ---", bootText)
            self.assertIn(f"[AI CORE] Ollama Online ({container.appSettings.ollamaModel})", bootText)
            self.assertIn("[PC HARDWARE] Monitoramento CPU/GPU Ativo", bootText)
            self.assertIn("[ARDUINO HUB] Status: DESCONECTADO", bootText)

    def testStartTelemetryLoopDispatchesSynchronouslyBeforeInterval(self) -> None:
        self.conn.clearMockTxHistory()
        # Mesmo com intervalo longo (10s), o primeiro pacote deve ser enviado de forma imediata
        self.display.startTelemetryLoop(intervalSeconds=10.0)
        try:
            txImmediate: list[str] = self.conn.getMockTxHistory()
            self.assertEqual(len(txImmediate), 1)
            self.assertIn("CPU: 45% 58C", txImmediate[0])
        finally:
            self.display.stopTelemetryLoop()

    def testMainLifecycleInitializeWifiAndSerialFallback(self) -> None:
        # 1. Valida Factory getConnectionProvider e formatação de status
        wifiProvider = getConnectionProvider("wifi")
        serialProvider = getConnectionProvider("serial")
        self.assertIsInstance(wifiProvider, NetworkConnection)
        self.assertIsInstance(serialProvider, SerialConnection)

        self.assertIn(
            "Wi-Fi (192.168.7.4:8080)",
            jarvisMain.formatArduinoStatus(
                {"connectionType": "wifi", "host": "192.168.7.4", "port": 8080, "isOpen": True}
            ),
        )
        self.assertIn(
            "Serial (COM3)",
            jarvisMain.formatArduinoStatus(
                {"connectionType": "serial", "comPort": "COM3", "isOpen": True}
            ),
        )
        self.assertIn(
            "Modo Simulação (Mock)",
            jarvisMain.formatArduinoStatus(
                {"connectionType": "wifi", "host": "192.168.7.4", "port": 8080, "isOpen": False}
            ),
        )

        # 2. Valida boot do main.py em modo Wi-Fi com fallback gracioso para Mock e teardown
        with (
            patch("socket.socket") as mockSocketCls,
            patch.object(pcStatusFacade, "getSnapshot", return_value=self.mockSnapshot),
            patch.object(pcStatusFacade, "shutdown"),
            patch.object(self.bus, "shutdown"),
        ):
            mockSockInstance: MagicMock = MagicMock()
            mockSockInstance.connect.side_effect = ConnectionRefusedError("ESP-01 desligado")
            mockSocketCls.return_value = mockSockInstance

            container: jarvisMain.JarvisContainer = jarvisMain.initializeRuntime(
                startBackgroundWorkers=True,
                connectionType="wifi",
            )
            try:
                self.assertIsInstance(container.arduinoConn, NetworkConnection)
                self.assertIs(container.display.connection, container.arduinoConn)
                self.assertIs(container.sensors.connection, container.arduinoConn)
                self.assertIs(container.presence, presenceTracker)
                self.assertIs(container.sensors.presenceTracker, presenceTracker)
                self.assertTrue(container.arduinoConn.isMockMode)

                time.sleep(0.08)
                txBeforeShutdown: list[str] = container.arduinoConn.getMockTxHistory()
                self.assertTrue(
                    any("JARVIS ONLINE" in pkt for pkt in txBeforeShutdown)
                )
            finally:
                jarvisMain.shutdownRuntime(container, goodbyeDelaySeconds=0.0)

            txAfterShutdown: list[str] = container.arduinoConn.getMockTxHistory()
            self.assertIn("<LCD:JARVIS OFFLINE  |ATE LOGO!       >", txAfterShutdown)
            self.assertFalse(container.arduinoConn.isOpen())


def runMockDemo(cycles: int = 4, intervalSeconds: float = 3.5) -> None:
    """Demonstra o subsistema arduinoHub com telemetria real em modo simulado."""
    print("=" * 70)
    print(" JARVIS - Demonstração arduinoHub (Modo Simulação / Mock)")
    print("=" * 70)
    conn = getConnectionProvider()
    conn.connect()
    print(f"Estado da conexão: {conn.getStatus()}")

    bus: EventBus = EventBus()
    display: LcdDisplay = lcdDisplay
    reader: SensorReader = sensorReader
    display.connection = conn
    reader.connection = conn
    display.subscribeToEvents()

    bus.subscribe(
        "arduinoSensorUpdated",
        lambda payload: print(f"[EVENTBUS] Sensor recebido: {payload}"),
    )
    bus.subscribe(
        "arduinoButtonPressed",
        lambda payload: print(f"[EVENTBUS] Botão acionado: {payload}"),
    )
    bus.subscribe(
        "arduinoEventReceived",
        lambda payload: print(f"[EVENTBUS] Evento recebido: {payload}"),
    )

    try:
        print("\n1) Exibindo status iniciais...")
        display.showOnline(holdSeconds=0.0)
        time.sleep(1.0)
        display.showProcessing("CARREGANDO IA", holdSeconds=0.0)
        time.sleep(1.0)

        print(
            f"\n2) Alternando telas de telemetria real (pcStatusFacade) "
            f"a cada {intervalSeconds:.1f}s..."
        )
        display.startTelemetryLoop(intervalSeconds=intervalSeconds)
        time.sleep(cycles * intervalSeconds)
        display.stopTelemetryLoop()

        print("\n3) Simulando evento de alerta de temperatura via EventBus...")
        bus.publish("gpuHighTempAlert", {"gpuTemp": 83.0}, asyncDispatch=False)
        time.sleep(1.0)

        print("\n4) Simulando entrada de sensor/evento do Arduino e envio de <CMD:...>...")
        reader.injectMockSensor("TEMP_AMB", 24.5)
        conn.enqueueMockInput("<EVENT:PING>\n")
        reader.pollOnce()
        reader.sendCommand("RELAY1", "ON")
        time.sleep(0.2)
    finally:
        display.shutdown(sendGoodbye=True)
        reader.stop()
        conn.disconnect()
        pcStatusFacade.shutdown()
        bus.shutdown()
        print("\n[INFO] Demonstração finalizada com segurança.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Testes e demonstração em modo mock do subsistema arduinoHub."
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Executa demonstração visual de alternância de telas em modo mock.",
    )
    args, remainingArgs = parser.parse_known_args()

    if args.demo:
        runMockDemo()
    else:
        unittest.main(argv=[sys.argv[0], *remainingArgs])
