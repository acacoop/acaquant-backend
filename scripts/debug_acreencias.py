"""debug_acreencias.py — por qué un título/cliente NO aparece (o aparece bajo) en COBROS FUTUROS.

Recorre la MISMA cadena que api.services.acreencias.computar_acreencias, pero con
diagnóstico por tenencia:
  tenencia (SQL portafolio.tenencia, aum='si')  →  resolver(unidad → ticker calendario)
  →  ¿el ticker tiene flujos FUTUROS en Trading.Curvas?

Clasifica cada tenencia que NO proyecta:
  - SIN_MAPEO      → la `unidad` no resuelve a ningún ticker (ni vía assets.TICKER ni base)
  - NO_MODELADO    → resuelve a un ticker que NO está en Trading.Curvas
  - SIN_FLUJO_FUT  → está en Curvas pero sin flujos futuros (vencido, o CER sin cer_emision/cer_liq)

Read-only.
    python -m scripts.debug_acreencias                 # reporte global: qué NO proyecta y por qué
    python -m scripts.debug_acreencias --cuenta 12345  # detalle de una cuenta (cada holding)
    python -m scripts.debug_acreencias --top 30        # cuántas filas en el global
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from api.db import get_db_trading
from api.services.acreencias import _base_ticker, calendario_instrumentos
from api.services.assets_sql import assets_rows
from core.postgres import get_pool


def _curvas_index() -> tuple[set[str], dict[str, str], dict[str, dict]]:
    """(tickers_modelados, base→ticker, ticker→{curva, n_flujos, cer_emision})
    de TODO Trading.Curvas — para distinguir 'no modelado' de 'sin flujo futuro'."""
    tickers: set[str] = set()
    base2tk: dict[str, str] = {}
    meta: dict[str, dict] = {}
    for d in get_db_trading()["Curvas"].find(
        {}, {"_id": 0, "ticker_corto": 1, "ticker": 1, "curva": 1,
             "flujos": 1, "cer_emision": 1}):
        tk = d.get("ticker_corto") or d.get("ticker")
        if not tk:
            continue
        tickers.add(tk)
        base2tk.setdefault(_base_ticker(tk), tk)
        meta[tk] = {"curva": d.get("curva"), "n_flujos": len(d.get("flujos") or []),
                    "cer_emision": d.get("cer_emision")}
    return tickers, base2tk, meta


def _holdings() -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        f = cur.fetchone()[0]
        if not f:
            return []
        cur.execute("SELECT id_cuenta, unidad, cantidad FROM portafolio.tenencia "
                    "WHERE fecha = %s AND aum = 'si'", (f,))
        return [{"id_cuenta": r[0], "unidad": r[1], "cantidad": float(r[2] or 0)}
                for r in cur.fetchall()]


def _debug_ticker(inp: str) -> int:
    """Desglosa los flujos de un bono: cuáles son futuros y el MONTO por 100 VN
    (con el factor CER explícito) → explica 'monto bajo' y 'no aparece'."""
    from datetime import date as _date

    from engines.curvas import (
        cargar_cer,
        cargar_dias_habiles,
        fecha_flujo,
        get_cer_liquidacion,
        monto_flujo,
        monto_flujo_cer,
        monto_flujo_soberano,
    )
    inp = inp.strip().upper()
    db = get_db_trading()
    docs = list(db["Curvas"].find(
        {"$or": [{"ticker_corto": inp}, {"ticker": {"$regex": inp, "$options": "i"}}]},
        {"_id": 0, "ticker_corto": 1, "ticker": 1, "curva": 1,
         "cer_emision": 1, "flujos": 1, "moneda_flujo": 1}))
    if not docs:
        print(f"❌ {inp} NO está en Trading.Curvas → NO_MODELADO. Ese es el motivo de que "
              f"NO aparezca en cobros futuros (sin doc en Curvas, no hay flujos que proyectar).")
        return 0

    hoy = _date.today()
    cer_dict = cargar_cer(db.client, dias=1200)
    dias_h = cargar_dias_habiles(db.client)

    for d in docs:
        tk = d.get("ticker_corto") or d.get("ticker")
        curva = d.get("curva") or ""
        cer_em = d.get("cer_emision")
        flujos = d.get("flujos") or []
        print(f"\n═══ {tk}  curva={curva}  moneda_flujo={d.get('moneda_flujo')}  "
              f"cer_emision={cer_em}  #flujos={len(flujos)} ═══")
        print(f"  {'fecha':<12}{'monto/100VN':>16}  detalle")
        fut = 0
        for f in sorted(flujos, key=lambda x: str(fecha_flujo(x) or "")):
            fd = fecha_flujo(f)
            if not fd:
                continue
            futuro = fd > hoy
            if curva in ("soberanos", "dolar_linked"):
                monto, det = monto_flujo_soberano(f, 100), f"{curva} (USD)"
            elif curva == "cer":
                if not cer_em:
                    print(f"  {fd!s:<12}{'—':>16}  SKIP: cer_emision faltante → NO proyecta")
                    continue
                cer_liq = get_cer_liquidacion(cer_dict, dias_h, fd.isoformat())
                if not cer_liq:
                    print(f"  {fd!s:<12}{'—':>16}  SKIP: cer_liq no disponible → NO proyecta")
                    continue
                base = monto_flujo_cer(f, 100)
                factor = cer_liq / float(cer_em)
                monto = base * factor
                det = f"CER base={base:.4f} × (cer_liq {cer_liq:.2f} / cer_em {float(cer_em):.2f} = {factor:.4f})"
            else:
                monto, det = monto_flujo(f), curva
            if futuro:
                fut += 1
            mark = "" if futuro else "  ← pasado (no cuenta)"
            print(f"  {fd!s:<12}{monto:>16,.2f}  {det}{mark}")
        if fut == 0:
            print("  ⚠ SIN flujos FUTUROS → por eso NO aparece en cobros futuros.")
        else:
            print(f"  → {fut} flujos futuros. monto_cliente = cantidad/100 × monto/100VN.")
            print("    Si el monto/100VN se ve bajo: revisar el factor CER (cer_liq/cer_emision)")
            print("    o el shape del flujo (CER % vs absoluto). Pegame esto y lo cierro.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuenta", help="id_cuenta para detalle por holding")
    ap.add_argument("--ticker", help="inspeccionar los flujos de un bono (PMJ26, T30J6)")
    ap.add_argument("--top", type=int, default=40, help="filas en el reporte global")
    args = ap.parse_args()

    if args.ticker:
        return _debug_ticker(args.ticker)

    cal = calendario_instrumentos()                 # ticker → {moneda, flujos futuros}
    cal_base = {}
    for k in cal:
        cal_base.setdefault(_base_ticker(k), k)
    assets = {a.get("unidad"): a for a in assets_rows(["TICKER", "EMISOR"])}
    tickers_mod, base_mod, meta = _curvas_index()

    def resolver(unidad: str):
        a = assets.get(unidad)
        cands = ([a.get("TICKER")] if a else []) + [unidad]
        for c in cands:
            if not c:
                continue
            if c in cal:
                return c, None
            b = _base_ticker(c)
            if b in cal_base:
                return cal_base[b], None
        # No proyecta: clasificar por qué.
        for c in cands:
            if not c:
                continue
            if c in tickers_mod or _base_ticker(c) in base_mod:
                tk = c if c in tickers_mod else base_mod[_base_ticker(c)]
                m = meta.get(tk, {})
                return None, (f"SIN_FLUJO_FUT (curva={m.get('curva')}, "
                              f"flujos_total={m.get('n_flujos')}, "
                              f"cer_emision={'sí' if m.get('cer_emision') else 'NO'})")
        return None, "SIN_MAPEO" if not assets.get(unidad) else "NO_MODELADO"

    hs = _holdings()
    if not hs:
        print("⚠ Sin tenencia (portafolio.tenencia aum='si' vacío). Revisar el writer diario.")
        return 0

    if args.cuenta:
        print(f"═══ Cuenta {args.cuenta} — cadena por holding ═══\n")
        print(f"{'unidad':<16}{'cantidad':>14}  {'→ ticker / motivo'}")
        rows = [h for h in hs if str(h["id_cuenta"]) == str(args.cuenta)]
        if not rows:
            print("(esta cuenta no tiene tenencia en el último snapshot aum='si')")
            return 0
        for h in sorted(rows, key=lambda x: -x["cantidad"]):
            tk, motivo = resolver(h["unidad"])
            if tk:
                fl = cal[tk]
                total = sum(round(h["cantidad"] / 100.0 * x["monto"], 2) for x in fl["flujos"])
                det = f"→ {tk} ({fl['moneda']}, {len(fl['flujos'])} flujos fut) = {total:,.2f}"
            else:
                det = f"❌ {motivo}"
            print(f"{(h['unidad'] or '?'):<16}{h['cantidad']:>14,.2f}  {det}")
        return 0

    # Reporte GLOBAL: lo que NO proyecta, agregado por unidad + motivo.
    no_proy: dict[str, dict] = defaultdict(lambda: {"cant": 0.0, "cuentas": set(), "motivo": ""})
    ok_unidades: set[str] = set()
    for h in hs:
        tk, motivo = resolver(h["unidad"])
        if tk:
            ok_unidades.add(h["unidad"])
        else:
            e = no_proy[h["unidad"]]
            e["cant"] += h["cantidad"]
            e["cuentas"].add(h["id_cuenta"])
            e["motivo"] = motivo

    print("═══ COBERTURA acreencias — tenencias que NO proyectan cobro ═══\n")
    print(f"unidades que SÍ proyectan: {len(ok_unidades)}  |  que NO: {len(no_proy)}\n")
    print(f"{'unidad':<18}{'cantidad Σ':>16}{'#ctas':>7}  motivo")
    for u, e in sorted(no_proy.items(), key=lambda kv: -kv[1]["cant"])[:args.top]:
        print(f"{(u or '?'):<18}{e['cant']:>16,.0f}{len(e['cuentas']):>7}  {e['motivo']}")

    by_motivo: dict[str, int] = defaultdict(int)
    for e in no_proy.values():
        by_motivo[e["motivo"].split(" ")[0]] += 1
    print("\nResumen por motivo:", dict(by_motivo))
    print("\nLeyenda: SIN_MAPEO=unidad sin TICKER en assets · NO_MODELADO=ticker no está en "
          "Curvas · SIN_FLUJO_FUT=en Curvas pero sin flujos futuros (vencido / CER sin emisión).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
