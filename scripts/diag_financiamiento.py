"""diag_financiamiento.py — verifica en PROD los supuestos de la vista FINANCIAMIENTO.

REGLA #2: la vista se construyó sobre tres cosas que NO se pueden medir desde el
repo. Este diag las mide (read-only, sin escribir nada):

  1. UNIVERSO — cuántos assets tienen cartera FINANCIAMIENTO y cuántos siguen
     vigentes (vencimiento >= hoy). Si el `vencimiento` de alguno no está en ISO,
     la vista lo descarta en silencio: acá se cuenta y se muestra.
  2. PAYLOAD — cuántas filas cuenta × instrumento devuelve realmente el endpoint.
     Si se acerca a `_MAX_FILAS` hay que paginar o agregar server-side.
  3. TASA — el número que importa: qué porcentaje de las filas resuelve tasa por
     el match (id_cuenta, código). Si da bajo, el puente assets.ticker ↔ corchete
     de `informacion` no es el correcto y hay que revisarlo ANTES de creerle a la
     columna TASA de la pantalla.

Uso:
    python -m scripts.diag_financiamiento
    python -m scripts.diag_financiamiento --muestras 20
"""
from __future__ import annotations

import argparse
from datetime import date

from api.services import financiamiento as fin
from core.postgres import get_pool


def _fila(label: str, valor) -> None:
    print(f"  {label:.<46} {valor}")


def universo(hoy: str) -> None:
    print("\n1) UNIVERSO — portafolio.assets")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FILTER (WHERE upper(btrim(cartera)) = %(c)s), "
            "       count(*) FILTER (WHERE upper(btrim(cartera)) = %(c)s "
            "                          AND vencimiento ~ %(iso)s), "
            "       count(*) FILTER (WHERE upper(btrim(cartera)) = %(c)s "
            "                          AND vencimiento ~ %(iso)s "
            "                          AND vencimiento::date >= %(hoy)s) "
            "FROM portafolio.assets",
            {"c": fin.CARTERA, "iso": fin._RE_ISO, "hoy": hoy})
        total, iso, vigentes = cur.fetchone()
        _fila(f"assets con cartera {fin.CARTERA}", total)
        _fila("...con vencimiento en ISO YYYY-MM-DD", iso)
        _fila("...VIGENTES (vto >= hoy) → universo de la vista", vigentes)
        if total and iso < total:
            print(f"  ⚠ {total - iso} asset(s) con `vencimiento` en OTRO formato — "
                  f"la vista NO los muestra. Ejemplos:")
            cur.execute(
                "SELECT unidad, vencimiento FROM portafolio.assets "
                "WHERE upper(btrim(cartera)) = %(c)s AND NOT (vencimiento ~ %(iso)s) "
                "LIMIT 8", {"c": fin.CARTERA, "iso": fin._RE_ISO})
            for u, v in cur.fetchall():
                print(f"      {u[:60]!r} → vencimiento={v!r}")


def payload(hoy: str, muestras: int) -> dict:
    print("\n2) PAYLOAD — lo que devuelve el endpoint")
    # `libro` está @cached → se invoca con kwargs (regla de api/CLAUDE.md).
    d = fin.libro(scope=None, hoy=hoy)
    _fila("snapshot de tenencia usado (fecha)", d["fecha"])
    _fila("filas (cuenta × instrumento)", d["n"])
    _fila(f"truncado (tope {fin._MAX_FILAS})", d["truncado"])
    if d["truncado"]:
        print("  ⚠ El libro NO entra en un payload — hay que paginar o agregar server-side.")
    filas = d["filas"]
    _fila("cuentas distintas", len({f["id_cuenta"] for f in filas}))
    _fila("instrumentos distintos", len({f["unidad"] for f in filas}))
    monedas: dict[str, int] = {}
    for f in filas:
        monedas[f["moneda"] or "(vacía)"] = monedas.get(f["moneda"] or "(vacía)", 0) + 1
    _fila("monedas presentes", monedas or "—")
    _fila("filas dentro del AuM (aum='si')", sum(1 for f in filas if f["aum"]))
    if muestras and filas:
        print(f"\n  primeras {min(muestras, len(filas))} filas:")
        for f in filas[:muestras]:
            tasa = "—" if f["tasa"] is None else f"{f['tasa']:.3f}%"
            print(f"    {f['vencimiento']}  {f['ticker'][:18]:<18} "
                  f"{f['cuenta'][:28]:<28} cant={f['cantidad']:>16,.2f} "
                  f"{f['moneda']:<4} tasa={tasa}")
    return d


def cobertura_tasa(d: dict, muestras: int) -> None:
    print("\n3) TASA — cobertura del match (id_cuenta, código)")
    filas = d["filas"]
    if not filas:
        print("  sin filas: nada que medir.")
        return
    con = d["con_tasa"]
    pct = 100.0 * con / len(filas)
    _fila("filas con tasa resuelta", f"{con}/{len(filas)}  ({pct:.1f}%)")
    disp = [f for f in filas
            if f["tasa_min"] is not None and f["tasa_max"] is not None
            and f["tasa_max"] - f["tasa_min"] > 0.001]
    _fila("filas cuyo promedio esconde dispersión", len(disp))
    if pct < 50:
        print("  ⚠ COBERTURA BAJA. El puente assets.ticker ↔ corchete de "
              "`informacion` puede no ser el correcto. Ejemplos SIN tasa abajo:")
        sin = [f for f in filas if f["tasa"] is None][:muestras or 8]
        for f in sin:
            print(f"      ticker={f['ticker']!r} cuenta={f['id_cuenta']}")
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT n.id_cuenta, n.informacion, o.tasa "
                "FROM operaciones.operaciones o "
                "JOIN operaciones.negocio_movimientos n ON n.comprobante = o.boleto "
                "WHERE o.mercado = ANY(%s) AND o.tasa IS NOT NULL LIMIT 8",
                (list(fin._MERCADOS_TASA),))
            print("      textos `informacion` de boletos MAV con tasa (para comparar):")
            for c, info, t in cur.fetchall():
                print(f"        cuenta={c} tasa={t} :: {str(info)[:90]!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Diag de la vista FINANCIAMIENTO (read-only).")
    ap.add_argument("--muestras", type=int, default=10, help="filas de ejemplo a imprimir")
    ap.add_argument("--hoy", default=date.today().isoformat(), help="fecha de corte (ISO)")
    args = ap.parse_args()

    print(f"DIAG FINANCIAMIENTO — corte {args.hoy}")
    universo(args.hoy)
    d = payload(args.hoy, args.muestras)
    cobertura_tasa(d, args.muestras)
    print("\nlisto.")


if __name__ == "__main__":
    main()
