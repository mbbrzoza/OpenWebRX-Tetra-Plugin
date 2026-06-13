"""OpenWebRX+ LoRa-APRS decoder module (secondary — wzorzec Rtl433Module).
Author: SP8MB

Spawnuje lora_decoder.py: IQ (COMPLEX_FLOAT @250k) na stdin → linie JSON (CHAR)
na stdout (zdekodowane ramki APRS), które idą do LoRaAprsParser → mapa OWRX.
Stderr dekodera = log (status/błędy), drenowany do logger.
"""

import threading
from functools import partial
from subprocess import Popen, PIPE

from csdr.module import PopenModule
from pycsdr.types import Format

import logging

logger = logging.getLogger(__name__)


class LoRaDecoderModule(PopenModule):
    """LoRa-APRS: CSS demod + sync + dekod ramki Semtech → linie JSON dla parsera."""

    def __init__(self, lora_dir: str = "/opt/openwebrx-tetra"):
        self.lora_dir = lora_dir
        super().__init__()

    def getCommand(self):
        return ["python3", "-u", f"{self.lora_dir}/lora_decoder.py"]

    def getInputFormat(self) -> Format:
        return Format.COMPLEX_FLOAT

    def getOutputFormat(self) -> Format:
        return Format.CHAR

    def _getProcess(self):
        return Popen(self.getCommand(), stdin=PIPE, stdout=PIPE, stderr=PIPE)

    def start(self):
        self.process = self._getProcess()
        self.reader.resume()

        threading.Thread(
            target=self.pump(self.reader.read, self.process.stdin.write),
            daemon=True,
        ).start()

        threading.Thread(
            target=self.pump(partial(self.process.stdout.read1, 1024), self.writer.write),
            daemon=True,
        ).start()

        threading.Thread(target=self._drainStderr, daemon=True).start()

    def _drainStderr(self):
        try:
            for line in self.process.stderr:
                logger.debug("lora_decoder: %s", line.decode("utf-8", "replace").strip())
        except (ValueError, OSError):
            pass

    def stop(self):
        super().stop()
