"""Tipos e infraestructura compartidos por los extractores de metadata.

Aca vive lo que no depende del SO: el contenedor de metadata (`MediaInfo`), el
sentinel de "sin caratula" (`NO_ART`), el buzon thread-safe de un solo hueco
(`_LatestBox`) y el contrato (`MediaMonitor`) que cada extractor de plataforma
implementa. El visualizador solo conoce esto; de donde salen los datos (winrt en
Windows, MPRIS en Linux el dia que exista) le es indiferente.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MediaInfo:
    """Metadata de la pista en reproduccion. Todos los campos son opcionales:
    un reproductor puede no publicar album, o estar sin sesion (todo vacio). El
    extractor rellena lo que exista y deja el resto como cadena vacia -- nunca
    revienta por un atributo ausente."""

    title: str = ""
    artist: str = ""
    album_title: str = ""
    playback_status: str = ""

    @property
    def is_playing(self) -> bool:
        """True solo si el reproductor esta reproduciendo activamente. Lo usa el
        visualizador para decidir si el vinilo gira."""
        return self.playback_status == "PLAYING"


class _NoArt:
    """Sentinel: el extractor confirmo que la pista actual no tiene caratula.
    Distinto de None, que significa "todavia no llego nada" -- con None el
    consumidor no debe tocar lo que ya tenia dibujado; con NO_ART si debe
    borrarlo."""

    def __repr__(self) -> str:
        return "NO_ART"


NO_ART = _NoArt()


class _LatestBox:
    """Buzon de un solo hueco, thread-safe. El extractor (en su propio hilo)
    deja el ultimo valor y el consumidor (hilo de la GUI) lo lee. Nunca se
    auto-limpia: el valor queda hasta que llegue uno nuevo."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = None

    def put(self, value) -> None:
        with self._lock:
            self._value = value

    def get(self):
        with self._lock:
            return self._value


class MediaMonitor(Protocol):
    """Contrato que cumple cada extractor de plataforma. El visualizador maneja
    el ciclo de vida (start/stop) y lee sin bloquearse por frame."""

    def start(self) -> None:
        """Arranca la captura en segundo plano (hilo propio)."""

    def stop(self) -> None:
        """Detiene la captura y libera recursos (sesiones, handlers de eventos)."""

    def read(self) -> MediaInfo | None:
        """Ultima metadata conocida, o None si aun no llego ninguna."""

    def read_thumbnail(self):
        """Bytes crudos de la ultima caratula, NO_ART si la pista no tiene, o
        None si aun no llego nada. Decodificar a Surface es cosa del consumidor."""
