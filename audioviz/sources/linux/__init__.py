"""Fuentes exclusivas de Linux.

La captura del sistema aca va por PipeWire/PulseAudio (el monitor del sink de
salida), no por WASAPI: ver pipewire.PipeWireSource. Se apoya en el binario
`parec` (pipewire-pulse / pulseaudio) leyendo PCM crudo por una tuberia, asi que
no arrastra ninguna dependencia de Python -- por eso el registro de sources la
importa de forma perezosa igual que las de Windows.
"""
