"""Traducciones de la interfaz (es/en), en un solo lugar.

Diseño (a proposito, minimalista, sin dependencias):
  - `t(clave)` devuelve el texto en el idioma activo; si la clave no existe
    (p.ej. terminos tecnicos que no se traducen: 'rgb', 'oklch', 'log', nombres
    de fuentes o de notas), devuelve la clave tal cual. Asi los valores tecnicos
    "pasan de largo" sin necesidad de una entrada por cada uno.
  - El idioma es un estado GLOBAL del proceso. La UI se dibuja leyendo `t(...)`
    en vivo, asi que basta cambiar el idioma para que HUD y ayuda se actualicen
    al instante. El panel de configuracion, que arma sus filas una sola vez, se
    reconstruye a mano al cambiar de idioma (ver SettingsPanel._build_tabs).

Convencion de claves: 'tab_*' pestanas, 'viz_*' nombres de visualizacion, 's_*'
ajustes propios de las visualizaciones, 'kb_*' palabras de la linea de atajos,
'hud_*' texto del HUD. El resto son filas del panel o etiquetas de valores.
"""

from __future__ import annotations

LANGUAGES = ["es", "en"]
LANGUAGE_LABELS = ["Español", "English"]   # autonimos: se muestran igual en ambos idiomas

TEXTS: dict[str, dict[str, str]] = {
    "es": {
        # --- panel (chrome) ---
        "settings_title": "Configuración",
        "close_hint": "TAB / ESC cerrar",
        # --- pestanas ---
        "tab_audio": "Audio",
        "tab_visual": "Visual",
        "tab_window": "Ventana",
        "tab_cover": "Carátula",
        "tab_colors": "Colores",
        "tab_data": "Datos",
        "viz_bars": "Barras",
        "viz_circle": "Círculo",
        # --- filas: audio ---
        "src": "Fuente",
        "enable_fb2k": "Usar foobar2000",
        "rise": "Subida barras",
        "fall": "Caída barras",
        "band_layout": "Reparto bandas",
        "band_count": "Cantidad bandas",
        "note_low": "Nota grave",
        "note_high": "Nota aguda",
        "bands_per_oct": "Bandas/octava",
        "tuning": "Afinación",
        # --- filas: visual ---
        "language": "Idioma",
        "track_info": "Info de pista",
        "data_panel": "Panel de datos",
        "shortcuts": "Atajos teclado",
        # --- filas: ventana ---
        "monitor": "Monitor",
        "borderless": "Sin bordes",
        "always_top": "Siempre encima",
        # --- filas: caratula ---
        "view_mode": "Vista",
        "disc_size": "Tamaño disco",
        "colors_vivid": "Colores vivos",
        "colors_gray": "Incluir grises",
        "colors_default": "Usar defecto",
        # --- filas: colores ---
        "color_low": "Grave",
        "color_high": "Agudo",
        "use_third": "Usar 3er color",
        "color_mid": "Medio",
        "gradient": "Degradado",
        # --- filas: datos (HUD) ---
        "sample_rate": "Muestreo",
        "channels": "Canales",
        "analysis": "Análisis",
        "ballistics": "Subida/caída",
        "fps": "FPS",
        # --- titulos del picker de color ---
        "title_color_low": "Color grave",
        "title_color_high": "Color agudo",
        "title_color_mid": "Color medio",
        "pick_reset": "reset",
        # --- ajustes de las visualizaciones ---
        "s_max_height": "Altura máx.",
        "s_bars_colormode": "Modo de color",
        "s_bars_scope": "Alcance color",
        "s_custom_colors": "Colores propios",
        "s_use_cover": "Usar carátula",
        "s_bars_cover2": "Carátula 2 col",
        "s_ring_radius": "Radio anillo",
        "s_circle_center": "Centro vert.",
        "s_symmetric": "Simétrico",
        # --- etiquetas de valores (steppers) ---
        "dist_log": "log",
        "dist_octaves": "octavas",
        "grad_solid": "sólido",
        "grad_warm": "cálido",
        "grad_cool": "frío",
        "scope_channel": "por canal",
        "scope_span": "extremos",
        "cover2_gradient": "degradado",
        "cover2_channel": "por canal",
        "thumb_disc_cover": "disco+car",
        "thumb_disc": "disco",
        "thumb_cover": "carátula",
        "thumb_none": "nada",
        # --- HUD / linea de atajos ---
        "hud_waiting": "esperando audio…",
        "kb_source": "fuente",
        "kb_view": "vista",
        "kb_fullscreen": "pantalla completa",
        "kb_exit": "salir",
    },
    "en": {
        # --- panel (chrome) ---
        "settings_title": "Settings",
        "close_hint": "TAB / ESC close",
        # --- pestanas ---
        "tab_audio": "Audio",
        "tab_visual": "Visual",
        "tab_window": "Window",
        "tab_cover": "Cover",
        "tab_colors": "Colors",
        "tab_data": "Data",
        "viz_bars": "Bars",
        "viz_circle": "Circle",
        # --- filas: audio ---
        "src": "Source",
        "enable_fb2k": "Use foobar2000",
        "rise": "Bar rise",
        "fall": "Bar fall",
        "band_layout": "Band layout",
        "band_count": "Band count",
        "note_low": "Low note",
        "note_high": "High note",
        "bands_per_oct": "Bands/octave",
        "tuning": "Tuning",
        # --- filas: visual ---
        "language": "Language",
        "track_info": "Track info",
        "data_panel": "Data panel",
        "shortcuts": "Shortcuts",
        # --- filas: ventana ---
        "monitor": "Monitor",
        "borderless": "Borderless",
        "always_top": "Always on top",
        # --- filas: caratula ---
        "view_mode": "View",
        "disc_size": "Disc size",
        "colors_vivid": "Vivid colors",
        "colors_gray": "Include grays",
        "colors_default": "Use default",
        # --- filas: colores ---
        "color_low": "Low",
        "color_high": "High",
        "use_third": "Use 3rd color",
        "color_mid": "Mid",
        "gradient": "Gradient",
        # --- filas: datos (HUD) ---
        "sample_rate": "Sample rate",
        "channels": "Channels",
        "analysis": "Analysis",
        "ballistics": "Rise/fall",
        "fps": "FPS",
        # --- titulos del picker de color ---
        "title_color_low": "Low color",
        "title_color_high": "High color",
        "title_color_mid": "Mid color",
        "pick_reset": "reset",
        # --- ajustes de las visualizaciones ---
        "s_max_height": "Max height",
        "s_bars_colormode": "Color mode",
        "s_bars_scope": "Color scope",
        "s_custom_colors": "Custom colors",
        "s_use_cover": "Use cover",
        "s_bars_cover2": "Cover 2 col",
        "s_ring_radius": "Ring radius",
        "s_circle_center": "Vert. center",
        "s_symmetric": "Symmetric",
        # --- etiquetas de valores (steppers) ---
        "dist_log": "log",
        "dist_octaves": "octaves",
        "grad_solid": "solid",
        "grad_warm": "warm",
        "grad_cool": "cool",
        "scope_channel": "per channel",
        "scope_span": "span",
        "cover2_gradient": "gradient",
        "cover2_channel": "per channel",
        "thumb_disc_cover": "disc+cover",
        "thumb_disc": "disc",
        "thumb_cover": "cover",
        "thumb_none": "none",
        # --- HUD / linea de atajos ---
        "hud_waiting": "waiting for audio…",
        "kb_source": "source",
        "kb_view": "view",
        "kb_fullscreen": "fullscreen",
        "kb_exit": "exit",
    },
}

_lang = "es"


def set_language(lang: str) -> None:
    """Fija el idioma activo. Ignora valores desconocidos (deja el actual)."""
    global _lang
    if lang in TEXTS:
        _lang = lang


def get_language() -> str:
    return _lang


def t(key: str) -> str:
    """Texto de `key` en el idioma activo; si no hay traduccion, la propia clave
    (asi los terminos tecnicos que no se traducen pasan sin entrada)."""
    return TEXTS.get(_lang, {}).get(key, key)
