"""GUI de referencia (pygame). Se engancha al Engine y no sabe NADA de sockets,
WASAPI, FFT ni ventanas de Hann. Solo pide frames y dibuja.

    python -m audioviz.gui_pygame --source fb2k
    python -m audioviz.gui_pygame --source loopback --attack-ms 20 --decay-ms 30
    python -m audioviz.gui_pygame --source mic

Teclas:
    1 / 2 / 3 / 4   fuente: fb2k / loopback / mic / tone   (hot-swap, sin reiniciar)
    Q / A       attack  -/+
    W / S       decay   -/+
    M           metadata (now playing) on/off
    C           vista: caratula+disco / disco / caratula / nada
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

import numpy as np
import pygame
import pygame.freetype

from .engine import Engine
from .metadata import MetadataMonitor
from .settings_panel import SettingsPanel
from .thumbnail import NO_ART, ThumbnailMonitor
from .visualizations import RenderContext, build_visualizations

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

DEFAULT_METADATA_URL = "ws://100.97.196.102:25012/ws/media-info"
DEFAULT_THUMBNAIL_URL = "ws://100.97.196.102:25012/ws/thumbnail"


_display_bounds_cache: list[tuple[int, int, int, int]] | None = None


def _load_display_bounds() -> list[tuple[int, int, int, int]]:
    """Origen y tamano (x, y, w, h) de cada monitor, via SDL_GetDisplayBounds.
    pygame solo expone get_desktop_sizes() (tamanos, sin origen), pero para
    anclar la pantalla completa a un monitor concreto necesitamos su esquina en
    el escritorio virtual. Llamamos a la SDL2.dll que trae pygame por ctypes.
    Devuelve [] si algo falla (el llamador cae a un valor por defecto)."""
    try:
        dll = os.path.join(os.path.dirname(pygame.__file__), "SDL2.dll")
        if not os.path.exists(dll):
            cands = glob.glob(os.path.join(os.path.dirname(pygame.__file__), "SDL2.dll"))
            if not cands:
                return []
            dll = cands[0]
        sdl = ctypes.CDLL(dll)

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


def thumb_box(w, h) -> int:
    """Lado del recuadro donde entra la caratula, acotado a THUMB_MIN/MAX_PX."""
    return max(THUMB_MIN_PX, min(int(min(w, h) * THUMB_FRACTION), THUMB_MAX_PX))


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
                 enabled_viz: dict[str, bool], fullscreen_display: int = 0):
        self.show_metadata = show_metadata
        self.thumb_mode = thumb_mode
        self.max_bar_height = max_bar_height
        # Que visualizaciones estan activas, por id. El visualizador dibuja todas
        # las activas (superpuestas); el panel de configuracion las alterna.
        self.enabled_viz = enabled_viz
        # Indice del monitor al que se ancla la pantalla completa (F11).
        self.fullscreen_display = fullscreen_display


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="fb2k", choices=["fb2k", "loopback", "mic", "tone"])
    ap.add_argument("--fps", type=float, default=60.0)
    ap.add_argument("--attack-ms", type=float, default=20.0)
    ap.add_argument("--decay-ms", type=float, default=250.0)
    ap.add_argument("--bands", type=int, default=128, help="solo para --dist log")
    ap.add_argument("--dist", default="log", choices=["log", "octaves"])
    ap.add_argument("--note-lo", default="C0")
    ap.add_argument("--note-hi", default="F#10")
    ap.add_argument("--bpo", type=int, default=12, help="bandas por octava")
    ap.add_argument("--tuning", type=float, default=440.0)
    ap.add_argument("--seconds", type=float, default=0.0, help="0 = infinito")
    ap.add_argument("--max-bar-height", type=float, default=100.0,
                     help="altura maxima de las barras, como %% del alto de la pantalla (0-100)")
    ap.add_argument("--metadata-url", default=DEFAULT_METADATA_URL,
                     help="WebSocket de now-playing (vacio para desactivarlo del todo)")
    ap.add_argument("--thumbnail-url", default=DEFAULT_THUMBNAIL_URL,
                     help="WebSocket de caratula (vacio para desactivarlo del todo)")
    args = ap.parse_args()

    if not 0.0 <= args.max_bar_height <= 100.0:
        ap.error("--max-bar-height debe estar entre 0 y 100")

    pygame.init()
    pygame.freetype.init()
    screen = pygame.display.set_mode((1000, 560), pygame.RESIZABLE)
    pygame.display.set_caption("audioviz")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 14)   # HUD: solo ASCII
    font_meta = FallbackFont(META_FONT_CHAIN, 15, bold=True)   # now-playing: cualquier idioma

    engine = Engine(source=args.source, fps=args.fps, attack_ms=args.attack_ms,
                    decay_ms=args.decay_ms, n_bands=args.bands,
                    distribution=args.dist, note_lo=args.note_lo,
                    note_hi=args.note_hi, bands_per_octave=args.bpo,
                    tuning=args.tuning)

    # La metadata viaja por su propio WebSocket, ajeno al motor de audio: si el
    # servicio no esta arriba el hilo simplemente reintenta en el fondo y
    # read() devuelve None, sin afectar en nada al resto del visualizador.
    metadata_monitor = MetadataMonitor(args.metadata_url) if args.metadata_url else None
    if metadata_monitor:
        metadata_monitor.start()

    thumbnail_monitor = ThumbnailMonitor(args.thumbnail_url) if args.thumbnail_url else None
    if thumbnail_monitor:
        thumbnail_monitor.start()
    # C cicla 4 vistas: (caratula+disco, solo disco, solo caratula, nada).
    # Cada estado dice si se dibuja el vinilo y/o la caratula.
    THUMB_MODES = [(True, True), (True, False), (False, True), (False, False)]
    THUMB_MODE_LABELS = ["disco+car", "disco", "caratula", "nada"]

    # Visualizaciones del espectro. Cada una se dibuja si esta activa; el estado
    # de activacion vive en la vista y lo alterna el panel de configuracion.
    visualizations = build_visualizations()
    enabled_viz = {v.id: v.default_on for v in visualizations}

    view = ViewState(show_metadata=True, thumb_mode=0,
                     max_bar_height=args.max_bar_height, enabled_viz=enabled_viz,
                     fullscreen_display=0)
    panel = SettingsPanel(engine, view, THUMB_MODE_LABELS, visualizations)
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

    keymap = {pygame.K_1: "fb2k", pygame.K_2: "loopback", pygame.K_3: "mic", pygame.K_4: "tone"}
    running = True
    elapsed = 0.0

    # F11 alterna pantalla completa. Guardamos el tamano de la ventana (que el
    # usuario pudo haber redimensionado) para restaurarlo al volver.
    fullscreen = False
    windowed_size = screen.get_size()
    applied_display = view.fullscreen_display   # monitor que ya esta aplicado

    def clamp_display(idx: int) -> int:
        return max(0, min(idx, pygame.display.get_num_displays() - 1))

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

    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
                continue
            # TAB abre/cierra el panel. El panel atiende sus propios clics (y el
            # ESC cuando esta abierto); si consume el evento, no lo tratamos como
            # atajo. Con el panel cerrado los atajos directos siguen intactos.
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_TAB:
                panel.toggle()
                continue
            # F11 alterna pantalla completa <-> ventana. Usamos borderless (una
            # ventana sin bordes del tamano del escritorio): conserva la
            # resolucion nativa del display y evita los glitches del cambio de
            # modo que trae FULLSCREEN real. Al entrar guardamos el tamano
            # actual; al salir lo restauramos como ventana redimensionable.
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_F11:
                fullscreen = not fullscreen
                if fullscreen:
                    windowed_size = screen.get_size()
                    screen, applied_display = enter_fullscreen(view.fullscreen_display)
                else:
                    # Volvemos a ventana redimensionable y la centramos en el
                    # monitor donde estaba la pantalla completa; si no, se queda en
                    # la esquina con la barra de titulo fuera de la pantalla.
                    screen = pygame.display.set_mode(windowed_size, pygame.RESIZABLE)
                    mx, my, mw, mh = display_bounds(applied_display)
                    ww, wh = windowed_size
                    pygame.display.set_window_position(
                        (mx + max(0, (mw - ww) // 2), my + max(0, (mh - wh) // 2)))
                continue
            if panel.handle(ev, screen.get_size()):
                continue
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key in keymap:
                    try:
                        engine.set_source(keymap[ev.key])   # hot-swap
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

        # Si el panel cambio el monitor de destino mientras estamos en pantalla
        # completa, movemos la ventana al nuevo monitor en caliente. (El panel no
        # puede llamar a set_mode el mismo: dejaria obsoleto este 'screen'.)
        if fullscreen and view.fullscreen_display != applied_display:
            screen, applied_display = enter_fullscreen(view.fullscreen_display)

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
            disc_radius = thumb_box(w, h) * VINYL_ART_RATIO / 2.0
            ctx = RenderContext(width=w, height=h, header_h=header_h,
                                max_height_frac=view.max_bar_height / 100.0,
                                colors=CH_COLORS, grid_color=GRID,
                                center=(w // 2, h // 2), disc_radius=disc_radius)
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

        # C cicla entre caratula+disco / solo disco / solo caratula / nada.
        show_vinyl, show_art = THUMB_MODES[view.thumb_mode]
        if thumbnail_monitor and (show_vinyl or show_art):
            # Mantenemos thumb_surface al dia siempre (aunque la caratula no se
            # muestre ahora): el decode solo ocurre cuando cambian los bytes,
            # asi que al volver a "solo caratula" no aparece una imagen vieja.
            raw = thumbnail_monitor.read()
            if raw is not None and raw is not thumb_raw:
                thumb_raw = raw
                if raw is NO_ART:
                    # El servicio confirmo que la pista actual no tiene caratula:
                    # borramos lo que hubiera, no lo dejamos pegado de la anterior.
                    thumb_surface = art_panel = art_panel_for = None
                else:
                    try:
                        decoded = pygame.image.load(io.BytesIO(raw)).convert_alpha()
                    except Exception:
                        decoded = None  # frame corrupto/incompleto: no tocamos lo que ya habia
                    if decoded is not None:
                        thumb_surface = decoded
                        art_panel = art_panel_for = None  # invalida el panel: hay imagen nueva

            center = (w // 2, h // 2)
            box = thumb_box(w, h)

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
        screen.blit(font.render("1/2/3/4 fuente   Q/A attack   W/S decay   M metadata   C vista   F11 pantalla completa   TAB config   ESC salir",
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
