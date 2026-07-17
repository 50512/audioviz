"""Menu de configuracion modal (pygame). Se dibuja a mano —pygame no trae
widgets— pero encaja con el resto del visualizador, que ya pinta y cachea todo
manualmente, sin dependencias extra.

Filosofia: los widgets NO guardan estado propio. Leen y escriben la unica
fuente de verdad (el Engine y el estado de la vista) via un getter y un setter.
Asi el panel siempre refleja lo real —incluso si un atajo de teclado cambia el
mismo valor por fuera— y cada cambio se aplica en caliente al instante.

Se controla con mouse. Los atajos de teclado directos del visualizador siguen
funcionando en paralelo; este panel solo los hace descubribles y ajustables
con precision (sliders) o sin memorizar teclas (steppers).
"""

from __future__ import annotations

import colorsys
from typing import Callable

import numpy as np
import pygame
import pygame.surfarray

from . import config
from .analysis import NOTES, parse_note
from .sources import is_available
from .visualizations.base import SliderSetting, StepperSetting, ToggleSetting
from .visualizations.gradient import (GRADIENT_LABELS, GRADIENT_MODES,
                                      build_gradient)

# Paleta, alineada con la del visualizador (fondo 14,14,18 / acento 90,200,250).
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

ROW_H = 34
TAB_H = 30
PAD = 16
LABEL_W = 116
PANEL_W = 484   # ancho pensado para que las 5 pestanas fijas + las de viz respiren

SWATCH_BORDER = (120, 128, 150)   # borde de los recuadros de color (swatch/preview)


def _rgb_to_hex(c) -> str:
    return "#{:02X}{:02X}{:02X}".format(int(c[0]), int(c[1]), int(c[2]))


def _hex_to_rgb(s: str):
    """'#rrggbb' o 'rgb' (o sin #) -> (r, g, b), o None si no es un hex valido."""
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return None
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def midi_to_name(m: int) -> str:
    """69 -> 'A4', 12 -> 'C0'. Inverso de analysis.parse_note."""
    return f"{NOTES[m % 12]}{m // 12 - 1}"


class _Row:
    """Una fila del panel: etiqueta a la izquierda, control a la derecha. El
    control recibe su rect en cada layout y se dibuja/atiende dentro de el."""

    def __init__(self, label: str, control, visible: Callable[[], bool] | None = None):
        self.label = label
        self.control = control
        self.visible = visible or (lambda: True)


class Slider:
    """Deslizador horizontal con snap a paso. Un valor numerico continuo."""

    def __init__(self, get, setter, lo, hi, step=1, integer=True, fmt=None):
        self.get, self.set = get, setter
        self.lo, self.hi, self.step = lo, hi, step
        self.integer = integer
        self.fmt = fmt or ((lambda v: f"{int(v)}") if integer else (lambda v: f"{v:.1f}"))
        self.rect = pygame.Rect(0, 0, 0, 0)
        self._drag = False

    def _track(self) -> pygame.Rect:
        # Reservamos ~58px a la derecha para el texto del valor.
        return pygame.Rect(self.rect.x, self.rect.centery - 3, self.rect.w - 58, 6)

    def _value_from_x(self, x) -> float:
        tr = self._track()
        t = 0.0 if tr.w <= 0 else max(0.0, min(1.0, (x - tr.x) / tr.w))
        v = self.lo + round(t * (self.hi - self.lo) / self.step) * self.step
        v = max(self.lo, min(self.hi, v))
        return int(round(v)) if self.integer else v

    def handle(self, ev) -> bool:
        tr = self._track()
        hot = tr.inflate(16, 20)   # zona clicable generosa (barra + pomo)
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and hot.collidepoint(ev.pos):
            self._drag = True
            self.set(self._value_from_x(ev.pos[0]))
            return True
        if ev.type == pygame.MOUSEMOTION and self._drag:
            self.set(self._value_from_x(ev.pos[0]))
            return True
        if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1 and self._drag:
            self._drag = False
            return True
        return False

    def draw(self, surf, font):
        tr = self._track()
        t = 0.0 if self.hi == self.lo else (self.get() - self.lo) / (self.hi - self.lo)
        t = max(0.0, min(1.0, t))
        pygame.draw.rect(surf, TRACK, tr, border_radius=3)
        pygame.draw.rect(surf, TRACK_FILL, (tr.x, tr.y, int(tr.w * t), tr.h), border_radius=3)
        kx = tr.x + int(tr.w * t)
        pygame.draw.circle(surf, KNOB, (kx, tr.centery), 7)
        txt = font.render(self.fmt(self.get()), True, VALUE)
        surf.blit(txt, (tr.right + 10, self.rect.centery - txt.get_height() // 2))


class Stepper:
    """[<]  valor  [>]  para elegir entre opciones discretas ciclando. Encaja
    donde una lista desplegable no cabria (fuente, distribucion, vista).

    values/labels pueden ser una lista fija o un callable que la devuelve: la
    fuente, por ejemplo, cambia su set de opciones en vivo (fb2k aparece o no
    segun el toggle), asi que se recalcula en cada uso en lugar de congelarse."""

    def __init__(self, get, setter, values, labels=None):
        self.get, self.set = get, setter
        self._values = values
        self._labels = labels
        self.rect = pygame.Rect(0, 0, 0, 0)

    def _opts(self):
        """(valores, etiquetas) actuales. Resuelve los callables al vuelo."""
        vals = list(self._values() if callable(self._values) else self._values)
        if self._labels is None:
            labels = [str(v) for v in vals]
        else:
            labels = list(self._labels() if callable(self._labels) else self._labels)
        return vals, labels

    def _btns(self):
        r = self.rect
        left = pygame.Rect(r.x, r.centery - 12, 24, 24)
        right = pygame.Rect(r.right - 24, r.centery - 12, 24, 24)
        return left, right

    def _index(self, values) -> int:
        try:
            return values.index(self.get())
        except ValueError:
            return 0

    def handle(self, ev) -> bool:
        if ev.type != pygame.MOUSEBUTTONDOWN or ev.button != 1:
            return False
        left, right = self._btns()
        values, _ = self._opts()
        n = len(values)
        if n == 0:
            return False
        if left.collidepoint(ev.pos):
            self.set(values[(self._index(values) - 1) % n])
            return True
        if right.collidepoint(ev.pos):
            self.set(values[(self._index(values) + 1) % n])
            return True
        return False

    def draw(self, surf, font):
        left, right = self._btns()
        values, labels = self._opts()
        mouse = pygame.mouse.get_pos()
        for r, glyph in ((left, "<"), (right, ">")):
            pygame.draw.rect(surf, BTN_HOVER if r.collidepoint(mouse) else BTN_BG,
                             r, border_radius=5)
            g = font.render(glyph, True, BTN_TEXT)
            surf.blit(g, (r.centerx - g.get_width() // 2, r.centery - g.get_height() // 2))
        label = labels[self._index(values)] if values else ""
        t = font.render(label, True, VALUE)
        mid = pygame.Rect(left.right, self.rect.y, right.left - left.right, self.rect.h)
        surf.blit(t, (mid.centerx - t.get_width() // 2, mid.centery - t.get_height() // 2))


class Toggle:
    """Interruptor on/off. Un booleano."""

    def __init__(self, get, setter):
        self.get, self.set = get, setter
        self.rect = pygame.Rect(0, 0, 0, 0)

    def _knob_rect(self) -> pygame.Rect:
        return pygame.Rect(self.rect.x, self.rect.centery - 11, 44, 22)

    def handle(self, ev) -> bool:
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and self._knob_rect().collidepoint(ev.pos):
            self.set(not self.get())
            return True
        return False

    def draw(self, surf, font):
        r = self._knob_rect()
        on = bool(self.get())
        pygame.draw.rect(surf, TOGGLE_ON if on else TOGGLE_OFF, r, border_radius=11)
        cx = r.right - 11 if on else r.x + 11
        pygame.draw.circle(surf, TOGGLE_KNOB, (cx, r.centery), 8)


class TextInput:
    """Campo de texto de una linea. Click para enfocar; se escribe con el teclado;
    Enter o click afuera confirma; Esc desenfoca sin guardar. Mientras esta
    enfocado edita un buffer propio y solo lo vuelca al setter al confirmar, para
    que un cambio a medio escribir no dispare nada (p.ej. reconectar sockets)."""

    def __init__(self, get, setter, placeholder=""):
        self.get, self.set = get, setter
        self.placeholder = placeholder
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.focused = False
        self._buf = ""

    def commit(self) -> None:
        if self.focused:
            self.set(self._buf.strip())
            self.focused = False

    def handle(self, ev) -> bool:
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and self.rect.collidepoint(ev.pos):
            if not self.focused:
                self.focused = True
                self._buf = self.get() or ""
            return True
        if self.focused and ev.type == pygame.KEYDOWN:
            if ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.commit()
            elif ev.key == pygame.K_ESCAPE:
                self.focused = False          # cancela sin guardar
            elif ev.key == pygame.K_BACKSPACE:
                self._buf = self._buf[:-1]
            elif ev.unicode and ev.unicode.isprintable():
                self._buf += ev.unicode
            return True
        return False

    def draw(self, surf, font):
        r = self.rect
        pygame.draw.rect(surf, BTN_BG, r, border_radius=5)
        pygame.draw.rect(surf, TRACK_FILL if self.focused else PANEL_BORDER,
                         r, width=2 if self.focused else 1, border_radius=5)
        shown = self._buf if self.focused else (self.get() or "")
        color = VALUE if shown else LABEL
        surf.blit(font.render(shown or self.placeholder, True, color),
                  (r.x + 8, r.centery - font.get_height() // 2))
        if self.focused:
            cx = r.x + 8 + font.size(self._buf)[0] + 1
            pygame.draw.line(surf, VALUE, (cx, r.centery - 8), (cx, r.centery + 8), 1)


class Swatch:
    """Recuadro de color clicable. Muestra el color actual (via getter) y, al
    hacer clic, abre el picker modal (via on_open). No guarda estado: el color
    vive en el ViewState y el picker lo escribe en caliente."""

    def __init__(self, get, on_open):
        self.get, self.on_open = get, on_open
        self.rect = pygame.Rect(0, 0, 0, 0)

    def handle(self, ev) -> bool:
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and self.rect.collidepoint(ev.pos):
            self.on_open()
            return True
        return False

    def draw(self, surf, font):
        bar = pygame.Rect(self.rect.x, self.rect.centery - 10, self.rect.w, 20)
        pygame.draw.rect(surf, tuple(self.get()), bar, border_radius=5)
        pygame.draw.rect(surf, SWATCH_BORDER, bar, width=1, border_radius=5)


class GradientPreview:
    """Barra que muestra el degradado resultante de los colores base (2 o 3
    stops segun color_use_mid) con el modo elegido (colors_gradient_mode). Es
    solo vista: no atiende eventos. Cachea la superficie porque recalcular el
    degradado por cuadro seria un derroche."""

    def __init__(self, view):
        self.view = view
        self.rect = pygame.Rect(0, 0, 0, 0)
        self._key = None
        self._surf = None

    def handle(self, ev) -> bool:
        return False

    def _stops(self):
        v = self.view
        if v.color_use_mid:
            return [tuple(v.color_lo), tuple(v.color_mid), tuple(v.color_hi)]
        return [tuple(v.color_lo), tuple(v.color_hi)]

    def draw(self, surf, font):
        bar = pygame.Rect(self.rect.x, self.rect.centery - 11, self.rect.w, 22)
        if bar.w <= 0:
            return
        stops = self._stops()
        mode = self.view.colors_gradient_mode
        key = (tuple(stops), mode, bar.w)
        if key != self._key:
            cols = build_gradient(bar.w, stops, mode)
            strip = pygame.Surface((bar.w, 1))
            for i, c in enumerate(cols):
                strip.set_at((i, 0), c)
            self._surf = pygame.transform.scale(strip, (bar.w, bar.h))
            self._key = key
        surf.blit(self._surf, (bar.x, bar.y))
        pygame.draw.rect(surf, SWATCH_BORDER, bar, width=1, border_radius=4)


class ColorPicker:
    """Selector de color modal (HSV). Un cuadro Saturacion x Valor para el tono
    actual, una tira de tono al lado, un swatch del color solido elegido y un
    campo HEX para verlo/tipearlo exacto. Trabaja en HSV internamente y escribe
    el RGB resultante en caliente (via setter), asi el swatch de la pestana y el
    preview del degradado se actualizan mientras se arrastra.

    Se dibuja y atiende eventos por encima del panel (modal sobre modal); el
    panel le enruta TODO mientras esta abierto y lo pinta al final."""

    BOX_W = 300
    SV = 200          # lado del cuadro saturacion/valor
    HUE_W = 22        # ancho de la tira de tono

    def __init__(self, font, font_title):
        self.font = font
        self.font_title = font_title
        self.open = False
        self.get = None
        self.set = None
        self.default = (255, 255, 255)
        self.title = ""
        self.h = self.s = self.v = 0.0
        self._drag = None            # 'sv' | 'hue' | None
        self._sv_key = None          # (tono cuantizado) del cuadro SV cacheado
        self._sv_surf = None
        self._hue_surf = None        # tira de tono (no depende de nada: se cachea una vez)
        self.box = pygame.Rect(0, 0, 0, 0)
        self.sv_rect = pygame.Rect(0, 0, 0, 0)
        self.hue_rect = pygame.Rect(0, 0, 0, 0)
        self.swatch_rect = pygame.Rect(0, 0, 0, 0)
        self.reset_rect = pygame.Rect(0, 0, 0, 0)
        self.hex_input = TextInput(self._hex_get, self._hex_set)

    # --- color actual, ida y vuelta HSV<->RGB --------------------------------
    def _rgb(self):
        r, g, b = colorsys.hsv_to_rgb(self.h, self.s, self.v)
        return (round(r * 255), round(g * 255), round(b * 255))

    def _set_rgb(self, rgb):
        r, g, b = (max(0, min(255, int(x))) / 255.0 for x in rgb)
        self.h, self.s, self.v = colorsys.rgb_to_hsv(r, g, b)
        self._apply()

    def _apply(self):
        if self.set:
            self.set(self._rgb())

    def _hex_get(self):
        return _rgb_to_hex(self._rgb())

    def _hex_set(self, s):
        rgb = _hex_to_rgb(s)
        if rgb is not None:
            self._set_rgb(rgb)

    def _reset(self):
        self._set_rgb(self.default)

    def open_for(self, get, setter, default, title):
        self.get, self.set = get, setter
        self.default, self.title = tuple(default), title
        self._set_rgb(tuple(get()))
        self.hex_input.focused = False
        self.open = True

    @property
    def editing(self) -> bool:
        return self.open and self.hex_input.focused

    # --- superficies cacheadas -----------------------------------------------
    def _sv_surface(self):
        """Cuadro Saturacion(x) x Valor(y) para el tono actual. Se construye con
        numpy (una malla HSV->RGB) y se cachea por tono cuantizado."""
        key = round(self.h, 4)
        if key == self._sv_key and self._sv_surf is not None:
            return self._sv_surf
        n = 100
        S, V = np.meshgrid(np.linspace(0, 1, n), np.linspace(1, 0, n))
        i = int(self.h * 6) % 6
        f = self.h * 6 - int(self.h * 6)
        p, q, t = V * (1 - S), V * (1 - f * S), V * (1 - (1 - f) * S)
        r, g, b = ((V, t, p), (q, V, p), (p, V, t),
                   (p, q, V), (t, p, V), (V, p, q))[i]
        arr = np.ascontiguousarray(np.transpose(np.stack([r, g, b], -1), (1, 0, 2)))
        arr = (np.clip(arr, 0, 1) * 255).astype(np.uint8)
        surf = pygame.surfarray.make_surface(arr)
        self._sv_surf = pygame.transform.smoothscale(surf, (self.SV, self.SV))
        self._sv_key = key
        return self._sv_surf

    def _hue_surface(self):
        if self._hue_surf is not None:
            return self._hue_surf
        n = 128
        cols = [colorsys.hsv_to_rgb(x, 1.0, 1.0)
                for x in np.linspace(0, 1, n, endpoint=False)]
        arr = (np.array(cols)[None, :, :] * 255).astype(np.uint8)   # (1, n, 3): x=1, y=n
        surf = pygame.surfarray.make_surface(np.ascontiguousarray(arr))
        self._hue_surf = pygame.transform.smoothscale(surf, (self.HUE_W, self.SV))
        return self._hue_surf

    # --- layout / eventos / dibujo -------------------------------------------
    def _layout(self, size) -> None:
        box_h = PAD + 24 + self.SV + 14 + 26 + 12 + 26 + PAD
        sw, sh = size
        self.box = pygame.Rect((sw - self.BOX_W) // 2, (sh - box_h) // 2, self.BOX_W, box_h)
        b = self.box
        top = b.y + PAD + 24
        self.sv_rect = pygame.Rect(b.x + PAD, top, self.SV, self.SV)
        self.hue_rect = pygame.Rect(self.sv_rect.right + 14, top, self.HUE_W, self.SV)
        self.swatch_rect = pygame.Rect(b.x + PAD, self.sv_rect.bottom + 14, self.BOX_W - 2 * PAD, 26)
        row_y = self.swatch_rect.bottom + 12
        self.hex_input.rect = pygame.Rect(b.x + PAD + 44, row_y, 120, 26)
        self.reset_rect = pygame.Rect(b.right - PAD - 72, row_y, 72, 26)

    def _set_sv(self, pos) -> None:
        self.s = max(0.0, min(1.0, (pos[0] - self.sv_rect.x) / self.sv_rect.w))
        self.v = max(0.0, min(1.0, 1.0 - (pos[1] - self.sv_rect.y) / self.sv_rect.h))
        self._apply()

    def _set_hue(self, pos) -> None:
        self.h = max(0.0, min(1.0, (pos[1] - self.hue_rect.y) / self.hue_rect.h))
        self._apply()

    def handle(self, ev, size) -> bool:
        """Consume TODO evento mientras esta abierto (modal). Clic fuera del
        cuadro, o ESC, lo cierra."""
        self._layout(size)
        inp = self.hex_input
        if inp.focused and ev.type == pygame.KEYDOWN:
            inp.handle(ev)
            return True
        if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
            if inp.focused:
                inp.focused = False
            else:
                self.open = False
            return True
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            if inp.focused and not inp.rect.collidepoint(ev.pos):
                inp.commit()
            if self.sv_rect.collidepoint(ev.pos):
                self._drag = "sv"
                self._set_sv(ev.pos)
            elif self.hue_rect.collidepoint(ev.pos):
                self._drag = "hue"
                self._set_hue(ev.pos)
            elif self.reset_rect.collidepoint(ev.pos):
                self._reset()
            elif inp.rect.collidepoint(ev.pos):
                inp.handle(ev)
            elif not self.box.collidepoint(ev.pos):
                self.open = False
            return True
        if ev.type == pygame.MOUSEMOTION and self._drag:
            self._set_sv(ev.pos) if self._drag == "sv" else self._set_hue(ev.pos)
            return True
        if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
            self._drag = None
            return True
        return True

    def draw(self, surf) -> None:
        self._layout(surf.get_size())
        overlay = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        overlay.fill(OVERLAY)
        surf.blit(overlay, (0, 0))

        pygame.draw.rect(surf, PANEL_BG, self.box, border_radius=10)
        pygame.draw.rect(surf, PANEL_BORDER, self.box, width=2, border_radius=10)
        title = self.font_title.render(self.title, True, TITLE)
        surf.blit(title, (self.box.x + PAD, self.box.y + PAD))

        # Cuadro SV + marcador (circulo con doble contorno para verse en claro y oscuro).
        surf.blit(self._sv_surface(), self.sv_rect.topleft)
        pygame.draw.rect(surf, SWATCH_BORDER, self.sv_rect, width=1)
        mx = self.sv_rect.x + int(self.s * self.sv_rect.w)
        my = self.sv_rect.y + int((1.0 - self.v) * self.sv_rect.h)
        pygame.draw.circle(surf, (20, 20, 20), (mx, my), 6, 2)
        pygame.draw.circle(surf, (245, 245, 245), (mx, my), 6, 1)

        # Tira de tono + marcador horizontal.
        surf.blit(self._hue_surface(), self.hue_rect.topleft)
        pygame.draw.rect(surf, SWATCH_BORDER, self.hue_rect, width=1)
        hy = self.hue_rect.y + int(self.h * self.hue_rect.h)
        pygame.draw.rect(surf, (245, 245, 245),
                         (self.hue_rect.x - 2, hy - 2, self.hue_rect.w + 4, 4), 1)

        # Swatch del color solido elegido.
        pygame.draw.rect(surf, self._rgb(), self.swatch_rect, border_radius=5)
        pygame.draw.rect(surf, SWATCH_BORDER, self.swatch_rect, width=1, border_radius=5)

        # Fila inferior: etiqueta + campo HEX y boton de reset.
        lbl = self.font.render("HEX", True, LABEL)
        surf.blit(lbl, (self.box.x + PAD, self.hex_input.rect.centery - lbl.get_height() // 2))
        self.hex_input.draw(surf, self.font)
        mouse = pygame.mouse.get_pos()
        pygame.draw.rect(surf, BTN_HOVER if self.reset_rect.collidepoint(mouse) else BTN_BG,
                         self.reset_rect, border_radius=5)
        rt = self.font.render("reset", True, BTN_TEXT)
        surf.blit(rt, (self.reset_rect.centerx - rt.get_width() // 2,
                       self.reset_rect.centery - rt.get_height() // 2))


class SettingsPanel:
    """Modal centrado. Se abre/cierra con toggle(); cuando esta abierto,
    consume los eventos de mouse que caen sobre el (los clics fuera lo cierran).
    """

    def __init__(self, engine, view, thumb_mode_labels, visualizations=(),
                 on_source_change: Callable[[], None] | None = None):
        self.engine = engine
        self.view = view          # objeto con show_metadata, thumb_mode, max_bar_height
        # Se invoca al cambiar de fuente para persistir el ajuste al instante (el
        # resto del estado se guarda al cerrar el panel; la fuente tambien puede
        # cambiar por atajo de teclado, asi que su guardado no espera al cierre).
        self._on_source_change = on_source_change or (lambda: None)
        self.open = False
        self.rect = pygame.Rect(0, 0, PANEL_W, 0)
        self.font = pygame.font.SysFont("consolas", 14)
        self.font_title = pygame.font.SysFont("consolas", 16, bold=True)
        # Picker de color modal, compartido por los tres swatches (grave/agudo/
        # medio). Se abre al clic de un swatch y escribe su color en caliente.
        self.picker = ColorPicker(self.font, self.font_title)

        eng = engine
        # Monitores disponibles para anclar la pantalla completa (F11). La fila
        # solo aparece si hay mas de uno; el label muestra el indice y su tamano.
        n_disp = pygame.display.get_num_displays()
        disp_sizes = pygame.display.get_desktop_sizes()
        disp_labels = [f"{i + 1}: {w}x{h}" for i, (w, h) in enumerate(disp_sizes)]

        # --- pestana AUDIO: todo lo del motor de sonido/analisis --------------
        # note_lo/hi viajan como texto ('C0'); el slider trabaja en MIDI y
        # convierte al vuelo. parse_note('C0')=12, parse_note('F#10')=138.
        audio = [
            # La lista de fuentes se recalcula en vivo: fb2k solo figura si el
            # toggle de abajo esta encendido (available_sources decide).
            _Row("fuente", Stepper(lambda: eng.source_name, self._set_source,
                                   lambda: config.available_sources(eng.fb2k_enabled))),
            # fb2k es nicho (levanta su propio servidor WebSocket): oculta por
            # defecto. Encenderlo la agrega a la lista de arriba; apagarlo estando
            # en fb2k devuelve la fuente al default (ver _set_allow_fb2k). fb2k es
            # de Windows (foobar2000): el toggle ni aparece en Linux.
            _Row("habilitar fb2k", Toggle(lambda: eng.fb2k_enabled, self._set_allow_fb2k),
                 visible=lambda: is_available("fb2k")),
            _Row("attack", Slider(lambda: eng.attack_ms,
                                  lambda v: setattr(eng, "attack_ms", v),
                                  1, 500, step=1, fmt=lambda v: f"{int(v)} ms")),
            _Row("decay", Slider(lambda: eng.decay_ms,
                                 lambda v: setattr(eng, "decay_ms", v),
                                 0, 1000, step=5, fmt=lambda v: f"{int(v)} ms")),
            _Row("distribucion", Stepper(lambda: eng.distribution,
                                         lambda v: eng.reconfigure_analysis(distribution=v),
                                         ["log", "octaves"])),
            _Row("bandas", Slider(lambda: eng.n_bands_req,
                                  lambda v: eng.reconfigure_analysis(n_bands=v),
                                  8, 256, step=1),
                 visible=lambda: eng.distribution == "log"),
            _Row("nota grave", Slider(lambda: parse_note(eng.note_lo),
                                      lambda v: eng.reconfigure_analysis(note_lo=midi_to_name(v)),
                                      12, 138, step=1, fmt=lambda v: midi_to_name(int(v))),
                 visible=lambda: eng.distribution == "octaves"),
            _Row("nota aguda", Slider(lambda: parse_note(eng.note_hi),
                                      lambda v: eng.reconfigure_analysis(note_hi=midi_to_name(v)),
                                      12, 138, step=1, fmt=lambda v: midi_to_name(int(v))),
                 visible=lambda: eng.distribution == "octaves"),
            _Row("bandas/oct", Slider(lambda: eng.bands_per_octave,
                                      lambda v: eng.reconfigure_analysis(bands_per_octave=v),
                                      1, 48, step=1),
                 visible=lambda: eng.distribution == "octaves"),
            _Row("afinacion", Slider(lambda: eng.tuning,
                                     lambda v: eng.reconfigure_analysis(tuning=v),
                                     400, 480, step=0.5, integer=False,
                                     fmt=lambda v: f"{v:.1f} Hz"),
                 visible=lambda: eng.distribution == "octaves"),
        ]

        # --- pestana VISUAL: presentacion general + que visualizaciones se ven -
        visual = [
            _Row("host", TextInput(lambda: view.host,
                                   lambda v: setattr(view, "host", v),
                                   placeholder="ip del host")),
            _Row("metadata", Toggle(lambda: view.show_metadata,
                                    lambda v: setattr(view, "show_metadata", v))),
            # Interruptores maestros de los overlays de texto de la esquina. El
            # detalle de que valores muestra el HUD de datos vive en su propia
            # pestana ("datos"); aca solo se prende/apaga toda la linea.
            _Row("datos", Toggle(lambda: view.show_hud,
                                 lambda v: setattr(view, "show_hud", v))),
            _Row("atajos", Toggle(lambda: view.show_keybinds,
                                  lambda v: setattr(view, "show_keybinds", v))),
            _Row("pantalla", Stepper(lambda: view.fullscreen_display,
                                     lambda v: setattr(view, "fullscreen_display", v),
                                     list(range(n_disp)), disp_labels),
                 visible=lambda: n_disp > 1),
            # Ventana sin bordes (frameless). Se aplica al cerrar el panel: el
            # visualizador quita el marco in situ y estira la ventana hacia arriba
            # para recuperar el alto de la title bar (en frameless Windows no deja
            # redimensionar, asi que ese alto se compensa en vez de perderse). Sin
            # title bar, la ventana se arrastra con clic-y-arrastre en cualquier
            # parte. F11 (pantalla completa) manda por encima de este modo.
            _Row("sin bordes", Toggle(lambda: view.frameless,
                                      lambda v: setattr(view, "frameless", v))),
            # Ventana siempre encima (always-on-top). Tambien con la tecla T.
            _Row("siempre visible", Toggle(lambda: view.always_on_top,
                                           lambda v: setattr(view, "always_on_top", v))),
        ]

        # --- pestana CARATULA: como se muestra la caratula y de que color tine ---
        # La vista/tamano del disco y los filtros del extractor de color de la
        # caratula. Los toggles de color se aplican al cerrar el panel (re-extraen
        # la paleta de la pista actual); el fallback por defecto, apagado, hace que
        # se pinten los colores crudos extraidos.
        caratula = [
            _Row("vista", Stepper(lambda: view.thumb_mode,
                                  lambda v: setattr(view, "thumb_mode", v),
                                  list(range(len(thumb_mode_labels))), thumb_mode_labels)),
            _Row("tamano disco", Slider(lambda: view.vinyl_scale,
                                        lambda v: setattr(view, "vinyl_scale", v),
                                        0.3, 1.0, step=0.05, integer=False,
                                        fmt=lambda v: f"{v:.2f}x")),
            _Row("color estricto", Toggle(lambda: view.palette_strict,
                                          lambda v: setattr(view, "palette_strict", v))),
            _Row("color permisivo", Toggle(lambda: view.palette_relaxed,
                                           lambda v: setattr(view, "palette_relaxed", v))),
            _Row("color fallback", Toggle(lambda: view.palette_default_fallback,
                                          lambda v: setattr(view, "palette_default_fallback", v))),
        ]
        # --- pestana COLORES: colores base del modo normal (no caratula) -------
        # Tres swatches (grave/agudo y un 3er color opcional) que abren el picker
        # HSV; abajo, la barra de preview del degradado con su selector de modo.
        # El 3er color solo aparece si esta activado y solo pinta en degradado.
        def _color_swatch(attr, default, title):
            get = lambda a=attr: getattr(view, a)
            setr = lambda c, a=attr: setattr(view, a, c)
            return Swatch(get, lambda g=get, s=setr, d=default, t=title:
                          self.picker.open_for(g, s, d, t))
        colores = [
            _Row("grave", _color_swatch("color_lo", config.DEFAULT_COLOR_LO, "color grave")),
            _Row("agudo", _color_swatch("color_hi", config.DEFAULT_COLOR_HI, "color agudo")),
            _Row("3er color", Toggle(lambda: view.color_use_mid,
                                     lambda v: setattr(view, "color_use_mid", v))),
            _Row("medio", _color_swatch("color_mid", config.DEFAULT_COLOR_MID, "color medio"),
                 visible=lambda: view.color_use_mid),
            _Row("degradado", Stepper(lambda: view.colors_gradient_mode,
                                      lambda v: setattr(view, "colors_gradient_mode", v),
                                      GRADIENT_MODES, GRADIENT_LABELS)),
            _Row(None, GradientPreview(view)),
        ]

        # --- pestana DATOS: que valores muestra el HUD de la esquina -----------
        # El interruptor maestro (mostrar/ocultar toda la linea) vive en "visual";
        # aca se elige, valor por valor, que aparece cuando el HUD esta activo.
        def _hud(attr):
            return Toggle(lambda a=attr: getattr(view, a),
                          lambda v, a=attr: setattr(view, a, v))
        data = [
            _Row("fuente", _hud("hud_source")),
            _Row("frecuencia", _hud("hud_rate")),
            _Row("canales", _hud("hud_channels")),
            _Row("analisis", _hud("hud_analysis")),
            _Row("ataque/caida", _hud("hud_ballistics")),
            _Row("fps", _hud("hud_fps")),
        ]

        # Un interruptor por visualizacion registrada. El id se ata por argumento
        # por defecto para que cada lambda capture el suyo (no el ultimo del bucle).
        for viz in visualizations:
            visual.append(
                _Row(viz.label, Toggle(lambda vid=viz.id: view.enabled_viz.get(vid, False),
                                       lambda v, vid=viz.id: view.enabled_viz.__setitem__(vid, v))))

        # (label, filas) por pestana. Las dos fijas primero; luego una por cada
        # visualizacion que declare ajustes propios (settings()).
        self.tabs: list[tuple[str, list[_Row]]] = [
            ("audio", audio), ("visual", visual), ("caratula", caratula),
            ("colores", colores), ("datos", data)]
        for viz in visualizations:
            rows = [self._row_from_spec(spec) for spec in viz.settings()]
            if rows:
                self.tabs.append((viz.label, rows))
        self.active_tab = 0
        self._tab_rects: list[pygame.Rect] = []

    def _row_from_spec(self, spec) -> "_Row":
        """Traduce un spec de ajuste (declarado por la visualizacion) al widget
        correspondiente, cableado por getattr/setattr al atributo del ViewState."""
        view = self.view
        get = lambda a=spec.attr: getattr(view, a)
        setr = lambda v, a=spec.attr: setattr(view, a, v)
        if isinstance(spec, SliderSetting):
            return _Row(spec.label, Slider(get, setr, spec.lo, spec.hi, step=spec.step,
                                           integer=spec.integer, fmt=spec.fmt))
        if isinstance(spec, StepperSetting):
            return _Row(spec.label, Stepper(get, setr, list(spec.values), spec.labels))
        if isinstance(spec, ToggleSetting):
            return _Row(spec.label, Toggle(get, setr))
        raise TypeError(f"spec de ajuste no soportado: {type(spec).__name__}")

    def toggle(self) -> None:
        self.open = not self.open

    def _set_source(self, name) -> None:
        try:
            self.engine.set_source(name)   # hot-swap, igual que el atajo 1/2/3/4
            self._on_source_change()       # persiste la fuente sin esperar al cierre
        except Exception as exc:
            print(f"no se pudo cambiar de fuente: {exc}")

    def _set_allow_fb2k(self, on: bool) -> None:
        self.engine.fb2k_enabled = on
        # Si se apaga con fb2k como fuente activa, ya no esta en la lista ofrecida:
        # la sacamos de encima volviendo al default (esto tambien persiste). En
        # cualquier otro caso basta con guardar el flag al instante.
        if not on and self.engine.source_name == "fb2k":
            self._set_source(config.DEFAULTS["source"])
        else:
            self._on_source_change()

    def _visible_rows(self):
        """Filas visibles de la pestana activa."""
        return [r for r in self.tabs[self.active_tab][1] if r.visible()]

    def _focused_input(self):
        """El campo de texto enfocado (si hay uno), o None."""
        for r in self._visible_rows():
            if isinstance(r.control, TextInput) and r.control.focused:
                return r.control
        return None

    @property
    def editing(self) -> bool:
        """True si hay un campo de texto capturando el teclado, o si el picker de
        color esta abierto (es modal: se lleva todo el input). El visualizador lo
        consulta para no robar TAB/F11/atajos mientras tanto."""
        if not self.open:
            return False
        if self.picker.open:
            return True
        return self._focused_input() is not None

    def _layout(self, size) -> None:
        rows = self._visible_rows()
        title_h = 30
        h = PAD + title_h + TAB_H + len(rows) * ROW_H + PAD
        w, sh = size
        self.rect = pygame.Rect((w - PANEL_W) // 2, (sh - h) // 2, PANEL_W, h)

        # Barra de pestanas: reparte el ancho util en partes iguales.
        tabs_y = self.rect.y + PAD + title_h
        avail = PANEL_W - 2 * PAD
        tw = avail / len(self.tabs)
        self._tab_rects = [
            pygame.Rect(int(self.rect.x + PAD + i * tw), tabs_y, int(tw) - 3, TAB_H - 6)
            for i in range(len(self.tabs))
        ]

        y = tabs_y + TAB_H
        cx = self.rect.x + PAD + LABEL_W
        cw = self.rect.right - PAD - cx
        for r in rows:
            # Fila sin etiqueta (label None): el control ocupa todo el ancho util
            # (p.ej. la barra de preview del degradado). Si no, va en la columna
            # de la derecha, dejando LABEL_W para la etiqueta.
            if r.label is None:
                r.control.rect = pygame.Rect(self.rect.x + PAD, y, PANEL_W - 2 * PAD, ROW_H)
            else:
                r.control.rect = pygame.Rect(cx, y, cw, ROW_H)
            y += ROW_H

    def handle(self, ev, size) -> bool:
        """Devuelve True si consumio el evento (para que el visualizador no lo
        procese como atajo). Un clic fuera del panel lo cierra."""
        if not self.open:
            return False
        self._layout(size)

        # Picker de color abierto: es modal, se lleva TODO el evento (incluidos
        # clics fuera de el, que lo cierran) antes que cualquier otra cosa.
        if self.picker.open:
            return self.picker.handle(ev, size)

        # Con un campo de texto enfocado el teclado es suyo (typing/Enter/Esc);
        # no lo tratamos como cierre de panel ni atajo.
        focused = self._focused_input()
        if focused is not None and ev.type == pygame.KEYDOWN:
            focused.handle(ev)
            return True
        # Un click fuera del campo enfocado confirma su edicion (y sigue el flujo).
        if (focused is not None and ev.type == pygame.MOUSEBUTTONDOWN
                and not focused.rect.collidepoint(ev.pos)):
            focused.commit()

        if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
            self.open = False
            return True

        # Click en una pestana: cambia de seccion.
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            for i, tr in enumerate(self._tab_rects):
                if tr.collidepoint(ev.pos):
                    self.active_tab = i
                    return True

        for r in self._visible_rows():
            if r.control.handle(ev):
                return True

        if ev.type == pygame.MOUSEBUTTONDOWN:
            if not self.rect.collidepoint(ev.pos):
                self.open = False   # clic fuera: cerrar
            return True             # todo clic con el panel abierto es "suyo"

        # Traga tambien motion/up mientras esta abierto: el fondo no debe reaccionar.
        if ev.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONUP):
            return True
        return False

    def draw(self, surf) -> None:
        if not self.open:
            return
        self._layout(surf.get_size())

        overlay = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        overlay.fill(OVERLAY)
        surf.blit(overlay, (0, 0))

        pygame.draw.rect(surf, PANEL_BG, self.rect, border_radius=10)
        pygame.draw.rect(surf, PANEL_BORDER, self.rect, width=2, border_radius=10)

        title = self.font_title.render("configuracion", True, TITLE)
        surf.blit(title, (self.rect.x + PAD, self.rect.y + PAD))
        hint = self.font.render("TAB / ESC cerrar", True, LABEL)
        surf.blit(hint, (self.rect.right - PAD - hint.get_width(), self.rect.y + PAD + 2))

        # Pestanas: la activa mas clara y con un subrayado de acento.
        mouse = pygame.mouse.get_pos()
        for i, (label, _) in enumerate(self.tabs):
            tr = self._tab_rects[i]
            active = i == self.active_tab
            bg = TAB_ACTIVE_BG if (active or tr.collidepoint(mouse)) else TAB_INACTIVE_BG
            pygame.draw.rect(surf, bg, tr, border_radius=6)
            if active:
                pygame.draw.line(surf, TAB_ACCENT, (tr.x + 6, tr.bottom - 1),
                                 (tr.right - 6, tr.bottom - 1), 2)
            t = self.font.render(label, True, TAB_TEXT_ON if active else TAB_TEXT_OFF)
            surf.blit(t, (tr.centerx - t.get_width() // 2, tr.centery - t.get_height() // 2))

        for r in self._visible_rows():
            cr = r.control.rect
            if r.label is not None:
                lbl = self.font.render(r.label, True, LABEL)
                surf.blit(lbl, (self.rect.x + PAD, cr.centery - lbl.get_height() // 2))
            r.control.draw(surf, self.font)

        if self.picker.open:
            self.picker.draw(surf)   # modal sobre el panel
