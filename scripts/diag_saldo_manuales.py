"""scripts/diag_saldo_manuales.py — validar el cierre por cuenta ANTES de deployar.

READ-ONLY. Solo hace SELECT; no escribe una sola fila y no toca la app.

⚠️ **Se puede correr sin deployar.** `git pull` trae este archivo pero NO reinicia
la API: la página sigue mostrando los números viejos hasta que corras
`deploy/deploy.sh`. O sea que se valida primero y se deploya después.

**Qué compara.** El ajuste manual pasó a ser ACUMULADO — «el saldo inicial de hoy
es el saldo final de ayer», aplicado todos los días:

    REGLA VIEJA:  saldo = saldo del banco  +  manuales DE ESE DÍA
    REGLA NUEVA:  saldo = saldo del banco  +  Σ manuales HASTA ESE DÍA

El valor NUEVO **no se recalcula acá**: sale de `bancos._saldos_banco()`, la misma
función que va a usar la pantalla. Si este número está bien, la página va a
mostrar exactamente ese.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.diag_saldo_manuales              # el día hábil anterior
    python -m scripts.diag_saldo_manuales --fecha 2026-08-26
    python -m scripts.diag_saldo_manuales --todas      # también las que no cambian
    python -m scripts.diag_saldo_manuales --detalle    # día por día, para investigar
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from api.services import bancos as svc
from api.services._sql import _f, _q


def _plata(v: float | None) -> str:
    return f"{'—':>14}" if v is None else f"{v:>14,.2f}"


def _cuentas(cuenta_id: int | None) -> list[dict]:
    return _q(
        """SELECT c.id, c.bank_name, c.account_number, c.account_type, c.currency,
                  c.origen
             FROM bancos.cuentas c
            WHERE c.activa AND (%s OR c.id = %s)
            ORDER BY c.bank_name, c.currency, c.account_number""",
        (cuenta_id is None, cuenta_id))


def _etiqueta(c: dict) -> str:
    return (f"{c['bank_name']} · {c['account_type']} {c['currency']} "
            f"{c['account_number']}"
            + ("  [MANUAL]" if c["origen"] == "manual" else ""))


# ── MODO POR DEFECTO: una fila por cuenta, viejo contra nuevo ────────────────
def resumen(fecha: date, cuenta_id: int | None, todas: bool) -> None:
    cuentas = _cuentas(cuenta_id)
    # El valor NUEVO sale de la función real: no se reimplementa la regla.
    nuevos = svc._saldos_banco(fecha)
    # El VIEJO: el mismo saldo del banco, pero con los manuales de ESE DÍA nada más.
    del_dia = {r["cuenta_id"]: _f(r["ajuste"]) or 0.0 for r in _q(
        """SELECT cuenta_id, sum(CASE WHEN tipo = 'C' THEN abs(importe)
                                      ELSE -abs(importe) END) AS ajuste
             FROM bancos.movimientos_manuales
            WHERE fecha = %s GROUP BY cuenta_id""", (fecha,))}

    print(f"\nCIERRE DEL {fecha.isoformat()} ({fecha.strftime('%A')})\n")
    print(f"{'cuenta':<44}{'banco':>14}{'man. del día':>14}"
          f"{'acumulado':>14}{'SALDO VIEJO':>14}{'SALDO NUEVO':>14}{'':>4}")
    print("-" * 118)

    cambian = 0
    totales: dict[str, list[float]] = {}
    for c in cuentas:
        n = nuevos.get(c["id"])
        acum = (n or {}).get("ajuste") or 0.0
        dia = del_dia.get(c["id"]) or 0.0
        nuevo = (n or {}).get("valor")
        if (n or {}).get("fuente") == "manual":
            # La cuenta que Interbanking NO informa: no hay saldo del banco, y
            # con la regla vieja no tenía saldo ninguno (lo del día es un
            # movimiento, no un saldo).
            banco, viejo = None, (round(dia, 2) if dia else None)
        else:
            # El banco pelado = el nuevo menos su acumulado.
            banco = None if nuevo is None else round(nuevo - acum, 2)
            viejo = None if banco is None else round(banco + dia, 2)

        distinto = round((nuevo or 0.0) - (viejo or 0.0), 2)
        if distinto:
            cambian += 1
        elif not todas:
            continue

        print(f"{_etiqueta(c)[:43]:<44}{_plata(banco)}{_plata(dia)}{_plata(acum)}"
              f"{_plata(viejo)}{_plata(nuevo)}{'  <<<' if distinto else ''}")

        t = totales.setdefault(c["currency"] or "?", [0.0, 0.0])
        t[0] += viejo or 0.0
        t[1] += nuevo or 0.0

    print("-" * 118)
    for moneda, (v, nv) in sorted(totales.items()):
        print(f"{'TOTAL ' + moneda:<44}{'':>42}{_plata(round(v, 2))}"
              f"{_plata(round(nv, 2))}")
    print(f"\n{cambian} de {len(cuentas)} cuenta(s) cambian de saldo con la regla nueva.")
    if not todas:
        print("(Solo se listan las que cambian. Con --todas salen todas.)")
    print("\nEl SALDO NUEVO sale de `bancos._saldos_banco()`, la misma función que va")
    print("a usar la pantalla: si estos números están bien, la página va a mostrarlos.")
    print("Nada de esto está en la app todavía — hace falta `bash deploy/deploy.sh`.")


# ── MODO --detalle: día por día, para investigar una cuenta ──────────────────
def detalle(hasta: date, cuenta_id: int | None, dias: int) -> None:
    desde = hasta - timedelta(days=dias)
    cuentas = [c for c in _cuentas(cuenta_id)
               if cuenta_id is not None or _q(
                   "SELECT 1 FROM bancos.movimientos_manuales WHERE cuenta_id = %s LIMIT 1",
                   (c["id"],))]
    if not cuentas:
        print("Ninguna cuenta tiene movimientos manuales. Nada que investigar.")
        return

    for c in cuentas:
        print("=" * 96)
        print(f"[{c['id']}] {_etiqueta(c)}")
        print("-" * 96)
        print(f"{'fecha':<12}{'banco':>14}{'man. del día':>14}{'acumulado':>14}"
              f"{'SALDO':>14}{'INICIAL=ayer':>14}")
        print("-" * 96)
        previo = None
        d = desde
        while d <= hasta:
            r = (_q("""SELECT coalesce(e.saldo_cierre, s.saldo_operativo,
                                       s.saldo_dia) AS banco,
                              (SELECT sum(CASE WHEN tipo='C' THEN abs(importe)
                                               ELSE -abs(importe) END)
                                 FROM bancos.movimientos_manuales m
                                WHERE m.cuenta_id = %s AND m.fecha = %s) AS man,
                              (SELECT sum(CASE WHEN tipo='C' THEN abs(importe)
                                               ELSE -abs(importe) END)
                                 FROM bancos.movimientos_manuales m
                                WHERE m.cuenta_id = %s AND m.fecha <= %s) AS acum
                         FROM (SELECT 1) x
                         LEFT JOIN bancos.extracto_dia e
                                ON e.cuenta_id = %s AND e.fecha = %s
                         LEFT JOIN bancos.saldos s
                                ON s.cuenta_id = %s AND s.fecha = %s""",
                    (c["id"], d, c["id"], d, c["id"], d, c["id"], d)) or [{}])[0]
            banco, man = _f(r.get("banco")), _f(r.get("man")) or 0.0
            acum = _f(r.get("acum")) or 0.0
            saldo = (None if banco is None and not acum
                     else round((banco or 0.0) + acum, 2))
            # `<<<` = hay manuales de días anteriores pesando. Es justo donde la
            # regla vieja mostraba el saldo crudo de Interbanking.
            marca = "   <<< acá fallaba" if round(acum - man, 2) else ""
            print(f"{d.isoformat():<12}{_plata(banco)}{_plata(man)}{_plata(acum)}"
                  f"{_plata(saldo)}{_plata(previo)}{marca}")
            previo = saldo
            d += timedelta(days=1)

        movs = _q("""SELECT fecha, descripcion, importe, tipo, creado_por
                       FROM bancos.movimientos_manuales
                      WHERE cuenta_id = %s ORDER BY fecha, creado_at""", (c["id"],))
        if movs:
            print("\n  Movimientos manuales (TODOS, son los que forman el acumulado):")
            for m in movs:
                signo = "+" if m["tipo"] == "C" else "−"
                print(f"    {m['fecha']}  {signo}{abs(_f(m['importe']) or 0):>13,.2f}  "
                      f"{(m['descripcion'] or '')[:42]:<42}  {m['creado_por'] or ''}")
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fecha", help="AAAA-MM-DD (default: el día hábil anterior)")
    ap.add_argument("--cuenta", type=int, help="cuenta_id de bancos.cuentas")
    ap.add_argument("--todas", action="store_true",
                    help="listar también las cuentas cuyo saldo no cambia")
    ap.add_argument("--detalle", action="store_true",
                    help="día por día, para investigar una cuenta")
    ap.add_argument("--dias", type=int, default=7, help="ventana de --detalle")
    args = ap.parse_args()

    fecha = (date.fromisoformat(args.fecha) if args.fecha else svc.fecha_default())
    if args.detalle:
        detalle(fecha, args.cuenta, args.dias)
    else:
        resumen(fecha, args.cuenta, args.todas)


if __name__ == "__main__":
    main()
