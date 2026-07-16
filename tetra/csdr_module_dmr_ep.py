"""OpenWebRX+ DMR Enhanced Privacy (RC4) decoder module.
Author: SP8MB

Wraps dmr_ep_decoder.py (dsd-fme front-end) as a PopenModule so DMR can be
decoded WITH an RC4 Enhanced Privacy / Basic Privacy key entered by the user.
digiham (the default DMR path) cannot decrypt; this module can.

Input: Complex float IQ samples (48 kS/s, centred on the DMR carrier)
Output: 16-bit signed PCM audio (8 kHz)
Metadata: JSON lines on stderr -> parsed and forwarded via metaWriter
"""

import json
import pickle
import threading
from functools import partial
from subprocess import Popen, PIPE

from csdr.module import PopenModule
from pycsdr.modules import Writer
from pycsdr.types import Format

import logging

logger = logging.getLogger(__name__)


class DmrEpDecoderModule(PopenModule):
    """DMR decoder with RC4 Enhanced/Basic Privacy decryption via dsd-fme."""

    def __init__(self, tetra_dir: str = "/opt/openwebrx-tetra", key: str = ""):
        self.tetra_dir = tetra_dir
        self.key = (key or "").strip()
        self.metaWriter = None
        self.metaThread = None
        super().__init__()

    def getCommand(self):
        cmd = ["python3", "-u", f"{self.tetra_dir}/dmr_ep_decoder.py"]
        if self.key:
            cmd += ["--key", self.key]
        return cmd

    def getInputFormat(self) -> Format:
        return Format.COMPLEX_FLOAT

    def getOutputFormat(self) -> Format:
        return Format.SHORT

    def _getProcess(self):
        return Popen(self.getCommand(), stdin=PIPE, stdout=PIPE, stderr=PIPE)

    def start(self):
        self.process = self._getProcess()
        self.reader.resume()

        # stdin pump (IQ in)
        threading.Thread(
            target=self.pump(self.reader.read, self.process.stdin.write),
            daemon=True,
        ).start()

        # stdout pump (PCM audio out)
        threading.Thread(
            target=self.pump(partial(self.process.stdout.read1, 1024), self.writer.write),
            daemon=True,
        ).start()

        # stderr reader (metadata JSON lines)
        self.metaThread = threading.Thread(target=self._readMeta, daemon=True)
        self.metaThread.start()

    def _readMeta(self):
        try:
            for line in self.process.stderr:
                if self.metaWriter is None:
                    continue
                line = line.strip()
                if not line or not line.startswith(b"{"):
                    continue    # skip dsd-fme banner / [dmr_ep] status lines
                try:
                    meta = json.loads(line)
                    self.metaWriter.write(pickle.dumps(meta))
                except (json.JSONDecodeError, Exception) as e:
                    logger.debug("DMR-EP meta parse error: %s", e)
        except (ValueError, OSError):
            pass

    def setMetaWriter(self, writer: Writer) -> None:
        self.metaWriter = writer

    def stop(self):
        super().stop()
