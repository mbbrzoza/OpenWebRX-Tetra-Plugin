"""OpenWebRX+ LoRa-APRS demodulator chain (secondary/service — wzorzec ISM/packet).
Author: SP8MB

LoRa-APRS jako secondary digimode na nośnej 'empty' (surowy IQ). CSS (chirp spread
spectrum), BW 125 kHz, SF auto. IF 250 kS/s. Łańcuch: LoRaDecoderModule (IQ→JSON
linie) → LoRaAprsParser (JSON → owrx.aprs.AprsParser → mapa OWRX + klient).
"""

import json
import logging

from csdr.chain.demodulator import ServiceDemodulator, DialFrequencyReceiver
from owrx.toolbox import TextParser
from owrx.aprs import AprsParser

logger = logging.getLogger(__name__)


class LoRaAprsParser(TextParser):
    """Czyta linie JSON z lora_decoder.py → owrx.aprs.AprsParser → mapa OWRX + klient.
    Analog do IsmParser (rtl_433 JSON), ale dla LoRa-APRS (TNC2 zdekodowane przez nas)."""

    def __init__(self, service: bool = False):
        super().__init__(filePrefix="LORA", service=service)
        self.aprs = AprsParser()

    def parse(self, msg: bytes):
        try:
            d = json.loads(msg)
        except (ValueError, TypeError):
            return None
        if not isinstance(d, dict) or d.get("type") != "aprs":
            return None
        ad = {
            "source": d.get("source", "?"),
            "destination": d.get("destination", "APRS"),
            "path": d.get("path", []),
            "data": (d.get("info", "") or "").encode("latin-1", "replace"),
            "raw": d.get("raw", ""),
        }
        out = self.aprs.process(ad)          # parsuje pozycję + aktualizuje mapę
        # zostaw mode="APRS" (frontend renderuje w panelu wiadomości); dorzuć znacznik LoRa
        if out is not None:
            out["band"] = out.get("band") or "LoRa"
        return out

    def setDialFrequency(self, frequency: int) -> None:
        super().setDialFrequency(frequency)
        self.aprs.setDialFrequency(frequency)


class LoRaAprsDemodulator(ServiceDemodulator, DialFrequencyReceiver):
    """LoRa-APRS secondary demodulator dla OpenWebRX+ (jak IsmDemodulator)."""

    def __init__(self, sampleRate: int = 250000, service: bool = False):
        from csdr.module.lora import LoRaDecoderModule
        self.sampleRate = sampleRate
        self.parser = LoRaAprsParser(service=service)
        workers = [LoRaDecoderModule(), self.parser]
        super().__init__(workers)

    def getFixedAudioRate(self) -> int:
        return self.sampleRate

    def supportsSquelch(self) -> bool:
        return False

    def setDialFrequency(self, frequency: int) -> None:
        self.parser.setDialFrequency(frequency)
