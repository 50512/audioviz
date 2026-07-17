"""Fuente: microfono del dispositivo (captura de un input real, no loopback).

    A diferencia de LoopbackSource (que espia la SALIDA del sistema), esta
    fuente abre un dispositivo de ENTRADA normal -- el microfono por defecto,
    o uno elegido por nombre. Util para visualizar voz, instrumentos en vivo,
    o cualquier cosa que entre por line-in/microfono en vez de sonar por los
    parlantes.

    El sample_rate reportado es el del dispositivo (su "formato por defecto"),
    igual que en LoopbackSource: Windows puede resamplear antes de entregarte
    el stream.

Requiere: pip install pyaudiowpatch   (funciona igual con PyAudio normal, pero
reusamos pyaudiowpatch porque ya es dependencia de LoopbackSource y evita
tener dos bindings de PortAudio distintos en el proyecto).
"""

from __future__ import annotations

import numpy as np

from ..base import AudioSource, Frame, RingBuffer


class MicSource(AudioSource):
    def __init__(self, window_ms: float = 100.0, device_name: str | None = None) -> None:
        # Misma logica de ventana que LoopbackSource: duracion fija, no frames
        # fijos, para que attack/decay se comporten igual entre fuentes.
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

        if self.device_name:
            device = next(
                self._pa.get_device_info_by_index(i)
                for i in range(self._pa.get_device_count())
                if self.device_name.lower() in self._pa.get_device_info_by_index(i)["name"].lower()
                and self._pa.get_device_info_by_index(i)["maxInputChannels"] > 0
            )
        else:
            device = self._pa.get_default_input_device_info()

        self.sample_rate = int(device["defaultSampleRate"])
        self.channels = int(device["maxInputChannels"]) or 1

        # Ver comentario equivalente en LoopbackSource: la DURACION es lo
        # invariante, no el numero de frames.
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
