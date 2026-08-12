"""diag_caucion_saldo.py — encontrar los parámetros que dan el SALDO REAL del día.

El síntoma: una caución que vence dentro de un mes ya deja el ARS en negativo HOY.
El job manda solo cuatro parámetros (`desde`, `hasta`, `tipoCuenta`, `nivel`,
`ocultarCerradas`) y `posicionValuada` acepta VARIOS más que nunca probamos:

    porConcertacion · estado · lugar · tipoTitulo · especie

`porConcertacion` es el sospechoso principal: si la posición se arma por
LIQUIDACIÓN, lo que liquida dentro de un mes ya está adentro del `Acumulado` y por
eso el saldo sale negativo hoy. Por concertación debería mostrar lo que realmente
pasó.

Este diag NO calcula nada ni corrige a mano: **barre combinaciones de parámetros y
muestra el Acumulado de cash que devuelve cada una**, para elegir la que da el
número correcto. Después esa combinación se copia al daemon y listo.

Read-only: solo GETs a Aunesa.

Uso:
    python -m scripts.diag_caucion_saldo --cuenta 805
    python -m scripts.diag_caucion_saldo --cuenta 805 --detalle 3   # explota la variante 3
    python -m scripts.diag_caucion_saldo --cuenta 805 --extra "porConcertacion=false,lugar=Local"
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime

from core.calendario import proximo_habil
from jobs.aum import _SESSION, POSICION_URL, autenticar, obtener_cuentas
from jobs.portafolio_backfill import _PARAMS_BASE

_HDR: dict = {}
_CASH_DEFAULT = "ARS,USD,USDC"


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


def _parse_fecha(v) -> date | None:
    s = str(v or "")[:19]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:len(datetime.now().strftime(fmt))], fmt).date()
        except ValueError:
            continue
    return None


def _get(idc: str, params: dict) -> tuple[int, list]:
    """GET posicionValuada con los params dados. Devuelve (status, filas)."""
    resp = _SESSION.get(POSICION_URL.format(idc), params=params,
                        headers=_HDR["h"], timeout=180)
    if resp.status_code == 401:
        _HDR["h"] = autenticar()
        resp = _SESSION.get(POSICION_URL.format(idc), params=params,
                            headers=_HDR["h"], timeout=180)
    if resp.status_code != 200:
        return resp.status_code, []
    data = resp.json()
    return 200, (data if isinstance(data, list) else [])


def _cash_acumulado(raw: list, unidades: set[str]) -> dict[str, float]:
    """Acumulado por unidad de cash, con el MISMO signo invertido que el job."""
    out: dict[str, float] = {}
    for r in raw:
        if not isinstance(r, dict) or r.get("informacion") != "Acumulado":
            continue
        u = r.get("unidad") or ""
        if u in unidades:
            out[u] = out.get(u, 0.0) + _f(r.get("cantidad")) * -1
    return out


def _pendientes_cash(raw: list, unidades: set[str], hoy: date) -> tuple[int, int]:
    """(pendientes de cash, cuántos liquidan DESPUÉS de hoy)."""
    n = fut = 0
    for r in raw:
        if not isinstance(r, dict) or r.get("informacion") == "Acumulado":
            continue
        if (r.get("unidad") or "") not in unidades:
            continue
        n += 1
        f = _parse_fecha(r.get("fecha"))
        if f and f > hoy:
            fut += 1
    return n, fut


def _variantes(t0: date, t1: date) -> list[tuple[str, dict]]:
    """Cada variante = los params del job + lo que se quiera cambiar.

    El orden importa para leer la tabla: primero lo que hace HOY el sistema,
    después de a un parámetro por vez para que se vea CUÁL mueve el número.
    """
    base_t1 = {**_PARAMS_BASE, "desde": _ddmmyyyy(t1), "hasta": _ddmmyyyy(t1)}
    base_t0 = {**_PARAMS_BASE, "desde": _ddmmyyyy(t0), "hasta": _ddmmyyyy(t0)}
    return [
        ("T1 · como el daemon HOY",            base_t1),
        ("T1 · hasta VACÍO (job diario)",      {**base_t1, "hasta": ""}),
        ("T1 · porConcertacion=true",          {**base_t1, "porConcertacion": "true"}),
        ("T1 · porConcertacion=false",         {**base_t1, "porConcertacion": "false"}),
        ("T1 · estado=DIS",                    {**base_t1, "estado": "DIS"}),
        ("T1 · porConcert=true + estado=DIS",  {**base_t1, "porConcertacion": "true",
                                                "estado": "DIS"}),
        ("T1 · lugar=Local",                   {**base_t1, "lugar": "Local"}),
        ("T0 · como el daemon HOY",            base_t0),
        ("T0 · porConcertacion=true",          {**base_t0, "porConcertacion": "true"}),
    ]


def detalle(idc: str, etiqueta: str, params: dict, unidades: set[str], hoy: date) -> None:
    _sep(f"DETALLE — {etiqueta}")
    print("  params: " + " · ".join(f"{k}={v!r}" for k, v in sorted(params.items())))
    st, raw = _get(idc, params)
    if st != 200:
        print(f"  HTTP {st}")
        return
    print(f"  filas: {len(raw)}\n")

    cash = [r for r in raw if isinstance(r, dict) and (r.get("unidad") or "") in unidades]
    acum = [r for r in cash if r.get("informacion") == "Acumulado"]
    pend = [r for r in cash if r.get("informacion") != "Acumulado"]

    print("  ── ACUMULADO de cash (el número que termina en la vista) ──")
    for u, v in sorted(_cash_acumulado(raw, unidades).items()):
        print(f"    {u:<8} {v:>24,.2f}")
    print(f"    (filas Acumulado de cash: {len(acum)})")

    print(f"\n  ── PENDIENTES de cash ({len(pend)}) ──")
    if pend:
        print(f"    {'UNIDAD':<8} {'LIQUIDA':<12} {'FUTURO?':<9} {'CANTIDAD':>20}  INFORMACIÓN")
        print("    " + "-" * 92)
        for r in sorted(pend, key=lambda x: str(x.get("fecha") or "")):
            f = _parse_fecha(r.get("fecha"))
            print(f"    {r.get('unidad')!s:<8} {(f.isoformat() if f else '—'):<12} "
                  f"{('SÍ ←' if (f and f > hoy) else 'no'):<9} "
                  f"{_f(r.get('cantidad')) * -1:>20,.2f}  {str(r.get('informacion'))[:44]}")
    else:
        print("    (ninguno)")

    cauciones = [r for r in raw if isinstance(r, dict)
                 and "cauci" in str(r.get("informacion") or "").lower()]
    print(f"\n  ── FILAS DE CAUCIÓN en toda la respuesta ({len(cauciones)}) ──")
    for r in cauciones:
        f = _parse_fecha(r.get("fecha"))
        print(f"    unidad={r.get('unidad')!s:<8} liquida={(f.isoformat() if f else '—'):<12} "
              f"cant={_f(r.get('cantidad')) * -1:>18,.2f}  {str(r.get('informacion'))[:50]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cuenta", default="805")
    ap.add_argument("--unidades", default=_CASH_DEFAULT,
                    help=f"unidades de cash a mirar (default {_CASH_DEFAULT})")
    ap.add_argument("--detalle", type=int, default=None, metavar="N",
                    help="explota la variante N (número de la primera columna)")
    ap.add_argument("--extra", default="",
                    help="params extra propios, 'k=v,k=v' — se agregan como una variante más")
    args = ap.parse_args()
    unidades = {u.strip() for u in args.unidades.split(",") if u.strip()}

    print(f"DIAG CAUCIÓN / SALDO CASH — cuenta {args.cuenta} — "
          f"{datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")

    _HDR["h"] = autenticar()
    # obtener_cuentas devuelve un DataFrame (id, denominacion), no una lista de dicts.
    df = obtener_cuentas(_HDR["h"])
    denom = next((str(r["denominacion"]) for _, r in df.iterrows()
                  if str(r["id"]) == str(args.cuenta)), "")
    print(f"cuenta: [{args.cuenta}] {denom}")

    hoy = date.today()
    t0 = proximo_habil(hoy)
    t1 = proximo_habil(t0)
    print(f"hoy={hoy.isoformat()}   desde T0={_ddmmyyyy(t0)}   desde T1={_ddmmyyyy(t1)}")
    print("Regla H1: `desde = X` devuelve la posición liquidada al hábil ANTERIOR a X.")
    print(f"\nEl job manda SOLO estos params: {sorted(_PARAMS_BASE)}")
    print("`posicionValuada` acepta además: porConcertacion · estado · lugar · "
          "tipoTitulo · especie")

    variantes = _variantes(t0, t1)
    if args.extra:
        extra = dict(kv.split("=", 1) for kv in args.extra.split(",") if "=" in kv)
        variantes.append((f"T1 · {args.extra}",
                          {**_PARAMS_BASE, "desde": _ddmmyyyy(t1),
                           "hasta": _ddmmyyyy(t1), **extra}))

    if args.detalle is not None:
        i = args.detalle - 1
        if not 0 <= i < len(variantes):
            print(f"\n--detalle fuera de rango (hay {len(variantes)} variantes)")
            return
        detalle(args.cuenta, variantes[i][0], variantes[i][1], unidades, hoy)
        return

    _sep("BARRIDO DE PARÁMETROS — el Acumulado de cash que devuelve cada combinación")
    cols = sorted(unidades)
    hdr = f"  {'#':<3} {'VARIANTE':<34} {'HTTP':>5} {'FILAS':>7}"
    for u in cols:
        hdr += f" {u:>20}"
    hdr += f" {'PEND':>6} {'FUT':>5}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    for i, (etiqueta, params) in enumerate(variantes, 1):
        try:
            st, raw = _get(args.cuenta, params)
        except Exception as e:
            print(f"  {i:<3} {etiqueta:<34} {'ERR':>5}   {type(e).__name__}")
            continue
        if st != 200:
            print(f"  {i:<3} {etiqueta:<34} {st:>5}")
            continue
        acum = _cash_acumulado(raw, unidades)
        npend, nfut = _pendientes_cash(raw, unidades, hoy)
        line = f"  {i:<3} {etiqueta:<34} {st:>5} {len(raw):>7}"
        for u in cols:
            line += f" {acum.get(u, 0.0):>20,.2f}"
        line += f" {npend:>6} {nfut:>5}"
        print(line)

    print("\n  PEND = movimientos de cash pendientes · FUT = de esos, los que liquidan "
          "después de hoy.")

    _sep("CÓMO LEERLO")
    print("""
  Mirá la columna ARS y buscá la fila cuyo número coincide con el saldo REAL de la
  cuenta hoy. Esa combinación de parámetros es la respuesta — no hay nada que
  calcular ni corregir después.

  · Si la 3 (porConcertacion=true) da el número correcto y la 1 no, el problema era
    que la posición se pedía por LIQUIDACIÓN y la caución del mes que viene ya
    estaba adentro. Se agrega ese parámetro al daemon y se termina.

  · Si una variante devuelve HTTP 400, ese parámetro no existe o el valor no es el
    esperado — se descarta y listo.

  · Si TODAS dan el mismo ARS, ningún parámetro cambia el Acumulado: ahí sí habría
    que restar los pendientes futuros a mano (mirá el detalle con --detalle N para
    ver cuáles son).

  Para ver una variante completa, fila por fila:
      python -m scripts.diag_caucion_saldo --cuenta 805 --detalle 3

  Y si conocés otros parámetros, se prueban sin tocar el código:
      python -m scripts.diag_caucion_saldo --cuenta 805 --extra "porConcertacion=true,lugar=Local"
""")


if __name__ == "__main__":
    main()
