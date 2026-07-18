"""Extractor de metadata para Windows, basado en eventos de winrt.

En vez de sondear (o hablar con un servidor externo por WebSocket), este monitor
se suscribe a los canales de eventos de la sesion multimedia global de Windows
(`GlobalSystemMediaTransportControlsSessionManager`) y reacciona en tiempo real:

    * current_session_changed  -> otro reproductor tomo el control (o se fue)
    * media_properties_changed -> cambio titulo/artista/album/caratula
    * playback_info_changed    -> cambio play/pause/stop

Todo el trabajo asincrono de winrt corre en un loop de asyncio dentro de un hilo
propio, para no bloquear el frame de la GUI. Los handlers de eventos los invoca
winrt desde su propio thread-pool, asi que solo agendan trabajo en ese loop
(`run_coroutine_threadsafe` / `call_soon_threadsafe`); nunca tocan winrt desde
donde no deben.

Manejo de sesiones (clave para no filtrar memoria): existe UN solo manager
(como en reference.py) y a lo sumo UNA sesion enganchada a la vez. Al cambiar de
sesion se quitan los handlers de la vieja (via sus tokens) antes de enganchar la
nueva, y al parar se quita todo. Sin eso, cada cancion/reproductor dejaria
handlers colgados manteniendo vivas sesiones muertas.

Como la caratula es pesada, se deduplica por hash (igual que reference.py): solo
se publica cuando los bytes cambian de verdad, para no obligar a la GUI a
redecodificar la misma imagen. Ademas los refrescos de metadata se coalescen: si
llega otro evento mientras uno esta en vuelo, se agenda uno solo al terminar.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading

from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
)
from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions

from ..base import NO_ART, MediaInfo, _LatestBox

# Enum de winrt -> nombre estable que consume el visualizador. MediaInfo.is_playing
# compara contra "PLAYING", asi que ese nombre es parte del contrato.
_STATUS_NAMES = {
    PlaybackStatus.CLOSED: "CLOSED",
    PlaybackStatus.OPENED: "OPENED",
    PlaybackStatus.CHANGING: "CHANGING",
    PlaybackStatus.STOPPED: "STOPPED",
    PlaybackStatus.PLAYING: "PLAYING",
    PlaybackStatus.PAUSED: "PAUSED",
}


def _status_name(status) -> str:
    try:
        return _STATUS_NAMES.get(status, "")
    except Exception:
        return ""


class WinRTMediaMonitor:
    """Monitor de metadata/caratula por eventos de winrt. Cumple el contrato
    `MediaMonitor`: start/stop/read/read_thumbnail."""

    def __init__(self) -> None:
        self._info_box = _LatestBox()   # MediaInfo | None
        self._thumb_box = _LatestBox()  # bytes | NO_ART | None

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_future: asyncio.Future | None = None

        # --- estado que SOLO se toca desde el hilo del loop (sin locks) --------
        self._manager = None
        self._cs_token = None                # token de current_session_changed
        self._session = None
        self._session_tokens: tuple = ()     # (media_token, playback_token)
        # Ultimos campos conocidos: los guardamos para recomponer MediaInfo cuando
        # cambia solo el estado de reproduccion (sin re-pedir las props de texto).
        self._title = ""
        self._artist = ""
        self._album = ""
        self._status = ""
        self._thumb_hash: str | None = None  # hash de la ultima caratula publicada
        self._meta_busy = False              # hay un refresco de metadata en vuelo
        self._meta_pending = False           # llego otro evento mientras tanto

    # --- API publica (contrato MediaMonitor) ---------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._call_on_loop(self._shutdown)
        if self._thread:
            self._thread.join(timeout=2.0)

    def read(self) -> MediaInfo | None:
        return self._info_box.get()

    def read_thumbnail(self):
        return self._thumb_box.get()

    # --- el loop de asyncio vive en su propio hilo ---------------------------

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception:
            pass
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop.close()

    async def _serve(self) -> None:
        try:
            self._manager = await SessionManager.request_async()
        except Exception:
            return  # sin manager no hay nada que hacer; el visualizador corre sin barra
        try:
            self._cs_token = self._manager.add_current_session_changed(
                self._on_session_changed)
        except Exception:
            self._cs_token = None
        # Engancha la sesion actual (si la hay) y publica el estado inicial.
        self._bind_session(self._manager.get_current_session())
        # Se queda vivo hasta que stop() resuelva este future: mientras, los
        # eventos de winrt van agendando refrescos en este mismo loop.
        self._stop_future = self._loop.create_future()
        try:
            await self._stop_future
        except asyncio.CancelledError:
            pass

    # --- agendado thread-safe (handlers de winrt corren en otro hilo) --------

    def _call_on_loop(self, fn, *args) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(fn, *args)

    def _schedule_coro(self, coro) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(coro, loop)
        else:
            coro.close()  # evita el warning "coroutine was never awaited"

    # --- handlers de eventos (invocados por winrt desde su thread-pool) ------

    def _on_session_changed(self, sender, args) -> None:
        self._call_on_loop(self._rebind)

    def _on_media_changed(self, sender, args) -> None:
        self._schedule_coro(self._refresh_metadata())

    def _on_playback_changed(self, sender, args) -> None:
        self._call_on_loop(self._refresh_playback)

    # --- manejo de sesion (todo esto corre en el hilo del loop) --------------

    def _rebind(self) -> None:
        self._bind_session(self._manager.get_current_session())

    def _bind_session(self, session) -> None:
        """Engancha una nueva sesion (o ninguna). Siempre desengancha la anterior
        primero: es lo que evita filtrar handlers/sesiones."""
        self._unbind_session()
        self._session = session
        if session is None:
            # No hay nada reproduciendo: limpiamos para no dejar datos viejos.
            self._title = self._artist = self._album = ""
            self._status = ""
            self._publish()
            self._set_thumb(NO_ART)
            return
        try:
            mt = session.add_media_properties_changed(self._on_media_changed)
            pt = session.add_playback_info_changed(self._on_playback_changed)
            self._session_tokens = (mt, pt)
        except Exception:
            self._session_tokens = ()
        # Estado inicial de la sesion recien enganchada.
        self._refresh_playback()
        self._loop.create_task(self._refresh_metadata())

    def _unbind_session(self) -> None:
        session, tokens = self._session, self._session_tokens
        self._session = None
        self._session_tokens = ()
        if session is not None and tokens:
            mt, pt = tokens
            try:
                session.remove_media_properties_changed(mt)
            except Exception:
                pass
            try:
                session.remove_playback_info_changed(pt)
            except Exception:
                pass

    def _shutdown(self) -> None:
        """Quita el handler del manager, desengancha la sesion y despierta a
        _serve para que el loop termine. Corre en el hilo del loop."""
        if self._manager is not None and self._cs_token is not None:
            try:
                self._manager.remove_current_session_changed(self._cs_token)
            except Exception:
                pass
            self._cs_token = None
        self._unbind_session()
        if self._stop_future is not None and not self._stop_future.done():
            self._stop_future.set_result(None)

    # --- refrescos de datos --------------------------------------------------

    def _publish(self) -> None:
        self._info_box.put(MediaInfo(
            title=self._title,
            artist=self._artist,
            album_title=self._album,
            playback_status=self._status,
        ))

    def _refresh_playback(self) -> None:
        """Actualiza solo el estado de reproduccion (sincrono: get_playback_info
        no es async). Barato, asi que no hace falta coalescer."""
        session = self._session
        if session is None:
            return
        try:
            info = session.get_playback_info()
            self._status = _status_name(info.playback_status)
        except Exception:
            return
        self._publish()

    async def _refresh_metadata(self) -> None:
        """Re-pide titulo/artista/album y la caratula. Coalescido: si ya hay uno
        en vuelo, marca 'pendiente' y agenda uno solo al terminar, para no apilar
        pedidos cuando llegan varios eventos seguidos."""
        if self._meta_busy:
            self._meta_pending = True
            return
        self._meta_busy = True
        try:
            session = self._session
            if session is None:
                return
            try:
                props = await session.try_get_media_properties_async()
            except Exception:
                return  # frame malo: no pisamos lo que ya teniamos
            # Cada atributo puede venir vacio/None; nunca reventamos por su ausencia.
            self._title = props.title or ""
            self._artist = props.artist or ""
            self._album = props.album_title or ""
            self._publish()
            await self._update_thumbnail(props)
        finally:
            self._meta_busy = False
            if self._meta_pending:
                self._meta_pending = False
                self._loop.create_task(self._refresh_metadata())

    async def _update_thumbnail(self, props) -> None:
        ref = getattr(props, "thumbnail", None)
        if ref is None:
            self._set_thumb(NO_ART)   # la pista actual no tiene caratula
            return
        raw = await self._read_stream(ref)
        if raw:
            self._set_thumb(raw)
        # Si la lectura fallo (raw vacio), dejamos la caratula anterior intacta.

    async def _read_stream(self, ref) -> bytes | None:
        """Vuelca el stream de la caratula a bytes crudos. Mismo patron que
        reference.py, cerrando siempre reader y stream en finally."""
        stream = None
        reader = None
        try:
            stream = await ref.open_read_async()
            buffer = Buffer(stream.size)
            await stream.read_async(buffer, buffer.capacity, InputStreamOptions.NONE)
            reader = DataReader.from_buffer(buffer)
            data = bytearray(buffer.length)
            reader.read_bytes(data)
            return bytes(data)
        except Exception:
            return None
        finally:
            if reader is not None:
                reader.close()
            if stream is not None:
                stream.close()

    def _set_thumb(self, value) -> None:
        """Publica la caratula deduplicando por hash: solo cuando los bytes
        cambian de verdad, para no forzar a la GUI a redecodificar lo mismo."""
        if value is NO_ART:
            if self._thumb_hash is not None:
                self._thumb_hash = None
                self._thumb_box.put(NO_ART)
            return
        digest = hashlib.md5(value).hexdigest()
        if digest != self._thumb_hash:
            self._thumb_hash = digest
            self._thumb_box.put(value)
