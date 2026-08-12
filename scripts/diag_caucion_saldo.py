"""diag_caucion_saldo.py — por qué el CASH (ARS/USD) aparece negativo por una caución.

El síntoma: una caución que vence dentro de un mes ya deja el saldo de ARS en
negativo HOY. La pregunta: ¿el `Acumulado` que devuelve Aunesa es el saldo AL DÍA,
o ya tiene neteado lo que va a liquidar en el futuro?

Eso NO se puede deducir del código — hay que mirar la respuesta cruda. Este diag
la desarma para las unidades de CASH de una cuenta y prueba la MISMA fecha con y
sin `hasta`, que es justamente la hipótesis a testear.

Para cada sonda imprime:
  · el `Acumulado` de cada unidad de cash (el número que termina en la vista);
  · TODOS los movimientos pendientes de cash, con su fecha de liquidación;
  · cuáles liquidan DESPUÉS del horizonte pedido — si el Acumulado ya los
    contiene, ESE es el origen del negativo;
  · las filas de caución específicamente, con su fecha;
  · la aritmética: Acumulado − (lo que liquida después del horizonte) = el saldo
    que la vista DEBERÍA mostrar.

Read-only: solo hace GET a Aunesa, no escribe una sola fila.

Uso:
    python -m scripts.diag_caucion_saldo --cuenta 805
    python -m scripts.diag_caucion_saldo --cuenta 805 --unidades ARS,USD,USDC
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime

from core.calendario import proximo_habil
from jobs.aum import _SESSION, POSICION_URL, autenticar, obtener_cuentas
from jobs.portafolio_backfill import _PARAMS_BASE

_HDR: dict = {}


def _sep(t: str) -> None:
    print("\n" + "=" * 100)
    print(t)
    print("=" * 100)


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _traer(idc: str, desde: date, hasta: date | None) -> list:
    """GET posicionValuada. `hasta=None` = mandar el `hasta` vacío del job viejo."""
    params = {**_PARAMS_BASE, "desde": _ddmmyyyy(desde),
              "hasta": _ddmmyyyy(hasta) if hasta else ""}
    resp = _SESSION.get(POSICION_URL.format(idc), params=params,
                        headers=_HDR["h"], timeout=120)
    if resp.status_code == 401:
        _HDR["h"] = autenticar()
        resp = _SESSION.get(POSICION_URL.format(idc), params=params,
                            headers=_HDR["h"], timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def _parse_fecha(v) -> date | None:
    """La respuesta mezcla formatos — se prueban los dos que aparecen."""
    s = str(v or "")[:19]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:len(datetime.now().strftime(fmt))], fmt).date()
        except ValueError:
            continue
    return None


def sonda(idc: str, etiqueta: str, desde: date, hasta: date | None,
          unidades: set[str], horizonte: date) -> None:
    _sep(f"{etiqueta}   desde={_ddmmyyyy(desde)}   "
         f"hasta={_ddmmyyyy(hasta) if hasta else '(vacío)'}")
    try:
        raw = _traer(idc, desde, hasta)
    except Exception as e:
        print(f"  ERROR: {e}")
        return
    print(f"  filas totales en la respuesta: {len(raw)}")

    cash = [r for r in raw if isinstance(r, dict) and (r.get("unidad") or "") in unidades]
    if not cash:
        print(f"  (ninguna fila de {sorted(unidades)} — ¿la cuenta no tiene cash?)")
        return

    acum = [r for r in cash if r.get("informacion") == "Acumulado"]
    pend = [r for r in cash if r.get("informacion") != "Acumulado"]

    print("\n  ── ACUMULADO por unidad (ES el número que termina en la vista) ──")
    tot_acum: dict[str, float] = {}
    for r in acum:
        u = r["unidad"]
        # El job invierte el signo (`cantidad × -1`) — se replica para comparar
        # contra lo que se ve en pantalla, no contra el crudo.
        tot_acum[u] = tot_acum.get(u, 0.0) + _f(r.get("cantidad")) * -1
    for u, v in sorted(tot_acum.items()):
        print(f"    {u:<8} {v:>24,.2f}")

    print(f"\n  ── MOVIMIENTOS PENDIENTES de cash ({len(pend)}) ──")
    if not pend:
        print("    (ninguno)")
    else:
        print(f"    {'UNIDAD':<8} {'LIQUIDA':<12} {'¿DESPUÉS DEL':<14} "
              f"{'CANTIDAD':>20}  INFORMACIÓN")
        print(f"    {'':<8} {'':<12} {'HORIZONTE?':<14} {'':>20}")
        print("    " + "-" * 96)
    fuera: dict[str, float] = {}
    for r in sorted(pend, key=lambda x: str(x.get("fecha") or "")):
        u = r.get("unidad") or ""
        f = _parse_fecha(r.get("fecha"))
        cant = _f(r.get("cantidad")) * -1
        despues = bool(f and f > horizonte)
        if despues:
            fuera[u] = fuera.get(u, 0.0) + cant
        print(f"    {u:<8} {(f.isoformat() if f else '—'):<12} "
              f"{('SÍ ←' if despues else 'no'):<14} {cant:>20,.2f}  "
              f"{str(r.get('informacion'))[:46]}")

    cauciones = [r for r in raw if isinstance(r, dict)
                 and "cauci" in str(r.get("informacion") or "").lower()]
    print(f"\n  ── FILAS DE CAUCIÓN en TODA la respuesta ({len(cauciones)}) ──")
    for r in cauciones:
        f = _parse_fecha(r.get("fecha"))
        print(f"    unidad={r.get('unidad')!s:<8} liquida={(f.isoformat() if f else '—'):<12} "
              f"cant={_f(r.get('cantidad')) * -1:>18,.2f}  "
              f"{str(r.get('informacion'))[:52]}")

    print(f"\n  ── ARITMÉTICA (horizonte = {horizonte.isoformat()}) ──")
    if not fuera:
        print("    Ningún movimiento de cash liquida después del horizonte.")
        print("    → si el saldo igual sale negativo, NO viene de un pendiente futuro:")
        print("      el Acumulado ya lo trae adentro y hay que resolverlo de otra forma.")
    for u in sorted(set(tot_acum) | set(fuera)):
        a, x = tot_acum.get(u, 0.0), fuera.get(u, 0.0)
        print(f"    {u:<8} Acumulado {a:>22,.2f}   "
              f"liquida DESPUÉS {x:>20,.2f}   "
              f"saldo al horizonte {a - x:>22,.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cuenta", default="805", help="id_cuenta a auditar")
    ap.add_argument("--unidades", default="ARS,USD,USDC",
                    help="unidades de cash a mirar, separadas por coma")
    args = ap.parse_args()
    unidades = {u.strip() for u in args.unidades.split(",") if u.strip()}

    print(f"DIAG CAUCIÓN / SALDO CASH — cuenta {args.cuenta} — "
          f"{datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")

    _HDR["h"] = autenticar()
    denom = ""
    for c in obtener_cuentas(_HDR["h"]):
        if str(c.get("id") or c.get("idCuenta") or "") == str(args.cuenta):
            denom = c.get("denominacion") or ""
            break
    print(f"cuenta: [{args.cuenta}] {denom}")

    hoy = date.today()
    t0 = proximo_habil(hoy)          # posición liquidada A HOY
    t1 = proximo_habil(t0)           # posición liquidada a MAÑANA (la que usa la vista)

    print(f"\nhoy={hoy.isoformat()}   desde t0={_ddmmyyyy(t0)}   desde t1={_ddmmyyyy(t1)}")
    print("Regla H1: `desde = X` devuelve la posición liquidada al hábil ANTERIOR a X.")

    # El experimento: la MISMA fecha, con y sin `hasta`. Si el `hasta` acota, los
    # movimientos de dentro de un mes tienen que desaparecer en la segunda.
    sonda(args.cuenta, "1) T1 con `hasta` VACÍO (como el job diario viejo)",
          t1, None, unidades, horizonte=hoy)
    sonda(args.cuenta, "2) T1 con `hasta` = `desde` (como quedó el daemon)",
          t1, t1, unidades, horizonte=hoy)
    sonda(args.cuenta, "3) T0 con `hasta` = `desde`",
          t0, t0, unidades, horizonte=hoy)

    _sep("CÓMO LEERLO")
    print("""
  · Si en (1) hay movimientos con 'DESPUÉS DEL HORIZONTE = SÍ' y en (2) NO, el
    `hasta` SÍ acota y el problema ya está resuelto en el daemon.

  · Si el Acumulado es IGUAL en (1) y en (2), el `hasta` no cambia nada: Aunesa
    ya nos da el saldo neteado con el futuro adentro. Ahí la solución no es la
    fecha del request sino restarle a mano los pendientes futuros — que es
    exactamente lo que imprime el bloque ARITMÉTICA.

  · Las filas de CAUCIÓN dicen en qué fecha liquida cada pata. Si la fecha está a
    un mes y su importe ya está dentro del Acumulado, ESE es el origen del
    negativo de hoy.
""")


if __name__ == "__main__":
    main()
