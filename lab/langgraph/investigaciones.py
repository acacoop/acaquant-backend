"""`lab/langgraph/investigaciones.py` — EL MÉTODO. Una fila por tipo de caso.

EL PROBLEMA QUE RESUELVE
========================

Tres corridas de la MISMA pregunta tomaron tres caminos distintos: una fue al
comentario del código, otra grepeó de más, otra leyó la entrada equivocada del
diario y ni miró si el job había corrido. Todas llegaron parecido. **Y ese es
el problema: no se puede confiar en alguien cuyo método cambia cada vez, aunque
suela acertar.**

EL PISO, QUE NO ES UN GUION
===========================

Cada tipo de investigación declara **lo que hay que haber mirado antes de poder
concluir**. No en qué orden, no qué más — un MÍNIMO. Arriba de eso el modelo
sigue siendo libre, que es para lo que sirve.

Y no se pide: **se verifica**. El motor mira qué herramientas se ejecutaron de
verdad —no si el modelo dice que las miró— y si falta alguna del piso, no lo
deja concluir: lo manda de vuelta nombrando el faltante.

Es el invariante #1 del AV AGENT aplicado a una investigación: **no se concluye
sobre lo que no se miró.**

SUMAR UN TIPO ES UNA FILA
=========================

Como `agente/catalogo.py`. Nadie toca el grafo, ni el prompt, ni una lista
paralela en otro archivo. Y el piso se valida contra las herramientas que
existen de verdad al importar el módulo: un piso que nombra una herramienta
inexistente no llega a producción, revienta acá.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Investigacion:
    """UN tipo de caso, con todo lo que hay que saber de él."""

    nombre: str
    que_es: str
    # Cómo se le plantea el caso al modelo. `{caso}` se reemplaza por el sujeto.
    pregunta: str
    # Las herramientas que hay que haber llamado para poder concluir.
    piso: tuple[str, ...] = ()
    # Qué NO alcanza con suponer. Va al prompt: es la parte del método que se
    # explica, mientras que el piso es la parte que se verifica.
    ojo_con: tuple[str, ...] = field(default_factory=tuple)

    def falta(self, usadas: set[str]) -> list[str]:
        return [h for h in self.piso if h not in usadas]


INVESTIGACIONES: dict[str, Investigacion] = {i.nombre: i for i in (

    Investigacion(
        nombre="reincidencia",
        que_es="algo que el agente dio por arreglado y volvió",
        pregunta="¿Por qué reincidió «{caso}»? El arreglo que se aplicó no "
                 "sirvió: averiguá por qué y decime qué hacer.",
        # Sin las corridas de los jobs no se puede afirmar que otro proceso
        # nuestro haya intervenido — y esa es la causa más común.
        piso=("reincidencias", "hallazgos_del_sujeto", "acciones_sobre",
              "corridas_del_job", "codigo_de"),
        ojo_con=("La causa más común NO es un bug: son dos procesos nuestros "
                 "que se contradicen. Mirá qué otra cosa corrió en el medio.",
                 "Un comentario del código que afirma algo no es prueba de que "
                 "eso sea cierto hoy. Si no leíste las dos partes, decilo.")),

    Investigacion(
        nombre="bono_sin_precio",
        que_es="un bono que aparece sin precio o con el precio viejo",
        pregunta="¿Por qué «{caso}» no tiene precio? Distinguí si no está "
                 "suscripto, si no tiene punta, o si el dato está viejo.",
        piso=("ficha_del_bono", "precio_del_simbolo", "hallazgos_del_sujeto"),
        ojo_con=("Que no haya fila en market_snapshot NO prueba que el bono no "
                 "cotice: esa tabla sólo guarda lo que el motor pidió.",
                 "El ticker corto y el símbolo de mercado son distintos, y el "
                 "símbolo puede ser la pata en dólares.")),

    Investigacion(
        nombre="job",
        que_es="un job que falló, no corrió, o dejó el dato viejo",
        pregunta="¿Qué le pasa al job «{caso}»? Averiguá si corrió, si dejó "
                 "el dato, y de quién es el problema.",
        piso=("corridas_del_job", "hallazgos_del_sujeto", "buscar_en_repo"),
        ojo_con=("Que el proceso salga con error NO significa que no haya "
                 "escrito, y que salga en verde no significa que sí. Lo que "
                 "importa es si el DATO está.",)),

    # Sin piso: es la puerta para preguntar cualquier cosa. Existe declarada y
    # no como un `if` suelto — así el modo libre es una decisión visible y no
    # el agujero por el que se escapa el método.
    Investigacion(
        nombre="libre",
        que_es="una pregunta suelta, sin método declarado",
        pregunta="{caso}"),
)}


def _validar() -> None:
    """El piso no puede nombrar una herramienta que no existe.

    Se chequea AL IMPORTAR y no en un test: un piso roto haría que el motor
    pida para siempre algo que nadie puede darle, y el síntoma sería un agente
    que da vueltas sin concluir — un bug carísimo de diagnosticar por lo lejos
    que queda la causa del efecto.
    """
    from lab.langgraph.herramientas import HERRAMIENTAS
    existen = {h.name for h in HERRAMIENTAS}
    for inv in INVESTIGACIONES.values():
        if faltan := [h for h in inv.piso if h not in existen]:
            raise RuntimeError(
                f"la investigación «{inv.nombre}» exige {faltan}, que no "
                f"existe(n) como herramienta. Disponibles: {sorted(existen)}")


_validar()
