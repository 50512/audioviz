"""Contrato de una visualizacion del espectro.

Una visualizacion recibe un VizFrame ya analizado (alturas 0..1 por banda y
canal) y lo dibuja sobre la superficie de pygame. No sabe NADA de sockets, FFT
ni del panel de configuracion: solo pinta. El visualizador mantiene una lista de
ellas y dibuja las que esten activas, en orden, una encima de otra.

Para anadir una visualizacion nueva: subclase de Visualization con un `id` y una
`label` unicos, implementa draw(), y registrala en __init__.py. El panel de
configuracion genera su interruptor solo, y si la visualizacion declara ajustes
propios via settings(), tambien le arma una pestana con sus controles.

Los ajustes se declaran como datos (SliderSetting/StepperSetting/ToggleSetting),
no como widgets: la visualizacion no depende de pygame ni del panel. Cada spec
apunta por nombre a un atributo del estado de vista (ViewState), que es donde
vive el valor; el panel traduce el spec al widget y lo cablea a ese atributo.

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
from typing import Any, Callable, Sequence

import pygame

from ..engine import VizFrame


# --- specs de ajuste: datos que describen un control, sin tocar pygame --------
# Cada uno apunta a un atributo del ViewState por nombre (`attr`); el panel hace
# getattr/setattr sobre el para leer y escribir en caliente.

@dataclass(frozen=True)
class SliderSetting:
    label: str
    attr: str
    lo: float
    hi: float
    step: float = 1
    integer: bool = True
    fmt: Callable[[float], str] | None = None


@dataclass(frozen=True)
class StepperSetting:
    label: str
    attr: str
    values: Sequence[Any]
    labels: Sequence[str] | None = None


@dataclass(frozen=True)
class ToggleSetting:
    label: str
    attr: str


@dataclass(frozen=True)
class RenderContext:
    """Todo lo que una visualizacion necesita del entorno para dibujar, aparte
    del propio frame. Lo arma el visualizador cada cuadro."""

    width: int                                  # ancho de la ventana en px
    height: int                                 # alto de la ventana en px
    header_h: int                               # alto reservado arriba (barra now-playing)
    max_height_frac: float                      # tope de altura de las barras verticales, fraccion 0..1
    colors: Sequence[tuple[int, int, int]]      # color por canal
    grid_color: tuple[int, int, int]            # color de las lineas de base
    bars_gradient_mode: str                     # color de las barras: solid/rgb/warm/cool/oklch
    bars_gradient_scope: str                    # alcance del degradado de barras: channel/span
    center: tuple[int, int]                     # centro del disco/vinilo central (px)
    disc_radius: float                          # radio del vinilo central (px), este o no visible
    circle_radius_mult: float                   # multiplicador del radio interior del circulo de barras
    circle_max_height_frac: float               # tope de largo de las barras del circulo, fraccion 0..1
    circle_gradient_mode: str                   # modo de degradado del circulo (rgb/warm/cool/oklch)
    cover_palette: "list[tuple[int, int, int]] | None"  # 1..3 colores de la caratula (o None)
    bars_use_cover: bool                        # las barras usan la paleta de la caratula
    circle_use_cover: bool                      # el circulo usa la paleta de la caratula


class Visualization:
    """Base de toda visualizacion. `id` es la clave estable (estado, guardado);
    `label` es lo que ve el usuario en el panel."""

    id: str = ""
    label: str = ""
    default_on: bool = False

    def draw(self, surf: pygame.Surface, frame: VizFrame, ctx: RenderContext) -> None:
        raise NotImplementedError

    def settings(self) -> list:
        """Ajustes propios de esta visualizacion (specs de base.py). El panel les
        arma una pestana. Vacio = sin pestana propia."""
        return []
