"""negocio_movimientos.py — pega a Aunesa, consolida y persiste boletos
del día SQL-native en `operaciones.negocio_movimientos` (Postgres).

Decomiso Mongo: el writer NO escribe `CashFlow.NegocioMovimientos` (Mongo);
escribe directo a SQL (ON CONFLICT). El puente `jobs/sync_postgres.py::sync_negocio`
ya fue ELIMINADO. Idempotente por `(fecha, comprobante)`: si ya existe el boleto,
se actualiza; si es nuevo, inserta. No duplica.

Cron: una corrida por hora de 12 ART a 22 ART (15-22 UTC) L-V.
Ver deploy/crontab.txt.

Lookback: por default ingiere hoy + `_LOOKBACK_HABILES` días hábiles previos.
Las diferencias diarias de futuros llegan a Aunesa T+1/T+2 → ingerir SÓLO "hoy"
las perdía (corte del 2026-05-06). Idempotente: re-ingerir no duplica.

Uso:
    python -m jobs.negocio_movimientos              # hoy + últimos hábiles (lookback)
    python -m jobs.negocio_movimientos --fecha 2026-05-04
    python -m jobs.negocio_movimientos --desde 2026-05-07 --hasta 2026-07-25   # backfill
    python -m jobs.negocio_movimientos --fecha 2026-05-04 --dry

Schema doc en CashFlow.NegocioMovimientos:
{
  fecha          : "2026-05-04",            # ISO YYYY-MM-DD ART
  comprobante    : "BOL 2026069919",        # ID único del boleto en Aunesa
  cuenta         : "[805] MOLLO ...",
  id_cuenta      : "805",                    # derivado de cuenta (índice; vista COMERCIAL)
  categoria      : "compra",                 # 16 categorías posibles
  op             : "Compra",                 # Compra/Venta/Susc FCI/...
  ticker         : "AL30",                   # short ticker o null
  cantidad       : -1.00,                    # cuotapartes / VN, signo cliente
  precio         : 91410.00,                 # precio o tasa% (cauciones)
  importe        : 91410.00,                 # plata movida, signo cliente
  moneda         : "ARS",                    # ARS / USD / USDC / etc
  plazo          : "Inm",                    # CI / 24hs / Contado Inmediato / N días
  lugar          : "Local",                  # Local / CV / A3 / etc
  estado         : "DIS",                    # DIS / DIF / etc
  informacion    : "Compra [AL30] 1,00@...",
  n_lineas       : 2,                        # cantidad de líneas raw que conforman
  ingestado_en   : ISODate("2026-05-04T15:30:00Z")
}

Índice único: (fecha, comprobante).
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import UTC, date, datetime, timedelta

sys.path.insert(0, ".")

from api.services import aunesa_negocio as svc
from api.services._mep import get_mep_for_date
from core.postgres import get_job_pool

# Ventana de lookback (días hábiles hacia atrás, hoy incluido aparte). Las
# "Diferencias diarias" de futuros llegan a Aunesa T+1 (a veces T+2): cuando el
# cron corre el día T, todavía NO están → hay que re-ingerir los últimos días
# hábiles para capturarlas (idempotente por (fecha, comprobante), no duplica).
# Ver scripts/diag_diferencias_freshness.py: el corte del 2026-05-06 fue por
# ingerir SOLO "hoy". También recupera correcciones tardías de cualquier boleto.
_LOOKBACK_HABILES = 2

# `cuenta` viene "[805] NOMBRE" → id de la cuenta comitente. Denormalizado en
# el doc para que las queries por cuenta usen índice (en vez de regex). Lo
# consume la vista COMERCIAL (api/services/comercial.py).
_RE_ID_CUENTA = re.compile(r"^\[(\d+)\]")

# ANULACIONES (2026-08-04). Aunesa carga un boleto mal, lo ANULA y emite uno
# corregido; el upsert no lo borraba nunca → quedaba inflando volumen para
# siempre (medido: 855 fantasmas YTD). Ahora, tras escribir, se MARCA lo que
# Aunesa dejó de devolver para esa fecha.
# El tope existe porque una respuesta PARCIAL de Aunesa (timeout a mitad de
# página, error transitorio) haría anular medio día de golpe. Si los candidatos
# superan el tope, no se anula NADA y se grita en el log.
_TOPE_ANULACION_PCT = 0.20
_TOPE_ANULACION_MIN = 5


def _extract_id_cuenta(cuenta: str | None) -> str | None:
    m = _RE_ID_CUENTA.match(cuenta or "")
    return m.group(1) if m else None


def _reconciliar(cur, fecha_iso: str, vivos: list[str], logger) -> int:
    """Marca `anulado_en` en los boletos de `fecha_iso` que Aunesa ya no devuelve.
    Devuelve cuántos marcó (0 si el tope de seguridad lo abortó)."""
    cur.execute(
        "SELECT count(*) AS total, "
        "       count(*) FILTER (WHERE anulado_en IS NULL "
        "                          AND comprobante <> ALL(%(vivos)s)) AS candidatos "
        "  FROM negocio_movimientos WHERE fecha = %(fecha)s",
        {"fecha": fecha_iso, "vivos": vivos})
    total, candidatos = cur.fetchone()
    if not candidatos:
        return 0

    tope = max(_TOPE_ANULACION_MIN, int(total * _TOPE_ANULACION_PCT))
    if candidatos > tope:
        logger.error(
            "ANULACIÓN ABORTADA en %s: %d candidatos sobre %d boletos (tope %d). "
            "Aunesa probablemente respondió parcial. NO se marcó nada — revisar a mano.",
            fecha_iso, candidatos, total, tope)
        return 0

    cur.execute(
        "UPDATE negocio_movimientos SET anulado_en = now() "
        " WHERE fecha = %(fecha)s AND anulado_en IS NULL "
        "   AND comprobante <> ALL(%(vivos)s)",
        {"fecha": fecha_iso, "vivos": vivos})
    logger.warning("Anulados %d boleto(s) en %s (Aunesa dejó de devolverlos).",
                   candidatos, fecha_iso)
    return candidatos




def _boleto_a_doc(b: dict, fecha_iso: str, ahora: datetime, mep: float | None) -> dict:
    """Convierte un boleto consolidado del service al doc de Mongo.
    Descarta `lineas` raw (audit puede agregarse después si hace falta).

    `mep` es el MEP del día (puede ser None si no hay cotización para esa
    fecha). Se guarda en cada boleto como snapshot inmutable — sirve para
    pesificar/dolarizar después sin volver a `Valuaciones.Dolar`.
    """
    return {
        "fecha":        fecha_iso,
        "comprobante":  b.get("comprobante"),
        "cuenta":       b.get("cuenta"),
        "id_cuenta":    _extract_id_cuenta(b.get("cuenta")),
        "categoria":    b.get("categoria"),
        "op":           b.get("op"),
        "ticker":       b.get("ticker"),
        "cantidad":     b.get("cantidad"),
        "precio":       b.get("precio"),
        "importe":      b.get("importe"),
        "moneda":       b.get("moneda"),
        "plazo":        b.get("plazo"),
        "lugar":        b.get("lugar"),
        "estado":       b.get("estado"),
        "informacion":  b.get("informacion"),
        "n_lineas":     b.get("n_lineas"),
        "mep":          mep,
        "ingestado_en": ahora,
    }


def run(fecha_d: date, dry: bool = False) -> dict:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logger = logging.getLogger("negocio_movimientos")

    fecha_iso = fecha_d.isoformat()
    logger.info("Iniciando ingesta NegocioMovimientos para %s ART", fecha_iso)

    # Pegar a Aunesa.
    consolidado = svc.fetch_y_consolidar(fecha=fecha_d)
    boletos = consolidado["boletos"]
    logger.info("Aunesa devolvió %d movimientos → %d boletos consolidados",
                consolidado["meta"]["total"], len(boletos))

    if not boletos:
        logger.info("Sin boletos para persistir.")
        return {"upsertados": 0, "matched": 0, "boletos": 0, "fecha": fecha_iso}

    # Filtrar boletos sin comprobante (no se pueden upsertar de manera estable).
    persistibles = [b for b in boletos if b.get("comprobante")]
    skipped = len(boletos) - len(persistibles)
    if skipped:
        logger.warning("Skipeados %d boletos sin comprobante.", skipped)

    if dry:
        logger.info("[DRY] No persiste. %d boletos persistirían.", len(persistibles))
        return {
            "upsertados": 0, "matched": 0,
            "boletos": len(persistibles), "skipped": skipped,
            "fecha": fecha_iso, "dry": True,
        }

    # MEP del día — una lookup, se reusa para todos los boletos de la fecha.
    mep = get_mep_for_date(fecha_iso)
    if mep is None:
        logger.warning("Sin MEP para %s — boletos quedarán con mep=null.", fecha_iso)

    ahora = datetime.now(UTC)
    docs = [_boleto_a_doc(b, fecha_iso, ahora, mep) for b in persistibles]

    # Escritura SQL operaciones.negocio_movimientos (upsert por fecha+comprobante).
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO negocio_movimientos "
            "(fecha, comprobante, id_cuenta, categoria, op, ticker, cantidad, precio, importe, "
            " moneda, mep, cuenta, plazo, lugar, estado, informacion, ingestado_en) "
            "VALUES (%(fecha)s, %(comprobante)s, %(id_cuenta)s, %(categoria)s, %(op)s, %(ticker)s, "
            " %(cantidad)s, %(precio)s, %(importe)s, %(moneda)s, %(mep)s, %(cuenta)s, %(plazo)s, "
            " %(lugar)s, %(estado)s, %(informacion)s, %(ingestado_en)s) "
            "ON CONFLICT (fecha, comprobante) DO UPDATE SET "
            "id_cuenta=EXCLUDED.id_cuenta, categoria=EXCLUDED.categoria, op=EXCLUDED.op, "
            "ticker=EXCLUDED.ticker, cantidad=EXCLUDED.cantidad, precio=EXCLUDED.precio, "
            "importe=EXCLUDED.importe, moneda=EXCLUDED.moneda, mep=EXCLUDED.mep, "
            "cuenta=EXCLUDED.cuenta, plazo=EXCLUDED.plazo, lugar=EXCLUDED.lugar, "
            "estado=EXCLUDED.estado, informacion=EXCLUDED.informacion, "
            "ingestado_en=EXCLUDED.ingestado_en, anulado_en=NULL",
            docs)
        n = len(docs)
        anulados = _reconciliar(cur, fecha_iso, [d["comprobante"] for d in docs], logger)
        conn.commit()
    logger.info("SQL upsert OK: %d boletos → operaciones.negocio_movimientos", n)

    return {"fecha": fecha_iso, "boletos": len(persistibles),
            "skipped": skipped, "upsertados": n, "anulados": anulados}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fecha", help="YYYY-MM-DD; ingesta SÓLO ese día")
    parser.add_argument("--desde", help="YYYY-MM-DD; default hoy-lookback hábiles ART")
    parser.add_argument("--hasta", help="YYYY-MM-DD; default hoy ART")
    parser.add_argument("--dry", action="store_true",
                        help="No escribe a SQL, solo reporta cuántos persistirían")
    args = parser.parse_args()

    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()  # ART

    if args.fecha:
        try:
            dias = [datetime.strptime(args.fecha, "%Y-%m-%d").date()]
        except ValueError:
            print(f"--fecha mal formada: {args.fecha}")
            return 1
    elif args.desde or args.hasta:
        # Rango explícito (backfill). Ingesta cada día hábil de [desde, hasta].
        try:
            desde_d = (datetime.strptime(args.desde, "%Y-%m-%d").date() if args.desde else hoy)
            hasta_d = (datetime.strptime(args.hasta, "%Y-%m-%d").date() if args.hasta else hoy)
        except ValueError:
            print("--desde/--hasta mal formadas (YYYY-MM-DD)")
            return 1
        if desde_d > hasta_d:
            print("--desde no puede ser mayor que --hasta")
            return 1
        dias = []
        d = desde_d
        while d <= hasta_d:
            if d.weekday() < 5:  # feriados devuelven vacío igual, no rompe
                dias.append(d)
            d += timedelta(days=1)
    else:
        # Default del cron: hoy + últimos días hábiles (captura diferencias T+1).
        # Calendario único (core.calendario): la copia local anterior contaba
        # SOLO weekday<5 — un feriado consumía un lugar de la ventana T+1 y
        # las diferencias de futuros que llegan tarde podían quedar afuera.
        from core.calendario import ultimos_habiles
        dias = ultimos_habiles(hoy, _LOOKBACK_HABILES)

    from core.job_runs import JobRunLogger
    with JobRunLogger("negocio_movimientos") as jr:
        agg = {"dias": len(dias), "rango": f"{dias[0]}..{dias[-1]}" if dias else "",
               "boletos": 0, "skipped": 0, "upsertados": 0, "anulados": 0}
        for i, d in enumerate(dias):
            res = run(fecha_d=d, dry=args.dry)
            for k in ("boletos", "skipped", "upsertados", "anulados"):
                agg[k] += res.get(k, 0) or 0
            if i < len(dias) - 1:
                time.sleep(2)  # throttle suave entre días (REGLA #4)
        for k, v in agg.items():
            jr.set_stat(k, v)
    print(f"\n→ {agg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
