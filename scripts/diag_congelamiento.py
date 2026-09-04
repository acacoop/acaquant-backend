"""`scripts/diag_congelamiento.py` — ¿POR QUÉ SE TILDA LA APP? Read-only.

Contesta con DATOS lo que hoy se contesta con una sensación: *«la app se
congela, actualizo y anda»*. No propone nada ni escribe nada; separa las tres
causas posibles, que se arreglan en tres lugares distintos y **se ven iguales
desde la silla del que usa la app**:

  1. **La API se puso lenta** → `manager.latencia_endpoints`: qué endpoint tarda
     más que su propia normalidad, desde cuándo, y si además da 5xx.
  2. **A la API le PIDEN más** → el mismo lugar, mirado por VOLUMEN. Un endpoint
     nuevo que se pollea de más no aparece como «lento»: aparece como que todo
     lo demás se puso lento, que es una pista que manda al lugar equivocado.
  3. **El NAVEGADOR se clavó** → `agente.pulso_cliente`, que trae las dos
     mitades de «se me colgó»: `ciega` (los pedidos fallan, AGENT.md §0.dg) y
     `tilde` (el hilo principal bloqueado, §0.dm). Un tilde no deja request ni
     excepción: si no lo cuenta el navegador, acá no hay nada que mirar.

⚠️ **Sin este script la elección entre las tres es una corazonada** (REGLA #2),
y las tres tienen arreglos incompatibles: perfilar un endpoint, bajar un poll,
o buscar el render caro de una vista.

Uso:
    python -m scripts.diag_congelamiento              # últimas 48 h vs 7 días
    python -m scripts.diag_congelamiento --horas 24
    python -m scripts.diag_congelamiento --vista /renta-fija
"""
from __future__ import annotations

import argparse
import statistics
import sys

TZ = "America/Argentina/Buenos_Aires"


def _filas(sql: str, params: tuple = ()) -> list[tuple]:
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _titulo(t: str) -> None:
    print(f"\n{'═' * 78}\n{t}\n{'═' * 78}")


# ── 1 y 2. LO QUE MIDIÓ EL SERVIDOR ────────────────────────────────────────
def latencia(horas: int) -> None:
    """Cada endpoint contra SÍ MISMO, nunca contra otro.

    Un ranking de lentos es una lista de cosas inherentemente lentas y no cambia
    nunca (el LLM siempre va a estar primero); lo que informa es **quién empeoró
    respecto de su propia historia**. La base es la MEDIANA de sus horas previas:
    un pico anterior subiría el promedio y taparía justo lo que se busca.
    """
    _titulo(f"1) ENDPOINTS — últimas {horas} h contra su propia mediana previa")
    filas = _filas(
        "WITH ahora AS ("
        "  SELECT endpoint, sum(n) AS n, sum(total_ms) AS ms, max(max_ms) AS pico,"
        "         sum(lentas) AS lentas, sum(errores) AS errores"
        "    FROM manager.latencia_endpoints"
        "   WHERE hora >= now() - make_interval(hours => %s) GROUP BY endpoint),"
        " antes AS ("
        "  SELECT endpoint, hora, total_ms::float / greatest(n, 1) AS avg_ms"
        "    FROM manager.latencia_endpoints"
        "   WHERE hora <  now() - make_interval(hours => %s)"
        "     AND hora >= now() - make_interval(hours => %s) AND n > 0)"
        " SELECT a.endpoint, a.n, (a.ms::float / greatest(a.n, 1))::int, a.pico,"
        "        a.lentas, a.errores,"
        "        (percentile_cont(0.5) WITHIN GROUP (ORDER BY b.avg_ms))::int,"
        "        count(b.hora)::int"
        "   FROM ahora a LEFT JOIN antes b USING (endpoint)"
        "  GROUP BY a.endpoint, a.n, a.ms, a.pico, a.lentas, a.errores"
        "  ORDER BY a.ms DESC LIMIT 25",
        (horas, horas, horas + 24 * 7))
    if not filas:
        print("  (sin telemetría en la ventana — ¿la API se reinició recién?)")
        return
    print(f"  {'endpoint':<44} {'req':>6} {'avg':>7} {'base':>7} {'×':>5} "
          f"{'pico':>7} {'>1s':>5} {'5xx':>4}")
    for ep, n, avg, pico, lentas, errores, base, horas_base in filas:
        # `?` y no un número inventado: sin historia suficiente NO hay contra qué
        # comparar, y poner 1.0× ahí sería afirmar «está igual que siempre».
        veces = f"{avg / base:.1f}" if base and horas_base >= 6 else "?"
        marca = "  ← " if (errores or (base and horas_base >= 6 and avg > base * 2.5)) else "    "
        print(f"{marca}{ep[:44]:<44} {n:>6} {avg:>7} {base or 0:>7} {veces:>5} "
              f"{pico:>7} {lentas:>5} {errores:>4}")
    print("\n  ← = da 5xx, o tarda más del DOBLE de su propia mediana.")
    print("  `base` es la mediana de las horas previas del MISMO endpoint; «?» = "
          "sin historia suficiente,\n  que no es lo mismo que «está igual».")


def volumen(horas: int) -> None:
    """QUIÉN PIDE MÁS, y cómo cambió. Un poll nuevo no se ve como «lento»."""
    _titulo(f"2) VOLUMEN — quién le pide más a la API (últimas {horas} h)")
    filas = _filas(
        "WITH ahora AS ("
        "  SELECT endpoint, sum(n) AS n FROM manager.latencia_endpoints"
        "   WHERE hora >= now() - make_interval(hours => %s) GROUP BY endpoint),"
        " antes AS ("
        "  SELECT endpoint, sum(n)::float / %s AS por_hora"
        "    FROM manager.latencia_endpoints"
        "   WHERE hora <  now() - make_interval(hours => %s)"
        "     AND hora >= now() - make_interval(hours => %s) GROUP BY endpoint)"
        " SELECT a.endpoint, a.n, (a.n::float / %s), coalesce(b.por_hora, 0)"
        "   FROM ahora a LEFT JOIN antes b USING (endpoint)"
        "  ORDER BY a.n DESC LIMIT 20",
        (horas, 24 * 7, horas, horas + 24 * 7, horas))
    if not filas:
        print("  (sin datos)")
        return
    print(f"  {'endpoint':<48} {'req':>8} {'req/h':>8} {'antes/h':>8} {'×':>5}")
    for ep, n, por_hora, antes in filas:
        veces = f"{por_hora / antes:.1f}" if antes else "nuevo"
        print(f"  {ep[:48]:<48} {n:>8} {por_hora:>8.1f} {antes:>8.1f} {veces:>5}")
    print("\n  «nuevo» = no existía en la semana previa. Un endpoint que se "
          "pollea de más no sale\n  lento: hace que salga lento TODO lo demás.")


# ── 3. LO QUE VIO EL NAVEGADOR ─────────────────────────────────────────────
def pantallas(horas: int, vista: str | None) -> None:
    """Las dos mitades de «se me colgó la app», que NO son el mismo problema."""
    _titulo(f"3) PANTALLAS — lo que reportó el navegador (últimas {horas} h)")
    filtro = " AND vista = %s" if vista else ""
    args: tuple = (horas, vista) if vista else (horas,)

    ciegas = _filas(
        "SELECT vista, endpoint, motivo, count(*)::int,"
        "       count(DISTINCT email)::int,"
        "       to_char(min(at) AT TIME ZONE %s, 'DD/MM HH24:MI'),"
        "       to_char(max(at) AT TIME ZONE %s, 'DD/MM HH24:MI')"
        "  FROM agente.pulso_cliente"
        " WHERE at >= now() - make_interval(hours => %s) AND tipo = 'ciega'"
        + filtro +
        " GROUP BY vista, endpoint, motivo ORDER BY 4 DESC LIMIT 25",
        (TZ, TZ) + args)
    print("\n  A) CIEGAS — el pedido falla y el navegador anda bien (§0.dg)")
    if not ciegas:
        print("     (ninguna: ningún poll estuvo más de un minuto fallando)")
    else:
        print(f"     {'vista':<16} {'endpoint':<34} {'motivo':<26} {'n':>4} {'pers':>4}  desde → hasta")
        for v, ep, mot, n, pers, desde, hasta in ciegas:
            print(f"     {v[:16]:<16} {ep[:34]:<34} {mot[:26]:<26} {n:>4} {pers:>4}  {desde} → {hasta}")

    tildes = _filas(
        "SELECT vista, count(*)::int, count(DISTINCT email)::int,"
        "       max(ms), (avg(ms))::int,"
        "       max((datos->>'peor_tarea_ms')::int),"
        "       min((datos->>'memoria_mb')::int), max((datos->>'memoria_mb')::int),"
        "       to_char(max(at) AT TIME ZONE %s, 'DD/MM HH24:MI')"
        "  FROM agente.pulso_cliente"
        " WHERE at >= now() - make_interval(hours => %s) AND tipo = 'tilde'"
        + filtro +
        " GROUP BY vista ORDER BY 4 DESC LIMIT 20",
        (TZ,) + args)
    print("\n  B) TILDADAS — el navegador NO responde y nada falla (§0.dm)")
    if not tildes:
        print("     (ninguna. OJO: si el deploy del frontend con `lib/tilde.ts` es")
        print("      posterior a la ventana, «ninguna» sólo dice que nadie midió.)")
        return
    print(f"     {'vista':<18} {'veces':>5} {'pers':>4} {'peor':>7} {'prom':>7} "
          f"{'tarea':>7} {'mem MB':>12}  último")
    for v, n, pers, peor, prom, tarea, mem_min, mem_max, ult in tildes:
        js = "JS" if (tarea or 0) >= (peor or 0) / 2 else "—"
        mem = f"{mem_min or 0}→{mem_max or 0}"
        print(f"     {v[:18]:<18} {n:>5} {pers:>4} {(peor or 0) / 1000:>6.1f}s "
              f"{(prom or 0) / 1000:>6.1f}s {(tarea or 0) / 1000:>6.1f}s {mem:>12}  {ult} {js}")
    print("\n     `tarea` ≈ `peor` (JS) → el hilo se fue en JS de ESA vista: "
          "buscar qué calcula o\n     dibuja en cada refresco. `tarea` ≈ 0 → no "
          "fue JS; si `mem MB` sube sin parar,\n     es una fuga de la pestaña.")


def cuando(horas: int) -> None:
    """¿DESDE CUÁNDO? La pregunta que ordena todo lo demás."""
    _titulo("4) ¿DESDE CUÁNDO? — carga y latencia por día (últimos 8 días)")
    filas = _filas(
        "SELECT to_char(date_trunc('day', hora AT TIME ZONE %s), 'DD/MM'),"
        "       sum(n)::bigint, (sum(total_ms)::float / greatest(sum(n), 1))::int,"
        "       sum(lentas)::bigint, sum(errores)::bigint"
        "  FROM manager.latencia_endpoints"
        " WHERE hora >= now() - interval '8 days'"
        " GROUP BY 1 ORDER BY min(hora)", (TZ,))
    if not filas:
        print("  (sin datos)")
        return
    avgs = [f[2] for f in filas]
    base = statistics.median(avgs) if avgs else 0
    print(f"  {'día':<8} {'requests':>10} {'avg ms':>8} {'>1s':>8} {'5xx':>6}   perfil")
    for dia, n, avg, lentas, err in filas:
        barra = "█" * min(40, int((avg / base) * 12)) if base else ""
        print(f"  {dia:<8} {n:>10} {avg:>8} {lentas:>8} {err:>6}   {barra}")
    print(f"\n  La barra compara cada día contra la mediana de los 8 ({base} ms). "
          "Un salto que empieza\n  un día y no se va es un cambio, no un pico de mercado.")
    print(f"\n  Nota: {horas} h es la ventana de los bloques 1-3; este bloque "
          "siempre mira 8 días\n  para que se vea el ANTES.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horas", type=int, default=48,
                    help="ventana a analizar (default 48)")
    ap.add_argument("--vista", default=None,
                    help="filtrar los reportes del navegador por una vista (/renta-fija)")
    a = ap.parse_args()

    print(f"DIAG CONGELAMIENTO — ventana de {a.horas} h" +
          (f" · vista {a.vista}" if a.vista else ""))
    for paso in (lambda: latencia(a.horas), lambda: volumen(a.horas),
                 lambda: pantallas(a.horas, a.vista), lambda: cuando(a.horas)):
        try:
            paso()
        except Exception as e:
            # Un bloque que no puede leer NO tumba a los otros tres, y lo dice:
            # «no pude mirar» y «no había nada» son afirmaciones distintas.
            print(f"\n  ⚠ ese bloque no se pudo leer: {type(e).__name__}: {e}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
