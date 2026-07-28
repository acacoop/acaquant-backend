"""diag_diferencias_freshness.py — ¿por qué las Diferencias Diarias se cortan
el 2026-05-06 si la base sigue recibiendo movimientos nuevos?

READ-ONLY. Responde en 3 bloques, sin asumir nada (REGLA #2):

  A) BASE — ¿el job sigue escribiendo negocio_movimientos, o está toda frenada?
     - max(fecha) y max(ingestado_en) de TODA la tabla vs SOLO diferencias.
     - conteo por día de los últimos ~25 días: total vs diferencias.
     Si hay filas nuevas totales pero 0 diferencias → el problema es upstream
     (Aunesa dejó de mandarlas, o un filtro nuestro las descarta), NO el cron.

  B) AUNESA EN VIVO — para las últimas jornadas hábiles pega a Aunesa y mira el
     RAW (antes de excluir): ¿siguen viniendo movimientos "Diferencias diarias"?
     ¿Los descarta `_excluir`? Muestra ejemplos + qué substring los mata.
     Esto separa "Aunesa no las manda más" de "las estamos filtrando".

  C) job_runs — últimas corridas del cron negocio_movimientos.

Uso (en el Droplet):
    python -m scripts.diag_diferencias_freshness
    python -m scripts.diag_diferencias_freshness --dias-aunesa 6
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta

import requests

from api.services import aunesa_negocio as svc
from core.postgres import get_pool

DIF_LIKE = "Diferencias diarias%"


def _dias_habiles_recientes(n: int) -> list[date]:
    """Últimos `n` días hábiles ART (excluye sáb/dom), hoy incluido."""
    out: list[date] = []
    d = (datetime.now(UTC) - timedelta(hours=3)).date()
    while len(out) < n:
        if d.weekday() < 5:  # 0=lun .. 4=vie
            out.append(d)
        d -= timedelta(days=1)
    return out


def bloque_base() -> None:
    print("\n" + "=" * 72)
    print("A) BASE — negocio_movimientos (freshness)")
    print("=" * 72)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT max(fecha) AS max_fecha, max(ingestado_en) AS max_ing, count(*) AS n "
            "FROM negocio_movimientos"
        )
        r = cur.fetchone()
        print(f"  TOTAL     → max(fecha)={r[0]}  max(ingestado_en)={r[1]}  filas={r[2]:,}")

        cur.execute(
            "SELECT max(fecha) AS max_fecha, max(ingestado_en) AS max_ing, count(*) AS n "
            "FROM negocio_movimientos "
            "WHERE categoria = 'otro' AND informacion ILIKE %s",
            (DIF_LIKE,),
        )
        r = cur.fetchone()
        print(f"  DIFERENC. → max(fecha)={r[0]}  max(ingestado_en)={r[1]}  filas={r[2]:,}")

        print("\n  Últimos 25 días con filas — total vs diferencias:")
        print(f"    {'fecha':<12} {'total':>10} {'diferencias':>12}")
        cur.execute(
            "SELECT fecha, "
            "  count(*) AS total, "
            "  count(*) FILTER (WHERE categoria='otro' AND informacion ILIKE %s) AS dif "
            "FROM negocio_movimientos "
            "GROUP BY fecha ORDER BY fecha DESC LIMIT 25",
            (DIF_LIKE,),
        )
        for f, tot, dif in cur.fetchall():
            flag = "  <-- 0 diferencias" if dif == 0 else ""
            print(f"    {f!s:<12} {tot:>10,} {dif:>12,}{flag}")


def bloque_aunesa(dias: int) -> None:
    print("\n" + "=" * 72)
    print(f"B) AUNESA EN VIVO — últimas {dias} jornadas hábiles (RAW, antes de excluir)")
    print("=" * 72)
    for d in _dias_habiles_recientes(dias):
        print(f"\n  ── {d.isoformat()} ──")
        try:
            headers = svc._autenticar()
            resp = requests.get(
                svc.OPS_URL,
                params={
                    "tiposCuenta": "Comitente",
                    "concertacionDesde": d.strftime("%d/%m/%Y"),
                    "concertacionHasta": d.strftime("%d/%m/%Y"),
                },
                headers=headers,
                timeout=180,
            )
            resp.raise_for_status()
            body = (resp.text or "").strip()
            data = resp.json() if body else []
            if not isinstance(data, list):
                print(f"    shape inesperada: {type(data).__name__}")
                continue
        except Exception as e:
            print(f"    ERROR pegando a Aunesa: {e}")
            continue

        raw_total = len(data)
        # RAW crudos cuya informacion menciona "Diferencia" (case-insensitive).
        dif_raw = [
            r for r in data
            if "diferencia" in str(r.get("informacion") or "").lower()
        ]
        # ¿Cuántos de esos los mata _excluir?
        dif_excl = [r for r in dif_raw if svc._excluir(r)]
        print(f"    raw_total={raw_total:,}   raw c/'Diferencia'={len(dif_raw)}   "
              f"de esos EXCLUIDOS por _excluir={len(dif_excl)}")

        # Ejemplos de informacion cruda (hasta 6 distintos) + si se excluye.
        vistos: set[str] = set()
        muestras = 0
        for r in dif_raw:
            info = str(r.get("informacion") or "")
            if info in vistos:
                continue
            vistos.add(info)
            excl = svc._excluir(r)
            print(f"      [{'EXCL' if excl else 'ok  '}] moneda={r.get('moneda')!s:6} "
                  f"info={info[:90]!r}")
            muestras += 1
            if muestras >= 6:
                break
        if not dif_raw:
            print("      (Aunesa NO devolvió ningún movimiento con 'Diferencia' este día)")


def bloque_job_runs() -> None:
    print("\n" + "=" * 72)
    print("C) job_runs — últimas corridas de negocio_movimientos")
    print("=" * 72)
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT started_at, status, data->'stats' AS stats FROM manager.job_runs "
                "WHERE tipo = 'negocio_movimientos' "
                "ORDER BY started_at DESC LIMIT 12"
            )
            for started, status, stats in cur.fetchall():
                print(f"    {started!s:<28} status={status!s:8} stats={stats}")
    except Exception as e:
        print(f"    (no se pudo leer job_runs: {e})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dias-aunesa", type=int, default=5,
                    help="Cuántas jornadas hábiles recientes pegar a Aunesa (default 5)")
    ap.add_argument("--skip-aunesa", action="store_true",
                    help="Sólo mirar la base (no pega a Aunesa)")
    args = ap.parse_args()

    bloque_base()
    if not args.skip_aunesa:
        bloque_aunesa(args.dias_aunesa)
    bloque_job_runs()
    print("\n" + "=" * 72)
    print("Listo. Interpretación rápida:")
    print("  · Base con filas nuevas TOTALES pero 0 diferencias en días recientes")
    print("    → NO es el cron. Es upstream (Aunesa o filtro).")
    print("  · Aunesa RAW trae 'Diferencia' y _excluir las marca EXCL")
    print("    → las estamos filtrando nosotros (revisar EXCLUIR_* del texto nuevo).")
    print("  · Aunesa RAW NO trae 'Diferencia' ningún día reciente")
    print("    → Aunesa dejó de mandarlas (cambio de origen / futuros cerrados).")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
