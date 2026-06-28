"""Cron: pega argentinadatos.com y persiste riesgo país / IPC / REM en Mongo.

Escribe 3 series simples + REM estructurado:

    Trading.RiesgoPais           ← /v1/finanzas/indices/riesgo-pais
    Trading.InflacionMensual     ← /v1/finanzas/indices/inflacion
    Trading.InflacionInteranual  ← /v1/finanzas/indices/inflacionInteranual
    Trading.REM                  ← /v1/rems/{...} (IPC INDEC esperado por el REM)

Las 3 series de índices usan shape estándar `{fecha: 'YYYY-MM-DD', valor: float}`.

El REM se filtra en la ingesta a UN SOLO indicador (IPC nivel general INDEC)
y se guarda con shape chica: solo estadísticos de consenso + periodo YYYY-MM
normalizado. Clave única: (informe, periodo, periodo_tipo).

Cadencia: diaria. Riesgo país cambia intradía pero con 1 update al cierre
alcanza para la foto. El REM sale ~1× al mes (15 del mes aprox) pero correr
diario es barato y permite que un informe con corrección tardía se tome.

Uso:
    python -m jobs.argentina_datos                # todo
    python -m jobs.argentina_datos --only riesgo  # solo una
    python -m jobs.argentina_datos --only rem     # solo REM
    python -m jobs.argentina_datos --only rem --reset   # drop + re-ingest REM
"""
from __future__ import annotations

import argparse
import logging
import re
from datetime import UTC, datetime
from typing import Any

from pymongo import ASCENDING, UpdateOne

from core.argentina_datos import (
    ArgDataError,
    get_inflacion_interanual,
    get_inflacion_mensual,
    get_rems_mes,
    get_rems_meses,
    get_rems_ultimo,
    get_riesgo_pais_serie,
)
from core.job_runs import JobRunLogger
from core.mongo import get_mongo_client
from core.pg_mirror import mirror_job, write_native

# Indicador REM único que nos interesa. argentinadatos.com lo devuelve con
# este label literal (verificado en /rem/debug: abril 2026).
_INDICADOR_IPC_INDEC = "Precios minoristas (IPC nivel general-Nacional; INDEC)"

# Meses que difieren entre es/en. Los otros (feb/mar/may/jun/jul/sep/oct/nov)
# coinciden letra por letra. argentinadatos devuelve 'Apr-26', 'Aug-26', etc.
_MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
    "jan": 1, "apr": 4, "aug": 8, "dec": 12,
}


def _periodo_a_yyyymm(raw) -> str | None:
    """Normaliza cualquier formato razonable de período a 'YYYY-MM'.
    Devuelve None si no parsea."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{1,2})", s)
    if m:
        y, mm = int(m.group(1)), int(m.group(2))
        if 1 <= mm <= 12:
            return f"{y:04d}-{mm:02d}"
    m = re.match(r"^(\d{1,2})[-/](\d{4})$", s)
    if m:
        mm, y = int(m.group(1)), int(m.group(2))
        if 1 <= mm <= 12:
            return f"{y:04d}-{mm:02d}"
    m = re.match(r"^([a-záéíóú]{3,})[\s.\-/]+(\d{2,4})$", s)
    if m:
        mes_name = m.group(1)[:3]
        y_raw = int(m.group(2))
        y = 2000 + y_raw if y_raw < 100 else y_raw
        mm = _MESES.get(mes_name)
        if mm:
            return f"{y:04d}-{mm:02d}"
    return None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("argentina_datos")

# Sanity por serie — si algún punto viene fuera de rango lo descartamos.
# Defensa contra agregador corrompido o cambio de schema.
SANITY = {
    "RiesgoPais":          (50,  15_000),   # bps; típico 500-3000, tope amplio
    "InflacionMensual":    (-10, 100),      # %, permite deflación leve
    "InflacionInteranual": (-50, 1_000),    # %; 2024 pasó por 300%
}


def _persistir_serie(
    datos: list[dict], sanity_range: tuple[float, float], nombre: str,
) -> dict[str, Any]:
    """Escribe la serie SQL-ONLY (macro.series_macro) por (serie, fecha). `nombre`
    == clave `serie`. Ya NO escribe Trading.<serie> en Mongo."""
    from datetime import date as _date
    lo, hi = sanity_range
    pg_rows = []
    descartados = 0
    for d in datos:
        fecha = d.get("fecha")
        valor = d.get("valor")
        if not fecha or valor is None:
            continue
        try:
            v = float(valor)
        except (TypeError, ValueError):
            continue
        if not (lo <= v <= hi):
            logger.warning(
                "[%s] %s=%s fuera de [%s, %s] — descartado", nombre, fecha, v, lo, hi,
            )
            descartados += 1
            continue
        try:
            pg_rows.append({"serie": nombre, "fecha": _date.fromisoformat(fecha[:10]),
                            "valor": v})
        except ValueError:
            pass

    if not pg_rows:
        return {"persistidos": 0, "descartados": descartados}

    n = write_native("macro.series_macro", ["serie", "fecha"], pg_rows)
    if not n:
        logger.warning("[%s] write_native devolvió 0 — revisar Postgres", nombre)
    return {"persistidos": n, "descartados": descartados}


def _parse_informe(path_o_item: str | dict) -> str | None:
    """Extrae 'YYYY-MM' de un path '/rems/YYYY/MM' o de un item del REM."""
    if isinstance(path_o_item, dict):
        v = path_o_item.get("informe")
        return str(v)[:7] if v else None
    if not isinstance(path_o_item, str):
        return None
    partes = [p for p in path_o_item.split("/") if p]
    # formato esperado: rems/YYYY/MM
    if len(partes) >= 3 and partes[-2].isdigit() and partes[-1].isdigit():
        return f"{partes[-2]}-{partes[-1].zfill(2)}"
    return None


def _persistir_rem(coll, items: list[dict]) -> dict[str, Any]:
    """Upsert de registros REM en `Trading.REM`, filtrados a UN indicador
    (_INDICADOR_IPC_INDEC) y con schema mínimo.

    Clave única: (informe, periodo, periodo_tipo). El periodo se normaliza
    a 'YYYY-MM' para poder ordenar/comparar lexicográficamente sin parsing
    en cada query.
    """
    from datetime import date as _date
    ops = []
    pg_rows = []
    descartados_indicador = 0
    descartados_periodo = 0
    for r in items:
        if r.get("indicador") != _INDICADOR_IPC_INDEC:
            descartados_indicador += 1
            continue

        informe_raw = r.get("informe")
        periodo_raw = r.get("periodo")
        periodo_tipo = r.get("periodoTipo")
        if not (informe_raw and periodo_raw and periodo_tipo):
            descartados_periodo += 1
            continue

        periodo_yyyymm = _periodo_a_yyyymm(periodo_raw)
        if periodo_yyyymm is None:
            descartados_periodo += 1
            continue

        informe_key = str(informe_raw)[:7]
        doc = {
            "informe":       informe_key,
            "periodo":       periodo_yyyymm,
            "periodo_tipo":  periodo_tipo,
            "fecha_informe": r.get("fecha"),
            "mediana":       r.get("mediana"),
            "promedio":      r.get("promedio"),
            "desvio":        r.get("desvio"),
            "minimo":        r.get("minimo"),
            "maximo":        r.get("maximo"),
            "p10":           r.get("percentil10"),
            "p25":           r.get("percentil25"),
            "p75":           r.get("percentil75"),
            "p90":           r.get("percentil90"),
            "participantes": r.get("participantes"),
            "updated_at":    datetime.now(UTC),
        }
        ops.append(UpdateOne(
            {
                "informe":      informe_key,
                "periodo":      periodo_yyyymm,
                "periodo_tipo": periodo_tipo,
            },
            {"$set": doc},
            upsert=True,
        ))
        try:
            fi = _date.fromisoformat(str(doc["fecha_informe"])[:10]) \
                if doc.get("fecha_informe") else None
        except ValueError:
            fi = None
        pg_rows.append({
            "informe": informe_key, "periodo": periodo_yyyymm, "periodo_tipo": periodo_tipo,
            "fecha_informe": fi, "mediana": doc["mediana"], "promedio": doc["promedio"],
            "desvio": doc["desvio"], "minimo": doc["minimo"], "maximo": doc["maximo"],
            "p10": doc["p10"], "p25": doc["p25"], "p75": doc["p75"], "p90": doc["p90"],
            "participantes": doc["participantes"], "updated_at": doc["updated_at"],
        })
    if not ops:
        return {
            "persistidos":           0,
            "descartados_indicador": descartados_indicador,
            "descartados_periodo":   descartados_periodo,
        }
    coll.bulk_write(ops, ordered=False)
    # Dual-write a Postgres (flag MERCADO_SQL_WRITE, best-effort).
    mirror_job("rem", ["informe", "periodo", "periodo_tipo"], pg_rows)
    return {
        "persistidos":           len(ops),
        "descartados_indicador": descartados_indicador,
        "descartados_periodo":   descartados_periodo,
    }


def _ingestar_rem(db, reset: bool = False) -> dict[str, Any]:
    """Baja informes REM faltantes + re-baja siempre el último.

    `reset=True` dropea la colección antes de ingestar (para migrar schema
    sin dejar docs con shape vieja). Correr una vez tras deploy.
    """
    coll = db["REM"]

    if reset:
        coll.drop()
        logger.info("REM: colección dropeada (--reset)")

    # Índices del nuevo schema (sin campo indicador). Idempotente.
    coll.create_index(
        [("informe", ASCENDING), ("periodo", ASCENDING), ("periodo_tipo", ASCENDING)],
        unique=True,
        name="informe_periodo_tipo_uniq",
    )
    coll.create_index([("periodo", ASCENDING)], name="periodo")

    paths = get_rems_meses()
    disponibles = {_parse_informe(p) for p in paths}
    disponibles.discard(None)

    ya_cargados: set[str] = set(coll.distinct("informe"))

    nuevos = sorted(disponibles - ya_cargados)
    stats_por_informe: dict[str, Any] = {}
    total_persistidos = 0

    # Siempre re-baja el último (puede cambiar post-publicación).
    try:
        ultimos = get_rems_ultimo()
        informe_ult = _parse_informe(ultimos[0]) if ultimos else None
        if informe_ult:
            s = _persistir_rem(coll, ultimos)
            stats_por_informe[informe_ult] = s
            total_persistidos += s["persistidos"]
            # Lo consideramos ya tratado para no bajarlo 2 veces.
            nuevos = [n for n in nuevos if n != informe_ult]
    except ArgDataError as e:
        logger.warning("rems/ultimo falló: %s", e)

    for informe in nuevos:
        anio_str, mes_str = informe.split("-")
        try:
            items = get_rems_mes(int(anio_str), int(mes_str))
        except ArgDataError as e:
            logger.warning("rems/%s falló: %s", informe, e)
            continue
        s = _persistir_rem(coll, items)
        stats_por_informe[informe] = s
        total_persistidos += s["persistidos"]
        logger.info("REM %s: %s", informe, s)

    return {
        "total_persistidos": total_persistidos,
        "informes_tocados":  len(stats_por_informe),
        "por_informe":       stats_por_informe,
    }


def run(solo: str | None = None, reset_rem: bool = False) -> dict[str, Any]:
    """Fetchea y persiste las series. `solo` = 'riesgo'|'ipc'|'ipcy'|'rem'|None.
    `reset_rem=True` solo tiene efecto si corre REM — dropea la colección antes
    de ingestar."""
    client = get_mongo_client()
    db = client["Trading"]

    resultado: dict[str, Any] = {"ok": True, "series": {}}

    # Riesgo país
    if solo in (None, "riesgo"):
        try:
            serie = get_riesgo_pais_serie()
            stats = _persistir_serie(serie, SANITY["RiesgoPais"], "RiesgoPais")
            resultado["series"]["riesgo_pais"] = stats
            logger.info("RiesgoPais: %s", stats)
        except ArgDataError as e:
            resultado["ok"] = False
            resultado["series"]["riesgo_pais"] = {"error": str(e)}
            logger.error("RiesgoPais falló: %s", e)

    # Inflación mensual
    if solo in (None, "ipc"):
        try:
            serie = get_inflacion_mensual()
            stats = _persistir_serie(
                serie, SANITY["InflacionMensual"], "InflacionMensual",
            )
            resultado["series"]["inflacion_mensual"] = stats
            logger.info("InflacionMensual: %s", stats)
        except ArgDataError as e:
            resultado["ok"] = False
            resultado["series"]["inflacion_mensual"] = {"error": str(e)}
            logger.error("InflacionMensual falló: %s", e)

    # Inflación interanual
    if solo in (None, "ipcy"):
        try:
            serie = get_inflacion_interanual()
            stats = _persistir_serie(
                serie, SANITY["InflacionInteranual"], "InflacionInteranual",
            )
            resultado["series"]["inflacion_interanual"] = stats
            logger.info("InflacionInteranual: %s", stats)
        except ArgDataError as e:
            resultado["ok"] = False
            resultado["series"]["inflacion_interanual"] = {"error": str(e)}
            logger.error("InflacionInteranual falló: %s", e)

    # REM (estructurado, shape propia)
    if solo in (None, "rem"):
        try:
            stats = _ingestar_rem(db, reset=reset_rem)
            resultado["series"]["rem"] = stats
            logger.info("REM: %s", stats)
        except ArgDataError as e:
            resultado["ok"] = False
            resultado["series"]["rem"] = {"error": str(e)}
            logger.error("REM falló: %s", e)

    return resultado


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["riesgo", "ipc", "ipcy", "rem"])
    parser.add_argument("--reset", action="store_true",
                        help="Dropea Trading.REM antes de ingestar (migración de schema)")
    args = parser.parse_args()

    with JobRunLogger("argentina_datos") as jr:
        res = run(solo=args.only, reset_rem=args.reset)
        jr.stats.update(res)
        if not res.get("ok"):
            jr.error("argentina_datos: al menos una serie falló")

    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
