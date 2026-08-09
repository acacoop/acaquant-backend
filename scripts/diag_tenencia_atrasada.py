"""Diag READ-ONLY: por qué la tenencia no se actualiza desde el 05/08.

Las vistas TENENCIA VALORIZADA y TÍTULOS EN ALQUILER leen `portafolio.tenencia`,
que escribe el cron `jobs.portafolio_backfill --diario` (11:00 UTC, L-V). Si la
última fecha quedó vieja, el problema está en UNO de estos lugares y este diag los
separa:

  1) el JOB no corrió (cron caído / unit deshabilitada),
  2) corrió y FALLÓ (queda la traza en `manager.job_runs`),
  3) corrió OK pero no escribió filas (Aunesa devolvió vacío ese día),
  4) escribió, pero la vista filtra y no las muestra (`aum='si'`, cartera, etc.).

Uso:
    python -m scripts.diag_tenencia_atrasada
"""
from __future__ import annotations

from datetime import date, timedelta

from api.services._sql import _q

JOBS = ("portafolio_backfill", "portafolio_backfill_diario", "aum")


def ultimas_fechas() -> None:
    print("\n── 1) ÚLTIMAS FECHAS en portafolio.tenencia ──────────────────")
    try:
        rows = _q("SELECT fecha, COUNT(*) AS filas, COUNT(DISTINCT id_cuenta) AS cuentas "
                  "FROM portafolio.tenencia GROUP BY fecha ORDER BY fecha DESC LIMIT 12")
    except Exception as e:
        print(f"   ERROR: {type(e).__name__}: {e}")
        return
    if not rows:
        print("   ✗ la tabla está VACÍA")
        return
    hoy = date.today()
    for r in rows:
        f = r["fecha"]
        dias = (hoy - f).days if isinstance(f, date) else "?"
        print(f"   {f}  ·  {r['filas']:>6} filas · {r['cuentas']:>4} cuentas   (hace {dias} días)")
    # ¿Qué días hábiles faltan entre la última cargada y hoy?
    ultima = rows[0]["fecha"]
    if isinstance(ultima, date):
        faltan = []
        d = ultima + timedelta(days=1)
        while d <= hoy:
            if d.weekday() < 5:          # 0-4 = lunes a viernes
                faltan.append(d.isoformat())
            d += timedelta(days=1)
        print(f"\n   Días HÁBILES sin cargar desde la última: {faltan or 'ninguno'}")
        if faltan:
            print("   → si hay días hábiles en esa lista, el writer no escribió esos días.")


def corridas() -> None:
    print("\n── 2) ¿CORRIÓ el job? (manager.job_runs) ─────────────────────")
    # Columnas REALES (sql/schema.sql:1812): tipo / started_at / finished_at /
    # status / data(jsonb con stats y errores). No hay 'job' ni 'detalle'.
    try:
        rows = _q(
            "SELECT tipo, status, started_at, finished_at, data FROM manager.job_runs "
            "WHERE tipo ILIKE ANY(%(p)s) ORDER BY started_at DESC LIMIT 15",
            {"p": [f"%{j}%" for j in JOBS]})
    except Exception as e:
        print(f"   no pude leer job_runs: {type(e).__name__}: {e}")
        return
    if not rows:
        print("   ✗ NINGUNA corrida registrada de portafolio_backfill.")
        print("     → el cron no lo está ejecutando (revisar deploy/crontab.txt en el Droplet).")
        # Puede estar registrado con otro nombre: mostrar qué tipos SÍ corrieron hoy.
        try:
            otros = _q("SELECT DISTINCT tipo FROM manager.job_runs "
                       "WHERE started_at > now() - interval '3 days' ORDER BY tipo")
            print(f"     jobs que SÍ corrieron en 3 días: {[o['tipo'] for o in otros]}")
        except Exception:
            pass
        return
    for r in rows:
        d = r["data"] if isinstance(r["data"], dict) else {}
        err = str(d.get("errors") or d.get("error") or "")[:120]
        marca = "  ⚠" if str(r["status"]).lower() not in ("ok", "success") else ""
        print(f"   {r['started_at']}  [{r['status']}]{marca}  {r['tipo']}")
        if err:
            print(f"      errores: {err}")


def detalle_corridas(n: int = 3) -> None:
    """Los STATS y el LOG de las últimas corridas.

    `JobRunLogger` marca `ok` cuando no hubo excepción NI errores registrados — así
    que una corrida que no escribió una sola fila igual sale `ok`. Los stats son lo
    único que dice cuántas cuentas se procesaron de verdad.
    """
    print(f"\n── 2b) STATS de las últimas {n} corridas ─────────────────────")
    try:
        rows = _q("SELECT tipo, status, started_at, data FROM manager.job_runs "
                  "WHERE tipo ILIKE ANY(%(p)s) ORDER BY started_at DESC LIMIT %(n)s",
                  {"p": [f"%{j}%" for j in JOBS], "n": int(n)})
    except Exception as e:
        print(f"   no pude leer: {type(e).__name__}: {e}")
        return
    for r in rows:
        d = r["data"] if isinstance(r["data"], dict) else {}
        print(f"\n   ▸ {r['started_at']}  [{r['status']}]  {r['tipo']}")
        print(f"     elapsed_s : {d.get('elapsed_s')}")
        print(f"     stats     : {d.get('stats')}")
        errs = d.get("errors") or []
        print(f"     errors    : {errs if errs else '(ninguno)'}")
        for linea in (d.get("log") or [])[-12:]:
            print(f"       | {linea}")


def por_cartera() -> None:
    """Si la fecha SÍ está pero la vista se ve vacía, el filtro es el sospechoso."""
    print("\n── 3) La ÚLTIMA fecha cargada, abierta por cartera y AuM ─────")
    try:
        rows = _q(
            "SELECT cartera, aum, COUNT(*) AS filas FROM portafolio.tenencia "
            "WHERE fecha = (SELECT MAX(fecha) FROM portafolio.tenencia) "
            "GROUP BY cartera, aum ORDER BY filas DESC LIMIT 15")
    except Exception as e:
        print(f"   ERROR: {type(e).__name__}: {e}")
        return
    for r in rows:
        print(f"   {r['filas']:>6} filas   cartera={r['cartera']!r:<28} aum={r['aum']!r}")
    print("   (la vista de AuM filtra aum='si' — si todo vino con otro valor, se ve vacía)")


def main() -> int:
    print(f"\n{'=' * 70}\nDIAG — tenencia atrasada (hoy {date.today().isoformat()})\n{'=' * 70}")
    ultimas_fechas()
    corridas()
    detalle_corridas()
    por_cartera()
    print("\nCómo leerlo: si faltan días hábiles Y no hay corridas → el cron no ejecuta.")
    print("Si hay corridas con estado de error → el detalle dice por qué falló.")
    print("Si corrió OK y no hay filas → Aunesa no devolvió posiciones ese día.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
