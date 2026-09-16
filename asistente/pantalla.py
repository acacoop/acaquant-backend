"""El contrato entre una herramienta y la PANTALLA: qué se dibuja y qué se le
avisa al modelo que ya se dibujó. Doc: docs/AvAgentAI.md §12.

El resultado de una herramienta tiene DOS destinatarios y no son el mismo:

  · el MODELO, que lo lee para redactar (`herramientas.para_el_modelo`);
  · la PANTALLA, que dibuja una tabla con él (`_tabla`, que al modelo se le
    oculta porque son instrucciones de dibujo, no datos).

Que uno de los dos no supiera del otro es lo que hacía que el texto enumerara
cinco bonos mientras la tabla mostraba ocho filas: dos salidas del mismo
resultado, sin nadie que las coordine. Por eso acá viven las tres piezas
juntas — cómo se declara, qué ve la pantalla y qué se le avisa al modelo — y
no repartidas en cada agente.
"""
from __future__ import annotations

MAX_FILAS = 200


def tabla(campo: str, columnas: list[str], titulo: str, *,
          total: str | None = None, moneda: str | None = None) -> dict:
    """La declaración de tabla de una herramienta, para su clave `_tabla`.

    `titulo` es OBLIGATORIO y va por firma: una tabla sin sujeto es un dato sin
    dueño. Cuando en un turno corren dos herramientas salen dos tablas pegadas,
    y sin título no se sabe cuál es de qué («¿de qué bono son estos flujos?»).
    Lo sabe la herramienta en el momento de armarla; deducirlo después es
    adivinar.

    Args:
        campo: qué clave del resultado es la lista de filas.
        columnas: qué columnas dibujar, en orden.
        titulo: de qué es esta tabla («Tenencia de la 805», «Flujos del YM38O»).
        total: la clave del resultado con el total, si hay uno.
        moneda: en qué moneda están los importes, si son de una sola.
    """
    if not campo or not titulo or not str(titulo).strip():
        raise ValueError("una tabla necesita `campo` y `titulo`")
    d = {"campo": campo, "columnas": list(columnas), "titulo": " ".join(str(titulo).split())}
    if total:
        d["total"] = total
    if moneda:
        d["moneda"] = moneda
    return d


def declarada(resultado) -> tuple[dict, list] | None:
    """(declaración, filas) si este resultado trae una tabla dibujable, o None.
    Una tabla sin filas no es una tabla: la pantalla no dibuja nada y al modelo
    no hay que avisarle de algo que no se ve."""
    if not isinstance(resultado, dict):
        return None
    decl = resultado.get("_tabla")
    if not isinstance(decl, dict) or not decl.get("campo"):
        return None
    filas = resultado.get(decl["campo"])
    if not isinstance(filas, list) or not filas:
        return None
    return decl, filas


def para_dibujar(resultado) -> dict | None:
    """La tabla lista para la pantalla. Columnas, filas y total salen del
    resultado: acá no se calcula nada, para que la tabla y la prosa no puedan
    decir cosas distintas."""
    if (d := declarada(resultado)) is None:
        return None
    decl, filas = d
    return {"titulo": decl.get("titulo") or "",
            "columnas": list(decl.get("columnas") or []),
            "filas": filas[:MAX_FILAS], "cuantas": len(filas),
            "total": resultado.get(decl["total"]) if decl.get("total") else None,
            "moneda": decl.get("moneda")}


def aviso(resultado) -> str | None:
    """Lo ÚNICO de la tabla que viaja al modelo: que existe, de qué es y cuántas
    filas tiene. No los datos — esos ya los tiene en el resultado.

    Existe porque la instrucción «si hay tabla no la enumeres» era inejecutable:
    `_tabla` es justo lo que se le oculta, así que se le pedía evaluar una
    condición sobre un dato que no recibe. Con esto la puede evaluar.

    Y es PREVENTIVO, no descriptivo: además de «no la repitas», dice que no
    pida otra herramienta que vuelva a dibujar lo mismo. Dibujar una tabla no
    es una acción del modelo —es un efecto de un payload que no ve—, así que
    llamar dos herramientas con tabla le sale gratis y al usuario le aparecen
    dos tablas pegadas de lo mismo. Medido en el LAB: `alternativas_para_rotar`
    contestó la rotación y el modelo llamó igual a `instrumentos_de_la_curva`
    para «completar»."""
    if (d := declarada(resultado)) is None:
        return None
    decl, filas = d
    cols = ", ".join(decl.get("columnas") or [])
    return (f"La pantalla YA le está mostrando al usuario la tabla «{decl.get('titulo')}» "
            f"con {len(filas)} fila(s) y estas columnas: {cols}. No la repitas en el texto, "
            f"y NO llames otra herramienta para volver a mostrar lo mismo: serían dos tablas "
            f"pegadas de lo mismo.")
