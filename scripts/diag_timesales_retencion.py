"""diag_timesales_retencion.py — ¿Trading.TimeSales tiene retención activa y cuánto pesa?

Read-only y SOLO METADATA (REGLA #4): NO escanea la colección. Usa collStats
(instantáneo, lee estadísticas internas), list_collections (¿time-series? ¿TTL?),
index_information (¿hay TTL index?) y el índice _id para el doc más viejo/nuevo
(sort _id limit 1 = index-backed, 1 doc, sin COLLSCAN). Seguro en horario de mercado.

Responde:
  1. ¿Hay retención activa? (expireAfterSeconds si es time-series, o TTL index si normal)
  2. ¿Cuánto pesa? (docs, storage, índices, avg por doc)
  3. ¿Desde cuándo hay datos y cuánto crece por día? → estima el ahorro de bajar la retención

    python -m scripts.diag_timesales_retencion
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.mongo import get_mongo_client_read

DB, COLL = "Trading", "TimeSales"


def _mb(n: float | None) -> str:
    return f"{(n or 0) / 1_048_576:,.0f} MB"


def _ts_opts(db, coll: str) -> tuple[bool, int | None, str | None]:
    """(es_timeseries, expireAfterSeconds, timeField) desde list_collections."""
    for c in db.list_collections(filter={"name": coll}):
        opts = c.get("options", {})
        ts = opts.get("timeseries")
        if ts:
            return True, opts.get("expireAfterSeconds"), ts.get("timeField")
        return False, opts.get("expireAfterSeconds"), None
    return False, None, None


def _edad_doc(col, es_ts: bool, time_field: str | None):
    """(mas_viejo, mas_nuevo) como datetime — index-backed, 1 doc cada uno.

    Normal: ObjectId del _id (su generation_time ≈ ingesta). Time-series: el
    timeField está clusterizado → min/max barato."""
    try:
        if es_ts and time_field:
            old = col.find({}, {time_field: 1, "_id": 0}).sort([(time_field, 1)]).limit(1)
            new = col.find({}, {time_field: 1, "_id": 0}).sort([(time_field, -1)]).limit(1)
            o = next(iter(old), {}).get(time_field)
            n = next(iter(new), {}).get(time_field)
            return o, n
        old = col.find({}, {"_id": 1}).sort([("_id", 1)]).limit(1)
        new = col.find({}, {"_id": 1}).sort([("_id", -1)]).limit(1)
        o = next(iter(old), {}).get("_id")
        n = next(iter(new), {}).get("_id")
        return (o.generation_time if o else None), (n.generation_time if n else None)
    except Exception as e:
        print(f"  (no se pudo determinar edad: {type(e).__name__}: {str(e)[:50]})")
        return None, None


def main() -> int:
    cli = get_mongo_client_read()
    db = cli[DB]
    col = db[COLL]

    print(f"═══ {DB}.{COLL} — retención y peso (read-only) ═══\n")

    es_ts, expire, time_field = _ts_opts(db, COLL)
    stats = db.command("collStats", COLL)
    n = stats.get("count", 0)
    storage = stats.get("storageSize", 0)
    idx = stats.get("totalIndexSize", 0)
    avg = stats.get("avgObjSize", 0)

    # ── 1) Retención ──
    print("1) RETENCIÓN")
    print(f"   tipo colección : {'time-series' if es_ts else 'normal'}")
    ttl_activo = False
    if es_ts:
        if expire:
            print(f"   ✅ TTL ACTIVO  : expireAfterSeconds={expire} = {expire / 86400:.0f} días")
            ttl_activo = True
        else:
            print("   ❌ SIN TTL     : expireAfterSeconds=None → crece sin techo")
    else:
        ttl_idx = [
            (nm, sp) for nm, sp in col.index_information().items()
            if "expireAfterSeconds" in sp
        ]
        if ttl_idx:
            for nm, sp in ttl_idx:
                d = sp["expireAfterSeconds"] / 86400
                print(f"   ✅ TTL ACTIVO  : índice '{nm}' {list(sp['key'])} → {d:.0f} días")
            ttl_activo = True
        else:
            print("   ❌ SIN TTL     : ningún índice con expireAfterSeconds → crece sin techo")

    # ── 2) Peso ──
    print("\n2) PESO")
    print(f"   documentos     : {n:,}")
    print(f"   datos (storage): {_mb(storage)}")
    print(f"   índices        : {_mb(idx)}  ({stats.get('nindexes', '?')} índices)")
    print(f"   total en disco : {_mb(storage + idx)}")
    print(f"   avg por doc    : {avg:,.0f} bytes")

    # ── 3) Antigüedad + estimación de ahorro ──
    print("\n3) ANTIGÜEDAD Y CRECIMIENTO (estimado)")
    viejo, nuevo = _edad_doc(col, es_ts, time_field)
    if viejo and nuevo:
        viejo = viejo if viejo.tzinfo else viejo.replace(tzinfo=UTC)
        nuevo = nuevo if nuevo.tzinfo else nuevo.replace(tzinfo=UTC)
        dias_hist = max((nuevo - viejo).days, 1)
        print(f"   doc más viejo  : {viejo:%Y-%m-%d}")
        print(f"   doc más nuevo  : {nuevo:%Y-%m-%d}")
        print(f"   historia       : {dias_hist} días")
        por_dia = n / dias_hist
        bytes_dia = (storage + idx) / dias_hist
        print(f"   ~docs/día      : {por_dia:,.0f}")
        print(f"   ~peso/día      : {_mb(bytes_dia)}")
        print("\n   Estimación de lo que QUEDARÍA / SE LIBERARÍA por retención")
        print("   (hipótesis lineal — el real lo da el TTL al aplicarse):")
        for ret in (90, 45, 30, 20):
            quedan_dias = min(ret, dias_hist)
            quedan = _mb(bytes_dia * quedan_dias)
            libera = _mb(bytes_dia * max(dias_hist - ret, 0))
            print(f"     retención {ret:>3}d → quedan ~{quedan:>9}  | se libera ~{libera}")
    else:
        print("   (sin datos de antigüedad)")

    print("\n" + "─" * 60)
    if not ttl_activo:
        print("VEREDICTO: NO hay retención activa → escenario B (crece sin techo).")
        print("Acción candidata: python -m scripts.db_maintenance --apply --solo ttl")
        print("(aplica el TTL de 90d ya definido; FUERA de rueda — REGLA #4).")
    else:
        print("VEREDICTO: retención activa. Evaluar bajar el TTL al working set (≤20d + colchón).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
