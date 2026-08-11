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
  4. UMBRAL HD/DL — si el corte en 5.000 parte bien el universo o si hay muchos
     instrumentos parecidos cayendo a los dos lados del corte (la clasificación
     HD/DL es una heurística de arranque, no una verdad).

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
    _fila("monedas presentes (tenencia, informativo)", monedas or "—")
    clases: dict[str, int] = {}
    for f in filas:
        clases[f["clase"] or "(SIN CLASIFICAR)"] = clases.get(f["clase"] or "(SIN CLASIFICAR)", 0) + 1
    _fila("clases (HD/DL) — la vista muestra UNA por vez", clases or "—")
    if sin := clases.get("(SIN CLASIFICAR)"):
        print(f"  ⚠ {sin} fila(s) sin CLASE_ACTIVO — correr "
              f"`python -m jobs.assets_autofill --regla financiamiento_clase`")
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


def umbral_hd_dl(muestras: int) -> None:
    """¿El corte en 5.000 parte bien el universo? (REGLA #2: la regla HD/DL es
    una HEURÍSTICA — esto es lo que permite confirmarla o corregirla con datos).

    Si los nominales están bien separados (un grupo de miles y otro de millones)
    el umbral es sano. Si hay muchos justo alrededor del corte, la regla está
    partiendo instrumentos parecidos en clases distintas y hay que revisarla.
    """
    from jobs.assets_autofill import _UMBRAL_HD
    print(f"\n4) UMBRAL HD/DL — corte en {_UMBRAL_HD:,.0f}")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT a.unidad, sum(t.cantidad) AS nominal, a.clase_activo "
            "FROM portafolio.assets a "
            "JOIN portafolio.tenencia t ON t.unidad = a.unidad "
            "WHERE upper(btrim(a.cartera)) = %(c)s "
            "  AND t.fecha = (SELECT max(fecha) FROM portafolio.tenencia) "
            "  AND t.cantidad IS NOT NULL "
            "GROUP BY a.unidad, a.clase_activo ORDER BY 2",
            {"c": fin.CARTERA})
        rows = cur.fetchall()
    if not rows:
        print("  sin tenencia de financiamiento: nada que medir.")
        return
    noms = [float(n) for _, n, _ in rows]
    hd = [n for n in noms if n <= _UMBRAL_HD]
    _fila("unidades con nominal", len(noms))
    _fila(f"→ HD (≤ {_UMBRAL_HD:,.0f})", f"{len(hd)}  ({100*len(hd)/len(noms):.1f}%)")
    _fila("→ DL (>)", f"{len(noms) - len(hd)}  ({100*(len(noms)-len(hd))/len(noms):.1f}%)")
    _fila("nominal mínimo / máximo", f"{min(noms):,.2f}  /  {max(noms):,.2f}")
    # La ZONA GRIS: nominales dentro de un factor 10 del corte, para los dos lados.
    gris = sorted(n for n in noms if _UMBRAL_HD / 10 <= n <= _UMBRAL_HD * 10)
    _fila("en la zona gris (×/÷10 del corte)", len(gris))
    if gris:
        print("  ⚠ Cuanto más pobladas estén estas cifras, menos confiable es el corte:")
        for n in gris[:muestras or 10]:
            print(f"      {n:>18,.2f}  → {'HD' if n <= _UMBRAL_HD else 'DL'}")
    ya = [c for _, _, c in rows if (c or "").strip()]
    _fila("unidades con clase YA cargada (no se pisan)", len(ya))


def main() -> None:
    ap = argparse.ArgumentParser(description="Diag de la vista FINANCIAMIENTO (read-only).")
    ap.add_argument("--muestras", type=int, default=10, help="filas de ejemplo a imprimir")
    ap.add_argument("--hoy", default=date.today().isoformat(), help="fecha de corte (ISO)")
    args = ap.parse_args()

    print(f"DIAG FINANCIAMIENTO — corte {args.hoy}")
    universo(args.hoy)
    d = payload(args.hoy, args.muestras)
    cobertura_tasa(d, args.muestras)
    umbral_hd_dl(args.muestras)
    print("\nlisto.")


if __name__ == "__main__":
    main()
