"""Menu de configuracion modal (pygame). Se dibuja a mano —pygame no trae
widgets— pero encaja con el resto del visualizador, que ya pinta y cachea todo
manualmente, sin dependencias extra.

Este modulo arma la estructura (pestanas, filas, layout, ruteo de eventos) y
cablea cada control a su atributo del Engine o de la vista. Las piezas viven
aparte para no volverlo un monolito:

    theme         paleta y medidas compartidas
    widgets       controles genericos (Slider, Stepper, Toggle, TextInput)
    color_picker  lo especifico de color (Swatch, GradientPreview, ColorPicker)

Filosofia (comun a todos los controles): NO guardan estado propio. Leen y
escriben la unica fuente de verdad (el Engine y el estado de la vista) via un
getter y un setter. Asi el panel siempre refleja lo real —incluso si un atajo
de teclado cambia el mismo valor por fuera— y cada cambio se aplica al instante.

Se controla con mouse. Los atajos de teclado directos del visualizador siguen
funcionando en paralelo; este panel solo los hace descubribles y ajustables
con precision (sliders) o sin memorizar teclas (steppers).
"""

from __future__ import annotations

from typing import Callable

import pygame

from . import config
from .analysis import NOTES, parse_note
from .color_picker import ColorPicker, GradientPreview, Swatch
from .sources import is_available
from .theme import (LABEL, LABEL_W, OVERLAY, PAD, PANEL_BG, PANEL_BORDER,
                    PANEL_W, ROW_H, TAB_ACCENT, TAB_ACTIVE_BG, TAB_H,
                    TAB_INACTIVE_BG, TAB_TEXT_OFF, TAB_TEXT_ON, TITLE)
from .visualizations.base import SliderSetting, StepperSetting, ToggleSetting
from .visualizations.gradient import GRADIENT_LABELS, GRADIENT_MODES
from .widgets import Slider, Stepper, TextInput, Toggle


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
