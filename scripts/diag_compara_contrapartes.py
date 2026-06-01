"""diag_compara_contrapartes.py — compara la vista Contrapartes VIEJA vs NUEVA
POR IMPORTE (no por cantidad de movimientos).

Por qué por importe y no por conteo: cada vista le pega a una API distinta y
agrupa/batchea distinto, así que el NÚMERO de movimientos casi nunca coincide
(una puede tener 10 líneas y la otra 3 boletos por la misma operación). Lo que
tiene que reconciliar es la PLATA. Por eso acá comparamos importes, no counts.

  VIEJA : OperacionesAPI.MesaAPI agrupado por `contraparte`, sumando `bruto`
          (feed flujo_contrapartes: trades; excluye cauciones colocadoras y
          futuros financieros).
  NUEVA : CashFlow.NegocioMovimientos cuyo `id_cuenta` ∈ las cuentas de la
          contraparte (CashFlow.Contrapartes.cuenta), sumando `importe`,
          excluyendo futuros (unidad USDL). Trae TODO con historia.

La NUEVA trae de más los MOVIMIENTOS DE FONDOS (deposito / transferencia /
extraccion) que la VIEJA no tiene. Para una comparación justa, la NUEVA se
desglosa en buckets y la columna que reconcilia contra la VIEJA es **OPERA**
(operaciones de mercado: compra, venta, susc/rescate FCI, acreencia, cauciones
tomadoras). FONDOS y OTRO son "lo que la vista nueva agrega".

Las fechas se normalizan a ISO de los dos lados (MesaAPI guarda DD/MM/YYYY crudo
de Aunesa; NegocioMovimientos guarda YYYY-MM-DD) para poder filtrar por ventana.

Read-only. NO borra ni modifica nada.

Uso (desde la raíz del repo en el Droplet):
    venv/bin/python -m scripts.diag_compara_contrapartes
    venv/bin/python -m scripts.diag_compara_contrapartes --desde 2026-01-01
    venv/bin/python -m scripts.diag_compara_contrapartes --cp DRACMA,IEB --cat
    venv/bin/python -m scripts.diag_compara_contrapartes --tol 0.5   # umbral Δ% del flag
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from core.mongo import get_mongo_client_read

# ── Buckets de categoría (lado NUEVO) ───────────────────────────────────────
# OPERA  = operaciones de mercado → es lo comparable contra la VIEJA.
# FONDOS = movimientos de dinero que la VIEJA no tiene.
# EXCL   = lo que la VIEJA explícitamente excluye (cauciones colocadoras).
# OTRO   = comisiones, impuestos, solicitudes FCI, etc.
_CATS_OPERA = {"compra", "venta", "suscripcion_fci", "rescate_fci", "acreencia"}
_CATS_FONDOS = {"deposito", "transferencia", "extraccion"}


def _bucket(cat: str | None) -> str:
    c = (cat or "").lower()
    if c in _CATS_FONDOS:
        return "FONDOS"
    if "caucion" in c and "colocadora" in c:
        return "EXCL"
    if c in _CATS_OPERA or "caucion" in c:
        return "OPERA"
    return "OTRO"


def _to_iso(raw) -> str | None:
    """Normaliza a 'YYYY-MM-DD'. Acepta ISO ya formado o DD/MM/YYYY."""
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    if "/" in s:
        try:
            d, m, y = s[:10].split("/")
            return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        except (ValueError, IndexError):
            return None
    return None


def _num(x) -> float:
    if isinstance(x, (int, float)):
        return float(x)
    return 0.0


def _ccy(moneda: str | None) -> str:
    m = (moneda or "").strip().upper()
    if m.startswith("USD"):
        return "USD"
    if m == "ARS":
        return "ARS"
    return m or "(sin)"


def _fmt(x: float) -> str:
    return f"{x:,.0f}".replace(",", ".")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default=None, help="ventana ISO YYYY-MM-DD (inclusive)")
    ap.add_argument("--hasta", default=None, help="ventana ISO YYYY-MM-DD (inclusive)")
    ap.add_argument("--cp", default=None,
                    help="filtrar a estas contrapartes (coma-separado, ej. DRACMA,IEB)")
    ap.add_argument("--cat", action="store_true",
                    help="desglose por categoría (importe) del lado NUEVO")
    ap.add_argument("--tol", type=float, default=1.0,
                    help="umbral Δ%% sobre OPERA para marcar ⚠ no-reconcilia (default 1.0)")
    args = ap.parse_args()
    cp_filtro = [t.strip().lower() for t in (args.cp or "").split(",") if t.strip()]
    desde, hasta = args.desde, args.hasta

    def _in_window(iso: str | None) -> bool:
        if not (desde or hasta):
            return True
        if iso is None:
            return False
        if desde and iso < desde:
            return False
        return not (hasta and iso > hasta)

    c = get_mongo_client_read()
    contrapartes = c["CashFlow"]["Contrapartes"]
    mesa = c["OperacionesAPI"]["MesaAPI"]
    nego = c["CashFlow"]["NegocioMovimientos"]

    # contraparte (nombre) → set de id_cuenta (sus fondos); y el inverso.
    cp_to_ids: dict[str, set[str]] = defaultdict(set)
    id_to_cp: dict[str, str] = {}
    for d in contrapartes.find({}, {"_id": 0, "contraparte": 1, "cuenta": 1}):
        cp = (d.get("contraparte") or "").strip()
        idc = str(d.get("cuenta") or "").strip()
        if cp and idc:
            cp_to_ids[cp].add(idc)
            id_to_cp[idc] = cp

    # ── VIEJA: MesaAPI por (contraparte, moneda) → [n, neto, bruto_abs] ──────
    old: dict[tuple[str, str], list] = defaultdict(lambda: [0, 0.0, 0.0])
    for d in mesa.find({}, {"_id": 0, "contraparte": 1, "bruto": 1,
                            "moneda": 1, "concertacion": 1}):
        if not _in_window(_to_iso(d.get("concertacion"))):
            continue
        cp = (d.get("contraparte") or "").strip()
        if not cp:
            continue
        ccy = _ccy(d.get("moneda"))
        v = _num(d.get("bruto"))
        o = old[(cp, ccy)]
        o[0] += 1
        o[1] += v
        o[2] += abs(v)

    # ── NUEVA: NegocioMovimientos por (id_cuenta, moneda) → buckets ─────────
    # new_id[(idc, ccy)][bucket] = [n, neto]
    new_id: dict[tuple[str, str], dict[str, list]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0.0])
    )
    # cat_by_cp[(cp, ccy)][categoria] = [n, importe_abs]  (para --cat)
    cat_by_cp: dict[tuple[str, str], dict[str, list]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0.0])
    )
    all_ids = list(id_to_cp)
    for d in nego.find(
        {"id_cuenta": {"$in": all_ids}, "unidad": {"$nin": ["USDL"]}},
        {"_id": 0, "id_cuenta": 1, "importe": 1, "moneda": 1,
         "fecha": 1, "categoria": 1},
    ):
        if not _in_window(_to_iso(d.get("fecha"))):
            continue
        idc = str(d.get("id_cuenta") or "")
        ccy = _ccy(d.get("moneda"))
        v = _num(d.get("importe"))
        bkt = _bucket(d.get("categoria"))
        slot = new_id[(idc, ccy)][bkt]
        slot[0] += 1
        slot[1] += v
        cp = id_to_cp.get(idc)
        if cp:
            cc = cat_by_cp[(cp, ccy)][d.get("categoria") or "(sin)"]
            cc[0] += 1
            cc[1] += abs(v)

    # ── Armar filas por (contraparte, moneda) ───────────────────────────────
    rows = []
    for cp, ids in cp_to_ids.items():
        if cp_filtro and not any(t in cp.lower() for t in cp_filtro):
            continue
        ccys = {ccy for (c2, ccy) in old if c2 == cp}
        for idc in ids:
            ccys |= {ccy for (i2, ccy) in new_id if i2 == idc}
        for ccy in sorted(ccys):
            o = old.get((cp, ccy), [0, 0.0, 0.0])
            opera_n = 0
            opera_net = fondos_net = otro_net = 0.0
            for idc in ids:
                b = new_id.get((idc, ccy))
                if not b:
                    continue
                opera_n += b["OPERA"][0]
                opera_net += b["OPERA"][1]
                fondos_net += b["FONDOS"][1]
                otro_net += b["OTRO"][1] + b["EXCL"][1]
            old_net = o[1]
            delta = opera_net - old_net
            base = abs(old_net) if abs(old_net) > 1 else max(abs(opera_net), 1.0)
            pct = (delta / base * 100) if base else 0.0
            rows.append({
                "cp": cp, "ccy": ccy,
                "old_n": o[0], "old_net": old_net,
                "opera_n": opera_n, "opera_net": opera_net,
                "fondos_net": fondos_net, "otro_net": otro_net,
                "delta": delta, "pct": pct,
            })

    # Orden: por importe OPERA nuevo (lo más grande primero).
    rows.sort(key=lambda r: -abs(r["opera_net"]))

    print("=" * 118)
    win = f"  ventana: {desde or '∅'} → {hasta or '∅'}" if (desde or hasta) else "  (todo el histórico)"
    print(f"COMPARA Contrapartes POR IMPORTE — VIEJA (MesaAPI.bruto) vs NUEVA (NegocioMovimientos.importe){win}")
    print(f"{len(cp_to_ids)} contrapartes con cuenta · OPERA = subset comparable · ⚠ si |Δ%| > {args.tol}")
    print("=" * 118)
    print(f"{'CONTRAPARTE':<22} {'CCY':<5} {'VIEJA $':>16} {'NUEVA OPERA $':>16} "
          f"{'Δ$':>15} {'Δ%':>7}  {'+FONDOS $':>14} {'+OTRO $':>12}")
    print("-" * 118)
    tot = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])  # ccy -> [old, opera, fondos, otro]
    n_warn = 0
    for r in rows:
        flag = ""
        if abs(r["pct"]) > args.tol and (abs(r["old_net"]) > 1 or abs(r["opera_net"]) > 1):
            flag = "  ⚠"
            n_warn += 1
        t = tot[r["ccy"]]
        t[0] += r["old_net"]
        t[1] += r["opera_net"]
        t[2] += r["fondos_net"]
        t[3] += r["otro_net"]
        print(f"{r['cp'][:21]:<22} {r['ccy']:<5} "
              f"{_fmt(r['old_net']):>16} {_fmt(r['opera_net']):>16} "
              f"{_fmt(r['delta']):>15} {r['pct']:>+6.1f}% "
              f"{_fmt(r['fondos_net']):>14} {_fmt(r['otro_net']):>12}{flag}")
        if args.cat:
            cats = cat_by_cp.get((r["cp"], r["ccy"]), {})
            orden = sorted(cats.items(), key=lambda kv: -kv[1][1])
            desg = "  ".join(f"{k}={_fmt(v[1])}({v[0]})" for k, v in orden)
            if desg:
                print(f"      └─ {desg}")
    print("-" * 118)
    for ccy, t in sorted(tot.items()):
        d = t[1] - t[0]
        print(f"{'TOTAL ' + ccy:<22} {ccy:<5} {_fmt(t[0]):>16} {_fmt(t[1]):>16} "
              f"{_fmt(d):>15} {'':>7}  {_fmt(t[2]):>14} {_fmt(t[3]):>12}")
    print(f"\n{n_warn} filas con |Δ%| > {args.tol} sobre OPERA (revisar esas contrapartes).")

    # ── Contrapartes con movimientos en MesaAPI pero SIN cuenta linkeable ───
    old_cps = {cp for (cp, _ccy) in old}
    sin_cuenta = sorted(old_cps - set(cp_to_ids))
    if sin_cuenta:
        print(f"\n⚠ {len(sin_cuenta)} contrapartes con movimientos en MesaAPI pero SIN `cuenta` "
              f"en CashFlow.Contrapartes (no linkean por id_cuenta):")
        for cp in sin_cuenta:
            neto = sum(old[(cp, ccy)][1] for (c2, ccy) in old if c2 == cp)
            print(f"    {cp}  (neto MesaAPI: {_fmt(neto)})")

    print("\nCÓMO LEERLO:")
    print("• Δ$/Δ% comparan VIEJA contra NUEVA-OPERA (mismo universo: operaciones de mercado).")
    print("  Si reconcilian (~0%), la migración a NegocioMovimientos no pierde plata.")
    print("• +FONDOS y +OTRO es lo que la vista nueva SUMA (depósitos/extracciones, comisiones, etc.).")
    print("• Filas ⚠ → mirar con --cp NOMBRE --cat para ver qué categoría descuadra.")


if __name__ == "__main__":
    main()
