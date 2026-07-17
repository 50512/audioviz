"""Fuentes exclusivas de Windows.

Dependen de APIs propias del SO (WASAPI via pyaudiowpatch). El registro de
fuentes (ver sources/__init__.py) las oculta fuera de Windows, y su import es
perezoso para que la libreria de captura no sea exigible en otros SO.
"""
