"""operaciones_view.py — helpers PUROS compartidos de la vista Operaciones.

Quedan acá los helpers que el router todavía usa:
  - `ddmmyyyy_a_iso`: parser de fecha de CashFlow.Movimientos (vista FLUJOS, Mongo
    VIVA — esa colección aún no se migró).
  - `motor`: selector SQL/Mongo por flag (lo usa el selector de COMERCIAL).
  - `OPS_MONEDAS`: validación de moneda.

Los cuerpos Mongo de /ops/* (`ops_serie_mongo`/`ops_resumen_mongo`/`ops_agro_mongo`/
`ops_aranceles_mongo` + sus helpers de rollup/match) se ELIMINARON: leían
`CashFlow.Operaciones`/`OpsSerieDiaria`/`Clientes.Comitentes`, DROPEADAS de Mongo
(2026-06-15/16). Esas vistas son SQL-native (`api/services/operaciones_sql.py`).
REGLA #1 (19/6): Mongo se apaga; el código muerto se borra.
"""
from __future__ import annotations

import os
from datetime import datetime

OPS_MONEDAS = ("ARS", "USD", "USD_DOL")


def ddmmyyyy_a_iso(raw: str | None) -> str | None:
    """'02/07/2025' (dd/mm/yyyy) → '2025-07-02'. None si no parsea.
    CashFlow.Movimientos guarda la fecha en dd/mm/yyyy; la API la sirve en ISO."""
    try:
        return datetime.strptime((raw or "").strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def motor(req: str | None, env: str = "OPERACIONES_SQL") -> str:
    """Motor de datos para una vista. Override por request `?_engine=sql|mongo`;
    si no, el global `env`=1 → 'sql', sino 'mongo'. Cada vista tiene su flag →
    cutover independiente."""
    if req in ("sql", "mongo"):
        return req
    return "sql" if os.getenv(env) == "1" else "mongo"
