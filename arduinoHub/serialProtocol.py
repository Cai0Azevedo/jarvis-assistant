import re
import unicodedata
from typing import Any, Optional


class SerialProtocol:
    """Empacotador e parser do protocolo serial determinístico do Projeto JARVIS.

    Formatos suportados:
    - PC -> Arduino (LCD 16x2): ``<LCD:Linha1(16)|Linha2(16)>\\n``
    - PC -> Arduino (Comando): ``<CMD:ACAO:VALOR>\\n``
    - Arduino -> PC (Sensor): ``<SEN:NOME:VALOR>\\n``
    - Arduino -> PC (Botão): ``<BTN:ID>\\n`` ou ``<BTN:ID:ESTADO>\\n``
    """

    DEFAULT_LCD_COLS: int = 16
    _PACKET_PATTERN: re.Pattern[str] = re.compile(r"^<([A-Z0-9_]+):(.*)>$")

    @classmethod
    def sanitizeText(cls, text: str, maxLength: Optional[int] = None) -> str:
        """Remove delimitadores do protocolo e normaliza caracteres para ASCII seguro em LCD."""
        normalized: str = unicodedata.normalize("NFKD", str(text))
        asciiText: str = normalized.encode("ascii", "ignore").decode("ascii")
        cleaned: str = (
            asciiText.replace("<", "")
            .replace(">", "")
            .replace("|", "/")
            .replace("\r", " ")
            .replace("\n", " ")
            .strip()
        )
        if maxLength is not None and maxLength > 0:
            return cleaned[:maxLength]
        return cleaned

    @classmethod
    def formatLcdLine(cls, text: str, cols: int = DEFAULT_LCD_COLS) -> str:
        """Sanitiza, trunca e preenche com espaços para exatamente ``cols`` caracteres."""
        safeText: str = cls.sanitizeText(text, maxLength=cols)
        return safeText.ljust(cols)

    @classmethod
    def formatLcdMessage(
        cls,
        line1: str,
        line2: str = "",
        maxCols: int = DEFAULT_LCD_COLS,
    ) -> str:
        """Formata duas linhas de exatos 16 caracteres no padrão ``<LCD:Linha1|Linha2>\\n``."""
        formattedLine1: str = cls.formatLcdLine(line1, cols=maxCols)
        formattedLine2: str = cls.formatLcdLine(line2, cols=maxCols)
        return f"<LCD:{formattedLine1}|{formattedLine2}>\n"

    @classmethod
    def encodeLcd(
        cls,
        line1: str,
        line2: str = "",
        maxCols: int = DEFAULT_LCD_COLS,
    ) -> str:
        """Alias compatível para ``formatLcdMessage``."""
        return cls.formatLcdMessage(line1, line2, maxCols=maxCols)

    @classmethod
    def encodeCommand(cls, action: str, value: Any) -> str:
        """Empacota um comando de atuador no formato ``<CMD:ACAO:VALOR>\\n``."""
        safeAction: str = (
            cls.sanitizeText(action).replace(":", "_").replace(" ", "_").upper()
        )
        safeValue: str = cls.sanitizeText(str(value)).replace(":", "_")
        if not safeAction:
            raise ValueError("A ação do comando serial não pode ser vazia.")
        return f"<CMD:{safeAction}:{safeValue}>\n"

    @classmethod
    def encodeSensor(cls, sensorName: str, value: Any) -> str:
        """Empacota uma leitura de sensor no formato ``<SEN:NOME:VALOR>\\n``."""
        safeName: str = (
            cls.sanitizeText(sensorName).replace(":", "_").replace(" ", "_").upper()
        )
        safeValue: str = cls.sanitizeText(str(value)).replace(":", "_")
        return f"<SEN:{safeName}:{safeValue}>\n"

    @classmethod
    def parseIncoming(cls, rawMessage: str) -> dict[str, Any]:
        """Interpreta mensagens vindas do Arduino (ex: ``<BTN:1>`` ou ``<SEN:TEMP:25>``)."""
        if not rawMessage:
            return {}

        cleanedLine: str = rawMessage.strip()
        match: Optional[re.Match[str]] = cls._PACKET_PATTERN.match(cleanedLine)
        if not match:
            return {"type": "INVALID", "raw": cleanedLine}

        packetType: str = match.group(1)
        body: str = match.group(2)

        if packetType == "SEN":
            parts: list[str] = body.split(":", 1)
            if len(parts) < 2 or not parts[0].strip():
                return {"type": "INVALID", "raw": cleanedLine}
            sensorName: str = parts[0].strip().upper()
            rawValue: str = parts[1].strip()
            return {
                "type": "SEN",
                "name": sensorName,
                "value": cls._parseScalarValue(rawValue),
                "raw": cleanedLine,
            }

        if packetType == "BTN":
            parts = body.split(":", 1)
            buttonId: Any = cls._parseScalarValue(parts[0].strip())
            state: Any = (
                cls._parseScalarValue(parts[1].strip()) if len(parts) > 1 else "PRESSED"
            )
            return {
                "type": "BTN",
                "button": buttonId,
                "state": state,
                "value": buttonId,
                "raw": cleanedLine,
            }

        if packetType == "CMD":
            parts = body.split(":", 1)
            if len(parts) < 2:
                return {"type": "INVALID", "raw": cleanedLine}
            return {
                "type": "CMD",
                "action": parts[0].strip().upper(),
                "value": parts[1].strip(),
                "raw": cleanedLine,
            }

        if packetType == "LCD":
            parts = body.split("|", 1)
            return {
                "type": "LCD",
                "line1": parts[0],
                "line2": parts[1] if len(parts) > 1 else "",
                "raw": cleanedLine,
            }

        return {
            "type": packetType,
            "payload": body,
            "raw": cleanedLine,
        }

    @classmethod
    def parsePacket(cls, rawLine: str) -> Optional[dict[str, Any]]:
        """Faz o parsing retornando ``None`` para mensagens vazias ou inválidas."""
        parsed: dict[str, Any] = cls.parseIncoming(rawLine)
        if not parsed or parsed.get("type") == "INVALID":
            return None
        return parsed

    @staticmethod
    def _parseScalarValue(rawValue: str) -> Any:
        """Converte valores numéricos recebidos via serial para float/int quando aplicável."""
        if not rawValue:
            return rawValue
        try:
            if "." in rawValue:
                return float(rawValue)
            return int(rawValue)
        except ValueError:
            return rawValue


def formatLcdMessage(line1: str, line2: str = "") -> str:
    """Função utilitária de módulo para formatar mensagens no padrão ``<LCD:L1|L2>\\n``."""
    return SerialProtocol.formatLcdMessage(line1, line2)


def parseIncoming(rawMessage: str) -> dict[str, Any]:
    """Função utilitária de módulo para interpretar pacotes seriais recebidos do Arduino."""
    return SerialProtocol.parseIncoming(rawMessage)
