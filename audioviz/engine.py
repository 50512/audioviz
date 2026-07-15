"""El motor. Es a esto a lo que se engancha la GUI.

Reparto de responsabilidades:

    Source  -> entrega audio crudo               (no sabe de FFT ni de pixeles)
    Engine  -> analiza y mantiene el estado      (no sabe de pixeles)
    GUI     -> dibuja y ofrece controles         (no sabe de sockets ni de WASAPI)

La GUI solo llama a poll(). Los parametros son propiedades vivas: escribirlas
reconfigura el motor en caliente, sin reiniciar nada.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .analysis import (DB_CEIL, DB_FLOOR, Smoother, bands_from_edges,
                       log_edges, octave_bands, spectra)
from .sources import AudioSource


@dataclass(frozen=True)
class VizFrame:
    """Todo lo que la GUI necesita para dibujar un frame. Nada mas."""

    sample_rate: int
    channels: int
    bands_db: np.ndarray   # (channels, n_bands) dBFS, ya suavizado
    peaks_db: np.ndarray   # (channels,) dBFS, SIN suavizar (verdad cruda)
    centers: np.ndarray    # (n_bands,) centro de cada banda en Hz

    def normalized(self) -> np.ndarray:
        """bands_db mapeado a 0..1. Es lo que quieres para alturas de barra."""
        return np.clip((self.bands_db - DB_FLOOR) / (DB_CEIL - DB_FLOOR), 0.0, 1.0)


def make_source(name: str, **kw) -> AudioSource:
    if name == "fb2k":
        from .sources import Fb2kSource
        return Fb2kSource(**kw)
    if name == "loopback":
        from .sources import LoopbackSource
        return LoopbackSource(**kw)
    if name == "tone":
        from .sources import ToneSource
        return ToneSource(**kw)
    raise ValueError(f"fuente desconocida: {name!r}")


class Engine:
    def __init__(self, source: str = "fb2k", fps: float = 60.0,
                 attack_ms: float = 20.0, decay_ms: float = 300.0,
                 n_bands: int = 48, lo_hz: float = 30.0, hi_hz: float = 16000.0,
                 distribution: str = "log", note_lo: str = "C0",
                 note_hi: str = "F#10", bands_per_octave: int = 12,
                 tuning: float = 440.0, transpose: int = 0,
                 bandwidth: float = 0.5) -> None:
        self._fps = fps
        self._attack_ms = attack_ms
        self._decay_ms = decay_ms
        self.lo_hz, self.hi_hz = lo_hz, hi_hz
        self.distribution = distribution

        if distribution == "octaves":
            # El numero de bandas NO se configura aca: lo dicta el rango de notas.
            self._plan = octave_bands(note_lo, note_hi, bands_per_octave,
                                      tuning, transpose, bandwidth)
        else:
            self._plan = log_edges(n_bands, lo_hz, hi_hz)
        self.n_bands = len(self._plan[2])

        self._smoother = Smoother(fps, attack_ms, decay_ms)
        self._source: AudioSource | None = None
        self._source_name = ""
        self._last: VizFrame | None = None

        self.set_source(source)

    # --- parametros vivos: la GUI escribe, el motor se reconfigura -----------

    @property
    def attack_ms(self) -> float:
        return self._attack_ms

    @attack_ms.setter
    def attack_ms(self, v: float) -> None:
        self._attack_ms = max(v, 0.0)
        self._retune()

    @property
    def decay_ms(self) -> float:
        return self._decay_ms

    @decay_ms.setter
    def decay_ms(self, v: float) -> None:
        self._decay_ms = max(v, 0.0)
        self._retune()

    @property
    def fps(self) -> float:
        return self._fps

    @fps.setter
    def fps(self, v: float) -> None:
        # Critico: alpha se deriva de tau Y del framerate. Si la GUI mide un fps
        # real distinto del nominal, hay que reavisar o las ballistics mienten.
        self._fps = max(v, 1.0)
        self._retune()

    def _retune(self) -> None:
        self._smoother.set_rates(self._fps, self._attack_ms, self._decay_ms)

    # --- fuente intercambiable en caliente ----------------------------------

    @property
    def source_name(self) -> str:
        return self._source_name

    def set_source(self, name: str, **kw) -> None:
        if self._source is not None:
            self._source.stop()
        self._source = make_source(name, **kw)
        self._source.start()
        self._source_name = name
        self._smoother.reset()   # el estado anterior no aplica a la fuente nueva
        self._last = None

    # --- lo unico que la GUI llama en su bucle -------------------------------

    def poll(self) -> VizFrame | None:
        """Ultimo frame analizado, o None si aun no hay audio. No bloquea."""
        frame = self._source.read()
        if frame is None:
            return self._last  # mantenemos el ultimo: evita parpadeo en huecos

        peaks_db, mags, freqs = spectra(frame)
        raw = bands_from_edges(mags, freqs, *self._plan)
        smoothed = self._smoother(raw).copy()

        self._last = VizFrame(
            sample_rate=frame.sample_rate,
            channels=frame.channels,
            bands_db=smoothed,
            peaks_db=peaks_db,
            centers=self._plan[2],
        )
        return self._last

    def close(self) -> None:
        if self._source:
            self._source.stop()

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
