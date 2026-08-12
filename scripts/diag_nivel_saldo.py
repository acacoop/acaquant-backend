"""diag_nivel_saldo.py — buscar el NIVEL (o el endpoint) que da el saldo real de caja.

Continuación de `diag_caucion_saldo`. Ese barrido probó `porConcertacion`, `estado`
y `lugar`: **ninguno mueve el `Acumulado`** — los tres devuelven el mismo ARS. Y el
`hasta` que agregamos saca los movimientos pendientes de la LISTA pero no del
`Acumulado`, así que no cambia el número, solo esconde de qué está hecho.

Quedan dos cosas sin probar, y son las dos que este diag ataca:

  1. **`nivel`** — el job manda `"Especie x cuenta"` fijo desde siempre. Es un
     desplegable en la UI de Aunesa, así que tiene otros valores. Si alguno agrupa
     por MONEDA o da SALDOS, ahí está el número que buscamos.

  2. **Otros endpoints** — `posicionValuada` puede no ser el lugar. Se prueba un
     puñado de rutas hermanas y se reporta cuál responde 200.

Todo es GET y todo es descubrimiento: un `nivel` inválido devuelve 400/500 y se
descarta. No se asume que ninguno exista.

Uso:
    python -m scripts.diag_nivel_saldo --cuenta 805
    python -m scripts.diag_nivel_saldo --cuenta 805 --nivel "Saldos"   # probar uno puntual
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime

from core.calendario import proximo_habil
from jobs.aum import _SESSION, POSICION_URL, autenticar, obtener_cuentas
from jobs.portafolio_backfill import _PARAMS_BASE

_BASE = "https://aca.aunesa.com/Irmo/api"
_HDR: dict = {}
_CASH = {"ARS", "USD", "USDC"}

# Valores de `nivel` a tantear. NINGUNO está confirmado salvo el primero (el que
# usa el job). El resto son candidatos: se prueban y el que no exista dará != 200.
_NIVELES = [
    "Especie x cuenta",          # ← el único confirmado (lo manda el job)
    "Especie",
    "Cuenta",
    "Cuenta x especie",
    "Moneda",
    "Especie x moneda",
    "Saldos",
    "Saldo",
    "Resumen",
    "Total",
    "Consolidado",
]

# Rutas hermanas plausibles. Mismo criterio: se prueban, no se asumen.
_ENDPOINTS = [
    "cuentas/{idc}/saldos",
    "cuentas/{idc}/saldo",
    "cuentas/{idc}/disponibilidad",
    "cuentas/{idc}/disponible",
    "cuentas/{idc}/posicion",
    "cuentas/{idc}/tenencia",
    "cuentas/{idc}/cuentaCorriente",
    "cuentas/{idc}/movimientos",
]


def _sep(t: str) -> None:
    print("\n" + "=" * 100)
    print(t)
    print("=" * 100)


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _get(url: str, params: dict | None = None) -> tuple[int, object]:
    try:
        resp = _SESSION.get(url, params=params or {}, headers=_HDR["h"], timeout=180)
    except Exception as e:
        return -1, type(e).__name__
    if resp.status_code == 401:
        _HDR["h"] = autenticar()
        resp = _SESSION.get(url, params=params or {}, headers=_HDR["h"], timeout=180)
    if resp.status_code != 200:
        return resp.status_code, resp.text[:180]
    try:
        return 200, resp.json()
    except Exception:
        return 200, resp.text[:180]


def _cash_acum(raw) -> dict[str, float]:
    """ARS/USD/USDC del `Acumulado`, con el signo invertido que aplica el job."""
    out: dict[str, float] = {}
    if not isinstance(raw, list):
        return out
    for r in raw:
        if not isinstance(r, dict) or r.get("informacion") != "Acumulado":
            continue
        u = r.get("unidad") or ""
        if u in _CASH:
            out[u] = out.get(u, 0.0) + _f(r.get("cantidad")) * -1
    return out


def barrer_niveles(idc: str, desde: str, niveles: list[str]) -> None:
    _sep("1) BARRIDO DE `nivel` — el parámetro que el job nunca movió")
    print(f"  desde={desde}  ·  hasta={desde}\n")
    print(f"  {'NIVEL':<24} {'HTTP':>5} {'FILAS':>7} {'ARS':>20} {'USD':>16}   NOTA")
    print("  " + "-" * 96)
    for n in niveles:
        params = {**_PARAMS_BASE, "desde": desde, "hasta": desde, "nivel": n}
        st, data = _get(POSICION_URL.format(idc), params)
        if st != 200:
            nota = str(data)[:34].replace("\n", " ")
            print(f"  {n:<24} {st:>5} {'—':>7} {'—':>20} {'—':>16}   {nota}")
            continue
        filas = len(data) if isinstance(data, list) else 0
        acum = _cash_acum(data)
        print(f"  {n:<24} {st:>5} {filas:>7} "
              f"{acum.get('ARS', 0.0):>20,.2f} {acum.get('USD', 0.0):>16,.2f}")


def probar_endpoints(idc: str, desde: str) -> None:
    _sep("2) RUTAS HERMANAS — ¿el saldo vive en otro endpoint?")
    print(f"  {'RUTA':<34} {'HTTP':>5} {'TIPO':<10} {'N':>6}   PRIMERAS CLAVES / ERROR")
    print("  " + "-" * 96)
    for tpl in _ENDPOINTS:
        ruta = tpl.format(idc=idc)
        st, data = _get(f"{_BASE}/{ruta}", {"desde": desde, "hasta": desde})
        if st != 200:
            print(f"  {ruta:<34} {st:>5} {'—':<10} {'—':>6}   {str(data)[:38]}")
            continue
        if isinstance(data, list):
            claves = sorted((data[0] or {}).keys())[:6] if data and isinstance(
                data[0], dict) else []
            print(f"  {ruta:<34} {st:>5} {'list':<10} {len(data):>6}   {claves}")
        elif isinstance(data, dict):
            print(f"  {ruta:<34} {st:>5} {'dict':<10} {len(data):>6}   "
                  f"{sorted(data.keys())[:6]}")
        else:
            print(f"  {ruta:<34} {st:>5} {'otro':<10} {'—':>6}   {str(data)[:38]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cuenta", default="805")
    ap.add_argument("--nivel", default=None, help="probar SOLO este valor de `nivel`")
    ap.add_argument("--horizonte", choices=["t0", "t1"], default="t1")
    ap.add_argument("--sin-endpoints", action="store_true",
                    help="saltear el tanteo de rutas hermanas")
    args = ap.parse_args()

    print(f"DIAG NIVEL / SALDO — cuenta {args.cuenta} — "
          f"{datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")
    _HDR["h"] = autenticar()
    df = obtener_cuentas(_HDR["h"])
    denom = next((str(r["denominacion"]) for _, r in df.iterrows()
                  if str(r["id"]) == str(args.cuenta)), "")
    print(f"cuenta: [{args.cuenta}] {denom}")

    hoy = date.today()
    t0 = proximo_habil(hoy)
    d = t0 if args.horizonte == "t0" else proximo_habil(t0)
    desde = d.strftime("%d/%m/%Y")
    print(f"horizonte={args.horizonte}  desde={desde}\n")
    print("Ya descartado por el barrido anterior: porConcertacion, estado y lugar NO")
    print("cambian el Acumulado. El `hasta` tampoco — solo oculta los pendientes.")

    barrer_niveles(args.cuenta, desde, [args.nivel] if args.nivel else _NIVELES)
    if not args.sin_endpoints:
        probar_endpoints(args.cuenta, desde)

    _sep("CÓMO LEERLO")
    print("""
  · Un `nivel` con HTTP 200 y un ARS **distinto** al de "Especie x cuenta" es la
    pista: esa vista agrupa de otra forma y puede ser la del saldo real.

  · Un `nivel` con 200 y el MISMO ARS solo cambia el agrupamiento, no el número.

  · 400 / 500 = ese valor no existe. Se descarta y ya.

  · En las rutas hermanas, cualquier 200 vale la pena mirarlo: las claves que
    imprime dicen si trae saldos.

  Si conocés el nombre exacto del nivel, probalo directo:
      python -m scripts.diag_nivel_saldo --cuenta 805 --nivel "<el nombre>"

  Y si ninguno da el número: el `Acumulado` es un neteo de todo lo conocido y la
  única salida es restarle los pendientes futuros, que SE VEN pidiendo con el
  `hasta` VACÍO (con `hasta` no vienen). Ahí habría que revertir ese cambio.
""")


if __name__ == "__main__":
    main()
