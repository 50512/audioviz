"""Fuente: loopback de WASAPI (captura lo que suena en el sistema).

    ADVERTENCIAS -- leelas antes de usar esto:

    1. El loopback SOLO existe en modo COMPARTIDO. Si foobar tiene el
       dispositivo en WASAPI exclusivo o ASIO, el audio engine de Windows
       esta fuera del camino y NO HAY NADA QUE CAPTURAR. Esta fuente y el
       bit-perfect son mutuamente excluyentes.

    2. El sample_rate que reporta es el del ENDPOINT (el "formato
       predeterminado" de Windows, tipicamente 48000 Hz), NO el del archivo.
       Un FLAC de 44.1k aparecera aca como 48k, ya resampleado por el mixer.

    3. Captura TODO el sistema: foobar, el navegador, Discord, los sonidos
       de Windows. No solo tu musica.

    Sirve para: visualizar cualquier cosa que suene, sin tocar foobar.
    No sirve para: analizar la senal real que llega a tu DAC.

Requiere: pip install pyaudiowpatch   (solo Windows)
"""

from __future__ import annotations

import numpy as np

from ..base import AudioSource, Frame, RingBuffer


class LoopbackSource(AudioSource):
    def __init__(self, window_ms: float = 100.0, device_name: str | None = None) -> None:
        # En MILISEGUNDOS, no en frames. Asi coincide con foo_uie_webview, que usa
        # 100 ms por defecto. Si las dos fuentes usaran ventanas de distinta
        # DURACION, el suavizado implicito (por solape) seria distinto y tus
        # ajustes de attack/decay no transferirian entre fuentes.
        self.window_ms = window_ms
        self.window = 0          # frames; se deriva del sample rate real en start()
        self.device_name = device_name
        self._pa = None
        self._stream = None
        self._ring: RingBuffer | None = None
        self.sample_rate = 0
        self.channels = 0

    def start(self) -> None:
        import pyaudiowpatch as pyaudio  # import tardio: solo existe en Windows

        self._pa = pyaudio.PyAudio()

        # El dispositivo de loopback es el "espejo" del endpoint de salida.
        if self.device_name:
            device = next(
                d for d in self._pa.get_loopback_device_info_generator()
                if self.device_name.lower() in d["name"].lower()
            )
        else:
            speakers = self._pa.get_default_wasapi_loopback()
            device = speakers

        self.sample_rate = int(device["defaultSampleRate"])
        self.channels = int(device["maxInputChannels"])

        # La ventana en frames se deriva del rate REAL del endpoint. A 48 kHz,
        # 100 ms = 4800 frames; a 44.1 kHz, 4410. La DURACION es lo invariante,
        # igual que en foobar -> Δf sale 1/0.1s = 10 Hz en ambos casos.
        self.window = max(64, int(self.sample_rate * self.window_ms / 1000.0))

        # Capacidad: 4 ventanas. Absorbe jitter del scheduler sin gastar memoria.
        self._ring = RingBuffer(self.window * 4, self.channels)

        self._stream = self._pa.open(
            format=pyaudio.paFloat32,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=device["index"],
            frames_per_buffer=self.window // 4,
            stream_callback=self._on_audio,
        )
        self._stream.start_stream()

    def _on_audio(self, in_data, frame_count, time_info, status):
        import pyaudiowpatch as pyaudio

        block = np.frombuffer(in_data, dtype=np.float32).reshape(-1, self.channels)
        self._ring.write(block)
        return (None, pyaudio.paContinue)

    def read(self) -> Frame | None:
        if self._ring is None:
            return None
        # El ring convierte bloques contiguos en la ventana deslizante que el
        # visualizador espera -- misma semantica que entrega foobar.
        audio = self._ring.read_last(self.window)
        if audio is None:
            return None
        return Frame(sample_rate=self.sample_rate, audio=audio)

    def stop(self) -> None:
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
        if self._pa:
            self._pa.terminate()
