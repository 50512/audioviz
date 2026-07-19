"""Interpolacion de color para degradados, compartida por las visualizaciones.

El modo decide COMO se interpola entre dos colores, porque una recta en RGB pasa
cerca del gris a mitad de camino cuando los extremos son casi complementarios
(como el cian y el rojo que usamos):

    rgb    interpolacion RGB lineal (rapida, pero el medio tiende al gris)
    warm   gira el tono por cian->verde->amarillo->naranja->rojo (medio vivo)
    cool   gira el tono por cian->azul->magenta->rojo (medio violeta)
    oklch  gira el tono en espacio perceptual (medio saturado, sin arcoiris)

(OKLCH = OKLab en polar: interpola el tono como giro manteniendo la croma alta,
por eso no se desatura aunque los extremos sean casi complementarios; una recta
en OKLab plano SI pasaria cerca del gris, igual que RGB.)
"""

from __future__ import annotations

import colorsys
import math

# Modos de degradado. GRADIENT_MODES son los ids estables (flag, estado);
# GRADIENT_LABELS lo que se ve en el panel. Mismo orden.
GRADIENT_MODES = ["rgb", "warm", "cool", "oklch"]
GRADIENT_LABELS = ["rgb", "cálido", "frío", "oklch"]
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
    """Interpola en OKLCH: L y croma lineales, tono por el arco mas corto. La
    croma no cruza el cero, asi que el medio no se desatura aunque los extremos
    sean casi complementarios."""
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


def interp(c0, c1, t, mode):
    """Un unico color interpolado entre c0 y c1 en el parametro t (0..1)."""
    if mode == "oklch":
        return _lerp_oklch(_rgb_to_oklab(c0), _rgb_to_oklab(c1), t)
    if mode == "warm":
        return _lerp_hsv(c0, c1, t, ascending=False)
    if mode == "cool":
        return _lerp_hsv(c0, c1, t, ascending=True)
    return _lerp_rgb(c0, c1, t)


def build_gradient(n, stops, mode):
    """Lista de n colores solidos que recorren `stops` (1..k colores) segun el
    modo de interpolacion. 1 stop = color solido; 2 = degradado clasico; 3+ =
    multiescala con esos colores como puntos equiespaciados (el del medio es el
    punto medio del degradado)."""
    stops = [tuple(s) for s in stops]
    if n <= 1:
        return [stops[0]]
    if len(stops) == 1:
        return [stops[0]] * n
    k = len(stops)
    out = []
    for i in range(n):
        p = (i / (n - 1)) * (k - 1)       # posicion global en [0, k-1]
        seg = min(int(p), k - 2)          # segmento entre stops[seg] y stops[seg+1]
        out.append(interp(stops[seg], stops[seg + 1], p - seg, mode))
    return out
