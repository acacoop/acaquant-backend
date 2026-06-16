"""Clasificación patrimonial de clientes (escribe a `Clientes.Comitentes.nivel_3`).

`clasificar_nivel_3()` es **pura**: no toca Mongo, no pega nada — recibe
inputs y devuelve el label. Lo usan tanto el job `jobs.segmentar_patrimonial`
(re-clasifica TODO) como el endpoint `bulk-fondeo` (re-clasifica solo las
cuentas tocadas al subir el Excel).

Reglas (ver docs/SEGMENTACION_PATRIMONIAL.md):

- Personas Humanas (PH) — umbral en **USD** vía MEP del día:
  - `< 50.000`              → `PH RETAIL`
  - `≥ 50.000 ≤ 100.000`    → `PH MEDIO RETAIL`
  - `> 100.000`             → `PH ALTO PATRIMONIO`

- Personas Jurídicas (PJ) — umbral en **UVAs**:
  - `≤ 350.000`             → `PJ PEQUEÑA`
  - `> 350.000 ≤ 700.000`   → `PJ MEDIANA`
  - `> 700.000`             → `PJ GRANDE`

- **Excepción**: `tipo_cliente == "Fondo Común de Inversión"` (señal fresca de Aunesa)
  o `id_cuenta` ∈ `CashFlow.Contrapartes` → SIEMPRE `PJ GRANDE` (no depende de cupo/UVA).

Los labels llevan prefijo PH/PJ y van en MAYÚSCULAS — convención del
sistema para todos los nivel_1..5 (evita duplicados por capitalización:
"Productores" vs "PRODUCTORES"). Bulk y PATCH del manager también
`.upper()` antes de persistir.

PH/PJ se distingue por `tipo_cliente` (mapping confirmado contra 1773 cuentas
reales, ver `scripts/diag_tipo_cliente.py`).

Devuelve `None` (= sin clasificar) cuando falta input:
- `cupo_transaccional_ars` ausente o ≤ 0.
- `tipo_cliente` `None` o no mapeado.
- `mep` ausente (para PH) o `uva` ausente (para PJ).
"""
from __future__ import annotations

# Mapping PH/PJ desde tipo_cliente — confirmado 2026-05-28 sobre 1773 cuentas
# (commit 988d8f3 / scripts/diag_tipo_cliente.py). Si Aunesa agrega tipos
# nuevos, caen en `None` (sin clasificar) y el diag los detecta al re-correr.
_TIPOS_PH: frozenset[str] = frozenset({"Persona", "Empleado"})
_TIPOS_PJ: frozenset[str] = frozenset({
    "Empresa",
    "Fondo Común de Inversión",
    "Compañía de seguros",
    "Fideicomiso",
    "Institucional",
})

# Un FCI es SIEMPRE PJ GRANDE (regla de negocio). Señal fresca del sync de Aunesa
# — más confiable que CashFlow.Contrapartes, que puede quedar desactualizado.
_TIPO_FCI = "Fondo Común de Inversión"

# Umbrales — orden de las tablas del doc (PH en USD, PJ en UVAs).
_UMBRAL_PH_RETAIL_USD = 50_000.0
_UMBRAL_PH_MEDIO_USD  = 100_000.0
_UMBRAL_PJ_PEQUENA_UVA = 350_000.0
_UMBRAL_PJ_MEDIANA_UVA = 700_000.0


def clasificar_nivel_3(
    tipo_cliente: str | None,
    cupo_transaccional_ars: float | None,
    *,
    mep: float | None,
    uva: float | None = None,
    es_contraparte: bool = False,
) -> str | None:
    """Devuelve el label de segmento patrimonial para una cuenta, o None."""
    # FCI (tipo_cliente) o contraparte (id ∈ CashFlow.Contrapartes — sociedades
    # gerentes, etc.) → SIEMPRE PJ GRANDE, sin importar cupo/UVA. Va ANTES del
    # check de cupo.
    if tipo_cliente == _TIPO_FCI or es_contraparte:
        return "PJ GRANDE"
    if not tipo_cliente or cupo_transaccional_ars is None or cupo_transaccional_ars <= 0:
        return None

    if tipo_cliente in _TIPOS_PH:
        if not mep or mep <= 0:
            return None
        cupo_usd = cupo_transaccional_ars / mep
        if cupo_usd < _UMBRAL_PH_RETAIL_USD:
            return "PH RETAIL"
        if cupo_usd <= _UMBRAL_PH_MEDIO_USD:
            return "PH MEDIO RETAIL"
        return "PH ALTO PATRIMONIO"

    if tipo_cliente in _TIPOS_PJ:
        if not uva or uva <= 0:
            return None
        cupo_uva = cupo_transaccional_ars / uva
        if cupo_uva <= _UMBRAL_PJ_PEQUENA_UVA:
            return "PJ PEQUEÑA"
        if cupo_uva <= _UMBRAL_PJ_MEDIANA_UVA:
            return "PJ MEDIANA"
        return "PJ GRANDE"

    # tipo_cliente desconocido (Aunesa agregó algo nuevo) → sin clasificar.
    return None


def cargar_ids_contrapartes(db_cashflow=None) -> set[str]:
    """Set de `id_cuenta` que son contrapartes (SQL `clientes.contrapartes`) →
    PJ GRANDE por regla de negocio. `db_cashflow` se acepta por compat (se ignora).
    NO es puro (lee SQL) — separado a propósito de `clasificar_nivel_3`."""
    from core.postgres import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_cuenta FROM contrapartes "
                    "WHERE id_cuenta IS NOT NULL AND id_cuenta <> ''")
        return {str(r[0]).strip() for r in cur.fetchall()}
