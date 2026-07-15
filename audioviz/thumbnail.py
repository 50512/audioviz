"""Monitor de caratula: cliente WebSocket de "album art".

Escucha `ws://host:port/ws/thumbnail`, que emite un mensaje cada vez que
cambia la caratula en reproduccion (JSON: type, data -- un data URI
"data:image/jpeg;base64,..."). Este hilo solo decodifica el base64 a bytes
crudos y los deja en memoria; construir el pygame.Surface (que toca el
contexto de video) le corresponde a quien llama a read(), en el hilo
principal.

Mismo patron que metadata.py: hilo propio con su loop de asyncio, reconexion
con backoff fijo si el servicio no esta arriba o se cae, y el visualizador
sigue funcionando sin caratula si el socket nunca conecta.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading

import websockets


class _NoArt:
    """Sentinel: el servicio confirmo que la pista actual no tiene caratula
    (mensaje con "data": null). Distinto de None, que significa "todavia no
    llego ningun mensaje" -- con None el consumidor no debe tocar lo que ya
    tenia dibujado; con NO_ART si debe borrarlo."""

    def __repr__(self) -> str:
        return "NO_ART"


NO_ART = _NoArt()


class _LatestBox:
    """Buzon de un solo hueco, thread-safe. Nunca se auto-limpia."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: bytes | _NoArt | None = None

    def put(self, value: bytes | _NoArt) -> None:
        with self._lock:
            self._value = value

    def get(self) -> bytes | _NoArt | None:
        with self._lock:
            return self._value


class ThumbnailMonitor:
    """Cliente de /ws/thumbnail. Reconecta solo mientras este activo."""

    RETRY_SECONDS = 3.0
    MAX_MESSAGE_BYTES = 16 * 1024 * 1024  # caratulas en base64 superan el 1MB por defecto de websockets

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

    def read(self) -> bytes | _NoArt | None:
        """Bytes crudos de la ultima caratula conocida, NO_ART si el servicio
        confirmo que la pista actual no tiene, o None si aun no llego nada."""
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
                async with websockets.connect(self.url, open_timeout=5.0,
                                               max_size=self.MAX_MESSAGE_BYTES) as ws:
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
            payload = json.loads(message)
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        data_uri = payload.get("data")
        if data_uri is None:
            self._box.put(NO_ART)  # la pista actual no tiene caratula
            return
        if not isinstance(data_uri, str):
            return
        _, _, b64 = data_uri.partition(",")  # descarta "data:image/jpeg;base64,"
        if not b64:
            return
        try:
            raw = base64.b64decode(b64, validate=False)
        except ValueError:  # incluye binascii.Error, que hereda de ValueError
            return
        if raw:
            self._box.put(raw)
