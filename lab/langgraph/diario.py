"""`lab/langgraph/diario.py` — EL LIBRO DE INVESTIGACIONES.

QUÉ ES UNA FILA
===============

**Una investigación hecha, con fecha.** No «lo que sabemos de M31G6» —eso se
pudre—: un hecho fechado, como `agente.acciones`. Investigar lo mismo mañana
crea OTRA fila, no un update. Así se puede ver que el 3/9 se concluyó una cosa
y el 10/9 otra, que es justamente lo interesante.

Es **append-only por permisos**, no por disciplina: `escritor_lab` tiene INSERT
y no tiene UPDATE ni DELETE. Un libro que se puede reescribir no es un libro.

LA IDENTIDAD ES `(tipo, caso)`
==============================

Misma idea que el trío `(habilidad, sujeto, regla)` del AV AGENT: sin una
identidad, dos filas sobre lo mismo no se juntan y la tabla no sirve para
buscar nada.

POR QUÉ NO ES UN BLOB
=====================

Lo que se filtra va en COLUMNA. `de_quien_es` con su CHECK, y sobre todo las
tres de la CALIDAD DE LA CORRIDA —`piso_cubierto`, `corto_por_presupuesto`,
`vueltas`— que dejan preguntar «¿cuáles investigaciones fueron flojas?» sin que
el modelo opine de sí mismo: son hechos sobre la corrida, igual que
`habilidades.ultimo_resultado`. El invariante #12 sigue intacto.

Y los CHECK son los que evitan el cementerio: **un veredicto sin fuentes no
entra**, igual que un hallazgo sin `que_hacer`.
"""
from __future__ import annotations

import logging
import pathlib

from lab.langgraph.base import escribir, leer

logger = logging.getLogger(__name__)

# ⚠️ **EL ESQUEMA YA NO VIVE ACÁ: vive en `sql/lab.sql`.**
#
# Vivía en esta constante «al lado de las queries que lo usan», y el argumento
# era bueno — pero se pagaba caro: se aplicaba COPIANDO EL TEXTO al editor de
# Supabase, así que `deploy.sh` no lo tocaba nunca. Media DDL del lab se
# autocuraba en cada deploy (los GRANT de las vistas, que viven en
# `sql/schema.sql`) y la otra media dependía de que alguien se acordara. Un
# `ALTER` nuevo anda local y en producción no existe, con un error que habla de
# otra cosa.
#
# Ahora `scripts/apply_schema.py` lo aplica en cada deploy, y `--sql` lo lee del
# MISMO archivo — una sola fuente (REGLA #9), no dos que pueden divergir.
_SQL_LAB = pathlib.Path(__file__).resolve().parents[2] / "sql" / "lab.sql"


def sql_esquema() -> str:
    """El DDL del lab, leído de `sql/lab.sql`. Lo imprime `correr.py --sql`."""
    try:
        return _SQL_LAB.read_text(encoding="utf-8")
    except OSError as e:
        return f"-- no pude leer {_SQL_LAB}: {e}"

# Los campos del veredicto que van a la base, EN EL ORDEN del INSERT. Se
# derivan del modelo para que agregar un campo arriba no obligue a acordarse
# de dos listas.
_CAMPOS = ("titulo", "de_quien_es", "que_paso", "por_que", "que_haria",
           "lo_que_no_se", "de_donde")


def guardar(tipo: str, caso: str, veredicto: dict, *, herramientas: list[str],
            piso_cubierto: bool, corto: bool, vueltas: int,
            modelo: str = "", por: str = "") -> dict:
    """Anota la investigación. **Nunca levanta hacia el que la llamó.**

    Que no se pueda guardar no invalida lo que se averiguó: la investigación
    igual sirve. Pero el fallo se DICE —vuelve en `error`— porque tragárselo
    dejaría creer que quedó registrada.
    """
    if veredicto is None:
        return {"ok": False, "error": "no hay veredicto que guardar"}
    try:
        from lab.langgraph.veredicto import LISTAS
        # Una lista que llega vacía se guarda vacía y el CHECK la rechaza: es
        # lo que queremos. Lo que NO puede pasar es mandar `None` a una columna
        # NOT NULL y que el error hable de otra cosa.
        valores = [list(veredicto.get(c) or []) if c in LISTAS
                   else (veredicto.get(c) or "") for c in _CAMPOS]
        f = escribir(
            "INSERT INTO lab.investigaciones "
            " (tipo, caso, titulo, de_quien_es, que_paso, por_que, que_haria, "
            "  lo_que_no_se, de_donde, herramientas_usadas, piso_cubierto, "
            "  corto_por_presupuesto, vueltas, modelo, por) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (tipo, caso, *valores, list(herramientas),
             piso_cubierto, corto, int(vueltas), modelo, por))
        return {"ok": True, "id": f[0] if f else None}
    except Exception as e:
        logger.warning("diario: no pude guardar (%s)", e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


def anteriores(tipo: str, caso: str, limite: int = 3) -> list[dict]:
    """Lo que YA se investigó sobre este mismo caso. Vacío si no hay o si no se
    pudo leer — y esa diferencia NO se puede tapar acá, así que el que llama
    recibe una lista y no una afirmación de que no existe nada."""
    r = leer(
        "SELECT id, to_char(at,'YYYY-MM-DD HH24:MI') AS cuando, de_quien_es, "
        "       titulo, que_haria, lo_que_no_se, piso_cubierto, "
        "       corto_por_presupuesto "
        "  FROM lab.investigaciones WHERE tipo = %s AND upper(caso) = upper(%s) "
        " ORDER BY at DESC LIMIT %s", (tipo, caso, int(limite)))
    if isinstance(r, str):
        logger.info("diario: sin antecedentes (%s)", r)
        return []
    cols, filas = r
    return [dict(zip(cols, f, strict=True)) for f in filas]


def historial(tipo: str = "", caso: str = "", limite: int = 20) -> str:
    """El libro, para mirarlo desde la terminal."""
    cond, params = [], []
    if tipo:
        cond.append("tipo = %s")
        params.append(tipo)
    if caso:
        cond.append("upper(caso) = upper(%s)")
        params.append(caso)
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    r = leer("SELECT to_char(at,'YYYY-MM-DD HH24:MI') AS cuando, tipo, caso, "
             "       de_quien_es, piso_cubierto, corto_por_presupuesto, "
             "       vueltas, que_haria "
             f"  FROM lab.investigaciones{where} ORDER BY at DESC LIMIT %s",
             (*params, int(limite)))
    if isinstance(r, str):
        return r
    _cols, filas = r
    if not filas:
        return "todavía no hay investigaciones guardadas."
    out = [f"{'CUÁNDO':17} {'TIPO':14} {'CASO':12} {'DE QUIÉN':10} PISO CORTE VUELTAS"]
    for f in filas:
        out.append(f"{f[0]:17} {f[1]:14} {f[2]:12} {f[3]:10} "
                   f"{'sí' if f[4] else 'NO':4} {'sí' if f[5] else 'no':5} {f[6]:>7}")
        out.append(f"      → {' '.join(str(f[7]).split())[:100]}")
    return "\n".join(out)
