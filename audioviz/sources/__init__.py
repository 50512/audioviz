from .base import AudioSource, Frame, LatestSlot, RingBuffer

__all__ = ["AudioSource", "Frame", "LatestSlot", "RingBuffer",
           "Fb2kSource", "LoopbackSource", "MicSource", "ToneSource"]


def __getattr__(name):
    # Import perezoso: loopback y mic dependen de pyaudiowpatch (solo Windows)
    # y fb2k de websockets. Importar el paquete no debe exigir ambas.
    if name == "Fb2kSource":
        from .fb2k import Fb2kSource
        return Fb2kSource
    if name == "LoopbackSource":
        from .loopback import LoopbackSource
        return LoopbackSource
    if name == "MicSource":
        from .mic import MicSource
        return MicSource
    if name == "ToneSource":
        from .synthetic import ToneSource
        return ToneSource
    raise AttributeError(name)
