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

     Y sobre todo contesta la pregunta que decide si esto es UNA carga manual o
     UNA PARA SIEMPRE: **¿1816 dice el par en algún lado?** Su curva se llama
     "Soberanos Duales" y no lo dice, pero de su ficha guardamos SOLO 10 campos y
     nunca miramos el resto. Dos candidatos, los dos gratis: la `denominacion`
     (el nombre oficial del bono, que suele nombrar la tasa) y las claves del
     registro crudo que hoy tiramos a la basura. Si el par está ahí, esto se
     automatiza y ningún bono nuevo va a necesitar edición manual.

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
    python -m scripts.diag_migrar_curva --crudo   # + ficha cruda de 1816 (2 créditos)
"""
from __future__ import annotations

import argparse

from core.postgres import get_pool

_SEP = "=" * 96


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _v(x) -> str:
    return "—" if x is None or str(x).strip() == "" else str(x)


def _pares(blob: str | None) -> list[tuple[str, object]]:
    """El blob jsonb como pares `(clave, valor)`. Vacío si no se puede leer —
    un blob roto no puede tumbar un diagnóstico."""
    if not blob:
        return []
    try:
        import json
        d = json.loads(blob)
    except Exception:
        return []
    return list(d.items()) if isinstance(d, dict) else []


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
               -- El blob COMPLETO menos `flujos` (que es el cronograma y no aporta
               -- acá). Antes esto listaba solo los NOMBRES de las claves: se vio que
               -- 5 de 8 tienen `tasa_referencia` y no se pudo leer qué dice, que era
               -- justo el dato. Ver los nombres sin los valores no responde nada.
               (data - 'flujos')::text                  AS blob
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
        print("     blob `data` (sin el cronograma), con VALORES:")
        for k, val in sorted(_pares(f["blob"])):
            marca = "  ← ¿LA SEGUNDA PATA?" if "tasa" in k.lower() else ""
            print(f"        {k:<22} {str(val)[:44]}{marca}")
    print("\n  Cómo se lee: si alguna CLAVE del blob o de los flujos nombra la")
    print("  segunda pata (tamar/tasa/spread/devaluación), la fuente ya la tenemos y")
    print("  el backfill sale de acá. Si NO aparece en ningún lado, hay que cargarla")
    print("  a mano — son 8 filas, pero mejor saberlo antes que descubrirlo después.")

    # ── 1b) ¿1816 dice el par? Lo que YA tenemos guardado, 0 créditos ─────────
    print(f"\n{'─' * 92}")
    print("  1b) LA DENOMINACIÓN DE 1816 — ¿nombra el par? (0 créditos, ya está guardada)")
    print(f"{'─' * 92}")
    print("  Esta es LA pregunta que decide si esto es una carga manual o una para")
    print("  siempre. El nombre OFICIAL de un bono suele decir contra qué ajusta")
    print("  ('BONO … TASA DUAL TAMAR …'). Si lo dice, se parsea y ningún dual nuevo")
    print("  vuelve a necesitar edición a mano.\n")
    tks = [f["ticker"] for f in filas]
    den = _q("""
        SELECT c.ticker, i.denominacion, i.curva AS curva_1816
        FROM mercado.curvas c
        LEFT JOIN research.mkt_1816_instrumentos i ON upper(i.ticker) = upper(c.ticker)
        WHERE c.ticker = ANY(%s) ORDER BY c.ticker
    """, (tks,))
    sin_ficha = 0
    for d in den:
        if not d["denominacion"]:
            sin_ficha += 1
            print(f"  {d['ticker']:<12} ✗ sin ficha de 1816")
            continue
        print(f"  {d['ticker']:<12} {d['denominacion']}")
        print(f"  {'':<12} (curva 1816: {_v(d['curva_1816'])})")
    if sin_ficha:
        print(f"\n  ⚠ {sin_ficha} sin ficha. Se llena con: "
              "python -m jobs.mercado_1816_discovery --apply --catalogo")


def duales_crudo() -> None:
    """La ficha CRUDA de 1816 para las curvas de duales. 1 crédito por curva.

    Guardamos 10 campos del registro que manda 1816 y el resto se descarta sin
    haberlo mirado nunca. Si entre esos campos descartados viene el ajuste, el par
    sale solo y esta discusión se termina. Cuesta 2 créditos averiguarlo — mucho
    menos que editar a mano cada dual que salga de acá a siempre.
    """
    print(f"\n{_SEP}\n1c) FICHA CRUDA DE 1816 — los campos que hoy tiramos\n{_SEP}")
    try:
        from core import mercado_1816
    except Exception as e:                                    # pragma: no cover
        print(f"  ✗ no se pudo importar el cliente de 1816: {str(e)[:80]}")
        return
    if not mercado_1816.disponible():
        print("  ✗ falta MERCADO_1816_API_KEY en el .env — se saltea este bloque.")
        return

    try:
        todas = mercado_1816.curvas() or []
    except Exception as e:
        print(f"  ✗ 1816 no respondió: {str(e)[:80]}")
        return
    # Se busca 'dual' en CUALQUIER valor del dict, no en las claves que uno cree
    # que existen. La versión anterior filtraba por `nombre`/`descripcion` y dijo
    # "1816 no tiene ninguna curva dual" mientras el bloque 1b mostraba, ocho
    # veces, «Soberanos Duales»: adivinar el nombre de una clave no da un dato,
    # da un falso negativo que además suena a hallazgo.
    curvas = [c for c in todas
              if any("dual" in str(v).lower() for v in c.values())]
    if not curvas:
        print(f"  (ninguna de las {len(todas)} curvas de 1816 menciona 'dual')")
        print(f"  claves que trae una curva: {sorted(todas[0]) if todas else '—'}")
        return

    guardados = {"ticker", "denominacion", "curva", "isinCode", "fechaEmision",
                 "fechaVencimiento", "monedaDenom", "monedaPago", "emisorNombre"}
    for c in curvas:
        cid = c.get("id") or c.get("curvaId")
        nombre = c.get("nombre") or c.get("descripcion") or f"id={cid}"
        print(f"\n  ── curva «{nombre}» ──")
        try:
            insts = mercado_1816.instrumentos(curva_id=cid) or []
        except Exception as e:
            print(f"     ✗ {str(e)[:80]}")
            continue
        if not insts:
            print("     (sin instrumentos)")
            continue
        muestra = insts[0]
        print(f"     {len(insts)} instrumento(s). Campos del PRIMERO ({muestra.get('ticker')}):")
        for k in sorted(muestra):
            marca = "  " if k in guardados else "★ "   # ★ = lo estamos descartando
            print(f"     {marca}{k:<26} {str(muestra[k])[:52]}")
        print("\n     ★ = campo que 1816 manda y NOSOTROS NO guardamos. Si alguno")
        print("     nombra la tasa/el ajuste, el par se automatiza y no hay carga manual.")


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
    ap = argparse.ArgumentParser(description="Medir antes de eliminar `curva`")
    ap.add_argument("--crudo", action="store_true",
                    help="+ ficha cruda de 1816 para las curvas de duales (2 créditos)")
    args = ap.parse_args()

    print(_SEP)
    print("MIGRAR `curva` A LOS EJES — lo que hay que medir antes de tocar código")
    print(_SEP)
    duales()
    if args.crudo:
        duales_crudo()
    else:
        print("\n  (con --crudo se pide además la ficha cruda de 1816 para ver los")
        print("   campos que hoy descartamos. Cuesta 2 créditos y puede terminar")
        print("   con la carga manual de los duales para siempre.)")
    emisores()
    mapa()
    print(f"\n{_SEP}\nFIN — nada de esto escribió en la base.\n{_SEP}")


if __name__ == "__main__":
    main()
