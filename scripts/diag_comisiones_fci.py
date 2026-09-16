"""Diagnostico read-only de COMISIONES FCI.

Ejecutar en el droplet:
    python -m scripts.diag_comisiones_fci --desde 2025-06-01

No escribe nada. Compara la serie que usa el grafico con la cobertura de
portafolio.tenencia, fee_admin y portafolio.backfill_log.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal

from api.services import comisiones_fci as svc
from core.postgres import get_job_pool


def _json_default(value):
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"no serializable: {type(value).__name__}")


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


_FCI = [x.upper() for x in svc.FCI]
_WHERE_FCI = "upper(btrim(coalesce(t.cartera, ''))) = ANY(%(fci)s)"


_SQL_MESES = f"""
WITH base AS (
    SELECT t.*
    FROM portafolio.tenencia t
    WHERE t.fecha >= %(desde)s AND {_WHERE_FCI}
),
meses AS (
    SELECT date_trunc('month', fecha)::date AS mes,
           min(fecha) AS primera_foto,
           count(DISTINCT fecha) AS fotos_distintas,
           max(fecha) AS ultima_foto
    FROM base
    GROUP BY 1
), corte AS (
    SELECT
        m.mes,
        m.ultima_foto,
        m.primera_foto,
        m.fotos_distintas,
        sum(t.valuacion) AS valuacion_corte,
        count(DISTINCT t.unidad) AS fondos_corte,
        count(DISTINCT t.id_cuenta) AS cuentas_corte,
        count(DISTINCT t.unidad) FILTER (WHERE a.fee_admin IS NOT NULL)
            AS fondos_con_fee,
        count(DISTINCT t.unidad) FILTER (WHERE a.fee_admin IS NULL)
            AS fondos_sin_fee,
        sum(t.valuacion) FILTER (WHERE a.fee_admin IS NULL)
            AS valuacion_sin_fee,
        min(a.fee_admin) FILTER (WHERE a.fee_admin IS NOT NULL) AS fee_min,
        max(a.fee_admin) FILTER (WHERE a.fee_admin IS NOT NULL) AS fee_max
    FROM meses m
    JOIN base t ON t.fecha = m.ultima_foto
    LEFT JOIN portafolio.assets a ON a.unidad = t.unidad
    GROUP BY m.mes, m.ultima_foto, m.primera_foto, m.fotos_distintas
),
backfill AS (
    SELECT
        m.mes,
        count(*) FILTER (WHERE b.status = 'ok') AS log_ok,
        count(*) FILTER (WHERE b.status = 'vacia') AS log_vacia,
        count(*) FILTER (WHERE b.status LIKE 'error%%') AS log_error,
        count(*) FILTER (WHERE b.status = 'timeout') AS log_timeout,
        count(*) FILTER (WHERE b.status IS NULL) AS log_sin_fila
    FROM meses m
    JOIN base t ON date_trunc('month', t.fecha)::date = m.mes
    LEFT JOIN portafolio.backfill_log b
      ON b.fecha = t.fecha AND b.id_cuenta = t.id_cuenta
    GROUP BY m.mes
)
SELECT c.*, b.log_ok, b.log_vacia, b.log_error, b.log_timeout, b.log_sin_fila
FROM corte c
JOIN backfill b ON b.mes = c.mes
ORDER BY c.mes
"""


_SQL_TOP = f"""
WITH meses AS (
    SELECT date_trunc('month', t.fecha)::date AS mes, max(t.fecha) AS ultima_foto
    FROM portafolio.tenencia t
    WHERE t.fecha >= %(desde)s AND {_WHERE_FCI}
    GROUP BY 1
), fondos AS (
    SELECT
        m.mes,
        t.unidad,
        sum(t.valuacion) AS valuacion,
        max(a.fee_admin) AS fee_admin,
        sum(t.valuacion * a.fee_admin * 0.5 / 365) AS arancel_dia
    FROM meses m
    JOIN portafolio.tenencia t ON t.fecha = m.ultima_foto
    LEFT JOIN portafolio.assets a ON a.unidad = t.unidad
    WHERE {_WHERE_FCI}
    GROUP BY m.mes, t.unidad
), cambios AS (
    SELECT
        *,
        valuacion - lag(valuacion) OVER (PARTITION BY unidad ORDER BY mes)
            AS cambio_valuacion,
        arancel_dia - lag(arancel_dia) OVER (PARTITION BY unidad ORDER BY mes)
            AS cambio_arancel_dia
    FROM fondos
)
SELECT *
FROM cambios
WHERE mes >= %(desde)s
ORDER BY mes, abs(coalesce(cambio_arancel_dia, arancel_dia)) DESC NULLS LAST
"""


def _float_rows(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        clean = {}
        for key, value in row.items():
            if isinstance(value, Decimal):
                clean[key] = float(value)
            else:
                clean[key] = value
        out.append(clean)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desde", default="2026-05-01", help="fecha inicial YYYY-MM-DD")
    parser.add_argument("--mes", default="2026-05", help="mes cuyo salto se quiere explicar")
    parser.add_argument("--top", type=int, default=20, help="fondos principales por mes")
    args = parser.parse_args()

    desde = max(date.fromisoformat(args.desde), svc.INICIO_HISTORICO)
    params = {"desde": desde, "fci": _FCI}
    meses = _float_rows(_q(_SQL_MESES, params))
    cambios = _float_rows(_q(_SQL_TOP, params))

    top_por_mes = {}
    for row in cambios:
        mes = row["mes"].strftime("%Y-%m") if isinstance(row["mes"], date) else str(row["mes"])
        top_por_mes.setdefault(mes, []).append(row)
    for rows in top_por_mes.values():
        del rows[args.top:]

    resultado = {
        "instrucciones": {
            "lectura": "comparar serie_api con las barras del grafico",
            "valuacion_sin_fee": "queda fuera del total calculable; no significa cero",
            "fee_admin": "fraccion anual: 0.025 = 2.5%",
            "backfill": "log_error/timeout/sin_fila indican cobertura incompleta",
        },
        "serie_api": svc.serie_mensual(),
        "meses": meses,
        "fondos_top_por_mes": top_por_mes,
        "fees_sospechosos": _float_rows(_q(
            "SELECT unidad, emisor, cartera, fee_admin "
            "FROM portafolio.assets "
            "WHERE upper(btrim(coalesce(cartera, ''))) = ANY(%(fci)s) "
            "AND (fee_admin < 0 OR fee_admin > 0.25) "
            "ORDER BY fee_admin DESC NULLS LAST",
            {"fci": _FCI},
        )),
        "mes_objetivo": args.mes,
        "inicio_historico": svc.INICIO_HISTORICO,
    }
    print(json.dumps(resultado, ensure_ascii=True, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
