"""api/services/cuentas_sql.py — accionistas y contrapartes (catálogos de cuenta).

Servicio PURO (sin FastAPI). Sirve `/api/cuentas/*` (`api/routers/cuentas.py`).
Fuente única SQL: `clientes.accionistas` (`cuenta` viene como "[N] NOMBRE" y de
ahí se derivan `id_cuenta` y `nombre`; carga manual con
`scripts/cargar_accionistas.py`) y `clientes.contrapartes` (`id_cuenta` es la
clave, `contraparte` el nombre y `segmento` el grupo). Ambas devuelven el MISMO
shape `{cuenta, id_cuenta, nombre, grupo}` para que el front las trate igual.
"""
from __future__ import annotations

import re

from api.services._sql import _q

_RE_CUENTA = re.compile(r"^\[(\d+)\]\s*(.*)$")


def listar_accionistas() -> list[dict]:
    rows = _q(
        "SELECT cuenta, accionista FROM accionistas "
        "WHERE cuenta IS NOT NULL AND cuenta <> '' ORDER BY cuenta"
    )
    out = []
    for r in rows:
        raw = str(r["cuenta"] or "").strip()
        if not raw:
            continue
        m = _RE_CUENTA.match(raw)
        out.append({
            "cuenta": raw,
            "id_cuenta": m.group(1) if m else None,
            "nombre": m.group(2).strip() if m else raw,
            "grupo": r["accionista"] or "",
        })
    return out


def listar_contrapartes() -> list[dict]:
    rows = _q(
        "SELECT id_cuenta, contraparte, segmento FROM contrapartes "
        "WHERE id_cuenta IS NOT NULL AND id_cuenta <> '' ORDER BY id_cuenta"
    )
    return [
        {"cuenta": r["id_cuenta"], "id_cuenta": r["id_cuenta"],
         "nombre": r["contraparte"] or "", "grupo": r["segmento"] or ""}
        for r in rows
    ]
