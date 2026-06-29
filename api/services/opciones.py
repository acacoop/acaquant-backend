"""Capa de servicio — opciones: helpers puros + mutación de tasa (SQL-native).

SQL-NATIVE (decomiso Mongo): el dominio OPCIONES se lee 100% desde Postgres
(`api/services/opciones_sql.py` → `mercado.options_*`). La DB `Opciones` (Mongo)
fue dropeada. Este módulo conserva SOLO lo que NO es lectura Mongo:

  * `update_opciones_tasa` — mutación de la tasa risk-free (escribe SQL
    `mercado.options_metadata.config`).
  * `_leg_key`, `_pick_px`, `estrategia_desde_buckets` — helpers PUROS de pricing
    de estrategias, reusados por `opciones_sql.estrategia_historico` (la fuente
    de los buckets es SQL).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha1


def update_opciones_tasa(valor: float) -> dict:
    """Actualiza la tasa libre de riesgo en mercado.options_metadata.config (SQL-native).
    Mergea SOLO el campo `tasa` (vía `||` jsonb) → no pisa `expiries`/`expiries_disponibles`
    que escriben el Manager/motor. El motor la toma en su próximo chequeo (~5 min).
    Post-update hace clear_cache() (no decorator — es mutación)."""
    from api.cache import clear_cache
    from core import pg_mirror
    pg_mirror.merge_jsonb_native(
        "options_metadata", ["type"], ["config"], {"tasa": float(valor)},
    )
    clear_cache()
    return {"ok": True, "tasa": float(valor)}


# ─────────────────────────────────────────────────────────────
# Costo histórico de estrategia — helpers puros (fuente-agnósticos)
# ─────────────────────────────────────────────────────────────

def _leg_key(legs: list[dict]) -> str:
    """Firma estable del template para cacheo."""
    canon = [
        {"offset": int(leg.get("offset", 0)),
         "tipo": str(leg.get("tipo", "")).upper(),
         "side": str(leg.get("side", "")).lower(),
         "qty": int(leg.get("qty", 1))}
        for leg in legs
    ]
    # Hash de cache-key (no criptográfico) → usedforsecurity=False.
    return sha1(json.dumps(canon, sort_keys=True).encode(), usedforsecurity=False).hexdigest()[:12]


def _pick_px(bid: float, offer: float, last: float, side: str) -> float:
    """Replica lib/estrategias.ts::getPx.

    buy  → offer (si bid>0 y offer>0) si no last
    sell → bid   (si bid>0 y offer>0) si no last
    """
    bid = bid or 0
    offer = offer or 0
    last = last or 0
    if offer > 0 and bid > 0:
        return offer if side == "buy" else bid
    return last


def estrategia_desde_buckets(
    buckets: dict[datetime, list[dict]], legs: list[dict],
) -> list[dict]:
    """Post-procesa los buckets {bucket: [{symbol,bid,offer,last,strike,tipo,spot}]} → serie
    de costo de la estrategia. Independiente de la fuente (SQL) → lo reusa
    `opciones_sql.estrategia_historico`.

    Pricing por pata (matchea lib/estrategias.ts):
        buy  → offer (si offer y bid >0) si no last
        sell → bid   (si offer y bid >0) si no last
    Buckets donde la estrategia no es válida (pata fuera de rango / iliquida) se omiten."""
    out: list[dict] = []
    for ts, docs in buckets.items():
        # buildPorStrike: {strike: {CALL: doc, PUT: doc}}
        por_strike: dict[float, dict] = {}
        spot = 0.0
        for d in docs:
            k = d.get("strike")
            t = d.get("tipo")
            if not k or t not in ("CALL", "PUT"):
                continue
            por_strike.setdefault(float(k), {})[t] = d
            s = d.get("spot") or 0
            if s > spot:  # mejor proxy: spot más alto reportado (tick más reciente del bucket)
                spot = float(s)

        if spot <= 0 or not por_strike:
            continue

        # liquidStrikes: hay bid>0 o offer>0 en call o put
        def _liq(d):
            return bool(d) and ((d.get("bid") or 0) > 0 or (d.get("offer") or 0) > 0)

        liquid = sorted(
            k for k, v in por_strike.items() if _liq(v.get("CALL")) or _liq(v.get("PUT"))
        )
        if not liquid:
            continue

        # ATM = strike líquido más cercano al spot
        atm_idx = min(range(len(liquid)), key=lambda i: abs(liquid[i] - spot))
        atm = liquid[atm_idx]

        # Aplicar legs (offsets)
        valid = True
        neto = 0.0
        used_k: list[float] = []
        for leg in legs:
            idx = atm_idx + int(leg.get("offset", 0))
            if idx < 0 or idx >= len(liquid):
                valid = False
                break
            K = liquid[idx]
            d = por_strike.get(K, {}).get(str(leg.get("tipo", "")).upper())
            px = _pick_px(d.get("bid"), d.get("offer"), d.get("last"), leg.get("side", "buy")) if d else 0
            if px <= 0:
                valid = False
                break
            side = str(leg.get("side", "buy")).lower()
            qty = int(leg.get("qty", 1))
            mult = 1 if side == "buy" else -1
            neto += px * qty * mult
            used_k.append(K)

        if not valid:
            continue

        uniq_k = sorted(set(used_k))
        out.append({
            "ts":      ts.replace(tzinfo=UTC).isoformat() if ts.tzinfo is None else ts.isoformat(),
            "costo":   round(neto * 100, 2),
            "atm":     atm,
            "spot":    round(spot, 2),
            "strikes": "/".join(f"{int(k)}" for k in uniq_k),
        })

    out.sort(key=lambda x: x["ts"])
    return out
