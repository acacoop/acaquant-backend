"""[DEPRECADO 2026-05-04] Cron: pega dolarapi.com y persiste oficial/mayorista/blue.

Apagado en deploy/crontab.txt (línea comentada). El live de oficial/mayorista
ya viene del feed MAE (`Valuaciones.DolarOficialLive`, script local en PC
oficina) y la serie histórica del oficial migró a `Trading.DOLAR` (BCRA
A3500). La serie blue quedó sin fuente.

El código se mantiene por si querés reactivar el cron (ej. para que `/argy`
vuelva a tener anchors 7d/MTD/YTD anclados a dolarapi). Para reactivarlo:
descomentar la línea en deploy/crontab.txt y reinstalar el crontab.

Escribe en `Valuaciones.DolarOficial` — 1 doc por (casa, fecha_snapshot).
El snapshot es diario (`$set` on-upsert por clave compuesta), así no
duplicamos docs intradía. Tambien guarda `last_update` con el timestamp
exacto del último fetch exitoso.

Schema del doc:
    {
        casa: "oficial" | "mayorista" | "blue",
        fecha: "YYYY-MM-DD",            # key lógica diaria
        compra: float,
        venta: float,
        valor: float,                    # alias = venta (usado por services)
        fechaActualizacion: datetime,    # del payload de dolarapi
        fuente: "dolarapi.com",
        updated_at: datetime             # cuando corrimos este job
    }

Uso:
    python -m jobs.dolar_api
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from pymongo import UpdateOne

from core.dolar_api import CASAS_SOPORTADAS, DolarApiError, get_soportadas
from core.job_runs import JobRunLogger
from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("dolar_api")

# Sanity check — si el agregador devuelve un valor fuera de este rango lo
# descartamos. Defensa contra respuesta corrompida, cambio de schema de
# dolarapi, o escenario improbable de compromiso del servicio. Rango
# ajustado a los niveles actuales del dólar AR (~1400) con un ceiling
# razonablemente estricto: detecta spikes con un 0 de más antes que
# contaminen los docs. Subir cuando los valores se acerquen al techo.
VALOR_MIN = 1000
VALOR_MAX = 2500


def _parse_fecha_act(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        # dolarapi manda ISO con 'Z' o con offset.
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("fechaActualizacion no parseable: %r", raw)
        return None


def run() -> dict:
    """Ejecuta el fetch y escribe los snapshots. Devuelve stats."""
    client = get_mongo_client()
    coll = client["Valuaciones"]["DolarOficial"]

    try:
        items = get_soportadas()
    except DolarApiError as e:
        logger.error("dolarapi falló: %s", e)
        return {"ok": False, "error": str(e), "escritos": 0}

    if not items:
        return {"ok": False, "error": "dolarapi devolvió 0 casas soportadas", "escritos": 0}

    now = datetime.now(UTC)
    fecha_key = now.date().isoformat()

    ops = []
    escritos_por_casa: dict[str, dict] = {}
    descartados: list[str] = []
    for it in items:
        casa = it.get("casa")
        compra = it.get("compra")
        venta = it.get("venta")
        if casa not in CASAS_SOPORTADAS or venta is None:
            continue

        # Sanity check — fuera del rango [VALOR_MIN, VALOR_MAX] asumimos
        # que el valor viene corrupto y lo descartamos sin persistir.
        venta_f = float(venta)
        if not (VALOR_MIN <= venta_f <= VALOR_MAX):
            msg = (
                f"Sanity fail {casa}: venta={venta_f} "
                f"fuera de [{VALOR_MIN}, {VALOR_MAX}]"
            )
            logger.error(msg)
            descartados.append(msg)
            continue

        doc = {
            "casa":               casa,
            "fecha":              fecha_key,
            "compra":             float(compra) if compra is not None else None,
            "venta":              float(venta),
            "valor":              float(venta),  # alias para consumers
            "fechaActualizacion": _parse_fecha_act(it.get("fechaActualizacion")),
            "fuente":             "dolarapi.com",
            "updated_at":         now,
        }
        ops.append(UpdateOne(
            {"casa": casa, "fecha": fecha_key},
            {"$set": doc},
            upsert=True,
        ))
        escritos_por_casa[casa] = {"compra": doc["compra"], "venta": doc["venta"]}

    if not ops:
        err = (
            "ningún doc válido tras sanity check: " + "; ".join(descartados)
            if descartados else "ningún doc válido"
        )
        return {
            "ok":          False,
            "error":       err,
            "escritos":    0,
            "descartados": descartados,
        }

    coll.bulk_write(ops, ordered=False)
    logger.info(
        "DolarOficial upsert OK — casas: %s",
        ", ".join(f"{k}={v['venta']}" for k, v in escritos_por_casa.items()),
    )
    return {
        "ok":          True,
        "escritos":    len(ops),
        "descartados": descartados,
        "por_casa":    escritos_por_casa,
        "fecha":       fecha_key,
    }


def main() -> int:
    with JobRunLogger("dolar_api") as jr:
        res = run()
        # Publicamos stats al logger; si hubo error lo marcamos como partial
        # vía .error() — el JobRunLogger calcula status automáticamente.
        jr.stats.update(res)
        if not res.get("ok"):
            jr.error(str(res.get("error") or "dolar_api job failed"))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
