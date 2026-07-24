"""Feed Eikon — futuros de commodities de CHICAGO (CBOT) para AGRO → tab CHICAGO.

Mismo riel que los quotes de equities US (`core/eikon_live.py`): el MISMO script
local de oficina (`scripts/eikon_feed_simple.py`, Workspace abierto) suscribe los
RICs de continuación de CBOT y postea a `POST /api/ingest/eikon/chicago/quotes`;
la API persiste crudo en SQL `mercado.eikon_chicago_snapshot` (1 fila por RIC).
La PC NO toca la base ni conoce los factores.

Modelado (viene del script de commodities original del user, 2026-07-24):
  - FAMILIAS: 5 commodities, cada una con sus RICs de continuación (`Sc1` =
    soja posición 1 = contrato más cercano, `Sc2` el siguiente, etc. — OJO:
    Aceite y Harina saltean la posición 3, así estaba en el script original).
  - Campos Eikon: CONTR_MNTH (mes del contrato, ej 'JUL6'), PRIMACT_1 (last de
    FUTUROS — para acciones es CF_LAST, ver INTEGRACION_REUTERS.md §5) y
    SEC_ACT_1 (variación neta del día, misma unidad que el precio).
  - FACTORES a USD/tonelada viven ACÁ (server-side, fuente única): Chicago
    cotiza en ¢/bushel (soja/maíz/trigo), ¢/libra (aceite) y USD/short ton
    (harina). Se convierten AL LEER (`tablero_chicago`) — la tabla guarda crudo.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from core.pg_mirror import write_native
from core.postgres import get_pool

TABLE = "mercado.eikon_chicago_snapshot"

# Semáforo EN LÍNEA de la vista: el feed manda las 25 filas en CADA loop
# (heartbeat, sin cache-diff — payload mínimo) → si el último updated_at tiene
# menos de este TTL, el script de oficina está prendido.
# OJO con ajustar esto a la baja: el loop REAL del feed no es 20s — es 20s de
# sleep + lo que tarden los get_data de Eikon (184 RICs de acciones + 25 CBOT),
# ~30-40s típico y con picos peores cuando Eikon viene lento. Con TTL=60s el
# semáforo TITILABA (rojo/verde) con el script prendido (visto 2026-07-24).
# 180s = varios latidos de margen; "apagado" se detecta en ~3 minutos.
ONLINE_TTL_S = 180

# familia (key estable) → label de la vista, RICs de continuación y factor de
# conversión a USD/tonelada. Orden del dict = orden de las tablas en la vista.
FAMILIAS: dict[str, dict] = {
    "soja":        {"label": "Soja",           "factor": 0.367437,
                    "rics": ["Sc1", "Sc2", "Sc3", "Sc4", "Sc5"]},
    "aceite_soja": {"label": "Aceite de Soja", "factor": 22.04585538,
                    "rics": ["BOc1", "BOc2", "BOc4", "BOc5", "BOc6"]},
    "maiz":        {"label": "Maíz",           "factor": 0.393685,
                    "rics": ["Cc1", "Cc2", "Cc3", "Cc4", "Cc5"]},
    "trigo":       {"label": "Trigo",          "factor": 0.367444,
                    "rics": ["Wc1", "Wc2", "Wc3", "Wc4", "Wc5"]},
    "harina_soja": {"label": "Harina de Soja", "factor": 1.102292769,
                    "rics": ["SMc1", "SMc2", "SMc4", "SMc5", "SMc6"]},
}

# RIC → (familia, posición de continuación). La posición sale del sufijo del RIC
# (`BOc4` → 4), NO del índice en la lista (hay familias que saltean la 3).
_RE_POS = re.compile(r"c(\d+)$")


def _pos(ric: str) -> int:
    m = _RE_POS.search(ric)
    return int(m.group(1)) if m else 0


RIC_FAMILIA: dict[str, str] = {
    ric: fam for fam, cfg in FAMILIAS.items() for ric in cfg["rics"]
}


def universo_chicago() -> list[dict]:
    """Lista de suscripción del feed (GET /api/ingest/eikon/chicago/universo):
    [{ric, familia}]. Sale de la constante — no hay catálogo editable (los RICs
    de continuación de CBOT son fijos)."""
    return [{"ric": ric, "familia": fam} for ric, fam in RIC_FAMILIA.items()]


def upsert_chicago(docs: list[dict]) -> int:
    """Upsertea quotes del feed en `mercado.eikon_chicago_snapshot` (1 fila por
    RIC, crudo sin factor). Solo acepta RICs del universo conocido (payload con
    un RIC ajeno se ignora — el token de ingesta no decide el modelo). Mismo
    contrato que eikon_live.upsert_quotes: `updated_at` lo pone el server."""
    if not docs:
        return 0
    now = datetime.now(UTC)
    rows: list[dict] = []
    for data in docs:
        if not isinstance(data, dict):
            continue
        ric = (data.get("ric") or "").strip()
        if ric not in RIC_FAMILIA:
            continue
        rows.append({
            "ric":        ric,
            "familia":    RIC_FAMILIA[ric],
            "data":       data,          # dict → jsonb passthrough (mes/last/var_neta)
            "updated_at": now,
        })
    if not rows:
        return 0
    return write_native(TABLE, ["ric"], rows)


def _num(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _fila(ric: str, factor: float, data: dict, updated_at) -> dict:
    """Fila de la vista: precio/variación convertidos a USD/t con el factor de
    la familia. Cualquier pata faltante → None (celda vacía, nunca rompe)."""
    last = _num(data.get("last"))
    var = _num(data.get("var_neta"))
    return {
        "ric":        ric,
        "posicion":   _pos(ric),
        "mes":        data.get("mes"),
        "precio":     last * factor if last is not None else None,
        "variacion":  var * factor if var is not None else None,
        "updated_at": updated_at,
    }


def tablero_chicago() -> dict:
    """Payload de GET /api/derivados/agro/chicago: una tabla por familia (orden
    de FAMILIAS), filas por posición de continuación, precios en USD/tonelada.
    Las familias sin datos (feed nunca prendido) salen con rows=[] — la vista
    lo muestra como "sin datos" en vez de esconder la tabla."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT ric, data, updated_at FROM {TABLE}")
        snap = {ric: (data or {}, upd) for ric, data, upd in cur.fetchall()}

    familias = []
    for fam, cfg in FAMILIAS.items():
        rows = [
            _fila(ric, cfg["factor"], *snap[ric])
            for ric in cfg["rics"] if ric in snap
        ]
        rows.sort(key=lambda r: r["posicion"])
        familias.append({
            "familia":    fam,
            "label":      cfg["label"],
            "rows":       rows,
            "updated_at": max((r["updated_at"] for r in rows), default=None),
        })
    # Semáforo: prendido si el heartbeat del feed llegó hace < ONLINE_TTL_S.
    max_upd = max((f["updated_at"] for f in familias if f["updated_at"]), default=None)
    online = bool(
        max_upd and datetime.now(UTC) - max_upd < timedelta(seconds=ONLINE_TTL_S)
    )
    return {"familias": familias, "unidad": "USD/t",
            "updated_at": max_upd, "online": online}
