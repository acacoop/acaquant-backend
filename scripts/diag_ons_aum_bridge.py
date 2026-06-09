"""scripts/diag_ons_aum_bridge.py — encontrar el JOIN real ON ↔ tenencia.

El diag anterior cruzó AuM.unidad DIRECTO contra BondsMaster.asset y dio 0
(falso negativo): el join del repo va por Assets (AuM.unidad → Assets →
instrumento). Este diag arregla eso y resuelve la pregunta clave para el motor
de acreencias: ¿con qué CÓDIGO tienen los clientes las ONs en AuM?

Hace 3 cosas, todas read-only y scopeadas al último snapshot de AuM:

  1. MAPPING vía Assets: para cada ON (asset "YM40O" y su variante USD "YM40D"),
     busca el doc en Assets (por TICKER o unidad) y reporta qué `unidad` usa
     Assets (que es la clave con la que AuM guarda la tenencia).

  2. JOIN correcto a AuM: con esas `unidad` de Assets, consulta el último AuM
     y cuenta cuentas + nominal por ON. (lo que F debió hacer).

  3. DESCUBRIMIENTO independiente del mapping: recorre TODO el último AuM,
     enriquece cada unidad con Assets (emisor/clase_activo) y lista las que
     pintan a ON (clase_activo ~ 'obligaci'/'negociable'/'ON', o emisor ∈ los
     20 emisores de BondsMaster). Esto caza tenencias bajo códigos que NO
     mapeamos — incluida la variante 'D' (dólar) si los clientes la usan.

Correr:  python -m scripts.diag_ons_aum_bridge
"""
from __future__ import annotations

from collections import defaultdict

from core.mongo import get_mongo_client_read


def _short_from_full(full: str) -> str:
    """'MERV - XMEV - YM40D - 24hs' → 'YM40D'. '' si no parsea."""
    if not full or not isinstance(full, str):
        return ""
    parts = [p.strip() for p in full.split(" - ")]
    return parts[2] if len(parts) >= 3 else ""


def main() -> None:
    cli = get_mongo_client_read()
    trading = cli["Trading"]
    valuaciones = cli["Valuaciones"]

    bonds = list(trading["BondsMaster"].find(
        {}, {"_id": 0, "asset": 1, "emisor": 1, "tickers": 1, "moneda_flujo": 1}))
    emisores_on = {(b.get("emisor") or "").strip() for b in bonds if b.get("emisor")}

    # Índices de Assets en memoria (colección chica ~1630).
    assets = list(valuaciones["Assets"].find(
        {}, {"_id": 0, "unidad": 1, "TICKER": 1, "EMISOR": 1, "CLASE_ACTIVO": 1}))
    by_ticker = {a.get("TICKER"): a for a in assets if a.get("TICKER")}
    by_unidad = {a.get("unidad"): a for a in assets if a.get("unidad")}

    # --- 1. MAPPING ON → Assets.unidad ---------------------------------------
    print("=" * 92)
    print("1. MAPPING vía Assets — qué `unidad` de Assets corresponde a cada ON")
    print("=" * 92)
    print(f"{'asset':<8}{'usd':<8}{'match_por':<16}{'assets.unidad':<16}"
          f"{'assets.TICKER':<16}{'clase_activo':<20}")
    print("-" * 92)
    aum_keys: dict[str, str] = {}  # unidad de Assets → asset ON (para el join)
    sin_map = []
    for b in bonds:
        asset = b.get("asset", "")
        t = b.get("tickers") or {}
        usd = _short_from_full(t.get("USD", ""))
        cand = [asset, usd]
        adoc, how = None, ""
        for c in cand:
            if not c:
                continue
            if c in by_ticker:
                adoc, how = by_ticker[c], f"TICKER=={c}"
                break
            if c in by_unidad:
                adoc, how = by_unidad[c], f"unidad=={c}"
                break
        if adoc:
            au = adoc.get("unidad", "")
            aum_keys[au] = asset
            print(f"{asset:<8}{usd:<8}{how:<16}{au:<16}"
                  f"{adoc.get('TICKER',''):<16}{(adoc.get('CLASE_ACTIVO') or '')[:18]:<20}")
        else:
            sin_map.append(asset)
    if sin_map:
        print(f"\n⚠️  {len(sin_map)} ONs sin match en Assets: {sin_map}")

    # --- 2. JOIN correcto a AuM ----------------------------------------------
    ultimo = valuaciones["AuM"].find_one(
        sort=[("fecha_snapshot", -1)], projection={"fecha_snapshot": 1})
    if not ultimo:
        print("\nAuM vacío — corto acá.")
        return
    fsnap = ultimo["fecha_snapshot"]

    print("\n" + "=" * 92)
    print(f"2. JOIN correcto a AuM (snapshot {fsnap}) usando las unidad de Assets")
    print("=" * 92)
    if aum_keys:
        cur = valuaciones["AuM"].find(
            {"fecha_snapshot": fsnap, "unidad": {"$in": list(aum_keys)}},
            {"_id": 0, "id_cuenta": 1, "unidad": 1, "cantidad": 1})
        agg: dict[str, dict] = defaultdict(lambda: {"cuentas": set(), "nominal": 0.0})
        for r in cur:
            on = aum_keys.get(r.get("unidad"), r.get("unidad"))
            agg[on]["cuentas"].add(r.get("id_cuenta"))
            agg[on]["nominal"] += r.get("cantidad") or 0.0
        if agg:
            print(f"{'ON':<8}{'#cuentas':>10}{'nominal_total':>18}")
            tot = set()
            for on in sorted(agg, key=lambda x: -agg[x]["nominal"]):
                tot |= agg[on]["cuentas"]
                print(f"{on:<8}{len(agg[on]['cuentas']):>10}{agg[on]['nominal']:>18,.0f}")
            print(f"\n→ {len(agg)} ONs con tenencia vía mapping, {len(tot)} cuentas")
        else:
            print("  Las unidad mapeadas no aparecen en el último AuM.")

    # --- 3. DESCUBRIMIENTO (independiente del mapping) -----------------------
    print("\n" + "=" * 92)
    print(f"3. DESCUBRIMIENTO — toda tenencia que PINTA a ON en AuM {fsnap}")
    print("   (clase_activo ~ obligaci/negociable, o emisor ∈ emisores de BondsMaster)")
    print("=" * 92)
    cur = valuaciones["AuM"].find(
        {"fecha_snapshot": fsnap},
        {"_id": 0, "id_cuenta": 1, "unidad": 1, "cantidad": 1})
    disc: dict[str, dict] = defaultdict(lambda: {"cuentas": set(), "nominal": 0.0})
    for r in cur:
        u = r.get("unidad")
        adoc = by_unidad.get(u) or by_ticker.get(u)
        emisor = (adoc.get("EMISOR") if adoc else "") or ""
        clase = (adoc.get("CLASE_ACTIVO") if adoc else "") or ""
        clase_l = clase.lower()
        es_on = ("obligaci" in clase_l or "negociable" in clase_l
                 or clase_l.strip() in {"on", "ons"}
                 or emisor.strip() in emisores_on)
        if es_on:
            d = disc[u]
            d["cuentas"].add(r.get("id_cuenta"))
            d["nominal"] += r.get("cantidad") or 0.0
            d["emisor"] = emisor
            d["clase"] = clase
            d["mapeada"] = "sí" if u in aum_keys else "NO"
    if not disc:
        print("  Ninguna tenencia pinta a ON. (revisar criterio o que AuM excluya ONs)")
    else:
        print(f"{'unidad':<10}{'emisor':<22}{'clase_activo':<22}"
              f"{'#ctas':>6}{'nominal':>16}{'mapeada?':>10}")
        print("-" * 92)
        tot = set()
        for u in sorted(disc, key=lambda x: -disc[x]["nominal"]):
            d = disc[u]
            tot |= d["cuentas"]
            print(f"{u:<10}{(d['emisor'])[:20]:<22}{(d['clase'])[:20]:<22}"
                  f"{len(d['cuentas']):>6}{d['nominal']:>16,.0f}{d['mapeada']:>10}")
        no_map = [u for u in disc if disc[u]["mapeada"] == "NO"]
        print(f"\n→ {len(disc)} unidades ON con tenencia, {len(tot)} cuentas. "
              f"{len(no_map)} NO mapeadas a BondsMaster: {no_map}")

    print("\n✅ diag read-only completo — nada se escribió.")


if __name__ == "__main__":
    main()
