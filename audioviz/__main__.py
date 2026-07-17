"""Render loop agnostico del origen.

    python -m audioviz --source loopback
    python -m audioviz --source fb2k
    python -m audioviz --source mic       (microfono del dispositivo)
    python -m audioviz --source tone      (sin foobar, para desarrollar)

El bucle de abajo NO sabe de que fuente viene el audio. Ese es el punto.
"""

from __future__ import annotations

import argparse
import time

from .analysis import Smoother, bands, spectra, to_glyphs
from .sources import AudioSource, available_sources


def build_source(name: str) -> AudioSource:
    if name == "fb2k":
        from .sources import Fb2kSource
        return Fb2kSource()
    if name == "loopback":
        from .sources import LoopbackSource
        return LoopbackSource()
    if name == "mic":
        from .sources import MicSource
        return MicSource()
    if name == "tone":
        from .sources import ToneSource
        return ToneSource()
    raise SystemExit(f"fuente desconocida: {name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    # Solo las fuentes compatibles con este SO (Windows+Generic o Linux+Generic).
    ap.add_argument("--source", default="loopback", choices=available_sources())
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--attack-ms", type=float, default=20.0,
                    help="subida: bajo = transientes veraces")
    ap.add_argument("--decay-ms", type=float, default=300.0,
                    help="bajada: alto = menos parpadeo, mas persistencia")
    ap.add_argument("--seconds", type=float, default=0.0, help="0 = infinito")
    args = ap.parse_args()

    smooth = Smoother(fps=args.fps, attack_ms=args.attack_ms,
                      decay_ms=args.decay_ms)
    period = 1.0 / args.fps
    t0 = time.monotonic()

    with build_source(args.source) as source:
        while True:
            if args.seconds and time.monotonic() - t0 > args.seconds:
                break

            # ---- todo lo de abajo es identico para cualquier fuente ----
            frame = source.read()          # latest-wins: nunca bloquea, nunca encola
            if frame is None:
                time.sleep(period)
                continue

            peaks_db, mags, freqs = spectra(frame)   # FFT en el hilo de render,
            db = smooth(bands(mags, freqs))          # no en el del socket

            cells = [f"{'LR'[c] if c < 2 else c}[{peaks_db[c]:5.1f}] {to_glyphs(db[c])}"
                     for c in range(frame.channels)]
            print(f"{frame.sample_rate // 1000:3d}k  " + "  ".join(cells),
                  end="\r", flush=True)

            time.sleep(period)
    print()


if __name__ == "__main__":
    main()
