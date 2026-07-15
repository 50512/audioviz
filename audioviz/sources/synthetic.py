"""Fuente sintetica: tonos generados. Para desarrollar la GUI sin foobar corriendo.

Es la razon principal por la que vale la pena la abstraccion: puedes iterar el
visualizador sin depender de que suene musica, y testearlo de forma determinista.
"""

from __future__ import annotations

import threading
import time

import numpy as np

from .base import AudioSource, Frame, LatestSlot


class ToneSource(AudioSource):
    """Barrido de frecuencia, util para verificar el eje X de tu espectro."""

    def __init__(self, sample_rate: int = 44100, window: int = 4410,
                 channels: int = 2, fps: float = 60.0) -> None:
        self.sample_rate, self.window, self.channels = sample_rate, window, channels
        self.period = 1.0 / fps
        self._slot = LatestSlot()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def read(self) -> Frame | None:
        return self._slot.get()

    def _run(self) -> None:
        phase = 0
        t0 = time.monotonic()
        while not self._stop.is_set():
            elapsed = time.monotonic() - t0
            # Barrido log de 100 Hz a 10 kHz, ciclo de 8 s.
            freq = 100.0 * (100.0 ** ((elapsed % 8.0) / 8.0))

            t = (np.arange(self.window) + phase) / self.sample_rate
            mono = 0.5 * np.sin(2 * np.pi * freq * t)

            audio = np.zeros((self.window, self.channels), dtype=np.float32)
            for c in range(self.channels):
                # Paneo lento, para ver los canales moverse distinto.
                #
                # PROFUNDIDAD LIMITADA a propósito. La version anterior usaba
                # 0.5 + 0.5*sin(...), que con canales en antifase da ganancia
                # EXACTAMENTE 0.0 -> silencio real -> -inf dB. El canal
                # desaparecia cada 10 s y parecia un bug del motor. Una senal de
                # test que te hace dudar de codigo correcto es una mala senal de
                # test. Ahora el canal mas bajo se queda en -20 dBFS: se ve el
                # paneo, pero nunca se muere.
                MIN_GAIN = 0.1                     # -20 dBFS
                lfo = np.sin(2 * np.pi * 0.1 * elapsed + c * np.pi)   # -1..+1
                gain = MIN_GAIN + (1.0 - MIN_GAIN) * (0.5 + 0.5 * lfo)
                audio[:, c] = mono * gain

            self._slot.put(Frame(self.sample_rate, audio))
            phase += self.window
            time.sleep(self.period)
