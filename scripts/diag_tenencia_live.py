"""Diag READ-ONLY: qué hay adentro de `portafolio.tenencia_live` y si tiene sentido.

Es la lupa del daemon `jobs/tenencia_live.py`. Responde cuatro cosas, en orden:

  1) ¿QUÉ HAY?      — filas y cuentas por horizonte, y de qué día.
  2) ¿ESTÁ FRESCO?  — cuándo se actualizó cada cuenta. Una tabla "live" congelada
                      en silencio es peor que no tenerla, así que esto es lo
                      primero que hay que mirar cuando algo parece raro.
  3) ¿TIENE SENTIDO? — para una cuenta, la foto conciliada (portafolio.tenencia)
                      contra t0 y t1, unidad por unidad. Las diferencias entre t0
                      y t1 son EXACTAMENTE lo que se concertó hoy: si ahí aparece
                      algo que no operó, o falta algo que sí operó, el problema
                      está en el daemon y no en el dato.
  4) ¿DE DÓNDE VINO? — filas por `origen` (barrido de apertura vs boleto detectado),
                      que dice si el detector intradía está funcionando o si todo
                      viene del barrido de la mañana.

Uso:
    python -m scripts.diag_tenencia_live
    python -m scripts.diag_tenencia_live --cuenta 1346
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

from api.services._sql import _q

CUENTA_DEFAULT = "805"
TOL = 1e-6


def _arg(flag: str, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def que_hay() -> None:
    print("\n── 1) QUÉ HAY en portafolio.tenencia_live ────────────────────────────────")
    try:
        filas = _q("SELECT fecha, horizonte, desde_consultado, COUNT(*) AS filas, "
                   "       COUNT(DISTINCT id_cuenta) AS cuentas "
                   "FROM portafolio.tenencia_live "
                   "GROUP BY fecha, horizonte, desde_consultado "
                   "ORDER BY fecha DESC, horizonte")
    except Exception as e:
        print(f"   ✗ no pude leer: {type(e).__name__}: {e}")
        print("     (si dice que la relación no existe, el daemon todavía no corrió)")
        return
    if not filas:
        print("   ✗ la tabla está VACÍA — el daemon no escribió nada todavía")
        return
    for f in filas:
        print(f"   {f['fecha']}  {f['horizonte']:<3}  desde={f['desde_consultado']}  "
              f"{f['filas']:>6} filas · {f['cuentas']:>4} cuentas")
    fechas = {f["fecha"] for f in filas}
    if len(fechas) > 1:
        print(f"\n   ⚠ hay {len(fechas)} fechas distintas. La tabla NO es acumulativa: el")
        print("     primer barrido del día debería haber borrado lo anterior.")


def frescura() -> None:
    print("\n── 2) ¿ESTÁ FRESCO? (actualizado_at por cuenta) ──────────────────────────")
    try:
        filas = _q(
            "SELECT id_cuenta, MAX(actualizado_at) AS ult "
            "FROM portafolio.tenencia_live "
            "WHERE fecha = (SELECT MAX(fecha) FROM portafolio.tenencia_live) "
            "GROUP BY id_cuenta")
    except Exception as e:
        print(f"   ✗ {type(e).__name__}: {e}")
        return
    if not filas:
        print("   (sin datos)")
        return
    ahora = datetime.now(UTC)
    tramos = {"< 5 min": 0, "5-30 min": 0, "30-120 min": 0, "> 2 horas": 0}
    for f in filas:
        mins = (ahora - f["ult"]).total_seconds() / 60
        if mins < 5:
            tramos["< 5 min"] += 1
        elif mins < 30:
            tramos["5-30 min"] += 1
        elif mins < 120:
            tramos["30-120 min"] += 1
        else:
            tramos["> 2 horas"] += 1
    for k, v in tramos.items():
        print(f"   {k:<12} {v:>5} cuentas")
    viejas = sorted(filas, key=lambda f: f["ult"])[:5]
    print("\n   Las 5 más viejas:")
    for f in viejas:
        mins = (ahora - f["ult"]).total_seconds() / 60
        print(f"     cuenta {f['id_cuenta']:<8} hace {mins:>6.0f} min   ({f['ult']:%H:%M:%S} UTC)")
    print("\n   Con el daemon corriendo, lo NORMAL es que casi todo esté en 30-120 min")
    print("   (el barrido de apertura) y unas pocas en <5 min (las que operaron).")


def comparar(cuenta: str) -> None:
    print(f"\n── 3) ¿TIENE SENTIDO? · cuenta {cuenta} ──────────────────────────────────")
    try:
        foto = _q("SELECT fecha, unidad, cantidad FROM portafolio.tenencia "
                  "WHERE id_cuenta = %(c)s AND fecha = "
                  "  (SELECT MAX(fecha) FROM portafolio.tenencia WHERE id_cuenta = %(c)s)",
                  {"c": cuenta})
        live = _q("SELECT horizonte, unidad, cantidad, desde_consultado, origen, "
                  "       actualizado_at "
                  "FROM portafolio.tenencia_live WHERE id_cuenta = %(c)s AND fecha = "
                  "  (SELECT MAX(fecha) FROM portafolio.tenencia_live)", {"c": cuenta})
    except Exception as e:
        print(f"   ✗ {type(e).__name__}: {e}")
        return
    if not live:
        print(f"   ✗ la cuenta {cuenta} no está en tenencia_live")
        return

    f_foto = foto[0]["fecha"] if foto else None
    m_foto = {r["unidad"]: float(r["cantidad"] or 0) for r in foto}
    m = {"t0": {}, "t1": {}}
    meta = {}
    for r in live:
        m[r["horizonte"]][r["unidad"]] = float(r["cantidad"] or 0)
        meta[r["horizonte"]] = (r["desde_consultado"], r["origen"], r["actualizado_at"])

    print(f"   foto conciliada : fecha={f_foto} ({len(m_foto)} unidades)")
    for h in ("t0", "t1"):
        if h in meta:
            d, o, ts = meta[h]
            print(f"   {h}              : desde={d} · origen={o} · "
                  f"actualizado {ts:%H:%M:%S} UTC ({len(m[h])} unidades)")

    unidades = sorted(set(m_foto) | set(m["t0"]) | set(m["t1"]))
    print(f"\n   {'unidad':<46} {'FOTO':>15} {'t0':>15} {'t1':>15}   Δ t1−t0")
    for u in unidades:
        a, b, c = m_foto.get(u), m["t0"].get(u), m["t1"].get(u)
        d = (c or 0) - (b or 0)
        marca = "  ← concertado hoy" if abs(d) > TOL else ""
        print(f"   {u[:46]:<46} "
              f"{('—' if a is None else f'{a:,.2f}'):>15} "
              f"{('—' if b is None else f'{b:,.2f}'):>15} "
              f"{('—' if c is None else f'{c:,.2f}'):>15}   "
              f"{d:>12,.2f}{marca}")
    difs = sum(1 for u in unidades
               if abs((m["t1"].get(u) or 0) - (m["t0"].get(u) or 0)) > TOL)
    print(f"\n   {difs} unidad(es) cambian entre t0 y t1 → eso es lo concertado HOY.")
    print("   Si es 0 y la cuenta operó hoy, el daemon no se enteró (mirar el detector).")
    print("   Si cambia algo que NO operó hoy, hay un problema — no es 'más fresco'.")


def origenes() -> None:
    print("\n── 4) ¿DE DÓNDE VINO CADA FILA? ──────────────────────────────────────────")
    try:
        filas = _q("SELECT origen, horizonte, COUNT(*) AS filas, "
                   "       COUNT(DISTINCT id_cuenta) AS cuentas "
                   "FROM portafolio.tenencia_live "
                   "WHERE fecha = (SELECT MAX(fecha) FROM portafolio.tenencia_live) "
                   "GROUP BY origen, horizonte ORDER BY origen, horizonte")
    except Exception as e:
        print(f"   ✗ {type(e).__name__}: {e}")
        return
    for f in filas:
        print(f"   {f['origen']!s:<10} {f['horizonte']:<3} "
              f"{f['filas']:>6} filas · {f['cuentas']:>4} cuentas")
    if not any(f["origen"] == "boleto" for f in filas):
        print("\n   Todavía NO hay filas con origen='boleto': o nadie operó desde que")
        print("   arrancó el daemon, o el detector intradía no está encontrando nada.")


def main() -> int:
    cuenta = _arg("--cuenta", CUENTA_DEFAULT)
    ahora = (datetime.now(UTC) - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")
    print(f"\n{'=' * 78}\nDIAG — tenencia_live   ·   {ahora} ART\n{'=' * 78}")
    que_hay()
    frescura()
    comparar(cuenta)
    origenes()
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
