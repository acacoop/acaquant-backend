"""scripts/diag_interbanking.py — smoke de las 5 APIs de Interbanking con datos reales.

READ-ONLY. Todas las llamadas son GET; no escribe en Interbanking ni en la base.

Corré ANTES `python -m scripts.diag_interbanking_auth`. Este script asume que el
token ya funciona: acá lo que se prueba son los datos, no la autenticación.

Qué contesta (las preguntas que no se pueden responder sin pegarle a la API real,
REGLA #2 — nada de esto es inferible del YAML):

  1. ¿Qué cuentas ve Interbanking? ¿Son las mismas que hoy están en
     `operaciones.tesoreria_cuentas`, que se descubrieron mirando movimientos de
     Aunesa porque Aunesa no tiene endpoint de cuentas?
  2. ¿`initial_operating_balance` se parece al saldo inicial que el back office
     carga a mano todos los días?
  3. ¿El extracto cierra? (`apertura + créditos − débitos == cierre`)
  4. ¿Los movimientos traen un ID estable por movimiento? v1 declara un campo
     `id` que v2 no tiene — si es estable, resuelve la idempotencia al persistir.
  5. ¿Qué valores toma de verdad el `status` de las transferencias?
  6. ¿Los comprobantes traen el `vep_number` que hoy se tipea a mano en la tab VEPS?

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.diag_interbanking                 # cuentas + la primera cuenta a fondo
    python -m scripts.diag_interbanking --cuenta 3      # ídem con la cuenta #3 del listado
    python -m scripts.diag_interbanking --solo-cuentas  # solo el inventario (2 llamadas)
    python -m scripts.diag_interbanking --saldos-todas  # saldo de TODAS las cuentas
    python -m scripts.diag_interbanking --dias 30       # ventana de los históricos

Costo en llamadas: el modo default son ~7. `--saldos-todas` es 2 + una por cuenta.
El plan admite 100 por minuto y el cliente ya throttlea a 80.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta

from core import interbanking as ib

SEP = "=" * 78


def _titulo(t: str) -> None:
    print(f"\n{SEP}\n{t}\n{SEP}")


def _catalogo_tesoreria() -> list[dict] | None:
    """Lo que hoy tiene `operaciones.tesoreria_cuentas`. None si no hay DB."""
    try:
        from api.services._sql import _q
        return _q(
            "SELECT cuenta_operativa, unidad, numero_cuenta "
            "FROM operaciones.tesoreria_cuentas ORDER BY cuenta_operativa"
        )
    except Exception as e:  # sin DB el resto del diag igual sirve
        print(f"  (sin cruce contra tesoreria_cuentas: {type(e).__name__}: {e})")
        return None


def inventario_cuentas() -> list[dict]:
    _titulo("1. CUENTAS QUE VE INTERBANKING")
    cuentas = ib.todas_las_cuentas()
    print(f"  {len(cuentas)} cuentas (CC + CA)\n")
    for i, c in enumerate(cuentas):
        print(f"  #{i:<3} banco={c.get('bank_number')} ({c.get('bank_name')})"
              f" | {c.get('account_type')} {c.get('currency')}"
              f" | nro={c.get('account_number')}"
              f" | cbu={c.get('account_cbu')}"
              f" | {c.get('account_label')}")

    _titulo("1b. CRUCE CONTRA operaciones.tesoreria_cuentas")
    cat = _catalogo_tesoreria()
    if cat is None:
        return cuentas

    print(f"  Catálogo actual de Tesorería: {len(cat)} filas")
    for r in cat:
        print(f"     {r['cuenta_operativa']} | {r['unidad']} | nro={r.get('numero_cuenta')}")

    # El único cruce que NO requiere suponer nada es por número de cuenta, y solo
    # sirve donde el back office ya lo cargó (en tesoreria_cuentas es carga manual).
    nros_ib = {str(c.get("account_number") or "").strip() for c in cuentas}
    nros_cat = {str(r.get("numero_cuenta") or "").strip() for r in cat if r.get("numero_cuenta")}
    print(f"\n  Cuentas con numero_cuenta cargado en el catálogo: {len(nros_cat)} de {len(cat)}")
    if nros_cat:
        comunes = nros_ib & nros_cat
        print(f"  Coinciden por número con Interbanking : {len(comunes)}")
        solo_cat = nros_cat - nros_ib
        if solo_cat:
            print(f"  En el catálogo y NO en Interbanking   : {sorted(solo_cat)}")
    else:
        print("  No hay ningún numero_cuenta cargado → el cruce automático no es posible.")
        print("  Comparar a ojo las dos listas de arriba: eso decide si el maestro de")
        print("  Interbanking puede reemplazar al descubrimiento por movimientos.")
    return cuentas


def _elegir(cuentas: list[dict], idx: int) -> dict | None:
    if not cuentas:
        print("\n✗ Sin cuentas, no hay nada más que probar.")
        return None
    if idx >= len(cuentas):
        print(f"\n✗ No existe la cuenta #{idx}; hay {len(cuentas)}.")
        return None
    c = cuentas[idx]
    print(f"\n>>> Cuenta elegida: #{idx} {c.get('bank_name')} {c.get('account_type')} "
          f"{c.get('currency')} nro={c.get('account_number')}")
    return c


def probar_saldos(c: dict, desde: str, hasta: str) -> None:
    _titulo("2. SALDOS")
    r = ib.saldos(
        c["account_number"], c["bank_number"],
        account_type=c.get("account_type") or "CC", currency=c.get("currency") or "ARS",
    )
    b = r.get("balances") or {}
    print("  Saldo actual:")
    print(f"     contable           : {b.get('countable_balance')}")
    print(f"     operativo actual   : {b.get('current_operating_balance')}")
    print(f"     operativo INICIAL  : {b.get('initial_operating_balance')}"
          f"   <-- el que hoy se carga a mano en tesoreria_saldos")
    print(f"     proyectado 24hs    : {b.get('projected_balance_24hs')}")
    print(f"     proyectado 48hs    : {b.get('projected_balance_48hs')}")

    h = ib.saldos(
        c["account_number"], c["bank_number"],
        account_type=c.get("account_type") or "CC", currency=c.get("currency") or "ARS",
        date_since=desde, date_until=hasta,
    ).get("historical_balances") or []
    print(f"\n  Histórico {desde} → {hasta}: {len(h)} días")
    for d in h[:15]:
        print(f"     {d.get('operation_date')}  saldo={d.get('day_balance')}"
              f"  créditos={d.get('total_credits')}  débitos={d.get('total_debits')}")
    if len(h) > 15:
        print(f"     … y {len(h) - 15} más")


def probar_extractos(c: dict, desde: str, hasta: str) -> None:
    _titulo("3. EXTRACTOS (conciliación)")
    dias = ib.extractos(
        c["account_number"], c["bank_number"], desde, hasta,
        account_type=c.get("account_type") or "CC", currency=c.get("currency") or "ARS",
    ).get("statements") or []
    print(f"  {len(dias)} días entre {desde} y {hasta}\n")
    descuadres = 0
    for d in dias:
        ap = d.get("opening_balance") or 0
        ci = d.get("ending_balance") or 0
        cr = d.get("credits_total_amount") or 0
        de = d.get("debits_total_amount") or 0
        dif = round(ap + cr - de - ci, 2)
        marca = "" if abs(dif) < 0.01 else f"   <-- NO CIERRA por {dif}"
        if marca:
            descuadres += 1
        print(f"     {d.get('operation_date')}  ap={ap}  cr={cr}  de={de}  ci={ci}"
              f"  movs={d.get('total_movements')}{marca}")
    print(f"\n  Días que no cierran: {descuadres} de {len(dias)}")
    if descuadres:
        print("  Si hay descuadres, el extracto NO sirve como fuente de conciliación")
        print("  tal cual viene — habría que entender qué no está sumando.")


def probar_movimientos(c: dict, desde: str, hasta: str) -> None:
    _titulo("4. MOVIMIENTOS (v2 del día, y v1 para ver si trae ID estable)")
    base = dict(
        account_type=c.get("account_type") or "CC",
        currency=c.get("currency") or "ARS",
    )
    m2 = ib.movimientos(c["account_number"], c["bank_number"], "dia", **base)
    det2 = m2.get("movements_detail") or []
    print(f"  v2 / dia        : {len(det2)} movimientos")
    for x in det2[:10]:
        print(f"     {x.get('debit_credit_type')} {x.get('amount')}"
              f" | {x.get('code_description_bank') or x.get('code_description_ib')}"
              f" | cuit={x.get('customer_cuit')}"
              f" | {x.get('depositor_description')}"
              f" | comp={x.get('voucher_number')}")

    m1 = ib.movimientos(c["account_number"], c["bank_number"], "dia", version="v1", **base)
    det1 = m1.get("movements_detail") or []
    ids = [x.get("id") for x in det1 if x.get("id") is not None]
    print(f"\n  v1 / dia        : {len(det1)} movimientos, {len(ids)} con campo `id`")
    if ids:
        print(f"     ids: {ids[:20]}")
        print(f"     ¿únicos? {'SÍ' if len(set(ids)) == len(ids) else 'NO — hay repetidos'}")
        print("     Correr este diag DOS veces y comparar: si los ids no cambian entre")
        print("     corridas, sirven como clave de idempotencia al persistir.")
    else:
        print("     v1 no devolvió ids → no hay clave natural; habría que derivar una.")

    dif = ib.movimientos(c["account_number"], c["bank_number"], "diferidos", **base)
    print(f"\n  v2 / diferidos  : {len(dif.get('movements_detail') or [])} movimientos"
          f" (fechas futuras — hoy no tenemos esta información por ninguna otra fuente)")

    ant = ib.movimientos(
        c["account_number"], c["bank_number"], "anteriores",
        date_since=desde, date_until=hasta, **base
    )
    print(f"  v2 / anteriores : {len(ant.get('movements_detail') or [])} movimientos"
          f" entre {desde} y {hasta}")


def probar_transferencias(desde: str, hasta: str) -> None:
    _titulo("5. TRANSFERENCIAS")
    t = ib.transferencias(desde, hasta).get("transfers") or []
    print(f"  {len(t)} transferencias entre {desde} y {hasta}")
    estados = Counter((x.get("status") or "(sin estado)") for x in t)
    print(f"  Estados reales del campo `status`: {dict(estados)}")
    for x in t[:10]:
        print(f"     #{x.get('transfer_id')} {x.get('request_date')}"
              f" | {x.get('currency')} {x.get('amount')}"
              f" | {x.get('transfer_type_description')}"
              f" | {x.get('status')} | {x.get('account_label')}")

    v = ib.transferencias(desde, hasta, comprobantes=True).get("transfers") or []
    con_vep = [x for x in v if (x.get("afip") or {}).get("vep_number")]
    print(f"\n  {len(v)} comprobantes, {len(con_vep)} con VEP")
    for x in con_vep[:10]:
        a = x.get("afip") or {}
        print(f"     VEP {a.get('vep_number')} | {x.get('currency')} {x.get('amount')}"
              f" | {a.get('tax_description')} | período={a.get('fiscal_period')}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Smoke de las APIs de Interbanking")
    ap.add_argument("--cuenta", type=int, default=0, help="índice de la cuenta a probar a fondo")
    ap.add_argument("--dias", type=int, default=15, help="ventana de los históricos (máx 60)")
    ap.add_argument("--solo-cuentas", action="store_true", help="solo el inventario")
    ap.add_argument("--saldos-todas", action="store_true", help="saldo actual de TODAS las cuentas")
    args = ap.parse_args()

    hasta = date.today()
    desde = hasta - timedelta(days=min(args.dias, 60))
    d_desde, d_hasta = desde.isoformat(), hasta.isoformat()

    cuentas = inventario_cuentas()
    if args.solo_cuentas:
        return

    if args.saldos_todas:
        _titulo("SALDO ACTUAL DE TODAS LAS CUENTAS")
        for i, c in enumerate(cuentas):
            try:
                b = ib.saldos(
                    c["account_number"], c["bank_number"],
                    account_type=c.get("account_type") or "CC",
                    currency=c.get("currency") or "ARS",
                ).get("balances") or {}
                print(f"  #{i:<3} {c.get('bank_name'):<28} {c.get('currency')}"
                      f"  inicial={b.get('initial_operating_balance')}"
                      f"  actual={b.get('current_operating_balance')}")
            except ib.InterbankingError as e:
                print(f"  #{i:<3} {c.get('bank_name'):<28} ✗ {e}")
        return

    c = _elegir(cuentas, args.cuenta)
    if not c:
        return

    for paso, fn in (
        ("saldos", lambda: probar_saldos(c, d_desde, d_hasta)),
        ("extractos", lambda: probar_extractos(c, d_desde, d_hasta)),
        ("movimientos", lambda: probar_movimientos(c, d_desde, d_hasta)),
        ("transferencias", lambda: probar_transferencias(d_desde, d_hasta)),
    ):
        try:
            fn()
        except ib.InterbankingError as e:
            print(f"\n  ✗ {paso}: {e}")


if __name__ == "__main__":
    main()
