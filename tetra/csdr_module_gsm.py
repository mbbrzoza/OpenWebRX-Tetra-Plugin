"""OpenWebRX+ GSM cell identification module.
Author: SP8MB

Wraps gsm_decoder.py as a PopenModule.
Input: complex float IQ (500 kS/s, centred on the clicked GSM carrier)
Output: nothing (no audio) - SHORT format for the audio chain
Metadata: JSON lines on stderr (protocol "GSM") -> metaWriter
Retune: dial frequency written to a control file + SIGUSR1 to the decoder.
"""

import json
import os
import pickle
import signal
import threading
from functools import partial
from subprocess import Popen, PIPE

from csdr.module import PopenModule
from pycsdr.modules import Writer
from pycsdr.types import Format

import logging

logger = logging.getLogger(__name__)


class GsmDecoderModule(PopenModule):
    """GSM: BSIC/SI of the clicked BCCH carrier via gsm_decoder.py (gr-gsm + pycrate)."""

    def __init__(self, tetra_dir: str = "/opt/openwebrx-tetra"):
        self.tetra_dir = tetra_dir
        self.metaWriter = None
        self.metaThread = None
        self.frequency = None
        self.ready = False
        self.ctl = "/dev/shm/owrx_gsm_ctl_%d_%d" % (os.getpid(), id(self))
        super().__init__()

    def getCommand(self):
        return ["python3", "-u", f"{self.tetra_dir}/gsm_decoder.py", "--ctl", self.ctl]

    def getInputFormat(self) -> Format:
        return Format.COMPLEX_FLOAT

    def getOutputFormat(self) -> Format:
        return Format.SHORT

    def _getProcess(self):
        return Popen(self.getCommand(), stdin=PIPE, stdout=PIPE, stderr=PIPE)

    def _writeCtl(self):
        if self.frequency is None:
            return
        try:
            with open(self.ctl, "w") as f:
                f.write(str(int(self.frequency)))
        except OSError as e:
            logger.debug("GSM ctl write failed: %s", e)

    def setDialFrequency(self, frequency: int) -> None:
        if frequency == self.frequency:
            return
        self.frequency = frequency
        self._writeCtl()
        process = self.process
        if process is None:
            return
        if process.poll() is not None:
            # dekoder padł: uruchom ponownie (przeczyta częstotliwość z pliku ctl)
            logger.warning("gsm_decoder exited (%s), restarting", process.returncode)
            if self.reader is not None and self.writer is not None:
                self.start()
        elif self.ready:
            try:
                process.send_signal(signal.SIGUSR1)
            except OSError:
                pass

    def start(self):
        # AutoStartModule woła start() przy każdym setReader/setWriter: poprzedni proces
        # czyta ze starego czytnika, więc zamknij go zamiast zostawiać sierotę
        old = self.process
        if old is not None and old.poll() is None:
            old.terminate()
        self._writeCtl()
        self.ready = False
        self.process = self._getProcess()
        self.reader.resume()

        # stdin pump (IQ in)
        threading.Thread(
            target=self.pump(self.reader.read, self.process.stdin.write),
            daemon=True,
        ).start()

        # stdout pump (no audio, kept for the chain contract)
        threading.Thread(
            target=self.pump(partial(self.process.stdout.read1, 1024), self.writer.write),
            daemon=True,
        ).start()

        # stderr reader (metadata JSON lines)
        self.metaThread = threading.Thread(target=self._readMeta, args=(self.process,), daemon=True)
        self.metaThread.start()

    def _readMeta(self, process):
        try:
            for line in process.stderr:
                line = line.strip()
                if not line.startswith(b"{"):
                    if line:
                        logger.info("gsm_decoder: %s", line.decode("utf-8", "replace"))
                    continue
                try:
                    meta = json.loads(line)
                except ValueError as e:
                    logger.debug("GSM meta parse error: %s", e)
                    continue
                if meta.get("type") == "status" and meta.get("state") == "started" and process is self.process:
                    self.ready = True
                    if meta.get("freq") != self.frequency:
                        # przestrojono w trakcie startu dekodera
                        try:
                            process.send_signal(signal.SIGUSR1)
                        except OSError:
                            pass
                if self.metaWriter is not None:
                    try:
                        self.metaWriter.write(pickle.dumps(meta))
                    except Exception as e:
                        logger.debug("GSM meta write error: %s", e)
        except (ValueError, OSError):
            pass

    def setMetaWriter(self, writer: Writer) -> None:
        self.metaWriter = writer

    def stop(self):
        super().stop()
        try:
            os.unlink(self.ctl)
        except OSError:
            pass
