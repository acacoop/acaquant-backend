"""scripts/diag_ons_master.py — RADIOGRAFÍA read-only de Trading.BondsMaster.

Objetivo: decidir la arquitectura de (1) la vista de ONs y (2) el motor de
acreencias SIN asumir nada de prod (REGLA #2). Responde:

  A. ¿Qué hay en BondsMaster? cuántas ONs, emisores, monedas, cupón, vto.
  B. ¿Sirven los flujos? shape real, cuántos flujos FUTUROS tiene cada una
     (sin flujos futuros no hay acreencia que proyectar).
  C. ¿Se pisan con Curvas? (el merge actual dedup-ea por ticker → si una ON
     ya está en Curvas, BondsMaster se ignora).
  D. ¿Tienen PRECIO VIVO? ¿el ticker ARS/USD existe en MarketSnapshot?
  E. ¿Están en Assets? (join para emisor/rating/cartera y mapping unidad↔ticker).
  F. ¿Las tienen CLIENTES? cuántas cuentas y qué nominal total en el último AuM
     → ESTE es el universo relevante para acreencias.

100% read-only. Scopeado: BondsMaster es chico; AuM se consulta SOLO el último
snapshot + $in del set de ONs (no escanea la colección). No escribe nada.

Correr:  python -m scripts.diag_ons_master
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from core.mongo import get_mongo_client_read


def _parse_fecha(raw):
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.strptime(raw.strip()[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return None


def main() -> None:
    cli = get_mongo_client_read()
    trading = cli["Trading"]
    valuaciones = cli["Valuaciones"]
    hoy = datetime.now()

    bonds = list(trading["BondsMaster"].find({}, {"_id": 0}))
    print("=" * 72)
    print(f"A. BondsMaster: {len(bonds)} documentos")
    print("=" * 72)
    if not bonds:
        print("  (colección vacía — no hay master de ONs todavía)")
        return

    # --- A/B: por instrumento -------------------------------------------------
    monedas = Counter()
    emisores = Counter()
    sin_flujos = []
    sin_flujos_futuros = []
    flujo_keys = Counter()
    assets_set = []  # el `asset` (ticker corto) de cada ON

    print(f"\n{'asset':<10}{'emisor':<22}{'mon':<5}{'cupon':>7}"
          f"{'vto':>9}{'#fl':>5}{'#fut':>6}  rango_flujos")
    print("-" * 88)
    for d in bonds:
        asset = d.get("asset", "?")
        assets_set.append(asset)
        emisor = (d.get("emisor") or "?")[:20]
        mon = d.get("moneda_flujo") or "?"
        cupon = d.get("tasa_cupon")
        vto = _parse_fecha(d.get("vencimiento"))
        flujos = d.get("flujos") or []
        for f in flujos:
            for k in f:
                flujo_keys[k] += 1
        fechas = [_parse_fecha(f.get("fecha")) for f in flujos]
        fechas = [f for f in fechas if f]
        futuros = [f for f in fechas if f > hoy]
        monedas[mon] += 1
        emisores[emisor] += 1
        if not flujos:
            sin_flujos.append(asset)
        elif not futuros:
            sin_flujos_futuros.append(asset)
        rango = ""
        if fechas:
            rango = f"{min(fechas):%Y-%m} → {max(fechas):%Y-%m}"
        cupon_s = f"{cupon:.3f}" if isinstance(cupon, (int, float)) else "-"
        vto_s = f"{vto:%Y-%m}" if vto else "-"
        print(f"{asset:<10}{emisor:<22}{mon:<5}{cupon_s:>7}"
              f"{vto_s:>9}{len(flujos):>5}{len(futuros):>6}  {rango}")

    print(f"\nMonedas:   {dict(monedas)}")
    print(f"Emisores:  {len(emisores)} distintos → {dict(emisores)}")
    print(f"Claves de flujo presentes (y en cuántos flujos): {dict(flujo_keys)}")
    if sin_flujos:
        print(f"⚠️  SIN flujos cargados ({len(sin_flujos)}): {sin_flujos}")
    if sin_flujos_futuros:
        print(f"⚠️  Con flujos pero TODOS pasados ({len(sin_flujos_futuros)}): "
              f"{sin_flujos_futuros}")

    # Sample de 2 flujos crudos para ver el shape exacto
    print("\nSample flujos crudos (primeras 2 ONs con flujos):")
    shown = 0
    for d in bonds:
        if d.get("flujos"):
            print(f"  {d.get('asset')}: {d['flujos'][:3]}")
            shown += 1
        if shown >= 2:
            break

    # --- C: overlap con Curvas ------------------------------------------------
    curvas_cortos = {c.get("ticker_corto") for c in
                     trading["Curvas"].find({}, {"ticker_corto": 1, "_id": 0})}
    pisados = [a for a in assets_set if a in curvas_cortos]
    print("\n" + "=" * 72)
    print(f"C. Overlap con Curvas (se DEDUP-ean del merge): {len(pisados)}")
    if pisados:
        print(f"   {pisados}")

    # --- D: precio vivo en MarketSnapshot ------------------------------------
    tickers_full = []
    for d in bonds:
        t = d.get("tickers") or {}
        for v in (t.get("ARS"), t.get("USD")):
            if v:
                tickers_full.append(v)
    snap_tickers = {s.get("ticker") for s in trading["MarketSnapshot"].find(
        {"ticker": {"$in": tickers_full}}, {"ticker": 1, "_id": 0})}
    print("\n" + "=" * 72)
    print(f"D. Precio vivo: {len(snap_tickers)}/{len(tickers_full)} tickers "
          f"(ARS+USD) existen en MarketSnapshot")
    faltan_precio = [t for t in tickers_full if t not in snap_tickers]
    if faltan_precio:
        print(f"   Sin snapshot ({len(faltan_precio)}): {faltan_precio[:15]}"
              f"{' ...' if len(faltan_precio) > 15 else ''}")

    # --- E: join con Assets ---------------------------------------------------
    assets_docs = list(valuaciones["Assets"].find(
        {}, {"TICKER": 1, "unidad": 1, "EMISOR": 1, "CALIFICACION": 1, "_id": 0}))
    assets_by_ticker = {a.get("TICKER"): a for a in assets_docs}
    assets_by_unidad = {a.get("unidad"): a for a in assets_docs}
    en_assets = [a for a in assets_set
                 if a in assets_by_ticker or a in assets_by_unidad]
    print("\n" + "=" * 72)
    print(f"E. En Valuaciones.Assets (por TICKER o unidad): "
          f"{len(en_assets)}/{len(assets_set)}")
    faltan_assets = [a for a in assets_set
                     if a not in assets_by_ticker and a not in assets_by_unidad]
    if faltan_assets:
        print(f"   Sin Asset ({len(faltan_assets)}): {faltan_assets}")

    # --- F: tenencia de clientes (universo de acreencias) --------------------
    # Scopeado: último snapshot + $in del set de ONs. No escanea AuM.
    ultimo = valuaciones["AuM"].find_one(sort=[("fecha_snapshot", -1)],
                                         projection={"fecha_snapshot": 1})
    print("\n" + "=" * 72)
    if not ultimo:
        print("F. AuM vacío — no se puede medir tenencia de clientes")
        return
    fsnap = ultimo["fecha_snapshot"]
    cur = valuaciones["AuM"].find(
        {"fecha_snapshot": fsnap, "unidad": {"$in": assets_set}},
        {"id_cuenta": 1, "unidad": 1, "cantidad": 1, "_id": 0})
    por_on = {}
    for r in cur:
        u = r.get("unidad")
        info = por_on.setdefault(u, {"cuentas": set(), "nominal": 0.0})
        info["cuentas"].add(r.get("id_cuenta"))
        info["nominal"] += r.get("cantidad") or 0.0
    print(f"F. Tenencia de clientes (snapshot {fsnap}) — UNIVERSO ACREENCIAS")
    print("-" * 72)
    if not por_on:
        print("   Ningún cliente tiene ONs de BondsMaster en el último AuM.")
    else:
        print(f"   {'asset':<10}{'#cuentas':>10}{'nominal_total':>18}")
        total_cuentas = set()
        for u in sorted(por_on, key=lambda x: -por_on[x]["nominal"]):
            info = por_on[u]
            total_cuentas |= info["cuentas"]
            print(f"   {u:<10}{len(info['cuentas']):>10}{info['nominal']:>18,.0f}")
        print(f"\n   {len(por_on)} ONs con tenencia, "
              f"{len(total_cuentas)} cuentas distintas en total")
    print("\n✅ diag read-only completo — nada se escribió.")


if __name__ == "__main__":
    main()
