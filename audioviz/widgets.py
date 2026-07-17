"""Controles genericos del panel de configuracion (pygame no trae widgets).

Filosofia: los widgets NO guardan estado propio. Leen y escriben la unica
fuente de verdad (el Engine y el estado de la vista) via un getter y un setter.
Asi el panel siempre refleja lo real —incluso si un atajo de teclado cambia el
mismo valor por fuera— y cada cambio se aplica en caliente al instante.

Cada control expone la misma interfaz minima que el panel espera:
    .rect              rect asignado en cada layout
    .handle(ev)        atiende un evento; devuelve True si lo consumio
    .draw(surf, font)  se dibuja dentro de su rect

Son agnosticos de color/estilo salvo por las constantes de theme. Los controles
especificos de color (swatch, picker HSV) viven aparte, en color_picker.
"""

from __future__ import annotations

import pygame

from .theme import (BTN_BG, BTN_HOVER, BTN_TEXT, KNOB, LABEL, PANEL_BORDER,
                    TOGGLE_KNOB, TOGGLE_OFF, TOGGLE_ON, TRACK, TRACK_FILL, VALUE)


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
