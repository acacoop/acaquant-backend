"""diag_bondsmaster_vs_curvas.py — ¿qué de BondsMaster falta en Trading.Curvas? (read-only)

Paso 1 del decommission de BondsMaster (consolidar todo en Trading.Curvas). El sync
(ons.sync_ons_to_curvas) ya copia BondsMaster → Curvas como on_<sector> en cada edición,
así que la mayoría debería estar. Esto LO CONFIRMA antes de tocar nada:

  - en Curvas (OK): el ticker_corto del BM está en Curvas.
  - FALTA: no está → hay que sincronizar (python -m ... o re-guardar la ON).
  - flujos≠: está pero con distinta cantidad de flujos (stale) → re-sync.

    python -m scripts.diag_bondsmaster_vs_curvas
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def main() -> int:
    trd = get_mongo_client_read()["Trading"]
    curvas = {c["ticker_corto"]: c for c in trd["Curvas"].find(
        {}, {"_id": 0, "ticker_corto": 1, "flujos": 1, "curva": 1}) if c.get("ticker_corto")}

    bm = list(trd["BondsMaster"].find({}, {"_id": 0}))
    print(f"═══ BondsMaster ({len(bm)} docs) vs Trading.Curvas ═══\n")

    falta, stale, ok = [], [], 0
    for b in bm:
        asset = b.get("asset")
        if not asset:
            continue
        c = curvas.get(asset)
        n_bm = len(b.get("flujos") or [])
        if not c:
            falta.append((asset, b.get("sector"), n_bm))
        else:
            n_cv = len(c.get("flujos") or [])
            if n_bm != n_cv:
                stale.append((asset, c.get("curva"), n_bm, n_cv))
            else:
                ok += 1

    print(f"✅ en Curvas y al día : {ok}")
    print(f"⚠ FALTAN en Curvas    : {len(falta)}")
    print(f"⚠ flujos distintos     : {len(stale)}\n")

    if falta:
        print("── FALTAN (hay que sincronizar) ──")
        print(f"  {'asset':<12}{'sector':<14}{'#flujos BM':>11}")
        for a, s, n in sorted(falta):
            print(f"  {a:<12}{(s or '?'):<14}{n:>11}")
    if stale:
        print("\n── flujos distintos (BM vs Curvas) ──")
        print(f"  {'asset':<12}{'curva':<14}{'BM':>5}{'Curvas':>8}")
        for a, cv, nb, nc in sorted(stale):
            print(f"  {a:<12}{(cv or '?'):<14}{nb:>5}{nc:>8}")

    print("\n" + "─" * 56)
    if not falta and not stale:
        print("✅ TODO BondsMaster ya está en Curvas → el paso 1 está hecho.")
        print("   Se puede avanzar a repuntar los consumidores (paso 2).")
    else:
        print("Para sincronizar los que faltan/están stale, correr el sync:")
        print('  python -c "from api.services.ons import sync_ons_to_curvas as s; print(s())"')
        print("(idempotente: copia BondsMaster → Curvas on_*). Después re-correr este diag.")

    # Cross-check: ¿hay on_* en Curvas que NO tengan su BondsMaster? (info)
    on_sin_bm = [tc for tc, c in curvas.items()
                 if str(c.get("curva") or "").startswith("on") and tc not in {b.get("asset") for b in bm}]
    if on_sin_bm:
        print(f"\nNota: {len(on_sin_bm)} curva on_* en Curvas SIN doc en BondsMaster "
              f"(ya viven solo en Curvas): {', '.join(sorted(on_sin_bm)[:15])}"
              f"{'…' if len(on_sin_bm) > 15 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
