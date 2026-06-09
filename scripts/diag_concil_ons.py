"""scripts/diag_concil_ons.py — conciliador de cobertura ONs (AuM vs Curvas).

Qué instrumentos HD (hard dollar) / DL (dollar linked) tienen los clientes y
NO están modelados en Trading.Curvas → el GAP a dar de alta.

Relación (la clave en común es el ticker):
    Valuaciones.AuM.unidad   (último fecha_snapshot)
      → Valuaciones.Assets   (match por unidad) · filtro CARTERA ∈ {HD, DL}
      → Assets.TICKER  ↔  Trading.Curvas (ticker_corto / ticker)

Match "menos restrictivo": exacto O por base (misma base, distinta pata O/D →
cuenta como cubierto, ej. YM40D ya está si Curvas tiene YM40O). Sin nominales.

100% read-only. AuM scopeado al último snapshot. No escribe nada.

Correr:  python -m scripts.diag_concil_ons
"""
from __future__ import annotations

from collections import Counter

from core.mongo import get_mongo_client_read

# Carteras que SÍ entran a la conciliación de ONs (hard dollar / dollar linked).
CARTERAS_ON = {"HD", "DL"}


def _base(code: str | None) -> str:
    """'YM40D' → 'YM40', 'YM40O' → 'YM40'. Deja igual lo que no tiene pata."""
    if not code or len(code) < 3:
        return code or ""
    return code[:-1] if code[-1] in ("O", "D", "C") else code


def main() -> None:
    cli = get_mongo_client_read()
    val = cli["Valuaciones"]
    trading = cli["Trading"]

    # 1) Último snapshot + unidades que tienen los clientes.
    ultimo = val["AuM"].find_one(sort=[("fecha_snapshot", -1)], projection={"fecha_snapshot": 1})
    if not ultimo:
        print("AuM vacío.")
        return
    fsnap = ultimo["fecha_snapshot"]
    unidades = [u for u in val["AuM"].distinct("unidad", {"fecha_snapshot": fsnap}) if u]
    print(f"AuM snapshot {fsnap}: {len(unidades)} unidades en cartera de clientes")

    # 2) Assets por unidad/ticker.
    assets = list(val["Assets"].find({}, {"_id": 0, "unidad": 1, "TICKER": 1, "EMISOR": 1, "CARTERA": 1}))
    by_unidad = {a.get("unidad"): a for a in assets if a.get("unidad")}
    by_ticker = {a.get("TICKER"): a for a in assets if a.get("TICKER")}

    # 3) Curvas: sets para el match (exacto + base).
    curvas = list(trading["Curvas"].find({}, {"_id": 0, "ticker": 1, "ticker_corto": 1}))
    set_full = {c.get("ticker") for c in curvas if c.get("ticker")}
    set_corto = {c.get("ticker_corto") for c in curvas if c.get("ticker_corto")}
    set_base = {_base(c) for c in set_corto}

    def cubierto(ticker: str | None, unidad: str) -> bool:
        for cand in (ticker, unidad):
            if cand and (cand in set_corto or cand in set_full or _base(cand) in set_base):
                return True
        return False

    # 4) Filtrar a CARTERA HD/DL y clasificar cubierto / falta.
    carteras_vistas: Counter = Counter()
    falta, cubre = [], 0
    for unidad in unidades:
        a = by_unidad.get(unidad) or by_ticker.get(unidad)
        if not a:
            continue
        cartera = (a.get("CARTERA") or "").strip()
        carteras_vistas[cartera or "(vacío)"] += 1
        if cartera.upper() not in CARTERAS_ON:
            continue
        if cubierto(a.get("TICKER"), unidad):
            cubre += 1
        else:
            falta.append({"unidad": unidad, "ticker": a.get("TICKER"),
                          "emisor": a.get("EMISOR"), "cartera": cartera})

    # 5) Salida.
    print("\nCARTERA de lo que tienen los clientes (para confirmar HD/DL):")
    for c, n in carteras_vistas.most_common():
        marca = "  ← ON" if c.upper() in CARTERAS_ON else ""
        print(f"   {c:<28} {n}{marca}")

    total_on = cubre + len(falta)
    print("\n" + "=" * 64)
    print(f"HD/DL en cartera de clientes: {total_on}  ·  ya en Curvas: {cubre}  ·  FALTAN: {len(falta)}")
    print("=" * 64)
    if falta:
        print(f"\nGAP — dar de alta en Curvas ({len(falta)}):")
        print(f"   {'unidad':<12}{'ticker':<14}{'cartera':<8}emisor")
        print("   " + "-" * 60)
        for g in sorted(falta, key=lambda x: ((x['emisor'] or ''), x['unidad'])):
            print(f"   {g['unidad']:<12}{(g['ticker'] or '')[:13]:<14}{g['cartera']:<8}{g['emisor'] or ''}")
    else:
        print("\n✅ No falta ninguna HD/DL: todo lo que tienen los clientes ya está en Curvas.")

    print("\n✅ diag read-only completo — nada se escribió.")


if __name__ == "__main__":
    main()
