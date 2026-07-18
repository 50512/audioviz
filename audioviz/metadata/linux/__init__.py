"""Extractor de metadata para Linux. Aun vacio.

El dia que exista, aca vivira un monitor basado en MPRIS (D-Bus: la interfaz
org.mpris.MediaPlayer2) que implemente el contrato `MediaMonitor` de
audioviz.metadata.base y se enganche en `create_media_monitor()` del paquete
padre. Por ahora, en Linux el visualizador corre sin barra de now-playing.
"""
