"""Paleta y medidas del panel de configuracion, en un solo lugar.

Las comparten el panel (settings_panel), los controles genericos (widgets) y el
selector de color (color_picker), asi que viven aca para no duplicarlas ni
generar dependencias cruzadas entre esos modulos. Alineadas con la paleta del
visualizador (fondo 14,14,18 / acento frio 90,200,250).
"""

from __future__ import annotations

# --- colores -----------------------------------------------------------------
OVERLAY = (0, 0, 0, 150)          # oscurece el fondo para enfocar el modal
PANEL_BG = (22, 23, 30)
PANEL_BORDER = (70, 78, 104)
TITLE = (225, 225, 232)
LABEL = (150, 150, 165)
VALUE = (210, 212, 222)
TRACK = (44, 46, 58)
TRACK_FILL = (90, 200, 250)
KNOB = (214, 218, 232)
BTN_BG = (36, 38, 48)
BTN_HOVER = (52, 55, 68)
BTN_TEXT = (200, 202, 214)
TOGGLE_ON = (90, 200, 140)
TOGGLE_OFF = (66, 68, 82)
TOGGLE_KNOB = (232, 234, 242)
TAB_ACTIVE_BG = (52, 55, 68)     # pestana seleccionada
TAB_INACTIVE_BG = (28, 29, 38)   # pestanas de fondo
TAB_ACCENT = (90, 200, 250)      # subrayado de la pestana activa (acento frio)
TAB_TEXT_ON = (225, 225, 232)
TAB_TEXT_OFF = (140, 142, 156)
SWATCH_BORDER = (120, 128, 150)  # borde de los recuadros de color (swatch/preview)

# --- medidas -----------------------------------------------------------------
ROW_H = 34
TAB_H = 30
PAD = 16
LABEL_W = 124
PANEL_W = 576   # ancho pensado para que las 6 pestanas fijas + las de viz respiren
