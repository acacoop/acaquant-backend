"""Loader + slicer del catálogo técnico (docs/asistente/estrategias.md).

Antes el archivo entero se inyectaba en el system prompt (~10K tokens/request).
Ahora se consulta por sección bajo demanda mediante la tool
`consultar_catalogo_estrategias`. Así el prompt base queda chico y el modelo
solo "paga" la sección cuando realmente la necesita.

Usa el loader genérico (`markdown_loader.load_md`) para cachear por mtime.
"""
from __future__ import annotations

from pathlib import Path

from api.agent.markdown_loader import load_md

ESTRATEGIAS_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "asistente" / "estrategias.md"


def load_estrategias() -> str:
    return load_md(ESTRATEGIAS_PATH)


# Mapa tema → (marker_start, marker_end). end=None significa "hasta el final".
# Los marcadores son strings literales que aparecen al inicio de una línea
# del markdown. El slice devuelve desde start hasta justo antes de end.
SECCIONES: dict[str, tuple[str, str | None]] = {
    "mapa-datos":        ("## Mapa de datos → tools → campos",        "# 1. FIXED INCOME"),
    "fi-curva":          ("# 1. FIXED INCOME — Posiciones de curva",  "# 2. FIXED INCOME"),
    "fi-butterfly":      ("# 2. FIXED INCOME — Butterflies",          "# 3. FIXED INCOME"),
    "fi-carry":          ("# 3. FIXED INCOME — Carry",                "# 4. INFLATION"),
    "inflation":         ("# 4. INFLATION",                            "# 5. FX"),
    "fx-carry":          ("# 5. FX / CARRY",                           "# 6. OPTIONS"),
    "options-bullish":   ("## 6.1 Estrategias BULLISH",                "## 6.2 Estrategias BEARISH"),
    "options-bearish":   ("## 6.2 Estrategias BEARISH",                "## 6.3 Estrategias NEUTRAL"),
    "options-neutral":   ("## 6.3 Estrategias NEUTRAL",                "## 6.4 Estrategias VOLATILIDAD"),
    "options-vol":       ("## 6.4 Estrategias VOLATILIDAD",            "## 6.5 Notas generales"),
    "options-notas":     ("## 6.5 Notas generales opciones AR",        "# 7. MACRO"),
    "macro":             ("# 7. MACRO",                                "# 8. ESTRATEGIAS QUE NO APLICAN"),
    "no-aplican":        ("# 8. ESTRATEGIAS QUE NO APLICAN",           "# 9. REGLA DE CONSTRUCCIÓN"),
    "reglas-asistente":  ("# 9. REGLA DE CONSTRUCCIÓN",                None),
}


def temas_disponibles() -> list[str]:
    return list(SECCIONES.keys())


def get_seccion(tema: str) -> str:
    """Retorna el slice del catálogo para un tema dado.

    Si el tema no existe, devuelve un mensaje con la lista de temas válidos.
    """
    full = load_estrategias()
    if not full:
        return "(catálogo no disponible — archivo estrategias.md faltante)"

    if tema not in SECCIONES:
        return (
            f"Tema '{tema}' no reconocido. Temas disponibles: "
            + ", ".join(f"'{t}'" for t in SECCIONES)
        )

    start_marker, end_marker = SECCIONES[tema]
    start = full.find(start_marker)
    if start == -1:
        return f"(sección '{tema}' no encontrada en el catálogo)"

    if end_marker is None:
        return full[start:].strip()

    end = full.find(end_marker, start + len(start_marker))
    if end == -1:
        return full[start:].strip()

    return full[start:end].strip()
