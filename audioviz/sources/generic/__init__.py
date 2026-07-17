"""Fuentes independientes del SO.

Corren igual en Windows y en Linux: solo dependen de numpy (tono sintetico) o de
websockets (foobar via foo_uie_webview, que se conecta a un servidor WebSocket
que no tiene nada de especifico de plataforma). El registro (ver
sources/__init__.py) las ofrece en todos los entornos.
"""
