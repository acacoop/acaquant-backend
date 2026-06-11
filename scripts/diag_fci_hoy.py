"""diag_fci_hoy.py — READ-ONLY. FCI suscripción/rescate de HOY en las 4 capas.

Mira el MISMO universo (FCI suscri + rescate, fecha = hoy ART) en:
  - Mongo  CashFlow.NegocioMovimientos   (NMm)
  - Mongo  CashFlow.Operaciones          (OPm)
  - SQL    negocio_movimientos           (NMs)
  - SQL    operaciones                   (OPs)

y arma UNA tabla por comprobante mostrando el valor en cada lado → se ve de un
vistazo dónde se corta (qué hay en NegocioMov pero no en Ops, qué llega con
bruto 0, qué está en Mongo pero no en SQL).

NO escribe nada. Scopeado a hoy.

Uso:
    python -m scripts.diag_fci_hoy
    python -m scripts.diag_fci_hoy --fecha 2026-06-10
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from core.mongo import get_mongo_client_read

_CATS = ("suscripcion_fci", "rescate_fci",
         "solicitud_suscripcion_fci", "solicitud_rescate_fci")


def _abs(v):
    try:
        return abs(float(v))
    except (TypeError, ValueError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha", help="YYYY-MM-DD (default: hoy ART)")
    args = ap.parse_args()
    fecha = args.fecha or (datetime.now(UTC) - timedelta(hours=3)).date().isoformat()

    cli = get_mongo_client_read()
    cf = cli["CashFlow"]
    print(f"=== FCI suscri/rescate — fecha {fecha} ===\n")

    # ── 1) Mongo NegocioMovimientos (NMm) ──
    nmm: dict[str, dict] = {}
    for d in cf["NegocioMovimientos"].find(
        {"fecha": fecha, "categoria": {"$in": list(_CATS)}},
        {"_id": 0, "comprobante": 1, "categoria": 1, "importe": 1},
    ):
        c = str(d.get("comprobante") or "").strip()
        if c:
            nmm[c] = {"cat": d.get("categoria"), "importe": _abs(d.get("importe"))}

    # ── 2) Mongo Operaciones (OPm) — por boleto de NMm + por concertacion+mercado ──
    opm: dict[str, dict] = {}
    filtro_ops = {"$or": [
        {"boleto": {"$in": list(nmm)}} if nmm else {"boleto": "__none__"},
        {"concertacion": fecha, "mercado": {"$regex": "FCI", "$options": "i"}},
    ]}
    for o in cf["Operaciones"].find(
        filtro_ops,
        {"_id": 0, "boleto": 1, "bruto": 1, "etapa": 1, "mercado": 1, "ingestado_en": 1},
    ):
        b = str(o.get("boleto") or "").strip()
        if b:
            opm[b] = {"bruto": o.get("bruto"), "etapa": o.get("etapa"),
                      "mercado": o.get("mercado"), "ing": o.get("ingestado_en")}

    # ── 3 y 4) SQL ──
    nms: dict[str, dict] = {}
    ops_sql: dict[str, object] = {}
    sql_ok = True
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT comprobante, categoria, importe FROM negocio_movimientos "
                "WHERE fecha = %s AND categoria = ANY(%s)",
                (fecha, list(_CATS)))
            for comp, cat, imp in cur.fetchall():
                nms[str(comp).strip()] = {"cat": cat, "importe": _abs(imp)}
            comps = list(nmm) or ["__none__"]
            cur.execute(
                "SELECT boleto, bruto, mercado FROM operaciones "
                "WHERE boleto = ANY(%s) OR (concertacion = %s AND mercado ILIKE %s)",
                (comps, fecha, "%FCI%"))
            for bol, bruto, merc in cur.fetchall():
                ops_sql[str(bol).strip()] = {"bruto": bruto, "mercado": merc}
    except Exception as e:
        sql_ok = False
        print(f"⚠️ SQL no consultable ({type(e).__name__}: {e}) — solo Mongo\n")

    # ── Resumen de conteos ──
    print(f"NMm (NegocioMov Mongo): {len(nmm)}")
    print(f"OPm (Operaciones Mongo): {len(opm)}")
    if sql_ok:
        print(f"NMs (NegocioMov SQL):   {len(nms)}")
        print(f"OPs (Operaciones SQL):  {len(ops_sql)}")
    print()

    # ── Tabla consolidada por comprobante ──
    todos = sorted(set(nmm) | set(opm) | set(nms) | set(ops_sql))
    if not todos:
        print("Sin FCI suscri/rescate para esa fecha en ninguna capa.")
        return

    print(f"{'COMPROBANTE':<22} {'CAT':<26} {'NMm imp':>12} {'OPm bruto':>12} {'etapa':<12} {'NMs imp':>12} {'OPs bruto':>12}")
    print("-" * 122)
    for c in todos:
        a = nmm.get(c, {})
        o = opm.get(c, {})
        s = nms.get(c, {})
        q = ops_sql.get(c, {})
        cat = (a.get("cat") or s.get("cat") or "")[:25]
        def f(x):
            return "—" if x is None else (f"{x:,.0f}".replace(",", ".") if isinstance(x, (int, float)) else str(x))
        print(f"{c:<22} {cat:<26} {f(a.get('importe')):>12} {f(o.get('bruto')):>12} "
              f"{o.get('etapa') or '—'!s:<12} {f(s.get('importe')):>12} {f(q.get('bruto')):>12}")

    print("\nLeyenda: NMm/OPm = Mongo NegocioMov/Operaciones · NMs/OPs = SQL. "
          "'—' = no está en esa capa. Buscá: importe en NMm pero OPm '—' (no llegó a Ops) "
          "o OPm bruto 0 (no se corrigió) o OPm OK pero OPs '—' (sync no propagó).")


if __name__ == "__main__":
    main()
