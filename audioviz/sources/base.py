"""Contrato comun a todas las fuentes de audio.

La idea entera del paquete cabe aca: una fuente es cualquier cosa que sepa
entregar la ventana de audio mas reciente. Como lo consiga -- WebSocket,
loopback de WASAPI, un archivo, un generador -- no le importa al visualizador.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Frame:
    """Una ventana de audio. Es TODO lo que el visualizador necesita saber."""

    sample_rate: int
    audio: np.ndarray  # (frames, channels) float32, interleaved ya deshecho

    @property
    def frames(self) -> int:
        return self.audio.shape[0]

    @property
    def channels(self) -> int:
        return self.audio.shape[1]


class AudioSource(ABC):
    """Fuente de audio.

    Contrato:
      - start() arranca la captura (no bloquea)
      - read()  devuelve el Frame MAS RECIENTE, o None si aun no hay nada.
                No bloquea. No encola.
      - stop()  libera recursos.

    'Latest-wins', no cola: el productor y el consumidor corren a ritmos
    distintos y desacoplados. Encolar haria crecer la latencia sin limite
    cuando la GUI va mas lenta que la fuente.
    """

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def read(self) -> Frame | None: ...

    # Azucar: with SomeSource() as src: ...
    def __enter__(self) -> "AudioSource":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


class LatestSlot:
    """Buzon de un solo hueco, thread-safe. El productor pisa; el consumidor lee."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: Frame | None = None

    def put(self, frame: Frame) -> None:
        with self._lock:
            self._frame = frame

    def get(self) -> Frame | None:
        with self._lock:
            return self._frame


class RingBuffer:
    """Buffer circular de frames de audio.

    Necesario para fuentes que entregan bloques CONTIGUOS (loopback de WASAPI),
    porque el visualizador quiere una ventana deslizante, no bloques sueltos.

    foobar via foo_uie_webview NO necesita esto: ya entrega ventanas solapadas
    centradas en la posicion de reproduccion. Esa diferencia semantica es la
    unica asimetria real entre las dos fuentes, y se resuelve aca.
    """

    def __init__(self, capacity: int, channels: int) -> None:
        self._buf = np.zeros((capacity, channels), dtype=np.float32)
        self._capacity = capacity
        self._write = 0
        self._filled = 0
        self._lock = threading.Lock()

    def write(self, block: np.ndarray) -> None:
        n = block.shape[0]
        if n == 0:
            return
        if n >= self._capacity:  # el bloque tapa el buffer entero
            block = block[-self._capacity:]
            n = self._capacity

        with self._lock:
            end = self._write + n
            if end <= self._capacity:
                self._buf[self._write:end] = block
            else:  # da la vuelta
                cut = self._capacity - self._write
                self._buf[self._write:] = block[:cut]
                self._buf[: end - self._capacity] = block[cut:]

            self._write = end % self._capacity
            self._filled = min(self._filled + n, self._capacity)

    def read_last(self, n: int) -> np.ndarray | None:
        """Las n muestras mas recientes, en orden cronologico."""
        with self._lock:
            if self._filled < n:
                return None
            start = (self._write - n) % self._capacity
            if start + n <= self._capacity:
                return self._buf[start:start + n].copy()
            cut = self._capacity - start
            out = np.empty((n, self._buf.shape[1]), dtype=np.float32)
            out[:cut] = self._buf[start:]
            out[cut:] = self._buf[: n - cut]
            return out
