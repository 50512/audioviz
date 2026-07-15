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
    ESC         salir

Requiere: pip install pygame-ce websockets
"""

from __future__ import annotations

import argparse
import io
import math

import numpy as np
import pygame
import pygame.freetype

from .engine import Engine
from .metadata import MetadataMonitor
from .thumbnail import NO_ART, ThumbnailMonitor

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


def draw_channel(surf, band_h, rect, color, reverse=False):
    """band_h: (n_bands,) en 0..1. rect: (x, y, w, h)."""
    x, y, w, h = rect
    n = len(band_h)
    bw = w / n
    bands = band_h[::-1] if reverse else band_h
    for i, v in enumerate(bands):
        bh = max(1, int(v * h))
        bx = int(x + i * bw)
        pygame.draw.rect(surf, color, (bx + 1, y + h - bh, int(bw) - 1, bh))


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
    show_metadata = True

    thumbnail_monitor = ThumbnailMonitor(args.thumbnail_url) if args.thumbnail_url else None
    if thumbnail_monitor:
        thumbnail_monitor.start()
    # C cicla 4 vistas: (caratula+disco, solo disco, solo caratula, nada).
    # Cada estado dice si se dibuja el vinilo y/o la caratula.
    THUMB_MODES = [(True, True), (True, False), (False, True), (False, False)]
    thumb_mode = 0
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

    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
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
                    show_metadata = not show_metadata
                elif ev.key == pygame.K_c:
                    thumb_mode = (thumb_mode + 1) % len(THUMB_MODES)

        frame = engine.poll()          # <- lo unico que la GUI le pide al motor
        screen.fill(BG)
        w, h = screen.get_size()

        # El estado de reproduccion se lee SIEMPRE (aunque la barra este oculta
        # con M): controla el giro del vinilo. Sin metadata, el disco gira.
        now_playing = metadata_monitor.read() if metadata_monitor else None
        spinning = now_playing.is_playing if now_playing is not None else True

        media_info = now_playing if show_metadata else None
        header_h = 0
        if media_info is not None:
            draw_metadata_bar(screen, font_meta, w, media_info)
            header_h = META_BAR_H

        if frame is not None:
            heights = frame.normalized()           # (channels, n_bands) en 0..1
            ch = frame.channels
            pad, top, gap = 16, 56 + header_h, 12
            max_plot_h = h * args.max_bar_height / 100.0

            if ch == 2:
                # Stereo: canales lado a lado.
                # Canal izquierdo: graves en borde izquierdo, agudos hacia el centro.
                # Canal derecho: agudos hacia el centro, graves en borde derecho (invertido).
                plot_h = h - top - pad
                bar_h = min(plot_h, max_plot_h)
                half = w // 2
                y = top
                bottom = y + plot_h

                left_w = half - pad - gap // 2
                pygame.draw.line(screen, GRID, (pad, bottom), (pad + left_w, bottom))
                draw_channel(screen, heights[0], (pad, bottom - bar_h, left_w, bar_h), CH_COLORS[0])

                right_x = half + gap // 2
                right_w = w - pad - right_x
                pygame.draw.line(screen, GRID, (right_x, bottom), (right_x + right_w, bottom))
                draw_channel(screen, heights[1], (right_x, bottom - bar_h, right_w, bar_h), CH_COLORS[1],
                             reverse=True)
            else:
                plot_h = (h - top - pad - gap * (ch - 1)) / ch
                bar_h = min(plot_h, max_plot_h)
                for c in range(ch):
                    y = top + c * (plot_h + gap)
                    bottom = y + plot_h
                    pygame.draw.line(screen, GRID, (pad, bottom), (w - pad, bottom))
                    draw_channel(screen, heights[c], (pad, bottom - bar_h, w - 2 * pad, bar_h),
                                 CH_COLORS[c % len(CH_COLORS)])

            hud = (f"{engine.source_name}  |  {frame.sample_rate} Hz  {ch}ch  "
                   f"|  {engine.distribution} {engine.n_bands}b  "
                   f"|  attack {engine.attack_ms:.0f}ms  decay {engine.decay_ms:.0f}ms  "
                   f"|  {clock.get_fps():.0f} fps")
        else:
            hud = f"{engine.source_name}  |  esperando audio…"

        # C cicla entre caratula+disco / solo disco / solo caratula / nada.
        show_vinyl, show_art = THUMB_MODES[thumb_mode]
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
        screen.blit(font.render("1/2/3/4 fuente   Q/A attack   W/S decay   M metadata   C vista   ESC salir",
                                True, GRID), (16, header_h + 34))
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
