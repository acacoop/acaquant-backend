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

from core.postgres import get_pool


def _filas(sql: str, params: tuple = ()) -> list[tuple]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hallazgos", action="store_true", help="fila por fila")
    ap.add_argument("--habilidad", default="", help="filtrar por una")
    a = ap.parse_args()

    # ── 1. ¿ESTÁ VIVO? ─────────────────────────────────────────────────────
    _titulo("EL LATIDO")
    lat = _filas("SELECT at, EXTRACT(epoch FROM now() - at)::int "
                 "FROM agente.latido WHERE id = 1")
    if not lat:
        print("  ✗ NUNCA latió — el daemon `agente.service` no arrancó, o "
              "arrancó y no completó una pasada")
    else:
        at, hace = lat[0]
        print(f"  {'✔ VIVO' if hace < 180 else '✗ FRÍO'} · última pasada "
              f"{str(at)[:19]} (hace {hace}s)")

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

    # ── 5. EL LIBRO ────────────────────────────────────────────────────────
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
