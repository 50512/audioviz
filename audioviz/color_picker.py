"""Colores personalizados del modo normal: los controles para elegirlos y verlos.

Aisla del panel todo lo especifico de color, que de otro modo lo agrandaria sin
razon (numpy, colorsys, superficies HSV):

    Swatch          recuadro de color clicable que abre el picker
    GradientPreview barra que muestra como interpolan los colores elegidos
    ColorPicker     selector HSV modal (cuadro SV + tira de tono + HEX + reset)

Todos siguen la misma interfaz que el resto de controles del panel (rect /
handle / draw), asi el panel los trata igual que a un Slider o un Toggle.
"""

from __future__ import annotations

import colorsys

import numpy as np
import pygame
import pygame.surfarray

from .theme import (BTN_BG, BTN_HOVER, BTN_TEXT, LABEL, OVERLAY, PAD, PANEL_BG,
                    PANEL_BORDER, SWATCH_BORDER, TITLE)
from .visualizations.gradient import build_gradient
from .widgets import TextInput


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
