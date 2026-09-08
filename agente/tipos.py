"""`agente/tipos.py` — el vocabulario. No importa NADA del proyecto.

Es una tabla de datos: la pueden leer el catálogo, los detectores y la vista sin
abrir un ciclo de imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── LOS ESTADOS DE UN HALLAZGO ─────────────────────────────────────────────
#
# CINCO, y cada uno se atiende distinto. Un estado de más es una rama de más en
# cada pantalla, para siempre. Se sacó `visto` del modelo viejo: "alguien lo
# miró y no hizo nada" no se atiende distinto de `nuevo`.
NUEVO = "nuevo"          # apareció y nadie lo tocó
EN_CURSO = "en_curso"    # se aplicó el arreglo y falta que el detector confirme
RESUELTO = "resuelto"    # ya no está
IGNORADO = "ignorado"    # una persona dijo «no me interesa» — reversible
REINCIDIO = "reincidio"  # estaba resuelto POR ACCIÓN y volvió

ESTADOS = (NUEVO, EN_CURSO, RESUELTO, IGNORADO, REINCIDIO)
# ⚠️ `reincidio` ES abierto (2026-09-01, §0.cy). Sin él acá, un hallazgo que
# volvió no lo cerraba nadie (`_cerrar_ausentes` mira ABIERTOS), no lo actualizaba
# nadie (`_ver` también) —así que la próxima vez que se lo viera nacía OTRA fila
# en `reincidencias`— y no aparecía en ENCONTRÓ. M31G6 quedó así desde el 28/08:
# «abierto» para siempre y visible en ninguna pantalla.
ABIERTOS = (NUEVO, EN_CURSO, REINCIDIO)

# ── CÓMO SE CERRÓ. Son DOS y NO significan lo mismo ────────────────────────
#
# ⚠️ Esta distinción es la que sostiene a `reincidencias`. Sin ella, un bono que
# no operó esa noche se auto-resuelve, vuelve mañana, y la tabla que debería
# estar vacía se llena de ruido hasta que nadie la mira.
POR_ACCION = "accion"      # se apretó el arreglo Y DESPUÉS el detector no lo vio
POR_AUSENCIA = "ausencia"  # el detector no lo vio, y nada más
# ⚠️ **CADUCIDAD ≠ AUSENCIA.** «No lo vi» es una observación; «el sujeto dejó de
# existir» es un HECHO VERIFICADO, con fuente y fecha. La diferencia importa por
# dos motivos, y ninguno es cosmético:
#
#   · Un hallazgo caducado **no puede reincidir**: «volver» no significa nada
#     sobre un bono que venció. Sin este tercer valor, el cierre caía en ACCIÓN
#     (si alguien había apretado el arreglo antes) y el sujeto muerto quedaba
#     habilitado a generar una reincidencia — que es literalmente cómo nació la
#     primera fila de esa tabla (M31G6, 28/08).
#   · Y le da un LUGAR a la respuesta «esto ya no aplica». Antes la única salida
#     era que una persona apretara «no me interesa», que es falso (nadie está
#     desinteresado) y además pierde el motivo para siempre.
#
# Es un CIERRE y no un sexto estado a propósito: el hallazgo queda `resuelto`
# —que es verdad, ya no está—, ninguna pantalla suma una rama, y hereda solo la
# regla que importa (no reincide). Ver `docs/AGENT.md` §8, invariante 4.
POR_CADUCIDAD = "caducidad"
CIERRES = (POR_ACCION, POR_AUSENCIA, POR_CADUCIDAD)

# ── RESULTADO DE UNA CORRIDA ───────────────────────────────────────────────
#
# ⚠️ `SIN_DATOS` NO es `OK` con la lista vacía: es «no pude mirar». Solo `OK`
# habilita cerrar por ausencia. Ver `registro.guardar`.
OK, SIN_DATOS, ERROR = "ok", "sin_datos", "error"

SEVERIDADES = ("alta", "media", "baja")
DOMINIOS = ("MERCADO", "SISTEMA", "DATOS", "SEGURIDAD")
# CUÁNDO tiene sentido mirar. El horario vive en `agente/reloj.py` y acá solo
# se lo nombra: `cierre` es «una vez, con el día cerrado», y es lo que hace que
# nada le siga pidiendo al mercado después de las 17.
VENTANAS = ("rueda", "cierre", "habil", "siempre")
TIPOS = ("detector", "consulta", "accion")

# DE QUÉ COSA HABLA EL SUJETO de una habilidad. Es lo que hace que la caducidad
# sea general y no un parche por detector: el motor no sabe qué es un bono, sabe
# preguntarle a `agente/vigencia.py` por el TIPO declarado acá.
#
# Vacío ("") es una declaración explícita y válida: «mi sujeto no es una cosa
# que pueda dejar de existir» (una vista, un campo, una prueba). No es un olvido
# — sin declararlo, la habilidad no caduca nada, que es el default seguro.
#
# ⚠️ Solo entran los tipos que tienen **partida de defunción verificable**. Sumar
# uno acá sin una fuente que afirme «esto ya no existe» haría que el agente
# cerrara hallazgos por no encontrar el sujeto, y NO ENCONTRARLO NO ES PRUEBA DE
# NADA — es el invariante 1 disfrazado de feature nueva.
SUJETOS = ("bono",)

# Quién FIRMA lo que el agente aplica solo, sin que nadie apriete. Es una
# constante y no un literal suelto: HISTORIAL la compara para pintar «SOLO»,
# `vista.solo()` cuenta lo hecho hoy filtrando por ella, y `agente/autonomo`
# la usa para no reintentar lo que ya intentó. No es un email a propósito.
ACTOR_AGENTE = "av-agent"


# ── AGUDO vs CRÓNICO — la pregunta que decide QUÉ hacer con un hallazgo ────
#
# ⚠️⚠️ **UN PROBLEMA QUE PASA TODOS LOS DÍAS NO ES UN INCIDENTE: ES UNA
# CONFIGURACIÓN MAL PUESTA.** Y arreglarlo todas las veces es taparlo.
#
# Pedido del user (2026-09-04), y es la crítica correcta al agente de hoy:
#
#   *«capaz está mal que el cron diga 14hs — el agente debe poder buscar
#    mejoras y potenciar lo que puede llegar a haber mal, no dejar todo como
#    está y parchear»*
#
# Hoy el agente mira cada hallazgo AISLADO y por eso todo termina en «relanzá el
# job». Para la primera vez está bien; para la vez número treinta, relanzar ES
# el parche — lo que hay que revisar es el umbral, el cron, o si el job sigue
# haciendo falta.
#
# Un EPISODIO es una vez que el problema NACIÓ. No cuenta las veces que el
# detector lo vio: un problema que persiste no crea fila nueva (sube `veces`).
# Así que tres episodios son tres veces que apareció, se fue, y volvió — que es
# exactamente lo que un incidente aislado NO hace.
VENTANA_CRONICO_D = 30
# TRES y no dos: dos veces en un mes puede ser casualidad, tres ya es un ritmo.
# El número está acá y no en una vista para que haya UNA sola definición de
# «crónico» (REGLA #9) — la calcula `agente/vista._con_historial`.
EPISODIOS_CRONICO = 3

# ── LA NATURALEZA DE UNA REGLA — qué se cuenta y qué no ────────────────────
#
# ⚠️⚠️ **«CRÓNICO» CUENTA CUÁNTAS VECES NACIÓ EL PROBLEMA SOBRE EL MISMO
# SUJETO, y ese número sólo significa algo si el sujeto es UNA COSA FIJA**: un
# job, un motor, una tabla, un ticker. Ahí «apareció tres veces» es un patrón —
# algo se rompe, se arregla y se vuelve a romper.
#
# Si el sujeto es un GRUPO —«ONs HARD DÓLAR», «CEDEAR», «CARTERA», «el peso de
# la base»— la fila nace, se vacía y vuelve a nacer **cada vez que el mundo
# crece**. Contar esos nacimientos mide el ritmo del negocio, no una falla; y
# encima nunca baja a cero, porque siempre hay un título más. Marcarlos «⚠
# crónico» entrena a ignorar el cartel justo donde sí importa (§0.ep).
#
# Por eso la naturaleza se DECLARA por regla en el catálogo y no se adivina: no
# hay dato en el hallazgo que diga si su sujeto es una cosa o un grupo.
INCIDENTE = "incidente"    # se rompió algo que no debería: CUENTA episodios
INFORME = "informe"        # nace por CALENDARIO (el peso de la base, la tabla de 1816)
RECURRENTE = "recurrente"  # nace porque el UNIVERSO CRECIÓ (títulos, cuentas, clientes)
NATURALEZAS = (INCIDENTE, INFORME, RECURRENTE)
# Las dos que NO cuentan episodios ni entran en PATRONES. `INCIDENTE` es el
# default de toda regla no declarada: el lado que no se puede olvidar es el otro.
SIN_EPISODIOS = (INFORME, RECURRENTE)

# ⚠️ **CRÓNICO ACTIVO ≠ CRÓNICO HISTÓRICO**, y confundirlos arruina el ranking.
# Medido el 2026-09-04, la primera vez que se listaron: de 25 crónicos, ELEVEN
# ya no pasaban — seis `soberanos_faltantes` que alguien silenció el 26/08 y
# cuatro `precio_viejo` que se cortaron el 28/08. Seguían arriba de la lista
# compitiendo por atención con los que rompen hoy.
#
# Es el mismo error que el agente persigue en los datos —«lo que pasó» leído
# como «lo que está pasando»— cometido por la herramienta que lo mide.
DIAS_ACTIVO = 7


# ── «NO HAY NADA QUE HACER» NO ES UN `que_hacer` ───────────────────────────
#
# ⚠️⚠️ **EL AGENTE AVISA DE LO NUESTRO. UN HECHO DEL MUNDO NO ES UN HALLAZGO:
# ES EL SILENCIO.**
#
# User (2026-09-08), sobre `bono_sin_precio · sin_punta` (§0.eu):
#
#   *«si es por iliquidez no lo quiero ver. Ver solamente algo que ES un error.
#    Iliquidez no es un error, por lo que no me interesa el aviso: SIN AVISO DOY
#    POR SENTADO LA ILIQUIDEZ»*
#
# El invariante #2 —«un hallazgo sin `que_hacer` no se guarda»— ya decía esto, y
# `sin_punta` lo cumplía **de forma nominal**: su `que_hacer` era la frase «Nada
# que apretar: el papel no operó».
#
# Medido sobre los **83 textos `que_hacer`** que declaraba el agente (52 llamadas
# a `Hallazgo()` en `agente/detectores/` + 31 filas de `agente/reportes.py`):
# **CUATRO** empezaban diciendo que no había nada que hacer, y `sin_punta` era el
# único de los detectores. Los otros 79 nombran una acción — «Relanzar»,
# «Revisar», «Mirar el log», «Cargarle el símbolo», «Cerrarlo en el router».
#
# O sea: el modelo ya sabía que eso no era un hallazgo, y el texto lo esquivó.
# Un CHECK que se cumple escribiendo «no hay nada que hacer» no es un CHECK. Por
# eso la prohibición vive acá —en el constructor, donde no se puede olvidar— y
# no en un test: *un test que existe para recordarte algo es la señal de que el
# diseño no lo garantiza solo.*
#
# ⚠️ Compara sólo el ARRANQUE del texto, normalizado. «Ver si el job dejó de
# escribir; si no cambia nada, no hacer nada» es un `que_hacer` legítimo y no
# empieza con ninguna de estas.
NADA_QUE_HACER = ("nada que", "nada para", "no hay nada", "ninguna accion",
                  "ninguna acción", "no hacer nada", "no hay accion",
                  "no hay acción")


class SinDatos(Exception):
    """«No pude mirar». La levanta un detector que no pudo leer su fuente.

    Es distinta de devolver `[]`: un detector que devuelve vacío está afirmando
    que no hay nada, y con eso el agente CIERRA los problemas que no vinieron.
    Levantar esto dice «no sé» — y entonces no se cierra nada.
    """


@dataclass(frozen=True)
class Hallazgo:
    """Lo que una habilidad vio, en un momento.

    ⚠️ `que_hacer` es obligatorio y la base lo exige con un CHECK. **Si no se
    puede decir qué hacer, la regla está mal pensada** — una fila que solo dice
    «esto está mal» le pasa el problema entero al que la lee.

    ⚠️ Y **tampoco vale escribir que no hay nada que hacer** (`NADA_QUE_HACER`).
    Ahí la regla no está mal pensada: no es una regla. Es un hecho del mundo, y
    el lugar de un hecho del mundo es el silencio (§0.eu).
    """

    sujeto: str
    regla: str
    severidad: str
    problema: str
    que_hacer: str
    nombre: str = ""
    # ⚠️ **EL ERROR CRUDO, TAL CUAL.** User (2026-08-25): *«debería verse el
    # código del error real; con eso alcanza para darme cuenta de quién es el
    # error. El `que_hacer` está de más — es mejor mostrar la evidencia»*.
    #
    # Y tenía razón: «se arregla del otro lado, verificar si es de ellos o si se
    # nos venció una credencial» es la MISMA frase para AUNESA, 1816, BCRA e
    # Interbanking, escrita a mano en el detector. No sale de ningún dato. Un
    # texto que no cambia con el caso no informa: entrena a saltearlo.
    #
    # El error de verdad ya se calculaba y quedaba enterrado en `evidencia`, que
    # la pantalla ni lee. Acá es un campo propio, y por eso se muestra.
    detalle: str = ""
    evidencia: dict = field(default_factory=dict)

    def __post_init__(self):
        if not str(self.sujeto).strip():
            raise ValueError("un hallazgo sin sujeto no se le puede adjudicar a nada")
        if not str(self.regla).strip():
            raise ValueError(f"«{self.sujeto}» sin regla: la identidad del "
                             "problema es habilidad+sujeto+regla")
        if not str(self.que_hacer).strip():
            raise ValueError(f"«{self.sujeto}/{self.regla}» sin `que_hacer`")
        if " ".join(str(self.que_hacer).split()).lower().startswith(NADA_QUE_HACER):
            raise ValueError(
                f"«{self.sujeto}/{self.regla}»: el `que_hacer` empieza diciendo "
                f"que no hay nada que hacer ({self.que_hacer[:40]!r}). Entonces "
                "esto no es un hallazgo, es un dato: el agente avisa de lo "
                "NUESTRO y un hecho del mundo se afirma con el SILENCIO. Si "
                "igual hace falta guardarlo, no es por acá — es una tabla del "
                "dominio, no `agente.hallazgos`")
        if self.severidad not in SEVERIDADES:
            raise ValueError(f"severidad «{self.severidad}» no existe")


@dataclass(frozen=True)
class Habilidad:
    """UNA cosa que el agente sabe hacer, con todo lo que hay que saber de ella.

    ⚠️ **`arreglo` se declara POR REGLA, no por habilidad.** Una habilidad puede
    tener reglas de las dos clases: en `precio_moneda`, `pata_equivocada` se
    arregla con un botón y `cotiza_en_pesos` es contexto. Colgar el arreglo de la
    habilidad obligaría a elegir mal para una de las dos.

    De ahí sale la CLASE, que nadie escribe:
        con arreglo → `trabajo` → AHORA (hoy) + ENCONTRÓ (hasta arreglarse)
        sin arreglo → `aviso`   → AHORA y nada más
    """

    nombre: str
    tipo: str
    dominio: str
    que_mira: str
    cada_segundos: int
    correr: object                       # callable(umbrales) -> list[Hallazgo]
    ventana: str = "siempre"
    usa_ia: bool = False
    umbrales: dict = field(default_factory=dict)
    arreglos: dict = field(default_factory=dict)   # regla -> id del arreglo
    # DE QUÉ TIPO es su sujeto (`SUJETOS`), o "" si no es una cosa que pueda
    # dejar de existir. Es lo único que hay que agregar para que una habilidad
    # sepa caducar — sin tocar el motor, ni el registro, ni una lista aparte.
    sujeto_es: str = ""
    # ⚠️ **QUÉ REGLAS MERECEN QUE EL INVESTIGADOR VAYA SOLO, y cuánto tiene que
    # AGUANTAR el problema antes de gastar en él.** `{regla: segundos}`.
    #
    # Vacío es el default y es una declaración: esta habilidad no dispara nada.
    # Investigar cuesta (8 a 18 llamadas al modelo), así que el permiso se da
    # caso por caso — «no con todo, con casos que vayamos eligiendo».
    #
    # Los segundos NO son burocracia: son la corrección de un error real. Aunesa
    # se cayó, el hallazgo nació, y para cuando lo miramos ya no estaba —se había
    # recuperado solo—. Disparar en el momento del hallazgo habría pagado una
    # investigación entera de algo que se arregló sin que nadie hiciera nada.
    # **No se investiga lo que se acaba de caer: lo que SIGUE caído.**
    #
    # El TIPO de investigación no se declara acá: ya vive en
    # `lab.langgraph.investigaciones.DE_LA_HABILIDAD`. Ver `agente/triage.py`.
    investigar: dict = field(default_factory=dict)

    # ⚠️ **QUÉ REGLAS PUEDE APLICAR SOLO, y por qué.** `{regla: motivo}`.
    # Vacío es el default y es una declaración: nada de esta habilidad se
    # aplica sin que alguien apriete. Solo tiene sentido sobre una regla con
    # arreglo declarado en `arreglos` y cuyo arreglo no pida datos (un robot
    # no tilda listas); el test lo exige. El juez sigue siendo el pre-flight:
    # `arreglos.aplicar` rechaza igual lo que `puede_aplicar` no deja.
    automatico: dict = field(default_factory=dict)

    # ⚠️ **QUÉ ES CADA REGLA: `{regla: INCIDENTE | INFORME | RECURRENTE}`**
    # (`NATURALEZAS`, `AGENT.md` §0.eg, §0.ek y §0.ep). Sólo el INCIDENTE cuenta
    # episodios y puede volverse «crónico»; los otros dos son el ritmo del
    # negocio, no un patrón a corregir.
    #
    # La pregunta que se contesta acá es UNA: **¿el sujeto de esta regla es una
    # COSA FIJA (un job, un motor, un ticker) o un GRUPO que se llena y se
    # vacía?** Un grupo nunca es crónico.
    #
    # ⚠️ **Toda regla CON ARREGLO tiene que estar declarada** — lo exige
    # `__post_init__`, no un test que haya que acordarse de correr. Una regla
    # con botón es trabajo que hace una persona, y ahí la pregunta de arriba
    # SIEMPRE tiene respuesta. Las reglas sin arreglo (los avisos) caen en
    # INCIDENTE por default, que es el comportamiento de siempre.
    naturaleza: dict = field(default_factory=dict)

    def __post_init__(self):
        for campo, validos in (("tipo", TIPOS), ("dominio", DOMINIOS),
                               ("ventana", VENTANAS)):
            if getattr(self, campo) not in validos:
                raise ValueError(f"«{self.nombre}»: {campo}="
                                 f"{getattr(self, campo)!r} no es uno de {validos}")
        if not str(self.que_mira).strip():
            raise ValueError(f"«{self.nombre}» no dice qué mira: la tab de "
                             "habilidades lo mostraría vacío")
        if self.cada_segundos < 30:
            raise ValueError(f"«{self.nombre}»: {self.cada_segundos}s es un "
                             "ritmo que ningún detector necesita")
        # Un tipo de sujeto inventado no puede fallar en silencio: `vigencia` no
        # lo encontraría en su registro, no verificaría nada, y la habilidad
        # nunca caducaría — sin un error, sin un log, sin nada que mirar.
        for regla, seg in (self.investigar or {}).items():
            # Un `investigar={"x": 0}` dispararía en el instante del hallazgo,
            # que es exactamente lo que esto vino a evitar. Y un valor en
            # minutos donde van segundos (`20` en vez de `20*60`) es el error de
            # tipeo obvio: con el piso, no llega a producción.
            if not isinstance(seg, int) or seg < 300:
                raise ValueError(
                    f"«{self.nombre}/{regla}»: la espera es {seg!r} y tiene que "
                    "ser un entero de al menos 300 segundos. Investigar lo que "
                    "se acaba de caer paga por problemas que se arreglan solos")
        for regla, nat in (self.naturaleza or {}).items():
            if nat not in NATURALEZAS:
                raise ValueError(f"«{self.nombre}/{regla}»: naturaleza="
                                 f"{nat!r} no es una de {NATURALEZAS}")
        # ⚠️ **EL OLVIDO QUE ESTO HACE IMPOSIBLE** (§0.ep): `on_faltante`
        # describía su fila de familia como «una OFERTA de catálogo, no un
        # problema» —en un comentario— y la pantalla igual la marcaba «⚠ crónico
        # · 3× en 30d», porque nadie la había DECLARADO. Un comentario no es una
        # declaración, y una lista opcional se olvida: se olvidó dos veces.
        sin_declarar = sorted(r for r in self.arreglos if r not in self.naturaleza)
        if sin_declarar:
            raise ValueError(
                f"«{self.nombre}»: {sin_declarar} tiene(n) arreglo y no "
                f"declara(n) `naturaleza`. ¿El sujeto de esa regla es UNA COSA "
                f"(un ticker, un job) o un GRUPO que se llena y se vacía? Lo "
                f"primero es {INCIDENTE!r}; lo segundo, {RECURRENTE!r}")
        if self.sujeto_es and self.sujeto_es not in SUJETOS:
            raise ValueError(f"«{self.nombre}»: sujeto_es={self.sujeto_es!r} no "
                             f"es uno de {SUJETOS} — y un tipo que `vigencia` no "
                             "conoce hace que la habilidad no caduque nada, "
                             "callada")

    def arreglo_de(self, regla: str) -> str:
        return self.arreglos.get(regla, "")
