"""scripts/diag_migrar_curva.py — los 3 datos que faltan para matar `curva`. READ-ONLY.

La decisión ya está tomada: `mercado.curvas.curva` se elimina del master y todo
(vista, forwards, breakevens, fair value) pasa a leer los EJES. Lo que falta no es
decidir, es MEDIR — y son tres cosas que no se pueden suponer:

  1) LOS DUALES. Un dual no es una familia nueva: es un bono con DOS patas de
     rendimiento, y por eso el trader lo mira en la tabla de CER *y* en la de
     TAMAR. Hoy se guarda `ajuste='dual'`, que no dice contra qué ajusta — o sea
     que la segunda pata no está escrita en ningún lado. Este bloque vuelca TODO
     lo que la base sabe de cada dual (dónde está archivado hoy, qué dice el blob
     `data`, qué forma tienen sus flujos) para decidir de dónde sale el par sin
     inventarlo.

  2) LOS EMISORES CORPORATIVOS. La industria (energía, agro, banco…) es atributo
     del EMISOR, no del bono: YPF no es energía en un bono y otra cosa en otro.
     Guardarla por bono es escribir el mismo dato N veces y esperar que nadie lo
     escriba distinto. Acá se cuenta cuántos emisores hay que clasificar A MANO
     y cuántos bonos cuelga cada uno — es lo que dice si el trabajo es una tarde
     o un proyecto.

  3) EL MAPA `curva` → EJES. Migrar no es un renombre: `on_energia` contesta quién
     emite y de qué industria pero NO contra qué ajusta, mientras que `tasa_fija`
     contesta el ajuste pero no quién emite. Una palabra se convierte en tres
     campos. Este bloque muestra, curva por curva, a qué ejes se abre y —lo que
     importa— CUÁNTOS BONOS QUEDARÍAN SIN EJES, que son los que hoy se ven en la
     vista y dejarían de verse si se migra a ciegas.

READ-ONLY. No escribe, no borra, no toca los motores.

Uso:
    python -m scripts.diag_migrar_curva
"""
from __future__ import annotations

from core.postgres import get_pool

_SEP = "=" * 96


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _v(x) -> str:
    return "—" if x is None or str(x).strip() == "" else str(x)


def _titulo(n: int, t: str) -> None:
    print(f"\n{_SEP}\n{n}) {t}\n{_SEP}")


# ── 1) LOS DUALES ────────────────────────────────────────────────────────────
def duales() -> None:
    _titulo(1, "LOS DUALES — ¿de dónde sale la SEGUNDA pata?")
    print("  `ajuste='dual'` guarda que son duales pero no CONTRA QUÉ ajustan. La")
    print("  pata que hoy sí se conoce es dónde están archivados (`curva`): si un")
    print("  dual está en `cer`, esa es una de las dos. La otra hay que encontrarla.\n")

    filas = _q("""
        SELECT ticker, curva, tipo, ajuste, emisor_tipo, moneda_eje, moneda_flujo,
               cupon_anual, cer_emision, fecha_vencimiento,
               (SELECT count(*) FROM jsonb_array_elements(flujos)) AS n_flujos,
               (SELECT string_agg(DISTINCT k, ', ' ORDER BY k)
                  FROM jsonb_array_elements(flujos) f,
                       jsonb_object_keys(f) k)          AS claves_flujo,
               (SELECT string_agg(k, ', ' ORDER BY k)
                  FROM jsonb_object_keys(data) k)       AS claves_data
        FROM mercado.curvas
        WHERE ajuste = 'dual'
        ORDER BY curva, ticker
    """)
    if not filas:
        print("  (no hay ninguna fila con ajuste='dual')")
        return

    print(f"  {len(filas)} dual(es):\n")
    for f in filas:
        print(f"  ── {f['ticker']} " + "─" * max(0, 86 - len(str(f["ticker"]))))
        print(f"     archivado hoy en : curva={_v(f['curva'])}   (← ESTA es una de las dos patas)")
        print(f"     tipo / moneda    : {_v(f['tipo'])} · eje={_v(f['moneda_eje'])} "
              f"· flujo={_v(f['moneda_flujo'])}")
        print(f"     cupon_anual      : {_v(f['cupon_anual'])}   "
              f"cer_emision: {_v(f['cer_emision'])}   vto: {_v(f['fecha_vencimiento'])}")
        print(f"     flujos           : {f['n_flujos']} cupones · claves: "
              f"{_v(f['claves_flujo'])}")
        print(f"     claves del blob  : {_v(f['claves_data'])}")
    print("\n  Cómo se lee: si alguna CLAVE del blob o de los flujos nombra la")
    print("  segunda pata (tamar/tasa/spread/devaluación), la fuente ya la tenemos y")
    print("  el backfill sale de acá. Si NO aparece en ningún lado, hay que cargarla")
    print("  a mano — son 8 filas, pero mejor saberlo antes que descubrirlo después.")


# ── 2) EMISORES CORPORATIVOS ─────────────────────────────────────────────────
def emisores() -> None:
    _titulo(2, "EMISORES CORPORATIVOS — cuántos hay que clasificar por industria")
    print("  La industria es del EMISOR, no del bono. Clasificar 1 vez por emisor")
    print("  en vez de 1 vez por bono es la diferencia entre un dato y N copias que")
    print("  se pueden contradecir. Este número dice cuánto trabajo manual es.\n")

    tot = _q("""
        SELECT count(*) AS bonos, count(DISTINCT emisor) AS emisores,
               count(*) FILTER (WHERE emisor IS NULL OR trim(emisor) = '') AS sin_emisor
        FROM mercado.curvas WHERE emisor_tipo = 'corporativo'
    """)[0]
    print(f"  corporativos: {tot['bonos']} bonos · {tot['emisores']} emisores "
          f"distintos · {tot['sin_emisor']} bono(s) sin emisor cargado")

    filas = _q("""
        SELECT COALESCE(NULLIF(trim(emisor), ''), '∅ SIN EMISOR') AS emisor,
               count(*) AS n,
               string_agg(DISTINCT COALESCE(sector, '—'), ', ') AS sector_hoy
        FROM mercado.curvas WHERE emisor_tipo = 'corporativo'
        GROUP BY 1 ORDER BY 2 DESC, 1
    """)
    print(f"\n  {'EMISOR':<40}{'BONOS':>6}   SECTOR QUE TIENE HOY")
    print("  " + "-" * 88)
    for f in filas:
        print(f"  {f['emisor'][:39]:<40}{f['n']:>6}   {_v(f['sector_hoy'])[:38]}")
    print("  " + "-" * 88)
    print(f"  → hay que asignarle una INDUSTRIA a {len(filas)} emisor(es).")
    print("  El 'sector que tiene hoy' es la curva vieja con el prefijo `on_` sacado,")
    print("  NO una industria — sirve de borrador, no de fuente.")

    # Y los NO corporativos que hoy tienen sector cargado: ahí hay que vaciar.
    otros = _q("""
        SELECT COALESCE(emisor_tipo, '∅ sin eje') AS et, count(*) AS n
        FROM mercado.curvas
        WHERE COALESCE(emisor_tipo, '') <> 'corporativo'
          AND sector IS NOT NULL AND trim(sector) <> ''
        GROUP BY 1 ORDER BY 2 DESC
    """)
    if otros:
        print("\n  ⚠ NO corporativos que hoy tienen `sector` cargado (hay que vaciarlo):")
        for o in otros:
            print(f"      {o['et']:<20}{o['n']:>5}")


# ── 3) EL MAPA curva → EJES ──────────────────────────────────────────────────
def mapa() -> None:
    _titulo(3, "MAPA `curva` → EJES — qué haría exactamente la migración")
    print("  Cada curva de hoy, a qué ejes se abre. Una curva que se abre a UNA sola")
    print("  combinación es un renombre limpio. Una que se abre a VARIAS es la prueba")
    print("  de que esa palabra estaba contestando más de una pregunta.\n")

    filas = _q("""
        SELECT COALESCE(curva, '∅ sin curva') AS curva,
               COALESCE(emisor_tipo, '?')     AS emisor_tipo,
               COALESCE(moneda_eje, '?')      AS moneda_eje,
               COALESCE(ajuste, '?')          AS ajuste,
               count(*) AS n
        FROM mercado.curvas GROUP BY 1,2,3,4 ORDER BY 1, 5 DESC
    """)
    actual = None
    for f in filas:
        if f["curva"] != actual:
            actual = f["curva"]
            print(f"\n  {actual}")
        falta = " ← SIN EJES" if "?" in (f["emisor_tipo"], f["moneda_eje"], f["ajuste"]) else ""
        print(f"      {f['emisor_tipo']:<14}{f['moneda_eje']:<6}{f['ajuste']:<14}"
              f"{f['n']:>5}{falta}")

    # LO QUE IMPORTA: los que HOY SE VEN y con la migración dejarían de verse.
    print(f"\n{_SEP}\n  EL RIESGO REAL: bonos que hoy se ven y quedarían sin clasificar\n{_SEP}")
    print("  La vista de hoy filtra por `curva`. Si un bono tiene `curva` cargada")
    print("  pero le faltan los ejes, al migrar DESAPARECE de la pantalla — sin")
    print("  error, sin log, sin que nadie se entere. Son estos:\n")
    huerf = _q("""
        SELECT ticker, curva, emisor, tipo,
               COALESCE(emisor_tipo, '—') AS emisor_tipo,
               COALESCE(moneda_eje, '—')  AS moneda_eje,
               COALESCE(ajuste, '—')      AS ajuste
        FROM mercado.curvas
        WHERE curva IS NOT NULL AND trim(curva) <> ''
          AND (emisor_tipo IS NULL OR moneda_eje IS NULL OR ajuste IS NULL)
        ORDER BY curva, ticker
    """)
    if not huerf:
        print("  ✅ ninguno. Todo lo que hoy se ve tiene ejes → la migración no")
        print("     esconde nada.")
    else:
        print(f"  {'TICKER':<12}{'CURVA HOY':<16}{'EMISOR_TIPO':<14}{'MONEDA':<8}"
              f"{'AJUSTE':<12}EMISOR")
        print("  " + "-" * 92)
        for h in huerf:
            print(f"  {_v(h['ticker'])[:11]:<12}{_v(h['curva'])[:15]:<16}"
                  f"{h['emisor_tipo']:<14}{h['moneda_eje']:<8}{h['ajuste']:<12}"
                  f"{_v(h['emisor'])[:30]}")
        print("  " + "-" * 92)
        print(f"  → {len(huerf)} bono(s). Estos hay que clasificar ANTES de migrar,")
        print("    no después: son los que se caen de la vista en silencio.")


def main() -> None:
    print(_SEP)
    print("MIGRAR `curva` A LOS EJES — lo que hay que medir antes de tocar código")
    print(_SEP)
    duales()
    emisores()
    mapa()
    print(f"\n{_SEP}\nFIN — nada de esto escribió en la base.\n{_SEP}")


if __name__ == "__main__":
    main()
