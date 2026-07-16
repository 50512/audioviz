"""GUI de referencia (pygame). Se engancha al Engine y no sabe NADA de sockets,
WASAPI, FFT ni ventanas de Hann. Solo pide frames y dibuja.

    python -m audioviz.gui_pygame --source loopback --attack-ms 20 --decay-ms 30
    python -m audioviz.gui_pygame --source fb2k
    python -m audioviz.gui_pygame --source mic

Teclas:
    1 / 2 / 3 / 4   fuente: loopback / fb2k / mic / tone   (hot-swap, sin reiniciar)
    Q / A       attack  -/+
    W / S       decay   -/+
    M           metadata (now playing) on/off
    C           vista: caratula+disco / disco / caratula / nada
    T           ventana siempre encima (always-on-top) on/off
    F11         pantalla completa on/off
    TAB         panel de configuracion (mouse) on/off
    ESC         salir (o cerrar el panel si esta abierto)

Requiere: pip install pygame-ce websockets
"""

from __future__ import annotations

import argparse
import ctypes
import glob
import io
import math
import os
import warnings

import numpy as np
import pygame
import pygame.freetype

from . import config
from .engine import Engine
from .metadata import MetadataMonitor
from .settings_panel import SettingsPanel
from .thumbnail import NO_ART, ThumbnailMonitor
from .visualizations import RenderContext, build_visualizations
from .visualizations.bars import (BARS_GRADIENT_MODES, BARS_SCOPES,
                                  DEFAULT_BARS_GRADIENT, DEFAULT_BARS_SCOPE)
from .visualizations.circle_bars import DEFAULT_RADIUS_MULT
from .visualizations.gradient import DEFAULT_GRADIENT, GRADIENT_MODES
from .visualizations.palette import extract_palette

BG = (14, 14, 18)
GRID = (34, 34, 42)
CH_COLORS = [(90, 200, 250), (250, 130, 110), (150, 230, 140), (240, 200, 90)]
TEXT = (150, 150, 165)
META_BG = (24, 24, 30)
META_TEXT = (225, 225, 232)
META_TEXT_PAUSED = (140, 140, 150)
META_BAR_H = 28
# La barra de now-playing puede traer cualquier idioma. Consolas (primaria)
# mantiene el look monoespaciado del ASCII; las de reserva cubren japones
# (kana + kanji + Han, asi que tambien chino) y coreano (hangul). pygame.font
# no encadena glifos, asi que lo hacemos nosotros via pygame.freetype.
META_FONT_CHAIN = ["consolas", "yugothicui", "malgungothic"]
# --- caratula (label sobre el vinilo) ---
THUMB_BACKDROP = (34, 36, 48, 235)   # relleno del marco: mas notorio que el fondo (14,14,18)
THUMB_BORDER = (96, 108, 142)        # trazo del borde: acento frio, claro pero oscuro
THUMB_BORDER_W = 3
THUMB_MARGIN = 12          # grosor del marco alrededor de la caratula
THUMB_RADIUS_FRAC = 0.05   # radio de esquinas redondeadas, como fraccion del lado
THUMB_FRACTION = 0.32      # lado del recuadro de la caratula, como fraccion del lado menor de la ventana
THUMB_MIN_PX = 64
THUMB_MAX_PX = 320

# --- disco de vinilo (detras de la caratula) ---
VINYL_ART_RATIO = 1.7      # diametro del disco = lado de la caratula * esto
VINYL_BODY = (20, 20, 26)
VINYL_GROOVE = (44, 46, 58)
VINYL_LABEL = (30, 31, 40)   # circulo central (tapado por la caratula si existe)
VINYL_SHEEN = (46, 50, 66)   # brillo aditivo que hace visible el giro
VINYL_SPIN_DPS = 45.0        # grados por segundo mientras hay reproduccion

WS_PORT = 25012            # puerto de los servicios de metadata/caratula (fijo)


def build_urls(host: str) -> tuple[str, str]:
    """(url_metadata, url_thumbnail) desde el host (IP o hostname). El puerto y
    las rutas son el formato de la API. Host vacio -> ('', '') = sin conexion."""
    host = host.strip()
    if not host:
        return "", ""
    base = f"ws://{host}:{WS_PORT}/ws"
    return f"{base}/media-info", f"{base}/thumbnail"


def build_monitors(host: str):
    """Crea y arranca los monitores para el host. Devuelve (metadata, thumbnail),
    cada uno None si el host esta vacio (sin conexion)."""
    murl, turl = build_urls(host)
    meta = MetadataMonitor(murl) if murl else None
    if meta:
        meta.start()
    thumb = ThumbnailMonitor(turl) if turl else None
    if thumb:
        thumb.start()
    return meta, thumb


_sdl_cache: object | None = None


def _sdl_lib():
    """La SDL2.dll que trae pygame, cargada por ctypes y cacheada (None si no se
    puede). La usamos para funciones que pygame no expone: origen de cada monitor,
    alto del marco de la ventana y posicion global del cursor. El sentinel False
    evita reintentar una carga que ya fallo."""
    global _sdl_cache
    if _sdl_cache is None:
        try:
            dll = os.path.join(os.path.dirname(pygame.__file__), "SDL2.dll")
            if not os.path.exists(dll):
                cands = glob.glob(os.path.join(os.path.dirname(pygame.__file__), "SDL2*.dll"))
                dll = cands[0] if cands else None
            _sdl_cache = ctypes.CDLL(dll) if dll else False
        except Exception:
            _sdl_cache = False
    return _sdl_cache or None


_display_bounds_cache: list[tuple[int, int, int, int]] | None = None


def _load_display_bounds() -> list[tuple[int, int, int, int]]:
    """Origen y tamano (x, y, w, h) de cada monitor, via SDL_GetDisplayBounds.
    pygame solo expone get_desktop_sizes() (tamanos, sin origen), pero para
    anclar la pantalla completa a un monitor concreto necesitamos su esquina en
    el escritorio virtual. Llamamos a la SDL2.dll que trae pygame por ctypes.
    Devuelve [] si algo falla (el llamador cae a un valor por defecto)."""
    sdl = _sdl_lib()
    if sdl is None:
        return []
    try:
        class _Rect(ctypes.Structure):
            _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int),
                        ("w", ctypes.c_int), ("h", ctypes.c_int)]

        sdl.SDL_GetDisplayBounds.argtypes = [ctypes.c_int, ctypes.POINTER(_Rect)]
        out = []
        for i in range(pygame.display.get_num_displays()):
            r = _Rect()
            if sdl.SDL_GetDisplayBounds(i, ctypes.byref(r)) == 0:
                out.append((r.x, r.y, r.w, r.h))
        return out
    except Exception:
        return []


def _window_top_border(win) -> int:
    """Alto en px del marco superior (title bar + borde) de la ventana, via
    SDL_GetWindowBordersSize. Se consulta con la ventana AUN con bordes: es el
    desplazamiento que hay que compensar al pasar a frameless (subir la ventana
    ese alto). Cae a 31 (title bar tipica a 100%% DPI en Windows) si SDL no ayuda."""
    sdl = _sdl_lib()
    if sdl is not None:
        try:
            sdl.SDL_GetWindowFromID.restype = ctypes.c_void_p
            sdl.SDL_GetWindowFromID.argtypes = [ctypes.c_uint32]
            wptr = sdl.SDL_GetWindowFromID(ctypes.c_uint32(win.id))
            top = ctypes.c_int(); left = ctypes.c_int()
            bottom = ctypes.c_int(); right = ctypes.c_int()
            sdl.SDL_GetWindowBordersSize.argtypes = \
                [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_int)] * 4
            rc = sdl.SDL_GetWindowBordersSize(wptr, ctypes.byref(top), ctypes.byref(left),
                                              ctypes.byref(bottom), ctypes.byref(right))
            if rc == 0 and top.value > 0:
                return top.value
        except Exception:
            pass
    return 31


def _global_mouse() -> tuple[int, int] | None:
    """Posicion absoluta del cursor en el escritorio (px), via SDL. Es
    independiente de la ventana, asi arrastrar no se realimenta cuando movemos la
    ventana bajo el puntero. None si SDL no coopera (el llamador no arrastra)."""
    sdl = _sdl_lib()
    if sdl is None:
        return None
    try:
        sdl.SDL_GetGlobalMouseState.argtypes = \
            [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        sdl.SDL_GetGlobalMouseState.restype = ctypes.c_uint32
        x = ctypes.c_int(); y = ctypes.c_int()
        sdl.SDL_GetGlobalMouseState(ctypes.byref(x), ctypes.byref(y))
        return (x.value, y.value)
    except Exception:
        return None


def _display_window():
    """pygame.Window de la ventana creada por el modulo display. Nos deja togglear
    el borde, mover y redimensionar SIN recrear la superficie (el mismo 'screen'
    sigue valido; cambiar win.size redimensiona la superficie del display in situ).
    Silencia el DeprecationWarning de mezclar Window con render por display module:
    es justo el uso soportado por from_display_module."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return pygame.Window.from_display_module()


def display_bounds(idx: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) del monitor idx (acotado a los disponibles). Si SDL no
    coopera, cae al monitor primario en (0,0)."""
    global _display_bounds_cache
    if _display_bounds_cache is None:
        _display_bounds_cache = _load_display_bounds()
    bounds = _display_bounds_cache
    if not bounds:
        w, h = pygame.display.get_desktop_sizes()[0]
        return (0, 0, w, h)
    return bounds[max(0, min(idx, len(bounds) - 1))]


class FallbackFont:
    """Renderiza texto eligiendo, por caracter, la primera fuente de la cadena
    que tenga ese glifo. Resuelve el caso de titulos con kanji/hangul/etc. que
    con una sola fuente saldrian como cajas o '?'. Cachea el ultimo render
    porque la metadata cambia rara vez."""

    def __init__(self, names, size, bold=False) -> None:
        self.fonts = []
        for name in names:
            f = pygame.freetype.SysFont(name, size, bold=bold)
            f.origin = True   # render_to posiciona por baseline -> alinea fuentes distintas
            self.fonts.append(f)
        self._cache_key = None
        self._cache_surf = None

    def _font_for(self, ch: str):
        for f in self.fonts:
            m = f.get_metrics(ch)
            if m and m[0] is not None:   # freetype devuelve None si falta el glifo
                return f
        return self.fonts[0]   # nadie lo tiene: caja de la primaria, mejor que nada

    def render(self, text: str, color) -> pygame.Surface:
        key = (text, color)
        if key == self._cache_key:
            return self._cache_surf

        # Agrupa caracteres consecutivos que usan la misma fuente.
        runs: list[tuple[str, object]] = []
        for ch in text:
            f = self._font_for(ch)
            if runs and runs[-1][1] is f:
                runs[-1] = (runs[-1][0] + ch, f)
            else:
                runs.append((ch, f))

        ascent = max((f.get_sized_ascender() for _, f in runs), default=1)
        descent = min((f.get_sized_descender() for _, f in runs), default=0)  # negativo
        advances = [sum(m[4] for m in f.get_metrics(run) if m) for run, f in runs]
        width = max(1, int(sum(advances)))
        height = max(1, int(ascent - descent))

        surf = pygame.Surface((width, height), pygame.SRCALPHA)
        x = 0.0
        for (run, f), adv in zip(runs, advances):
            f.render_to(surf, (int(x), ascent), run, color)
            x += adv

        self._cache_key, self._cache_surf = key, surf
        return surf


def draw_metadata_bar(surf, font, w, info) -> None:
    """Barra de "now playing" pegada al borde superior. info: MediaInfo.
    font es un FallbackFont (soporta glifos no latinos: kanji, hangul...)."""
    pygame.draw.rect(surf, META_BG, (0, 0, w, META_BAR_H))
    if not info.title and not info.artist:
        return
    label = " - ".join(p for p in (info.title, info.artist) if p)
    if not info.is_playing:
        label += "  (pausado)"
    color = META_TEXT if info.is_playing else META_TEXT_PAUSED
    text = font.render(label, color)
    x = max(8, (w - text.get_width()) // 2)
    y = (META_BAR_H - text.get_height()) // 2
    surf.blit(text, (x, y))


def thumb_box(w, h, scale: float = 1.0) -> int:
    """Lado del recuadro donde entra la caratula. El tamano "natural" se acota a
    THUMB_MIN/MAX_PX y luego se multiplica por `scale` (permite reducirlo por
    debajo del minimo natural a voluntad). Como el vinilo y el radio del circulo
    se derivan de este lado, escalarlo aca los reduce a todos en cascada."""
    natural = max(THUMB_MIN_PX, min(int(min(w, h) * THUMB_FRACTION), THUMB_MAX_PX))
    return max(1, int(natural * scale))


def fit_within(orig_size, box) -> tuple[int, int]:
    """Tamano que conserva el aspecto y cabe en un cuadrado de lado box."""
    ow, oh = orig_size
    scale = min(box / ow, box / oh)
    return max(1, int(ow * scale)), max(1, int(oh * scale))


def _rounded_mask(size, radius, ss=4) -> pygame.Surface:
    """Mascara blanca con esquinas redondeadas, suavizada por supersampling."""
    w, h = size
    big = pygame.Surface((w * ss, h * ss), pygame.SRCALPHA)
    pygame.draw.rect(big, (255, 255, 255, 255), big.get_rect(), border_radius=radius * ss)
    return pygame.transform.smoothscale(big, (w, h))


def round_corners(surf, radius) -> pygame.Surface:
    """Devuelve una copia de surf con las esquinas redondeadas (bordes suaves)."""
    out = surf.convert_alpha()
    mask = _rounded_mask(out.get_size(), radius)
    out.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    return out


def build_art_panel(art_surf, box) -> pygame.Surface:
    """Caratula escalada + esquinas redondeadas, montada sobre un marco
    redondeado (fondo THUMB_BACKDROP + borde THUMB_BORDER). Se rehace solo
    cuando cambia la imagen o el tamano de ventana, no por frame."""
    aw, ah = fit_within(art_surf.get_size(), box)
    art = pygame.transform.smoothscale(art_surf, (aw, ah))
    art = round_corners(art, max(1, int(min(aw, ah) * THUMB_RADIUS_FRAC)))

    m = THUMB_MARGIN
    pw, ph = aw + 2 * m, ah + 2 * m
    pr = max(1, int(min(pw, ph) * THUMB_RADIUS_FRAC))
    ss = 4
    big = pygame.Surface((pw * ss, ph * ss), pygame.SRCALPHA)
    pygame.draw.rect(big, THUMB_BACKDROP, big.get_rect(), border_radius=pr * ss)
    pygame.draw.rect(big, THUMB_BORDER, big.get_rect(),
                     width=THUMB_BORDER_W * ss, border_radius=pr * ss)
    panel = pygame.transform.smoothscale(big, (pw, ph))

    panel.blit(art, (m, m))
    return panel


def make_vinyl(diameter, ss=2) -> pygame.Surface:
    """Disco de vinilo: cuerpo + surcos + label central + brillo aditivo (que
    hace visible el giro). Renderizado a ss veces y reducido, para bordes
    suaves. Cacheado por diametro."""
    d = diameter
    big = d * ss
    surf = pygame.Surface((big, big), pygame.SRCALPHA)
    c = big // 2
    r = c - 1
    pygame.draw.circle(surf, VINYL_BODY, (c, c), r)

    step = max(2, int(r * 0.028))
    for gr in range(int(r * 0.34), r, step):
        pygame.draw.circle(surf, VINYL_GROOVE, (c, c), gr, ss)

    pygame.draw.circle(surf, VINYL_LABEL, (c, c), int(r * 0.33))
    pygame.draw.circle(surf, VINYL_BODY, (c, c), max(2, int(r * 0.02)))  # agujero del eje

    # Brillo: dos barras opuestas, difuminadas por reduccion/ampliacion. En
    # BLEND_RGB_ADD el alpha del destino no cambia, asi que fuera del disco
    # (alpha 0) el brillo no aparece: se recorta solo al circulo.
    sheen = pygame.Surface((big, big), pygame.SRCALPHA)
    bar_w = max(1, int(big * 0.09))
    for ang in (0.5, 0.5 + math.pi):
        dx, dy = math.cos(ang) * r, math.sin(ang) * r
        pygame.draw.line(sheen, VINYL_SHEEN, (c - dx, c - dy), (c + dx, c + dy), bar_w)
    blur = max(4, big // 6)
    sheen = pygame.transform.smoothscale(pygame.transform.smoothscale(sheen, (blur, blur)), (big, big))
    surf.blit(sheen, (0, 0), special_flags=pygame.BLEND_RGB_ADD)

    return pygame.transform.smoothscale(surf, (d, d))


class ViewState:
    """Estado de la vista que NO vive en el Engine (es pura presentacion).
    Lo comparten los atajos de teclado, el panel de configuracion y el codigo
    de dibujo: una unica fuente de verdad para que no se desincronicen."""

    def __init__(self, show_metadata: bool, thumb_mode: int, max_bar_height: float,
                 enabled_viz: dict[str, bool], circle_radius_mult: float = 1.1,
                 circle_max_height: float = 100.0, circle_gradient_mode: str = "rgb",
                 vinyl_scale: float = 1.0, bars_gradient_mode: str = "solid",
                 bars_gradient_scope: str = "channel", bars_use_cover: bool = False,
                 circle_use_cover: bool = False, bars_cover_2col: str = "gradient",
                 circle_symmetric: bool = False, host: str = "",
                 fullscreen_display: int = 0, palette_strict: bool = True,
                 palette_relaxed: bool = True, palette_default_fallback: bool = True,
                 frameless: bool = False, always_on_top: bool = False):
        self.show_metadata = show_metadata
        self.thumb_mode = thumb_mode
        # Tope de altura de las barras verticales, como % del alto de la pantalla.
        self.max_bar_height = max_bar_height
        # Que visualizaciones estan activas, por id. El visualizador dibuja todas
        # las activas (superpuestas); el panel de configuracion las alterna.
        self.enabled_viz = enabled_viz
        # Multiplicador del radio interior del circulo de barras (x el radio del
        # vinilo). Ajustable en caliente desde el panel.
        self.circle_radius_mult = circle_radius_mult
        # Tope de largo de las barras del circulo, como % del espacio disponible.
        # Independiente de max_bar_height: el circulo tiene menos recorrido radial
        # y compartir el limite le recortaria demasiado el rango de movimiento.
        self.circle_max_height = circle_max_height
        # Modo de degradado del circulo (rgb/warm/cool/oklab). Ajustable en vivo.
        self.circle_gradient_mode = circle_gradient_mode
        # Multiplicador del tamano del vinilo. Cascada: reduce el vinilo, la
        # caratula y el radio interior del circulo (todos derivan de thumb_box).
        self.vinyl_scale = vinyl_scale
        # Color de las barras verticales: modo (solid/rgb/warm/cool/oklch) y
        # alcance del degradado (channel = por canal, span = de extremo a extremo).
        self.bars_gradient_mode = bars_gradient_mode
        self.bars_gradient_scope = bars_gradient_scope
        # Usar la paleta de la caratula en vez de los colores por defecto, por
        # visualizacion. La cantidad de colores extraidos define el mapeo.
        self.bars_use_cover = bars_use_cover
        self.circle_use_cover = circle_use_cover
        # Caratula con 2 colores en barras: mezclarlos (gradient) o uno por canal (split).
        self.bars_cover_2col = bars_cover_2col
        # Circulo: color por posicion (simetrico, sin costura) en vez de por banda.
        self.circle_symmetric = circle_symmetric
        # Host (IP/hostname) de los servicios de metadata/caratula. Editable en el
        # panel; el visualizador reconstruye los monitores cuando cambia.
        self.host = host
        # Indice del monitor al que se ancla la pantalla completa (F11).
        self.fullscreen_display = fullscreen_display
        # Filtros del extractor de color de la caratula, cada uno on/off desde el
        # panel (ver extract_palette): 1er filtro estricto, 2do filtro (fallback
        # permisivo) y el fallback por defecto. Con este ultimo apagado el motor
        # pinta con los colores crudos extraidos, por mas inutilizables que sean.
        self.palette_strict = palette_strict
        self.palette_relaxed = palette_relaxed
        self.palette_default_fallback = palette_default_fallback
        # Ventana sin bordes (frameless windowed). Se aplica quitando el marco in
        # situ y estirando la ventana hacia arriba para recuperar el alto de la
        # title bar (en frameless Windows no permite redimensionar, asi que ese
        # alto se compensa). Sin title bar la ventana se arrastra a mano. F11
        # (pantalla completa) es un estado aparte que manda sobre este.
        self.frameless = frameless
        # Ventana siempre encima del resto (always-on-top). Independiente del
        # marco y de la pantalla completa; se reasigna tras recrear la ventana.
        self.always_on_top = always_on_top


def main() -> None:
    # Los flags de configuracion usan default=SUPPRESS: si no se pasan, NO
    # aparecen en args. Asi se distingue "el usuario lo puso" de "quedo en el
    # default", que es lo que permite la precedencia flag > archivo > default.
    # El `dest` de cada uno coincide con la clave del esquema en config.py.
    S = argparse.SUPPRESS
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=S, choices=["loopback", "fb2k", "mic", "tone"])
    ap.add_argument("--attack-ms", dest="attack_ms", type=float, default=S)
    ap.add_argument("--decay-ms", dest="decay_ms", type=float, default=S)
    ap.add_argument("--bands", dest="n_bands", type=int, default=S, help="solo para --dist log")
    ap.add_argument("--dist", dest="distribution", default=S, choices=["log", "octaves"])
    ap.add_argument("--note-lo", dest="note_lo", default=S)
    ap.add_argument("--note-hi", dest="note_hi", default=S)
    ap.add_argument("--bpo", dest="bands_per_octave", type=int, default=S, help="bandas por octava")
    ap.add_argument("--tuning", type=float, default=S)
    ap.add_argument("--max-bar-height", dest="max_bar_height", type=float, default=S,
                     help="altura maxima de las barras, como %% del alto de la pantalla (0-100)")
    ap.add_argument("--circle-radius-mult", dest="circle_radius_mult", type=float, default=S,
                     help="radio interior del circulo de barras, como multiplo del radio "
                          "del vinilo central (1.0-3.0)")
    ap.add_argument("--circle-max-height", dest="circle_max_height", type=float, default=S,
                     help="largo maximo de las barras del circulo, como %% del espacio "
                          "disponible hasta el borde (0-100)")
    ap.add_argument("--circle-gradient", dest="circle_gradient_mode", default=S, choices=GRADIENT_MODES,
                     help="modo de degradado del circulo: rgb (medio gris), warm "
                          "(via verde/amarillo), cool (via magenta), oklch (perceptual)")
    ap.add_argument("--vinyl-scale", dest="vinyl_scale", type=float, default=S,
                     help="multiplicador del tamano del vinilo (y en cascada de la "
                          "caratula y el radio del circulo); 0.3-1.0")
    ap.add_argument("--bars-gradient", dest="bars_gradient_mode", default=S, choices=BARS_GRADIENT_MODES,
                     help="color de las barras verticales: solid (por canal) o un "
                          "degradado (rgb/warm/cool/oklch)")
    ap.add_argument("--bars-scope", dest="bars_gradient_scope", default=S, choices=BARS_SCOPES,
                     help="alcance del degradado de barras: channel (grave->agudo por "
                          "canal) o span (grave L -> grave R, todo el ancho)")
    ap.add_argument("--host", default=S,
                     help="IP o hostname de los servicios de metadata/caratula "
                          "(puerto y rutas fijos; vacio para desactivarlos)")
    # No-config: argumentos de arranque, no se persisten.
    ap.add_argument("--fps", type=float, default=60.0)
    ap.add_argument("--seconds", type=float, default=0.0, help="0 = infinito")
    ap.add_argument("--no-config", action="store_true",
                     help="no leer ni escribir el archivo de configuracion (arranque limpio)")
    args = ap.parse_args()

    # Solo se validan los flags realmente pasados (con SUPPRESS, los ausentes no
    # existen en args). Los valores del archivo se sanean aparte, sin abortar.
    if hasattr(args, "max_bar_height") and not 0.0 <= args.max_bar_height <= 100.0:
        ap.error("--max-bar-height debe estar entre 0 y 100")
    if hasattr(args, "circle_radius_mult") and not 1.0 <= args.circle_radius_mult <= 3.0:
        ap.error("--circle-radius-mult debe estar entre 1.0 y 3.0")
    if hasattr(args, "circle_max_height") and not 0.0 <= args.circle_max_height <= 100.0:
        ap.error("--circle-max-height debe estar entre 0 y 100")
    if hasattr(args, "vinyl_scale") and not 0.3 <= args.vinyl_scale <= 1.0:
        ap.error("--vinyl-scale debe estar entre 0.3 y 1.0")

    # Merge de precedencia: DEFAULTS < archivo guardado < flags explicitos.
    file_cfg = {} if args.no_config else config.load()
    cli = {k: getattr(args, k) for k in config.DEFAULTS if hasattr(args, k)}
    eff = {**config.DEFAULTS, **file_cfg, **cli}
    config.sanitize(eff)

    pygame.init()
    pygame.freetype.init()
    screen = pygame.display.set_mode((1000, 560), pygame.RESIZABLE)
    win = _display_window()   # handle para togglear borde / mover / redimensionar
    pygame.display.set_caption("audioviz")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 14)   # HUD: solo ASCII
    font_meta = FallbackFont(META_FONT_CHAIN, 15, bold=True)   # now-playing: cualquier idioma

    engine = Engine(source=eff["source"], fps=args.fps, attack_ms=eff["attack_ms"],
                    decay_ms=eff["decay_ms"], n_bands=eff["n_bands"],
                    distribution=eff["distribution"], note_lo=eff["note_lo"],
                    note_hi=eff["note_hi"], bands_per_octave=eff["bands_per_octave"],
                    tuning=eff["tuning"])

    # La metadata y la caratula viajan por sus propios WebSocket, ajenos al motor
    # de audio: si el servicio no esta arriba el hilo reintenta en el fondo y
    # read() devuelve None, sin afectar en nada al resto del visualizador. Ambos
    # salen del mismo host (editable en el panel; se reconstruyen al cambiarlo).
    metadata_monitor, thumbnail_monitor = build_monitors(eff["host"])
    applied_host = eff["host"]   # host con el que estan armados los monitores
    # C cicla 4 vistas: (caratula+disco, solo disco, solo caratula, nada).
    # Cada estado dice si se dibuja el vinilo y/o la caratula.
    THUMB_MODES = [(True, True), (True, False), (False, True), (False, False)]
    THUMB_MODE_LABELS = ["disco+car", "disco", "caratula", "nada"]

    # Visualizaciones del espectro. Cada una se dibuja si esta activa; el estado
    # de activacion vive en la vista y lo alterna el panel de configuracion.
    visualizations = build_visualizations()
    # Arranca con el default de cada visualizacion y lo pisa con lo guardado
    # (solo ids conocidos: ignoramos entradas viejas de visualizaciones que ya no
    # existen). Los flags no tocan enabled_viz; solo el panel/archivo.
    enabled_viz = {v.id: v.default_on for v in visualizations}
    for vid, on in (eff.get("enabled_viz") or {}).items():
        if vid in enabled_viz:
            enabled_viz[vid] = bool(on)

    # thumb_mode acotado por si el archivo trae un indice fuera de rango.
    thumb_mode = max(0, min(int(eff["thumb_mode"]), len(THUMB_MODES) - 1))

    view = ViewState(show_metadata=bool(eff["show_metadata"]), thumb_mode=thumb_mode,
                     max_bar_height=eff["max_bar_height"], enabled_viz=enabled_viz,
                     circle_radius_mult=eff["circle_radius_mult"],
                     circle_max_height=eff["circle_max_height"],
                     circle_gradient_mode=eff["circle_gradient_mode"],
                     vinyl_scale=eff["vinyl_scale"],
                     bars_gradient_mode=eff["bars_gradient_mode"],
                     bars_gradient_scope=eff["bars_gradient_scope"],
                     bars_use_cover=bool(eff["bars_use_cover"]),
                     circle_use_cover=bool(eff["circle_use_cover"]),
                     bars_cover_2col=eff["bars_cover_2col"],
                     circle_symmetric=bool(eff["circle_symmetric"]),
                     host=str(eff["host"]),
                     fullscreen_display=int(eff["fullscreen_display"]),
                     palette_strict=bool(eff["palette_strict"]),
                     palette_relaxed=bool(eff["palette_relaxed"]),
                     palette_default_fallback=bool(eff["palette_default_fallback"]),
                     frameless=bool(eff["frameless"]),
                     always_on_top=bool(eff["always_on_top"]))
    def persist_config() -> None:
        """Vuelca el estado vivo al archivo (salvo --no-config). Best-effort:
        config.save degrada en silencio si no puede escribir. Lo usan el cierre
        del panel y el cambio de fuente (que ocurre tambien fuera del panel)."""
        if not args.no_config:
            config.save(config.snapshot(view, engine))

    # on_source_change: la fuente puede cambiar desde el panel o por atajo, y en
    # ambos casos queremos guardarla al instante, sin esperar al cierre del panel.
    panel = SettingsPanel(engine, view, THUMB_MODE_LABELS, visualizations,
                          on_source_change=persist_config)
    # Cache: el hilo del socket solo entrega bytes crudos; decodificar a
    # Surface y convert_alpha() necesita el contexto de video, asi que se
    # hace aca, en el hilo principal, y solo cuando cambian los bytes.
    thumb_raw = None
    thumb_surface: pygame.Surface | None = None
    art_panel: pygame.Surface | None = None       # caratula + marco ya compuestos
    art_panel_for: int | None = None              # box para el que se compuso el panel cacheado
    vinyl_base: pygame.Surface | None = None       # disco sin rotar, cacheado por diametro
    vinyl_diam: int | None = None
    vinyl_angle = 0.0                              # se acumula solo mientras hay play
    cover_palette: list | None = None             # 1..3 colores de la caratula actual

    def extract_cover_palette(surf):
        """extract_palette con los flags vivos de los filtros (panel de ajustes)."""
        return extract_palette(surf, strict=view.palette_strict,
                               relaxed=view.palette_relaxed,
                               default_fallback=view.palette_default_fallback)

    def palette_flags():
        return (view.palette_strict, view.palette_relaxed, view.palette_default_fallback)

    applied_palette_flags = palette_flags()   # flags con los que se extrajo la paleta

    keymap = {pygame.K_1: "loopback", pygame.K_2: "fb2k", pygame.K_3: "mic", pygame.K_4: "tone"}
    running = True
    elapsed = 0.0

    # F11 alterna pantalla completa. Guardamos el tamano de la ventana (que el
    # usuario pudo haber redimensionado) para restaurarlo al volver.
    fullscreen = False
    windowed_size = screen.get_size()
    applied_display = view.fullscreen_display   # monitor que ya esta aplicado

    # Estado del modo frameless-windowed (distinto de la pantalla completa):
    #   frameless_applied  -> si la ventana esta ahora mismo sin bordes en modo
    #                         ventana (con la compensacion de alto aplicada).
    #   frameless_top      -> px que subimos la ventana al entrar; se revierten al
    #                         salir (SDL_GetWindowBordersSize da 0 ya sin marco,
    #                         asi que hay que recordar el valor con el que entramos).
    # El arrastre manual (no hay title bar) usa la posicion global del cursor.
    frameless_applied = False
    frameless_top = 31
    dragging = False
    drag_offset = (0, 0)

    def clamp_display(idx: int) -> int:
        return max(0, min(idx, pygame.display.get_num_displays() - 1))

    def set_frameless(on: bool) -> None:
        # Quita/pone el marco IN SITU (sin set_mode: 'screen' sigue valido y
        # cambiar win.size redimensiona su superficie). Al entrar, sube la ventana
        # y la agranda 'top' px para que el area dibujable ocupe donde estaba la
        # title bar; al salir revierte ese delta exacto (los arrastres horizontales
        # y verticales del usuario se conservan, solo deshacemos la compensacion).
        nonlocal frameless_applied, frameless_top
        if on == frameless_applied:
            return
        if on:
            top = _window_top_border(win)   # con la ventana AUN con bordes
            x, y = win.position
            w, h = win.size
            win.borderless = True
            win.size = (w, h + top)
            win.position = (x, y - top)
            frameless_top = top
            frameless_applied = True
        else:
            x, y = win.position
            w, h = win.size
            win.position = (x, y + frameless_top)
            win.size = (w, h - frameless_top)
            win.borderless = False
            frameless_applied = False

    def sync_on_top() -> None:
        # Aplica la preferencia always-on-top a la ventana actual. Idempotente y
        # sin efectos de geometria, asi que se puede llamar sin llevar la cuenta:
        # al togglear, al cerrar el panel y tras recrear la ventana (F11).
        win.always_on_top = view.always_on_top

    def enter_fullscreen(idx: int):
        # Ventana sin bordes (NOFRAME) del tamano del monitor elegido, MOVIDA a su
        # esquina real. Reusar la ventana hace que set_mode(display=idx) se ignore
        # (crece en el sitio), asi que reposicionamos a mano con el origen que da
        # SDL_GetDisplayBounds. Devuelve (superficie, indice aplicado).
        idx = clamp_display(idx)
        x, y, w, h = display_bounds(idx)
        surf = pygame.display.set_mode((w, h), pygame.NOFRAME)
        pygame.display.set_window_position((x, y))
        return surf, idx

    # Restaura la preferencia frameless guardada (arrancamos en ventana, no en
    # pantalla completa, asi que se aplica directo sobre la ventana con bordes).
    if view.frameless:
        set_frameless(True)
    sync_on_top()   # restaura la preferencia always-on-top guardada

    while running:
        # El guardado se dispara al CERRAR el panel: recordamos si estaba abierto
        # antes de procesar los eventos y comparamos despues. Cubre todas las vias
        # de cierre (TAB, ESC, clic fuera) sin tocar la logica interna del panel.
        panel_was_open = panel.open
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
                continue
            # Con un campo de texto del panel enfocado, el teclado es suyo: no
            # robamos TAB/F11 ni los atajos (se escribirian en vez de tipearse).
            editing = panel.editing
            # TAB abre/cierra el panel. El panel atiende sus propios clics (y el
            # ESC cuando esta abierto); si consume el evento, no lo tratamos como
            # atajo. Con el panel cerrado los atajos directos siguen intactos.
            if not editing and ev.type == pygame.KEYDOWN and ev.key == pygame.K_TAB:
                panel.toggle()
                continue
            # F11 alterna pantalla completa <-> ventana. Usamos borderless (una
            # ventana sin bordes del tamano del escritorio): conserva la
            # resolucion nativa del display y evita los glitches del cambio de
            # modo que trae FULLSCREEN real. Al entrar guardamos el tamano
            # actual; al salir lo restauramos como ventana redimensionable.
            if not editing and ev.type == pygame.KEYDOWN and ev.key == pygame.K_F11:
                fullscreen = not fullscreen
                if fullscreen:
                    # El tamano a restaurar es el de la ventana normal: si estamos
                    # en frameless le descontamos la compensacion de alto. El
                    # fullscreen es su propia ventana sin bordes (NOFRAME), asi que
                    # el estado frameless-windowed deja de estar aplicado.
                    w, h = screen.get_size()
                    windowed_size = (w, h - frameless_top) if frameless_applied else (w, h)
                    frameless_applied = False
                    dragging = False
                    screen, applied_display = enter_fullscreen(view.fullscreen_display)
                    win = _display_window()   # set_mode pudo recrear la ventana
                    sync_on_top()
                else:
                    # Volvemos a ventana redimensionable y la centramos en el
                    # monitor donde estaba la pantalla completa; si no, se queda en
                    # la esquina con la barra de titulo fuera de la pantalla.
                    screen = pygame.display.set_mode(windowed_size, pygame.RESIZABLE)
                    win = _display_window()
                    mx, my, mw, mh = display_bounds(applied_display)
                    ww, wh = windowed_size
                    pygame.display.set_window_position(
                        (mx + max(0, (mw - ww) // 2), my + max(0, (mh - wh) // 2)))
                    # Re-aplica la preferencia frameless sobre la ventana restaurada.
                    if view.frameless:
                        set_frameless(True)
                    sync_on_top()
                continue
            if panel.handle(ev, screen.get_size()):
                continue
            # Ventana sin bordes: arrastre manual (no hay title bar que agarrar).
            # Solo con el panel cerrado (si esta abierto, panel.handle ya se llevo
            # el clic arriba) y fuera de pantalla completa. La posicion global del
            # cursor evita realimentacion al mover la ventana bajo el puntero.
            if frameless_applied and not fullscreen:
                if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                    gm = _global_mouse()
                    if gm is not None:
                        wx, wy = win.position
                        drag_offset = (gm[0] - wx, gm[1] - wy)
                        dragging = True
                    continue
                if ev.type == pygame.MOUSEMOTION and dragging:
                    gm = _global_mouse()
                    if gm is not None:
                        win.position = (gm[0] - drag_offset[0], gm[1] - drag_offset[1])
                    continue
                if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1 and dragging:
                    dragging = False
                    continue
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key in keymap:
                    try:
                        engine.set_source(keymap[ev.key])   # hot-swap
                        persist_config()                    # guarda la fuente elegida
                    except Exception as exc:
                        print(f"no se pudo cambiar de fuente: {exc}")
                # Parametros vivos: escribir la propiedad reconfigura el motor.
                elif ev.key == pygame.K_q:
                    engine.attack_ms = max(1.0, engine.attack_ms - 5)
                elif ev.key == pygame.K_a:
                    engine.attack_ms = engine.attack_ms + 5
                elif ev.key == pygame.K_w:
                    engine.decay_ms = max(0.0, engine.decay_ms - 5)
                elif ev.key == pygame.K_s:
                    engine.decay_ms = engine.decay_ms + 5
                elif ev.key == pygame.K_m:
                    view.show_metadata = not view.show_metadata
                elif ev.key == pygame.K_c:
                    view.thumb_mode = (view.thumb_mode + 1) % len(THUMB_MODES)
                elif ev.key == pygame.K_t:
                    view.always_on_top = not view.always_on_top
                    sync_on_top()
                    persist_config()   # guarda al instante (atajo fuera del panel)

        # El panel se acaba de cerrar: aplicamos cambios de host (reconstruyendo
        # los monitores en caliente) y persistimos el estado (salvo --no-config).
        if panel_was_open and not panel.open:
            if view.host != applied_host:
                if metadata_monitor:
                    metadata_monitor.stop()
                if thumbnail_monitor:
                    thumbnail_monitor.stop()
                metadata_monitor, thumbnail_monitor = build_monitors(view.host)
                applied_host = view.host
                # La caratula vieja ya no aplica: la nueva conexion la reemplaza.
                thumb_raw = thumb_surface = art_panel = art_panel_for = cover_palette = None
            # Si se tocaron los filtros del extractor de color, re-extraemos la
            # paleta de la caratula actual en caliente (el k-means es barato y la
            # imagen ya esta decodificada; sin esto el cambio no se veria hasta la
            # proxima pista).
            if palette_flags() != applied_palette_flags:
                applied_palette_flags = palette_flags()
                if thumb_surface is not None:
                    cover_palette = extract_cover_palette(thumb_surface)
            # Cambio del modo frameless: se aplica al cerrar el panel, salvo en
            # pantalla completa (ahi se aplicara al salir de ella, con F11).
            if not fullscreen and view.frameless != frameless_applied:
                set_frameless(view.frameless)
            sync_on_top()   # el panel pudo togglear always-on-top
            persist_config()

        # Si el panel cambio el monitor de destino mientras estamos en pantalla
        # completa, movemos la ventana al nuevo monitor en caliente. (El panel no
        # puede llamar a set_mode el mismo: dejaria obsoleto este 'screen'.)
        if fullscreen and view.fullscreen_display != applied_display:
            screen, applied_display = enter_fullscreen(view.fullscreen_display)
            win = _display_window()
            sync_on_top()

        frame = engine.poll()          # <- lo unico que la GUI le pide al motor
        screen.fill(BG)
        w, h = screen.get_size()

        # El estado de reproduccion se lee SIEMPRE (aunque la barra este oculta
        # con M): controla el giro del vinilo. Sin metadata, el disco gira.
        now_playing = metadata_monitor.read() if metadata_monitor else None
        spinning = now_playing.is_playing if now_playing is not None else True

        media_info = now_playing if view.show_metadata else None
        header_h = 0
        if media_info is not None:
            draw_metadata_bar(screen, font_meta, w, media_info)
            header_h = META_BAR_H

        if frame is not None:
            # Geometria del vinilo central: la comparte el ctx para que las
            # visualizaciones (p.ej. el circulo) se ubiquen respecto al disco,
            # este visible o no. Misma cuenta que usa el bloque del vinilo.
            disc_radius = thumb_box(w, h, view.vinyl_scale) * VINYL_ART_RATIO / 2.0
            ctx = RenderContext(width=w, height=h, header_h=header_h,
                                max_height_frac=view.max_bar_height / 100.0,
                                colors=CH_COLORS, grid_color=GRID,
                                bars_gradient_mode=view.bars_gradient_mode,
                                bars_gradient_scope=view.bars_gradient_scope,
                                center=(w // 2, h // 2), disc_radius=disc_radius,
                                circle_radius_mult=view.circle_radius_mult,
                                circle_max_height_frac=view.circle_max_height / 100.0,
                                circle_gradient_mode=view.circle_gradient_mode,
                                circle_symmetric=view.circle_symmetric,
                                cover_palette=cover_palette,
                                bars_use_cover=view.bars_use_cover,
                                circle_use_cover=view.circle_use_cover,
                                bars_cover_2col=view.bars_cover_2col)
            for viz in visualizations:
                if view.enabled_viz.get(viz.id):
                    viz.draw(screen, frame, ctx)

            ch = frame.channels
            hud = (f"{engine.source_name}  |  {frame.sample_rate} Hz  {ch}ch  "
                   f"|  {engine.distribution} {engine.n_bands}b  "
                   f"|  attack {engine.attack_ms:.0f}ms  decay {engine.decay_ms:.0f}ms  "
                   f"|  {clock.get_fps():.0f} fps")
        else:
            hud = f"{engine.source_name}  |  esperando audio…"

        # Decodificamos la caratula SIEMPRE que cambien los bytes (aunque el arte
        # no se muestre): asi thumb_surface no queda viejo y la paleta de color
        # esta lista para las visualizaciones que la usen, este el disco visible
        # o no. El decode/extraccion solo ocurre en el cambio de bytes.
        if thumbnail_monitor:
            raw = thumbnail_monitor.read()
            if raw is not None and raw is not thumb_raw:
                thumb_raw = raw
                if raw is NO_ART:
                    # El servicio confirmo que la pista actual no tiene caratula:
                    # borramos lo que hubiera, no lo dejamos pegado de la anterior.
                    thumb_surface = art_panel = art_panel_for = None
                    cover_palette = None
                else:
                    try:
                        decoded = pygame.image.load(io.BytesIO(raw)).convert_alpha()
                    except Exception:
                        decoded = None  # frame corrupto/incompleto: no tocamos lo que ya habia
                    if decoded is not None:
                        thumb_surface = decoded
                        art_panel = art_panel_for = None  # invalida el panel: hay imagen nueva
                        cover_palette = extract_cover_palette(decoded)  # 1..3 colores, o None

        # C cicla entre caratula+disco / solo disco / solo caratula / nada.
        show_vinyl, show_art = THUMB_MODES[view.thumb_mode]
        if show_vinyl or show_art:
            center = (w // 2, h // 2)
            box = thumb_box(w, h, view.vinyl_scale)

            # Disco de fondo: cacheado por diametro, rotado por frame.
            if show_vinyl:
                diam = int(box * VINYL_ART_RATIO)
                if vinyl_base is None or diam != vinyl_diam:
                    vinyl_base = make_vinyl(diam)
                    vinyl_diam = diam
                if spinning:
                    vinyl_angle = (vinyl_angle + VINYL_SPIN_DPS * clock.get_time() / 1000.0) % 360.0
                rotated = pygame.transform.rotozoom(vinyl_base, vinyl_angle, 1.0)
                screen.blit(rotated, rotated.get_rect(center=center))

            # Caratula + marco encima (estaticos), solo si hay imagen. El panel
            # se rehace si la imagen cambio (invalidado arriba) o si cambio el
            # tamano de la ventana (box distinto).
            if show_art and thumb_surface is not None:
                if art_panel is None or art_panel_for != box:
                    art_panel = build_art_panel(thumb_surface, box)
                    art_panel_for = box
                screen.blit(art_panel, art_panel.get_rect(center=center))

        screen.blit(font.render(hud, True, TEXT), (16, header_h + 16))
        screen.blit(font.render("1/2/3/4 fuente   Q/A attack   W/S decay   M metadata   C vista   T on-top   F11 pantalla completa   TAB config   ESC salir",
                                True, GRID), (16, header_h + 34))
        panel.draw(screen)   # modal encima de todo, si esta abierto
        pygame.display.flip()

        dt = clock.tick(args.fps) / 1000.0
        elapsed += dt

        # CRITICO: alpha se deriva de tau Y del framerate. Si la GUI no alcanza
        # los fps nominales, el fps real miente y las ballistics se desvian.
        # Realimentamos el fps MEDIDO para que tau siga siendo el que pediste.
        real = clock.get_fps()
        if real > 1.0 and abs(real - engine.fps) > 2.0:
            engine.fps = real

        if args.seconds and elapsed > args.seconds:
            running = False

    engine.close()
    if metadata_monitor:
        metadata_monitor.stop()
    if thumbnail_monitor:
        thumbnail_monitor.stop()
    pygame.quit()


if __name__ == "__main__":
    main()
