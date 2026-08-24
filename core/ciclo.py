"""core/ciclo.py — EL CICLO DE VIDA DE UNA COSA QUE EL AGENTE ENCONTRÓ O DIJO.

Doc madre: **`docs/AV_AGENT.md`** §0.bc.

POR QUÉ EXISTE
==============

El user (2026-08-21), después de la quinta corrección seguida sobre lo mismo:

    *«Los avisos, lo que encuentra, los mensajes… deberían estar codeados como
    OBJETOS CON SUS ESTADOS. Porque si no, esto va a escalar mal y siempre se va
    a solucionar sobre la marcha.»*

Tenía razón y el número lo dice: **22 tablas del agente, 8 formas distintas de
decir las mismas tres cosas** (está abierto · lo vi · se resolvió).

    resuelto_at IS NULL     controles_datos · av_agent_avisos · av_agent_centinela
    resuelto boolean        av_agent_avisos               (¡las DOS en la misma!)
    estado text             av_agent_preguntas · av_agent_runs · av_agent_propuestas
    hecho boolean           av_agent_aviso_items
    visto_at                salud_vistos · av_agent_centinela
    ok boolean              av_agent_acciones · manager.proveedor_estado
    aplicada_at             av_agent_preguntas · av_agent_propuestas
    la EXISTENCIA de la fila   av_agent_ignorados y 11 más

Y la más importante de todas —**`av_agent_hallazgos`, la que llena ENCONTRÓ**—
**no tiene estado**: es una FOTO con `corrida_at`. Todo su ciclo de vida
(atendido · visto · ignorado · vencido · ya votado) se **deriva en la lectura**,
cruzando otras cinco tablas, en funciones distintas.

    De ahí salen los bugs de esta semana, y son todos el mismo bug:
    dos pantallas derivando el mismo estado con criterios distintos.

`atendido`, `recien`, `ya_votado`, `sin_puerta`, `resuelto` — cinco
derivaciones escritas en cinco lugares en cinco días. Ninguna falla sola; se
contradicen entre ellas, que es el modo de falla de REGLA #9.

QUÉ HACE ESTO, Y QUÉ NO
=======================

**NO migra ninguna tabla.** Migrar 22 tablas de un saque es cómo se rompe un
sistema que funciona. Lo que hace es lo que ya funcionó tres veces en este repo
(`api/superficie.py`, `core/duplicados`, `core/escribe`):

    1. declarar el ciclo UNA vez y en un solo lugar;
    2. declarar CÓMO lo dice hoy cada tabla, con su traducción;
    3. un test que FALLA cuando aparece una tabla nueva sin declarar.

Con eso, la próxima pantalla no inventa su propia idea de «resuelto»: pregunta.
Y la deuda deja de ser invisible — se puede contar (`sin_migrar()`).

⚠️ **Lo que esto NO arregla**: las derivaciones que ya existen siguen donde
están hasta que cada superficie se mueva acá. Esto frena la sangría; no cura la
herida. La migración va tabla por tabla, y `sin_migrar()` dice cuántas faltan.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── LOS ESTADOS. Son SEIS y no se agregan a la ligera ───────────────────────
#
# Cada uno existe porque se ATIENDE distinto. Si dos se atienden igual, sobra
# uno: un estado de más es una rama de más en cada pantalla, para siempre.
NUEVO = "nuevo"          # apareció y nadie lo miró
VISTO = "visto"          # alguien lo miró y no hizo nada todavía
EN_CURSO = "en_curso"    # se actuó y falta la respuesta (el mercado, un job)
RESUELTO = "resuelto"    # ya no está: el problema se fue
IGNORADO = "ignorado"    # una persona dijo «no me interesa» — reversible
VOLVIO = "volvio"        # estaba resuelto y reapareció: NO es lo mismo que nuevo

# ── CÓMO se cerró. Son DOS y no significan lo mismo ─────────────────────────
#
# ⚠️⚠️ La distinción que faltaba, y que §0.de dejó como condición para volver a
# medir el tiempo. `resuelto` nunca quiso decir «alguien lo arregló»:
#
#   POR_ACCION    alguien apretó ARREGLAR y DESPUÉS el detector dejó de verlo.
#                 Si eso aguanta los hitos, el diagnóstico era correcto → VOTA.
#   POR_AUSENCIA  el detector no lo vio en esta corrida y nada más. Un bono que
#                 no operó esa noche se auto-resuelve. NO prueba nada → NO vota.
#
# Meterlos en el mismo saco es lo que hizo que el ✖ acusara a arreglos que nadie
# había hecho. Un cierre sin declarar cae en `POR_AUSENCIA`: **ante la duda, no
# se vota** — el lado que no fabrica señal.
POR_ACCION, POR_AUSENCIA = "accion", "ausencia"
CIERRES = (POR_ACCION, POR_AUSENCIA)


def vota(resuelto_como: str) -> bool:
    """¿Este cierre es evidencia sobre el DIAGNÓSTICO del agente?

    Solo el cierre por ACCIÓN lo es. Y solo se afirma cuando está declarado:
    `None`, vacío o desconocido → `False`.
    """
    return (resuelto_como or "").strip() == POR_ACCION

ESTADOS = (NUEVO, VISTO, EN_CURSO, RESUELTO, IGNORADO, VOLVIO)

# Qué transiciones tienen sentido. Sirve para dos cosas: que nadie escriba un
# salto imposible (`resuelto → en_curso`) y que la pantalla pueda ofrecer solo
# los botones que aplican.
#
# ⚠️ `RESUELTO → VOLVIO` es la única vuelta atrás, y es la más importante: un
# problema que reaparece **no es nuevo**, y contarlo como nuevo es cómo se
# pierde que algo se arregla y se rompe todas las semanas.
TRANSICIONES: dict[str, tuple[str, ...]] = {
    NUEVO:    (VISTO, EN_CURSO, RESUELTO, IGNORADO),
    VISTO:    (EN_CURSO, RESUELTO, IGNORADO),
    EN_CURSO: (RESUELTO, VISTO),
    RESUELTO: (VOLVIO,),
    IGNORADO: (NUEVO,),          # des-ignorar
    VOLVIO:   (VISTO, EN_CURSO, RESUELTO, IGNORADO),
}


def puede_pasar(de: str, a: str) -> bool:
    """¿Esa transición existe? **`False` ante lo desconocido**: inventar un
    salto es peor que rechazarlo."""
    return a in TRANSICIONES.get((de or "").strip(), ())


@dataclass(frozen=True)
class Forma:
    """CÓMO dice su estado una tabla que todavía no migró.

    `campos` son las columnas que hay que leer; `leer` las traduce al ciclo. Se
    declara la traducción y no se adivina: `resuelto_at IS NULL` y
    `resuelto = false` parecen lo mismo y **`av_agent_avisos` tiene las dos**.
    """
    tabla: str
    campos: tuple[str, ...]
    como: str                    # en una línea, para el diag
    leer: object                 # (fila: dict) -> str
    # ⚠️ **MEDIO CAMINO, DECLARADO.** `espeja=True` = su tabla sigue teniendo su
    # propia columna de estado, PERO cada fila es además un objeto en
    # `av_agent_items` con la clave canónica: tiene historia, antigüedad y
    # seguimiento. No está migrada (la columna sigue ahí) y tampoco es cruda.
    #
    # Es un campo y no una palabra adentro de `como` a propósito: el avance se
    # cuenta con esto, y contar leyendo un string es cómo un renombre inocente
    # convierte una barra de progreso en una mentira.
    espeja: bool = False
    # ⚠️⚠️ **NO TODO LO QUE TIENE ESTADO ES UN PROBLEMA.** La lista de deuda las
    # metía a todas en la misma bolsa y eso sobrestimaba el trabajo y, peor,
    # apuntaba a un objetivo equivocado: hay tablas que NO tienen que terminar
    # siendo `av_agent_items` porque no hablan de un problema.
    #
    #   problema  → sí: algo está mal en algo. Termina en la canónica.
    #   sensor    → una LECTURA cruda (¿contesta Aunesa?). El objeto lo hace el
    #               detector que la lee, no la tabla: si la migráramos, el
    #               termómetro pasaría a ser la fiebre.
    #   bitacora  → el registro de que algo CORRIÓ (un run, una acción). Su
    #               `estado` describe la corrida, no un problema — y su valor es
    #               justamente ser append-only.
    #   meta      → habla DE los items (el seguimiento de un arreglo). Meterla
    #               adentro sería que el modelo se contenga a sí mismo.
    #   acuse     → quién LEYÓ qué, por persona. `av_agent_items.visto_at` es
    #               uno solo para todos: migrarla perdería la distinción y el
    #               segundo admin no vería nunca el modal que el primero cerró.
    clase: str = "problema"


def _por_resuelto_at(f: dict) -> str:
    if f.get("resuelto_at"):
        return RESUELTO
    return VISTO if f.get("visto_at") else NUEVO


def _por_bool_resuelto(f: dict) -> str:
    return RESUELTO if f.get("resuelto") else NUEVO


def _por_hecho(f: dict) -> str:
    return RESUELTO if f.get("hecho") else NUEVO


def _por_estado_texto(f: dict) -> str:
    """`estado text` con vocabularios propios por tabla. Se mapea explícito: un
    `.startswith()` haría que `rechazada` caiga en cualquier lado."""
    return {
        "abierta": NUEVO, "respondida": RESUELTO,
        "propuesta": NUEVO, "aplicada": RESUELTO, "esperando": EN_CURSO,
        "fallida": VISTO, "rechazada": IGNORADO,
        "corriendo": EN_CURSO, "terminado": RESUELTO, "frenado": RESUELTO,
        "error": VISTO,
    }.get(str(f.get("estado") or "").strip().lower(), NUEVO)


def _por_existencia(f: dict) -> str:
    """La fila ES el estado: si está, pasó. Son tablas append-only (trazas,
    lecciones, evals) — no tienen ciclo y decirlo así es más honesto que
    inventarles uno."""
    return RESUELTO


def _por_seguimiento(f: dict) -> str:
    return {"mirando": EN_CURSO, "aguanto": RESUELTO,
            "volvio": VOLVIO}.get(str(f.get("estado") or ""), EN_CURSO)


# ── EL OBJETO. Uno solo, y el TIPO es un campo ──────────────────────────────
#
# El user (2026-08-21), y es la decomposición correcta:
#
#     *«Que todo lo del AV Agent esté como objeto. Va a ser SIEMPRE EL MISMO
#     ESTILO, solo que va a cambiar el TIPO DE PROBLEMA —log, aviso, etc.—
#     pero CÓMO VAN A ESTAR es lo mismo. Después cambiará la solución, el
#     análisis, etc.»*
#
# Tres cosas que varían por separado, y hoy estaban mezcladas en 22 tablas:
#
#     LA FORMA      cómo se guarda y cómo vive        → UNA. Es esto.
#     EL TIPO       de qué habla (bono · job · log)   → un CAMPO
#     LA SOLUCIÓN   qué se hace y cómo se explica     → enchufable, por tipo
#
# ⚠️ **`clave` ES LO QUE DA MEMORIA, y es lo que faltaba.** Hoy los hallazgos se
# reescriben enteros en cada corrida sin identidad estable: por eso el mismo
# problema aparece «nuevo» todas las ruedas, por eso perdió que ya lo habías
# votado, y por eso el user viene diciendo hace días que *el agente no tiene
# memoria*. Con una clave estable, el hallazgo que vuelve **es el mismo objeto**:
# conserva desde cuándo está abierto, cuántas veces se vio, y si ya estaba
# resuelto pasa a `volvio` en vez de a `nuevo`.

# ── QUÉ ES UN PROBLEMA Y QUÉ ES UNA COMUNICACIÓN ────────────────────────────
#
# ⚠️⚠️ **NO TODO OBJETO CON CICLO ES ALGO ROTO.** Un aviso dirigido («cargá el
# saldo inicial»), una fila de aviso («esta cuenta está descubierta, le toca a
# X») y una pregunta abierta («¿este bono te sirve?») son cosas que el agente
# DIJO: tienen ciclo (se atienden o no) y por eso son items — pero no son un
# problema de la base, y cada una ya tiene su propia pantalla (AHORA, la
# cabecera de ENCONTRÓ, HISTORIAL).
#
# Meterlas en la misma bolsa que los problemas es lo que hizo que QUÉ PIDE ALGO
# dijera *«58 de 256 abiertos»* — 142 de esos 256 eran filas de aviso, y el
# user, con razón: *«¿256 QUÉ??? no se entiende»*. Peor: sus «resueltos»
# entraban al seguimiento como si fueran arreglos en observación, y
# `cerrar_hitos` podía llegar a VOTARLOS al eval set con `origen='verificado'`
# — un aviso atendido contando como «arreglo de bono que aguantó».
#
# La lista vive ACÁ (el modelo) y no en cada lector: tres lectores con tres
# copias es exactamente el modo de falla de REGLA #9.
TIPOS_COMUNICACION: tuple[str, ...] = ("aviso", "aviso_fila", "pregunta")


def es_comunicacion(tipo: str) -> bool:
    """¿Este item es algo que el agente DIJO, y no algo roto?"""
    return (tipo or "").strip().lower() in TIPOS_COMUNICACION


# Cuánto se espera antes de creerle a un arreglo. **NO es un plazo, son HITOS.**
#
# El user: *«5 días es mucho — es el día siguiente para ver si vuelve. Pero a su
# vez tiene que tener memoria y recursos para que siga con el paso del tiempo:
# puede ser 2 días, 3 días…»*.
#
# Exacto, y son dos necesidades distintas que un plazo único no cubre:
#
#   · **la señal RÁPIDA**: si vuelve mañana, el arreglo no sirvió y hay que
#     saberlo mañana, no el viernes;
#   · **la CONFIANZA que se acumula**: aguantar un día no es lo mismo que
#     aguantar un mes, y esa diferencia es justo lo que habilita autonomía.
#
# Cada hito que pasa sin que vuelva SUMA confianza. Si vuelve en cualquiera, el
# seguimiento se corta ahí: **volver una vez borra los hitos anteriores**, porque
# un arreglo que falla al día 8 no es «7 días bueno», es un arreglo que falla.
HITOS_DIAS: tuple[int, ...] = (1, 2, 3, 7, 14, 30)


def hitos_cumplidos(dias: float) -> int:
    """Cuántos hitos aguantó. `0` = todavía no pasó ni el primer día."""
    return sum(1 for d in HITOS_DIAS if dias >= d)


def confianza(dias: float) -> float:
    """De 0 a 1, según cuántos hitos aguantó. Es una ESCALERA y no una recta:
    el salto grande es sobrevivir el primer día; de ahí en más suma despacio."""
    return round(hitos_cumplidos(dias) / len(HITOS_DIAS), 4)


def proximo_hito(dias: float) -> int | None:
    """El día del próximo hito, o `None` si ya los pasó todos."""
    return next((d for d in HITOS_DIAS if dias < d), None)


@dataclass
class Item:
    """**LO QUE EL AGENTE ENCONTRÓ O DIJO.** Uno solo para todo.

    Un bono mal cargado, un job que falló, una línea de ERROR de un motor, un
    aviso dirigido a una persona y una pregunta abierta **son la misma cosa**
    desde el punto de vista del ciclo de vida: aparecen, se ven, se actúan, se
    resuelven, y a veces vuelven. Lo único que cambia es de qué hablan y qué se
    hace con ellos — y las dos cosas son datos, no clases distintas.
    """

    # ── IDENTIDAD: esto es la memoria ───────────────────────────────────────
    clave: str                    # estable entre corridas. Ver `clave_de`.
    tipo: str                     # hallazgo · chequeo · log · aviso · pregunta
    origen: str = ""              # QUÉ lo produjo (el detector, el control)
    sujeto: str = ""              # el bono, el job, la cuenta
    regla: str = ""               # la causa, que es lo que se mide y se automatiza

    # ── CICLO ───────────────────────────────────────────────────────────────
    estado: str = NUEVO
    severidad: str = "media"
    veces: int = 1                # cuántas veces se volvió a ver ESTE objeto
    abierto_at: object = None     # desde cuándo — NO se pisa al re-verlo
    ultimo_at: object = None
    visto_at: object = None
    resuelto_at: object = None
    vuelto_at: object = None      # la última vez que volvió después de resuelto
    # CÓMO se cerró (`accion` | `ausencia`). Decide si el tiempo que aguantó
    # cuenta como evidencia. Ver `vota()`.
    resuelto_como: str = ""
    reaperturas: int = 0          # cuántas veces se dio por arreglado y volvió
    visto_por: str = ""
    # Cuánto había aguantado la última vez que volvió: `resuelto_at` se limpia al
    # reabrir (si no, el reloj le seguiría contando hitos a un arreglo que falló)
    # y sin esto se perdía la diferencia entre fallar al día 1 y al día 20.
    aguanto_hasta: object = None

    # ── LO QUE VARÍA POR TIPO ───────────────────────────────────────────────
    titulo: str = ""              # QUÉ PASÓ, en castellano
    afecta: str = ""              # a qué le pega en la app
    datos: dict = field(default_factory=dict)   # lo específico del tipo

    def dias_abierto(self, ahora=None) -> float:
        # CALENDARIO a propósito: un problema abierto molesta también el
        # sábado — «lleva 11 días roto» incluye el finde porque estuvo roto
        # el finde.
        return _dias(self.abierto_at, ahora)

    def dias_resuelto(self, ahora=None) -> float:
        # HÁBILES a propósito: el finde no PRUEBA nada (ver `dias_de_prueba`).
        return dias_de_prueba(self.resuelto_at, ahora)

    @property
    def confianza_del_arreglo(self) -> float:
        """Cuánto se le puede creer a que esto quedó arreglado. **0 si volvió**:
        no importa cuánto había aguantado antes."""
        if self.estado == VOLVIO or not self.resuelto_at:
            return 0.0
        # ⚠️ Y **0 si se cerró por AUSENCIA**: que el detector dejara de verlo no
        # dice que alguien lo haya arreglado, así que el tiempo transcurrido no
        # es evidencia de nada (§0.de).
        if not vota(self.resuelto_como):
            return 0.0
        return confianza(self.dias_resuelto())


def _dias(desde, ahora=None) -> float:
    from datetime import UTC, datetime
    if not desde:
        return 0.0
    try:
        ahora = ahora or datetime.now(UTC)
        d = desde if hasattr(desde, "timestamp") else datetime.fromisoformat(str(desde))
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
        return max(0.0, (ahora - d).total_seconds() / 86400)
    except Exception:
        return 0.0


def dias_de_prueba(desde, ahora=None) -> float:
    """**Días HÁBILES transcurridos — el reloj de los hitos.**

    ⚠️ El finde no prueba nada (user, 2026-08-22: *«hoy es SÁBADO, el mercado
    no abre — no puede contarse para los días de si volvió o no algo»*). Un
    arreglo resuelto el viernes llegaba al hito 1 el sábado a la tarde, con
    los motores apagados y los detectores sin correr: dos días de «aguantó»
    sin una sola oportunidad de fallar. Evidencia que no pudo contradecirse
    no es evidencia.

    Cuenta la fracción de cada día HÁBIL (L-V sin feriados AR, el calendario
    único de `core/calendario`) cubierta por [desde, ahora], con los límites
    del día en hora ARGENTINA — el día de mercado es un hecho argentino.
    Resuelto viernes al mediodía → el lunes al mediodía lleva 1.0, no 3.0.

    La asimetría es a propósito y es el lado seguro: **VOLVER cuenta siempre**
    (un problema que reaparece un sábado igual borra la confianza — eso lo
    decide `estado`, no este reloj); lo único que corre en hábiles es la
    ACUMULACIÓN de confianza.
    """
    from datetime import UTC, datetime, time, timedelta
    from zoneinfo import ZoneInfo

    from core import calendario
    if not desde:
        return 0.0
    try:
        tz = ZoneInfo("America/Argentina/Buenos_Aires")
        ahora = ahora or datetime.now(UTC)
        d = desde if hasattr(desde, "timestamp") else datetime.fromisoformat(str(desde))
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
        a, b = d.astimezone(tz), ahora.astimezone(tz)
        if b <= a:
            return 0.0
        total, dia = 0.0, a.date()
        while dia <= b.date():
            if calendario.es_habil(dia):
                ini = datetime.combine(dia, time.min, tzinfo=tz)
                lo, hi = max(a, ini), min(b, ini + timedelta(days=1))
                if hi > lo:
                    total += (hi - lo).total_seconds() / 86400
            dia += timedelta(days=1)
        return round(total, 4)
    except Exception:
        return 0.0


# ── QUÉ MERECE ATENCIÓN HOY ─────────────────────────────────────────────────
#
# ⚠️⚠️ **EL MODELO GUARDABA LA HISTORIA Y NADIE LA LEÍA.** `veces`,
# `abierto_at`, `vuelto_at` y los hitos existen desde §0.bd… y la pantalla
# seguía ordenando por severidad, que es lo mismo que ordenaba ANTES de tener
# memoria. Sesenta y cuatro cosas abiertas, todas iguales, para siempre — que
# es literalmente la queja del user («las cosas en ENCONTRÓ siguen figurando»).
#
# **Un tablero que no prioriza no es un tablero, es un depósito.**
#
# Las bandas de abajo se derivan SOLO de campos guardados, sin estimar nada.
# Estuve tentado de agregar una banda «estructural vs intermitente» comparando
# `veces` contra las corridas transcurridas — y no está, porque **la cadencia
# de cada origen no se puede saber desde acá**: el centinela corre cada 5
# minutos y el control una vez por noche, así que 18 veces significa cosas
# opuestas según quién lo vio. Inventar ese denominador habría dado un cartel
# con pinta de medición y sin medición atrás (REGLA #2). Lo que sí es exacto es
# si VOLVIÓ, y eso ya dice lo mismo con certeza.
BANDAS = ("volvio", "estancado", "arrastra", "nuevo", "mirando")

# Cuántos días sin que nadie haga nada convierten un pendiente en un estancado.
# Dos, y no una semana: el user, sobre el seguimiento — *«5 días es mucho… es el
# día siguiente para ver si vuelve»*. La misma vara para el otro lado.
DIAS_ESTANCADO = 2.0


def banda(it: Item, ahora=None) -> str:
    """En qué grupo cae este objeto HOY. Uno solo, y el orden es la prioridad.

        volvio     el arreglo FALLÓ. Nada informa más: alguien ya lo dio por
                   resuelto y volvió igual.
        estancado  lo viste, sigue abierto y hace días que no pasa nada.
        arrastra   lleva días abierto y NADIE lo miró todavía.
        nuevo      apareció hoy.
        mirando    resuelto, en período de prueba (los hitos de §0.bi).
    """
    if it.estado == VOLVIO or it.vuelto_at:
        return "volvio"
    if it.estado in (RESUELTO, IGNORADO):
        return "mirando"
    d = it.dias_abierto(ahora)
    if d < 1:
        return "nuevo"
    return "estancado" if it.visto_at else "arrastra"


def prioridad(it: Item, ahora=None) -> tuple:
    """La clave de orden. Se devuelve una TUPLA y no un puntaje a propósito: un
    número inventado («87 puntos») no se puede discutir ni auditar, y esconde
    cuál de los criterios lo puso ahí. Una tupla dice el porqué en orden."""
    b = banda(it, ahora)
    sev = {"alta": 0, "media": 1, "baja": 2}.get(it.severidad, 3)
    # Dentro de la banda manda la severidad y después la ANTIGÜEDAD: lo que
    # lleva más tiempo abierto va primero, porque es lo que más tiempo estuvo
    # sin que a nadie le importara.
    return (BANDAS.index(b) if b in BANDAS else len(BANDAS), sev,
            -it.dias_abierto(ahora), -int(it.veces or 0))


def identidad(sujeto: str, causa: str, respaldo: str = "") -> str:
    """**LA IDENTIDAD DE UN PROBLEMA: qué está mal, en qué cosa.**

    ⚠️ **`tipo` y `origen` NO son identidad: son QUIÉN LO VIO.** La primera
    versión los metía en la clave y el resultado fue que el mismo problema real
    producía DOS objetos:

        detector →  precio_moneda|live|bpoa7|pata_equivocada
        control  →  control|control:patas_equivocadas|bpoa7|patas_equivocadas

    Un bono con la pata mal cargada, mirado por el detector de rueda y por el
    control nocturno. Que lo vean dos no lo convierte en dos problemas — y con
    dos objetos, arreglarlo movía uno y dejaba el otro colgado, o sea el mismo
    síntoma de siempre adentro del modelo nuevo.

    **Un problema es (QUÉ COSA, QUÉ LE PASA).** Quién lo vio y cuándo son
    atributos, no parte del nombre.

    ⚠️ **El `respaldo` es para lo que NO tiene sujeto.** Un `db_cambio` habla de
    la base entera y no de un bono: sin respaldo, todos los sin-sujeto de una
    misma causa colapsarían en UN objeto y taparían al resto. Ahí el origen
    vuelve a la clave — es menos preciso, pero es preferible a fusionar cosas
    distintas.
    """
    s = (sujeto or "").strip().lower()
    c = (causa or "").strip().lower()
    if not c:
        return ""
    if not s:
        r = (respaldo or "").strip().lower()
        return f"{r}|{c}" if r else c
    return f"{s}|{c}"


def clave_de(tipo: str, origen: str, sujeto: str, regla: str = "") -> str:
    """La IDENTIDAD del objeto, estable entre corridas.

    ⚠️ **No lleva fecha ni hora a propósito.** Es lo que hace que el mismo
    problema, visto mañana, sea el MISMO objeto y no uno nuevo — que es toda la
    diferencia entre tener memoria y no tenerla.

    Y **sí lleva la REGLA**: si el agente cambia de causa sobre el mismo bono,
    es un diagnóstico distinto y merece su propia historia. Ese es justo el par
    que ya usa el eval set, así que las dos cosas se cuentan igual.
    """
    partes = [(x or "").strip().lower() for x in (tipo, origen, sujeto, regla)]
    return "|".join(p for p in partes if p)


# ── EL REGISTRO: cada tabla, y cómo lo dice HOY ─────────────────────────────
#
# El orden es el de la migración sugerida: primero las que tienen ciclo de
# verdad, al final las append-only (que no lo necesitan).
REGISTRO: tuple[Forma, ...] = (
    # ⭐ **LA CANÓNICA.** Es la única que ya habla el vocabulario común: su
    # columna `estado` ES uno de los seis, sin traducción. Las de abajo son la
    # deuda — se migran hacia ésta, no al revés.
    Forma("agente.av_agent_items", ("estado",),
          "⭐ estado CANÓNICO (no necesita traducción)",
          lambda f: (str(f.get("estado") or "").strip().lower()
                     if str(f.get("estado") or "").strip().lower() in ESTADOS
                     else NUEVO)),
    # Espeja en `av_agent_items` con la misma clave desde 2026-08-21 (§0.bf):
    # su tabla sigue siendo la que dibuja AHORA, pero la HISTORIA del problema
    # es la misma que ve ENCONTRÓ. Migrar la tabla entera es el paso siguiente.
    Forma("agente.av_agent_centinela", ("resuelto_at", "visto_at"),
          "resuelto_at NULL + visto_at (espeja en av_agent_items)",
          _por_resuelto_at, espeja=True),
    # Espeja en `av_agent_items` (§0.bg): su tabla sigue siendo la que arma el
    # resumen del job, pero cada anomalía es además un objeto con historia.
    Forma("manager.controles_datos", ("resuelto_at",),
          "resuelto_at NULL = vigente (espeja en av_agent_items)",
          _por_resuelto_at, espeja=True),
    # ── LO QUE EL AGENTE MANDA (§0.bk) ──────────────────────────────────────
    # Las tres espejan en `av_agent_items` desde 2026-08-21. Un aviso es
    # *«a este bono le falta el CER»* y una pregunta es *«falta decidir sobre
    # este bono»*: los dos son **(qué cosa, qué le pasa)**, la misma identidad
    # que un hallazgo — así que cuando el detector encuentra ese mismo dato
    # faltando, los dos escriben en el MISMO objeto. Sus tablas siguen siendo
    # las que dibujan la lista (con su campo para tipear, su vencimiento y su
    # dueño); el objeto aporta lo que la lista no tiene: desde cuándo, cuántas
    # veces y el seguimiento del arreglo.
    #
    # ⚠️ Tiene LAS DOS: `resuelto boolean` y `resuelto_at`. Gana el booleano,
    # que es el que filtran sus queries — el `_at` es la marca de tiempo.
    Forma("agente.av_agent_avisos", ("resuelto", "resuelto_at"),
          "resuelto bool (espeja en av_agent_items)", _por_bool_resuelto, espeja=True),
    # Fila por fila y no el aviso entero: es lo que permite decir «esta cuenta
    # lleva CUATRO DÍAS descubierta», que el aviso agrupado no puede saber
    # porque cada corrida lo rearma.
    Forma("agente.av_agent_aviso_items", ("hecho",),
          "hecho bool (espeja en av_agent_items)", _por_hecho, espeja=True),
    Forma("agente.av_agent_preguntas", ("estado", "aplicada_at"),
          "estado text: abierta|respondida (espeja en av_agent_items)",
          _por_estado_texto, espeja=True),
    Forma("agente.av_agent_propuestas", ("estado",),
          "estado text: propuesta|aplicada|esperando|fallida|rechazada",
          _por_estado_texto, clase="bitacora"),
    Forma("agente.av_agent_runs", ("estado",),
          "estado text: corriendo|terminado|frenado|error", _por_estado_texto, clase="bitacora"),
    Forma("agente.av_agent_seguimiento", ("estado",),
          "estado text: mirando|aguanto|volvio", _por_seguimiento, clase="meta"),
    # Espeja desde 2026-08-21 (§0.bl): ignorar un ticker apaga TODOS sus objetos
    # (por sujeto, no por causa — si un papel no interesa, no interesa en
    # ninguna de sus formas). Sin eso, la pantalla decía «no hay nada» y el
    # contador seguía sumando días de algo que el user ya descartó.
    Forma("agente.av_agent_ignorados", (),
          "la EXISTENCIA de la fila = ignorado (espeja en av_agent_items)",
          lambda f: IGNORADO, espeja=True),
    Forma("manager.salud_vistos", ("visto_at",),
          "tabla APARTE de vistos (no una columna)",
          lambda f: VISTO if f.get("visto_at") else NUEVO, clase="acuse"),
    Forma("manager.proveedor_estado", ("ok",),
          "ok bool", lambda f: RESUELTO if f.get("ok") else NUEVO, clase="sensor"),
    Forma("agente.av_agent_acciones", ("ok",),
          "ok bool (append-only: es el LIBRO)", _por_existencia),
    # Append-only: no tienen ciclo y no hay que dárselo.
    # La FOTO. Sigue existiendo (es el histórico por corrida, de donde salió la
    # antigüedad real del backfill) pero su ESTADO ya no se deriva de cinco
    # tablas: lo tiene su espejo en `av_agent_items`, unidos por `clave`.
    Forma("agente.av_agent_hallazgos", ("clave",),
          "FOTO por corrida — el estado vive en av_agent_items (JOIN por clave)",
          _por_existencia),
    Forma("agente.av_agent_trazas", (), "append-only", _por_existencia),
    Forma("agente.av_agent_evals", (), "append-only", _por_existencia),
    Forma("agente.av_agent_lecciones", (), "append-only", _por_existencia),
    Forma("agente.av_agent_errores", (), "memoria por patrón", _por_existencia),
    Forma("agente.av_agent_control", (), "config", _por_existencia),
    Forma("agente.av_agent_latido", (), "heartbeat", _por_existencia),
    # No dice el estado de NADA: dice cuándo se pudo mirar cada tipo. Es lo que
    # le permite a la pantalla distinguir «esto sigue pasando» de «hace tres
    # días que nadie confirma esto» — la mitad de lectura del guard de
    # `evaluados`, que al escribir impide cerrar sin haber mirado.
    Forma("agente.av_agent_evaluado", (), "telemetría: última pasada OK por tipo",
          _por_existencia, clase="meta"),
    Forma("manager.salud_eventos", (), "append-only: las TRANSICIONES",
          _por_existencia),
    Forma("manager.salud_config", (), "config", _por_existencia),
    Forma("manager.salud_diagnosticos", (), "cache", _por_existencia),
)

_POR_TABLA = {f.tabla: f for f in REGISTRO}

# Las que TIENEN un ciclo de verdad y todavía lo dicen a su manera. Es la deuda,
# y es contable: `sin_migrar()` la devuelve.
# ⚠️ La canónica NO es deuda: ya habla el vocabulario. Contarla ahí haría que
# la migración nunca pudiera llegar a cero — y un contador que no puede cerrar
# deja de mirarse.
CANONICA = "agente.av_agent_items"

_CON_CICLO = tuple(f.tabla for f in REGISTRO
                   if f.leer is not _por_existencia and f.tabla != CANONICA)


def estado_de(tabla: str, fila: dict) -> str:
    """El estado CANÓNICO de una fila, sea cual sea la forma en que su tabla lo
    escriba. **Es el árbitro**: la pantalla pregunta acá en vez de mirar la
    columna, así dos pantallas no pueden discrepar.

    Una tabla sin declarar devuelve `NUEVO` — el estado más ruidoso a propósito:
    ante la duda se muestra de más. Y el test la caza antes de que llegue a prod.
    """
    f = _POR_TABLA.get((tabla or "").strip())
    if f is None:
        return NUEVO
    try:
        return f.leer(fila or {})            # type: ignore[operator]
    except Exception:
        return NUEVO


def espejan() -> list[str]:
    """Las que ya tienen objeto con historia aunque conserven su columna.

    Es el avance REAL de la migración: lo que se ganaba con migrar —memoria,
    antigüedad, seguimiento— ya está; lo que falta es sacar la columna vieja,
    que es riesgo puro y ningún beneficio nuevo. Contarlas como cero sería
    subestimar el estado igual que contarlas como hechas sería inflarlo.
    """
    return [f.tabla for f in REGISTRO if f.espeja]


def sin_migrar() -> list[str]:
    """Las tablas con ciclo propio que todavía no usan el vocabulario común.

    Es la DEUDA, contada. Que sea un número y no una sensación es la mitad del
    valor de este módulo.

    ⚠️ Incluye las que NO son problemas (`clase != "problema"`): son deuda de
    *vocabulario* —cada una dice «terminado» a su manera— pero **no** son deuda
    de *modelo*. Para el avance de la migración usar `deuda_de_problemas()`.
    """
    return list(_CON_CICLO)


def deuda_de_problemas() -> list[str]:
    """Las que SÍ tienen que terminar siendo `av_agent_items`.

    Un run, una acción o el termómetro de Aunesa tienen estado y no son un
    problema: migrarlos no arreglaría nada y convertiría al modelo en un cajón.
    """
    return [f.tabla for f in REGISTRO
            if f.tabla in _CON_CICLO and f.tabla != CANONICA
            and f.clase == "problema"]


_RE_TABLA = re.compile(
    r"CREATE TABLE IF NOT EXISTS "
    r"((?:agente\.av_agent|manager\.salud|manager\.controles_datos|"
    r"manager\.proveedor_estado)[a-z_]*)", re.I)


def tablas_del_agente() -> set[str]:
    """Las tablas del agente que hay en el schema, **derivadas del archivo**.

    No es una lista a mano: si mañana alguien agrega `av_agent_loquesea`, esto
    la ve y el test exige que se declare cómo dice su estado.
    """
    import pathlib
    sql = pathlib.Path(__file__).resolve().parent.parent / "sql" / "schema.sql"
    try:
        return set(_RE_TABLA.findall(sql.read_text(encoding="utf-8")))
    except OSError:
        return set()
