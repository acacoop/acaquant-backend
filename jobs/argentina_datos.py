"""Cron: pega argentinadatos.com y persiste riesgo país / IPC / REM en Mongo.

Escribe 3 series simples + REM estructurado:

    Trading.RiesgoPais           ← /v1/finanzas/indices/riesgo-pais
    Trading.InflacionMensual     ← /v1/finanzas/indices/inflacion
    Trading.InflacionInteranual  ← /v1/finanzas/indices/inflacionInteranual
    Trading.REM                  ← /v1/rems/{...} (expectativas de mercado BCRA)

Las 3 series de índices usan shape estándar `{fecha: 'YYYY-MM-DD', valor: float}`
que consume `services/macro._fetch_serie_macro`. El REM tiene shape propia (1
doc por (informe, indicador, periodo, periodoTipo)) — ver _persistir_rem.

Cadencia: diaria. Riesgo país cambia intradía pero con 1 update al cierre
alcanza para la foto. El REM sale ~1× al mes (15 del mes aprox) pero correr
diario es barato y permite que un informe con corrección tardía se tome.

Uso:
    python -m jobs.argentina_datos                # todo
    python -m jobs.argentina_datos --only riesgo  # solo una
    python -m jobs.argentina_datos --only rem     # solo REM
"""
from __future__ import annotations

import argparse
import logging
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("argentina_datos")

# Sanity por serie — si algún punto viene fuera de rango lo descartamos.
# Misma filosofía que jobs/dolar_api: defensa contra agregador corrompido
# o cambio de schema.
SANITY = {
    "RiesgoPais":          (50,  15_000),   # bps; típico 500-3000, tope amplio
    "InflacionMensual":    (-10, 100),      # %, permite deflación leve
    "InflacionInteranual": (-50, 1_000),    # %; 2024 pasó por 300%
}


def _persistir_serie(
    coll, datos: list[dict], sanity_range: tuple[float, float], nombre: str,
) -> dict[str, Any]:
    """Escribe la serie como upsert por fecha. Devuelve stats del run."""
    lo, hi = sanity_range
    ops = []
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
        ops.append(UpdateOne(
            {"fecha": fecha[:10]},
            {"$set": {
                "fecha":      fecha[:10],
                "valor":      v,
                "fuente":     "argentinadatos.com",
                "updated_at": datetime.now(UTC),
            }},
            upsert=True,
        ))

    if not ops:
        return {"persistidos": 0, "descartados": descartados}

    coll.bulk_write(ops, ordered=False)
    return {"persistidos": len(ops), "descartados": descartados}


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
    """Upsert de una lista de registros REM en `Trading.REM`.

    Clave única: (informe, indicador, periodo, periodoTipo). Un mismo
    indicador puede proyectar mensual y anual para el mismo periodo, por
    eso la clave incluye el tipo. Si el informe ya existía, los cambios
    tardíos se pisan.
    """
    ops = []
    descartados = 0
    for r in items:
        informe = r.get("informe")
        indicador = r.get("indicador")
        periodo = r.get("periodo")
        periodo_tipo = r.get("periodoTipo")
        if not (informe and indicador and periodo and periodo_tipo):
            descartados += 1
            continue
        informe_key = str(informe)[:7]  # normaliza a 'YYYY-MM'
        doc = {
            "informe":        informe_key,
            "fecha":          r.get("fecha"),
            "muestra":        r.get("muestra"),
            "indicador":      indicador,
            "periodo":        periodo,
            "periodo_tipo":   periodo_tipo,
            "periodo_desde":  r.get("periodoDesde"),
            "periodo_hasta":  r.get("periodoHasta"),
            "unidad":         r.get("unidad"),
            "mediana":        r.get("mediana"),
            "promedio":       r.get("promedio"),
            "desvio":         r.get("desvio"),
            "maximo":         r.get("maximo"),
            "minimo":         r.get("minimo"),
            "p10":            r.get("percentil10"),
            "p25":            r.get("percentil25"),
            "p75":            r.get("percentil75"),
            "p90":            r.get("percentil90"),
            "participantes":  r.get("participantes"),
            "fuente":         r.get("fuente") or "BCRA",
            "publicacion_url": r.get("publicacionUrl"),
            "xlsx_url":       r.get("xlsxUrl"),
            "updated_at":     datetime.now(UTC),
        }
        ops.append(UpdateOne(
            {
                "informe":      informe_key,
                "indicador":    indicador,
                "periodo":      periodo,
                "periodo_tipo": periodo_tipo,
            },
            {"$set": doc},
            upsert=True,
        ))
    if not ops:
        return {"persistidos": 0, "descartados": descartados}
    coll.bulk_write(ops, ordered=False)
    return {"persistidos": len(ops), "descartados": descartados}


def _ingestar_rem(db) -> dict[str, Any]:
    """Baja informes REM faltantes + re-baja siempre el último.

    El último puede tener correcciones tardías. Los anteriores son estables,
    así que si ya están en Mongo los saltamos para no pegarle a la API por
    nada.
    """
    coll = db["REM"]

    # Índices (idempotente).
    coll.create_index(
        [
            ("informe",       ASCENDING),
            ("indicador",     ASCENDING),
            ("periodo",       ASCENDING),
            ("periodo_tipo",  ASCENDING),
        ],
        unique=True,
        name="informe_indicador_periodo_tipo_uniq",
    )
    coll.create_index([("indicador", ASCENDING), ("periodo", ASCENDING)],
                      name="indicador_periodo")

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


def run(solo: str | None = None) -> dict[str, Any]:
    """Fetchea y persiste las series. `solo` = 'riesgo'|'ipc'|'ipcy'|'rem'|None."""
    client = get_mongo_client()
    db = client["Trading"]

    resultado: dict[str, Any] = {"ok": True, "series": {}}

    # Riesgo país
    if solo in (None, "riesgo"):
        try:
            serie = get_riesgo_pais_serie()
            stats = _persistir_serie(db["RiesgoPais"], serie, SANITY["RiesgoPais"], "RiesgoPais")
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
                db["InflacionMensual"], serie, SANITY["InflacionMensual"], "InflacionMensual",
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
                db["InflacionInteranual"], serie,
                SANITY["InflacionInteranual"], "InflacionInteranual",
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
            stats = _ingestar_rem(db)
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
    args = parser.parse_args()

    with JobRunLogger("argentina_datos") as jr:
        res = run(solo=args.only)
        jr.stats.update(res)
        if not res.get("ok"):
            jr.error("argentina_datos: al menos una serie falló")

    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
