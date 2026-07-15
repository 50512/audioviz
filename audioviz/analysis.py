"""Analisis y render. Totalmente agnostico del origen: solo consume Frame."""

from __future__ import annotations

import numpy as np

from .sources import Frame

DB_FLOOR = -70.0
DB_CEIL = 0.0


def spectra(frame: Frame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """FFT por canal, sin mezclar.

    Devuelve:
        peaks_db  (channels,)       pico por canal en dBFS
        mags      (channels, bins)  magnitud por canal, normalizada a dBFS
        freqs     (bins,)           eje de frecuencias en Hz
    """
    audio = frame.audio
    n = frame.frames

    peaks = np.abs(audio).max(axis=0)
    with np.errstate(divide="ignore"):
        peaks_db = 20.0 * np.log10(np.maximum(peaks, 1e-12))

    window = np.hanning(n)

    # axis=0 procesa todos los canales de una pasada, sin bucle en Python.
    # La division por sum(window) es la ganancia coherente de la ventana: sin
    # ella la magnitud escala con N y el grafico satura (y cambia de brillo al
    # tocar el tamano de ventana). Con ella, 0 dBFS = senoidal a fondo de escala.
    mags = np.abs(np.fft.rfft(audio * window[:, None], axis=0))
    mags *= 2.0 / np.sum(window)
    mags = mags.T

    freqs = np.fft.rfftfreq(n, d=1.0 / frame.sample_rate)
    return peaks_db, mags, freqs


NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def parse_note(name: str) -> int:
    """'C0' -> 12,  'A4' -> 69,  'F#10' -> 138. Notacion cientifica (C4 = 60)."""
    s = name.strip().upper()
    i = 1 + (len(s) > 1 and s[1] == "#")
    return NOTES.index(s[:i]) + 12 * (int(s[i:]) + 1)


def log_edges(n: int, lo: float = 30.0, hi: float = 16000.0):
    """Distribucion geometrica entre dos frecuencias. -> (lo_edges, hi_edges, centers)"""
    e = np.geomspace(lo, hi, n + 1)
    return e[:-1], e[1:], np.sqrt(e[:-1] * e[1:])


def octave_bands(note_lo: str = "C0", note_hi: str = "F#10",
                 bands_per_octave: int = 12, tuning: float = 440.0,
                 transpose: int = 0, bandwidth: float = 0.5):
    """Distribucion MUSICAL, como el modo 'Octaves' de foo_vis_spectrum_analyzer.

    Las bandas son notas, no un barrido log arbitrario. Con bands_per_octave=12
    cada banda es un semitono. El numero de bandas NO se configura: se deriva.

        f(midi) = tuning * 2^((midi - 69) / 12)

    bandwidth es el semiancho en pasos de banda: 0.5 -> bandas contiguas
    (medio paso a cada lado), <0.5 deja huecos, >0.5 las solapa.
    """
    lo = parse_note(note_lo)
    hi = parse_note(note_hi)
    step = 12.0 / bands_per_octave                      # en semitonos
    n = int(round((hi - lo) / step)) + 1

    midi = lo + np.arange(n) * step + transpose
    centers = tuning * 2.0 ** ((midi - 69.0) / 12.0)

    half = bandwidth * step / 12.0                      # en octavas
    return centers * 2.0 ** -half, centers * 2.0 ** half, centers


def bands(mags: np.ndarray, freqs: np.ndarray, n: int = 24,
          lo: float = 30.0, hi: float = 16000.0) -> np.ndarray:
    """Atajo: distribucion log. -> (channels, n) en dBFS."""
    return bands_from_edges(mags, freqs, *log_edges(n, lo, min(hi, freqs[-1])))


def bands_from_edges(mags: np.ndarray, freqs: np.ndarray, lo_e: np.ndarray,
                     hi_e: np.ndarray, centers: np.ndarray) -> np.ndarray:
    """Agrupa el espectro en las bandas dadas. -> (channels, n_bands) en dBFS.

    Dos casos degenerados, ambos reales:

    1. Banda mas ANGOSTA que un bin -> no contiene ningun bin -> searchsorted
       devuelve un rango vacio y la barra se quedaria clavada en el piso.
       Solucion: interpolar la magnitud en el centro de la banda.

       Ojo: interpolar NO inventa resolucion. Con ventana de 100 ms (df=10 Hz)
       un semitono solo es resoluble por encima de ~168 Hz (df / (2^(1/12)-1)).
       Por debajo, esas barras son interpolacion honesta de bins vecinos, no
       medicion. Para resolucion REAL en graves hay que alargar la ventana:
       df*dt ~ 1 es un limite fisico, no un detalle de implementacion.

    2. Banda por encima de Nyquist (F#10 = 23.7 kHz > 22.05 kHz a 44.1k).
       Ahi no hay informacion en absoluto -> piso, y punto.
    """
    nyquist = freqs[-1]
    a_idx = np.searchsorted(freqs, lo_e, side="left")
    z_idx = np.searchsorted(freqs, hi_e, side="right")

    with np.errstate(divide="ignore"):
        db_full = 20.0 * np.log10(np.maximum(mags, 1e-12))

    n = len(centers)
    out = np.full((mags.shape[0], n), DB_FLOOR, dtype=np.float32)
    empty = []

    for b in range(n):
        if centers[b] > nyquist:
            continue                        # sin informacion: piso
        a, z = a_idx[b], z_idx[b]
        if z > a:
            out[:, b] = db_full[:, a:z].max(axis=1)
        else:
            empty.append(b)

    if empty:
        ce = centers[empty]
        for c in range(db_full.shape[0]):
            out[c, empty] = np.interp(ce, freqs, db_full[c])

    return out


class Smoother:
    """Envelope follower asimetrico, con constantes de tiempo en MILISEGUNDOS.

    state += (target - state) * alpha    <- media movil exponencial (IIR 1 polo)

    alpha se DERIVA de la constante de tiempo y del framerate:

        alpha = 1 - exp(-dt / tau)   con dt = 1/fps

    Esto es lo que hace que el visualizador se comporte igual a 30, 60 o 144 fps.
    Con un alpha fijo, tau = -dt / ln(1-alpha) cambia con los fps: el mismo
    codigo se siente distinto segun el framerate. Eso no es una perilla, es un bug.

    Por que attack != decay:

      attack (subida)  -- gobierna los transientes. Un transiente de UN frame
          solo alcanza alpha * su altura real. Con attack lento, la barra
          MIENTE sobre el pico. Rapido = veraz.

      decay  (bajada)  -- gobierna el parpadeo. Cada ventana FFT es una muestra
          distinta de una senal ruidosa. La caida lenta da persistencia visual,
          igual que el retorno lento de un VU.

    "Decir la verdad rapido, olvidar despacio."

    Referencias (tau al 63%, tiempo al 90% ~ 2.3x tau):
        attack   5 ms  -- casi sin suavizar, veraz y nervioso
        attack  20 ms  -- transientes limpios (por defecto)
        attack  60 ms  -- ya empieza a achatar los picos
        decay   80 ms  -- muy reactivo, parpadea con material denso
        decay  300 ms  -- balanceado, ballistics tipo VU (por defecto)
        decay  800 ms  -- casi peak-hold, hipnotico

    Se suaviza en dB (dominio log), no en amplitud lineal: asi el movimiento es
    perceptualmente uniforme en todo el rango dinamico.
    """

    def __init__(self, fps: float, attack_ms: float = 20.0,
                 decay_ms: float = 300.0, floor: float = DB_FLOOR) -> None:
        self.floor = floor
        self.set_rates(fps, attack_ms, decay_ms)
        self._state: np.ndarray | None = None

    def set_rates(self, fps: float, attack_ms: float, decay_ms: float) -> None:
        """Recalcula los alphas. Llamalo si cambias de framerate en caliente."""
        dt_ms = 1000.0 / fps
        self.fps, self.attack_ms, self.decay_ms = fps, attack_ms, decay_ms
        self.attack = 1.0 - np.exp(-dt_ms / max(attack_ms, 1e-6))
        self.decay = 1.0 - np.exp(-dt_ms / max(decay_ms, 1e-6))

    def reset(self) -> None:
        """Olvida el estado. Llamalo al cambiar de fuente: las ballistics de la
        fuente anterior no tienen nada que ver con la nueva."""
        self._state = None

    def __call__(self, db: np.ndarray) -> np.ndarray:
        # Recortar el TARGET al piso, no solo al dibujar. Sin esto el estado
        # persigue -240 dB en silencio y el attack tiene que trepar desde ahi:
        # el primer frame tras un silencio sale amputado (medido: 0% de barra).
        db = np.maximum(db, self.floor)

        if self._state is None or self._state.shape != db.shape:
            self._state = db.copy()
            return self._state

        rising = db > self._state
        self._state[rising] += (db[rising] - self._state[rising]) * self.attack
        self._state[~rising] += (db[~rising] - self._state[~rising]) * self.decay
        return self._state


GLYPHS = " ▁▂▃▄▅▆▇█"


def to_glyphs(db_row: np.ndarray) -> str:
    t = np.clip((db_row - DB_FLOOR) / (DB_CEIL - DB_FLOOR), 0.0, 1.0)
    return "".join(GLYPHS[int(v * (len(GLYPHS) - 1))] for v in t)
