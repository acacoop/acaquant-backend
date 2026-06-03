"""scripts/audit_operaciones.py — auditoría READ-ONLY de CashFlow.Operaciones.

Fase 0 del salto de calidad de datos. Mide la cobertura real de Operaciones y la
reconcilia contra NegocioMovimientos (verdad de referencia, feed de
consolidadosGenerales). **NO escribe nada. NO filtra nada**: lo que falta lo
CLASIFICA (por categoría / mes / moneda) para que vos decidas qué es hueco real y
qué es legítimo que `/informes` no traiga (cauciones, FCI bilateral, admin).

Qué mide:
  1. Operaciones: total, duplicados de boleto, rango, huecos vs días hábiles,
     nulos en campos críticos, breakdown por moneda / mercado / tipo_operacion.
  2. NegocioMovimientos: total, rango, categorías, monedas.
  3. Formato de claves: boletos vs comprobantes CRUDOS (¿alinean? ¿hay prefijo?).
  4. Reconciliación boleto↔comprobante (cruda Y normalizada a dígitos):
     - falta en Operaciones (está en NegMov, no en Ops) → por categoría + mes.
     - sobra en Operaciones (está en Ops, no en NegMov) → por mes.

Uso:
    python -m scripts.audit_operaciones
    python -m scripts.audit_operaciones --desde 2025-01-01   # acota la reconciliación
"""
from __future__ import annotations

import argparse
import re
from collections import Counter
from datetime import date, timedelta

from core.mongo import get_mongo_client_read

_SOLO_DIGITOS = re.compile(r"\D")


def _digits(s: str) -> str:
    return _SOLO_DIGITOS.sub("", s or "")


def _dias_habiles(desde: str, hasta: str) -> list[str]:
    """Lun-Vie entre dos ISO (incl.). NO descuenta feriados → un feriado va a
    aparecer como 'hueco'; lo aclaramos en el output."""
    try:
        d0 = date.fromisoformat(desde)
        d1 = date.fromisoformat(hasta)
    except ValueError:
        return []
    out, d = [], d0
    while d <= d1:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _pct(part: int, total: int) -> str:
    return f"{(100 * part / total):.1f}%" if total else "—"


def _print_counter(c: Counter, top: int | None = None, indent: str = "   ") -> None:
    items = c.most_common(top)
    if not items:
        print(f"{indent}(vacío)")
        return
    width = max((len(str(k)) for k, _ in items), default=0)
    for k, v in items:
        print(f"{indent}{k!s:<{width}}  {v:>8,}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", help="YYYY-MM-DD: acota la reconciliación a claves "
                                    "con fecha >= desde (default: todo)")
    args = ap.parse_args()
    desde = args.desde

    db = get_mongo_client_read()["CashFlow"]
    ops = db["Operaciones"]
    neg = db["NegocioMovimientos"]

    print("=" * 70)
    print("AUDITORÍA READ-ONLY — CashFlow.Operaciones (Fase 0)")
    if desde:
        print(f"Reconciliación acotada a fecha >= {desde}")
    print("=" * 70)

    # ── 1) Cargar claves + metadata mínima de cada colección ────────────────
    # Operaciones: boleto → (concertacion, moneda, tipo_operacion)
    ops_meta: dict[str, tuple] = {}
    ops_docs = ops_nulos = 0
    null_campos: Counter = Counter()
    mon_ops: Counter = Counter()
    merc_ops: Counter = Counter()
    tipo_ops: Counter = Counter()
    for d in ops.find({}, {"_id": 0, "boleto": 1, "concertacion": 1, "moneda": 1,
                           "tipo_operacion": 1, "mercado": 1, "cuenta": 1, "bruto": 1}):
        ops_docs += 1
        b = d.get("boleto")
        conc = d.get("concertacion")
        if b is None or str(b).strip() == "":
            ops_nulos += 1
            null_campos["boleto"] += 1
            continue
        for campo in ("concertacion", "cuenta", "bruto", "tipo_operacion"):
            v = d.get(campo)
            if v is None or (isinstance(v, str) and not v.strip()):
                null_campos[campo] += 1
        mon_ops[d.get("moneda") or "(null)"] += 1
        merc_ops[d.get("mercado") or "(null)"] += 1
        tipo_ops[d.get("tipo_operacion") or "(null)"] += 1
        ops_meta[str(b).strip()] = (conc, d.get("moneda"), d.get("tipo_operacion"))

    # NegocioMovimientos: comprobante → (categoria, fecha, moneda)
    neg_meta: dict[str, tuple] = {}
    neg_docs = 0
    cat_neg: Counter = Counter()
    mon_neg: Counter = Counter()
    for d in neg.find({}, {"_id": 0, "comprobante": 1, "categoria": 1, "fecha": 1, "moneda": 1}):
        neg_docs += 1
        c = d.get("comprobante")
        if c is None or str(c).strip() == "":
            continue
        cat_neg[d.get("categoria") or "(null)"] += 1
        mon_neg[d.get("moneda") or "(null)"] += 1
        neg_meta[str(c).strip()] = (d.get("categoria"), d.get("fecha"), d.get("moneda"))

    # ── 2) Self-audit de Operaciones ────────────────────────────────────────
    print("\n── 1) OPERACIONES ──────────────────────────────────────────────")
    print(f"   docs totales          {ops_docs:>10,}")
    print(f"   boletos únicos        {len(ops_meta):>10,}")
    dup = ops_docs - ops_nulos - len(ops_meta)
    print(f"   boletos nulos/vacíos  {ops_nulos:>10,}")
    print(f"   posibles duplicados   {dup:>10,}   (docs con boleto repetido)")

    fechas_ops = sorted({m[0] for m in ops_meta.values() if m[0]})
    if fechas_ops:
        cmin, cmax = fechas_ops[0], fechas_ops[-1]
        print(f"   rango concertación    {cmin} → {cmax}   ({len(fechas_ops)} días con ops)")
        habiles = _dias_habiles(cmin, cmax)
        presentes = set(fechas_ops)
        huecos = [d for d in habiles if d not in presentes]
        print(f"   días hábiles en rango {len(habiles):>10,}")
        print(f"   HUECOS (hábiles sin ops) {len(huecos):>7,}   (incluye feriados — revisar)")
        if huecos:
            muestra = huecos if len(huecos) <= 40 else huecos[:20] + ["…"] + huecos[-20:]
            print("      " + ", ".join(muestra))

    print("\n   Nulos en campos críticos (sobre boletos válidos):")
    for campo in ("concertacion", "cuenta", "bruto", "tipo_operacion"):
        n = null_campos[campo]
        print(f"      {campo:<16} {n:>8,}  ({_pct(n, len(ops_meta))})")

    print("\n   Por moneda:")
    _print_counter(mon_ops)
    print("\n   Por mercado (top 12):")
    _print_counter(merc_ops, top=12)
    print("\n   Por tipo_operacion (top 15):")
    _print_counter(tipo_ops, top=15)

    # ── 3) Self-audit de NegocioMovimientos ─────────────────────────────────
    print("\n── 2) NEGOCIOMOVIMIENTOS (verdad de referencia) ────────────────")
    print(f"   docs totales          {neg_docs:>10,}")
    print(f"   comprobantes únicos   {len(neg_meta):>10,}")
    fechas_neg = sorted({m[1] for m in neg_meta.values() if m[1]})
    if fechas_neg:
        print(f"   rango fecha           {fechas_neg[0]} → {fechas_neg[-1]}   ({len(fechas_neg)} días)")
    print("\n   Por categoría:")
    _print_counter(cat_neg)
    print("\n   Por moneda:")
    _print_counter(mon_neg)

    # ── 4) Formato de claves (¿alinean?) ────────────────────────────────────
    print("\n── 3) FORMATO DE CLAVES (boleto vs comprobante) ────────────────")
    print("   Operaciones.boleto (muestra cruda):")
    for b in list(ops_meta)[:8]:
        print(f"      {b!r}")
    print("   NegocioMovimientos.comprobante (muestra cruda):")
    for c in list(neg_meta)[:8]:
        print(f"      {c!r}")

    # ── 5) Reconciliación ───────────────────────────────────────────────────
    # Opcionalmente acotar por fecha (la del propio doc de cada colección).
    def _en_ventana_ops(k: str) -> bool:
        return not desde or (ops_meta[k][0] or "") >= desde

    def _en_ventana_neg(k: str) -> bool:
        return not desde or (neg_meta[k][1] or "") >= desde

    ops_keys = {k for k in ops_meta if _en_ventana_ops(k)}
    neg_keys = {k for k in neg_meta if _en_ventana_neg(k)}

    # Cruce CRUDO.
    inter_raw = ops_keys & neg_keys
    # Cruce NORMALIZADO (solo dígitos) — revela prefijos tipo "BOL ".
    ops_norm = {_digits(k): k for k in ops_keys}
    neg_norm = {_digits(k): k for k in neg_keys}
    inter_norm = set(ops_norm) & set(neg_norm)

    print("\n── 4) RECONCILIACIÓN boleto ↔ comprobante ──────────────────────")
    print(f"   claves Operaciones (en ventana)        {len(ops_keys):>10,}")
    print(f"   claves NegocioMovimientos (en ventana) {len(neg_keys):>10,}")
    print(f"   match CRUDO                            {len(inter_raw):>10,}")
    print(f"   match NORMALIZADO (solo dígitos)       {len(inter_norm):>10,}")
    if len(inter_norm) > len(inter_raw):
        print("   ⚠ El match normalizado es mayor → las claves tienen formatos")
        print("     distintos (prefijo/espacios). Hay que normalizar para cruzar bien.")

    # Usamos el cruce NORMALIZADO (el más permisivo) para clasificar lo que falta.
    falta_norm = set(neg_norm) - set(ops_norm)   # en NegMov, no en Ops
    sobra_norm = set(ops_norm) - set(neg_norm)   # en Ops, no en NegMov

    print(f"\n   FALTA en Operaciones (está en NegMov)  {len(falta_norm):>10,}")
    falta_cat: Counter = Counter()
    falta_mes: Counter = Counter()
    for nk in falta_norm:
        cat, fecha, _ = neg_meta[neg_norm[nk]]
        falta_cat[cat or "(null)"] += 1
        falta_mes[(fecha or "")[:7] or "(null)"] += 1
    print("      por categoría:")
    _print_counter(falta_cat, indent="         ")
    print("      por mes:")
    _print_counter(falta_mes, indent="         ")
    print("      muestra (comprobante · categoría · fecha):")
    for nk in list(falta_norm)[:15]:
        comp = neg_norm[nk]
        cat, fecha, _ = neg_meta[comp]
        print(f"         {comp!r:<20} {cat or '(null)':<22} {fecha or '(null)'}")

    print(f"\n   SOBRA en Operaciones (no está en NegMov) {len(sobra_norm):>8,}")
    print("   (esperable para concertación anterior a que existiera NegMov, o cargas manuales)")
    sobra_mes: Counter = Counter()
    for ok in sobra_norm:
        conc = ops_meta[ops_norm[ok]][0]
        sobra_mes[(conc or "")[:7] or "(null)"] += 1
    print("      por mes:")
    _print_counter(sobra_mes, indent="         ")
    print("      muestra (boleto · tipo · concertación):")
    for ok in list(sobra_norm)[:15]:
        bol = ops_norm[ok]
        conc, _, tipo = ops_meta[bol]
        print(f"         {bol!r:<20} {tipo or '(null)':<28} {conc or '(null)'}")

    print("\n" + "=" * 70)
    print("read-only: no se escribió nada.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
