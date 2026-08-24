"""scripts/diag_agente_frescura.py — ¿POR QUÉ AHORA MUESTRA COSAS VIEJAS?

Read-only. Contesta tres preguntas que desde acá no se pueden contestar (REGLA
#2) y que juntas explican una pantalla que dice «roto AHORA» con evidencia de
hace tres días:

  1. ¿Cuándo se refrescó por última vez CADA FILA abierta, y de qué tipo es?
     Una fila con `ultimo_at` viejo NO la está viendo el detector: está abierta
     porque nadie la pudo cerrar, que es distinto de estar rota.

  2. ¿CORREN los detectores? Se llama a `relevar_live()` y se mira qué tipos
     declara en `evaluados`. El que no está ahí es el que se cayó — y su tipo
     no se cierra por ausencia, por diseño (§0.be). Esa es la hipótesis.

  3. ¿Cuánto CUESTA medir la frescura de las tablas en vivo? Es el número que
     falta para poder mover el veredicto de las tablas de 1×/día a cada 10 min
     sin repetir el incidente de CPU (REGLA #4).

    python -m scripts.diag_agente_frescura
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from core.postgres import get_pool


def _edad(ts) -> str:
    if ts is None:
        return "nunca"
    s = (datetime.now(UTC) - ts).total_seconds()
    if s < 3600:
        return f"{int(s / 60)} min"
    if s < 86400:
        return f"{s / 3600:.1f} h"
    return f"{s / 86400:.1f} días"


def filas_abiertas() -> None:
    print("\n=== 1. LAS FILAS ABIERTAS DEL CENTINELA — ¿cuándo las vio un detector? ===")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT tipo, count(*), min(ultimo_at), max(ultimo_at), min(abierto_at) "
            "FROM agente.av_agent_centinela WHERE resuelto_at IS NULL "
            "GROUP BY tipo ORDER BY 2 DESC")
        filas = cur.fetchall()
    if not filas:
        print("  (no hay filas abiertas)")
        return
    print(f"  {'tipo':22} {'n':>4}  {'más viejo visto':>16} "
          f"{'más nuevo visto':>16}  {'abierto desde':>14}")
    for tipo, n, vmin, vmax, amin in filas:
        alarma = "  ⚠ NADIE LA MIRA" if vmax and (
            datetime.now(UTC) - vmax).total_seconds() > 3600 else ""
        print(f"  {tipo:22} {n:>4}  {_edad(vmin):>16} {_edad(vmax):>16}  "
              f"{_edad(amin):>14}{alarma}")
    print("\n  Cómo se lee: si «más nuevo visto» de un tipo es de hace horas, ese")
    print("  detector NO está corriendo. La fila sigue abierta porque el guard de")
    print("  `evaluados` impide cerrarla sin haber mirado — correcto al escribir,")
    print("  y una mentira al mostrarla como «roto AHORA».")


def detectores() -> None:
    print("\n=== 2. ¿QUÉ DETECTORES CORREN? (se llama a relevar_live de verdad) ===")
    from api.services import av_agent
    print(f"  en_rueda={av_agent.en_rueda()}  dia_habil={av_agent.dia_habil()}")
    if not av_agent.en_rueda():
        print("  ⚠ fuera de rueda: los detectores de mercado NO corren por diseño.")
    t0 = time.perf_counter()
    try:
        r = av_agent.relevar_live()
    except Exception as e:
        print(f"  ✖ relevar_live LEVANTÓ: {type(e).__name__}: {e}")
        print("    → ningún tipo entra en `evaluados` → NADA se cierra por")
        print("      ausencia → todo lo abierto se queda con su motivo viejo.")
        return
    ms = int((time.perf_counter() - t0) * 1000)
    ev = set(r.get("evaluados") or ())
    esperados = {"sin_precio", "precio_moneda", "latencia", "motor_caido",
                 "motor_ruidoso", "proveedor_caido"}
    print(f"  corrió en {ms} ms · {len(r.get('hallazgos') or [])} hallazgos")
    print(f"  evaluados  : {sorted(ev) or '(ninguno)'}")
    falta = sorted(esperados - ev)
    if falta:
        print(f"  ✖ NO evaluados: {falta}")
        print("    → esos son los que se quedan colgados. Mirá el log del")
        print("      centinela: `journalctl -u av_agent_centinela -g 'detector'`.")
    else:
        print("  ✔ los seis detectores declararon haber mirado.")


def costo_frescura_viva() -> None:
    print("\n=== 3. ¿CUÁNTO CUESTA medir la frescura de las tablas EN VIVO? ===")
    from api.services import av_agent_contexto as ctx
    perfiles = [p for p in ctx.perfiles(solo_con_ritmo=True) if p.get("col_fecha")]
    print(f"  tablas con ritmo medible: {len(perfiles)}")
    if not perfiles:
        return
    # UNA query para todas: el peaje de Supabase se paga por VIAJE (~8,5 ms),
    # así que 120 `max()` en un UNION ALL cuestan un viaje, no 120.
    partes = [f'SELECT {i} AS i, max("{p["col_fecha"]}") AS t '
              f'FROM "{p["schema"]}"."{p["tabla"]}"'
              for i, p in enumerate(perfiles)]
    sql = " UNION ALL ".join(partes)
    t0 = time.perf_counter()
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql)
            filas = cur.fetchall()
    except Exception as e:
        print(f"  ✖ la query falló: {type(e).__name__}: {str(e)[:200]}")
        return
    ms = (time.perf_counter() - t0) * 1000
    print(f"  UNA query con {len(perfiles)} max() → {ms:.0f} ms "
          f"({len(filas)} resultados)")
    veredicto = ("BARATO: se puede correr cada 10 min sin pensarlo"
                 if ms < 2000 else
                 "CARO: hay tablas sin índice en su columna de fecha — ver abajo"
                 if ms < 10000 else
                 "MUY CARO: NO ponerlo en un cron de 10 min (REGLA #4)")
    print(f"  → {veredicto}")

    if ms >= 2000:
        print("\n  Las 10 más lentas, medidas de a una (para saber a cuál indexar):")
        tiempos = []
        for p in perfiles:
            t1 = time.perf_counter()
            try:
                with get_pool().connection() as conn, conn.cursor() as cur:
                    cur.execute(f'SELECT max("{p["col_fecha"]}") '
                                f'FROM "{p["schema"]}"."{p["tabla"]}"')
                    cur.fetchone()
            except Exception:
                continue
            tiempos.append(((time.perf_counter() - t1) * 1000, p))
        for t, p in sorted(tiempos, reverse=True)[:10]:
            print(f"    {t:8.0f} ms  {p['schema']}.{p['tabla']} "
                  f"({p['filas']} filas, col «{p['col_fecha']}»)")

    # Y lo que importa de verdad: cuántos de los avisos de «tabla quieta» que
    # están en pantalla YA NO SON CIERTOS, porque la tabla volvió a escribir.
    ahora = datetime.now(UTC)
    vivo = {i: t for i, t in filas}
    revivieron = 0
    for i, p in enumerate(perfiles):
        f_guardada = ctx.frescura(p)
        if f_guardada["estado"] != "atrasada":
            continue
        f_viva = ctx.frescura({**p, "ultimo_dato": vivo.get(i)}, ahora=ahora)
        if f_viva["estado"] != "atrasada":
            revivieron += 1
            print(f"    ↩ {p['schema']}.{p['tabla']} — el perfil dice atrasada, "
                  f"en vivo NO lo está")
    print(f"\n  Avisos de «tabla quieta» que ya se arreglaron y siguen en "
          f"pantalla: {revivieron}")


def main() -> int:
    print("DIAG — por qué el agente muestra cosas viejas (read-only)")
    for fn in (filas_abiertas, detectores, costo_frescura_viva):
        try:
            fn()
        except Exception as e:
            print(f"\n  ✖ {fn.__name__} falló: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
