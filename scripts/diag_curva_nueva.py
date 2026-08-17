"""scripts/diag_curva_nueva.py — ¿1816 puede VALUAR los bonos de un ajuste que
todavía no tiene curva en la app? Doc madre: `docs/AV_AGENT.md` (E1.f).

**La pregunta que contesta, y por qué bloquea todo lo demás.** El AV Agent detectó
que `badlar`, `tpm` y `caucion` no tienen pill: sus bonos están cargados y no
aparecen en NINGUNA pantalla. Para darles curva hay que decidir **de dónde sale su
tasa**, y hay exactamente dos caminos con costos muy distintos:

  · **1816 la publica** → se trae, igual que `jobs/tamar_1816`. Es CONFIGURACIÓN:
    qué tickers pedir y a qué tabla. No hay matemática que escribir.
  · **1816 NO la publica** → hay que valuarla nosotros, o sea escribir la rama de
    cálculo en `engines/curvas.py`. Eso es CÓDIGO y lo hace un humano.

Elegir sin medir es lo que la REGLA #2 prohíbe: publicar el INSTRUMENTO no es lo
mismo que publicar su TASA. Con los TAMAR ya pasó — de los 9 corporativos con pata
TAMAR, 1816 solo cubría ZPC1O (ver `docs/RENTA_FIJA.md` paso 18).

**Es READ-ONLY**: no escribe una fila en ningún lado.

⚠ COSTO: `/indicadores` cuesta **tickers × campos**. Con el default (6 campos) y
~15 tickers son ~90 créditos de los 100.000 diarios. El script imprime el gasto
medido, no estimado.

Uso:
    python -m scripts.diag_curva_nueva                      # los tres sin curva
    python -m scripts.diag_curva_nueva --ajuste badlar      # uno solo
    python -m scripts.diag_curva_nueva --fecha 2026-08-15   # forzar una rueda
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging

from core import curvas_ejes, curvas_sql, mercado_1816

logger = logging.getLogger(__name__)

# Los mismos campos que pide `jobs/tamar_1816`: si esto sale bien, ese job es el
# molde exacto y no hay que inventar nada.
_CAMPOS = ("tea", "tna", "spread", "precioClean", "duration", "paridad")
_MAX_RETROCESO = 5


def _habil_anterior(d: dt.date) -> dt.date:
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def _mis_bonos_del_ajuste(ajuste: str) -> list[dict]:
    """Los bonos de `mercado.curvas` con ese ajuste (en cualquiera de sus dos
    patas: un dual también cuenta)."""
    out = []
    for d in curvas_sql.cargar_todos():
        ejes = curvas_ejes.ejes_de_doc(d)
        if ejes and ajuste in (ejes.ajuste, ejes.ajuste_alt):
            out.append(d)
    return out


def _sondear(ajuste: str, fecha: dt.date) -> None:
    bonos = _mis_bonos_del_ajuste(ajuste)
    print(f"\n{'=' * 72}\nAJUSTE «{ajuste}» — {len(bonos)} bono(s) en mercado.curvas")
    print("=" * 72)
    if not bonos:
        print("  (ninguno: no hay nada que valuar todavía)")
        return

    # 1816 usa el ticker BASE (sin la especie D/C) — la normalización vive en el
    # cliente, que es la convención del proveedor.
    pedidos = {mercado_1816.normalizar_ticker(d.get("ticker_corto")): d.get("ticker_corto")
               for d in bonos if d.get("ticker_corto")}
    pedidos.pop("", None)
    tickers = sorted(pedidos)
    print(f"  Se le van a pedir a 1816: {', '.join(tickers)}")
    print(f"  Costo: {len(tickers)} × {len(_CAMPOS)} = ~{len(tickers) * len(_CAMPOS)} créditos")

    # SIEMPRE con fechaOperacion explícita: sin ella la API usa HOY y un día sin
    # rueda devuelve todos los campos en null — eso hizo parecer que el campo
    # `spread` no existía (incidente 2026-08-16, RENTA_FIJA paso 18).
    d = _habil_anterior(fecha)
    inst: dict = {}
    for _ in range(_MAX_RETROCESO + 1):
        try:
            resp = mercado_1816.indicadores(tickers, list(_CAMPOS),
                                            fecha_operacion=d.isoformat())
        except Exception as e:
            print(f"  ✘ la llamada falló: {e}")
            return
        inst = resp.get("instrumentos") or {}
        if any((v or {}).get("tea") is not None for v in inst.values()):
            break
        d = _habil_anterior(d - dt.timedelta(days=1))
    print(f"  Rueda usada: {d.isoformat()}")

    print(f"\n  {'TICKER':<12}{'TEA':>10}{'TNA':>10}{'SPREAD':>10}{'PRECIO':>10}"
          f"{'DURATION':>10}{'PARIDAD':>10}")
    print("  " + "─" * 72)
    def _celda(v: dict, k: str) -> str:
        x = v.get(k)
        return f"{x:>10.4f}" if isinstance(x, (int, float)) else f"{'—':>10}"

    con_tea = 0
    for tk in tickers:
        v = inst.get(tk) or {}
        if v.get("tea") is not None:
            con_tea += 1
        print(f"  {tk:<12}" + "".join(
            _celda(v, k) for k in ("tea", "tna", "spread", "precioClean",
                                   "duration", "paridad")))

    print("  " + "─" * 72)
    pct = 100 * con_tea / len(tickers) if tickers else 0
    print(f"  ➡ 1816 devuelve TEA para {con_tea}/{len(tickers)} ({pct:.0f}%)")
    if con_tea == len(tickers):
        print("     VEREDICTO: 1816 los cubre TODOS → la curva se puede valuar "
              "TRAYENDO la tasa, igual que los TAMAR. Es configuración: sumar el "
              "ajuste al job y darle su pill. NO hay matemática que escribir.")
    elif con_tea:
        print("     VEREDICTO: cobertura PARCIAL. Se puede traer lo que 1816 tiene "
              "y el resto queda con la celda vacía (mismo criterio que los 8 "
              "corporativos TAMAR sin dato), o se valúa por motor para todos.")
    else:
        print("     VEREDICTO: 1816 NO publica su tasa → la única opción es "
              "valuarla NOSOTROS (rama de cálculo en engines/curvas.py). Eso es "
              "CÓDIGO y no lo puede resolver una tabla de configuración.")
    print("     ⚠ La escala de `tea`/`spread` son FRACCIONES (0.0973 = 9,73%).")


def main() -> None:
    ap = argparse.ArgumentParser(description="¿1816 puede valuar un ajuste sin curva?")
    ap.add_argument("--ajuste", help="uno solo (default: todos los que no tienen curva)")
    ap.add_argument("--fecha", help="rueda YYYY-MM-DD (default: hoy)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not mercado_1816.disponible():
        print("✗ falta MERCADO_1816_API_KEY en el .env")
        return

    # Los ajustes SIN CURVA se derivan del propio sistema (`ajuste_sin_curva`),
    # no se listan a mano: el día que badlar tenga pill, este diag deja de
    # ofrecerlo solo.
    ajustes = ([args.ajuste] if args.ajuste
               else [a for a in curvas_ejes.AJUSTES if curvas_ejes.ajuste_sin_curva(a)])
    print("=" * 72)
    print("¿1816 PUEDE VALUAR LOS AJUSTES QUE NO TIENEN CURVA?")
    print("=" * 72)
    print(f"Ajustes a sondear: {', '.join(ajustes) or '(ninguno — todos tienen curva)'}")

    try:
        b0 = (mercado_1816.balance() or {}).get("daily", {}).get("used")
    except Exception:
        b0 = None

    fecha = dt.date.fromisoformat(args.fecha) if args.fecha else dt.date.today()
    for a in ajustes:
        _sondear(a, fecha)

    if b0 is not None:
        try:
            fin = (mercado_1816.balance() or {}).get("daily", {})
            if fin.get("used") is not None:
                print(f"\nCréditos usados: {fin['used'] - b0} (medido) · "
                      f"día {fin.get('used')}/{fin.get('limit')}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
