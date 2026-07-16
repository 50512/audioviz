"""Circulo de barras: las bandas se disponen en anillo alrededor del disco
central, cada una como una cuna que crece radialmente hacia afuera.

A diferencia de las barras rectas, esta unifica los canales en uno solo
(promedio) para dibujar un unico espectro simetrico. El color va de barra a
barra: cada cuna es de color solido, pero el tono recorre un degradado desde el
celeste del canal izquierdo hasta el rojo del canal derecho.

El anillo arranca un poco por fuera del radio del vinilo (ctx.disc_radius), asi
las barras rodean el disco sin taparlo (el vinilo se pinta despues, encima).
"""

from __future__ import annotations

import math

import pygame

from ..engine import VizFrame
from .base import RenderContext, Visualization

INNER_MARGIN = 1.1    # radio interior = radio del vinilo * esto (un poco mayor)
FILL = 0.7            # fraccion angular ocupada por la barra (resto: hueco)
OUTER_PAD = 8         # px que dejamos libres hasta el borde de la ventana
MIN_LEN = 2.0         # largo minimo de barra, para que el anillo nunca desaparezca


def _lerp_color(c0, c1, t):
    return (round(c0[0] + (c1[0] - c0[0]) * t),
            round(c0[1] + (c1[1] - c0[1]) * t),
            round(c0[2] + (c1[2] - c0[2]) * t))


class CircleBarsVisualization(Visualization):
    id = "circle"
    label = "circulo"
    default_on = False

    def __init__(self) -> None:
        # El degradado depende solo del nº de bandas y de los dos extremos de
        # color; lo recalculamos solo cuando cambia alguno (el nº de bandas puede
        # variar en caliente al cambiar de modo o de resolucion de analisis).
        self._grad: list[tuple[int, int, int]] | None = None
        self._grad_key: tuple | None = None

    def _gradient(self, n, c0, c1):
        key = (n, tuple(c0), tuple(c1))
        if key == self._grad_key:
            return self._grad
        if n <= 1:
            grad = [tuple(c0)]
        else:
            grad = [_lerp_color(c0, c1, i / (n - 1)) for i in range(n)]
        self._grad, self._grad_key = grad, key
        return grad

    def draw(self, surf: pygame.Surface, frame: VizFrame, ctx: RenderContext) -> None:
        # Unifica canales: un unico espectro (promedio) para todo el anillo.
        mono = frame.normalized().mean(axis=0)   # (n_bands,) en 0..1
        n = len(mono)
        if n == 0:
            return

        cx, cy = ctx.center
        inner = ctx.disc_radius * INNER_MARGIN
        outer_limit = min(ctx.width, ctx.height) * 0.5 - OUTER_PAD
        max_len = max(MIN_LEN, outer_limit - inner) * ctx.max_height_frac

        grad = self._gradient(n, ctx.colors[0], ctx.colors[1])
        slot = 2.0 * math.pi / n
        half = slot * 0.5 * FILL                 # semiancho angular de cada barra

        for i in range(n):
            # float() nativo a proposito: pygame.draw.polygon rechaza escalares
            # numpy.float32 (los que trae el frame) como coordenadas.
            length = max(MIN_LEN, float(mono[i]) * max_len)
            a = -math.pi / 2.0 + i * slot        # 0 arriba, avanza en sentido horario
            a0, a1 = a - half, a + half
            r_out = inner + length
            cos0, sin0 = math.cos(a0), math.sin(a0)
            cos1, sin1 = math.cos(a1), math.sin(a1)
            pts = (
                (cx + inner * cos0, cy + inner * sin0),
                (cx + r_out * cos0, cy + r_out * sin0),
                (cx + r_out * cos1, cy + r_out * sin1),
                (cx + inner * cos1, cy + inner * sin1),
            )
            pygame.draw.polygon(surf, grad[i], pts)
