"""Diag de staleness en el chain de opciones (GGAL por default).

Compara TRES vistas de la misma data para ubicar dónde se queda atrás:

  [A] Mongo crudo — qué hay HOY en `Opciones.OptionsSnapshot` filtrado por
      prefijo del symbol (GFG/GFGC para GGAL). Cuenta total + bucketing de
      `updated_at` (cuántos docs en últimos 5min / 1h / 24h / +24h).
  [B] Service directo — qué devuelve `svc_opt.get_opciones(instrumento)`
      sin pasar por HTTP. Esto exhibe el shape exacto que ve el endpoint
      `/api/cotizaciones/opciones` (sin contar cache, que TTL=60s).
  [C] Diff — strikes que están en [B] pero NO en [A] (cache stale?) o
      al revés (filter del service mal aplicado?).

Objetivo: confirmar si el problema vive en
  · [A] Mongo: engine no escribe / escribe stale → motor de opciones colgado.
  · [B] Service: filtra mal o devuelve docs viejos junto con nuevos (no hay
    filter por updated_at en el service, así que tickers de expiraciones
    pasadas se cuelan si quedan huérfanos en Mongo).
  · [C] Más arriba: si A=B=correcto, el bug está en Next route / Vercel CDN.

Uso:
    python -m scripts.diag_opciones_freshness            # default GGAL
    python -m scripts.diag_opciones_freshness GGAL
    python -m scripts.diag_opciones_freshness PAMP
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from api.db import get_db_opciones
from api.services import opciones as svc_opt

# Prefijos por subyacente (las opciones MERV usan estas 3-4 letras al principio
# del symbol corto: GFG/GFGC para GGAL, PAMC/PAMV para PAMP, etc.).
PREFIJOS = {
    "GGAL": ("GFG",),
    "PAMP": ("PAM",),
    "YPFD": ("YPF",),
    "ALUA": ("ALU",),
    "TXAR": ("TXR",),
    "COME": ("COM",),
    "BMA":  ("BMA",),
}


def _bucket_age(updated_at: datetime, now: datetime) -> str:
    if updated_at is None:
        return "sin_updated_at"
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    age = now - updated_at
    if age < timedelta(minutes=5):
        return "≤5min"
    if age < timedelta(hours=1):
        return "≤1h"
    if age < timedelta(hours=24):
        return "≤24h"
    return ">24h"


def run(underlying: str) -> None:
    print("=" * 100)
    print(f"DIAG opciones freshness — underlying={underlying}")
    print("=" * 100)

    prefijos = PREFIJOS.get(underlying.upper())
    if not prefijos:
        print(f"⚠ No tengo prefijo registrado para {underlying}. Pegale a Mongo a mano.")
        print(f"  Prefijos conocidos: {list(PREFIJOS.keys())}")
        return

    db = get_db_opciones()
    col = db["OptionsSnapshot"]
    now = datetime.now(timezone.utc)

    # ── [A] Mongo crudo ──────────────────────────────────────────
    print("\n[A] Estado actual de Opciones.OptionsSnapshot")
    regex = "^(" + "|".join(prefijos) + ")"
    docs_a = list(col.find(
        {"symbol": {"$regex": regex}},
        {"_id": 0, "symbol": 1, "strike": 1, "tipo": 1, "vence": 1, "updated_at": 1, "last": 1},
    ))
    print(f"    Total docs con prefijo {prefijos}: {len(docs_a)}")

    if not docs_a:
        print("    ✗ Colección vacía para ese prefijo. Motor de opciones probablemente caído.")
        print("      Chequear: systemctl status motor_options (o como se llame el engine).")
        return

    # Bucketing por edad
    buckets = {"≤5min": 0, "≤1h": 0, "≤24h": 0, ">24h": 0, "sin_updated_at": 0}
    for d in docs_a:
        buckets[_bucket_age(d.get("updated_at"), now)] += 1
    print("    Distribución por edad de updated_at:")
    for k, v in buckets.items():
        marker = " ← stale" if k in (">24h", "sin_updated_at") and v > 0 else ""
        print(f"      {k:>14s}: {v}{marker}")

    # updated_at más reciente / más viejo
    uats = [d.get("updated_at") for d in docs_a if d.get("updated_at") is not None]
    if uats:
        ua_min = min(uats)
        ua_max = max(uats)
        if ua_min.tzinfo is None:
            ua_min = ua_min.replace(tzinfo=timezone.utc)
            ua_max = ua_max.replace(tzinfo=timezone.utc)
        print(f"    updated_at min (más viejo): {ua_min.isoformat()}")
        print(f"    updated_at max (más nuevo): {ua_max.isoformat()}")
        print(f"    edad del más nuevo:         {(now - ua_max).total_seconds():.0f}s")

    # ── [B] Service directo (lo que ve el endpoint, sin HTTP) ───
    print("\n[B] Service directo: svc_opt.get_opciones(instrumento)")
    # Limpiar cache para no leer el del run previo
    from api.cache import clear_cache
    clear_cache()
    docs_b = svc_opt.get_opciones(instrumento=underlying)
    print(f"    Devolvió {len(docs_b)} docs")
    if docs_b:
        # ¿Cuántos del service están stale?
        b_buckets = {"≤5min": 0, "≤1h": 0, "≤24h": 0, ">24h": 0, "sin_updated_at": 0}
        for d in docs_b:
            ua = d.get("updated_at")
            b_buckets[_bucket_age(ua, now)] += 1
        print("    Edad del data que retorna el service:")
        for k, v in b_buckets.items():
            marker = " ← stale" if k in (">24h", "sin_updated_at") and v > 0 else ""
            print(f"      {k:>14s}: {v}{marker}")

    # ── [C] Diff de symbols entre [A] (con _ticker_filter aplicado) y [B] ──
    print("\n[C] Diff de symbols entre Mongo y service")
    # Replicar el filtro que usa el service: _ticker_filter genera un regex
    # case-insensitive. Para comparar manzana-con-manzana, contamos lo que
    # entra al match real.
    from api.services.renta_fija import _ticker_filter
    filtro_simulado = _ticker_filter(underlying)
    docs_a_filtrados = list(col.find(
        {"symbol": filtro_simulado},
        {"_id": 0, "symbol": 1},
    ))
    syms_a = {d["symbol"] for d in docs_a_filtrados}
    syms_b = {d["instrumento"] for d in docs_b}

    print(f"    Symbols en Mongo (match _ticker_filter='{underlying}'): {len(syms_a)}")
    print(f"    Symbols devueltos por service:                          {len(syms_b)}")

    only_in_a = syms_a - syms_b
    only_in_b = syms_b - syms_a

    if only_in_a:
        print(f"\n    ⚠ {len(only_in_a)} symbols en Mongo que el service NO devuelve:")
        for s in sorted(only_in_a)[:10]:
            print(f"      · {s}")
        if len(only_in_a) > 10:
            print(f"      ... y {len(only_in_a) - 10} más")

    if only_in_b:
        print(f"\n    ⚠ {len(only_in_b)} symbols en service que YA no están en Mongo:")
        print("      (esto indicaría cache stale a nivel service, pero cache es 60s in-process)")
        for s in sorted(only_in_b)[:10]:
            print(f"      · {s}")
        if len(only_in_b) > 10:
            print(f"      ... y {len(only_in_b) - 10} más")

    if not only_in_a and not only_in_b:
        print("    ✓ Mongo y service coinciden exactamente. Bug está más arriba (Next route / CDN).")

    # ── Dump de strikes y last para inspección rápida ─────────────
    print("\n[D] Top 10 strikes por VOL (lo que aparece en la tabla)")
    docs_b_sorted = sorted(
        [d for d in docs_b if d.get("tipo") == "CALL"],
        key=lambda x: (x.get("strike") or 0),
    )
    print(f"    {'SYMBOL':<14} {'STRIKE':>10} {'LAST':>10} {'VENCE':>12} {'UPDATED_AT':>22}")
    for d in docs_b_sorted[:20]:
        ua = d.get("updated_at")
        ua_str = ua.isoformat() if isinstance(ua, datetime) else str(ua)[:22]
        print(f"    {d.get('instrumento',''):<14} {d.get('strike',0):>10.2f} "
              f"{d.get('last',0):>10.2f} {str(d.get('vence',''))[:10]:>12} {ua_str:>22}")

    print("\n" + "=" * 100)
    print("LECTURA")
    print("=" * 100)
    print("• Si [A] muestra docs en >24h → engine de opciones está stale / no escribe.")
    print("• Si [A] tiene docs frescos pero [B] devuelve viejos → @cached service pegado.")
    print("• Si [A]==[B] y son frescos → bug arriba (Vercel CDN, falta dynamic=force-dynamic en")
    print("  acaquant-web/src/app/api/cotizaciones/[...path]/route.ts).")
    print("• Si [C] tiene 'only_in_b' → cache desync raro. Si tiene 'only_in_a' → filter mal.")


if __name__ == "__main__":
    underlying = sys.argv[1].upper() if len(sys.argv) > 1 else "GGAL"
    run(underlying)
