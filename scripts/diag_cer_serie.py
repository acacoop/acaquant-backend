"""¿Está sana la serie CER? Cobertura, huecos y qué ve el motor.

Nace del 2026-08-17: el AV AGENT dijo *«la serie CER no llega hasta 2025-11-28»*
al dar de alta el TZXA7, y el user detectó que **nada avisaba** que la serie
estuviera vieja. El chequeo de SALUD miraba `MAX(fecha)` de `macro.series_macro`
SIN filtrar por `serie`, o sea que respondía por la serie más fresca de la tabla:
con el dólar al día, el CER podía estar congelado hace meses y el tablero verde.

Eso ya se arregló (contratos por serie). Lo que este script mide es la OTRA mitad,
que no se puede afirmar sin ver los datos (REGLA #2):

  ¿la serie está ATRASADA (termina y no sigue) o tiene un HUECO en el medio?

Son problemas distintos: un atraso es un job que dejó de correr; un hueco es un
día que la fuente no publicó o que se perdió en una ingesta. El mensaje viejo del
agente los confundía en una sola frase.

El CER es **FORWARD**: `jobs.bcra --today` pide hoy+21d para el CER proyectado, así
que una serie sana tiene el máximo POR DELANTE de hoy.

Read-only. No escribe nada.

    python -m scripts.diag_cer_serie              # CER
    python -m scripts.diag_cer_serie DOLAR BADLAR # otras series
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

from core.postgres import get_pool

# Cuántos días hacia atrás se buscan huecos. Un año cubre cualquier bono emitido
# hace poco, que es el caso que dispara el problema.
VENTANA_DIAS = 400


def _serie(cur, serie: str) -> None:
    print(f"\n{'=' * 74}\nSERIE {serie}\n{'=' * 74}")
    cur.execute("SELECT MIN(fecha), MAX(fecha), COUNT(*) "
                "FROM macro.series_macro WHERE serie = %s", (serie,))
    desde, hasta, n = cur.fetchone() or (None, None, 0)
    if not hasta:
        print("  ✘ NO HAY NI UNA FILA de esta serie.")
        return
    hoy = date.today()
    atraso = (hoy - hasta).days
    print(f"  rango   : {desde} → {hasta}   ({n:,} filas)")
    print(f"  hoy     : {hoy}")
    if atraso > 0:
        print(f"  ⚠ ATRASO: el último dato es de hace {atraso} días")
    else:
        print(f"  ✔ FORWARD: llega {-atraso} días por DELANTE de hoy "
              "(el CER se pide hoy+21d)")

    # HUECOS: días de la ventana que no tienen fila. Se compara contra el
    # calendario COMPLETO —no solo hábiles— porque el CER se publica todos los
    # días; un fin de semana faltante también rompe un T−10.
    ini = max(desde, hoy - timedelta(days=VENTANA_DIAS))
    cur.execute("SELECT fecha FROM macro.series_macro "
                "WHERE serie = %s AND fecha >= %s AND fecha <= %s ORDER BY fecha",
                (serie, ini, hasta))
    tiene = {r[0] for r in cur.fetchall()}
    faltan = []
    d = ini
    while d <= hasta:
        if d not in tiene:
            faltan.append(d)
        d += timedelta(days=1)
    print(f"  huecos  : {len(faltan)} días sin dato entre {ini} y {hasta}")
    if faltan:
        # Se agrupan en TRAMOS: 40 días sueltos listados uno por uno no se leen;
        # «del X al Y» dice si fue un corte o días salteados.
        tramos, ini_t, prev = [], faltan[0], faltan[0]
        for f in faltan[1:]:
            if (f - prev).days > 1:
                tramos.append((ini_t, prev))
                ini_t = f
            prev = f
        tramos.append((ini_t, prev))
        for a, b in tramos[:20]:
            dias = (b - a).days + 1
            print(f"      {a} → {b}  ({dias} día/s)")
        if len(tramos) > 20:
            print(f"      … y {len(tramos) - 20} tramos más")


def _lo_que_ve_el_motor() -> None:
    """La MISMA función que usa `engines/curvas.py` y el AV AGENT. Si esto
    devuelve algo distinto de lo de arriba, el problema es la ventana de
    `cargar_cer`, no la serie."""
    print(f"\n{'=' * 74}\nLO QUE VE EL MOTOR (engines/curvas.py)\n{'=' * 74}")
    try:
        from engines.curvas import cargar_cer, cargar_dias_habiles, get_cer_liquidacion
        cer = cargar_cer(dias=4000)
        habiles = cargar_dias_habiles()
    except Exception as e:
        print(f"  ✘ no se pudo cargar: {type(e).__name__}: {e}")
        return
    fechas = sorted(cer) if cer else []
    print(f"  cargar_cer(dias=4000) → {len(fechas):,} fechas"
          + (f", de {fechas[0]} a {fechas[-1]}" if fechas else " (VACÍO)"))
    print(f"  dias_habiles: {len(habiles):,} fechas"
          + (f", de {habiles[0]} a {habiles[-1]}" if habiles else " (VACÍO)"))
    hoy = date.today().isoformat()
    # El caso REAL del incidente: la emisión del TZXA7. Se prueban las DOS vías
    # para ver cuál falla — si la de la tabla devuelve None y la pura encuentra el
    # dato, el problema es la COBERTURA DEL CALENDARIO, no la serie CER.
    from core.calendario import restar_habiles
    from engines.curvas import fecha_cer_liquidacion, get_cer_en_fecha
    print("\n  emisión         T−10(tabla)  T−10(puro)   CER(tabla)  CER(puro)")
    for f in (hoy,
              (date.today() - timedelta(days=30)).isoformat(),
              (date.today() - timedelta(days=270)).isoformat(),
              "2025-11-28"):   # ← la emisión del TZXA7
        try:
            f_tabla = fecha_cer_liquidacion(habiles, f)
            f_puro = restar_habiles(date.fromisoformat(f), 10).isoformat()
            v_tabla = get_cer_liquidacion(cer, habiles, f)
            v_puro = get_cer_en_fecha(cer, date.fromisoformat(f_puro))
        except Exception as e:
            print(f"  {f}  ERROR {type(e).__name__}: {e}")
            continue
        print(f"  {f}      {f_tabla or '—'!s:<12} {f_puro:<12} "
              f"{str(v_tabla or '—')[:11]:<11} {str(v_puro or '—')[:11]}")


def main() -> None:
    series = [s.strip().upper() for s in sys.argv[1:]] or ["CER"]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT serie, MAX(fecha), COUNT(*) FROM macro.series_macro "
                    "GROUP BY serie ORDER BY serie")
        print(f"{'=' * 74}\nTODAS LAS SERIES DE macro.series_macro\n{'=' * 74}")
        print(f"  {'serie':<20} {'último dato':<14} filas")
        for s, mx, n in cur.fetchall():
            print(f"  {s:<20} {mx!s:<14} {n:,}")
        for s in series:
            _serie(cur, s)
    if "CER" in series:
        _lo_que_ve_el_motor()

    print("\nQué mirar:")
    print("  · El listado de arriba es EXACTAMENTE lo que el chequeo viejo tapaba:")
    print("    un MAX sobre la tabla entera respondía por la serie más fresca.")
    print("  · ATRASO = el job dejó de correr. HUECO = un día que no se ingestó.")
    print("  · Si el motor ve menos fechas que la tabla, el problema es la ventana")
    print("    de cargar_cer(dias=4000), no la serie.")


if __name__ == "__main__":
    main()
