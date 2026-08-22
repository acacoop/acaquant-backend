"""scripts/diag_tenencia_fechas.py — cómo escribe FECHAS el job de tenencias.

Read-only. El control `portafolio_diario` alertó un sábado que *«el 2026-08-21
(viernes) NO está»* — y el user, con razón: *«si justamente está bien que los
datos estén en T-1, la alerta está desconectada de cómo funciona eso»*. Antes
de re-modelar el control hay que MEDIR el contrato real (REGLA #2): ¿el job de
las 11:00 UTC escribe el día en que corre, o el hábil anterior?

Imprime:
  1. Las últimas 12 fechas DISTINTAS de `portafolio.tenencia` con su volumen —
     el patrón dice solo si es T o T-1 (y si los findes quedan huecos).
  2. Las últimas corridas de `portafolio_backfill` en `manager.job_runs`
     (cuándo corrió vs qué fecha dejó).
  3. Lo que HOY exigen el contrato de SALUD y el control, para compararlo.

Con la salida se decide la regla correcta del control (qué fecha exacta tiene
que existir según el día y la hora en que se mira) — sin adivinar.

Uso: python -m scripts.diag_tenencia_fechas
"""
from __future__ import annotations

from core.postgres import get_pool


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        print("── 1. Últimas fechas en portafolio.tenencia " + "─" * 24)
        cur.execute(
            "SELECT fecha, count(*) FROM portafolio.tenencia "
            "GROUP BY fecha ORDER BY fecha DESC LIMIT 12")
        for f, n in cur.fetchall():
            print(f"  {f}  ({n} filas)  {f.strftime('%A') if hasattr(f, 'strftime') else ''}")

        print("\n── 2. Últimas corridas de portafolio_backfill " + "─" * 22)
        cur.execute(
            "SELECT started_at, status, coalesce(data::text, '') "
            "  FROM manager.job_runs "
            " WHERE tipo LIKE '%%portafolio%%' "
            " ORDER BY started_at DESC LIMIT 6")
        for at, st, data in cur.fetchall():
            print(f"  {at:%d/%m %H:%M} UTC  {st:<8} {str(data)[:140]}")

    print("\n── 3. Lo que HOY se exige " + "─" * 40)
    from api.services import salud
    c = next((c for c in salud.CONTRATOS
              if c["tabla"] == "portafolio.tenencia"), None)
    print(f"  contrato SALUD: {c}")
    print("  control `portafolio_diario`: ver manager.controles_datos "
          "(la fila del diag de arriba dice qué fecha esperó y no encontró)")


if __name__ == "__main__":
    main()
