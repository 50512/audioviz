"""Monitor de metadata: cliente WebSocket de "now playing".

Escucha `ws://host:port/ws/media-info`, que solo emite un mensaje cuando la
metadata cambia (JSON: status, is_playing, title, artist). Guardamos el
ultimo mensaje en memoria y lo dejamos ahi hasta que llegue uno nuevo -- no
hay heartbeat que limpiar.

Corre en su propio hilo con su propio loop de asyncio, igual que Fb2kSource
en sources/fb2k.py, para no bloquear el frame de la GUI. Si el servicio no
esta arriba o se cae a mitad de cancion, reintentamos en el fondo con un
backoff fijo; la GUI simplemente sigue mostrando la ultima metadata conocida
(o nada, si nunca hubo conexion).
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass

import websockets


@dataclass(frozen=True)
class MediaInfo:
    status: str
    is_playing: bool
    title: str
    artist: str


class _LatestBox:
    """Buzon de un solo hueco, thread-safe. Nunca se auto-limpia."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: MediaInfo | None = None

    def put(self, value: MediaInfo) -> None:
        with self._lock:
            self._value = value

    def get(self) -> MediaInfo | None:
        with self._lock:
            return self._value


class MetadataMonitor:
    """Cliente de /ws/media-info. Reconecta solo mientras este activo."""

    RETRY_SECONDS = 3.0

    def __init__(self, url: str) -> None:
        self.url = url
        self._box = _LatestBox()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task | None = None
        self.connected = False

    # --- API publica ---------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._loop and self._task and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._task.cancel)
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._loop and not self._loop.is_closed():
            self._loop.close()

    def read(self) -> MediaInfo | None:
        """Ultima metadata conocida, o None si nunca llego ninguna."""
        return self._box.get()

    # --- interno: asyncio vive en su propio hilo -----------------------------

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._task = self._loop.create_task(self._serve())
        try:
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass
        finally:
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())

    async def _serve(self) -> None:
        # Bucle de reconexion: el servicio puede no existir todavia, o caerse
        # a mitad de cancion. Cualquiera de los dos es normal, no fatal.
        while True:
            try:
                async with websockets.connect(self.url, open_timeout=5.0) as ws:
                    self.connected = True
                    async for message in ws:
                        self._handle(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # sin servicio, host caido, handshake roto... reintentamos
            finally:
                self.connected = False
            await asyncio.sleep(self.RETRY_SECONDS)

    def _handle(self, message: str | bytes) -> None:
        if not isinstance(message, str):
            return
        try:
            data = json.loads(message)
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        self._box.put(MediaInfo(
            status=str(data.get("status", "")),
            is_playing=bool(data.get("is_playing", False)),
            title=str(data.get("title", "")),
            artist=str(data.get("artist", "")),
        ))
