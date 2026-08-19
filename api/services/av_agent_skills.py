"""api/services/av_agent_skills.py — EL REGISTRO ÚNICO DE HABILIDADES.

Doc madre: **`docs/AV_AGENT.md`** §0.o.

**LA LEY** (user, 2026-08-19):

    *«Necesito que se vaya centralizando todo: no solo esto, también lo que sabe
    resolver, lo que va entendiendo cuando encuentra algo… va a haber distintos
    tipos de habilidad pero POR LEY Y REGLA todo lo nuevo que se agregue de
    funcionalidad o habilidad tiene que quedar en esta tab, para que se vaya
    mapeando todo lo que va consolidando. Y a su vez dejar asentado si esa skill
    usa para algo IA o no, ya que muchas son solamente una función.»*

POR QUÉ SE **DERIVA** Y NO SE ESCRIBE A MANO
=============================================

Una lista de capacidades mantenida a mano se queda vieja **la primera vez que
alguien tiene apuro**, y una lista de capacidades desactualizada es peor que no
tenerla: dice que el agente sabe algo que no sabe, o esconde algo que sí. Es el
mismo error que este proyecto ya pagó con `MAPA_APP.md` —por eso su §0 se
autogenera— y con `deploy/SISTEMA.md`.

Así que el catálogo **se arma leyendo los registros que ya existen**:

    EXPLICADORES  (av_agent_explicar)  →  contestar una pregunta
    ACCIONES      (av_agent_hacer)     →  arreglar algo, con tu OK
    DETECTORES    (av_agent + controles_datos)  →  darse cuenta solo

Una skill nueva aparece acá **sola**, por existir. No hay forma de agregar una
capacidad y olvidarse de mapearla, porque no hay nada que acordarse de hacer.

POR QUÉ IMPORTA DECIR SI USA IA
================================

El user lo pidió explícito, y no es una curiosidad técnica: **cambia cuánto hay
que desconfiar**. Una skill determinista da el mismo resultado siempre y se
audita leyendo el código una vez; una que pasa por el modelo hay que mirarla
caso por caso. Y hay una tercera categoría que es la más común acá y la que se
suele contar mal: **`opcional`** — la parte que resuelve es una función, y el
modelo solo agrega la frase o cubre lo que la regla no supo. Si el modelo no
está, la skill **sigue funcionando**.

Contarlas todas como «IA» infla lo que el modelo hace de verdad; contarlas como
«no IA» esconde dónde hay que mirar. Por eso son tres valores y no un booleano.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Los tres tipos de habilidad, en el orden en que crece el agente:
# darse cuenta → poder explicarlo → saber arreglarlo.
DETECTAR, EXPLICAR, RESOLVER = "detectar", "explicar", "resolver"

# Cuánta IA hay adentro. TRES valores y no un booleano, a propósito (ver arriba).
SIN_IA, IA_OPCIONAL, CON_IA = "no", "opcional", "si"


@dataclass
class Skill:
    id: str
    tipo: str
    nombre: str          # en castellano, como lo diría una persona
    que_hace: str
    usa_ia: str          # no | opcional | si
    para_que_la_ia: str = ""     # obligatorio si usa_ia != "no"
    donde: str = ""              # dónde se ve o dónde escribe
    fuente: str = ""             # de qué registro salió (trazabilidad)
    extra: dict = field(default_factory=dict)


def _de_explicadores() -> list[Skill]:
    """Contestar una pregunta. **La IA es OPCIONAL en todas**: los números salen
    del cálculo determinista y el modelo solo redacta la frase — si no está, la
    explicación sale igual."""
    from api.services import av_agent_explicar as ex
    return [Skill(
        id=f"explicar.{e.id}", tipo=EXPLICAR, nombre=e.pregunta,
        que_hace=f"reproduce el cálculo paso a paso desde {e.de_donde}",
        usa_ia=IA_OPCIONAL,
        para_que_la_ia="resume el resultado en una frase; los números NO los "
                       "toca el modelo",
        donde="AV Agent → SKILLS", fuente="av_agent_explicar.EXPLICADORES",
        extra={"necesita": e.necesita},
    ) for e in ex.EXPLICADORES.values()]


def _de_acciones() -> list[Skill]:
    """Arreglar algo, con el OK del humano. Acá el uso de IA **varía por
    acción** y por eso se declara una por una en vez de asumirlo."""
    from api.services import av_agent_hacer as hc
    # Qué acciones llaman al modelo cuando la regla no alcanza. Se declara
    # EXPLÍCITO y no se adivina leyendo el código: una skill que dice "no usa
    # IA" y la usa es exactamente la clase de afirmación que no puede ser una
    # inferencia frágil.
    con_modelo = {
        "assets.cartera": "cuando ningún patrón del nombre alcanza, le pide al "
                          "modelo elegir de la lista CERRADA de 8 carteras",
    }
    out = []
    for a in hc.ACCIONES.values():
        motivo = con_modelo.get(a.id, "")
        out.append(Skill(
            id=f"resolver.{a.id}", tipo=RESOLVER, nombre=a.titulo,
            que_hace=f"resuelve «{a.sobre}» proponiendo {a.campo}; se aplica con "
                     f"tu OK y se verifica releyendo la base",
            usa_ia=IA_OPCIONAL if motivo else SIN_IA,
            para_que_la_ia=motivo, donde=a.donde,
            fuente="av_agent_hacer.ACCIONES",
            extra={"control": a.sobre, "campo": a.campo}))
    return out


# Los DETECTORES no viven en un registro con metadatos como los otros dos (son
# funciones sueltas del motor y controles de un job), así que la descripción es
# lo único que va a mano. **La LISTA no**: se cruza contra lo que existe de
# verdad y un test falla si alguien suma un detector y no lo describe acá — que
# es la forma de que la ley se cumpla sin depender de que alguien se acuerde.
_QUE_DETECTA: dict[str, str] = {
    "falta_en_base": "bonos que 1816 lista y nosotros no tenemos en el master",
    "sin_flujo": "bonos cargados sin cronograma de pagos: no valúan",
    "tasa_sospechosa": "tasas que se apartan de las de 1816 más de lo tolerable",
    "hueco_de_curva": "ajustes que existen en el master pero no tienen pill: "
                      "esos bonos quedan invisibles sin dar ningún error",
    "salud": "jobs que no corrieron, fallaron o dejaron el dato viejo",
    "sin_precio": "bonos sin precio, distinguiendo las 5 causas (sin símbolo · "
                  "fuera de Primary · pata equivocada · nunca operó · sin "
                  "actividad hoy)",
    "precio_moneda": "precios que llegan en la moneda equivocada para su curva",
    "db_cambio": "tablas NUEVAS, las que crecieron de golpe y las que "
                 "desaparecieron, comparando la foto de hoy contra la de ayer",
    "latencia": "endpoints que se pusieron lentos contra SU PROPIA normalidad "
                "(no un ranking de los más lentos) y los que devuelven 5xx",
    "tabla_quieta": "tablas que dejaron de escribir cuando deberían estar "
                    "escribiendo — la cadencia de cada una se MIDE observándola, "
                    "no la declara nadie",
    "motor_caido": "motores, jobs y APIs rotos DENTRO de su ventana horaria "
                   "(fuera de rueda un motor no está caído, está apagado)",
}


def _de_detectores() -> list[Skill]:
    """Darse cuenta solo. **Ninguno usa IA**, y eso es lo importante de esta
    lista: lo que encuentra el agente lo encuentra una función determinista que
    corre sola. El modelo aparece después, para leer patrones entre hallazgos."""
    from api.services import av_agent
    return [Skill(
        id=f"detectar.{tipo}", tipo=DETECTAR,
        nombre=_QUE_DETECTA.get(tipo, tipo).capitalize(),
        que_hace=_QUE_DETECTA.get(tipo, "sin describir"),
        usa_ia=SIN_IA, donde="AV Agent → ENCONTRÓ",
        fuente="av_agent.ACCION_POR_TIPO",
        extra={"accion": av_agent.ACCION_POR_TIPO.get(tipo)},
    ) for tipo in av_agent.ACCION_POR_TIPO]


def _de_controles() -> list[Skill]:
    """Los invariantes de datos que corren todas las noches. Son detección
    también — solo que sobre el negocio y no sobre el mercado — y se
    re-verifican solos: lo que se arregla desaparece sin que nadie lo marque."""
    try:
        from jobs.controles_datos import CONTROLES
    except Exception:
        logger.warning("av_agent_skills: no pude leer los controles")
        return []
    from api.services import av_agent_hacer as hc
    return [Skill(
        id=f"detectar.control.{c.id}", tipo=DETECTAR, nombre=c.titulo,
        que_hace="control de datos: se re-verifica todos los días y lo que se "
                 "resuelve desaparece solo",
        usa_ia=SIN_IA, donde="AV Agent → ENCONTRÓ",
        fuente="jobs.controles_datos.CONTROLES",
        extra={"resuelve": hc.POR_CONTROL.get(c.id)},
    ) for c in CONTROLES]


def catalogo() -> list[Skill]:
    """TODO lo que el agente sabe hacer, derivado de los registros reales."""
    out: list[Skill] = []
    for fn in (_de_detectores, _de_controles, _de_explicadores, _de_acciones):
        try:
            out.extend(fn())
        except Exception as e:
            logger.warning("av_agent_skills: %s falló: %s", fn.__name__, e)
    return out


def vista() -> dict:
    """Lo que dibuja la tab SKILLS: las habilidades agrupadas por tipo + el
    recuento de cuántas pasan por el modelo.

    Ese recuento es el que contesta, sin discutir, *«¿cuánto de esto es IA de
    verdad?»* — la pregunta que se hace el user cada vez que mira el programa."""
    skills = catalogo()
    por_tipo: dict[str, list[dict]] = {DETECTAR: [], EXPLICAR: [], RESOLVER: []}
    for s in skills:
        por_tipo.setdefault(s.tipo, []).append({
            "id": s.id, "nombre": s.nombre, "que_hace": s.que_hace,
            "usa_ia": s.usa_ia, "para_que_la_ia": s.para_que_la_ia,
            "donde": s.donde, "fuente": s.fuente, "extra": s.extra})
    return {
        "total": len(skills),
        "por_tipo": por_tipo,
        "ia": {
            "no": sum(1 for s in skills if s.usa_ia == SIN_IA),
            "opcional": sum(1 for s in skills if s.usa_ia == IA_OPCIONAL),
            "si": sum(1 for s in skills if s.usa_ia == CON_IA),
        },
        # Las tareas de IA vivas, para poder contrastar lo que las skills DICEN
        # que usan contra lo que el gateway tiene registrado. Si una skill
        # declara IA y no hay tarea, alguien se equivocó en algún lado.
        "tareas_ia": _tareas_ia(),
    }


def _tareas_ia() -> list[str]:
    try:
        from core.ai import _TAREAS
        return sorted(_TAREAS)
    except Exception:
        return []
