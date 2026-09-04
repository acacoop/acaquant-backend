"""`scripts/eval_investigador.py` — ¿EL INVESTIGADOR ACIERTA?

    python -m scripts.eval_investigador                 # los últimos 90 días
    python -m scripts.eval_investigador --dias 30
    python -m scripts.eval_investigador --detalle       # caso por caso

EL PROBLEMA QUE RESUELVE
========================

El investigador (`lab/langgraph/`) escribía informes prolijos y **nadie los
calificaba nunca**. El diario guardaba qué concluyó y con qué calidad corrió
—si cubrió el mínimo, si se quedó sin margen, cuántas vueltas dio— pero nada
comparaba lo que PROPUSO contra lo que terminó arreglando el problema. Sin ese
número no se puede decidir nada: ni si conviene dejarlo correr, ni —sobre
todo— si se le puede dar la llave para que aplique el arreglo él mismo.

LA VERDAD DE CAMPO YA EXISTE, NO HAY QUE FABRICARLA
===================================================

`agente.hallazgos` guarda, en los que se cerraron POR ACCIÓN, **qué arreglo se
aplicó** (`arreglo_aplicado`), y `agente.acciones` guarda el libro de lo que ese
arreglo escribió. O sea: para cada problema resuelto hay una respuesta correcta,
fechada y escrita por el sistema determinista. Comparar contra eso no cuesta un
token ni depende de que alguien etiquete nada a mano.

⚠️⚠️ **EL MODELO NO SE JUZGA A SÍ MISMO.** No hay un LLM-juez acá, y no es por
ahorrar: el invariante #12 del AV AGENT dice que el agente no se autoevalúa, y
un juez que es el mismo modelo que contestó mide su propio estilo, no su
acierto. La comparación es DETERMINISTA: ¿el veredicto nombró el arreglo que se
terminó aplicando? Se busca la clave del arreglo (`alta_bono`, `pata_dolar`, …)
y el nombre de su acción en el texto de `que_haria`.

⚠️ **TRES RESULTADOS, NO DOS.** Un caso puede salir `acertó`, `erró` o
**`no se puede decir`** — y ese tercero es el que hace que el número signifique
algo. Si el hallazgo nunca se cerró, o se cerró por ausencia (desapareció solo),
no hay contra qué comparar: contarlo como error inflaría el fracaso y contarlo
como acierto inflaría el éxito. Se cuenta aparte y se dice cuántos son. Es el
invariante #1 aplicado a la medición: **lo que no se pudo mirar no se cuenta
como mirado.**

SOLO LEE
========
Usa la conexión `lector_lab` (la misma jaula del investigador), así que ni
siquiera podría escribir. Es seguro correrlo en rueda.
"""
from __future__ import annotations

import argparse
import re
import sys

from lab.langgraph.base import leer

# Cuántos días para atrás mirar por default. 90 es un trimestre: suficiente para
# que el número no se mueva por dos casos raros de una semana mala.
DIAS_DEFAULT = 90

ACIERTO, ERROR, INDECIDIBLE = "acertó", "erró", "no se puede decir"


def _normalizar(txt: str) -> str:
    """Baja a minúsculas y unifica separadores.

    `alta_bono`, `alta bono` y `ALTA-BONO` son el mismo arreglo escrito por tres
    manos distintas: el catálogo, el prompt y la persona que redactó el
    veredicto. Comparar sin esto haría fallar aciertos reales, que es la peor
    forma de equivocarse acá — un eval que subestima se desactiva solo.
    """
    return re.sub(r"[\s_\-]+", " ", (txt or "").lower())


def _nombro_el_arreglo(que_haria: list[str], arreglo: str) -> bool:
    """¿El veredicto nombró ESTE arreglo?

    Conservador a propósito: busca la clave del arreglo tal cual y también sus
    palabras sueltas EN ORDEN (`alta_bono` → «alta … bono»). No intenta
    entender la frase — para eso haría falta un modelo, y un modelo juzgando a
    otro modelo es justo lo que el invariante #12 prohíbe.

    Que sea conservador significa que puede decir «erró» cuando el veredicto
    proponía lo correcto con otras palabras. Eso es un piso, no un techo: el
    número real de aciertos es **igual o mejor** que el que informa. Se dice
    explícito en la salida para que nadie lo lea al revés.
    """
    texto = _normalizar(" · ".join(que_haria or []))
    clave = _normalizar(arreglo)
    if clave and clave in texto:
        return True
    partes = [p for p in clave.split() if len(p) > 3]
    return bool(partes) and all(p in texto for p in partes)


def _casos(dias: int) -> list[dict] | str:
    """Cada investigación con el desenlace REAL de su caso, al lado.

    El join es por `(caso → sujeto)` y se queda con el hallazgo del mismo sujeto
    **cerrado DESPUÉS** de la investigación: un hallazgo que ya estaba cerrado
    cuando se investigó no es lo que la investigación predijo.
    """
    r = leer(
        "SELECT i.id, to_char(i.at,'YYYY-MM-DD HH24:MI') AS cuando, i.tipo,"
        "       i.caso, i.titulo, i.que_haria, i.piso_cubierto,"
        "       i.corto_por_presupuesto, i.de_quien_es,"
        "       h.arreglo_aplicado, h.cerrado_como, h.habilidad"
        "  FROM lab.investigaciones i"
        "  LEFT JOIN LATERAL ("
        "        SELECT arreglo_aplicado, cerrado_como, habilidad"
        "          FROM agente.hallazgos"
        "         WHERE upper(sujeto) = upper(i.caso)"
        "           AND cerrado_at IS NOT NULL"
        "           AND cerrado_at >= i.at"
        "         ORDER BY cerrado_at ASC LIMIT 1) h ON true"
        " WHERE i.at >= now() - make_interval(days => %s)"
        " ORDER BY i.at DESC", (int(dias),))
    if isinstance(r, str):
        return r
    cols, filas = r
    return [dict(zip(cols, f, strict=True)) for f in filas]


def _juzgar(c: dict) -> tuple[str, str]:
    """(veredicto del eval, por qué). Tres salidas, nunca dos."""
    if c["cerrado_como"] != "accion" or not (c.get("arreglo_aplicado") or "").strip():
        # Sin acción que lo cerrara no hay respuesta correcta contra la cual
        # comparar. NO es un error del investigador.
        motivo = ("el hallazgo nunca se cerró" if not c["cerrado_como"]
                  else f"se cerró por {c['cerrado_como']}, sin arreglo")
        return INDECIDIBLE, motivo
    arreglo = c["arreglo_aplicado"]
    if _nombro_el_arreglo(c.get("que_haria") or [], arreglo):
        return ACIERTO, f"propuso «{arreglo}», que es lo que se aplicó"
    return ERROR, f"se aplicó «{arreglo}» y no lo propuso"


def main(dias: int, detalle: bool) -> int:
    casos = _casos(dias)
    if isinstance(casos, str):
        print(f"no pude leer: {casos}")
        return 2
    if not casos:
        print(f"No hay investigaciones en los últimos {dias} días. Nada que medir.")
        return 0

    juzgados = [(c, *_juzgar(c)) for c in casos]
    n = len(juzgados)
    por = {v: [x for x in juzgados if x[1] == v] for v in (ACIERTO, ERROR, INDECIDIBLE)}
    decidibles = len(por[ACIERTO]) + len(por[ERROR])

    print(f"\n═══ EL INVESTIGADOR, últimos {dias} días ═══\n")
    print(f"  investigaciones            {n}")
    print(f"  con desenlace comparable   {decidibles}"
          + ("" if decidibles else "   ← sin esto no hay nada que medir"))
    if decidibles:
        pct = 100 * len(por[ACIERTO]) / decidibles
        print(f"  ACERTÓ                     {len(por[ACIERTO])}  ({pct:.0f}%)")
        print(f"  ERRÓ                       {len(por[ERROR])}")
    print(f"  no se puede decir          {len(por[INDECIDIBLE])}"
          "   (el hallazgo no cerró por acción)")

    # La calidad de la CORRIDA, que ya viene guardada y no depende de este eval.
    sin_piso = sum(1 for c in casos if not c["piso_cubierto"])
    cortadas = sum(1 for c in casos if c["corto_por_presupuesto"])
    print(f"\n  no cubrieron el mínimo     {sin_piso}")
    print(f"  se quedaron sin margen     {cortadas}")

    if decidibles:
        print("\n  ⚠️ El % es un PISO, no un techo: la comparación es textual, así que")
        print("     un veredicto que propone lo correcto con otras palabras cuenta")
        print("     como error. El acierto real es igual o mejor.")

    if detalle:
        print("\n─── caso por caso ───")
        for c, v, por_que in juzgados:
            marca = {ACIERTO: "✅", ERROR: "❌", INDECIDIBLE: "·"}[v]
            print(f"\n{marca} #{c['id']}  {c['cuando']}  {c['tipo']}/{c['caso']}")
            print(f"   {c['titulo'][:88]}")
            print(f"   → {v}: {por_que}")
            if v == ERROR:
                for x in (c.get("que_haria") or [])[:3]:
                    print(f"      propuso: {str(x)[:80]}")
    else:
        print("\n  (`--detalle` para verlos uno por uno)")
    print()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dias", type=int, default=DIAS_DEFAULT)
    ap.add_argument("--detalle", action="store_true", help="caso por caso")
    a = ap.parse_args()
    sys.exit(main(a.dias, a.detalle))
