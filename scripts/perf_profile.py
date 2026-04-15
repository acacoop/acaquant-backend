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
from datetime import datetime, timedelta

import bson

from core.mongo import get_mongo_client_read


def _aum_queries(client):
    queries = []

    last = client["Valuaciones"]["AuM"].find_one(
        {}, {"fecha_snapshot": 1, "_id": 0},
        sort=[("fecha_snapshot", -1)],
    )
    fecha_ultimo = last["fecha_snapshot"] if last else None

    fci_unidades = [
        a["unidad"]
        for a in client["Valuaciones"]["Assets"].find(
            {"CARTERA": "CARTERA FCI"}, {"unidad": 1, "_id": 0}
        )
        if a.get("unidad")
    ]

    queries.append(("aum._cargar_aum_ultimo", "Valuaciones", "AuM", "find", {
        "filter": {"fecha_snapshot": fecha_ultimo} if fecha_ultimo else {},
        "projection": {"_id": 0, "id_cuenta": 1, "cuenta": 1, "unidad": 1,
                       "cantidad": 1, "valuacion": 1, "fecha_snapshot": 1},
    }))

    queries.append(("aum._cargar_aum_fci_agg", "Valuaciones", "AuM", "agg", {
        "pipeline": [
            {"$match": {"unidad": {"$in": fci_unidades}} if fci_unidades else {"unidad": {"$in": []}}},
            {"$group": {
                "_id":       {"fecha": "$fecha_snapshot", "unidad": "$unidad"},
                "valuacion": {"$sum": "$valuacion"},
            }},
            {"$project": {
                "_id":            0,
                "fecha_snapshot": "$_id.fecha",
                "unidad":         "$_id.unidad",
                "valuacion":      "$valuacion",
            }},
        ],
    }))

    queries.append(("aum._cargar_aum_fci_snapshot", "Valuaciones", "AuM", "find", {
        "filter": {"unidad": {"$in": fci_unidades}, "fecha_snapshot": fecha_ultimo}
                  if (fci_unidades and fecha_ultimo) else {"unidad": {"$in": []}},
        "projection": {"_id": 0, "cuenta": 1, "unidad": 1, "valuacion": 1, "fecha_snapshot": 1},
    }))

    queries.append(("aum._cargar_assets", "Valuaciones", "Assets", "find", {
        "filter": {},
        "projection": {"_id": 0, "unidad": 1, "CARTERA": 1, "EMISOR": 1, "TICKER": 1,
                       "CLASE_ACTIVO": 1, "CALIFICACION": 1, "VENCIMIENTO": 1},
    }))

    queries.append(("aum._cargar_curvas_tasa_fija", "Trading", "Curvas", "find", {
        "filter": {"curva": "tasa_fija"},
        "projection": {"_id": 0, "ticker_corto": 1, "fecha_vencimiento": 1, "flujo_vencimiento": 1},
    }))

    queries.append(("aum._cargar_curvas_cer", "Trading", "Curvas", "find", {
        "filter": {"curva": "cer"},
        "projection": {"_id": 0, "ticker_corto": 1, "fecha_vencimiento": 1},
    }))

    return queries


def _mercado_queries(client):
    queries = []

    queries.append(("mercado._cargar_breakevens_historico", "Trading", "BreakevensHistorico", "find", {
        "filter": {},
        "projection": {"fecha": 1, "pares": 1, "_id": 0},
    }))

    queries.append(("mercado._cargar_forwards_historico(tasa_fija)", "Trading", "ForwardsHistorico", "find", {
        "filter": {"curva": "tasa_fija"},
        "projection": {"fecha": 1, "matrix": 1, "_id": 0},
    }))

    tickers_curvas = [d["ticker"] for d in client["Trading"]["Curvas"].find({}, {"ticker": 1, "_id": 0})]
    fecha_min_5d = datetime.utcnow() - timedelta(days=5)
    queries.append(("mercado._cargar_volumenes_diarios", "Trading", "TimeSales", "agg", {
        "pipeline": [
            {"$match": {"ticker": {"$in": tickers_curvas},
                        "money": {"$gt": 0},
                        "timestamp": {"$gte": fecha_min_5d}}},
            {"$group": {
                "_id": {
                    "fecha":  {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
                    "ticker": "$ticker",
                },
                "money": {"$sum": "$money"},
            }},
        ],
    }))

    tickers_tf = [d["ticker"] for d in client["Trading"]["Curvas"].find({"curva": "tasa_fija"}, {"ticker": 1, "_id": 0})]
    queries.append(("mercado._cargar_precios_diarios_curva(tasa_fija)", "Trading", "TimeSales", "agg", {
        "pipeline": [
            {"$match": {"ticker": {"$in": tickers_tf}, "price": {"$gt": 0}}},
            {"$sort":  {"timestamp": -1}},
            {"$group": {
                "_id": {
                    "ticker": "$ticker",
                    "fecha":  {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
                },
                "price": {"$first": "$price"},
            }},
        ],
    }))

    return queries


def _opciones_queries(_client):
    queries = []
    fecha_min = datetime.utcnow() - timedelta(days=20)
    queries.append(("opciones._fetch_vol_historico", "Opciones", "Data", "agg", {
        "pipeline": [
            {"$match": {"timestamp": {"$gte": fecha_min}, "ev": {"$gt": 0},
                        "strike": {"$exists": True}, "tipo": {"$exists": True}}},
            {"$group": {
                "_id": {"y": {"$year": "$timestamp"}, "m": {"$month": "$timestamp"},
                        "d": {"$dayOfMonth": "$timestamp"}, "s": "$symbol"},
                "ev":     {"$max":   "$ev"},
                "strike": {"$first": "$strike"},
                "tipo":   {"$first": "$tipo"},
            }},
            {"$group": {
                "_id": {"y": "$_id.y", "m": "$_id.m", "d": "$_id.d",
                        "strike": "$strike", "tipo": "$tipo"},
                "ev_total": {"$sum": "$ev"},
            }},
        ],
    }))
    return queries


def _operaciones_queries(client):
    queries = []

    queries.append(("operaciones._cargar_movimientos", "CashFlow", "Movimientos", "find", {
        "filter": {},
        "projection": {"_id": 0, "fecha": 1, "total": 1, "unidad": 1,
                       "informacion": 1, "cuenta": 1},
    }))

    queries.append(("operaciones._cargar_contrapartes.Flujo", "CashFlow", "Flujo", "find", {
        "filter": {},
        "projection": {"_id": 0, "bruto": 1, "concertacion": 1, "contraparte": 1,
                       "moneda": 1, "tipoOperacion": 1},
    }))

    # _cargar_fondos_flujo_aum: encadenado
    fondos = [d["contraparte"] for d in client["CashFlow"]["Contrapartes"].find(
        {"segmento": "Fondos"}, {"_id": 0, "contraparte": 1}
    )]
    queries.append(("operaciones.fondos_flujo_aum.Flujo", "CashFlow", "Flujo", "find", {
        "filter": {"contraparte": {"$in": fondos}, "moneda": "ARS"} if fondos else {"contraparte": {"$in": []}},
        "projection": {"_id": 0, "contraparte": 1, "concertacion": 1, "bruto": 1},
    }))
    queries.append(("operaciones.fondos_flujo_aum.Assets", "Valuaciones", "Assets", "find", {
        "filter": {"EMISOR": {"$in": fondos}, "CARTERA": "CARTERA FCI"} if fondos else {"EMISOR": {"$in": []}},
        "projection": {"_id": 0, "unidad": 1, "EMISOR": 1},
    }))
    fondo_unidades = []
    if fondos:
        fondo_unidades = [a["unidad"] for a in client["Valuaciones"]["Assets"].find(
            {"EMISOR": {"$in": fondos}, "CARTERA": "CARTERA FCI"}, {"unidad": 1, "_id": 0}
        ) if a.get("unidad")]
    queries.append(("operaciones.fondos_flujo_aum.AuM", "Valuaciones", "AuM", "agg", {
        "pipeline": [
            {"$match":   {"unidad": {"$in": fondo_unidades}} if fondo_unidades
                         else {"unidad": {"$in": []}}},
            {"$group":   {"_id": {"u": "$unidad", "f": "$fecha_snapshot"},
                          "valuacion": {"$sum": "$valuacion"}}},
            {"$project": {"_id": 0, "unidad": "$_id.u",
                          "fecha_snapshot": "$_id.f", "valuacion": 1}},
        ],
    }))

    return queries


def _portfolios_queries(_client):
    queries = []

    queries.append(("portfolios._get_carteras_df.Carteras", "Valuaciones", "Carteras", "find", {
        "filter": {},
        "projection": {"_id": 0},
    }))
    queries.append(("portfolios._get_carteras_ii_df.CarterasII", "Valuaciones", "CarterasII", "find", {
        "filter": {},
        "projection": {"_id": 0},
    }))

    return queries


def _build_queries(client):
    """Cada entry: (label, db_name, coll_name, kind, spec).

    kind = "find"  → spec = {"filter": ..., "projection": ...}
    kind = "agg"   → spec = {"pipeline": [...]}
    """
    queries = []
    queries.extend(_aum_queries(client))
    queries.extend(_mercado_queries(client))
    queries.extend(_opciones_queries(client))
    queries.extend(_operaciones_queries(client))
    queries.extend(_portfolios_queries(client))
    return queries


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


def _profile_find(client, db_name, coll_name, filtro, proj):
    col = client[db_name][coll_name]
    try:
        explain = client[db_name].command(
            "explain",
            {"find": coll_name, "filter": filtro, "projection": proj},
            verbosity="executionStats",
        )
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
    return docs, idx, mongo_ms, n_returned, n_examined, rt_ms


def _profile_agg(client, db_name, coll_name, pipeline):
    col = client[db_name][coll_name]
    try:
        explain = client[db_name].command(
            "explain",
            {"aggregate": coll_name, "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats",
        )
        idx = "?"
        mongo_ms = 0
        n_examined = 0
        stages = explain.get("stages") or []
        if stages:
            for stage in stages:
                qp = stage.get("$cursor", {}).get("queryPlanner", {})
                es = stage.get("$cursor", {}).get("executionStats", {})
                if qp:
                    idx = _find_index(qp.get("winningPlan", {})) or idx
                if es:
                    mongo_ms = max(mongo_ms, es.get("executionTimeMillis", 0))
                    n_examined = max(n_examined, es.get("totalDocsExamined", 0))
        else:
            qp = explain.get("queryPlanner", {})
            es = explain.get("executionStats", {})
            idx = _find_index(qp.get("winningPlan", {})) or idx
            mongo_ms = es.get("executionTimeMillis", 0)
            n_examined = es.get("totalDocsExamined", 0)
    except Exception as e:
        idx = f"explain-error: {type(e).__name__}"
        mongo_ms = 0
        n_examined = 0

    t0 = time.perf_counter()
    docs = list(col.aggregate(pipeline))
    rt_ms = (time.perf_counter() - t0) * 1000
    return docs, idx, mongo_ms, len(docs), n_examined, rt_ms


def profile(client, db_name, coll_name, kind, spec):
    if kind == "agg":
        docs, idx, mongo_ms, n_returned, n_examined, rt_ms = _profile_agg(
            client, db_name, coll_name, spec["pipeline"]
        )
    else:
        docs, idx, mongo_ms, n_returned, n_examined, rt_ms = _profile_find(
            client, db_name, coll_name, spec["filter"], spec["projection"]
        )

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
    for label, db_name, coll, kind, spec in _build_queries(client):
        print(f"→ {label} ...", flush=True)
        r = profile(client, db_name, coll, kind, spec)
        r["label"] = label
        rows.append(r)

    rows.sort(key=lambda r: r["roundtrip_ms"], reverse=True)

    print()
    hdr = f"{'QUERY':<48} {'DOCS':>9} {'EXAM':>9} {'MONGO':>9} {'RED':>9} {'TOTAL':>9} {'MB':>7}  ÍNDICE"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        ratio = r["totalDocsExamined"] / max(r["nReturned"], 1)
        flag = " ⚠" if ratio > 10 or r["index"] == "COLLSCAN" else ""
        print(f"{r['label']:<48} "
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
