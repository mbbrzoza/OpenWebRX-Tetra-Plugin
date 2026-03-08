"""OpenWebRX+ TETRA demodulator chain.
Author: SP8MB

Provides TETRA voice decoding as a primary demodulator chain,
following the same pattern as DMR/D-Star/YSF/NXDN in digiham.py.

TETRA uses pi/4-DQPSK modulation with 25 kHz channel spacing.
Symbol rate: 18000 sym/s, IF sample rate: 36000 S/s (2 sps).
Audio output: 8000 Hz PCM (ACELP codec).
"""

from csdr.chain import Chain
from csdr.chain.demodulator import BaseDemodulatorChain, FixedIfSampleRateChain, FixedAudioRateChain, MetaProvider
from pycsdr.modules import Writer, Buffer
from pycsdr.types import Format
from owrx.meta import MetaParser

import logging

logger = logging.getLogger(__name__)


class Tetra(BaseDemodulatorChain, FixedIfSampleRateChain, FixedAudioRateChain, MetaProvider):
    """TETRA voice demodulator chain for OpenWebRX+."""

    def __init__(self, tetra_dir: str = '/opt/openwebrx-tetra'):
        from csdr.module.tetra import TetraDecoderModule
        self.decoder = TetraDecoderModule(tetra_dir)
        workers = [self.decoder]
        self.metaParser = None
        super().__init__(workers)

    def getFixedIfSampleRate(self) -> int:
        return 36000

    def getFixedAudioRate(self) -> int:
        return 8000

    def setMetaWriter(self, writer: Writer) -> None:
        if self.metaParser is None:
            self.metaParser = MetaParser()
            buffer = Buffer(Format.CHAR)
            self.decoder.setMetaWriter(buffer)
            self.metaParser.setReader(buffer.getReader())
        self.metaParser.setWriter(writer)

    def supportsSquelch(self):
        return False

    def stop(self):
        if self.metaParser is not None:
            self.metaParser.stop()
        super().stop()
