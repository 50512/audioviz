"""Fuente: monitor de PipeWire/PulseAudio (captura lo que suena en el sistema).

    El equivalente en Linux del loopback de WASAPI: en vez de un dispositivo de
    entrada real, se conecta al MONITOR del sink de salida -- el "espejo" de lo
    que va a los parlantes -- y captura ese stream.

    ADVERTENCIAS -- leelas antes de usar esto:

    1. Captura TODO lo que suena en ese sink: tu musica, el navegador, Discord,
       los sonidos del sistema. No solo una app.

    2. El sample_rate es el del ENDPOINT (el sink, tipicamente 48000 Hz), no el
       del archivo. Un FLAC de 44.1k llega aca ya resampleado por el grafo de
       audio. Misma semantica que LoopbackSource en Windows.

    3. Si el sink esta SUSPENDIDO (nada suena) el monitor no entrega datos: read()
       devuelve None hasta que algo empiece a sonar. No es un fallo.

    Por defecto sigue al sink PREDETERMINADO via @DEFAULT_MONITOR@: si cambias la
    salida del sistema, la captura te sigue sin reiniciar. Con device_name puedes
    fijar un sink concreto (se le agrega '.monitor' si hace falta) o cualquier
    source por nombre parcial.

Requiere: el binario `parec` (viene con pipewire-pulse o pulseaudio). No hay
dependencia de Python: parec entrega PCM crudo por su stdout y aca solo se lee.
Por eso, a diferencia de las fuentes de Windows, esta no aparece en
requirements.txt. Si `parec` no existe, start() falla y el motor cae a la
siguiente fuente del fallback (ver Engine.set_source).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading

import numpy as np

from ..base import AudioSource, Frame, RingBuffer

# Device virtual que apunta siempre al monitor del sink predeterminado. parec lo
# resuelve en cada arranque, asi que cambiar la salida del sistema no exige tocar
# nada aca.
DEFAULT_MONITOR = "@DEFAULT_MONITOR@"

# Formato PCM que le pedimos a parec. float32 nativo -> np.frombuffer sin
# conversion, mismo dtype que consume el resto del pipeline.
_PAREC_FORMAT = "float32le"

# Fallback si no se puede sondear el formato nativo del monitor. 48 kHz estereo es
# lo tipico de un endpoint moderno; parec resamplea/remixa si el sink difiere.
_FALLBACK_RATE = 48000
_FALLBACK_CHANNELS = 2

# Latencia de ENTREGA que le pedimos a parec, en ms. NO tiene que ver con
# window_ms (el tamano de la ventana de ANALISIS, que sigue siendo 100 ms): esto
# es cada cuanto parec vuelca datos por la tuberia. Si es alta (p.ej. = window_ms
# = 100 ms), parec entrega en RAFAGAS de 100 ms: el ring avanza a saltos y, con el
# render a 60 fps (~16 ms), se repite la misma ventana ~6 veces y luego pega un
# salto -> movimiento a tirones. Con 10 ms los datos llegan mas finos que un frame
# de render, asi la ventana deslizante avanza suave. Confundir estas dos duraciones
# fue justo lo que causaba el stutter.
_LATENCY_MS = 10

# Cada cuantos ms leemos del pipe (y por tanto avanza el ring). Por debajo del
# periodo de render (16 ms @60 fps) para que cada frame vea una ventana ya
# deslizada. Reemplaza al viejo window//4 (~25 ms), que dejaba el ring por debajo
# del ritmo de render aunque parec entregara suave.
_READ_MS = 5


def _probe_device(device_name: str | None) -> tuple[str, int, int]:
    """Resuelve (device, sample_rate, channels) para parec.

    Sin device_name: el monitor del sink predeterminado. Con device_name: el
    primer source cuyo nombre lo contenga (preferimos los '.monitor', que son la
    salida capturable), o el propio nombre + '.monitor' si el usuario dio un sink.

    El rate/channels sale del formato que reporta `pactl list short sources` para
    ese device; si no se puede leer, caemos a los defaults. parec adapta el stream
    a lo que pidamos, asi que un valor equivocado degrada la calidad pero no rompe.
    """
    rows = _pactl_sources()

    if device_name is None:
        sink = _default_sink()
        monitor = f"{sink}.monitor" if sink else None
        rate, channels = rows.get(monitor, (_FALLBACK_RATE, _FALLBACK_CHANNELS))
        # Usamos el nombre virtual (no `monitor`) para que parec siga al sink
        # predeterminado aunque cambie despues del arranque.
        return DEFAULT_MONITOR, rate, channels

    key = device_name.lower()
    # Preferimos monitores: son la SALIDA del sistema, que es lo que esta fuente
    # promete capturar. Un source de entrada que casara el nombre seria un microfono.
    monitors = [name for name in rows if key in name.lower() and name.endswith(".monitor")]
    others = [name for name in rows if key in name.lower() and not name.endswith(".monitor")]
    match = (monitors or others or [None])[0]
    if match is None:
        # El usuario pudo nombrar un sink en vez de su monitor: probamos su espejo.
        match = f"{device_name}.monitor"
    rate, channels = rows.get(match, (_FALLBACK_RATE, _FALLBACK_CHANNELS))
    return match, rate, channels


def _pactl_sources() -> dict[str, tuple[int, int]]:
    """Mapa nombre_de_source -> (rate, channels) leido de pactl. Vacio si falla."""
    try:
        out = subprocess.run(
            ["pactl", "list", "short", "sources"],
            capture_output=True, text=True, timeout=2.0,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}

    result: dict[str, tuple[int, int]] = {}
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        name, fmt = cols[1], cols[3]
        ch = re.search(r"(\d+)ch", fmt)
        hz = re.search(r"(\d+)Hz", fmt)
        result[name] = (
            int(hz.group(1)) if hz else _FALLBACK_RATE,
            int(ch.group(1)) if ch else _FALLBACK_CHANNELS,
        )
    return result


def _default_sink() -> str | None:
    try:
        out = subprocess.run(
            ["pactl", "get-default-sink"],
            capture_output=True, text=True, timeout=2.0,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


class PipeWireSource(AudioSource):
    def __init__(self, window_ms: float = 100.0, device_name: str | None = None) -> None:
        # En MILISEGUNDOS, no en frames: misma ventana (en duracion) que loopback y
        # fb2k, para que attack/decay se comporten igual al cambiar de fuente.
        self.window_ms = window_ms
        self.window = 0          # frames; se deriva del sample rate real en start()
        self.device_name = device_name
        self.sample_rate = 0
        self.channels = 0
        self._proc: subprocess.Popen[bytes] | None = None
        self._ring: RingBuffer | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if shutil.which("parec") is None:
            # Sin parec no hay nada que hacer: que el motor caiga a la siguiente
            # fuente (tone) en vez de arrastrar un proceso muerto.
            raise RuntimeError("parec no esta instalado (pipewire-pulse / pulseaudio)")

        device, self.sample_rate, self.channels = _probe_device(self.device_name)

        # La ventana en frames se deriva del rate REAL del endpoint. A 48 kHz,
        # 100 ms = 4800 frames. La DURACION es lo invariante (igual que loopback).
        self.window = max(64, int(self.sample_rate * self.window_ms / 1000.0))

        # Capacidad: 4 ventanas. Absorbe jitter del scheduler sin gastar memoria.
        self._ring = RingBuffer(self.window * 4, self.channels)
        self._stop.clear()

        self._proc = subprocess.Popen(
            [
                "parec",
                "--device", device,
                "--format", _PAREC_FORMAT,
                "--rate", str(self.sample_rate),
                "--channels", str(self.channels),
                "--raw",
                # Baja latencia de entrega para que los datos lleguen finos, no en
                # rafagas: es lo que mantiene el movimiento fluido (ver _LATENCY_MS).
                "--latency-msec", str(_LATENCY_MS),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        # Fallo inmediato (device invalido, permisos): que start() sea honesto y
        # lance, para que el motor active su fallback en vez de quedarse sin datos.
        if self._proc.poll() is not None:
            raise RuntimeError("parec termino de inmediato al arrancar")

        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        # parec entrega bloques CONTIGUOS por stdout; el RingBuffer los convierte
        # en la ventana deslizante que espera el visualizador (misma idea que el
        # callback de PortAudio en loopback, pero leyendo de una tuberia).
        assert self._proc is not None and self._proc.stdout is not None
        frame_bytes = self.channels * 4                 # float32 = 4 bytes/muestra
        block_frames = max(64, int(self.sample_rate * _READ_MS / 1000.0))
        chunk = block_frames * frame_bytes
        leftover = b""
        stdout = self._proc.stdout
        while not self._stop.is_set():
            data = stdout.read(chunk)
            if not data:                                # EOF: parec murio
                break
            leftover += data
            n = len(leftover) // frame_bytes            # solo frames completos
            if n == 0:
                continue
            usable = leftover[: n * frame_bytes]
            leftover = leftover[n * frame_bytes:]
            block = np.frombuffer(usable, dtype=np.float32).reshape(-1, self.channels)
            if self._ring is not None:
                self._ring.write(block)

    def read(self) -> Frame | None:
        if self._ring is None:
            return None
        audio = self._ring.read_last(self.window)
        if audio is None:                               # aun no hay una ventana llena
            return None
        return Frame(sample_rate=self.sample_rate, audio=audio)

    def stop(self) -> None:
        self._stop.set()
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        if self._reader is not None:
            self._reader.join(timeout=1.0)
            self._reader = None
