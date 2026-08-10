"""Backfill: rellena `operaciones.operaciones.mep` en los boletos que no lo tienen.

Contexto (2026-08-10): la vista OPERACIONES en modo DOLARIZAR (`moneda=USD_DOL`)
convierte cada boleto ARS con SU snapshot `mep`. Los boletos que escribe
`jobs/fci_bilateral` nunca estamparon `mep` → el mercado FCI Bilateral tenía
volumen ARS real y ~0 dolarizado (julio 2026, reportado por la mesa). El service
ya no los descarta (cae al MEP del día vía `_MEP_ROW`), pero conviene dejar el
snapshot materializado: es más barato que resolver el TC por fila en cada query.

Qué hace: para cada boleto sin `mep` (NULL o 0) toma el último MEP > 0 de
`valuaciones.dolar` con timestamp <= fin-de-día ART de su `concertacion` — MISMO
criterio que `core.dolar_sql.mep_para_fecha` y que el enrich de
`api/services/operaciones_informes`. Los boletos anteriores al inicio del feed
MEP quedan sin TC (no hay dato que inventar).

Seguro (REGLA #4): un UPDATE por DÍA (batcheado, con throttle), guarda
`mep IS NULL OR mep = 0` → re-correrlo no toca nada una vez migrado.

Uso (Droplet):
    python -m scripts.backfill_ops_mep --dry-run   # solo cuenta
    python -m scripts.backfill_ops_mep

Después de correrlo, recalcular el agregado frío (usa la misma fórmula):
    python -m jobs.ops_agregado --full
"""
from __future__ import annotations

import sys
import time

from core.postgres import get_pool

PAUSA_S = 0.2

_SQL_PENDIENTES = """
SELECT concertacion, count(*) AS n
FROM operaciones.operaciones
WHERE (mep IS NULL OR mep = 0) AND concertacion IS NOT NULL
GROUP BY concertacion ORDER BY concertacion
"""

# El MEP del día = último > 0 con timestamp <= fin-de-día ART (arrastra el último
# día con dato, igual que dolar_sql.mep_para_fecha).
_SQL_UPDATE_DIA = """
UPDATE operaciones.operaciones SET mep = (
  SELECT d.mep FROM valuaciones.dolar d
  WHERE d.mep IS NOT NULL AND d.mep > 0
    AND d.timestamp < ((%(dia)s::date + 1)::timestamp
                       AT TIME ZONE 'America/Argentina/Buenos_Aires')
  ORDER BY d.timestamp DESC LIMIT 1)
WHERE concertacion = %(dia)s AND (mep IS NULL OR mep = 0)
"""


def run(dry: bool = False) -> int:
    pool = get_pool()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(_SQL_PENDIENTES)
        pendientes = cur.fetchall()
    total_filas = sum(n for _, n in pendientes)
    print(f"Días con boletos sin mep: {len(pendientes)} · boletos: {total_filas}")
    if dry or not pendientes:
        return 0

    tocados = sin_tc = 0
    with pool.connection() as conn:
        for i, (dia, n) in enumerate(pendientes, 1):
            with conn.cursor() as cur:
                cur.execute(_SQL_UPDATE_DIA, {"dia": dia})
                cur.execute("SELECT count(*) FROM operaciones.operaciones "
                            "WHERE concertacion = %s AND (mep IS NULL OR mep = 0)", (dia,))
                quedan = cur.fetchone()[0]
            conn.commit()
            tocados += n - quedan
            sin_tc += quedan
            if quedan:
                print(f"  {dia}: {n - quedan}/{n} (sin TC en el feed: {quedan})")
            if i % 20 == 0:
                print(f"  … {i}/{len(pendientes)} días · {tocados} boletos")
                time.sleep(PAUSA_S)
    print(f"OK — {tocados} boletos con mep estampado · {sin_tc} sin TC disponible.")
    print("Recordá: python -m jobs.ops_agregado --full")
    return 0


if __name__ == "__main__":
    sys.exit(run(dry="--dry-run" in sys.argv))
