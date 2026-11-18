import argparse
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
    SensorReader,
    SerialConnection,
    SerialProtocol,
    formatLcdMessage,
    lcdDisplay,
    parseIncoming,
    sensorReader,
    serialConnection,
)
from arduinoHub.serialConnection import SerialException
from pcHardware.pcStatusFacade import pcStatusFacade


class TestArduinoHub(unittest.TestCase):
    """Testes unitários e de integração do subsistema arduinoHub e ciclo de vida no main.py."""

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
        # Evento de comando de voz/texto retém exibição temporariamente e depois retoma telemetria
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

        self.bus.subscribe("arduinoSensorUpdated", lambda p: capturedSensors.append(p))
        self.bus.subscribe("arduinoButtonPressed", lambda p: capturedButtons.append(p))

        self.reader.injectMockSensor("TEMP", 25)
        self.conn.enqueueMockInput("<BTN:1>\n")
        self.reader.pollOnce()
        time.sleep(0.08)

        self.assertEqual(self.reader.getSensorValue("TEMP"), 25)
        self.assertEqual(len(capturedSensors), 1)
        self.assertEqual(capturedSensors[0]["name"], "TEMP")
        self.assertEqual(capturedSensors[0]["value"], 25)

        self.assertEqual(len(capturedButtons), 1)
        self.assertEqual(capturedButtons[0]["button"], 1)

        self.assertTrue(self.reader.sendCommand("RELAY1", "ON"))
        self.assertIn("<CMD:RELAY1:ON>", self.conn.getMockTxHistory())

    def testMainLifecycleInitializeAndGracefulShutdown(self) -> None:
        # Valida boot com fallback gracioso em caso de SerialException e envio de despedida no teardown
        with (
            patch("arduinoHub.serialConnection.serial") as mockSerialModule,
            patch.object(pcStatusFacade, "getSnapshot", return_value=self.mockSnapshot),
            patch.object(pcStatusFacade, "shutdown"),
            patch.object(self.bus, "shutdown"),
        ):
            mockSerialModule.Serial.side_effect = SerialException("Porta COM3 desconectada")
            container: jarvisMain.JarvisContainer = jarvisMain.initializeRuntime(
                startBackgroundWorkers=True
            )
            try:
                self.assertIs(container.serialConn, serialConnection)
                self.assertIs(container.hardwareFacade, pcStatusFacade)
                self.assertIs(container.display, lcdDisplay)
                self.assertTrue(container.serialConn.is_mock)

                time.sleep(0.08)
                txBeforeShutdown: list[str] = container.serialConn.getMockTxHistory()
                self.assertTrue(
                    any("JARVIS ONLINE" in pkt for pkt in txBeforeShutdown)
                )
            finally:
                jarvisMain.shutdownRuntime(container, goodbyeDelaySeconds=0.0)

            txAfterShutdown: list[str] = container.serialConn.getMockTxHistory()
            self.assertIn("<LCD:JARVIS OFFLINE  |ATE LOGO!       >", txAfterShutdown)
            self.assertFalse(container.serialConn.isOpen())


def runMockDemo(cycles: int = 4, intervalSeconds: float = 3.5) -> None:
    """Demonstra o subsistema arduinoHub com telemetria real em modo simulado (sem USB)."""
    print("=" * 70)
    print(" JARVIS - Demonstração arduinoHub (Modo Simulação / Mock)")
    print("=" * 70)
    print(f"Estado da conexão: {serialConnection.getStatus()}")

    bus: EventBus = EventBus()
    display: LcdDisplay = lcdDisplay
    reader: SensorReader = sensorReader
    display.subscribeToEvents()

    bus.subscribe(
        "arduinoSensorUpdated",
        lambda payload: print(f"[EVENTBUS] Sensor recebido: {payload}"),
    )
    bus.subscribe(
        "arduinoButtonPressed",
        lambda payload: print(f"[EVENTBUS] Botão acionado: {payload}"),
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

        print("\n4) Simulando entrada de sensor/botão do Arduino e envio de <CMD:...>...")
        reader.injectMockSensor("TEMP_AMB", 24.5)
        serialConnection.enqueueMockInput("<BTN:1>\n")
        reader.pollOnce()
        reader.sendCommand("RELAY1", "ON")
        time.sleep(0.2)
    finally:
        display.shutdown(sendGoodbye=True)
        reader.stop()
        serialConnection.disconnect()
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
