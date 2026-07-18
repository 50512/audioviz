"""Extraccion de metadata y caratula de la sesion multimedia del sistema.

Este subpaquete aisla TODA la logica de "que suena ahora" fuera del
visualizador. Dividido por SO, igual que sources/:

    windows/ -> extractor basado en eventos de winrt (GlobalSystemMedia...)
    linux/   -> aun vacio (MPRIS/D-Bus el dia que exista)

El visualizador no importa ninguna implementacion directamente: pide un monitor
con `create_media_monitor()` y consume el contrato `MediaMonitor` (start/stop/
read/read_thumbnail). Asi cambiar de backend o agregar Linux no toca la GUI.
"""

from __future__ import annotations

import sys

from .base import NO_ART, MediaInfo, MediaMonitor

__all__ = ["NO_ART", "MediaInfo", "MediaMonitor", "create_media_monitor"]


def create_media_monitor() -> MediaMonitor | None:
    """Monitor de metadata apropiado para este SO, o None si no hay backend.

    El import es perezoso: cada extractor arrastra librerias que solo existen en
    su SO (winrt en Windows), asi que importar el paquete no debe exigirlas."""
    if sys.platform.startswith("win"):
        from .windows.winrt_monitor import WinRTMediaMonitor
        return WinRTMediaMonitor()
    # Linux (y cualquier otro SO) todavia no tiene extractor: el visualizador
    # simplemente corre sin barra de now-playing.
    return None
