"""scripts/diag_concil_ons.py — DESCUBRIMIENTO para el conciliador de ONs.

Objetivo: diseñar bien la relación AuM → Assets → Curvas SIN asumir (REGLA #2).
Responde las 2 incógnitas antes de construir el conciliador:

  A. ¿Qué campo/valor de Assets identifica HD (hard dollar) / DL (dollar linked)?
     → escanea TODOS los campos de Assets buscando tokens dolar/linked/hard/HD/DL
       y lista los distintos CLASE_ACTIVO / CARTERA de lo que los clientes tienen.

  B. ¿El ticker matchea directo contra Curvas o hay que normalizar la pata O/D?
     → para cada tenencia cruza Assets.TICKER contra Curvas (exacto y por base
       sin sufijo) y reporta cómo matchea.

Y ya muestra el GAP preliminar: instrumentos renta-fija-ish que los clientes
tienen y NO están en Trading.Curvas.

100% read-only. Scopeado: AuM solo el último snapshot (agregado por unidad);
Assets y Curvas son chicos. No escribe nada.

Correr:  python -m scripts.diag_concil_ons
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from core.mongo import get_mongo_client_read

# Tokens que delatan hard-dollar / dollar-linked en cualquier campo de Assets.
_HD_DL = re.compile(r"hard|d[oó]lar|dollar|linked|\bhd\b|\bdl\b", re.IGNORECASE)


def _base(code: str | None) -> str:
    """Normaliza un ticker corto sacando el sufijo de pata (O/D/C) si parece
    serlo: 'YM40D' → 'YM40', 'YM40O' → 'YM40'. Deja igual los que no aplican."""
    if not code or len(code) < 3:
        return code or ""
    return code[:-1] if code[-1] in ("O", "D", "C") else code


def main() -> None:
    cli = get_mongo_client_read()
    val = cli["Valuaciones"]
    trading = cli["Trading"]

    # 1) Último snapshot de AuM.
    ultimo = val["AuM"].find_one(sort=[("fecha_snapshot", -1)], projection={"fecha_snapshot": 1})
    if not ultimo:
        print("AuM vacío.")
        return
    fsnap = ultimo["fecha_snapshot"]

    # 2) Tenencias del snapshot agrupadas por unidad (scoped al día → barato).
    pipeline = [
        {"$match": {"fecha_snapshot": fsnap}},
        {"$group": {"_id": "$unidad",
                    "cuentas": {"$addToSet": "$id_cuenta"},
                    "nominal": {"$sum": "$cantidad"}}},
    ]
    tenencias = {}
    for d in val["AuM"].aggregate(pipeline):
        tenencias[d["_id"]] = {"n": len(d["cuentas"]), "nominal": d.get("nominal") or 0.0}
    print(f"AuM snapshot {fsnap}: {len(tenencias)} unidades distintas en cartera de clientes\n")

    # 3) Assets en memoria (chico). Índice por unidad + descubrimiento de campos.
    assets = list(val["Assets"].find({}, {"_id": 0}))
    by_unidad = {a.get("unidad"): a for a in assets if a.get("unidad")}
    by_ticker = {a.get("TICKER"): a for a in assets if a.get("TICKER")}
    todas_keys: Counter = Counter()
    for a in assets:
        todas_keys.update(a.keys())
    print("=" * 72)
    print("A. Campos presentes en Valuaciones.Assets (y en cuántos docs):")
    print("   " + ", ".join(f"{k}={n}" for k, n in todas_keys.most_common()))

    # Buscar tokens HD/DL en cualquier campo string.
    hd_dl: dict[str, Counter] = defaultdict(Counter)
    for a in assets:
        for k, v in a.items():
            if isinstance(v, str) and _HD_DL.search(v):
                hd_dl[k][v] += 1
    print("\n   Campos/valores que matchean hard/dólar/linked/HD/DL:")
    if not hd_dl:
        print("   (ninguno — el flag HD/DL NO vive en un campo de texto de Assets)")
    for k in hd_dl:
        print(f"   · {k}: {dict(hd_dl[k])}")

    # 4) Curvas: sets de tickers para el match.
    curvas = list(trading["Curvas"].find({}, {"_id": 0, "ticker": 1, "ticker_corto": 1, "curva": 1}))
    set_full = {c.get("ticker") for c in curvas if c.get("ticker")}
    set_corto = {c.get("ticker_corto") for c in curvas if c.get("ticker_corto")}
    set_base = {_base(c) for c in set_corto}

    def en_curvas(ticker: str | None, unidad: str) -> str:
        """Devuelve cómo matchea contra Curvas: 'exacto' | 'base' | 'no'."""
        for cand in (ticker, unidad):
            if not cand:
                continue
            if cand in set_corto or cand in set_full:
                return "exacto"
            if _base(cand) in set_base:
                return "base"
        return "no"

    # 5) Clasificar las tenencias: clase/cartera + match con Curvas.
    por_clase: Counter = Counter()
    por_cartera: Counter = Counter()
    sin_asset = []
    match_modo: Counter = Counter()
    fci_excluidos = 0
    gap = []  # tenencias renta-fija-ish (NO FCI) que NO están en Curvas
    for unidad, t in tenencias.items():
        a = by_unidad.get(unidad) or by_ticker.get(unidad)
        if not a:
            sin_asset.append(unidad)
            continue
        clase = (a.get("CLASE_ACTIVO") or "").strip()
        cartera = (a.get("CARTERA") or "").strip()
        # Filtro principal: los FCI NO entran a la conciliación de ONs.
        if "fci" in cartera.lower():
            fci_excluidos += 1
            continue
        por_clase[clase or "(vacío)"] += 1
        por_cartera[cartera or "(vacío)"] += 1
        ticker = a.get("TICKER")
        modo = en_curvas(ticker, unidad)
        match_modo[modo] += 1
        if modo == "no":
            gap.append({
                "unidad": unidad, "ticker": ticker, "emisor": a.get("EMISOR"),
                "clase": clase, "cartera": cartera, "n": t["n"], "nominal": t["nominal"],
            })

    print("\n" + "=" * 72)
    print(f"B. Match Assets.TICKER ↔ Curvas (sin FCI): {dict(match_modo)}")
    print("   (exacto = string idéntico · base = mismo bono distinta pata O/D · no = falta)")
    print(f"   FCI excluidos de la conciliación: {fci_excluidos}")
    if sin_asset:
        print(f"   {len(sin_asset)} unidades sin doc en Assets (ej: {sin_asset[:10]})")

    print("\n   CLASE_ACTIVO de lo que tienen los clientes (con Asset):")
    for c, n in por_clase.most_common():
        print(f"   · {c:<32} {n}")
    print("\n   CARTERA de lo que tienen los clientes (con Asset):")
    for c, n in por_cartera.most_common():
        print(f"   · {c:<32} {n}")

    # 6) Gap preliminar (todo lo no-Curvas). El user dirá qué clase/cartera = HD/DL.
    print("\n" + "=" * 72)
    print(f"GAP preliminar — tenencias que NO están en Curvas ({len(gap)}):")
    print("   (incluye FCI/acciones; filtramos a HD/DL una vez sepamos el campo)")
    print(f"\n   {'unidad':<10}{'ticker':<12}{'emisor':<20}{'clase':<24}{'#ctas':>6}{'nominal':>16}")
    print("   " + "-" * 88)
    for g in sorted(gap, key=lambda x: -x["nominal"])[:60]:
        print(f"   {g['unidad']:<10}{(g['ticker'] or '')[:11]:<12}{(g['emisor'] or '')[:18]:<20}"
              f"{g['clase'][:22]:<24}{g['n']:>6}{g['nominal']:>16,.0f}")
    if len(gap) > 60:
        print(f"   … {len(gap) - 60} más")

    print("\n✅ diag read-only completo — nada se escribió.")


if __name__ == "__main__":
    main()
