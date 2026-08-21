"""Diag read-only: ¿el mayor que muestra CONCILIAR está actualizado, y con qué?

Contesta la pregunta «lo veo todo igual» sin suponer nada. La vista puede mostrar
lo mismo por causas MUY distintas y desde la pantalla se ven idénticas:

  · el job no corrió (cron caído, o el día pedido no es el que se está mirando);
  · corrió y falló (timeout de Aunesa: el request pesa 10 MB y tarda 75-330 s);
  · corrió bien pero no guardó NADA de esa cuenta porque le falta el
    `codigo_contable`, que se carga A MANO;
  · corrió, guardó, y la diferencia es real.

Las cuatro terminan en «la pantalla no cambió». Este script las separa. **No
pega a Aunesa** —solo lee la base—, así que corre en segundos y se puede tirar
todas las veces que haga falta.

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_mayor_estado
    python -m scripts.diag_mayor_estado --fecha 19/08/2026
    python -m scripts.diag_mayor_estado --dias 5      # historial de corridas

NO escribe nada en la base.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime

from core.calendario import restar_habiles
from core.postgres import get_job_pool
from core.tz import ahora_ar


def _plata(x) -> str:
    if x is None:
        return "—"
    return f"{float(x):,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _titulo(t: str) -> None:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def _corridas(dias: int) -> None:
    """El historial del job. ⚠️ Sin esto no se puede distinguir «no corrió» de
    «corrió y no encontró nada», que es la confusión que hace perder más tiempo."""
    _titulo(f"CORRIDAS DE mayor_sync (últimas {dias})")
    filas = _q("""SELECT corrida_at, fecha_conciliacion, asientos_api,
                         movimientos_api, movimientos_banco, cuentas_sin_mapear,
                         segundos, ok, error
                    FROM bancos.mayor_sync_log
                   ORDER BY id DESC LIMIT %s""", (dias,))
    if not filas:
        print("  ⚠️  NINGUNA corrida registrada. El job nunca se ejecutó (o el cron")
        print("      no está instalado). Ver deploy/crontab.txt → mayor_sync.")
        return
    print(f"  {'corrida (ART)':<20} {'día':<12} {'asientos':>9} {'movs':>8} "
          f"{'guardados':>10} {'seg':>6}  estado")
    for f in filas:
        cuando = f["corrida_at"].astimezone(ahora_ar().tzinfo).strftime("%d/%m %H:%M:%S")
        estado = "ok" if f["ok"] else f"ERROR: {(f['error'] or '')[:60]}"
        print(f"  {cuando:<20} {f['fecha_conciliacion']!s:<12} "
              f"{f['asientos_api'] or 0:>9} {f['movimientos_api'] or 0:>8} "
              f"{f['movimientos_banco'] or 0:>10} "
              f"{float(f['segundos'] or 0):>6.0f}  {estado}")

    ultima = filas[0]
    edad = (datetime.now(ultima["corrida_at"].tzinfo) - ultima["corrida_at"])
    horas = edad.total_seconds() / 3600
    print(f"\n  La última corrió hace {horas:.1f} h y pidió el día "
          f"{ultima['fecha_conciliacion']}.")
    if horas > 24:
        print("  ⚠️  Más de un día sin correr: revisar el cron y run_job.sh.")


def _mapeo() -> None:
    """Qué cuentas tienen `codigo_contable`. Es lo ÚNICO que decide si el mayor
    de una cuenta se guarda: sin él, el job la descarta en silencio."""
    _titulo("CUENTAS Y SU codigo_contable (se carga A MANO)")
    filas = _q("""SELECT id, bank_name, account_label, account_type, currency,
                         account_number, codigo_contable
                    FROM bancos.cuentas
                   WHERE activa
                   ORDER BY codigo_contable IS NULL, bank_name, currency""")
    con = [f for f in filas if f["codigo_contable"]]
    sin = [f for f in filas if not f["codigo_contable"]]
    for f in con:
        print(f"  {f['codigo_contable']:<16} #{f['id']:<4} {f['bank_name']} · "
              f"{f['account_type']} {f['currency']} {f['account_number']} "
              f"· {f['account_label'] or ''}")
    print(f"\n  {len(con)} mapeadas · {len(sin)} SIN mapear")
    if sin:
        print("  ⚠️  Las de abajo NO tienen mayor y nunca lo van a tener hasta que")
        print("      alguien les cargue el código. En CONCILIAR salen sin mayor.")
        for f in sin:
            print(f"      #{f['id']:<4} {f['bank_name']} · {f['account_type']} "
                  f"{f['currency']} {f['account_number']}")


def _del_dia(dia: date) -> None:
    """Lo que hay GUARDADO de ese día, cuenta por cuenta, contra el banco."""
    _titulo(f"MAYOR GUARDADO DEL {dia.strftime('%d/%m/%Y')}")
    filas = _q("""SELECT c.id, c.bank_name, c.account_type, c.currency,
                         c.account_number, c.codigo_contable,
                         count(m.movimiento_id) AS movs,
                         coalesce(sum(m.importe), 0) AS suma,
                         max(m.actualizado_at)   AS ultimo
                    FROM bancos.cuentas c
                    LEFT JOIN bancos.mayor_movimientos m
                           ON m.cuenta_id = c.id AND m.fecha_conciliacion = %s
                   WHERE c.activa AND c.codigo_contable IS NOT NULL
                   GROUP BY c.id, c.bank_name, c.account_type, c.currency,
                            c.account_number, c.codigo_contable
                   ORDER BY c.bank_name, c.currency""", (dia,))
    if not filas:
        print("  Ninguna cuenta mapeada: el job no guarda nada. Ver el bloque de arriba.")
        return

    banco = {r["cuenta_id"]: r for r in _q(
        """SELECT cuenta_id, count(*) AS movs,
                  sum(CASE WHEN tipo = 'C' THEN abs(importe) ELSE -abs(importe) END) AS suma
             FROM bancos.movimientos WHERE fecha = %s GROUP BY cuenta_id""", (dia,))}

    print(f"  {'cuenta':<38} {'mayor':>6} {'suma mayor':>18} {'banco':>6} "
          f"{'suma banco':>18}")
    vacias = 0
    for f in filas:
        b = banco.get(f["id"]) or {}
        nombre = (f"{f['bank_name']} {f['account_type']} {f['currency']} "
                  f"{f['account_number']}")[:38]
        print(f"  {nombre:<38} {f['movs']:>6} {_plata(f['suma']):>18} "
              f"{b.get('movs', 0):>6} {_plata(b.get('suma')):>18}")
        if not f["movs"] and (b.get("movs") or 0):
            vacias += 1

    ultimo = max((f["ultimo"] for f in filas if f["ultimo"]), default=None)
    print(f"\n  Última escritura de este día: "
          f"{ultimo.astimezone(ahora_ar().tzinfo).strftime('%d/%m %H:%M:%S') if ultimo else '—'}")
    if vacias:
        print(f"  ⚠️  {vacias} cuenta(s) con movimientos del BANCO y CERO del mayor.")
        print("      O contabilidad todavía no cargó, o el codigo_contable apunta")
        print("      a otra cuenta: `python -m scripts.diag_mayor_mapeo` lo separa.")


def _fechas_en_base() -> None:
    """Qué días hay guardados. Si el que se está mirando no está en esta lista,
    la vista NO está mostrando datos viejos: no tiene ninguno."""
    _titulo("DÍAS DEL MAYOR QUE HAY EN LA BASE")
    for f in _q("""SELECT fecha_conciliacion AS dia, count(*) AS movs,
                          count(DISTINCT cuenta_id) AS cuentas,
                          max(actualizado_at) AS ultimo
                     FROM bancos.mayor_movimientos
                    GROUP BY 1 ORDER BY 1 DESC LIMIT 10"""):
        cuando = f["ultimo"].astimezone(ahora_ar().tzinfo).strftime("%d/%m %H:%M")
        print(f"  {f['dia']}  {f['movs']:>6} movs · {f['cuentas']:>2} cuentas "
              f"· escrito {cuando}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fecha", help="dd/mm/aaaa (default: día hábil anterior)")
    p.add_argument("--dias", type=int, default=10, help="corridas a listar")
    a = p.parse_args()

    dia = (datetime.strptime(a.fecha, "%d/%m/%Y").date() if a.fecha
           else restar_habiles(ahora_ar().date(), 1))

    print(f"Ahora (ART): {ahora_ar().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"Día analizado: {dia.strftime('%d/%m/%Y')}"
          + ("" if a.fecha else "  (día hábil anterior, el default de la vista)"))
    _corridas(a.dias)
    _mapeo()
    _fechas_en_base()
    _del_dia(dia)
    print()


if __name__ == "__main__":
    main()
