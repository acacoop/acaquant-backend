"""Diag read-only: ¿hay OPCIONES agro en operaciones.operaciones? (one-shot).

Objetivo: descubrir con qué `tipo_operacion` / `instrumento` llegan las opciones
agropecuarias de clientes, para poder separarlas de los futuros en la vista AGRO
de negocio (/ops/agro). Hoy `clasificar_commodity` solo etiqueta FUTUROS.

NO escribe nada. Solo SELECT + GROUP BY. Correr fuera de rueda si se puede.

Uso (Droplet): python -m scripts.diag_agro_opciones
"""
from __future__ import annotations

from core.postgres import get_pool


def _run(cur, titulo: str, sql: str) -> None:
    print(f"\n===== {titulo} =====")
    cur.execute(sql)
    rows = cur.fetchall()
    if not rows:
        print("  (sin filas)")
        return
    for r in rows:
        print("  " + " | ".join("" if v is None else str(v) for v in r))


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        # 1) tipo_operacion que huelen a OPCIÓN (cualquier mercado).
        _run(cur, "tipo_operacion con 'OPCION'/'OPCIÓN' (conteo + rango fechas)",
             "SELECT tipo_operacion, count(*) AS n, "
             "min(concertacion) AS desde, max(concertacion) AS hasta "
             "FROM operaciones "
             "WHERE tipo_operacion ILIKE '%OPCION%' OR tipo_operacion ILIKE '%OPCIÓN%' "
             "GROUP BY tipo_operacion ORDER BY n DESC")

        # 2) tipo_operacion que huelen a AGRO / AGROPECUARIO (para ver el universo).
        _run(cur, "tipo_operacion con 'AGRO' (conteo + rango fechas)",
             "SELECT tipo_operacion, count(*) AS n, "
             "min(concertacion) AS desde, max(concertacion) AS hasta "
             "FROM operaciones "
             "WHERE tipo_operacion ILIKE '%AGRO%' "
             "GROUP BY tipo_operacion ORDER BY n DESC")

        # 3) De las opciones (si las hay): muestra de instrumentos para inferir
        #    commodity (SOJ/TRI/MAI) y tamaño de contrato.
        _run(cur, "Muestra de instrumentos en boletos de OPCIONES (top 30 por n)",
             "SELECT instrumento, count(*) AS n, "
             "min(ABS(cantidad)) AS cant_min, max(ABS(cantidad)) AS cant_max "
             "FROM operaciones "
             "WHERE (tipo_operacion ILIKE '%OPCION%' OR tipo_operacion ILIKE '%OPCIÓN%') "
             "GROUP BY instrumento ORDER BY n DESC LIMIT 30")

        # 4) Cómo está hoy poblado el campo commodity (baseline de la vista actual).
        _run(cur, "Distribución actual de commodity (lo que hoy ve /ops/agro)",
             "SELECT commodity, count(*) AS n FROM operaciones "
             "GROUP BY commodity ORDER BY n DESC")


if __name__ == "__main__":
    main()
