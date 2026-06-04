"""Compliance — compara el operador asignado por la mesa vs el que reporta Aunesa.

Objetivo (rol `compliance`): detectar cuentas cuyo operador en NUESTRA base
(`Clientes.Comitentes.operador_email`, que la mesa edita a mano) difiere del que
Aunesa reporta EN VIVO (`administrador.operador.email` de `cuentas/listadoCuentas`).

Recordá (ver jobs/sync_comitentes.py): el operador de Aunesa se copia a nuestra
base SOLO al crear la cuenta y nunca se vuelve a pisar → con el tiempo divergen
(la mesa reasigna, o Aunesa cambia). Compliance hace el cruce on-the-fly, NO
persiste nada. Cache de 5 min para no martillar Aunesa en cada refresh.
"""
from __future__ import annotations

from typing import Any

from api.cache import cached
from api.db import get_db_clientes
from core import aunesa


def _aunesa_operador(c: dict) -> tuple[str | None, str | None]:
    """(email, nombre) del operador que Aunesa reporta para la cuenta."""
    op = (c.get("administrador") or {}).get("operador") or {}
    email = (op.get("email") or "").strip() or None
    nombre = op.get("nombreReal") or op.get("nombre")
    return email, nombre


@cached(ttl=300)
def comparar_operadores() -> dict[str, Any]:
    """Todas las cuentas comitente con operador NUESTRO vs AUNESA (live), marcando
    las que difieren. Cruce por id_cuenta. No persiste.

    categoria por fila: `ok` (coinciden) · `distinto` (ambos tienen, no coinciden)
    · `falta_en_nuestra_base` (Aunesa sí, nosotros no) · `falta_en_aunesa`."""
    # 1) Aunesa EN VIVO (mismo endpoint/cliente que sync_comitentes).
    resp = aunesa.get("cuentas/listadoCuentas", params={"tipoCuenta": "Comitente"})
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        data = data.get("cuentas") or data.get("data") or [data]
    aunesa_cta: dict[str, dict] = {}
    for c in data:
        idc = str(c.get("id")) if c.get("id") is not None else None
        if not idc:
            continue
        email, nombre = _aunesa_operador(c)
        aunesa_cta[idc] = {"denominacion": c.get("denominacion"),
                           "email": email, "nombre": nombre}

    # 2) Nuestra base.
    nuestro_cta = {
        str(d["id_cuenta"]): d
        for d in get_db_clientes()["Comitentes"].find(
            {}, {"_id": 0, "id_cuenta": 1, "denominacion": 1,
                 "operador_email": 1, "operador_nombre": 1},
        )
        if d.get("id_cuenta")
    }

    # 3) Cruce sobre la unión de cuentas.
    filas: list[dict] = []
    n_dif = 0
    for idc in set(aunesa_cta) | set(nuestro_cta):
        a = aunesa_cta.get(idc)
        n = nuestro_cta.get(idc)
        nuestro_email = ((n or {}).get("operador_email") or "").strip() or None
        aunesa_email = (a or {}).get("email")
        if n is None:
            cat = "falta_en_nuestra_base"
        elif a is None:
            cat = "falta_en_aunesa"
        elif (nuestro_email or "") == (aunesa_email or ""):
            cat = "ok"
        else:
            cat = "distinto"
        difiere = cat != "ok"
        n_dif += difiere
        filas.append({
            "id_cuenta":      idc,
            "denominacion":   (n or {}).get("denominacion") or (a or {}).get("denominacion"),
            "nuestro_email":  nuestro_email,
            "nuestro_nombre": (n or {}).get("operador_nombre"),
            "aunesa_email":   aunesa_email,
            "aunesa_nombre":  (a or {}).get("nombre"),
            "categoria":      cat,
            "difiere":        difiere,
        })

    # Difieren primero, después por denominación → el auditor ve lo importante arriba.
    filas.sort(key=lambda f: (not f["difiere"], (f["denominacion"] or "").lower()))
    return {"filas": filas, "total": len(filas), "difieren": n_dif}
