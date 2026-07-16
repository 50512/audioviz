"""Registro de visualizaciones del espectro.

REGISTRY es el orden de dibujo y de aparicion en el panel. Para anadir una nueva
visualizacion basta con importarla aca y sumarla a la lista: el visualizador la
dibujara cuando este activa y el panel de configuracion le pondra su interruptor
automaticamente.
"""

from __future__ import annotations

from .bars import BarsVisualization
from .base import RenderContext, Visualization
from .circle_bars import CircleBarsVisualization

# Clases, no instancias: el visualizador construye una instancia de cada una (una
# visualizacion puede querer cachear estado propio entre cuadros).
REGISTRY: list[type[Visualization]] = [
    BarsVisualization,
    CircleBarsVisualization,
]

__all__ = ["RenderContext", "Visualization", "REGISTRY", "build_visualizations"]


def build_visualizations() -> list[Visualization]:
    """Una instancia por visualizacion registrada, en orden de dibujo."""
    return [cls() for cls in REGISTRY]
