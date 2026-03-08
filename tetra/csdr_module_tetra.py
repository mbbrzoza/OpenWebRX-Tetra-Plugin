"""OpenWebRX+ TETRA decoder module.
Author: SP8MB

This module wraps the TETRA decoder pipeline as a PopenModule for
integration into the OpenWebRX+ signal processing chain.

Input: Complex float IQ samples (36 kS/s)
Output: 16-bit signed PCM audio (8 kHz)
Metadata: JSON lines on stderr -> parsed and forwarded via metaWriter
"""

import json
import pickle
import threading
from subprocess import Popen, PIPE

from csdr.module import PopenModule
from pycsdr.modules import Writer
from pycsdr.types import Format

import logging

logger = logging.getLogger(__name__)


class TetraDecoderModule(PopenModule):
    """TETRA DQPSK demodulator + protocol decoder + ACELP codec.

    Wraps tetra_decoder.py which manages the full pipeline:
    IQ -> GNURadio DQPSK demod -> tetra-rx -> ACELP codec -> PCM

    Metadata (TETMON signaling) is emitted as JSON lines on stderr
    and forwarded to the meta writer for display in the frontend panel.
    """

    def __init__(self, tetra_dir: str = "/opt/openwebrx-tetra"):
        self.tetra_dir = tetra_dir
        self.metaWriter = None
        self.metaThread = None
        super().__init__()

    def getCommand(self):
        return ["python3", "-u", f"{self.tetra_dir}/tetra_decoder.py"]

    def getInputFormat(self) -> Format:
        return Format.COMPLEX_FLOAT

    def getOutputFormat(self) -> Format:
        return Format.SHORT

    def _getProcess(self):
        return Popen(self.getCommand(), stdin=PIPE, stdout=PIPE, stderr=PIPE)

    def start(self):
        self.process = self._getProcess()
        self.reader.resume()

        # stdin pump (IQ data in)
        threading.Thread(
            target=self.pump(self.reader.read, self.process.stdin.write),
            daemon=True
        ).start()

        # stdout pump (PCM audio out)
        from functools import partial
        threading.Thread(
            target=self.pump(partial(self.process.stdout.read1, 1024), self.writer.write),
            daemon=True
        ).start()

        # stderr reader (metadata JSON lines)
        self.metaThread = threading.Thread(
            target=self._readMeta,
            daemon=True
        )
        self.metaThread.start()

    def _readMeta(self):
        """Read JSON metadata lines from subprocess stderr."""
        try:
            for line in self.process.stderr:
                if self.metaWriter is None:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    meta = json.loads(line)
                    self.metaWriter.write(pickle.dumps(meta))
                except (json.JSONDecodeError, Exception) as e:
                    logger.debug("TETRA meta parse error: %s", e)
        except (ValueError, OSError):
            pass

    def setMetaWriter(self, writer: Writer) -> None:
        self.metaWriter = writer

    def stop(self):
        super().stop()
