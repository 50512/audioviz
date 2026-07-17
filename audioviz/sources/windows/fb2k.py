"""Fuente: foobar2000 via foo_uie_webview -> WebSocket.

Muestras REALES del visualisation stream de foobar, al sample rate REAL del
archivo. No pasa por el mixer de Windows, asi que la cadena WASAPI exclusiva /
ASIO sigue siendo bit-perfect: esto es un tap paralelo, no un desvio.

Protocolo (un mensaje WebSocket = un chunk completo; WebSocket preserva limites
de mensaje, por eso no hace falta framing por longitud):

    offset  tipo        campo
    0       uint32 LE   sample_rate
    4       uint16 LE   channel_count
    6       uint16 LE   reservado (alinea el payload a 4 -> Float32Array lo exige)
    8..     float32 LE  muestras interleaved
"""

from __future__ import annotations

import asyncio
import struct
import threading

import numpy as np
import websockets

from ..base import AudioSource, Frame, LatestSlot

HEADER = struct.Struct("<IHH")  # 8 bytes


def parse_chunk(payload: bytes) -> Frame:
    if len(payload) < HEADER.size:
        raise ValueError(f"mensaje demasiado corto: {len(payload)} bytes")

    sample_rate, channels, _reserved = HEADER.unpack_from(payload, 0)
    if channels == 0:
        raise ValueError("channel_count = 0")

    # frombuffer NO copia: reinterpreta los bytes recibidos.
    samples = np.frombuffer(payload, dtype="<f4", offset=HEADER.size)
    if samples.size % channels:
        raise ValueError(f"{samples.size} muestras no divisible entre {channels} canales")

    # reshape es gratis: numpy es row-major, y [f, c] cae en f*channels + c,
    # que es exactamente el layout interleaved.
    return Frame(sample_rate=sample_rate, audio=samples.reshape(-1, channels))


class Fb2kSource(AudioSource):
    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.host, self.port = host, port
        self._slot = LatestSlot()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._shutdown: asyncio.Event | None = None
        # Excepcion capturada del hilo de asyncio (p.ej. el bind del puerto falla
        # porque ya esta ocupado). Sin esto, el fallo moria en silencio en el hilo
        # y start() no tenia como enterarse. La lee start() para propagarlo.
        self._error: BaseException | None = None
        self.connected = False

    # --- API publica -------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # Esperamos a que el servidor levante. _serve setea _ready al bindear con
        # exito; si el bind falla, _run tambien setea _ready (con _error cargado)
        # para desbloquearnos al instante en vez de agotar el timeout completo.
        if not self._ready.wait(timeout=5.0):
            # Ni arranco ni fallo dentro del plazo: lo damos por muerto para que el
            # motor caiga al siguiente fallback en vez de adoptar una fuente inerte.
            self.stop()
            raise TimeoutError(
                f"fb2k: el servidor WebSocket no arranco en 5s ({self.host}:{self.port})")
        if self._error is not None:
            # El bind fallo (tipicamente el puerto ya esta ocupado). Limpiamos el
            # hilo/loop a medias y propagamos: set_source lo trata como arranque
            # fallido y prueba la siguiente fuente.
            self.stop()
            raise RuntimeError(
                f"fb2k: no se pudo abrir el WebSocket en {self.host}:{self.port}") from self._error

    def stop(self) -> None:
        # Apagado limpio: senalamos un Event DENTRO del loop y dejamos que _serve
        # salga por su cuenta. Llamar a loop.stop() a lo bruto aborta
        # run_until_complete a mitad de camino y revienta el context manager del
        # servidor -- se ve al cambiar de fuente en caliente.
        if self._loop and self._shutdown and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._shutdown.set)
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._loop and not self._loop.is_closed():
            self._loop.close()

    def read(self) -> Frame | None:
        return self._slot.get()

    # --- interno: asyncio vive en su propio hilo ---------------------------

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except BaseException as exc:
            # El arranque fallo (p.ej. el puerto esta ocupado). Lo guardamos y
            # desbloqueamos a start() ya mismo; sin esto la excepcion moria aca y
            # start() esperaba el timeout entero para volver con una fuente muerta.
            self._error = exc
            self._ready.set()
        finally:
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())

    async def _serve(self) -> None:
        # El Event hay que crearlo DENTRO del loop que lo va a usar.
        self._shutdown = asyncio.Event()
        async with websockets.serve(self._handle, self.host, self.port, max_size=None):
            self._ready.set()
            await self._shutdown.wait()   # sale limpio, cerrando el servidor

    async def _handle(self, ws) -> None:
        self.connected = True
        try:
            async for message in ws:
                if isinstance(message, str):
                    continue  # solo nos interesan los chunks binarios
                try:
                    self._slot.put(parse_chunk(message))
                except ValueError:
                    continue  # chunk corrupto: lo saltamos, no matamos la conexion
        except websockets.ConnectionClosed:
            pass
        finally:
            self.connected = False
