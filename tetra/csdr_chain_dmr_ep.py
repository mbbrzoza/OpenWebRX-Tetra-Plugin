"""OpenWebRX+ DMR Enhanced Privacy (RC4) demodulator chain.
Author: SP8MB

A DMR listening chain that can DECRYPT Enhanced Privacy (ALG 01 / RC4-40) and
Basic Privacy when given the key, by routing through dsd-fme instead of digiham.
Select this mode ("DMR EP") and enter the RC4 key in the profile to hear/read
encrypted traffic you are authorised to monitor.

IF sample rate: 48000 S/s (complex). Audio output: 8000 Hz PCM (AMBE via mbelib).
"""

from csdr.chain import Chain
from csdr.chain.demodulator import (
    BaseDemodulatorChain,
    FixedIfSampleRateChain,
    FixedAudioRateChain,
    MetaProvider,
)
from pycsdr.modules import Writer, Buffer
from pycsdr.types import Format
from owrx.meta import MetaParser

import logging

logger = logging.getLogger(__name__)


class DmrEp(BaseDemodulatorChain, FixedIfSampleRateChain, FixedAudioRateChain, MetaProvider):
    """DMR demodulator chain with RC4 privacy decryption (dsd-fme)."""

    def __init__(self, key: str = "", tetra_dir: str = "/opt/openwebrx-tetra"):
        from csdr.module.dmr_ep import DmrEpDecoderModule
        self.decoder = DmrEpDecoderModule(tetra_dir, key=key)
        self.metaParser = None
        super().__init__([self.decoder])

    def getFixedIfSampleRate(self) -> int:
        return 48000

    def getFixedAudioRate(self) -> int:
        return 8000

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
