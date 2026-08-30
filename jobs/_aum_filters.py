"""Reglas de exclusión del AuM — qué tenencia NO cuenta.

**Single source of truth.** Lo consumen TRES lugares, y dos de ellos NO son
jobs: `jobs/portafolio_backfill.py` (el writer diario de `portafolio.tenencia`,
que marca `aum='si'/'no'` fila por fila), `api/services/import_tenencia_sql.py`
y `api/services/sin_operador.py`. Congelado por `tests/unit/test_golden_pipeline.py`,
que corre en CI.

⚠️ Este docstring decía «usado por `jobs/aum.py`» sobre `Valuaciones.AuM`. Las
dos cosas se fueron: la colección se eliminó el 2026-06-15 con el decomiso de
Mongo y `jobs/aum.py::run` con ella. El destino hoy es la columna `aum` de
`portafolio.tenencia`.

Reglas:
  1. `unidad == "USDL"` (cash USD link, no contabiliza).
  2. `cuenta` o `unidad` contiene "OTC" o "CDC" (case-insensitive).
  3. `id_cuenta` aparece en las contrapartes (el id) — son
     cuentas de fondos / sociedades gerentes (SCHRODER, TORONTO, LOMBARD,
     etc.) que operamos pero cuyas tenencias no son AuM real, son
     cuotapartes. Match por id (no por la denominación) porque el formato
     difiere entre fuentes — la tenencia trae prefijo "[NN] ".
  4. `cuenta` contiene como palabra completa un nombre de contraparte —
     `\bNOMBRE\b` case-insensitive sobre los valores únicos de las
     contrapartes (ADCAP, ALLARIA, BALANZ, ...).
     Cubre cuentas que se nos escapan de la regla 3 porque su id_cuenta
     no quedó alineado con el de la tabla de contrapartes.
  5. `unidad == "ARS"` para `[100]` y `[101]` (decisión puntual de negocio:
     no contabilizar el cash ARS de esas dos cuentas en el AuM).

Las reglas 3 y 4 viven en BD (no se hardcodean) — el equipo edita la lista
desde el panel de Contrapartes y la exclusión la respeta sola.
"""
from __future__ import annotations

import re

# Sub-strings (case-insensitive) en `cuenta` o `unidad` que disparan exclusión
# por patrón. Todo lo que no es patrón (sociedades gerentes específicas) sale
# de Mongo via `load_contrapartes_id_cuentas()`.
#   OTC → operaciones OTC, no contabilizan en AuM.
#   CDC → cuentas CDC, mismo criterio.
EXCLUDE_PATTERN_KEYWORDS: tuple[str, ...] = ("OTC", "CDC")

# Match exacto en `unidad` — códigos cortos donde un substring matchearía
# falsos positivos.
EXCLUDE_UNIDAD_EXACT: frozenset[str] = frozenset({"USDL"})

# Cuentas donde se descarta específicamente la tenencia ARS (cash) — el
# negocio decidió no contabilizar el efectivo de estas cuentas en el AuM.
CUENTAS_SIN_ARS: frozenset[str] = frozenset({
    "[100] ACA VALORES S.A.",
    "[101] ASOCIACION DE COOPERATIVAS ARGENTINAS COOP LTDA",
})

_RE_PATTERN = re.compile(
    "|".join(re.escape(k) for k in EXCLUDE_PATTERN_KEYWORDS),
    re.IGNORECASE,
)


# Strings que aparecen en `contraparte` pero no son nombres reales.
_CONTRAPARTE_PLACEHOLDERS: frozenset[str] = frozenset({
    "", "NO APLICA", "N/A", "NONE", "NULL", "-",
})

# Mínimo de caracteres para que un nombre se use como criterio de match.
# Nombres muy cortos (1-2 chars) darían falsos positivos masivos.
_MIN_CONTRAPARTE_NAME_LEN = 3


def load_contrapartes_names() -> frozenset[str]:
    """Lee `clientes.contrapartes` (SQL) y devuelve nombres únicos de
    `contraparte` (uppercase, sin placeholders, len >= 3). Usado para
    matchear por palabra completa contra `cuenta` del AuM cuando el
    matching por id_cuenta no alcanza."""
    from core.postgres import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT contraparte FROM contrapartes WHERE contraparte IS NOT NULL")
        raw = [r[0] for r in cur.fetchall()]
    out: set[str] = set()
    for r in raw:
        if not isinstance(r, str):
            continue
        s = r.strip().upper()
        if not s or s in _CONTRAPARTE_PLACEHOLDERS or len(s) < _MIN_CONTRAPARTE_NAME_LEN:
            continue
        out.add(s)
    return frozenset(out)


def _build_contrapartes_regex(names: frozenset[str] | set[str]) -> str | None:
    """Arma `\\b(NOMBRE1|NOMBRE2|...)\\b` para matcheo case-insensitive
    sobre `cuenta`. Devuelve None si no hay nombres."""
    if not names:
        return None
    # Sorted por longitud desc — Mongo regex es greedy y matchea primero
    # el más largo que aplique en una posición dada.
    sorted_names = sorted(names, key=lambda x: (-len(x), x))
    return r"\b(?:" + "|".join(re.escape(n) for n in sorted_names) + r")\b"


def load_contrapartes_id_cuentas() -> frozenset[str]:
    """Lee `clientes.contrapartes.id_cuenta` (SQL) y devuelve el set como strings.
    Match por id (no por la denominación) porque el formato de cuenta difiere
    entre tenencia (prefijo "[NN] ") y contrapartes — el id numérico es la única
    clave estable."""
    from core.postgres import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_cuenta FROM contrapartes "
                    "WHERE id_cuenta IS NOT NULL AND id_cuenta <> ''")
        return frozenset(str(r[0]) for r in cur.fetchall())


def is_excluded(
    cuenta: str | None,
    unidad: str | None,
    id_cuenta: str | int | None = None,
    contrapartes_ids: frozenset[str] | set[str] | None = None,
    contrapartes_names: frozenset[str] | set[str] | None = None,
) -> bool:
    """True si esta combinación NO debe persistirse (ni quedar) en AuM.

    `contrapartes_ids` (regla 3): set de `id_cuenta` a excluir.
    `contrapartes_names` (regla 4): set de nombres de contraparte; si la
    `cuenta` los contiene como palabra completa, excluye.
    """
    cuenta = cuenta or ""
    unidad = unidad or ""
    if unidad in EXCLUDE_UNIDAD_EXACT:
        return True
    if _RE_PATTERN.search(unidad) or _RE_PATTERN.search(cuenta):
        return True
    if (
        contrapartes_ids
        and id_cuenta is not None
        and str(id_cuenta) in contrapartes_ids
    ):
        return True
    if contrapartes_names and cuenta:
        pattern = _build_contrapartes_regex(contrapartes_names)
        if pattern and re.search(pattern, cuenta, re.IGNORECASE):
            return True
    return unidad == "ARS" and cuenta in CUENTAS_SIN_ARS


