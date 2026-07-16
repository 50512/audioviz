"""Barras de espectro: una barra por banda, altura = magnitud. La visualizacion
original y la que va activa por defecto.

Stereo se dibuja como dos mitades enfrentadas (graves en los bordes, agudos
hacia el centro); mono/multicanal, como filas apiladas.

Color: por defecto solido (izquierda celeste, derecha rojo). Se le puede activar
un degradado (mismos modos que el circulo, ver gradient.py) con dos alcances:

    por canal   dentro de cada canal, del grave (celeste) al agudo (rojo).
    extremos    un unico degradado que barre TODO el ancho: del grave de L
                (celeste, borde izq) al grave de R (rojo, borde der).
"""

from __future__ import annotations

import pygame

from ..engine import VizFrame
from .base import RenderContext, SliderSetting, StepperSetting, Visualization
from .gradient import GRADIENT_LABELS, GRADIENT_MODES, build_gradient

# Modo de color: "solid" (colores por canal) + los degradados de gradient.py.
BARS_GRADIENT_MODES = ["solid"] + GRADIENT_MODES
BARS_GRADIENT_LABELS = ["solido"] + GRADIENT_LABELS
DEFAULT_BARS_GRADIENT = "solid"

# Alcance del degradado (donde se aplica).
BARS_SCOPES = ["channel", "span"]
BARS_SCOPE_LABELS = ["por canal", "extremos"]
DEFAULT_BARS_SCOPE = "channel"


def draw_channel(surf, band_h, rect, color, reverse=False):
    """band_h: (n_bands,) en 0..1. rect: (x, y, w, h).
    color: un RGB solido, o una secuencia de n colores indexada por banda.
    reverse invierte el orden de dibujo (agudos primero); el color, al indexarse
    por banda, sigue pegado a su banda pase lo que pase."""
    x, y, w, h = rect
    n = len(band_h)
    bw = w / n
    solid = isinstance(color, tuple)
    order = range(n - 1, -1, -1) if reverse else range(n)
    for pos, bi in enumerate(order):
        bh = max(1, int(band_h[bi] * h))
        bx = int(x + pos * bw)
        pygame.draw.rect(surf, color if solid else color[bi],
                         (bx + 1, y + h - bh, int(bw) - 1, bh))


class BarsVisualization(Visualization):
    id = "bars"
    label = "barras"
    default_on = True

    def __init__(self) -> None:
        # Cache de las paletas por banda: recalcular n colores (o 2n) cada cuadro
        # seria un derroche. La clave incluye n porque puede cambiar en caliente.
        self._cache = None
        self._cache_key = None

    def settings(self) -> list:
        return [
            SliderSetting("alto", "max_bar_height", 0, 100, step=1,
                          fmt=lambda v: f"{int(v)} %"),
            StepperSetting("degradado", "bars_gradient_mode",
                           BARS_GRADIENT_MODES, BARS_GRADIENT_LABELS),
            StepperSetting("aplicar", "bars_gradient_scope",
                           BARS_SCOPES, BARS_SCOPE_LABELS),
        ]

    def _bar_colors(self, n, c0, c1, mode, scope):
        """(left, right, row) de colores por banda para el modo/alcance dados.
        - "por canal": las tres son el mismo degradado grave->agudo.
        - "extremos": un degradado de 2n barre L (mitad baja) y R (mitad alta);
          right queda invertido porque el canal derecho se dibuja invertido, asi
          el grave de R (rojo) cae en el borde derecho."""
        key = (n, tuple(c0), tuple(c1), mode, scope)
        if key == self._cache_key:
            return self._cache
        row = build_gradient(n, c0, c1, mode)
        if scope == "span":
            full = build_gradient(2 * n, c0, c1, mode)
            left, right = full[:n], list(reversed(full[n:]))
        else:
            left = right = row
        self._cache = (left, right, row)
        self._cache_key = key
        return self._cache

    def draw(self, surf: pygame.Surface, frame: VizFrame, ctx: RenderContext) -> None:
        heights = frame.normalized()           # (channels, n_bands) en 0..1
        ch = frame.channels
        n = heights.shape[1]
        w, h = ctx.width, ctx.height
        pad, top, gap = 16, 56 + ctx.header_h, 12
        max_plot_h = h * ctx.max_height_frac

        # Color por canal: solido (RGB) o paleta por banda (secuencia), segun modo.
        solid = ctx.bars_gradient_mode == "solid"
        if solid:
            left_col, right_col = ctx.colors[0], ctx.colors[1]
        else:
            left_col, right_col, row_col = self._bar_colors(
                n, ctx.colors[0], ctx.colors[1],
                ctx.bars_gradient_mode, ctx.bars_gradient_scope)

        if ch == 2:
            # Stereo: canales lado a lado.
            # Canal izquierdo: graves en borde izquierdo, agudos hacia el centro.
            # Canal derecho: agudos hacia el centro, graves en borde derecho (invertido).
            plot_h = h - top - pad
            bar_h = min(plot_h, max_plot_h)
            half = w // 2
            bottom = top + plot_h

            left_w = half - pad - gap // 2
            pygame.draw.line(surf, ctx.grid_color, (pad, bottom), (pad + left_w, bottom))
            draw_channel(surf, heights[0], (pad, bottom - bar_h, left_w, bar_h), left_col)

            right_x = half + gap // 2
            right_w = w - pad - right_x
            pygame.draw.line(surf, ctx.grid_color, (right_x, bottom), (right_x + right_w, bottom))
            draw_channel(surf, heights[1], (right_x, bottom - bar_h, right_w, bar_h), right_col,
                         reverse=True)
        else:
            # Mono/multicanal: filas apiladas. El alcance "extremos" no tiene
            # sentido aca (no hay dos mitades enfrentadas), asi que cada fila usa
            # el degradado grave->agudo (row_col) o su color solido de canal.
            plot_h = (h - top - pad - gap * (ch - 1)) / ch
            bar_h = min(plot_h, max_plot_h)
            for c in range(ch):
                y = top + c * (plot_h + gap)
                bottom = y + plot_h
                col = ctx.colors[c % len(ctx.colors)] if solid else row_col
                pygame.draw.line(surf, ctx.grid_color, (pad, bottom), (w - pad, bottom))
                draw_channel(surf, heights[c], (pad, bottom - bar_h, w - 2 * pad, bar_h), col)
