"""diag_fci_bilateral.py — READ-ONLY. Entender el FCI bilateral vs no-bilateral.

Pregunta de negocio (vista REFERIDOS, etapa 2): para llegar al AUM en FCI de un
referido, ¿qué parte de las SUSCRIPCIONES se hizo por movimiento BILATERAL (directo
con la sociedad gerente) y qué parte por mercado (boleto vía API de informes)?

En CashFlow.Operaciones el bilateral queda marcado con `mercado="FCI Bilateral"`
(lo escribe jobs/fci_bilateral.py). El FCI "normal" entra con OTRO `mercado` — cuál
es, lo descubre este diag (no se asume). Reconcilia 3 cosas:

  1) TAXONOMÍA: qué valores de `mercado` / `operacion` tienen las ops FCI → para
     ver con qué etiqueta entra el no-bilateral.
  2) SPLIT bilateral vs no-bilateral: $ y % de suscripciones y rescates.
  3) Opcional --referido: lo mismo pero scopeado a las cuentas de ese referido,
     abierto por sociedad gerente (emisor).

Excluye `etapa="solicitud"` (el pedido; la liquidación ya cuenta → no doblar).
Scopeado por `concertacion` (índice) → barato, sin COLLSCAN (REGLA #4).

Uso:
    python -m scripts.diag_fci_bilateral                      # últimos 180 días, global
    python -m scripts.diag_fci_bilateral --dias 365
    python -m scripts.diag_fci_bilateral --referido "NOMBRE COOP"
    python -m scripts.diag_fci_bilateral --desde 2026-01-01 --hasta 2026-06-11
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from core.mongo import get_mongo_client_read

_BILATERAL = "FCI Bilateral"
# Detección amplia de "op FCI" en Operaciones: por mercado o por tipo_operacion.
# Captura tanto el bilateral (mercado="FCI Bilateral") como el normal (sea cual
# sea su mercado) → el diag muestra después cómo se reparten.
_FCI_OR = [
    {"mercado": {"$regex": "fci", "$options": "i"}},
    {"tipo_operacion": {"$regex": "fci|fondo", "$options": "i"}},
]


def _money(x: float) -> str:
    return f"{x:,.0f}".replace(",", ".")


def _abs(v) -> float:
    return abs(float(v)) if v is not None else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=180)
    ap.add_argument("--desde", type=str, default=None, help="YYYY-MM-DD (pisa --dias)")
    ap.add_argument("--hasta", type=str, default=None, help="YYYY-MM-DD")
    ap.add_argument("--referido", type=str, default=None)
    args = ap.parse_args()

    cli = get_mongo_client_read()
    cf = cli["CashFlow"]
    ops = cf["Operaciones"]

    hoy_art = (datetime.now(UTC) - timedelta(hours=3)).date()
    hasta = args.hasta or hoy_art.isoformat()
    desde = args.desde or (hoy_art - timedelta(days=args.dias)).isoformat()

    base_match: dict = {
        "concertacion": {"$gte": desde, "$lte": hasta},
        "etapa": {"$ne": "solicitud"},
        "$or": _FCI_OR,
    }

    print(f"=== Diag FCI bilateral — concertacion {desde} → {hasta} ===")
    if args.referido:
        print(f"    scope: referido = {args.referido!r}")
    print()

    # ── Scope opcional por referido (cuentas activas de ese referido) ──────────
    if args.referido:
        ids = sorted({
            str(d["id_cuenta"]) for d in cli["Clientes"]["Comitentes"].find(
                {"referido": args.referido, "estado": "Activa"},
                {"_id": 0, "id_cuenta": 1}) if d.get("id_cuenta")
        })
        if not ids:
            print(f"⚠️ El referido {args.referido!r} no tiene cuentas activas. Nada que mostrar.")
            return
        print(f"[scope] {len(ids)} cuentas del referido")
        base_match["cuenta"] = {"$in": ids}
        print()

    # ── 1) TAXONOMÍA: (mercado, operacion) → n, bruto ──────────────────────────
    print("── 1) TAXONOMÍA de ops FCI (mercado × operacion) ──")
    print("    (así vemos con qué `mercado` entra el NO-bilateral)\n")
    tax = list(ops.aggregate([
        {"$match": base_match},
        {"$group": {"_id": {"m": "$mercado", "op": "$operacion"},
                    "n": {"$sum": 1},
                    "bruto": {"$sum": {"$abs": {"$ifNull": ["$bruto", 0]}}}}},
        {"$sort": {"bruto": -1}},
    ]))
    if not tax:
        print("    (sin ops FCI en el rango)\n")
        return
    print(f"    {'mercado':<28}{'operacion':<16}{'n':>7}{'bruto':>20}")
    for t in tax:
        m = str(t["_id"].get("m") or "—")
        op = str(t["_id"].get("op") or "—")
        print(f"    {m:<28}{op:<16}{t['n']:>7}{_money(t['bruto']):>20}")
    print()

    # ── 2) SPLIT bilateral vs no-bilateral, por operacion ──────────────────────
    print("── 2) SPLIT bilateral vs no-bilateral ──\n")
    split: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for t in tax:
        m = str(t["_id"].get("m") or "—")
        op = str(t["_id"].get("op") or "—")
        clase = "BILATERAL" if m == _BILATERAL else "no-bilateral"
        split[op][clase] += t["bruto"]
    for op, dd in split.items():
        bil = dd.get("BILATERAL", 0.0)
        nob = dd.get("no-bilateral", 0.0)
        tot = bil + nob or 1.0
        print(f"    {op}:  bilateral {_money(bil)} ({bil / tot * 100:.1f}%)   "
              f"no-bilateral {_money(nob)} ({nob / tot * 100:.1f}%)")
    print()

    # ── 3) Por sociedad gerente (emisor) — solo con --referido ─────────────────
    if args.referido:
        print("── 3) Por sociedad gerente (emisor) — bilateral vs no ──\n")
        # instrumento (Operaciones) == unidad (Assets) → emisor.
        emisor_de_unidad = {
            d["unidad"]: (d.get("EMISOR") or "—")
            for d in cli["Valuaciones"]["Assets"].find(
                {"CARTERA": {"$in": ["FCI", "CARTERA FCI"]}},
                {"_id": 0, "unidad": 1, "EMISOR": 1}) if d.get("unidad")
        }
        por_emisor: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for d in ops.find(base_match,
                          {"_id": 0, "instrumento": 1, "mercado": 1, "operacion": 1, "bruto": 1}):
            emisor = emisor_de_unidad.get(d.get("instrumento"), "(sin emisor en Assets)")
            clase = "BILATERAL" if d.get("mercado") == _BILATERAL else "no-bilateral"
            por_emisor[emisor][clase] += _abs(d.get("bruto"))
        print(f"    {'emisor':<30}{'bilateral':>18}{'no-bilateral':>18}{'% bilat':>10}")
        for emisor, dd in sorted(por_emisor.items(),
                                 key=lambda kv: -(kv[1].get('BILATERAL', 0) + kv[1].get('no-bilateral', 0))):
            bil = dd.get("BILATERAL", 0.0)
            nob = dd.get("no-bilateral", 0.0)
            tot = bil + nob or 1.0
            print(f"    {emisor:<30}{_money(bil):>18}{_money(nob):>18}{bil / tot * 100:>9.1f}%")
        print()

    print("=== Lectura ===")
    print("  - El paso 1 te dice qué `mercado` usa el NO-bilateral (lo que no es 'FCI Bilateral').")
    print("  - El paso 2 es el % que buscás: de las suscripciones, cuánto entró bilateral.")
    print("  - Con --referido ves el split por gerente (que es como vamos a cobrar la comisión).")


if __name__ == "__main__":
    main()
