"""OpenWebRX+ GSM cell identification chain (tryb "GSM").
Author: SP8MB

Kliknięcie nośnej BCCH (C0) GSM na wodospadzie w trybie "GSM" -> gsm_decoder.py (gr-gsm)
wyznacza BSIC, a z SI1..13: PLMN, LAC, CI, alokację, sąsiadów GSM/UMTS/LTE, GPRS.
Przestrojenie (setDialFrequency) uruchamia nową analizę.

IF sample rate: 500 000 S/s (complex). Audio: brak (cisza).
"""

from csdr.chain.demodulator import (
    BaseDemodulatorChain,
    FixedIfSampleRateChain,
    FixedAudioRateChain,
    MetaProvider,
    DialFrequencyReceiver,
)
from pycsdr.modules import Writer, Buffer
from pycsdr.types import Format
from owrx.meta import MetaParser

import logging

logger = logging.getLogger(__name__)


class Gsm(BaseDemodulatorChain, FixedIfSampleRateChain, FixedAudioRateChain, MetaProvider, DialFrequencyReceiver):
    """Identyfikacja komórki GSM (gr-gsm + SI)."""

    def __init__(self, tetra_dir: str = "/opt/openwebrx-tetra"):
        from csdr.module.gsm import GsmDecoderModule
        self.decoder = GsmDecoderModule(tetra_dir)
        self.metaParser = None
        super().__init__([self.decoder])

    def getFixedIfSampleRate(self) -> int:
        return 500000

    def getFixedAudioRate(self) -> int:
        return 12000

    def setDialFrequency(self, frequency: int) -> None:
        self.decoder.setDialFrequency(frequency)

    def setMetaWriter(self, writer: Writer) -> None:
        if self.metaParser is None:
            self.metaParser = MetaParser()
            buffer = Buffer(Format.CHAR)
            self.decoder.setMetaWriter(buffer)
            self.metaParser.setReader(buffer.getReader())
        self.metaParser.setWriter(writer)

    def supportsSquelch(self) -> bool:
        return False

    def stop(self):
        if self.metaParser is not None:
            self.metaParser.stop()
        super().stop()
