"""scripts/purge_ticker.py — borra un ticker de bono de TODAS las colecciones Mongo.

Para cuando un instrumento mal cargado (ej. TY30P) rompe motores y hay que sacarlo
de raíz. El mapa de dónde vive un ticker (colección + campo + formato) está
verificado en el código (engines/valores.py, jobs/snapshot_cierre.py, jobs/aum.py,
core/adhoc_subscriptions.py, api/services/canje.py).

Dos formatos en juego:
  - CORTO: "TY30P"                         (Curvas.ticker_corto, Assets.unidad, AuM.unidad)
  - LARGO: "MERV - XMEV - TY30P - <plazo>" (ticker en el resto: MarketSnapshot, TimeSales…)

El match del formato largo es ANCLADO al símbolo exacto (`^MERV - XMEV - TY30P - `)
→ NO pisa variantes como TY30PD (rueda dólar) ni TY30PC (CCL). El regex anclado en `^`
usa el índice de `ticker` donde existe (TimeSales, MarketSnapshot) → barato (REGLA #4).
Igual: SIEMPRE corré primero el dry-run y mirá los counts antes de --apply.

Grupos:
  - MERCADO (default en --apply): Curvas, MarketSnapshot, SnapshotsCierre, TimeSales,
    CanjeCierre, AdhocSubscriptions. Son datos de instrumento/mercado → seguro borrar.
  - TENENCIAS (solo con --include-aum): Valuaciones.Assets + Valuaciones.AuM. OJO: son
    valuación de clientes. Si el bono SIGUE en Aunesa, el job jobs.aum lo vuelve a crear
    mañana. Borralas solo si es un alta fantasma/mala, no una tenencia real.

Uso:
    python -m scripts.purge_ticker --ticker TY30P                 # DRY-RUN (no toca nada)
    python -m scripts.purge_ticker --ticker TY30P --apply         # borra grupo MERCADO
    python -m scripts.purge_ticker --ticker TY30P --apply --include-aum   # + Assets/AuM
"""
from __future__ import annotations

import argparse
import re

from core.mongo import get_mongo_client, get_mongo_client_read

try:
    import config
except Exception:  # pragma: no cover
    config = None


# (db, coll, filtro, grupo). filtro se arma por símbolo dentro de run().
def _targets(symbol: str) -> list[tuple[str, str, dict, str]]:
    esc = re.escape(symbol)
    long_re = {"$regex": rf"^MERV - XMEV - {esc} - "}
    return [
        # ── MERCADO / instrumento ─────────────────────────────────────────────
        ("Trading", "Curvas",
         {"$or": [{"ticker": long_re}, {"ticker_corto": symbol}]}, "mercado"),
        ("Trading", "MarketSnapshot", {"ticker": long_re}, "mercado"),
        ("Trading", "SnapshotsCierre",
         {"$or": [{"ticker": long_re}, {"ticker_corto": symbol}]}, "mercado"),
        ("Trading", "TimeSales", {"ticker": long_re}, "mercado"),
        ("Trading", "CanjeCierre", {"ticker": long_re}, "mercado"),
        ("Trading", "AdhocSubscriptions",
         {"$or": [{"ticker": long_re}, {"_id": long_re}]}, "mercado"),
        # ── TENENCIAS / valuación (solo con --include-aum) ───────────────────
        ("Valuaciones", "Assets",
         {"$or": [{"unidad": symbol}, {"TICKER": long_re}]}, "tenencias"),
        ("Valuaciones", "AuM", {"unidad": symbol}, "tenencias"),
    ]


def _variantes(client, symbol: str) -> list[str]:
    """Distinct de tickers que CONTIENEN el símbolo (para mostrar TY30PC/TY30PD si existen).
    Solo sobre colecciones master chicas e indexadas → barato. NO se borran: se reportan."""
    contiene = {"$regex": re.escape(symbol)}
    vistos: set[str] = set()
    for db, coll, field in [("Trading", "Curvas", "ticker"),
                            ("Valuaciones", "Assets", "TICKER"),
                            ("Valuaciones", "Assets", "unidad")]:
        try:
            for v in client[db][coll].distinct(field, {field: contiene}):
                if v:
                    vistos.add(str(v))
        except Exception:
            pass
    return sorted(vistos)


def run(symbol: str, apply: bool, include_aum: bool) -> int:
    client = get_mongo_client() if apply else get_mongo_client_read()
    targets = _targets(symbol)

    print(f"\n=== PURGE ticker '{symbol}'  ({'APPLY (borra)' if apply else 'DRY-RUN (no toca nada)'}) ===")
    print(f"    formato largo: '^MERV - XMEV - {symbol} - <plazo>'  ·  corto: '{symbol}'\n")

    total = 0
    for db, coll, filtro, grupo in targets:
        if grupo == "tenencias" and not include_aum:
            existentes = client[db].list_collection_names()
            n = client[db][coll].count_documents(filtro) if coll in existentes else 0
            flag = "  (SKIP — pasá --include-aum para tocar tenencias)"
            print(f"  [{grupo:9}] {db}.{coll:18} matches={n:<8}{flag if n else '  (0)'}")
            continue
        try:
            if coll not in client[db].list_collection_names():
                print(f"  [{grupo:9}] {db}.{coll:18} (colección no existe — skip)")
                continue
            n = client[db][coll].count_documents(filtro)
            if apply and n:
                res = client[db][coll].delete_many(filtro)
                print(f"  [{grupo:9}] {db}.{coll:18} BORRADOS={res.deleted_count}")
                total += res.deleted_count
            else:
                print(f"  [{grupo:9}] {db}.{coll:18} matches={n}")
                total += n
        except Exception as e:  # pragma: no cover
            print(f"  [{grupo:9}] {db}.{coll:18} ERROR: {e}")

    # Variantes relacionadas (no se borran): TY30PC / TY30PD / etc.
    vars_rel = _variantes(client, symbol)
    otras = [v for v in vars_rel if v != symbol and not v.startswith(f"MERV - XMEV - {symbol} - ")]
    if otras:
        print("\n  ⚠ Variantes que CONTIENEN el símbolo y NO se tocan (revisalas a mano si querés):")
        for v in otras:
            print(f"      · {v}")

    # config.TICKERS_EXTRA_PRECIOS (hardcoded en código, no Mongo)
    if config is not None:
        extra = getattr(config, "TICKERS_EXTRA_PRECIOS", []) or []
        hits = [t for t in extra if f" {symbol} " in f" {t} "]
        if hits:
            print(f"\n  ⚠ '{symbol}' aparece en config.TICKERS_EXTRA_PRECIOS (código, no Mongo): {hits}")
            print("     Eso se edita en config.py + redeploy, no lo borra este script.")

    if not apply:
        print(f"\n[DRY-RUN] {total} docs matchean. Para borrar el grupo MERCADO:")
        print(f"    python -m scripts.purge_ticker --ticker {symbol} --apply"
              + ("  --include-aum" if include_aum else ""))
        print("Después reiniciá el motor:  systemctl restart motor_curvas.service")
    else:
        print(f"\n✅ Listo. {total} docs borrados. Reiniciá:  systemctl restart motor_curvas.service")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Borra un ticker de bono de todas las colecciones.")
    ap.add_argument("--ticker", required=True, help="símbolo corto, ej. TY30P")
    ap.add_argument("--apply", action="store_true", help="borra (default: solo dry-run/check)")
    ap.add_argument("--include-aum", action="store_true",
                    help="además borra Valuaciones.Assets + AuM (tenencias — ojo, ver docstring)")
    args = ap.parse_args()
    return run(args.ticker.strip().upper(), args.apply, args.include_aum)


if __name__ == "__main__":
    raise SystemExit(main())
