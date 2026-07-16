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

from typing import Callable

import pygame

from .analysis import NOTES, parse_note
from .visualizations.base import SliderSetting, StepperSetting, ToggleSetting

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
PANEL_W = 430


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
    donde una lista desplegable no cabria (fuente, distribucion, vista)."""

    def __init__(self, get, setter, values, labels=None):
        self.get, self.set = get, setter
        self.values = list(values)
        self.labels = list(labels) if labels else [str(v) for v in values]
        self.rect = pygame.Rect(0, 0, 0, 0)

    def _btns(self):
        r = self.rect
        left = pygame.Rect(r.x, r.centery - 12, 24, 24)
        right = pygame.Rect(r.right - 24, r.centery - 12, 24, 24)
        return left, right

    def _index(self) -> int:
        try:
            return self.values.index(self.get())
        except ValueError:
            return 0

    def handle(self, ev) -> bool:
        if ev.type != pygame.MOUSEBUTTONDOWN or ev.button != 1:
            return False
        left, right = self._btns()
        n = len(self.values)
        if left.collidepoint(ev.pos):
            self.set(self.values[(self._index() - 1) % n])
            return True
        if right.collidepoint(ev.pos):
            self.set(self.values[(self._index() + 1) % n])
            return True
        return False

    def draw(self, surf, font):
        left, right = self._btns()
        mouse = pygame.mouse.get_pos()
        for r, glyph in ((left, "<"), (right, ">")):
            pygame.draw.rect(surf, BTN_HOVER if r.collidepoint(mouse) else BTN_BG,
                             r, border_radius=5)
            g = font.render(glyph, True, BTN_TEXT)
            surf.blit(g, (r.centerx - g.get_width() // 2, r.centery - g.get_height() // 2))
        label = self.labels[self._index()]
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
            _Row("fuente", Stepper(lambda: eng.source_name, self._set_source,
                                   ["loopback", "fb2k", "mic", "tone"])),
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
            _Row("vista", Stepper(lambda: view.thumb_mode,
                                  lambda v: setattr(view, "thumb_mode", v),
                                  list(range(len(thumb_mode_labels))), thumb_mode_labels)),
            _Row("tamano disco", Slider(lambda: view.vinyl_scale,
                                        lambda v: setattr(view, "vinyl_scale", v),
                                        0.3, 1.0, step=0.05, integer=False,
                                        fmt=lambda v: f"{v:.2f}x")),
            _Row("pantalla", Stepper(lambda: view.fullscreen_display,
                                     lambda v: setattr(view, "fullscreen_display", v),
                                     list(range(n_disp)), disp_labels),
                 visible=lambda: n_disp > 1),
        ]
        # Un interruptor por visualizacion registrada. El id se ata por argumento
        # por defecto para que cada lambda capture el suyo (no el ultimo del bucle).
        for viz in visualizations:
            visual.append(
                _Row(viz.label, Toggle(lambda vid=viz.id: view.enabled_viz.get(vid, False),
                                       lambda v, vid=viz.id: view.enabled_viz.__setitem__(vid, v))))

        # (label, filas) por pestana. Las dos fijas primero; luego una por cada
        # visualizacion que declare ajustes propios (settings()).
        self.tabs: list[tuple[str, list[_Row]]] = [("audio", audio), ("visual", visual)]
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
        """True si hay un campo de texto capturando el teclado. El visualizador
        lo consulta para no robar TAB/F11/atajos mientras se escribe."""
        return self.open and self._focused_input() is not None

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
            r.control.rect = pygame.Rect(cx, y, cw, ROW_H)
            y += ROW_H

    def handle(self, ev, size) -> bool:
        """Devuelve True si consumio el evento (para que el visualizador no lo
        procese como atajo). Un clic fuera del panel lo cierra."""
        if not self.open:
            return False
        self._layout(size)

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
            lbl = self.font.render(r.label, True, LABEL)
            surf.blit(lbl, (self.rect.x + PAD, cr.centery - lbl.get_height() // 2))
            r.control.draw(surf, self.font)
