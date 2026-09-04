"""`scripts/diag_agente.py` — QUÉ TIENE EL AGENTE ADENTRO, ahora mismo.

Read-only. Contesta las cuatro preguntas que no se pueden mirar desde la app
sin abrirla, y que son las que hay que revisar después de un deploy:

    1. ¿Miró?          — el catálogo: cuándo corrió cada habilidad y cómo salió
    2. ¿Qué encontró?  — los hallazgos abiertos, por habilidad y por regla
    3. ¿Está vivo?     — el latido del daemon
    4. ¿Volvió algo?   — la tabla que DEBE estar vacía

    python -m scripts.diag_agente
    python -m scripts.diag_agente --hallazgos       # el detalle, fila por fila
    python -m scripts.diag_agente --habilidad salud # solo esa
"""
from __future__ import annotations

import argparse
import sys

from agente import tipos
from core.postgres import get_pool


def _filas(sql: str, params: tuple = ()) -> list[tuple]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


def _dur(segundos) -> str:
    """Una duración legible. Un `3600` no se lee; un `1,0h` sí — y la diferencia
    entre 3 minutos y 2 horas es toda la conclusión."""
    s = int(segundos or 0)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s / 3600:.1f}h"
    return f"{s / 86400:.1f}d"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hallazgos", action="store_true", help="fila por fila")
    ap.add_argument("--habilidad", default="", help="filtrar por una")
    a = ap.parse_args()

    # ── 1. ¿ESTÁ VIVO? ─────────────────────────────────────────────────────
    #
    # ⚠️ **LA PREGUNTA LA CONTESTA `motor.vivo()`, ACÁ NO SE RE-DERIVA.**
    # Este diag tenía su propio umbral fijo de 180 s y cantaba «FRÍO» todas las
    # noches con el agente perfectamente vivo: fuera de rueda el ciclo es de
    # 300 s, así que a los 258 s el daemon está a mitad de camino, no muerto.
    #
    # El motor ya lo resuelve bien —tres ciclos de gracia sobre el ritmo que el
    # propio latido declara en `proximo_en_s`— y el modal lee de ahí. Tener el
    # criterio escrito dos veces es la REGLA #9 en chiquito: las dos mitades
    # son coherentes consigo mismas y contestan distinto. Un tablero que dice
    # «detenido» cuando todo anda enseña a ignorar el tablero.
    _titulo("EL LATIDO")
    from agente import motor

    v = motor.vivo()
    if v.get("hace_s") is None:
        print("  ✗ NUNCA latió — el daemon `agente.service` no arrancó, o "
              "arrancó y no completó una pasada"
              + (f" · {v['error']}" if v.get("error") else ""))
    else:
        cada, hace = v.get("cada_s") or 0, v["hace_s"]
        print(f"  {'✔ VIVO' if v['vivo'] else '✗ FRÍO'} · última pasada "
              f"{str(v['at'])[:19]} (hace {hace}s · vuelve cada {cada}s)")
        if not v["vivo"]:
            print(f"      pasaron más de {cada * 3}s sin latir — tres ciclos. "
                  f"Mirar `systemctl status agente.service`.")

    # ── 2. ¿MIRÓ? ──────────────────────────────────────────────────────────
    #
    # Esta es LA tabla del rediseño: «corrí y no encontré nada» y «no corrí» se
    # veían iguales en el agente viejo, y de ahí salían los cierres a ciegas.
    _titulo("EL CATÁLOGO — cuándo miró cada habilidad")
    print(f"  {'HABILIDAD':22} {'CLASE':8} {'RESULT':9} {'HOY':>4} "
          f"{'ABIER':>6} {'TOTAL':>6} {'VOLV':>5}  ÚLTIMA CORRIDA")
    for r in _filas(
            "SELECT h.nombre, h.ventana, h.ultimo_resultado, h.corridas_hoy, "
            "       h.ultima_corrida_at, h.ultimo_error, h.cada_segundos, "
            "       coalesce(v.hallazgos_abiertos,0), coalesce(v.hallazgos_total,0), "
            "       coalesce(v.reincidencias,0) "
            "  FROM agente.habilidades h "
            "  LEFT JOIN agente.v_habilidades v ON v.nombre = h.nombre "
            " WHERE (%s = '' OR h.nombre = %s) "
            " ORDER BY h.dominio, h.nombre", (a.habilidad, a.habilidad)):
        nombre, ventana, res, hoy, ult, err, cada, ab, tot, volv = r
        print(f"  {nombre:22} {ventana:8} {(res or '—'):9} {hoy:>4} "
              f"{ab:>6} {tot:>6} {volv:>5}  "
              f"{str(ult)[:19] if ult else 'NUNCA'}  (cada {cada}s)")
        if err:
            print(f"       ⚠ {err[:150]}")

    # ⚠️ **«NUNCA CORRIÓ» NO ES «NO PUDO MIRAR».** Son tres estados y la
    # confusión entre ellos es exactamente lo que el rediseño existe para
    # evitar. Una habilidad de ventana `rueda` a las 20:15 no falló: no le tocó.
    ciegas = _filas("SELECT nombre, ultimo_resultado, ultimo_error "
                    "  FROM agente.habilidades "
                    " WHERE ultimo_resultado IN ('error', 'sin_datos')")
    nunca = _filas("SELECT nombre, ventana FROM agente.habilidades "
                   " WHERE ultima_corrida_at IS NULL")
    if ciegas:
        print(f"\n  ⚠ {len(ciegas)} habilidad(es) NO PUDIERON MIRAR. **No "
              f"cerraron nada**, que es lo correcto: una corrida ciega que "
              f"cierra problemas deja el tablero en verde el día que menos ve.")
        for n, res, err in ciegas:
            print(f"      {n} [{res}]: {(err or '')[:120]}")
    if nunca:
        print(f"\n  · {len(nunca)} todavía NO CORRIERON — no es lo mismo que "
              f"haber fallado:")
        for n, v in nunca:
            print(f"      {n} (ventana «{v}»: espera su horario)")

    # Hasta cuántos sujetos se listan por regla. Más que esto y el renglón se
    # vuelve ilegible; ahí el número solo ya alcanza.
    SUJETOS_MAX = 12

    # ── 3. ¿QUÉ ENCONTRÓ? ──────────────────────────────────────────────────
    _titulo("LOS HALLAZGOS ABIERTOS")
    tot = _filas("SELECT count(*) FROM agente.hallazgos "
                 " WHERE estado IN ('nuevo','en_curso')")[0][0]
    print(f"  {tot} abiertos en total\n")
    print(f"  {'HABILIDAD':22} {'REGLA':28} {'SEV':6} {'N':>4}  ARREGLO")
    for nombre, regla, sev, n, arr in _filas(
            "SELECT habilidad, regla, severidad, count(*), max(arreglo) "
            "  FROM agente.hallazgos WHERE estado IN ('nuevo','en_curso') "
            "   AND (%s = '' OR habilidad = %s) "
            " GROUP BY habilidad, regla, severidad "
            " ORDER BY count(*) DESC", (a.habilidad, a.habilidad)):
        print(f"  {nombre:22} {regla:28} {sev:6} {n:>4}  "
              f"{arr or '— (aviso: vive solo en AHORA)'}")
        # ⚠️ **CUÁL, no solo cuántos.** Agrupar por regla contesta «hay 11» y
        # deja afuera la única pregunta que sirve para decidir: cuáles. Se
        # muestran los sujetos mientras entren en un renglón — con 200 filas la
        # lista sería ilegible y ahí el conteo alcanza.
        if n <= SUJETOS_MAX:
            quienes = [x[0] for x in _filas(
                "SELECT sujeto FROM agente.hallazgos "
                " WHERE estado IN ('nuevo','en_curso') AND habilidad = %s "
                "   AND regla = %s AND severidad = %s ORDER BY sujeto",
                (nombre, regla, sev))]
            print(f"  {'':22} └─ {', '.join(quienes)[:150]}")

    # AHORA y ENCONTRÓ, de la MISMA query que dibuja cada pantalla.
    ahora = _filas("SELECT count(*) FROM agente.v_ahora")[0][0]
    enc = _filas("SELECT count(*) FROM agente.v_encontro")[0][0]
    print(f"\n  AHORA (hoy, sin leer): {ahora}     "
          f"ENCONTRÓ (con arreglo): {enc}")

    if a.hallazgos:
        _titulo("EL DETALLE")
        for r in _filas(
                "SELECT id, habilidad, sujeto, regla, severidad, veces, "
                "       detectado_at, problema "
                "  FROM agente.hallazgos WHERE estado IN ('nuevo','en_curso') "
                "   AND (%s = '' OR habilidad = %s) "
                " ORDER BY severidad, detectado_at DESC LIMIT 200",
                (a.habilidad, a.habilidad)):
            print(f"  #{r[0]} [{r[4]}] {r[1]}/{r[3]} · {r[2]} · ×{r[5]} · "
                  f"{str(r[6])[:16]}\n      {str(r[7])[:150]}")

    # ── 3.b LA TASA DE RESPALDO ────────────────────────────────────────────
    #
    # El círculo completo de `bono_sin_tasa`: qué bonos operan sin que el motor
    # les calcule la TEA, cuáles quedaron TAPADOS con la tasa de 1816, y cuáles
    # son un agujero real. Sin esto no hay forma de ver si la mitad útil del
    # rediseño está haciendo algo.
    _titulo("LA TASA DE RESPALDO (1816) — el círculo de `bono_sin_tasa`")
    tapados = _filas(
        "SELECT t.ticker, t.tea, t.duration, t.precio, t.fecha_1816, t.pedido_at "
        "  FROM agente.tasa_1816 t WHERE t.tea IS NOT NULL "
        " ORDER BY t.ticker")
    print(f"  {len(tapados)} bono(s) TAPADOS: el motor no les calcula la TEA y "
          f"1816 sí la tiene\n")
    if tapados:
        print(f"  {'TICKER':10} {'TEA':>9} {'DURATION':>9} {'PRECIO':>12}  "
              f"RUEDA 1816       PEDIDA")
        for tk, tea, dur, px, fecha, ped in tapados:
            print(f"  {tk:10} {(float(tea) * 100 if tea is not None else 0):>8.2f}% "
                  f"{(float(dur) if dur is not None else 0):>9.2f} "
                  f"{(float(px) if px is not None else 0):>12,.2f}  "
                  f"{str(fecha)[:10]:16} {str(ped)[:16]}")
        print("\n  → estos ya NO salen en «--»: la vista de curvas los muestra "
              "con `tea_fuente = 1816`.")

    agujeros = _filas(
        "SELECT sujeto FROM agente.hallazgos "
        " WHERE habilidad = 'tasas_al_cierre' AND regla = 'sin_tasa_ni_en_1816' "
        "   AND estado IN ('nuevo','en_curso') ORDER BY sujeto")
    if agujeros:
        print(f"\n  ⚠ {len(agujeros)} AGUJERO(S) REAL(ES) — operan, el motor no "
              f"los calcula y 1816 tampoco los publica:")
        print(f"      {', '.join(a[0] for a in agujeros)}")
        print("      No es un atraso: hay que ver por qué el motor no los "
              "calcula (ejes, moneda del flujo, cronograma).")

    # ── 4. ¿VOLVIÓ ALGO? ───────────────────────────────────────────────────
    _titulo("REINCIDENCIAS — esta tabla DEBE estar vacía")
    filas = _filas("SELECT sujeto, regla, arreglo_aplicado, "
                   "       round(dias_aguanto, 1), volvio_at "
                   "  FROM agente.reincidencias ORDER BY volvio_at DESC LIMIT 30")
    if not filas:
        print("  ✔ vacía — nada de lo que dimos por arreglado volvió")
    else:
        print(f"  ⚠ {len(filas)} — un arreglo que aplicamos NO sirvió\n")
        for s, r, arr, d, v in filas:
            print(f"    {s} · {r} · arreglo «{arr}» · aguantó {d} días · "
                  f"volvió {str(v)[:16]}")

    # ── 4b. LO SILENCIADO ──────────────────────────────────────────────────
    #
    # **El único lugar donde el silencio se ve, y es la TERMINAL a propósito**
    # (pedido del user 2026-08-27: los ignorados no ensucian el modal). Pero
    # invisible del todo tampoco puede quedar: un silencio que nadie puede
    # enumerar es cómo muere un monitoreo. Si una habilidad junta 40, esa
    # habilidad está mal pensada y esta tabla es lo que lo dice.
    _titulo("LO SILENCIADO — «no me interesa», por problema y no por fila")
    filas = _filas("SELECT habilidad, sujeto, regla, por, motivo, desde, hasta "
                   "  FROM agente.silenciados "
                   " ORDER BY habilidad, sujeto LIMIT 100")
    if not filas:
        print("  — nada silenciado")
    else:
        print(f"  {len(filas)} problema(s) silenciado(s)\n")
        print(f"  {'HABILIDAD':22} {'SUJETO':22} {'REGLA':24} "
              f"{'HASTA':12} QUIÉN")
        for hab, suj, reg, por, mot, _desde, hasta in filas:
            # «para siempre» es el default: se dice, no se deja en blanco.
            cuando = "siempre" if hasta is None else str(hasta)[:10]
            print(f"  {hab:22.22} {suj:22.22} {reg:24.24} {cuando:12} "
                  f"{(por or '—')[:24]}")
            if mot:
                print(f"      motivo: {mot}")
        print("\n  → se administra desde la base: una fila silencia, borrarla revive.")

    # ── 5. LO CRÓNICO ──────────────────────────────────────────────────────
    _titulo("LO CRÓNICO — lo que pasa SIEMPRE, y por lo tanto no es un incidente")
    print("  Un EPISODIO = una vez que el problema NACIÓ (no las veces que se lo vio:")
    print("  un problema que persiste no crea fila nueva). Tres episodios son tres")
    print(f"  veces que apareció, se fue y volvió → desde {tipos.EPISODIOS_CRONICO} es CRÓNICO.\n")
    cronicos = _filas(
        "SELECT habilidad, sujeto, regla, count(*)::int AS episodios, "
        # ⚠️ **MEDIANA, no promedio.** Un episodio de cuatro horas entre
        # cuarenta de tres minutos mueve el promedio a doce y cuenta una
        # historia que no pasó. La mediana contesta «cuánto dura ESTO», que es
        # la pregunta.
        "       (percentile_cont(0.5) WITHIN GROUP ("
        "          ORDER BY extract(epoch FROM "
        "                   coalesce(cerrado_at, now()) - detectado_at)))::int AS mediana_s, "
        "       max(extract(epoch FROM "
        "           coalesce(cerrado_at, now()) - detectado_at))::int AS max_s, "
        "       min(detectado_at) AS desde, max(detectado_at) AS ultima, "
        "       count(*) FILTER (WHERE estado = ANY(%s))::int AS abiertos "
        "  FROM agente.hallazgos "
        " WHERE detectado_at > now() - make_interval(days => %s) "
        " GROUP BY habilidad, sujeto, regla "
        "HAVING count(*) >= %s "
        " ORDER BY max(detectado_at) > now() - make_interval(days => %s) DESC, "
        "          count(*) DESC LIMIT 40",
        (list(tipos.ABIERTOS), tipos.VENTANA_CRONICO_D, tipos.EPISODIOS_CRONICO,
         tipos.DIAS_ACTIVO))
    if not cronicos:
        print("  Ninguno. Todo lo que apareció en 30 días es puntual — no hay nada")
        print("  que se esté tapando arreglándolo una y otra vez.")
    else:
        from datetime import UTC, datetime, timedelta
        corte = datetime.now(UTC) - timedelta(days=tipos.DIAS_ACTIVO)
        # ⚠️ `c[7]` es ÚLTIMA, no `c[6]` (que es DESDE). Con el índice corrido,
        # un crónico que arrancó hace tres días y paró ayer se leía como
        # «sigue pasando», y uno viejo que sigue rompiendo caía en histórico —
        # o sea, la lista decía exactamente lo contrario de lo que mira.
        ULTIMA = 7
        activos = [c for c in cronicos if c[ULTIMA] and c[ULTIMA] > corte]
        viejos = [c for c in cronicos if not (c[ULTIMA] and c[ULTIMA] > corte)]

        def _fila(c):
            hab, suj, reg, n, med, mx, _desde, ultima, abiertos = c
            marca = " ●" if abiertos else "  "
            print(f"  {n:>5}{marca} {_dur(med):>7} {_dur(mx):>8}  {hab:<19} "
                  f"{str(suj)[:24]:<24} {str(reg)[:20]:<20} {str(ultima)[:16]}")

        print(f"  ── SIGUEN PASANDO (última vez en {tipos.DIAS_ACTIVO} días) "
              "──────────────────────────")
        if not activos:
            print("  ninguno.")
        else:
            print(f"  {'EPIS':>5}   {'MEDIA':>7} {'PEOR':>8}  {'HABILIDAD':<19} "
                  f"{'SUJETO':<24} {'REGLA':<20} última")
            for c in activos:
                _fila(c)
            print("\n  ⚠️ **ACÁ ESTÁN LAS MEJORAS, y la columna que decide es MEDIA.**")
            print("  Cuarenta episodios de TRES MINUTOS no son «se cae seguido»: son un")
            print("  umbral demasiado sensible, y se arregla cambiando un número.")
            print("  Cuarenta episodios de DOS HORAS sí son un problema de verdad, y")
            print("  entonces hay que hablar con quien lo rompe. Son conclusiones")
            print("  opuestas y sin la duración no se distinguen.")
            print("\n  Los umbrales se editan EN CALIENTE (`agente.habilidades.umbrales`),")
            print("  sin deploy: el código trae el default y la base lo pisa.")
        if viejos:
            print(f"\n  ── YA NO PASAN (nada hace {tipos.DIAS_ACTIVO}+ días) "
                  "─────────────────────────────")
            print(f"  {len(viejos)} problema(s) que fueron crónicos y se cortaron. "
                  "No compiten por tu atención:")
            for hab, suj, reg, n, _m, _x, _d, ultima, _ab in viejos[:12]:
                print(f"  {n:>5}   {hab:<19} {str(suj)[:24]:<24} "
                      f"{str(reg)[:20]:<20} última {str(ultima)[:10]}")
            if len(viejos) > 12:
                print(f"  … y {len(viejos) - 12} más.")
        print(f"\n  {len(activos)} activo(s) · {len(viejos)} histórico(s). "
              "● = tiene un hallazgo ABIERTO ahora.")

    # ── 6. EL LIBRO ────────────────────────────────────────────────────────
    _titulo("LO ÚLTIMO QUE ESCRIBIÓ")
    libro = _filas("SELECT at, arreglo, sujeto, campo, antes, despues, ok, error "
                   "  FROM agente.acciones ORDER BY id DESC LIMIT 15")
    if not libro:
        print("  (todavía no escribió nada)")
    for at, arr, suj, campo, antes, desp, ok, err in libro:
        print(f"  {str(at)[:19]} {'✔' if ok else '✗'} {arr} · {suj} · "
              f"{campo}: {antes or '—'} → {desp or '—'}"
              + (f"  ⚠ {err[:80]}" if err else ""))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
