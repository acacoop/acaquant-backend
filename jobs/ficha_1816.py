"""jobs/ficha_1816.py — 1816 es la fuente de verdad del EMISOR. Backfill + mantenimiento.

## El problema

El emisor se cargaba a mano, y a mano el mismo emisor se escribe de N formas:

    BCO.COMAFI  /  B. Comafi          BANCO HIPOTECARIO  /  B. Hipotecario
    VISTA ENERGY / Vista Energy       TECPETROL / Tecpetrol

No es cosmético: **cualquier cosa que agrupe por emisor cuenta mal**. Una
exposición por emisor con Comafi partido en dos filas subestima las dos, y nadie
lo nota porque las dos filas existen y suman bien por separado.

Medido el 2026-08-15: 74 strings distintos para 67 emisores reales (7 grupos
duplicados), más **43 bonos sin emisor** que 1816 sí tiene.

## Por qué 1816 y no un catálogo nuestro

La alternativa era inventar una tabla canónica + alias y mantenerla a mano. Sería
una tercera lista para desincronizar. 1816 ya publica `emisorNombre` con un solo
nombre por emisor, cubre **140/140** de nuestros corporativos (verificado), y va
a ser también la fuente del alta de instrumentos nuevos — así que el bono que
entre mañana ya nace con el nombre bueno, sin que nadie lo tipee.

**Acá SÍ se pisa**, al revés que `assets_autofill`. Es la diferencia entre
completar (donde el humano sabe algo que la máquina no) y **estandarizar** (donde
el humano no aporta: escribir "BCO.COMAFI" en vez de "Banco Comafi" no es
criterio, es tipeo). El diag lo respaldó: **0 casos** donde 1816 diga un emisor
distinto de verdad — donde cubre, coincide. Lo único que cambia es la forma.

## Alcance: el emisor y nada más

`monedaDenom` y las fechas de 1816 también están en el catálogo y también podrían
corregir cosas nuestras — pero tocan la VALUACIÓN (en qué moneda se expresa un
bono decide cómo se lo divide). Eso se REPORTA como divergencia y no se escribe:
un emisor mal escrito es un reporte feo, una moneda mal puesta es plata mal
contada. No es el mismo riesgo y no se decide igual.

No pega a la API: lee `research.mkt_1816_instrumentos`, que llena
`jobs/mercado_1816_discovery --apply --catalogo`. Así puede correr todos los días
sin gastar un crédito.

Uso:
    python -m jobs.ficha_1816
    python -m jobs.ficha_1816 --dry     # qué cambiaría, sin escribir
"""
from __future__ import annotations

import argparse

from core.job_runs import JobRunLogger
from core.postgres import get_pool

# Dónde vive un emisor hoy. Las dos se estandarizan con el mismo nombre — si no,
# agrupar por emisor daría distinto según de qué tabla se lea, que es el problema
# que este job viene a cerrar.
DESTINOS: tuple[tuple[str, str], ...] = (
    ("mercado.curvas", "el master de renta fija"),
    ("portafolio.assets", "el catálogo de títulos"),
)


def decidir_emisores(filas: list[dict]) -> list[dict]:
    """`[{ticker, emisor}]` con los que hay que reescribir. PURA.

    Entra `{ticker, nuestro, de_1816}` y sale sólo lo que cambia. Dos reglas, y
    las dos importan:

      · sin dato en 1816 → no se toca. Cubre el 8% de soberanos y los pocos
        títulos que 1816 no lista: no tener nombre canónico no habilita a borrar
        el que hay.
      · idéntico → no se toca. Sin esto el job "actualizaría" 200 filas por día
        y el log dejaría de servir para ver qué cambió de verdad.
    """
    cambios = []
    for f in filas:
        bueno = (f.get("de_1816") or "").strip()
        actual = (f.get("nuestro") or "").strip()
        if bueno and bueno != actual:
            cambios.append({"ticker": f["ticker"], "emisor": bueno, "antes": actual})
    return cambios


def leer(tabla: str) -> list[dict]:
    """Nuestro emisor + el de 1816, por ticker. `upper()` en el join porque los
    tickers conviven en las dos formas y un match sensible a mayúsculas dejaría
    afuera justo los que hay que arreglar."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"""
            SELECT t.ticker, t.emisor AS nuestro, i.emisor AS de_1816
            FROM {tabla} t                                          -- noqa: S608
            JOIN research.mkt_1816_instrumentos i
              ON upper(i.ticker) = upper(t.ticker)
            WHERE t.ticker IS NOT NULL AND trim(t.ticker) <> ''
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def aplicar(tabla: str, cambios: list[dict]) -> int:
    if not cambios:
        return 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            f"UPDATE {tabla} SET emisor = %(emisor)s "
            f"WHERE upper(ticker) = upper(%(ticker)s)",
            cambios)
        conn.commit()
    return len(cambios)


def divergencias_moneda() -> list[dict]:
    """Bonos donde 1816 dice OTRA moneda de denominación. Sólo se reporta.

    Es el mismo dato que explica los precios de 150.000 en la tabla USD (un bono
    en dólares mostrado con su pata en pesos), pero la moneda decide cómo se
    valúa — corregirla automáticamente sería mover plata desde un job."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT c.ticker, c.moneda_eje, i.moneda_denom
            FROM mercado.curvas c
            JOIN research.mkt_1816_instrumentos i ON upper(i.ticker) = upper(c.ticker)
            WHERE c.moneda_eje IS NOT NULL AND i.moneda_denom IS NOT NULL
              AND upper(c.moneda_eje) <> upper(i.moneda_denom)
            ORDER BY c.ticker
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser(description="Estandarizar el emisor contra 1816")
    ap.add_argument("--dry", action="store_true", help="no escribe")
    args = ap.parse_args()

    with JobRunLogger("ficha_1816") as jr:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM research.mkt_1816_instrumentos")
            catalogo = cur.fetchone()[0]
        jr.set_stat("catalogo_1816", catalogo)
        if not catalogo:
            jr.log("⚠ `research.mkt_1816_instrumentos` VACÍA — no hay contra qué "
                   "estandarizar. Correr: python -m jobs.mercado_1816_discovery "
                   "--apply --catalogo")
            return 0
        jr.log(f"catálogo de 1816: {catalogo} instrumentos")

        for tabla, que_es in DESTINOS:
            cambios = decidir_emisores(leer(tabla))
            vacios = sum(1 for c in cambios if not c["antes"])
            if not args.dry:
                aplicar(tabla, cambios)
            jr.log(f"  · {tabla} ({que_es}): {len(cambios)} emisor(es) "
                   f"{'a estandarizar' if args.dry else 'estandarizados'} "
                   f"— {vacios} estaban vacíos, {len(cambios) - vacios} escritos distinto")
            # SIN tope, a propósito: este job PISA y el valor viejo se pierde,
            # así que el log es el único registro de qué había antes. El tope
            # existiría para no inundar el log diario — pero después del backfill
            # inicial los cambios por día son ~0, así que no hay nada que inundar.
            for c in cambios:
                jr.log(f"      {c['ticker']:<10} {c['antes'] or '(vacío)'!r} → {c['emisor']!r}")
            jr.set_stat(f"{tabla}_cambios", len(cambios))

        # Se reporta y NO se escribe: la moneda decide la valuación.
        div = divergencias_moneda()
        jr.set_stat("moneda_divergente", len(div))
        # La LISTA viaja con la corrida: el agente la convierte en aviso
        # (`job_reporto`, §0.dd). Un contador solo dice que hay algo.
        jr.set_stat("moneda_divergente_lista",
                    [f"{d['ticker']}: nuestro={d['moneda_eje']} 1816={d['moneda_denom']}"
                     for d in div[:200]])
        if div:
            jr.log(f"⚠ {len(div)} bono(s) donde 1816 dice OTRA moneda de "
                   f"denominación (NO se corrige — toca la valuación):")
            for d in div[:20]:
                jr.log(f"      {d['ticker']:<10} nuestro={d['moneda_eje']} "
                       f"1816={d['moneda_denom']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
