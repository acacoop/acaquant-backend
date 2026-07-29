"""api/services/estrategia.py — lecturas de la vista ESTRATEGIA (TRADING).

Doc vivo: docs/ESTRATEGIA_QUANT.md. Service PURO (sin FastAPI): agrega el
ledger (estrategia.senales + estrategia.resultados) y la evaluación live que
escribe engines/estrategia.py. Acá NO se calcula ninguna señal — el único
emisor es el engine (si la API recalculara, habría dos verdades).

  - get_live()          → zona LIVE: última evaluación por ticker.
  - get_track_record()  → zona TRACK-RECORD: hit-rate, expectativa, edge por
                          factor y curva de equity por horizonte.
  - get_senales()       → tabla de señales resueltas (auditoría fila a fila).
"""
from __future__ import annotations

from api.cache import cached
from core import estrategia_sql as db


@cached(ttl=10)
def get_live() -> list[dict]:
    """Última evaluación por ticker (upsert del engine cada ~60s), ordenada por
    |score| desc — lo más accionable arriba. Shape por fila:
    {ticker, ts, score, direccion, cobertura, factores, indice_ref, precio,
     pesos_version, inputs, eval_at}."""
    filas = db.evals_live()
    filas.sort(key=lambda r: abs(r.get("score") or 0), reverse=True)
    return filas


@cached(ttl=30)
def get_senales(dias: int = 30, limite: int = 200) -> list[dict]:
    """Señales con resultado (una fila por señal×horizonte), desc por ts."""
    return db.senales_resueltas(dias=dias, limite=limite)


# Muestra mínima para creer un stat (menos que esto es ruido, se devuelve igual
# pero el frontend lo marca como "muestra chica").
_N_MINIMO = 30


@cached(ttl=60)
def get_track_record(dias: int = 90) -> dict:
    """Evaluación del modelo sobre las señales RESUELTAS (no parciales para los
    stats; las parciales cuentan solo en `n_parciales`).

    Por horizonte: n, hit_rate, expectativa (ret promedio direccional),
    mfe/mae promedio, curva de equity (cumsum de ret_pct por ts asc).
    Edge por factor: para cada factor, expectativa cuando el factor empujaba
    EN la dirección emitida vs cuando empujaba EN CONTRA — si el "a favor"
    no supera al "en contra", el factor no está prediciendo nada.
    """
    filas = db.senales_resueltas(dias=dias, limite=5000)
    resueltas = [f for f in filas if f.get("ret_pct") is not None and not f.get("parcial")]
    parciales = [f for f in filas if f.get("parcial")]

    por_h: dict[int, dict] = {}
    horizontes = sorted({f["horizonte_min"] for f in resueltas})
    for h in horizontes:
        fs = sorted((f for f in resueltas if f["horizonte_min"] == h),
                    key=lambda f: f["ts"] or "")
        rets = [f["ret_pct"] for f in fs]
        ganadas = sum(1 for f in fs if f.get("gano"))
        equity: list[dict] = []
        acum = 0.0
        for f in fs:
            acum += f["ret_pct"]
            equity.append({"ts": f["ts"], "acum_pct": round(acum, 3)})
        por_h[h] = {
            "n": len(fs),
            "hit_rate": round(ganadas / len(fs) * 100, 1) if fs else None,
            "expectativa_pct": round(sum(rets) / len(rets), 4) if rets else None,
            "mfe_prom": round(sum(f["mfe_pct"] or 0 for f in fs) / len(fs), 3) if fs else None,
            "mae_prom": round(sum(f["mae_pct"] or 0 for f in fs) / len(fs), 3) if fs else None,
            "equity": equity,
        }

    # Edge por factor (sobre el horizonte medio si existe, si no el primero).
    h_ref = 30 if 30 in por_h else (horizontes[0] if horizontes else None)
    edge_factores: dict[str, dict] = {}
    if h_ref is not None:
        fs = [f for f in resueltas if f["horizonte_min"] == h_ref]
        for nombre in ("recorrido_indice", "alineacion", "nafta_papel", "confluencia"):
            a_favor = []
            en_contra = []
            for f in fs:
                v = (f.get("factores") or {}).get(nombre)
                if v is None:
                    continue
                # signo del factor vs dirección emitida (LONG=+, SHORT=−)
                dir_signo = 1 if f["direccion"] == "LONG" else -1
                (a_favor if v * dir_signo > 0 else en_contra).append(f["ret_pct"])
            edge_factores[nombre] = {
                "n_favor": len(a_favor),
                "n_contra": len(en_contra),
                "exp_favor": round(sum(a_favor) / len(a_favor), 4) if a_favor else None,
                "exp_contra": round(sum(en_contra) / len(en_contra), 4) if en_contra else None,
            }

    return {
        "dias": dias,
        "n_total": len(resueltas),
        "n_parciales": len(parciales),
        "muestra_suficiente": len(resueltas) >= _N_MINIMO,
        "n_minimo": _N_MINIMO,
        "horizontes": por_h,
        "edge_factores": edge_factores,
        "horizonte_edge": h_ref,
    }
