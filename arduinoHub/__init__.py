from typing import Optional, Union

from appConfig.settings import Settings, settings
from arduinoHub.lcdDisplay import LcdDisplay, lcdDisplay
from arduinoHub.networkConnection import NetworkConnection, networkConnection
from arduinoHub.sensorReader import SensorReader, sensorReader
from arduinoHub.serialConnection import SerialConnection, serialConnection
from arduinoHub.serialProtocol import SerialProtocol, formatLcdMessage, parseIncoming

ArduinoConnection = Union[SerialConnection, NetworkConnection]


def getConnectionProvider(
    connectionType: Optional[str] = None,
    appSettings: Optional[Settings] = None,
) -> ArduinoConnection:
    """Fábrica unificada que retorna NetworkConnection (Wi-Fi) ou SerialConnection (USB)."""
    cfg: Settings = appSettings or settings
    selectedType: str = (connectionType or cfg.arduinoConnectionType or "wifi").strip().lower()

    if selectedType == "wifi":
        return NetworkConnection(
            host=cfg.arduinoHost,
            port=cfg.arduinoPort,
            timeout=cfg.arduinoSocketTimeout,
            appSettings=cfg,
        )

    return SerialConnection(
        port=cfg.comPort,
        baudRate=cfg.baudRate,
        timeout=cfg.serialTimeout,
        appSettings=cfg,
    )


__all__: list[str] = [
    "ArduinoConnection",
    "LcdDisplay",
    "NetworkConnection",
    "SensorReader",
    "SerialConnection",
    "SerialProtocol",
    "formatLcdMessage",
    "getConnectionProvider",
    "lcdDisplay",
    "networkConnection",
    "parseIncoming",
    "sensorReader",
    "serialConnection",
]
