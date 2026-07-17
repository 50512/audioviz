"""Fuentes exclusivas de Windows.

Ligadas a Windows: la captura del sistema (loopback/mic) usa WASAPI via
pyaudiowpatch, y fb2k se conecta a foobar2000 (una app de Windows) por WebSocket.
El registro de fuentes (ver sources/__init__.py) las oculta fuera de Windows, y
su import es perezoso para que esas dependencias no sean exigibles en otros SO.
"""
