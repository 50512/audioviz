"""Circulo de barras: las bandas se disponen en anillo alrededor del disco
central, cada una como una cuna que crece radialmente hacia afuera.

A diferencia de las barras rectas, esta unifica los canales en uno solo
(promedio) para dibujar un unico espectro simetrico. El color va de barra a
barra: cada cuna es de color solido, pero el tono recorre un degradado desde el
celeste del canal izquierdo hasta el rojo del canal derecho. El modo del
degradado (ctx.circle_gradient_mode) esta explicado en gradient.py.

El anillo arranca un poco por fuera del radio del vinilo (ctx.disc_radius), asi
las barras rodean el disco sin taparlo (el vinilo se pinta despues, encima).
"""

from __future__ import annotations

import math

import pygame

from ..engine import VizFrame
from .base import (RenderContext, SliderSetting, StepperSetting, ToggleSetting,
                   Visualization)
from .gradient import GRADIENT_LABELS, GRADIENT_MODES, build_gradient

DEFAULT_RADIUS_MULT = 1.0   # radio interior por defecto = radio del vinilo * esto
DEFAULT_CENTER = 50.0       # posicion vertical del centro por defecto (% del alto; 50 = mitad)
FILL = 0.7            # fraccion angular ocupada por la barra (resto: hueco)
OUTER_PAD = 8         # px que dejamos libres hasta el borde de la ventana
MIN_LEN = 2.0         # largo minimo de barra, para que el anillo nunca desaparezca


class CircleBarsVisualization(Visualization):
    id = "circle"
    label = "viz_circle"   # clave i18n; el panel la traduce (pestana y toggle)
    default_on = False

    def __init__(self) -> None:
        # El degradado depende solo del nº de bandas y de los dos extremos de
        # color; lo recalculamos solo cuando cambia alguno (el nº de bandas puede
        # variar en caliente al cambiar de modo o de resolucion de analisis).
        self._grad: list[tuple[int, int, int]] | None = None
        self._grad_key: tuple | None = None

    def settings(self) -> list:
        # Los labels (fila y valores) son claves i18n; el panel los traduce.
        return [
            SliderSetting("s_ring_radius", "circle_radius_mult", 1.0, 3.0, step=0.05,
                          integer=False, fmt=lambda v: f"{v:.2f}x"),
            SliderSetting("s_max_height", "circle_max_height", 0, 100, step=1,
                          fmt=lambda v: f"{int(v)} %"),
            SliderSetting("s_circle_center", "circle_center", 0, 100, step=1,
                          fmt=lambda v: f"{int(v)} %"),
            StepperSetting("gradient", "circle_gradient_mode",
                           GRADIENT_MODES, GRADIENT_LABELS),
            ToggleSetting("s_custom_colors", "circle_use_custom"),
            ToggleSetting("s_use_cover", "circle_use_cover"),
            ToggleSetting("s_symmetric", "circle_symmetric"),
        ]

    def _gradient(self, n, stops, mode):
        key = (n, tuple(stops), mode)
        if key == self._grad_key:
            return self._grad
        self._grad = build_gradient(n, stops, mode)
        self._grad_key = key
        return self._grad

    def draw(self, surf: pygame.Surface, frame: VizFrame, ctx: RenderContext) -> None:
        # Unifica canales: un unico espectro (promedio) para todo el anillo.
        mono = frame.normalized().mean(axis=0)   # (n_bands,) en 0..1
        n = len(mono)
        if n == 0:
            return

        cx, cy = ctx.center
        inner = ctx.disc_radius * ctx.circle_radius_mult
        outer_limit = min(ctx.width, ctx.height) * 0.5 - OUTER_PAD
        max_len = max(MIN_LEN, outer_limit - inner) * ctx.circle_max_height_frac

        # Con "caratula" activa, la paleta extraida (1..3 colores) manda: 1 =
        # solido, 2 = degradado, 3 = degradado con punto medio. Se interpola en
        # oklch (perceptual, natural entre colores arbitrarios). Si no, los
        # colores por defecto con el modo elegido.
        if ctx.circle_use_cover and ctx.cover_palette:
            stops, mode = ctx.cover_palette, "oklch"
        else:
            # Fallback (sin caratula util): paleta personalizada si el circulo la
            # tiene activada, o los colores por defecto. El circulo siempre es un
            # degradado, asi que el 3er color (medio, parte de lo personalizado) se
            # usa siempre que este activado.
            chan = ctx.custom_colors if ctx.circle_use_custom else ctx.colors
            mid = ctx.custom_mid if ctx.circle_use_custom else None
            stops, mode = [chan[0], chan[1]], ctx.circle_gradient_mode
            if mid is not None:
                stops = [chan[0], mid, chan[1]]
        grad = self._gradient(n, stops, mode)
        slot = 2.0 * math.pi / n
        half = slot * 0.5 * FILL                 # semiancho angular de cada barra
        symmetric = ctx.circle_symmetric

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
            if symmetric:
                # Color por posicion horizontal (no por banda): derecha = primer
                # color, izquierda = ultimo, arriba/abajo = el medio. Es simetrico
                # respecto al eje vertical, asi que cierra sin costura (arriba y
                # abajo coinciden por ambos lados). t=0 derecha .. t=1 izquierda.
                t = (1.0 - math.cos(a)) * 0.5
                color = grad[int(t * (n - 1))]
            else:
                color = grad[i]                  # lineal por banda (con costura arriba)
            pygame.draw.polygon(surf, color, pts)
