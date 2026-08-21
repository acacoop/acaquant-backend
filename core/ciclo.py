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

    # ── LO QUE VARÍA POR TIPO ───────────────────────────────────────────────
    titulo: str = ""              # QUÉ PASÓ, en castellano
    afecta: str = ""              # a qué le pega en la app
    datos: dict = field(default_factory=dict)   # lo específico del tipo

    def dias_abierto(self, ahora=None) -> float:
        return _dias(self.abierto_at, ahora)

    def dias_resuelto(self, ahora=None) -> float:
        return _dias(self.resuelto_at, ahora)

    @property
    def confianza_del_arreglo(self) -> float:
        """Cuánto se le puede creer a que esto quedó arreglado. **0 si volvió**:
        no importa cuánto había aguantado antes."""
        if self.estado == VOLVIO or not self.resuelto_at:
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
    Forma("mercado.av_agent_items", ("estado",),
          "⭐ estado CANÓNICO (no necesita traducción)",
          lambda f: (str(f.get("estado") or "").strip().lower()
                     if str(f.get("estado") or "").strip().lower() in ESTADOS
                     else NUEVO)),
    # Espeja en `av_agent_items` con la misma clave desde 2026-08-21 (§0.bf):
    # su tabla sigue siendo la que dibuja AHORA, pero la HISTORIA del problema
    # es la misma que ve ENCONTRÓ. Migrar la tabla entera es el paso siguiente.
    Forma("mercado.av_agent_centinela", ("resuelto_at", "visto_at"),
          "resuelto_at NULL + visto_at (espeja en av_agent_items)",
          _por_resuelto_at),
    Forma("manager.controles_datos", ("resuelto_at",),
          "resuelto_at NULL = vigente", _por_resuelto_at),
    # ⚠️ Tiene LAS DOS: `resuelto boolean` y `resuelto_at`. Gana el booleano,
    # que es el que filtran sus queries — el `_at` es la marca de tiempo.
    Forma("mercado.av_agent_avisos", ("resuelto", "resuelto_at"),
          "resuelto bool (+ resuelto_at redundante)", _por_bool_resuelto),
    Forma("mercado.av_agent_aviso_items", ("hecho",),
          "hecho bool", _por_hecho),
    Forma("mercado.av_agent_preguntas", ("estado", "aplicada_at"),
          "estado text: abierta|respondida", _por_estado_texto),
    Forma("mercado.av_agent_propuestas", ("estado",),
          "estado text: propuesta|aplicada|esperando|fallida|rechazada",
          _por_estado_texto),
    Forma("mercado.av_agent_runs", ("estado",),
          "estado text: corriendo|terminado|frenado|error", _por_estado_texto),
    Forma("mercado.av_agent_seguimiento", ("estado",),
          "estado text: mirando|aguanto|volvio", _por_seguimiento),
    Forma("mercado.av_agent_ignorados", (),
          "la EXISTENCIA de la fila = ignorado", lambda f: IGNORADO),
    Forma("manager.salud_vistos", ("visto_at",),
          "tabla APARTE de vistos (no una columna)",
          lambda f: VISTO if f.get("visto_at") else NUEVO),
    Forma("manager.proveedor_estado", ("ok",),
          "ok bool", lambda f: RESUELTO if f.get("ok") else NUEVO),
    Forma("mercado.av_agent_acciones", ("ok",),
          "ok bool (append-only: es el LIBRO)", _por_existencia),
    # Append-only: no tienen ciclo y no hay que dárselo.
    # La FOTO. Sigue existiendo (es el histórico por corrida, de donde salió la
    # antigüedad real del backfill) pero su ESTADO ya no se deriva de cinco
    # tablas: lo tiene su espejo en `av_agent_items`, unidos por `clave`.
    Forma("mercado.av_agent_hallazgos", ("clave",),
          "FOTO por corrida — el estado vive en av_agent_items (JOIN por clave)",
          _por_existencia),
    Forma("mercado.av_agent_trazas", (), "append-only", _por_existencia),
    Forma("mercado.av_agent_evals", (), "append-only", _por_existencia),
    Forma("mercado.av_agent_lecciones", (), "append-only", _por_existencia),
    Forma("mercado.av_agent_errores", (), "memoria por patrón", _por_existencia),
    Forma("mercado.av_agent_control", (), "config", _por_existencia),
    Forma("mercado.av_agent_latido", (), "heartbeat", _por_existencia),
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
CANONICA = "mercado.av_agent_items"

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


def sin_migrar() -> list[str]:
    """Las tablas con ciclo propio que todavía no usan el vocabulario común.

    Es la DEUDA, contada. Que sea un número y no una sensación es la mitad del
    valor de este módulo.
    """
    return list(_CON_CICLO)


_RE_TABLA = re.compile(
    r"CREATE TABLE IF NOT EXISTS "
    r"((?:mercado\.av_agent|manager\.salud|manager\.controles_datos|"
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
