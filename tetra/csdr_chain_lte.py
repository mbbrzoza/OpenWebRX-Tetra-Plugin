"""OpenWebRX+ LTE cell identification chain (tryb "LTE").
Author: SP8MB

Kliknięcie nośnej LTE na wodospadzie w trybie "LTE" -> lte_decoder.py wyznacza PCI,
MIB, a dla komórek 1,4 MHz także SIB1..7 (PLMN, TAC, Cell ID, pasmo, sąsiedzi).
Przestrojenie (setDialFrequency) uruchamia nową analizę.

IF sample rate: 1 920 000 S/s (complex) = 6 PRB. Audio: brak (cisza).
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


class Lte(BaseDemodulatorChain, FixedIfSampleRateChain, FixedAudioRateChain, MetaProvider, DialFrequencyReceiver):
    """Identyfikacja komórki LTE (PSS/SSS + pdsch_ue + SIB)."""

    def __init__(self, tetra_dir: str = "/opt/openwebrx-tetra"):
        from csdr.module.lte import LteDecoderModule
        self.decoder = LteDecoderModule(tetra_dir)
        self.metaParser = None
        super().__init__([self.decoder])

    def getFixedIfSampleRate(self) -> int:
        return 1920000

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
