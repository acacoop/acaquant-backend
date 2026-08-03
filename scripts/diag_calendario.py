"""scripts/diag_calendario.py — ¿por qué el calendario económico está vacío?

Read-only sobre la DB; SÍ pega a FMP (1 request del free tier de 250/día).
Separa las tres causas posibles de "Sin eventos próximos":

  1. La tabla `home.market_calendar` está vacía / desactualizada → el job
     jobs/economic_calendar nunca corrió o viene fallando.
  2. FMP no devuelve nada (API key vencida, endpoint caído, free tier sin
     calendario) → se ve en el error o en "FMP devolvió 0".
  3. FMP devuelve datos pero el FILTRO del job los tira: solo AR/US/BR con
     impacto ALTO (nivel 3). Si FMP cambió el nombre del país o el campo de
     impacto, se descarta todo en silencio. Por eso se imprime el desglose por
     país e impacto de lo CRUDO, antes de filtrar.

Uso:
    python -m scripts.diag_calendario
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from core.postgres import connect


def main() -> int:
    print("=== 1. Estado de home.market_calendar ===")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*), min(evt_ts), max(evt_ts) FROM home.market_calendar")
        n, f_min, f_max = cur.fetchone()
        print(f"filas: {n}   rango: {f_min} → {f_max}")
        cur.execute("SELECT count(*) FROM home.market_calendar WHERE evt_ts >= now()")
        print(f"futuros (lo que muestra la vista): {cur.fetchone()[0]}")
        cur.execute(
            """SELECT started_at, status, finished_at, data->'stats' FROM manager.job_runs
               WHERE tipo = 'economic_calendar' ORDER BY started_at DESC LIMIT 5"""
        )
        filas = cur.fetchall()
        print("\núltimas corridas registradas en manager.job_runs:")
        if not filas:
            print("  (ninguna — ojo: este job NO usa JobRunLogger, así que puede")
            print("   estar corriendo igual sin dejar rastro acá; mirar el log del cron)")
        for ini, status, fin, stats in filas:
            print(f"  {ini}  {status:<10} fin={fin}  {stats}")

    print("\n=== 2-3. Qué devuelve FMP HOY (crudo, antes del filtro) ===")
    from core.fmp import FmpError, economic_calendar
    hoy = datetime.now(UTC).date()
    try:
        evs = economic_calendar(
            desde=hoy.isoformat(), hasta=(hoy + timedelta(days=60)).isoformat())
    except FmpError as e:
        print(f"!! FMP falló: {e}")
        print("   → causa 2: el job no puede traer nada (revisar FMP_API_KEY en .env).")
        return 1

    print(f"FMP devolvió {len(evs)} eventos para los próximos 60 días")
    if not evs:
        print("   → causa 2: la API responde OK pero sin datos.")
        return 1

    print("\nmuestra cruda (primeros 3):")
    for ev in evs[:3]:
        print(f"  {ev}")

    paises = Counter(str(e.get("country")) for e in evs)
    impactos = Counter(str(e.get("impact")) for e in evs)
    print(f"\npaíses (top 10): {paises.most_common(10)}")
    print(f"valores de `impact`: {impactos.most_common()}")

    from jobs.economic_calendar import IMPACT_MIN, _country_code, _impact_to_int
    pasan = [e for e in evs
             if _country_code(e.get("country", "")) is not None
             and _impact_to_int(e.get("impact")) >= IMPACT_MIN]
    print(f"\npasan el filtro del job (AR/US/BR + impacto ≥ {IMPACT_MIN}): {len(pasan)}")
    if not pasan:
        print("   → causa 3: FMP trae datos pero el filtro los descarta TODOS.")
        print("     Comparar los valores de arriba con COUNTRIES/IMPACT_MAP del job.")
    else:
        print("   → el filtro está bien: correr `python -m jobs.economic_calendar` llena la tabla.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
