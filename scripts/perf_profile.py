"""perf_profile.py — Mide cada query real de las vistas contra Mongo (prod).

Por query reporta:
  nReturned            docs devueltos
  totalDocsExamined    docs leídos por el engine (si >> nReturned → falta índice)
  mongo_ms             executionTimeMillis reportado por Mongo
  roundtrip_ms         time.perf_counter() alrededor de list(find(...))
  transfer_ms          roundtrip - mongo (proxy de red + BSON decode)
  mb                   tamaño total del payload
  índice               winning plan (o COLLSCAN)

Ejecutar en el servidor (donde .env apunta a prod):

    cd /root/TradingAV && venv/bin/python -m scripts.perf_profile
"""

import time

import bson

from core.mongo import get_mongo_client_read

# (label, db, coll, filtro, projection)
QUERIES = [
    ("aum._cargar_aum", "Valuaciones", "AuM", {},
        {"_id": 0, "id_cuenta": 1, "cuenta": 1, "unidad": 1, "tipoTitulo": 1,
         "cantidad": 1, "precio": 1, "valuacion": 1, "fecha_snapshot": 1}),
    ("aum._cargar_assets", "Valuaciones", "Assets", {},
        {"_id": 0, "unidad": 1, "CARTERA": 1, "EMISOR": 1, "TICKER": 1,
         "CLASE_ACTIVO": 1, "CALIFICACION": 1, "VENCIMIENTO": 1}),
    ("aum._cargar_curvas_tasa_fija", "Trading", "Curvas", {"curva": "tasa_fija"},
        {"_id": 0, "ticker_corto": 1, "fecha_vencimiento": 1, "flujo_vencimiento": 1}),
    ("aum._cargar_curvas_cer", "Trading", "Curvas", {"curva": "cer"},
        {"_id": 0, "ticker_corto": 1, "fecha_vencimiento": 1}),
]


def _find_index(node):
    """Busca recursivamente el indexName o COLLSCAN en el winning plan."""
    if isinstance(node, dict):
        if "indexName" in node:
            return node["indexName"]
        if node.get("stage") == "COLLSCAN":
            return "COLLSCAN"
        for v in node.values():
            r = _find_index(v)
            if r:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _find_index(v)
            if r:
                return r
    return None


def profile(client, db_name, coll_name, filtro, proj):
    col = client[db_name][coll_name]

    try:
        explain = col.find(filtro, proj).explain("executionStats")
        stats = explain.get("executionStats", {})
        plan = explain.get("queryPlanner", {}).get("winningPlan", {})
        idx = _find_index(plan) or "?"
        mongo_ms = stats.get("executionTimeMillis", 0)
        n_returned = stats.get("nReturned", 0)
        n_examined = stats.get("totalDocsExamined", 0)
    except Exception as e:
        idx = f"explain-error: {type(e).__name__}"
        mongo_ms = 0
        n_returned = 0
        n_examined = 0

    t0 = time.perf_counter()
    docs = list(col.find(filtro, proj))
    rt_ms = (time.perf_counter() - t0) * 1000

    try:
        mb = sum(len(bson.encode(d)) for d in docs) / (1024 * 1024)
    except Exception:
        mb = 0.0

    return {
        "nReturned": n_returned or len(docs),
        "totalDocsExamined": n_examined,
        "mongo_ms": mongo_ms,
        "roundtrip_ms": rt_ms,
        "transfer_ms": max(rt_ms - mongo_ms, 0),
        "mb": mb,
        "index": idx,
    }


def main():
    client = get_mongo_client_read()
    rows = []
    for label, db_name, coll, filtro, proj in QUERIES:
        print(f"→ {label} ...", flush=True)
        r = profile(client, db_name, coll, filtro, proj)
        r["label"] = label
        rows.append(r)

    rows.sort(key=lambda r: r["roundtrip_ms"], reverse=True)

    print()
    hdr = f"{'QUERY':<32} {'DOCS':>9} {'EXAM':>9} {'MONGO':>9} {'RED':>9} {'TOTAL':>9} {'MB':>7}  ÍNDICE"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        ratio = r["totalDocsExamined"] / max(r["nReturned"], 1)
        flag = " ⚠" if ratio > 10 or r["index"] == "COLLSCAN" else ""
        print(f"{r['label']:<32} "
              f"{r['nReturned']:>9,} "
              f"{r['totalDocsExamined']:>9,} "
              f"{r['mongo_ms']:>7,}ms "
              f"{r['transfer_ms']:>7,.0f}ms "
              f"{r['roundtrip_ms']:>7,.0f}ms "
              f"{r['mb']:>6.1f}M  "
              f"{r['index']}{flag}")

    print()
    print("Lectura: ⚠ = COLLSCAN o examined/returned > 10×  → candidato a index/projection.")


if __name__ == "__main__":
    main()
