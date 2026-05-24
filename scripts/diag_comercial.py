"""scripts/diag_comercial.py — QA de la vista COMERCIAL (OPERACIONES).

Valida números + performance:
  1. Cronometra cada endpoint del service (cache fría: primer hit por proceso).
  2. Corre explain() en las queries calientes de NegocioMovimientos / AuM y
     reporta IXSCAN (índice OK) vs COLLSCAN (escaneo total = problema).
  3. Chequea invariantes de consistencia (Σ por cuenta == total).

Read-only. Correr en el Droplet tras git pull + backfill:
    python -m scripts.diag_comercial
    python -m scripts.diag_comercial --operador alguien@acavalores.com.ar
"""
from __future__ import annotations

import argparse
import time

from api.services.comercial import (
    _CATS_OPERACIONES,
    _CATS_VOLUMEN,
    listar_operadores_comercial,
    operaciones_cliente,
    operador_comercial,
    portafolio_cliente,
    serie_comercial,
)
from core.mongo import get_mongo_client_read


def _timed(label, fn, *a, **k):
    t0 = time.perf_counter()
    out = fn(*a, **k)
    ms = (time.perf_counter() - t0) * 1000
    print(f"  {ms:8.1f} ms  {label}")
    return out


def _stage(plan: dict) -> str:
    """Recorre el winningPlan buscando IXSCAN/COLLSCAN."""
    cur = plan
    while cur:
        st = cur.get("stage")
        if st == "IXSCAN":
            return f"IXSCAN [{cur.get('indexName')}]"
        if st == "COLLSCAN":
            return "⚠️ COLLSCAN (sin índice)"
        cur = cur.get("inputStage")
    return "?"


def _explain(coll, filtro, sort=None) -> str:
    q = coll.find(filtro)
    if sort:
        q = q.sort(sort)
    plan = q.explain().get("queryPlanner", {}).get("winningPlan", {})
    return _stage(plan)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--operador", help="operador_email (default: el de más cuentas)")
    args = ap.parse_args()

    ops = listar_operadores_comercial()
    if not ops:
        print("❌ Sin operadores en Clientes.Comitentes.")
        return
    email = args.operador or ops[0]["operador_email"]
    print(f"Operadores: {len(ops)}. Diagnóstico para: {email}\n")

    print("── Latencias (cache fría) ──")
    data = _timed("operador_comercial", operador_comercial, operador=email)
    _timed("serie_comercial volumen", serie_comercial, operador=email, metric="volumen")
    _timed("serie_comercial aum", serie_comercial, operador=email, metric="aum")
    cli = data["clientes"]
    idc = cli[0]["id_cuenta"] if cli else None
    if idc:
        _timed("serie_comercial cliente", serie_comercial, operador=email, metric="volumen", id_cuenta=idc)
        _timed("portafolio_cliente", portafolio_cliente, id_cuenta=idc)
        _timed("operaciones_cliente", operaciones_cliente, id_cuenta=idc)

    r = data["resumen"]
    print(f"\n── Números ── AuM={r['aum_gestionado']:,.0f} · clientes={r['n_clientes']} · "
          f"MTD={r['volumen_mtd']:,.0f} · YTD={r['volumen_ytd']:,.0f}")

    # Invariante: Σ Vol YTD por cuenta == Vol YTD total (mismo origen).
    suma_ytd = round(sum(c["volumen_ytd"] for c in cli), 2)
    ok = abs(suma_ytd - r["volumen_ytd"]) < 1.0
    print(f"  invariante Σ(volumen_ytd cuentas)={suma_ytd:,.0f} vs total={r['volumen_ytd']:,.0f} "
          f"→ {'OK' if ok else '❌ DESCUADRA'}")

    print("\n── Índices (explain de las queries calientes) ──")
    db = get_mongo_client_read()
    nm = db["CashFlow"]["NegocioMovimientos"]
    aum = db["Valuaciones"]["AuM"]
    ids = [c["id_cuenta"] for c in cli]
    print(f"  volumen operador : {_explain(nm, {'id_cuenta': {'$in': ids}, 'categoria': {'$in': list(_CATS_VOLUMEN)}, 'moneda': 'ARS'})}")
    if idc:
        print(f"  operaciones cli  : {_explain(nm, {'id_cuenta': idc, 'categoria': {'$in': list(_CATS_OPERACIONES)}}, sort=[('fecha', -1)])}")
        snap = aum.find_one({}, {"fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)])
        if snap:
            print(f"  portafolio cli   : {_explain(aum, {'fecha_snapshot': snap['fecha_snapshot'], 'id_cuenta': idc})}")

    # ¿Quedaron docs sin id_cuenta? (backfill pendiente)
    falta = nm.count_documents({"id_cuenta": {"$exists": False}})
    print(f"\n  NegocioMovimientos sin id_cuenta: {falta}  {'← correr backfill' if falta else '✓'}")


if __name__ == "__main__":
    main()
