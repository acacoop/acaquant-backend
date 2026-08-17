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


def _mis_bonos_del_ajuste(ajuste: str) -> list[str]:
    """Tickers de `mercado.curvas` con ese ajuste (en cualquiera de sus dos patas:
    un dual también cuenta)."""
    out = []
    for d in curvas_sql.cargar_todos():
        ejes = curvas_ejes.ejes_de_doc(d)
        tc = (d.get("ticker_corto") or "").strip().upper()
        if tc and ejes and ajuste in (ejes.ajuste, ejes.ajuste_alt):
            out.append(tc)
    return out


def _del_universo_1816(ajuste: str) -> list[str]:
    """Tickers que **1816** publica en curvas de ese ajuste.

    **Sin esto el veredicto no vale.** La primera corrida (2026-08-16) sondeó
    `badlar` con UN solo ticker —el único que ya estaba en `mercado.curvas`— y
    concluyó "1816 no lo publica". Con n=1, y encima un provincial ilíquido, eso
    no es una medición: es una anécdota. Los BADLAR que importan (TB27, TB31P,
    TD26) todavía NO están en nuestra base, así que el universo que hay que
    sondear es el de ELLOS, no el nuestro.

    Sale de `research.mkt_1816_instrumentos` —el catálogo que ya persiste
    `jobs/mercado_1816_discovery --catalogo`— así que **cuesta 0 créditos**. La
    curva se traduce a ejes con la misma tabla que usa todo el sistema.
    """
    curvas_del_ajuste = [nombre for nombre, ejes in curvas_ejes.EJES_1816.items()
                         if ajuste in (ejes.ajuste, ejes.ajuste_alt)]
    if not curvas_del_ajuste:
        return []
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT ticker FROM research.mkt_1816_instrumentos "
                        "WHERE curva = ANY(%s) AND activo", (curvas_del_ajuste,))
            return sorted({(r[0] or "").strip().upper() for r in cur.fetchall() if r[0]})
    except Exception as e:
        print(f"  (no pude leer el catálogo de 1816: {str(e)[:80]})")
        return []


def _sondear(ajuste: str, fecha: dt.date, tope: int) -> None:
    mios = _mis_bonos_del_ajuste(ajuste)
    suyos = _del_universo_1816(ajuste)
    print(f"\n{'=' * 72}\nAJUSTE «{ajuste}» — {len(mios)} en mercado.curvas · "
          f"{len(suyos)} en el catálogo de 1816")
    print("=" * 72)

    # El universo a sondear son LOS DOS: los nuestros (¿1816 los cubre?) y los de
    # 1816 que todavía no tenemos (que son justo los que se querrían dar de alta).
    # 1816 usa el ticker BASE, sin la especie D/C.
    tickers = sorted({mercado_1816.normalizar_ticker(t) for t in (*mios, *suyos)} - {""})
    if not tickers:
        print("  (ninguno de los dos lados: no hay nada que valuar todavía)")
        return
    if len(tickers) > tope:
        print(f"  ⚠ {len(tickers)} tickers — se sondean los primeros {tope} "
              f"(subilo con --tope si querés el universo entero)")
        tickers = tickers[:tope]
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
    if len(tickers) < 3:
        print(f"     ⚠ MUESTRA DE {len(tickers)}: no alcanza para concluir nada sobre "
              "el ajuste. Un solo ticker ilíquido dice más del ticker que de 1816.")
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
    ap.add_argument("--tope", type=int, default=25,
                    help="máximo de tickers por ajuste (default 25 = ~150 créditos)")
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
        _sondear(a, fecha, args.tope)

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
