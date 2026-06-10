"""diag_fci_estado.py — READ-ONLY. Por qué el FCI bilateral no llega completo a Operaciones.

Cruza, por comprobante, las 3 capas para el FCI bilateral de los últimos N días:
  1) CashFlow.NegocioMovimientos  (FUENTE — lo que manda el feed)
  2) CashFlow.Operaciones (Mongo)  (lo que escribe jobs.fci_bilateral)
  3) Postgres operaciones (SQL)     (lo que ve la app si lee SQL) — opcional

Responde:
  - ¿Corrió fci_bilateral? ¿Qué hizo? (último JobRun)
  - ¿La FUENTE tiene importe, o viene en 0? (garbage in)
  - ¿Operaciones tiene cada comprobante, con bruto correcto y `etapa`?
  - ¿SQL coincide con Mongo?
  - Lista de los comprobantes ROTOS (fuente con plata pero ops falta / bruto 0 / sin etapa).

NO escribe nada. Scopeado a los últimos N días por `fecha` (índice) + join por boleto
(índice uq_boleto) → barato, sin COLLSCAN (REGLA #4).

Uso:
    python -m scripts.diag_fci_estado            # últimos 10 días
    python -m scripts.diag_fci_estado --dias 20
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from core.mongo import get_mongo_client_read

_CATS_LIQ = ("suscripcion_fci", "rescate_fci")                       # CL (bilateral) / BOL (normal)
_CATS_SOL = ("solicitud_suscripcion_fci", "solicitud_rescate_fci")  # DOC
_TODAS = _CATS_LIQ + _CATS_SOL


def _prefijo(comprobante: str) -> str:
    s = (comprobante or "").strip().upper()
    for p in ("CL", "DOC", "BOL"):
        if s.startswith(p):
            return p
    return "OTRO"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=10)
    args = ap.parse_args()

    cli = get_mongo_client_read()
    cf = cli["CashFlow"]
    hoy_art = (datetime.now(UTC) - timedelta(hours=3)).date()
    desde = (hoy_art - timedelta(days=args.dias)).isoformat()
    print(f"=== Diag FCI bilateral — desde {desde} (últimos {args.dias}d ART) ===\n")

    # 0) Último run de fci_bilateral
    jr = cli["Manager"]["JobRuns"].find_one({"tipo": "fci_bilateral"}, sort=[("started_at", -1)])
    if jr:
        print(f"[JobRun fci_bilateral] {jr.get('started_at')}  status={jr.get('status')}")
        print(f"  stats: {jr.get('stats')}")
        if jr.get("errors"):
            print(f"  errors: {jr['errors'][:3]}")
    else:
        print("[JobRun fci_bilateral] ⚠️ NO HAY NINGÚN RUN registrado")
    print()

    # 1) FUENTE: NegocioMovimientos FCI (CL/DOC bilaterales + BOL normales)
    src = list(cf["NegocioMovimientos"].find(
        {"fecha": {"$gte": desde}, "categoria": {"$in": list(_TODAS)}},
        {"_id": 0, "comprobante": 1, "categoria": 1, "fecha": 1, "importe": 1},
    ))
    # Solo los que el job intenta llevar: CL/DOC (bilateral) + BOL (corrección bruto)
    fuente: dict[str, dict] = {}
    for d in src:
        comp = str(d.get("comprobante") or "").strip()
        if not comp:
            continue
        pref = _prefijo(comp)
        cat = d.get("categoria")
        # CL+liq, DOC+sol, BOL+liq son los relevantes (mismo criterio que el job).
        if not ((pref == "CL" and cat in _CATS_LIQ) or
                (pref == "DOC" and cat in _CATS_SOL) or
                (pref == "BOL" and cat in _CATS_LIQ)):
            continue
        imp = d.get("importe")
        fuente[comp] = {"pref": pref, "cat": cat, "fecha": d.get("fecha"),
                        "importe": abs(imp) if imp is not None else None}

    print(f"[FUENTE NegocioMov] {len(fuente)} comprobantes FCI relevantes")
    by_pref: dict[str, dict] = {}
    for v in fuente.values():
        b = by_pref.setdefault(v["pref"], {"n": 0, "imp0": 0})
        b["n"] += 1
        if not v["importe"]:
            b["imp0"] += 1
    for pref, b in sorted(by_pref.items()):
        print(f"  {pref}: {b['n']}  (importe 0/null en fuente: {b['imp0']})")
    print()

    # 2) DESTINO: Operaciones (Mongo) por esos boletos
    comps = list(fuente.keys())
    ops = {}
    if comps:
        for o in cf["Operaciones"].find(
            {"boleto": {"$in": comps}},
            {"_id": 0, "boleto": 1, "bruto": 1, "etapa": 1, "mercado": 1, "ingestado_en": 1},
        ):
            ops[str(o.get("boleto")).strip()] = o

    faltan, bruto0, sin_etapa = [], [], []
    for comp, v in fuente.items():
        o = ops.get(comp)
        if o is None:
            if v["pref"] != "BOL":   # BOL ya entra por el API; el job solo corrige bruto
                faltan.append(comp)
            continue
        if (o.get("bruto") in (0, None)) and v["importe"]:
            bruto0.append(comp)
        if not o.get("etapa") and v["pref"] in ("CL", "DOC"):
            sin_etapa.append(comp)

    print(f"[DESTINO Operaciones Mongo] {len(ops)}/{len(fuente)} comprobantes presentes")
    print(f"  ✗ FALTAN en Operaciones (CL/DOC con fuente): {len(faltan)}")
    print(f"  ✗ bruto=0/null pese a tener importe en fuente: {len(bruto0)}")
    print(f"  ✗ sin campo `etapa` (CL/DOC):                 {len(sin_etapa)}")
    print()

    def _muestras(titulo, lista):
        if not lista:
            return
        print(f"  · {titulo} (hasta 12):")
        for comp in lista[:12]:
            v = fuente[comp]
            o = ops.get(comp) or {}
            print(f"      {comp}  [{v['pref']}/{v['cat']}]  fecha={v['fecha']}  "
                  f"imp_fuente={v['importe']}  ops_bruto={o.get('bruto')}  "
                  f"etapa={o.get('etapa')}  ing={o.get('ingestado_en')}")

    _muestras("FALTAN en Operaciones", faltan)
    _muestras("bruto=0 en Operaciones", bruto0)
    _muestras("sin etapa", sin_etapa)
    print()

    # 3) SQL (opcional) — cuántos de esos boletos están en Postgres y con bruto
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT boleto, bruto FROM operaciones WHERE boleto = ANY(%s)", (comps,))
            sql_rows = {str(r[0]).strip(): r[1] for r in cur.fetchall()}
        sql_present = len(sql_rows)
        sql_b0 = sum(1 for comp, b in sql_rows.items()
                     if (b in (0, None)) and fuente.get(comp, {}).get("importe"))
        print(f"[SQL operaciones] {sql_present}/{len(fuente)} comprobantes presentes · bruto=0 con importe: {sql_b0}")
        # Mongo tiene pero SQL no (sync no propagó)
        solo_mongo = [c for c in ops if c not in sql_rows]
        print(f"  en Mongo pero NO en SQL (sync no propagó): {len(solo_mongo)}")
        if solo_mongo[:8]:
            print(f"     {solo_mongo[:8]}")
    except Exception as e:
        print(f"[SQL] no se pudo consultar Postgres ({type(e).__name__}: {e}) — salteado")

    print("\n=== Lectura ===")
    print("  - importe 0/null en FUENTE → el feed manda mal (garbage in): el job NO lo puede arreglar.")
    print("  - FALTAN en Operaciones    → el job no escribió (¿corrió? ¿quedó fuera del lookback de 10d?).")
    print("  - bruto=0 en Operaciones con importe en fuente → falló la corrección (paso 5/6 del job).")
    print("  - en Mongo pero no en SQL  → el sync no levantó (ingestado_en no bumpeado / sync caído).")


if __name__ == "__main__":
    main()
