"""scripts/diag_1816_fuente.py — ¿alcanza 1816 como fuente de verdad? READ-ONLY.

Decisión del user (2026-08-15): **1816 es la fuente de verdad** para la ficha de
los bonos — el EMISOR primero, y a futuro también el alta de instrumentos nuevos.
Antes de escribir un backfill contra esa fuente hay que medir UNA cosa que decide
todo el diseño: **cuánto cubre**.

`research.mkt_1816_watch` está descripta en el schema como *"universo CURADO
(soberanos + HD)"*. Si eso es literal, 1816 no puede estandarizar los 121
corporativos de la tabla USD y el backfill se cae antes de empezar. Si en cambio
`mkt_1816_instrumentos` trae el catálogo completo, es el ancla exacta que hace
falta. No se puede saber cuál de las dos sin mirar la base.

Lo que este diag responde, en orden de qué bloquea a qué:

  1) COBERTURA — de nuestros bonos, cuántos existen en 1816. Partido por
     `emisor_tipo`, porque el problema reportado es de CORPORATIVOS y un 95% de
     cobertura sobre soberanos no dice nada del caso que importa.

  2) EMISORES — cuántas formas distintas de escribir el mismo emisor tenemos
     (`BCO.COMAFI` / `B. Comafi` / `BANCO COMAFI` son tres filas hoy), y qué dice
     1816 para esos mismos tickers. Es la lista de reemplazos del backfill.

  3) TNA ABSURDA — los bonos USD/DL con rendimiento imposible (|TNA| > 25%),
     con la TNA y la TEA de 1816 al lado. El `last_price` es UNO solo, así que lo
     que rompe el número es otra cosa (el flujo, la fecha de emisión, el VN); ver
     los dos valores juntos es lo que dice cuál.

READ-ONLY total (REGLA #4): sólo SELECT, sin escrituras, sin `--aplicar`.

Uso:
    python -m scripts.diag_1816_fuente
    python -m scripts.diag_1816_fuente --catalogo    # + el catálogo real (1 créd/curva)
    python -m scripts.diag_1816_fuente --umbral 25   # el corte de TEA en % (default 25)
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from collections import defaultdict

from core.postgres import get_pool

_SEP = "=" * 96


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _sep(t: str) -> None:
    print(f"\n{_SEP}\n{t}\n{_SEP}")


# Prefijos de forma societaria: son ruido para AGRUPAR, no para mostrar. `BCO.
# COMAFI` y `B. Comafi` tienen que caer en la misma bolsa para poder contarlos
# como un solo emisor mal escrito.
_RUIDO = re.compile(r"\b(BCO|BANCO|B|CIA|COMPANIA|S\.?A\.?|SA|SAU|SAIC|CL|CLASE)\b")


def clave_emisor(s: str) -> str:
    """Forma canónica SOLO para agrupar variantes. No es el nombre bueno."""
    s = unicodedata.normalize("NFKD", (s or "").upper())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = _RUIDO.sub(" ", s)
    return " ".join(s.split())


def main() -> None:
    ap = argparse.ArgumentParser(description="¿Alcanza 1816 como fuente de verdad?")
    ap.add_argument("--catalogo", action="store_true",
                    help="recorrer las curvas de 1816 y traer sus instrumentos "
                         "(1 crédito por curva)")
    ap.add_argument("--umbral", type=float, default=25.0,
                    help="|TNA| en %% a partir de la cual un bono USD es sospechoso")
    args = ap.parse_args()

    # ── 0) ¿QUÉ TIENE 1816 PARA DAR? ────────────────────────────────────────
    if args.catalogo:
        _sep("0) CATÁLOGO COMPLETO DE 1816 (cuesta 1 crédito)")
        print("  Lo que hay en `mkt_1816_instrumentos` es lo que la WATCHLIST pidió,")
        print("  no lo que 1816 tiene: las dos dan 66 y no es casualidad. Para saber")
        print("  qué hay DE VERDAD hay que recorrer las curvas — la API no acepta un")
        print("  \'traeme todo\'. De eso depende si el backfill de emisores es posible.")
        from core import mercado_1816
        # `instrumentos()` SIN filtro devuelve HTTP 400: la API exige `texto` o
        # `curvaId`. O sea que no hay "traeme todo" — hay que recorrer las CURVAS
        # y pedir los instrumentos de cada una. Son 1 crédito por llamada.
        cat: list[dict] = []
        try:
            curvas_1816 = mercado_1816.curvas()
            print(f"\n  curvas que publica 1816: {len(curvas_1816)}")
            for c in curvas_1816:
                cid = c.get("id") or c.get("curvaId")
                nom = c.get("nombre") or c.get("descripcion") or cid
                if cid is None:
                    continue
                try:
                    ins = mercado_1816.instrumentos(curva_id=int(cid))
                except Exception as e:
                    print(f"    ⚠ curva {nom}: {e}")
                    continue
                cat.extend(ins)
                print(f"    {str(nom)[:44]:<46} {len(ins):>4} instrumentos")
        except Exception as e:
            print(f"  ⚠ no se pudo consultar ({e}) — ¿credenciales cargadas?")
        if cat:
            nuestros = {r["ticker"].upper() for r in _q(
                "SELECT ticker FROM mercado.curvas WHERE ticker IS NOT NULL")}
            suyos = {str(i.get("ticker") or "").upper() for i in cat if i.get("ticker")}
            print(f"\n  TOTAL 1816: {len(cat)} instrumentos ({len(suyos)} tickers únicos)")
            print(f"  de NUESTROS {len(nuestros)} bonos, 1816 tiene: {len(nuestros & suyos)}")
            print(f"\n  {'EMISOR_TIPO':<18}{'BONOS':>8}{'EN EL CATÁLOGO':>16}{'%':>8}")
            print("  " + "-" * 50)
            for r in _q("SELECT COALESCE(emisor_tipo,'(sin clasificar)') AS t, "
                        "array_agg(upper(ticker)) AS tks "
                        "FROM mercado.curvas GROUP BY 1 ORDER BY 1"):
                tks = set(r["tks"] or [])
                hay = len(tks & suyos)
                pct = (100.0 * hay / len(tks)) if tks else 0
                print(f"  {r['t']:<18}{len(tks):>8}{hay:>16}{pct:>7.0f}%")
            campos = sorted({k for i in cat[:50] for k in i})
            print(f"\n  campos por instrumento: {', '.join(campos)}")

    # ── 1) COBERTURA ────────────────────────────────────────────────────────
    _sep("1) COBERTURA — ¿1816 conoce nuestros bonos?")
    tot = _q("SELECT count(*) n FROM research.mkt_1816_instrumentos")[0]["n"]
    act = _q("SELECT count(*) n FROM research.mkt_1816_watch WHERE activo")[0]["n"]
    print(f"  catálogo 1816 (`mkt_1816_instrumentos`): {tot} instrumentos")
    print(f"  universo curado (`mkt_1816_watch` activo): {act} tickers")
    con_em = _q("SELECT count(*) n FROM research.mkt_1816_instrumentos "
                "WHERE emisor IS NOT NULL AND trim(emisor) <> ''")[0]["n"]
    print(f"  con emisor cargado en 1816: {con_em}")

    print("\n  NUESTROS bonos (mercado.curvas) vs el catálogo de 1816:")
    print(f"  {'EMISOR_TIPO':<16}{'BONOS':>8}{'EN 1816':>10}{'CON EMISOR':>12}{'%':>8}")
    print("  " + "-" * 54)
    for r in _q("""
        SELECT COALESCE(c.emisor_tipo, '(sin clasificar)') AS tipo,
               count(*) AS bonos,
               count(i.ticker) AS en_1816,
               count(NULLIF(trim(COALESCE(i.emisor, '')), '')) AS con_emisor
        FROM mercado.curvas c
        LEFT JOIN research.mkt_1816_instrumentos i ON upper(i.ticker) = upper(c.ticker)
        GROUP BY 1 ORDER BY 2 DESC
    """):
        pct = (100.0 * r["en_1816"] / r["bonos"]) if r["bonos"] else 0
        print(f"  {r['tipo']:<16}{r['bonos']:>8}{r['en_1816']:>10}"
              f"{r['con_emisor']:>12}{pct:>7.0f}%")

    # ── 2) EMISORES ─────────────────────────────────────────────────────────
    _sep("2) EMISORES — cuántas formas de escribir lo mismo")
    filas = _q("""
        SELECT c.ticker, c.emisor AS nuestro, i.emisor AS de_1816
        FROM mercado.curvas c
        LEFT JOIN research.mkt_1816_instrumentos i ON upper(i.ticker) = upper(c.ticker)
        WHERE c.emisor IS NOT NULL AND trim(c.emisor) <> ''
    """)
    grupos: dict[str, set[str]] = defaultdict(set)
    for f in filas:
        grupos[clave_emisor(f["nuestro"])].add(f["nuestro"].strip())
    dupes = {k: v for k, v in grupos.items() if len(v) > 1}
    print(f"  emisores distintos hoy: {len(grupos)} claves → "
          f"{len({f['nuestro'].strip() for f in filas})} strings")
    print(f"  claves con MÁS DE UNA forma de escribirse: {len(dupes)}")
    for k, v in sorted(dupes.items(), key=lambda kv: -len(kv[1]))[:25]:
        print(f"    {k:<28} {' | '.join(sorted(v))}")

    conflicto = [f for f in filas if f["de_1816"]
                 and clave_emisor(f["nuestro"]) != clave_emisor(f["de_1816"])]
    print(f"\n  bonos donde 1816 dice OTRO emisor (no es sólo la forma): "
          f"{len(conflicto)}")
    for f in conflicto[:20]:
        print(f"    {f['ticker']:<10} nuestro={f['nuestro']!r:<32} 1816={f['de_1816']!r}")

    sin = _q("""
        SELECT count(*) n FROM mercado.curvas c
        WHERE (c.emisor IS NULL OR trim(c.emisor) = '')
          AND EXISTS (SELECT 1 FROM research.mkt_1816_instrumentos i
                      WHERE upper(i.ticker) = upper(c.ticker)
                        AND trim(COALESCE(i.emisor, '')) <> '')
    """)[0]["n"]
    print(f"\n  bonos SIN emisor que 1816 sí tiene (el backfill los completa): {sin}")

    # ── 3) TNA ABSURDA ──────────────────────────────────────────────────────
    _sep(f"3) TASAS ABSURDAS — bonos USD con |TEA| > {args.umbral:.0f}%")
    print("  OJO con la unidad: `market_snapshot.tea` guarda una FRACCIÓN")
    print("  (1.421 = 142,1%), no un porcentaje — el front la multiplica por 100")
    print("  al mostrarla. La primera corrida de este diag comparaba contra 25 y")
    print(f"  devolvió 0 casos con AFCHO al 142% en pantalla. El corte real es "
          f"{args.umbral / 100:.2f}.")
    print("  NO se calcula nada acá. Se leen los DOS números ya guardados: nuestra")
    print("  TEA (`market_snapshot`, la que escribe motor_curvas) y la de 1816")
    print("  (`mkt_1816_series`, su última fecha). Ponerlos uno al lado del otro es")
    print("  lo que dice DÓNDE está el error — el precio es UNO solo, así que si")
    print("  1816 saca un número sano del mismo precio, lo roto es nuestro insumo:")
    print("  el flujo, la fecha de emisión o el valor nominal.")
    raros = _q("""
        WITH u AS (
            SELECT ticker, campo, valor,
                   row_number() OVER (PARTITION BY ticker, campo ORDER BY fecha DESC) rn
            FROM research.mkt_1816_series WHERE campo IN ('tna', 'tea')
        )
        SELECT c.ticker, c.emisor_tipo, c.moneda_eje, c.ajuste,
               c.fecha_vencimiento, c.flujo_vencimiento,
               s.last_price, s.tea, s.duration, s.paridad,
               max(CASE WHEN u.campo = 'tna' THEN u.valor END) AS tna_1816,
               max(CASE WHEN u.campo = 'tea' THEN u.valor END) AS tea_1816,
               (SELECT count(*) FROM jsonb_array_elements(c.flujos)) AS n_flujos
        FROM mercado.curvas c
        JOIN mercado.market_snapshot s ON s.ticker = c.instrumento
        LEFT JOIN u ON upper(u.ticker) = upper(c.ticker) AND u.rn = 1
        WHERE c.moneda_eje = 'USD' AND s.tea IS NOT NULL AND abs(s.tea) > %s
        GROUP BY c.ticker, c.emisor_tipo, c.moneda_eje, c.ajuste,
                 c.fecha_vencimiento, c.flujo_vencimiento, c.flujos,
                 s.last_price, s.tea, s.duration, s.paridad
        ORDER BY abs(s.tea) DESC
    """, (args.umbral / 100.0,))
    print(f"\n  encontrados: {len(raros)}")
    print(f"\n  {'TICKER':<9}{'TIPO':<13}{'TEA':>9}{'TEA 1816':>10}{'DUR':>7}"
          f"{'PX':>10}{'FLUJOS':>8}{'VTO':>12}  AJUSTE")
    print("  " + "-" * 92)
    for r in raros[:40]:
        def n(v, d=2):
            return f"{float(v):,.{d}f}" if v is not None else "--"
        print(f"  {str(r['ticker'])[:8]:<9}{str(r['emisor_tipo'] or '')[:12]:<13}"
              f"{n(r['tea']):>9}{n(r['tea_1816']):>10}{n(r['duration']):>7}"
              f"{n(r['last_price']):>10}{r['n_flujos'] or 0!s:>8}"
              f"{r['fecha_vencimiento'] or '--'!s:>12}  {r['ajuste'] or '--'}")
    if len(raros) > 40:
        print(f"  … y {len(raros) - 40} más")

    print("\n  CÓMO SE LEE:")
    print("    · TEA 1816 razonable y la nuestra no  → el problema es NUESTRO flujo.")
    print("    · FLUJOS = 0                          → el bono no tiene cronograma.")
    print("    · DUR = 0 con vencimiento lejano      → el flujo no se está leyendo.")
    print("    · TEA 1816 vacía                      → 1816 no cubre ese ticker.")


if __name__ == "__main__":
    main()
