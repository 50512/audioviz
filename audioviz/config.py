"""Persistencia de la configuracion del visualizador.

Guarda los ajustes en un JSON dentro de la carpeta de config del usuario
(%APPDATA%\\audioviz\\config.json en Windows). El visualizador lo carga al
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

from .visualizations.bars import (DEFAULT_BARS_COVER_2, DEFAULT_BARS_GRADIENT,
                                  DEFAULT_BARS_SCOPE)
from .visualizations.circle_bars import DEFAULT_RADIUS_MULT
from .visualizations.gradient import DEFAULT_GRADIENT

APP_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "audioviz")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

# Fuentes de audio validas (para sanear un archivo corrupto o editado a mano).
# fb2k es una fuente de nicho: levanta su PROPIO servidor WebSocket y, si el
# puerto ya esta ocupado, su arranque bloquea y da un tiron. Por eso no se ofrece
# ni entra en el fallback automatico salvo que el usuario la habilite; sigue
# siendo un valor persistible valido (available_sources decide su visibilidad).
SOURCES = ("loopback", "fb2k", "mic", "tone")
DISTRIBUTIONS = ("log", "octaves")

# Claves que tienen esquema (default en DEFAULTS y se fusionan en eff) pero que
# NO se persisten: cada arranque las toma de su default, nunca del archivo. El
# modo frameless-windowed es una preferencia efimera de sesion, no de
# instalacion: no se guarda al cerrar (ver snapshot) ni se lee de un archivo
# viejo (ver load, que las descarta al cargar).
NON_PERSISTENT = ("frameless",)


def available_sources(fb2k_enabled: bool) -> list[str]:
    """Fuentes ofrecidas en la UI y los atajos, en el orden de SOURCES. fb2k solo
    aparece si esta habilitada; el resto siempre. Unica fuente de verdad del
    listado (la comparten el panel y el mapa de teclas del visualizador)."""
    return [s for s in SOURCES if s != "fb2k" or fb2k_enabled]

# Host por defecto de los servicios de metadata/caratula (IP o hostname). El
# puerto y las rutas son fijos (formato de la API); ver build_urls en visualizer.
DEFAULT_HOST = "127.0.0.1"

# Esquema: clave canonica -> valor por defecto. Estas claves son las que se
# persisten y las que el visualizador fusiona con el archivo y los flags. Los
# nombres coinciden con los `dest` de argparse (asi el merge es directo).
DEFAULTS: dict = {
    "source": "loopback",
    "fb2k_enabled": False,
    "attack_ms": 20.0,
    "decay_ms": 250.0,
    "distribution": "log",
    "n_bands": 128,
    "note_lo": "C0",
    "note_hi": "F#10",
    "bands_per_octave": 12,
    "tuning": 440.0,
    "max_bar_height": 100.0,
    "circle_radius_mult": DEFAULT_RADIUS_MULT,
    "circle_max_height": 100.0,
    "circle_gradient_mode": DEFAULT_GRADIENT,
    "vinyl_scale": 1.0,
    "bars_gradient_mode": DEFAULT_BARS_GRADIENT,
    "bars_gradient_scope": DEFAULT_BARS_SCOPE,
    "bars_use_cover": False,
    "circle_use_cover": False,
    "bars_cover_2col": DEFAULT_BARS_COVER_2,
    "circle_symmetric": False,
    "host": DEFAULT_HOST,
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


def sanitize(eff: dict) -> None:
    """Corrige in-place los valores enum que, si vinieran invalidos de un archivo
    editado a mano, harian fallar la construccion del motor. El resto degrada
    solo (un modo de degradado desconocido cae a rgb, etc.)."""
    if eff.get("source") not in SOURCES:
        eff["source"] = DEFAULTS["source"]
    if eff.get("distribution") not in DISTRIBUTIONS:
        eff["distribution"] = DEFAULTS["distribution"]
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
        "circle_gradient_mode": view.circle_gradient_mode,
        "vinyl_scale": view.vinyl_scale,
        "bars_gradient_mode": view.bars_gradient_mode,
        "bars_gradient_scope": view.bars_gradient_scope,
        "bars_use_cover": view.bars_use_cover,
        "circle_use_cover": view.circle_use_cover,
        "bars_cover_2col": view.bars_cover_2col,
        "circle_symmetric": view.circle_symmetric,
        "host": view.host,
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
        "enabled_viz": dict(view.enabled_viz),
    }
