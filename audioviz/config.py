"""Persistencia de la configuracion del visualizador.

Guarda los ajustes en un JSON dentro de la carpeta de config del usuario
(%APPDATA%\\audioviz\\config.json en Windows; $XDG_CONFIG_HOME/audioviz/config.json
o ~/.config/audioviz/config.json en Linux). El visualizador lo carga al
arrancar y lo reescribe cada vez que se cierra el panel de configuracion.

Precedencia de valores (de menor a mayor): DEFAULTS < archivo < flag de CLI.
El merge lo arma el visualizador; aca vive el esquema (que claves se persisten
y sus defaults) y el snapshot del estado vivo.

La persistencia es best-effort: si no se puede leer/escribir (permisos, disco),
se degrada en silencio y la app sigue con los defaults, sin romperse.
"""

from __future__ import annotations

import json
import os

from . import i18n
from .sources import SOURCE_PLATFORMS
from .sources import available_sources as platform_sources
from .visualizations.bars import (DEFAULT_BARS_COVER_2, DEFAULT_BARS_GRADIENT,
                                  DEFAULT_BARS_SCOPE)
from .visualizations.circle_bars import DEFAULT_CENTER, DEFAULT_RADIUS_MULT
from .visualizations.gradient import DEFAULT_GRADIENT, GRADIENT_MODES

APP_DIR = os.path.join(
    os.environ.get("APPDATA")
    or os.environ.get("XDG_CONFIG_HOME")
    or os.path.join(os.path.expanduser("~"), ".config"),
    "audioviz",
)
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

# Fuentes de audio validas (para sanear un archivo corrupto o editado a mano).
# Sale del registro de sources: TODOS los nombres conocidos, de cualquier SO. Un
# archivo puede traer una fuente de otro SO (config compartida, o el equipo
# cambio de plataforma) y sigue siendo un valor persistible valido; que se pueda
# usar aca lo decide available_sources (por SO) y, en ultima instancia, el
# fallback del motor. fb2k es de nicho: levanta su PROPIO servidor WebSocket y no
# se ofrece salvo que el usuario la habilite (ver available_sources).
SOURCES = tuple(SOURCE_PLATFORMS)
DISTRIBUTIONS = ("log", "octaves")

# Claves que tienen esquema (default en DEFAULTS y se fusionan en eff) pero que
# NO se persisten: cada arranque las toma de su default, nunca del archivo. El
# modo frameless-windowed es una preferencia efimera de sesion, no de
# instalacion: no se guarda al cerrar (ver snapshot) ni se lee de un archivo
# viejo (ver load, que las descarta al cargar).
NON_PERSISTENT = ("frameless",)


def available_sources(fb2k_enabled: bool) -> list[str]:
    """Fuentes ofrecidas en la UI y los atajos, en orden canonico. Se filtran por
    dos criterios: (1) compatibilidad con el SO actual -- Windows ve Windows+
    Generic, Linux ve Linux+Generic (lo decide el registro de sources); (2) el
    gate de fb2k, que solo aparece si esta habilitada. Unica fuente de verdad del
    listado (la comparten el panel y el mapa de teclas del visualizador)."""
    return [s for s in platform_sources() if s != "fb2k" or fb2k_enabled]

# Colores base del modo normal (no extraido de la caratula). color_lo tine el
# grave/canal izquierdo y color_hi el agudo/canal derecho: son los dos extremos
# del degradado. color_mid es un tercer color OPCIONAL (punto medio del
# degradado), analogo a cuando la caratula aporta 3 colores predominantes. Los
# dos primeros son el celeste y rojo historicos (CH_COLORS); el medio es un
# violeta que cae en el arco frio (cyan->azul->magenta->rojo) entre ambos.
DEFAULT_COLOR_LO = (90, 200, 250)
DEFAULT_COLOR_HI = (250, 130, 110)
DEFAULT_COLOR_MID = (185, 125, 230)

# Esquema: clave canonica -> valor por defecto. Estas claves son las que se
# persisten y las que el visualizador fusiona con el archivo y los flags. Los
# nombres coinciden con los `dest` de argparse (asi el merge es directo).
DEFAULTS: dict = {
    "source": "loopback",
    "fb2k_enabled": False,
    "attack_ms": 20.0,
    "decay_ms": 100.0,
    "distribution": "octaves",
    "n_bands": 128,
    "note_lo": "C0",
    "note_hi": "F#10",
    "bands_per_octave": 12,
    "tuning": 440.0,
    "max_bar_height": 100.0,
    "circle_radius_mult": DEFAULT_RADIUS_MULT,
    "circle_max_height": 100.0,
    # Altura del centro del conjunto disco+caratula+circulo, como % del alto de la
    # ventana (0 = abajo, 100 = arriba, 50 = mitad, el default de siempre).
    "circle_center": DEFAULT_CENTER,
    "circle_gradient_mode": "cool",   # "frio": medio violeta, mas agradable de arranque
    "vinyl_scale": 1.0,
    "bars_gradient_mode": DEFAULT_BARS_GRADIENT,
    "bars_gradient_scope": DEFAULT_BARS_SCOPE,
    "bars_use_cover": False,
    "circle_use_cover": False,
    "bars_cover_2col": DEFAULT_BARS_COVER_2,
    "circle_symmetric": True,
    # Colores base del modo normal (los persiste como listas [r,g,b]). El 3er
    # color solo se usa si color_use_mid esta activo Y la visualizacion esta en
    # degradado (las barras pueden estar en solido; ver bars/circle_bars).
    # colors_gradient_mode es el modo del degradado de la barra de PREVIEW de la
    # pestana de colores; cada visualizacion sigue eligiendo el suyo aparte.
    "color_lo": list(DEFAULT_COLOR_LO),
    "color_hi": list(DEFAULT_COLOR_HI),
    "color_mid": list(DEFAULT_COLOR_MID),
    "color_use_mid": False,
    "colors_gradient_mode": DEFAULT_GRADIENT,
    # Por visualizacion: usar los colores personalizados como fallback (ver arriba).
    "bars_use_custom": False,
    "circle_use_custom": False,
    # Idioma de la interfaz (panel, HUD, linea de atajos): "es" | "en".
    "language": "es",
    "show_metadata": True,
    # HUD de datos (esquina sup. izq.): interruptor maestro + que valores muestra.
    # La linea de atajos de teclado se controla aparte (show_keybinds).
    "show_hud": True,
    "show_keybinds": True,
    "hud_source": True,
    "hud_rate": True,
    "hud_channels": True,
    "hud_analysis": True,
    "hud_ballistics": True,
    "hud_fps": True,
    "thumb_mode": 0,
    "fullscreen_display": 0,
    # frameless: default de sesion, NO persistente (ver NON_PERSISTENT). Vive
    # aca solo para que el merge le de un valor inicial a eff en cada arranque.
    "frameless": False,
    "always_on_top": False,
    "palette_strict": True,
    "palette_relaxed": True,
    "palette_default_fallback": True,
    # enabled_viz no esta aca: su default depende del registro de visualizaciones
    # (cada una trae su default_on), asi que lo arma el visualizador.
}


def load() -> dict:
    """Config guardada, o {} si no existe / no se puede leer / esta corrupta."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        # Descarta claves no persistentes que pudieran venir de un archivo
        # guardado por una version anterior: siempre arrancan desde su default.
        for key in NON_PERSISTENT:
            data.pop(key, None)
        return data
    except (OSError, ValueError):
        return {}


def save(cfg: dict) -> None:
    """Escribe la config (best-effort). Usa un temporal + replace para no dejar
    un archivo a medias si el proceso muere a mitad de escritura."""
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except OSError:
        pass


def _coerce_rgb(value, default: tuple) -> tuple:
    """Convierte un color guardado ([r,g,b] de un JSON, o una tupla) a una tupla
    de 3 enteros 0-255. Cae al default si viene malformado (archivo editado a
    mano). Devolver SIEMPRE una tupla importa: las visualizaciones distinguen un
    color solido de un array por-banda con isinstance(x, tuple)."""
    try:
        t = tuple(int(round(float(x))) for x in value)
    except (TypeError, ValueError):
        return default
    if len(t) != 3:
        return default
    return tuple(max(0, min(255, x)) for x in t)


def sanitize(eff: dict) -> None:
    """Corrige in-place los valores enum que, si vinieran invalidos de un archivo
    editado a mano, harian fallar la construccion del motor. El resto degrada
    solo (un modo de degradado desconocido cae a rgb, etc.)."""
    if eff.get("source") not in SOURCES:
        eff["source"] = DEFAULTS["source"]
    # Una fuente valida en el esquema pero de OTRO SO (config traida de otra
    # maquina, o el equipo cambio de plataforma: p.ej. 'loopback' guardado en
    # Windows y abierto en Linux) no se puede usar aca. La llevamos a la preferida
    # de ESTE SO en vez de dejar que el motor caiga hasta 'tone' su fallback
    # definitivo -- si no, un Linux con el default de fabrica nunca capturaria la
    # salida del sistema. platform_sources() ya viene en orden canonico: [0] es la
    # preferida (pipewire en Linux, loopback en Windows).
    usable = platform_sources()
    if eff.get("source") not in usable:
        eff["source"] = usable[0]
    if eff.get("distribution") not in DISTRIBUTIONS:
        eff["distribution"] = DEFAULTS["distribution"]
    # Colores base -> tupla de 3 enteros (o su default). Ver _coerce_rgb.
    for key, default in (("color_lo", DEFAULT_COLOR_LO), ("color_hi", DEFAULT_COLOR_HI),
                         ("color_mid", DEFAULT_COLOR_MID)):
        eff[key] = _coerce_rgb(eff.get(key), default)
    if eff.get("colors_gradient_mode") not in GRADIENT_MODES:
        eff["colors_gradient_mode"] = DEFAULTS["colors_gradient_mode"]
    if eff.get("language") not in i18n.LANGUAGES:
        eff["language"] = DEFAULTS["language"]
    # Estar en fb2k implica tenerla habilitada: no se puede haber seleccionado sin
    # habilitarla antes, asi que un archivo con source=fb2k la reactiva sola (y
    # esto mismo hace que --source fb2k la habilite de forma implicita).
    if eff.get("source") == "fb2k":
        eff["fb2k_enabled"] = True


def snapshot(view, engine) -> dict:
    """Estado vivo actual como dict serializable, listo para save()."""
    return {
        "source": engine.source_name,
        "fb2k_enabled": engine.fb2k_enabled,
        "attack_ms": engine.attack_ms,
        "decay_ms": engine.decay_ms,
        "distribution": engine.distribution,
        "n_bands": engine.n_bands_req,
        "note_lo": engine.note_lo,
        "note_hi": engine.note_hi,
        "bands_per_octave": engine.bands_per_octave,
        "tuning": engine.tuning,
        "max_bar_height": view.max_bar_height,
        "circle_radius_mult": view.circle_radius_mult,
        "circle_max_height": view.circle_max_height,
        "circle_center": view.circle_center,
        "circle_gradient_mode": view.circle_gradient_mode,
        "vinyl_scale": view.vinyl_scale,
        "bars_gradient_mode": view.bars_gradient_mode,
        "bars_gradient_scope": view.bars_gradient_scope,
        "bars_use_cover": view.bars_use_cover,
        "circle_use_cover": view.circle_use_cover,
        "bars_cover_2col": view.bars_cover_2col,
        "circle_symmetric": view.circle_symmetric,
        "color_lo": list(view.color_lo),
        "color_hi": list(view.color_hi),
        "color_mid": list(view.color_mid),
        "color_use_mid": view.color_use_mid,
        "colors_gradient_mode": view.colors_gradient_mode,
        "bars_use_custom": view.bars_use_custom,
        "circle_use_custom": view.circle_use_custom,
        "show_metadata": view.show_metadata,
        "show_hud": view.show_hud,
        "show_keybinds": view.show_keybinds,
        "hud_source": view.hud_source,
        "hud_rate": view.hud_rate,
        "hud_channels": view.hud_channels,
        "hud_analysis": view.hud_analysis,
        "hud_ballistics": view.hud_ballistics,
        "hud_fps": view.hud_fps,
        "thumb_mode": view.thumb_mode,
        "fullscreen_display": view.fullscreen_display,
        # frameless no se guarda: es preferencia de sesion (ver NON_PERSISTENT).
        "always_on_top": view.always_on_top,
        "palette_strict": view.palette_strict,
        "palette_relaxed": view.palette_relaxed,
        "palette_default_fallback": view.palette_default_fallback,
        # El idioma es estado global del proceso (i18n), no vive en la vista.
        "language": i18n.get_language(),
        "enabled_viz": dict(view.enabled_viz),
    }
