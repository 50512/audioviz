"""Contrato de una visualizacion del espectro.

Una visualizacion recibe un VizFrame ya analizado (alturas 0..1 por banda y
canal) y lo dibuja sobre la superficie de pygame. No sabe NADA de sockets, FFT
ni del panel de configuracion: solo pinta. El visualizador mantiene una lista de
ellas y dibuja las que esten activas, en orden, una encima de otra.

Para anadir una visualizacion nueva: subclase de Visualization con un `id` y una
`label` unicos, implementa draw(), y registrala en __init__.py. El panel de
configuracion genera su interruptor solo.

OJO con la forma de los datos: el modo de analisis (log u octaves) lo decide el
Engine, es global, y TODAS las visualizaciones reciben el frame en ese modo (no
lo eligen ellas). El contrato de draw() no cambia entre modos, pero SI cambia la
forma del frame:

    - el numero de bandas varia (log: el que pidas; octaves: lo dicta el rango
      de notas) y puede cambiar en caliente de un cuadro al siguiente, porque el
      panel reconfigura el analisis en vivo;
    - frame.centers (Hz por banda) cambia de espaciado con el modo.

Por eso NO caches el conteo de bandas: lee len(frame.normalized()[c]) cada
cuadro. Asumir un numero fijo rompe al cambiar de modo o de nº de bandas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pygame

from ..engine import VizFrame


@dataclass(frozen=True)
class RenderContext:
    """Todo lo que una visualizacion necesita del entorno para dibujar, aparte
    del propio frame. Lo arma el visualizador cada cuadro."""

    width: int                                  # ancho de la ventana en px
    height: int                                 # alto de la ventana en px
    header_h: int                               # alto reservado arriba (barra now-playing)
    max_height_frac: float                      # tope de altura como fraccion 0..1 del alto
    colors: Sequence[tuple[int, int, int]]      # color por canal
    grid_color: tuple[int, int, int]            # color de las lineas de base
    center: tuple[int, int]                     # centro del disco/vinilo central (px)
    disc_radius: float                          # radio del vinilo central (px), este o no visible


class Visualization:
    """Base de toda visualizacion. `id` es la clave estable (estado, guardado);
    `label` es lo que ve el usuario en el panel."""

    id: str = ""
    label: str = ""
    default_on: bool = False

    def draw(self, surf: pygame.Surface, frame: VizFrame, ctx: RenderContext) -> None:
        raise NotImplementedError
