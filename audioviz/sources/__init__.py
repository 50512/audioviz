"""Registro de fuentes de audio, consciente de la plataforma.

Cada fuente se etiqueta con el SO en el que puede correr:

    generic  -> cualquier SO (solo numpy)
    windows  -> solo Windows (WASAPI via pyaudiowpatch, o fb2k via WebSocket)
    linux    -> solo Linux (aun no hay ninguna)

De aca sale la UNICA fuente de verdad de "que fuentes existen y cuales aplican
en este SO". El motor, el visualizador y la config la consumen en vez de repetir
la lista: asi en Windows solo se ven Windows+Generic y en Linux solo
Linux+Generic. El import de cada fuente es perezoso, porque las de un SO
dependen de librerias que solo existen ahi (y fb2k arrastra websockets):
importar el paquete no debe exigir ninguna de esas dependencias.
"""

from __future__ import annotations

import sys

from .base import AudioSource, Frame, LatestSlot, RingBuffer

__all__ = ["AudioSource", "Frame", "LatestSlot", "RingBuffer",
           "Fb2kSource", "LoopbackSource", "MicSource", "ToneSource",
           "SOURCE_PLATFORMS", "current_platform", "is_available",
           "available_sources"]

# Fuente -> plataforma. El ORDEN es el canonico (orden de fallback y de la UI):
# el primero es el preferido y 'tone' (generic) queda ultimo como red definitiva,
# siempre disponible en cualquier SO.
SOURCE_PLATFORMS: dict[str, str] = {
    "loopback": "windows",   # loopback WASAPI: captura la salida del sistema
    "fb2k": "windows",       # foobar via WebSocket: foobar2000 es de Windows
    "mic": "windows",        # entrada WASAPI via pyaudiowpatch
    "tone": "generic",       # tono sintetico: solo numpy
}


def current_platform() -> str:
    """El SO actual normalizado: 'windows', 'linux' u otro (p.ej. 'darwin').
    Las fuentes 'generic' corren en todos; las especificas solo donde coinciden."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def is_available(name: str) -> bool:
    """True si la fuente puede correr en este SO: es generic, o su plataforma
    coincide con la actual. Una fuente desconocida no esta disponible."""
    plat = SOURCE_PLATFORMS.get(name)
    return plat is not None and (plat == "generic" or plat == current_platform())


def available_sources() -> list[str]:
    """Nombres de las fuentes compatibles con este SO, en orden canonico."""
    return [name for name in SOURCE_PLATFORMS if is_available(name)]


def __getattr__(name):
    # Import perezoso: cada fuente vive en su subpaquete (windows/ linux/ generic/)
    # y solo se importa cuando de verdad se usa, para no arrastrar dependencias de
    # otro SO ni websockets al importar el paquete.
    if name == "Fb2kSource":
        from .windows.fb2k import Fb2kSource
        return Fb2kSource
    if name == "LoopbackSource":
        from .windows.loopback import LoopbackSource
        return LoopbackSource
    if name == "MicSource":
        from .windows.mic import MicSource
        return MicSource
    if name == "ToneSource":
        from .generic.synthetic import ToneSource
        return ToneSource
    raise AttributeError(name)
