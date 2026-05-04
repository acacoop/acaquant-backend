"""diagnostico_retorno_carry.py — dx en 30s del estado de Retorno Total y Carry Trade.

Read-only. Responde 4 preguntas:
1. ¿SnapshotsCierre está al día por curva? ¿Cuál es la última fecha?
2. ¿Está el cierre del 30/04 (jueves) por curva?
3. ¿MarketSnapshot tiene precios vivos para usar como punto live?
4. ¿Valuaciones.Dolar tiene fechas raras (sáb/dom) en los últimos 7 días?

Uso:
    python -m scripts.diagnostico_retorno_carry
"""
from datetime import date, datetime, timedelta

from core.mongo import get_mongo_client_read


def _mark(ok: bool) -> str:
    return "[OK]" if ok else "[!! ]"


def main() -> None:
    cli = get_mongo_client_read()
    db_trd = cli["Trading"]
    db_val = cli["Valuaciones"]

    hoy = date.today()
    print()
    print("=" * 64)
    print(f" Diagnóstico Retorno Total + Carry Trade   ({hoy.isoformat()})")
    print("=" * 64)

    # ── 1. SnapshotsCierre por curva ───────────────────────────────
    print("\n[1] SnapshotsCierre — última fecha persistida por curva")
    rows = list(db_trd["SnapshotsCierre"].aggregate([
        {"$group": {
            "_id":     "$curva",
            "ult":     {"$max": "$ts_cierre"},
            "primero": {"$min": "$ts_cierre"},
            "n":       {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ]))
    if not rows:
        print("    [!! ] Colección vacía. Hay que correr el backfill.")
    for r in rows:
        try:
            ult = date.fromisoformat(r["ult"])
            gap = (hoy - ult).days
        except Exception:
            gap = -1
        ok = 0 <= gap <= 3   # tolerancia fin de semana
        print(
            f"    {_mark(ok)} {r['_id']:<14}  "
            f"última: {r['ult']}  (hace {gap}d)  ·  {r['n']:>5} docs  ·  "
            f"desde {r['primero']}"
        )

    # ── 2. ¿Hay cierre del 30/04 (jueves) por curva? ─────────────
    print("\n[2] Cobertura del jueves 30/04 por curva")
    for curva in ("tasa_fija", "cer", "soberanos", "tamar", "dolar_linked"):
        n = db_trd["SnapshotsCierre"].count_documents(
            {"curva": curva, "ts_cierre": "2026-04-30"},
        )
        print(f"    {_mark(n > 0)} {curva:<14}  →  {n} bonos persistidos")

    # ── 3. MarketSnapshot — ¿hay precios live? ────────────────────
    print("\n[3] MarketSnapshot — bonos con precio live por curva")
    for curva in ("tasa_fija", "cer"):
        tickers = [
            d["ticker"]
            for d in db_trd["Curvas"].find({"curva": curva}, {"_id": 0, "ticker": 1})
        ]
        n_total = len(tickers)
        n_live = db_trd["MarketSnapshot"].count_documents({
            "ticker":             {"$in": tickers},
            "metrics.last_price": {"$gt": 0},
        })
        print(f"    {_mark(n_live > 0)} {curva:<14}  →  {n_live}/{n_total} bonos con precio")

    # ── 4. Valuaciones.Dolar — fechas raras en últimos 7 días ─────
    print("\n[4] Valuaciones.Dolar — días con MEP en los últimos 7d (¿hay sáb/dom?)")
    desde = datetime.combine(hoy - timedelta(days=7), datetime.min.time())
    docs = list(db_val["Dolar"].aggregate([
        {"$match": {"timestamp": {"$gte": desde}, "mep": {"$gt": 0}}},
        {"$addFields": {
            "fecha": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
        }},
        {"$group": {
            "_id":     "$fecha",
            "n":       {"$sum": 1},
            "ult_mep": {"$last": "$mep"},
        }},
        {"$sort": {"_id": 1}},
    ]))
    if not docs:
        print("    (sin datos en la ventana)")
    for d in docs:
        try:
            f = date.fromisoformat(d["_id"])
            wd = f.weekday()  # 0=lun, 6=dom
        except Exception:
            wd = -1
        nombre = ["lun", "mar", "mié", "jue", "vie", "SÁB", "DOM"][wd] if wd >= 0 else "?"
        es_finde = wd >= 5
        flag = "[!! FINDE]" if es_finde else "[OK hábil]"
        ult_mep = d.get("ult_mep")
        ult_mep_s = f"{ult_mep:.2f}" if isinstance(ult_mep, (int, float)) else str(ult_mep)
        print(f"    {flag}  {d['_id']} ({nombre})   {d['n']:>5} docs   ult_mep={ult_mep_s}")

    print()
    print("=" * 64)
    print(" Lectura del reporte:")
    print("   - [1] última fecha menor a hoy-3d → falta backfill o cron caído")
    print("   - [2] [!!] en tasa_fija/cer → correr backfill_snapshots_cierre 30/04")
    print("   - [3] 0 bonos con precio → motores de mercado están caídos")
    print("   - [4] [!! FINDE] → hay cron escribiendo Dolar fuera de L-V")
    print("=" * 64)
    print()


if __name__ == "__main__":
    main()
