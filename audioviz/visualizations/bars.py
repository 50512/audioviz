"""Barras de espectro: una barra por banda, altura = magnitud. La visualizacion
original y la que va activa por defecto.

Stereo se dibuja como dos mitades enfrentadas (graves en los bordes, agudos
hacia el centro); mono/multicanal, como filas apiladas.
"""

from __future__ import annotations

import pygame

from ..engine import VizFrame
from .base import RenderContext, SliderSetting, Visualization


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


class BarsVisualization(Visualization):
    id = "bars"
    label = "barras"
    default_on = True

    def settings(self) -> list:
        return [
            SliderSetting("alto", "max_bar_height", 0, 100, step=1,
                          fmt=lambda v: f"{int(v)} %"),
        ]

    def draw(self, surf: pygame.Surface, frame: VizFrame, ctx: RenderContext) -> None:
        heights = frame.normalized()           # (channels, n_bands) en 0..1
        ch = frame.channels
        w, h = ctx.width, ctx.height
        colors = ctx.colors
        pad, top, gap = 16, 56 + ctx.header_h, 12
        max_plot_h = h * ctx.max_height_frac

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
            pygame.draw.line(surf, ctx.grid_color, (pad, bottom), (pad + left_w, bottom))
            draw_channel(surf, heights[0], (pad, bottom - bar_h, left_w, bar_h), colors[0])

            right_x = half + gap // 2
            right_w = w - pad - right_x
            pygame.draw.line(surf, ctx.grid_color, (right_x, bottom), (right_x + right_w, bottom))
            draw_channel(surf, heights[1], (right_x, bottom - bar_h, right_w, bar_h), colors[1],
                         reverse=True)
        else:
            plot_h = (h - top - pad - gap * (ch - 1)) / ch
            bar_h = min(plot_h, max_plot_h)
            for c in range(ch):
                y = top + c * (plot_h + gap)
                bottom = y + plot_h
                pygame.draw.line(surf, ctx.grid_color, (pad, bottom), (w - pad, bottom))
                draw_channel(surf, heights[c], (pad, bottom - bar_h, w - 2 * pad, bar_h),
                             colors[c % len(colors)])
