"""Circulo de barras: las bandas se disponen en anillo alrededor del disco
central, cada una como una cuna que crece radialmente hacia afuera.

A diferencia de las barras rectas, esta unifica los canales en uno solo
(promedio) para dibujar un unico espectro simetrico. El color va de barra a
barra: cada cuna es de color solido, pero el tono recorre un degradado desde el
celeste del canal izquierdo hasta el rojo del canal derecho.

El modo del degradado (ctx.circle_gradient_mode) decide COMO se interpola entre
esos dos extremos, porque una recta en RGB pasa cerca del gris a mitad de camino:

    rgb    interpolacion RGB lineal (rapida, pero el medio tiende al gris)
    warm   gira el tono por cian->verde->amarillo->naranja->rojo (medio vivo)
    cool   gira el tono por cian->azul->magenta->rojo (medio violeta)
    oklch  gira el tono en espacio perceptual (medio saturado, sin arcoiris)

(Nota: una recta en OKLab tambien pasa cerca del gris cuando los extremos son
casi complementarios, como cian y rojo; por eso el modo perceptual usa OKLCH
—OKLab en polar— que interpola el tono como giro y mantiene la croma alta.)

El anillo arranca un poco por fuera del radio del vinilo (ctx.disc_radius), asi
las barras rodean el disco sin taparlo (el vinilo se pinta despues, encima).
"""

from __future__ import annotations

import colorsys
import math

import pygame

from ..engine import VizFrame
from .base import RenderContext, SliderSetting, StepperSetting, Visualization

DEFAULT_RADIUS_MULT = 1.1   # radio interior por defecto = radio del vinilo * esto
FILL = 0.7            # fraccion angular ocupada por la barra (resto: hueco)
OUTER_PAD = 8         # px que dejamos libres hasta el borde de la ventana
MIN_LEN = 2.0         # largo minimo de barra, para que el anillo nunca desaparezca

# Modos de degradado seleccionables. GRADIENT_MODES son los ids estables (flag,
# estado); GRADIENT_LABELS lo que se ve en el panel. Mismo orden.
GRADIENT_MODES = ["rgb", "warm", "cool", "oklch"]
GRADIENT_LABELS = ["rgb", "calido", "frio", "oklch"]
DEFAULT_GRADIENT = "rgb"


def _lerp_rgb(c0, c1, t):
    return (round(c0[0] + (c1[0] - c0[0]) * t),
            round(c0[1] + (c1[1] - c0[1]) * t),
            round(c0[2] + (c1[2] - c0[2]) * t))


def _lerp_hsv(c0, c1, t, ascending):
    """Interpola girando el tono (hue), no en RGB: saturacion y brillo se
    mantienen altos, asi el medio no cae al gris. `ascending` elige el sentido
    del giro (por que lado del circulo cromatico se pasa)."""
    h0, s0, v0 = colorsys.rgb_to_hsv(c0[0] / 255, c0[1] / 255, c0[2] / 255)
    h1, s1, v1 = colorsys.rgb_to_hsv(c1[0] / 255, c1[1] / 255, c1[2] / 255)
    up = (h1 - h0) % 1.0                  # distancia girando hacia tonos crecientes
    dh = up if ascending else up - 1.0    # descendente: el complemento del giro
    h = (h0 + dh * t) % 1.0
    r, g, b = colorsys.hsv_to_rgb(h, s0 + (s1 - s0) * t, v0 + (v1 - v0) * t)
    return (round(r * 255), round(g * 255), round(b * 255))


def _srgb_to_linear(u):
    u /= 255.0
    return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(u):
    u = 0.0 if u < 0.0 else (1.0 if u > 1.0 else u)   # recorta fuera de gamut
    s = u * 12.92 if u <= 0.0031308 else 1.055 * u ** (1 / 2.4) - 0.055
    return round(s * 255)


def _rgb_to_oklab(c):
    r, g, b = _srgb_to_linear(c[0]), _srgb_to_linear(c[1]), _srgb_to_linear(c[2])
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def _oklab_to_rgb(lab):
    L, a, b = lab
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (_linear_to_srgb(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
            _linear_to_srgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
            _linear_to_srgb(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s))


def _lerp_oklch(a_lab, b_lab, t):
    """Interpola en OKLCH (OKLab en polar): L y croma lineales, tono por el arco
    mas corto. A diferencia de la recta en OKLab, la croma no cruza el cero, asi
    que el medio no se desatura aunque los extremos sean casi complementarios."""
    l0, a0, b0 = a_lab
    l1, a1, b1 = b_lab
    c0, c1 = math.hypot(a0, b0), math.hypot(a1, b1)
    h0, h1 = math.atan2(b0, a0), math.atan2(b1, a1)
    dh = (h1 - h0) % (2 * math.pi)
    if dh > math.pi:
        dh -= 2 * math.pi                 # arco mas corto (por cualquiera de los lados)
    L = l0 + (l1 - l0) * t
    C = c0 + (c1 - c0) * t
    H = h0 + dh * t
    return _oklab_to_rgb((L, C * math.cos(H), C * math.sin(H)))


def _build_gradient(n, c0, c1, mode):
    """Lista de n colores solidos de c0 a c1 segun el modo de interpolacion."""
    if n <= 1:
        return [tuple(c0)]
    if mode == "oklch":
        a_lab, b_lab = _rgb_to_oklab(c0), _rgb_to_oklab(c1)   # extremos una sola vez
        return [_lerp_oklch(a_lab, b_lab, i / (n - 1)) for i in range(n)]
    if mode == "warm":
        return [_lerp_hsv(c0, c1, i / (n - 1), ascending=False) for i in range(n)]
    if mode == "cool":
        return [_lerp_hsv(c0, c1, i / (n - 1), ascending=True) for i in range(n)]
    return [_lerp_rgb(c0, c1, i / (n - 1)) for i in range(n)]


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

    def settings(self) -> list:
        return [
            SliderSetting("radio", "circle_radius_mult", 1.0, 3.0, step=0.05,
                          integer=False, fmt=lambda v: f"{v:.2f}x"),
            SliderSetting("alto", "circle_max_height", 0, 100, step=1,
                          fmt=lambda v: f"{int(v)} %"),
            StepperSetting("degradado", "circle_gradient_mode",
                           GRADIENT_MODES, GRADIENT_LABELS),
        ]

    def _gradient(self, n, c0, c1, mode):
        key = (n, tuple(c0), tuple(c1), mode)
        if key == self._grad_key:
            return self._grad
        self._grad = _build_gradient(n, c0, c1, mode)
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

        grad = self._gradient(n, ctx.colors[0], ctx.colors[1], ctx.circle_gradient_mode)
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
