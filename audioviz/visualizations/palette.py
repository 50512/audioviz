"""Extraccion de una paleta (1 a 3 colores) desde la caratula, via k-means.

Decisiones que hacen que sea util para un visualizador:

  - Clusteriza en OKLab, no en RGB: los grupos salen perceptualmente coherentes
    (dos verdes parecidos caen juntos, no se parten por diferencia numerica).
  - Semilla FIJA en el init k-means++: misma caratula -> misma paleta siempre.
    En un visualizador esto es clave; con init aleatorio los colores "saltarian"
    al reabrir o entre canciones parecidas.
  - Downscale a 48x48 antes de clusterizar: k-means casi instantaneo y la paleta
    no cambia (los colores dominantes se conservan al reducir).
  - Cuenta adaptativa: fusiona clusters perceptualmente cercanos y decide cuantos
    colores devolver (1..3) por cobertura. Un arte casi monocromo da 1; uno
    variado, 2 o 3.

La paleta final se ordena por luminosidad para que el mapeo a la escena (grave
oscuro -> agudo claro, por ejemplo) sea estable cuadro a cuadro.
"""

from __future__ import annotations

import numpy as np
import pygame

from .gradient import _oklab_to_rgb

SMALL = 48              # lado al que se reduce la caratula antes de clusterizar
KMEANS_SEED = 1234      # semilla fija del init: reproducibilidad total
KMEANS_ITERS = 24
MERGE_DIST = 0.10       # OKLab: por debajo de esto, dos centros son "el mismo"
DOMINANT = 0.70         # si el color mas pesado cubre esto o mas, devolvemos 1
SIGNIF = 0.12           # peso minimo (fraccion) para contar como color propio

# Manejo de negro/blanco (metodo mixto). Se descartan los centros casi-puros
# (luminosidad extrema Y croma baja: negro/blanco/gris-extremo, que no aportan
# color y sobre el fondo oscuro serian invisibles). Los que sobreviven se
# conservan, pero su luminosidad se normaliza al rango visible [L_FLOOR, L_CEIL]
# para que un color oscuro pero saturado (p.ej. azul marino) se vea sin perder
# su tono. L y croma son de OKLab (L en 0..1 aprox).
BLACK_L = 0.22          # por debajo -> demasiado oscuro
WHITE_L = 0.90          # por encima -> demasiado claro
LOW_CHROMA = 0.05       # en luminosidad extrema, por debajo de esto -> casi puro
GRAY_CHROMA = 0.03      # nivel 1 (estricto): por debajo de esto -> gris, se recorta
RELAXED_CHROMA = 0.0    # nivel 2 (permisivo): admite grises, como el proceso previo
L_FLOOR = 0.50          # se sube la luminosidad de los oscuros hasta aca
L_CEIL = 0.88           # se baja la de los muy claros hasta aca


def _rgb_to_oklab_np(rgb: np.ndarray) -> np.ndarray:
    """(n,3) sRGB 0..255 -> (n,3) OKLab. Version vectorizada de gradient._rgb_to_oklab."""
    c = rgb / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = lin[:, 0], lin[:, 1], lin[:, 2]
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = np.cbrt(l), np.cbrt(m), np.cbrt(s)
    return np.stack([
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    ], axis=1)


def _kmeans(points: np.ndarray, k: int, seed: int, iters: int):
    """k-means con init k-means++ sembrado. Devuelve (centros, conteos)."""
    rng = np.random.default_rng(seed)
    n = len(points)
    centers = [points[rng.integers(n)]]
    for _ in range(1, k):
        d2 = np.min([np.sum((points - c) ** 2, axis=1) for c in centers], axis=0)
        total = d2.sum()
        idx = rng.integers(n) if total <= 0 else rng.choice(n, p=d2 / total)
        centers.append(points[idx])
    centers = np.array(centers, dtype=float)

    labels = np.zeros(n, dtype=int)
    for _ in range(iters):
        d = np.sum((points[:, None, :] - centers[None, :, :]) ** 2, axis=2)  # (n,k)
        labels = np.argmin(d, axis=1)
        new = np.array([points[labels == j].mean(axis=0) if np.any(labels == j)
                        else centers[j] for j in range(k)])
        if np.allclose(new, centers):
            break
        centers = new
    counts = np.array([int(np.sum(labels == j)) for j in range(k)])
    return centers, counts


def _select(centers, counts, gray_chroma):
    """Elige la paleta final (1..3 colores) a partir de los centros del k-means,
    o None si ninguno es utilizable. `gray_chroma` es el piso de croma: sube la
    exigencia contra los grises. El resto de umbrales es fijo.

    Descarta -> fusiona -> cuenta -> normaliza. El descarte va ANTES de fusionar:
    si no, un gris/negro dominante se traga un acento sutil al promediarse con el
    (el centro fusionado queda gris y se descarta, perdiendo el color)."""
    L = centers[:, 0]
    chroma = np.hypot(centers[:, 1], centers[:, 2])
    near_pure = ((L < BLACK_L) | (L > WHITE_L)) & (chroma < LOW_CHROMA)
    usable = (counts > 0) & ~near_pure & (chroma >= gray_chroma)
    centers, counts = centers[usable], counts[usable]
    if len(centers) == 0:
        return None

    order = np.argsort(-counts)
    kept: list[np.ndarray] = []
    kept_w: list[float] = []
    for c, w in zip(centers[order], counts[order]):
        for i, kc in enumerate(kept):
            if np.linalg.norm(c - kc) < MERGE_DIST:
                kept_w[i] += w
                break
        else:
            kept.append(c)
            kept_w.append(float(w))

    kept = np.array(kept)
    weights = np.array(kept_w, dtype=float)
    order2 = np.argsort(-weights)
    kept, weights = kept[order2], weights[order2]
    frac = weights / weights.sum()

    if len(kept) == 1 or frac[0] >= DOMINANT:
        n = 1
    else:
        n = int(min(len(kept), max(2, min(3, int((frac >= SIGNIF).sum())))))

    # Normaliza la luminosidad al rango visible (conserva tono y croma; solo sube
    # oscuros / baja claros) y ordena por L para un mapeo estable.
    chosen = kept[:n].copy()
    chosen[:, 0] = np.clip(chosen[:, 0], L_FLOOR, L_CEIL)
    chosen = chosen[np.argsort(chosen[:, 0])]
    return [tuple(int(v) for v in _oklab_to_rgb(tuple(c))) for c in chosen]


def extract_palette(surface: pygame.Surface) -> list[tuple[int, int, int]] | None:
    """Paleta de 1 a 3 colores de la caratula, o None si no hay nada usable (el
    llamador cae entonces a los colores por defecto). Determinista.

    Degradacion en niveles antes de rendirse: se clusteriza UNA vez y se intenta
    la seleccion en dos pasadas cada vez mas permisivas:
      1) estricta: recorta grises (GRAY_CHROMA), para acentos vivos;
      2) permisiva: admite grises (RELAXED_CHROMA), como el proceso anterior,
         por si la reduccion de la imagen dejo solo tonos apagados/grisaceos.
    Recien si ambas fallan se devuelve None. Reusar el k-means hace que la segunda
    pasada sea practicamente gratis (solo se rehace la seleccion)."""
    try:
        small = pygame.transform.smoothscale(surface, (SMALL, SMALL))
        arr = pygame.surfarray.array3d(small).reshape(-1, 3).astype(float)
    except Exception:
        return None

    lab = _rgb_to_oklab_np(arr)
    centers, counts = _kmeans(lab, 3, KMEANS_SEED, KMEANS_ITERS)
    return (_select(centers, counts, GRAY_CHROMA)        # nivel 1: estricto
            or _select(centers, counts, RELAXED_CHROMA))  # nivel 2: permisivo
