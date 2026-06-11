"""operaciones_view.py — lógica PURA de la vista Operaciones (negocio + ops).

Constructores de match/agregación Mongo, selector de motor SQL/Mongo y las
series con live-fallback sobre el rollup. Antes vivían como helpers privados
dentro del router `api/routers/operaciones.py` (1.737 líneas) — al moverlos
acá: (1) se pueden testear sin levantar FastAPI, (2) el router vuelve a ser
thin HTTP plumbing, (3) el MCP / futuros jobs pueden reusarlos. El router los
importa de vuelta con sus nombres `_*` originales (alias) → cero cambios en
los call sites. AUDITORIA A2.

Reglas de dominio NO inferibles (ver CLAUDE.md "Operaciones — rollup, NO
escanear"): el cierre de caución (`es_cierre`) separa VOLUMEN de ARANCEL —
volumen lo excluye (la apertura ya cuenta el nocional), arancel lo INCLUYE
(el fee de caución vive solo en el cierre). `etapa=solicitud/liquidacion`
desdobla el FCI bilateral para no doblar el volumen.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from api.db import get_db_clientes

# La serie del gráfico de aranceles se acota por defecto a esta ventana (cubre
# de sobra los botones 1W…1A). El aggregate sobre toda la historia escanea
# ~180k-300k docs y el FETCH es inevitable. El botón ALL del front pide
# serie_full=True para traer la historia completa bajo demanda.
SERIE_VENTANA_DIAS = 550  # ~18 meses
OPS_MONEDAS = ("ARS", "USD", "USD_DOL")


def ddmmyyyy_a_iso(raw: str | None) -> str | None:
    """'02/07/2025' (dd/mm/yyyy) → '2025-07-02'. None si no parsea.
    CashFlow.Movimientos guarda la fecha en dd/mm/yyyy; la API la sirve en ISO."""
    try:
        return datetime.strptime((raw or "").strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def motor(req: str | None, env: str = "OPERACIONES_SQL") -> str:
    """Motor de datos para una vista. Override por request `?_engine=sql|mongo`
    (A/B en prod); si no, el global `env`=1 → 'sql', sino 'mongo'. Mongo es el
    default hasta el cutover. Cada vista tiene su flag → cutover independiente.
    La salida SQL == Mongo (validado por scripts/compare_*)."""
    if req in ("sql", "mongo"):
        return req
    return "sql" if os.getenv(env) == "1" else "mongo"


def importe_convertido(moneda: str) -> dict:
    """|importe| convertido a la moneda destino con el `mep` snapshot de CADA
    boleto (conversión histórica exacta — NO al MEP de hoy). Misma moneda →
    directo; ARS→USD → /mep; USD→ARS → ×mep. Si a un boleto de otra moneda le
    falta `mep`, aporta 0 (no se puede convertir sin su mep del día)."""
    abs_imp = {"$abs": {"$ifNull": ["$importe", 0]}}
    mep = {"$ifNull": ["$mep", 0]}
    if (moneda or "ARS").upper() == "USD":
        return {"$cond": [
            {"$eq": ["$moneda", "USD"]},
            abs_imp,
            {"$cond": [{"$gt": [mep, 0]}, {"$divide": [abs_imp, "$mep"]}, 0]},
        ]}
    return {"$cond": [
        {"$eq": ["$moneda", "ARS"]},
        abs_imp,
        {"$multiply": [abs_imp, mep]},
    ]}


def valor_si_categoria(target: str, conv: dict) -> dict:
    """`conv` (importe ya convertido a la moneda destino) si categoria ==
    target, sino 0. Para los $group por categoría de la serie / matrix."""
    return {"$cond": [{"$eq": ["$categoria", target]}, conv, 0]}


def ops_match(
    moneda: str,
    mercado: str | None,
    operacion: str | None = None,
    denominacion: str | None = None,
    cuenta: str | None = None,
    segmento: str | None = None,
) -> dict:
    # FCI bilateral aparece 2 veces (solicitud DOC + liquidación CL). Para NO doblar
    # el volumen se cuenta UNA sola vez, en el día "correcto":
    #   · SUSCRIPCIÓN → su SOLICITUD (día del pedido; así se ve en el día y no recién
    #     al liquidar T+1). Se EXCLUYE su liquidación.
    #   · RESCATE     → su LIQUIDACIÓN (la solicitud del rescate viene en 0). Se
    #     EXCLUYE su solicitud.
    # Todo lo demás (boletos normales SIN etapa, liquidaciones de rescate) pasa.
    # OJO: una suscripción CL muy vieja SIN su DOC quedaría excluida (riesgo asumido).
    m: dict = {"moneda": moneda, "es_cierre": False,
               "$nor": [
                   {"operacion": "Suscripción", "etapa": "liquidacion"},
                   {"operacion": "Rescate", "etapa": "solicitud"},
               ]}
    if mercado and mercado.lower() != "todos":
        m["mercado"] = mercado
    if operacion:
        m["operacion"] = operacion
    if denominacion:
        m["denominacion"] = denominacion
    if cuenta:
        m["cuenta"] = cuenta
    if segmento and segmento.lower() != "todos":
        m["segmento"] = segmento
    return m


def arancel_match(
    moneda: str,
    mercado: str | None = None,
    *,
    segmento: str | None = None,
    denominacion: str | None = None,
    cuenta: str | None = None,
) -> dict:
    """Como `ops_match` pero para sumar ARANCEL: incluye los CIERRES con arancel.

    El arancel de caución vive SOLO en el cierre (es_cierre=True); las aperturas
    NO traen arancel (verificado con scripts/diag_aranceles_caucion: $121,6M en el
    cierre, 0 en la apertura). El filtro de volumen (ops_match → es_cierre=False)
    los excluía y los PERDÍA. Acá los incluimos sin arrastrar compras/ventas: los
    cierres que NO son caución no tienen arancel, así que el `$or` sólo suma fees.
    """
    m = ops_match(moneda, mercado, denominacion=denominacion, cuenta=cuenta, segmento=segmento)
    del m["es_cierre"]
    m["$or"] = [{"es_cierre": False}, {"es_cierre": True, "arancel": {"$ne": 0}}]
    return m


def serie_bruto_rollup(cf, moneda, mercado, operacion, segmento) -> list[dict] | None:
    """Serie Σ bruto por fecha desde el rollup CashFlow.OpsSerieDiaria (histórico
    CERRADO, fecha < hoy) + el día de HOY en vivo (live-fallback). Devuelve None si
    el rollup está vacío (no construido) → el caller cae a la query live completa.

    Solo para el caso común (sin filtro de alta cardinalidad ni scope). El rollup
    ya pre-filtra es_cierre/solicitud igual que ops_match → equivalente."""
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date().isoformat()  # ART
    m: dict = {"moneda": moneda, "fecha": {"$lt": hoy}}
    if mercado and mercado.lower() != "todos":
        m["mercado"] = mercado
    if operacion:
        m["operacion"] = operacion
    if segmento and segmento.lower() != "todos":
        m["segmento"] = segmento
    rollup = list(cf["OpsSerieDiaria"].aggregate([
        {"$match": m},
        {"$group": {"_id": "$fecha", "bruto": {"$sum": "$bruto"}}},
    ]))
    if not rollup:
        return None  # rollup no construido para esta moneda → live
    serie = {r["_id"]: r["bruto"] for r in rollup}
    # Hoy en vivo (un solo día → índice concertacion, barato). El rollup llega a ayer.
    hoy_match = ops_match(moneda, mercado, operacion, None, None, segmento)
    hoy_match["concertacion"] = hoy
    hoy_doc = next(iter(cf["Operaciones"].aggregate([
        {"$match": hoy_match},
        {"$group": {"_id": None, "bruto": {"$sum": {"$ifNull": ["$bruto", 0]}}}},
    ])), None)
    if hoy_doc and hoy_doc.get("bruto"):
        serie[hoy] = hoy_doc["bruto"]
    return [{"fecha": f, "bruto": round(serie[f], 2)} for f in sorted(serie)]


def serie_arancel_rollup(cf, segmento, plen, serie_full) -> list[dict] | None:
    """Serie Σ arancel (PESOS, todas las monedas) por periodo desde OpsSerieDiaria
    (histórico < hoy) + hoy en vivo. El arancel es siempre en pesos → NO se filtra
    por moneda. None si el rollup está vacío → live. plen=7 mensual / 10 diario."""
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date().isoformat()  # ART
    m: dict = {"fecha": {"$lt": hoy}}
    if segmento and segmento.lower() != "todos":
        m["segmento"] = segmento
    if not serie_full:
        cutoff = (datetime.now(UTC) - timedelta(hours=3)
                  - timedelta(days=SERIE_VENTANA_DIAS)).date().isoformat()
        m["fecha"] = {"$gte": cutoff, "$lt": hoy}
    rollup = list(cf["OpsSerieDiaria"].aggregate([
        {"$match": m},
        {"$group": {"_id": {"$substr": ["$fecha", 0, plen]}, "ar": {"$sum": "$arancel"}}},
    ]))
    if not rollup:
        return None
    serie = {r["_id"]: r["ar"] for r in rollup}
    # Hoy en vivo (un día → índice concertacion). abs(arancel), sin filtro de moneda.
    # arancel_match: incluye los cierres de caución (es donde está el arancel).
    hoy_match = arancel_match("ARS", segmento=segmento)
    hoy_match.pop("moneda", None)
    hoy_match["concertacion"] = hoy
    hoy_doc = next(iter(cf["Operaciones"].aggregate([
        {"$match": hoy_match},
        {"$group": {"_id": None, "ar": {"$sum": {"$abs": {"$ifNull": ["$arancel", 0]}}}}},
    ])), None)
    if hoy_doc and hoy_doc.get("ar"):
        p = hoy[:plen]
        serie[p] = serie.get(p, 0) + hoy_doc["ar"]
    return [{"periodo": p, "arancel": round(serie[p], 2)} for p in sorted(serie)]


def op_cuentas(operador: str | None, scope: tuple[str, ...] | None) -> list[str] | None:
    """operador_email → id_cuentas de ese operador (intersecta con scope si lo
    hay). None si no se filtra por operador. En Operaciones el campo `cuenta`
    guarda el id_cuenta, así que se aplica como `match['cuenta'] = {$in: ...}`."""
    if not operador:
        return None
    cuentas = [str(c["id_cuenta"]) for c in get_db_clientes()["Comitentes"].find(
        {"operador_email": operador}, {"_id": 0, "id_cuenta": 1}) if c.get("id_cuenta")]
    if scope is not None:
        scope_set = set(scope)
        cuentas = [c for c in cuentas if c in scope_set]
    return cuentas
