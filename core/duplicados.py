"""core/duplicados.py — DÓNDE VIVE EL MISMO DATO DOS VECES, Y QUIÉN MANDA.

Doc madre: `docs/ARQUITECTURA.md` · `docs/AV_AGENT.md` §0.aa.

LA LECCIÓN, PAGADA TRES VECES EN CUATRO DÍAS
=============================================

> *Dos representaciones del mismo dato sin un árbitro declarado no conviven: se
> separan. Y cuando se separan **no falla nada** — cada mitad sigue siendo
> internamente coherente y el sistema miente en silencio.*

    2026-08-19  el símbolo del bono vivía en la COLUMNA y en el BLOB. El motor
                escribía el precio leyendo el blob y la vista lo buscaba por la
                columna. Divergieron en 2 de 229 y esos dos bonos salían enteros
                en `--` **con el precio existiendo**. Nada falló: cada mitad
                tenía razón sobre lo que miraba.

    2026-08-19  `preferencia` (MEP antes que cable) estaba escrita TRES veces.
                Las tres eligieron distinto y el diag mostraba una pata mientras
                el agente iba a pedir otra.

    2026-08-15  el renombre de columnas dejó el blob con el significado
                invertido. Durante cuatro días no pasó nada — hasta que pasó.

El problema no es tener el dato dos veces: a veces hace falta (un blob que leen
500 lugares no se migra de un día para el otro). **El problema es no declarar
quién manda, y que nadie mire si siguen diciendo lo mismo.**

QUÉ HACE ESTE MÓDULO
====================

Un registro **declarado** de los datos que viven en más de un lugar. Cada entrada
dice qué dato es, dónde vive, **quién es el árbitro** y qué se rompe si divergen.
Agregar uno nuevo son cinco líneas y una query.

`divergencias()` los corre a todos y devuelve solo lo que NO coincide. Lo lee el
AV Agent (`detectar_dato_partido`) y lo canta como cualquier otro hallazgo — así
la próxima vez que dos copias se separen, **alguien se entera el mismo día**.

⚠️ **Cada query en su propio `try`.** Una que se rompa no puede dejar sin mirar a
las demás, y —sobre todo— no puede devolver «no hay divergencias»: sin correr, la
respuesta correcta es *no sé*, y por eso `divergencias()` devuelve también qué no
pudo mirar. *El silencio no es un verde.*
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from core.postgres import get_pool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Duplicado:
    """Un dato que vive en dos lugares, con su árbitro declarado.

    `sql` devuelve UNA fila por sujeto que NO coincide: `(sujeto, valor_a,
    valor_b)`. Los que coinciden no se devuelven — la lista es de problemas, no
    un inventario.
    """
    id: str
    que: str          # qué dato es, en criollo
    a: str            # dónde vive la copia A
    b: str            # dónde vive la copia B
    arbitro: str      # cuál de las dos manda cuando difieren
    rompe: str        # qué se rompe si divergen (lo que hace que importe)
    sql: str


# ⚠️ **SE DECLARA, NO SE DESCUBRE.** No hay forma de deducir del esquema que dos
# columnas guardan «lo mismo» — eso lo sabe quien modeló. Lo que sí se consigue
# declarándolo es que el chequeo exista y que la próxima divergencia dure horas y
# no cuatro días.
DUPLICADOS: tuple[Duplicado, ...] = (
    Duplicado(
        id="simbolo_columna_vs_blob",
        que="el símbolo de mercado del bono (con el que se pide el precio)",
        a="mercado.curvas.instrumento (columna)",
        b="mercado.curvas.data->>'ticker' (blob)",
        arbitro="la COLUMNA — es la que migró el renombre del 2026-08-15",
        rompe="el motor escribe el precio leyendo el BLOB y la vista lo busca por "
              "la COLUMNA: si difieren, la fila sale entera en «--» teniendo el "
              "precio cargado, y ningún detector lo ve porque cada mitad es "
              "coherente consigo misma (el caso AO29/CO32)",
        sql="""
            SELECT ticker,
                   coalesce(instrumento, ''),
                   coalesce(data->>'ticker', '')
            FROM mercado.curvas
            WHERE instrumento IS NOT NULL
              AND coalesce(data->>'ticker', '') <> ''
              AND btrim(instrumento) <> btrim(data->>'ticker')
            ORDER BY ticker
        """),
    Duplicado(
        id="ticker_corto_columna_vs_blob",
        que="el ticker corto del bono (el que une con `portafolio.assets`)",
        a="mercado.curvas.ticker (columna, la PK)",
        b="mercado.curvas.data->>'ticker_corto' (blob)",
        arbitro="la COLUMNA — es la PK",
        rompe="es la clave del join con assets y con la tenencia: si el blob dice "
              "otra cosa, lo que lea por el blob valúa contra otro papel",
        sql="""
            SELECT ticker,
                   ticker,
                   coalesce(data->>'ticker_corto', '')
            FROM mercado.curvas
            WHERE coalesce(data->>'ticker_corto', '') <> ''
              AND upper(btrim(ticker)) <> upper(btrim(data->>'ticker_corto'))
            ORDER BY ticker
        """),
    Duplicado(
        id="simbolo_master_vs_especies",
        que="qué pata del bono se suscribe",
        a="mercado.curvas.instrumento (se carga a mano)",
        b="mercado.especies.es_default (se deriva de Primary)",
        arbitro="ESPECIES — sale del catálogo real; el master se tipea",
        rompe="el motor pide una pata y la mesa mira otra: la grilla muestra "
              "pesos en una curva en dólares (AO29/GD46/CO32)",
        sql="""
            SELECT c.ticker, coalesce(c.instrumento, ''), e.simbolo
            FROM mercado.curvas c
            JOIN mercado.especies e
                 ON upper(e.ticker) = upper(c.ticker) AND e.es_default
            WHERE c.instrumento IS NOT NULL
              AND btrim(c.instrumento) <> btrim(e.simbolo)
            ORDER BY c.ticker
        """),
    Duplicado(
        id="emisor_curvas_vs_assets",
        que="el emisor del papel",
        a="mercado.curvas.emisor",
        b="portafolio.assets.emisor",
        arbitro="1816 (`jobs.ficha_1816` escribe LOS DOS, justamente por esto)",
        rompe="cualquier cosa que AGRUPE por emisor cuenta distinto según de dónde "
              "lea, y no se nota: las dos filas existen y suman bien por separado",
        sql="""
            SELECT c.ticker, coalesce(c.emisor, ''), coalesce(a.emisor, '')
            FROM mercado.curvas c
            JOIN portafolio.assets a ON upper(a.ticker) = upper(c.ticker)
            WHERE coalesce(btrim(c.emisor), '') <> ''
              AND coalesce(btrim(a.emisor), '') <> ''
              AND upper(btrim(c.emisor)) <> upper(btrim(a.emisor))
            ORDER BY c.ticker
        """),
)

# Cuántos sujetos divergentes se listan por duplicado. Si son más, se dice el
# total igual: **truncar en silencio se lee como «solo hay estos»**.
TOPE_EJEMPLOS = 8


def divergencias() -> dict:
    """Los duplicados que NO coinciden hoy, y los que no se pudieron mirar.

    `{"partidos": [...], "sin_mirar": [...], "revisados": n}`. Nunca levanta: es
    un chequeo, y un chequeo que se cae no puede tumbar al job que lo llama.
    """
    partidos: list[dict] = []
    sin_mirar: list[dict] = []
    for d in DUPLICADOS:
        try:
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(d.sql)
                filas = cur.fetchall()
        except Exception as e:
            # **«No pude mirar» se DECLARA.** Si esto se tragara la excepción y
            # siguiera, un duplicado sin chequear se leería igual que uno sano.
            logger.warning("duplicados: no pude chequear %s (%s)", d.id, e)
            sin_mirar.append({"id": d.id, "que": d.que,
                              "error": f"{type(e).__name__}: {str(e)[:160]}"})
            continue
        if not filas:
            continue
        partidos.append({
            "id": d.id, "que": d.que, "a": d.a, "b": d.b,
            "arbitro": d.arbitro, "rompe": d.rompe,
            "n": len(filas),
            "ejemplos": [{"sujeto": r[0], "valor_a": r[1], "valor_b": r[2]}
                         for r in filas[:TOPE_EJEMPLOS]],
        })
    return {"partidos": partidos, "sin_mirar": sin_mirar,
            "revisados": len(DUPLICADOS) - len(sin_mirar)}
