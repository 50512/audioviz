"""GUI de referencia (pygame). Se engancha al Engine y no sabe NADA de sockets,
WASAPI, FFT ni ventanas de Hann. Solo pide frames y dibuja.

    python -m audioviz.gui_pygame --source fb2k
    python -m audioviz.gui_pygame --source loopback --attack-ms 20 --decay-ms 30

Teclas:
    1 / 2 / 3   fuente: fb2k / loopback / tone   (hot-swap, sin reiniciar)
    Q / A       attack  -/+
    W / S       decay   -/+
    ESC         salir

Requiere: pip install pygame-ce
"""

from __future__ import annotations

import argparse

import numpy as np
import pygame

from .engine import Engine

BG = (14, 14, 18)
GRID = (34, 34, 42)
CH_COLORS = [(90, 200, 250), (250, 130, 110), (150, 230, 140), (240, 200, 90)]
TEXT = (150, 150, 165)


def draw_channel(surf, band_h, rect, color, reverse=False):
    """band_h: (n_bands,) en 0..1. rect: (x, y, w, h)."""
    x, y, w, h = rect
    n = len(band_h)
    bw = w / n
    bands = band_h[::-1] if reverse else band_h
    for i, v in enumerate(bands):
        bh = max(1, int(v * h))
        bx = int(x + i * bw)
        pygame.draw.rect(surf, color, (bx + 1, y + h - bh, int(bw) - 1, bh))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="fb2k", choices=["fb2k", "loopback", "tone"])
    ap.add_argument("--fps", type=float, default=60.0)
    ap.add_argument("--attack-ms", type=float, default=20.0)
    ap.add_argument("--decay-ms", type=float, default=250.0)
    ap.add_argument("--bands", type=int, default=128, help="solo para --dist log")
    ap.add_argument("--dist", default="log", choices=["log", "octaves"])
    ap.add_argument("--note-lo", default="C0")
    ap.add_argument("--note-hi", default="F#10")
    ap.add_argument("--bpo", type=int, default=12, help="bandas por octava")
    ap.add_argument("--tuning", type=float, default=440.0)
    ap.add_argument("--seconds", type=float, default=0.0, help="0 = infinito")
    ap.add_argument("--max-bar-height", type=float, default=100.0,
                     help="altura maxima de las barras, como %% del alto de la pantalla (0-100)")
    args = ap.parse_args()

    if not 0.0 <= args.max_bar_height <= 100.0:
        ap.error("--max-bar-height debe estar entre 0 y 100")

    pygame.init()
    screen = pygame.display.set_mode((1000, 560), pygame.RESIZABLE)
    pygame.display.set_caption("audioviz")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 14)

    engine = Engine(source=args.source, fps=args.fps, attack_ms=args.attack_ms,
                    decay_ms=args.decay_ms, n_bands=args.bands,
                    distribution=args.dist, note_lo=args.note_lo,
                    note_hi=args.note_hi, bands_per_octave=args.bpo,
                    tuning=args.tuning)

    keymap = {pygame.K_1: "fb2k", pygame.K_2: "loopback", pygame.K_3: "tone"}
    running = True
    elapsed = 0.0

    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key in keymap:
                    try:
                        engine.set_source(keymap[ev.key])   # hot-swap
                    except Exception as exc:
                        print(f"no se pudo cambiar de fuente: {exc}")
                # Parametros vivos: escribir la propiedad reconfigura el motor.
                elif ev.key == pygame.K_q:
                    engine.attack_ms = max(1.0, engine.attack_ms - 5)
                elif ev.key == pygame.K_a:
                    engine.attack_ms = engine.attack_ms + 5
                elif ev.key == pygame.K_w:
                    engine.decay_ms = max(0.0, engine.decay_ms - 5)
                elif ev.key == pygame.K_s:
                    engine.decay_ms = engine.decay_ms + 5

        frame = engine.poll()          # <- lo unico que la GUI le pide al motor
        screen.fill(BG)
        w, h = screen.get_size()

        if frame is not None:
            heights = frame.normalized()           # (channels, n_bands) en 0..1
            ch = frame.channels
            pad, top, gap = 16, 56, 12
            max_plot_h = h * args.max_bar_height / 100.0

            if ch == 2:
                # Stereo: canales lado a lado.
                # Canal izquierdo: graves en borde izquierdo, agudos hacia el centro.
                # Canal derecho: agudos hacia el centro, graves en borde derecho (invertido).
                plot_h = h - top - pad
                bar_h = min(plot_h, max_plot_h)
                half = w // 2
                y = top
                bottom = y + plot_h

                left_w = half - pad - gap // 2
                pygame.draw.line(screen, GRID, (pad, bottom), (pad + left_w, bottom))
                draw_channel(screen, heights[0], (pad, bottom - bar_h, left_w, bar_h), CH_COLORS[0])

                right_x = half + gap // 2
                right_w = w - pad - right_x
                pygame.draw.line(screen, GRID, (right_x, bottom), (right_x + right_w, bottom))
                draw_channel(screen, heights[1], (right_x, bottom - bar_h, right_w, bar_h), CH_COLORS[1],
                             reverse=True)
            else:
                plot_h = (h - top - pad - gap * (ch - 1)) / ch
                bar_h = min(plot_h, max_plot_h)
                for c in range(ch):
                    y = top + c * (plot_h + gap)
                    bottom = y + plot_h
                    pygame.draw.line(screen, GRID, (pad, bottom), (w - pad, bottom))
                    draw_channel(screen, heights[c], (pad, bottom - bar_h, w - 2 * pad, bar_h),
                                 CH_COLORS[c % len(CH_COLORS)])

            hud = (f"{engine.source_name}  |  {frame.sample_rate} Hz  {ch}ch  "
                   f"|  {engine.distribution} {engine.n_bands}b  "
                   f"|  attack {engine.attack_ms:.0f}ms  decay {engine.decay_ms:.0f}ms  "
                   f"|  {clock.get_fps():.0f} fps")
        else:
            hud = f"{engine.source_name}  |  esperando audio…"

        screen.blit(font.render(hud, True, TEXT), (16, 16))
        screen.blit(font.render("1/2/3 fuente   Q/A attack   W/S decay   ESC salir",
                                True, GRID), (16, 34))
        pygame.display.flip()

        dt = clock.tick(args.fps) / 1000.0
        elapsed += dt

        # CRITICO: alpha se deriva de tau Y del framerate. Si la GUI no alcanza
        # los fps nominales, el fps real miente y las ballistics se desvian.
        # Realimentamos el fps MEDIDO para que tau siga siendo el que pediste.
        real = clock.get_fps()
        if real > 1.0 and abs(real - engine.fps) > 2.0:
            engine.fps = real

        if args.seconds and elapsed > args.seconds:
            running = False

    engine.close()
    pygame.quit()


if __name__ == "__main__":
    main()
