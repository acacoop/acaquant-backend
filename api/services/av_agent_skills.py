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

# ── LOS DOMINIOS: de qué habla cada habilidad ───────────────────────────────
#
# Pedido del user (2026-08-19): *«necesito que en SKILLS haya jerarquías de
# habilidades: MERCADO, ADMINISTRATIVO, SEGURIDAD, etc.»*
#
# Sin esto la tab es una lista plana de 37 filas donde un chequeo de permisos
# convive con un bono sin cronograma, y **no hay forma de mirar UN área**: para
# saber qué sabe el agente sobre seguridad hay que leer las 37. El tipo
# (detectar/explicar/resolver) dice CÓMO trabaja; el dominio dice SOBRE QUÉ, y
# esa es la pregunta que uno se hace primero.
MERCADO, SISTEMA, SEGURIDAD, ADMIN, DATOS = (
    "MERCADO", "SISTEMA", "SEGURIDAD", "ADMINISTRACIÓN", "DATOS")
DOMINIOS = (MERCADO, SISTEMA, SEGURIDAD, ADMIN, DATOS)


@dataclass
class Skill:
    id: str
    tipo: str
    nombre: str          # en castellano, como lo diría una persona
    que_hace: str
    usa_ia: str          # no | opcional | si
    dominio: str = DATOS         # de qué habla: MERCADO | SISTEMA | …
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
        usa_ia=IA_OPCIONAL, dominio=_DOMINIO_EXPLICADOR.get(e.id, MERCADO),
        para_que_la_ia="resume el resultado en una frase; los números NO los "
                       "toca el modelo",
        donde="AV Agent → SKILLS", fuente="av_agent_explicar.EXPLICADORES",
        extra={"necesita": e.necesita},
    ) for e in ex.EXPLICADORES.values()]


# ⚠️ **LAS CAPACIDADES QUE NO SON NI DETECTOR, NI ACCIÓN, NI EXPLICADOR.**
#
# La LEY de §0.o dice que toda habilidad nueva queda mapeada, y el catálogo se
# DERIVA de los tres registros que existen. Mandar un mensaje no cabe en ninguno:
# no se da cuenta de nada, no contesta una pregunta y no arregla un dato — es
# algo que el agente SABE HACER y que otros usan.
#
# Se declara acá y hay un test que exige que cada una siga existiendo como
# módulo: una capacidad listada que ya no está manda a buscar a un lugar vacío,
# que es peor que no listarla.
_CAPACIDADES: tuple[dict, ...] = (
    {"id": "mensajes.enviar",
     "nombre": "Mandarle un mensaje a alguien",
     "que_hace": "deja un pendiente con dueño en la barra de esa persona, sin "
                 "buzón nuevo: se ve, se cierra y se audita como el resto. "
                 "Resuelve operador → email desde la base, así nadie tiene que "
                 "saberse un mail",
     "dominio": ADMIN, "modulo": "api.services.av_agent_mensajes"},
    {"id": "mensajes.saldos_a_operadores",
     "nombre": "Saldos del día a cada operador",
     "que_hace": "a las 16:45 hábiles, un mensaje por operador con los saldos "
                 "!= 0 de SUS comitentes, descubiertos primero. Las cuentas sin "
                 "operador se cantan aparte: a ésas no le llegan a nadie",
     "dominio": ADMIN, "modulo": "jobs.saldos_a_operadores"},
    {"id": "agenda.que_estoy_haciendo",
     "nombre": "Decir qué está haciendo, y qué NO",
     "que_hace": "cruza el catálogo de habilidades con el crontab real y con las "
                 "corridas de job_runs para contestar la otra pregunta: no «qué "
                 "sé hacer» sino «¿lo estoy haciendo?». Por cada rutina dice cada "
                 "cuánto debería correr, cuándo corrió, cómo salió y qué dejó "
                 "abierto. **Distingue tres estados y no dos**: al día, atrasada "
                 "y SIN PODER JUZGAR — pintar de verde lo que no se pudo medir "
                 "sería justo la mentira que esto vino a impedir",
     "dominio": SISTEMA, "modulo": "api.services.av_agent_agenda",
     "donde": "AV Agent → CONTROL"},
    {"id": "errores.traducir",
     "nombre": "Traducir un error a castellano",
     "que_hace": "de una línea de log dice QUÉ PASÓ (sin nombres de clases ni "
                 "de archivos), A QUÉ AFECTA de la app y SI SIGUE pasando. Lo "
                 "conocido lo traduce una tabla de firmas —son motores nuestros, "
                 "fallan de un conjunto finito de formas— y solo lo que la regla "
                 "no supo va al modelo, UNA vez por patrón y no por fila. **A "
                 "qué afecta NO lo decide el modelo**: sale de la ficha declarada "
                 "del job o del motor",
     "dominio": SISTEMA, "modulo": "api.services.av_agent_errores",
     "donde": "el título de cada fila de MOTOR_RUIDOSO y de SALUD"},
    {"id": "logs.motores",
     "nombre": "Leer los logs de los motores",
     "que_hace": "junta lo que escribieron los motores en systemd y lo RESUME: "
                 "borra lo que cambia en cada repetición (fecha, símbolo, "
                 "número) y cuenta cuántas veces salió cada error y en qué "
                 "ventana. Sin eso, mil reconexiones son mil renglones y se lee "
                 "el último. Todavía no avisa solo: primero hay que medir cómo "
                 "es un día normal para no gritar todos los días",
     "dominio": SISTEMA, "modulo": "api.services.logs_sistema",
     "donde": "python -m scripts.diag_logs_motores"},
    {"id": "proveedores.probar",
     "nombre": "Llamar a Aunesa y entender el error",
     "que_hace": "prueba la conexión SIN mandar credenciales (un 5xx así prueba "
                 "que se rompió antes de leerlas: es de ellos) e interpreta el "
                 "código según el contrato que publica el custodio. Si contesta "
                 "en su formato de error cita sus palabras; si contesta el HTML "
                 "de Tomcat, dice que se rompió antes de su propio manejador. "
                 "Y barre las 5 APIs que usamos para decir si es el servicio "
                 "entero o un endpoint",
     "dominio": SISTEMA, "modulo": "api.services.av_agent_proveedores",
     "donde": "python -m scripts.diag_aunesa"},
    {"id": "causas.correlacionar",
     "nombre": "Saber que un problema explica a los otros",
     "que_hace": "cruza los hallazgos entre sí: si un job falló y el proveedor "
                 "del que depende está caído, el aviso pasa a decir POR QUÉ "
                 "falló y baja de severidad, y la causa dice a cuántos explica "
                 "— ese número es el impacto. La dependencia sale del CÓDIGO "
                 "(el import o la URL, las dos están escritas), no de una lista "
                 "que alguien tiene que acordarse de actualizar. Y no inventa: "
                 "si la dependencia no está escrita, no relaciona",
     "dominio": SISTEMA, "modulo": "api.services.av_agent_causas",
     "donde": "AV Agent → ENCONTRÓ"},
)


def _de_capacidades() -> list[Skill]:
    # Qué capacidad llama al modelo, y PARA QUÉ. Se declara (no se adivina
    # leyendo el código): una skill que dice «no usa IA» y la usa es la clase de
    # afirmación que no puede ser una inferencia frágil.
    con_modelo = {
        "errores.traducir": "solo cuando ninguna firma conocida matchea: "
                            "traduce esa línea de log a una oración, UNA vez por "
                            "patrón (se persiste), y sin decidir a qué afecta",
    }
    return [Skill(
        id=c["id"], tipo=RESOLVER, nombre=c["nombre"], que_hace=c["que_hace"],
        usa_ia=IA_OPCIONAL if con_modelo.get(c["id"]) else SIN_IA,
        dominio=c["dominio"], para_que_la_ia=con_modelo.get(c["id"], ""),
        # El default es la barra porque las dos primeras capacidades mandan
        # mensajes; una que no lo hace declara su propio `donde`. Sin esto, el
        # catálogo mandaba a mirar MIS AVISOS por algo que sale en la consola.
        donde=c.get("donde", "la barra de la persona (MIS AVISOS)"),
        fuente="av_agent_skills._CAPACIDADES",
        extra={"modulo": c["modulo"], "reglas": []},
    ) for c in _CAPACIDADES]


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
            dominio=_DOMINIO_ACCION.get(a.id, DATOS),
            para_que_la_ia=motivo, donde=a.donde,
            fuente="av_agent_hacer.ACCIONES",
            extra={"control": a.sobre, "campo": a.campo}))
    return out


# Los DETECTORES no viven en un registro con metadatos como los otros dos (son
# funciones sueltas del motor y controles de un job), así que la descripción es
# lo único que va a mano. **La LISTA no**: se cruza contra lo que existe de
# verdad y un test falla si alguien suma un detector y no lo describe acá — que
# es la forma de que la ley se cumpla sin depender de que alguien se acuerde.
# ⚠️ **DOS TEXTOS Y NO UNO.** Antes esto era un solo string que se usaba de
# nombre Y de descripción, así que cada fila de la tab mostraba **la misma frase
# repetida dos veces**, una en negrita y otra abajo. El user lo marcó: *«queda
# feo, se repiten las cosas»*. Y no era solo estética — un nombre de 20 palabras
# no se puede escanear, que es lo único que uno hace con una lista de 37.
#
#     NOMBRE     corto, se lee de un vistazo
#     QUÉ HACE   la explicación, con el criterio adentro
_QUE_DETECTA: dict[str, tuple[str, str]] = {
    "falta_en_base": (
        "Bonos que nos faltan",
        "los que 1816 lista y no están en el master"),
    "sin_flujo": (
        "Bonos sin cronograma",
        "están cargados pero sin flujos de pago: no valúan"),
    "tasa_sospechosa": (
        "Tasas que no cierran",
        "se apartan de las de 1816 más de lo tolerable"),
    "hueco_de_curva": (
        "Ajustes sin curva",
        "existen en el master y no tienen pill: esos bonos quedan invisibles "
        "sin dar ningún error"),
    "salud": (
        "Jobs que fallaron",
        "no corrieron, salieron con error o dejaron el dato viejo"),
    "sin_precio": (
        "Bonos sin precio, en rueda",
        "distingue las 4 causas y mide en TIEMPO DE MERCADO: sin símbolo "
        "cargado · nadie lo suscribió · suscripto sin punta · el precio dejó "
        "de moverse"),
    "precio_moneda": (
        "Precios en la moneda equivocada",
        "bonos de curva USD que muestran pesos, separando el que suscribe la "
        "PATA EQUIVOCADA del que cotiza así de verdad — y para eso busca la pata "
        "en dólares en DOS fuentes: `mercado.especies` y, si ahí no está, el "
        "catálogo de Primary (mirar una sola no alcanza para decir que no existe)"),
    "cron_desalineado": (
        "Crons del repo que no corren",
        "compara `deploy/crontab.txt` —que TODO el sistema trata como la fuente "
        "de verdad— contra el crontab REAL de la máquina. `deploy.sh` no lo "
        "instala, así que un cron nuevo puede vivir en el repo y no ejecutarse "
        "nunca: no falla nada, el catálogo lo muestra igual y el job no corrió. "
        "Mira las dos direcciones (lo que falta instalar y lo que corre sin "
        "estar declarado) y si no puede leer el crontab lo DICE en vez de "
        "callarse"),
    "respuesta": (
        "La respuesta a lo que se pidió",
        "cierra el círculo de una acción cuyo efecto NO es inmediato. Cuando se "
        "pide la pata en dólares de un bono, si esa pata cotiza o no lo contesta "
        "el mercado y no nosotros: acá se relee en cada pasada de rueda y se "
        "canta el veredicto, incluido el «no cotiza» — que es el que cierra el "
        "tema. La espera se mide en tiempo de mercado abierto, así que un «no» "
        "nunca es impaciencia"),
    "dato_partido": (
        "Copias del mismo dato que no coinciden",
        "los datos que viven en más de un lugar y dejaron de decir lo mismo. "
        "Detecta la CLASE de bug, no el caso: cuando dos copias se separan NO "
        "falla nada —cada mitad sigue coherente— y el sistema contesta con "
        "seguridad usando la equivocada. El registro de qué está duplicado y "
        "quién manda se DECLARA en `core/duplicados`"),
    "db_cambio": (
        "La base cambió",
        "tablas nuevas, las que crecieron de golpe y las que desaparecieron, "
        "comparando la foto de hoy contra la de ayer"),
    "latencia": (
        "Endpoints degradados",
        "los que se pusieron lentos contra SU PROPIA normalidad (no un ranking "
        "de los más lentos) y los que devuelven 5xx"),
    "tabla_quieta": (
        "Tablas que dejaron de escribir",
        "la cadencia de cada una se MIDE observándola, no la declara nadie, y "
        "el atraso se cuenta en tiempo de mercado"),
    "motor_caido": (
        "Motores y jobs caídos",
        "solo DENTRO de su ventana horaria: fuera de rueda un motor no está "
        "caído, está apagado"),
    "motor_ruidoso": (
        "Lo que los motores vienen diciendo",
        "no si PRODUCEN (eso es el de arriba) sino si se están rompiendo "
        "mientras producen. Distingue la RÁFAGA —algo fallando en loop ahora— "
        "de lo que MACHACA todo el día, que es una config rota que nadie mira: "
        "la cuenta sola no las separa, 76 veces en 3 minutos y 91 en 7 horas "
        "son dos problemas distintos"),
    "recuperado": (
        "Avisa también cuando algo VUELVE",
        "hasta ahora, si un motor caído volvía, su aviso simplemente dejaba de "
        "escribirse: el que estaba esperando no se enteraba nunca. Compara la "
        "corrida de ahora con la anterior y canta lo que se arregló. Dura 30 "
        "minutos: una buena noticia envejece más rápido que una mala"),
    "proveedor_caido": (
        "Proveedores de afuera que no responden",
        "Aunesa, 1816, Interbanking, BCRA. Se entera por el rastro de las "
        "llamadas REALES (no gasta créditos de 1816 ni se cree un health check "
        "que contesta bien mientras el endpoint que usamos devuelve 500) y, "
        "cuando ya hay una falla, LLAMA: prueba sin credenciales para separar "
        "«se cayeron ellos» de «se nos venció una clave», y barre las 5 APIs de "
        "Aunesa para decir si es su servicio entero o un endpoint. Avisa "
        "DIRECTO al back office, que es quien lo sufre y no ve la pantalla del "
        "agente"),
    "permiso_flojo": (
        "Permisos que están solo en los papeles",
        "endpoints sin gate, y —probando de verdad, sin credenciales— los que "
        "contestan igual: el borde no aplica lo que el código declara"),
}

# ⚠️ **QUÉ REGLAS EMITE CADA DETECTOR.** Es el puente entre el CATÁLOGO (que
# lista por tipo) y la MEDICIÓN (que mide por regla, porque la regla es la unidad
# que después se automatiza o no). Sin él, la tab muestra 40 capacidades y ningún
# número al lado — que es tener el dato y no usarlo.
#
# Se declara y **hay un test que lo cruza contra lo que los detectores emiten de
# verdad**: si alguien suma una regla y no la lista acá, sus votos no aparecen en
# ninguna fila y la skill se ve «sin votar» teniendo evidencia. Un error que no
# da ningún síntoma, que es la clase que este módulo persigue.
_REGLAS_DETECTOR: dict[str, tuple[str, ...]] = {
    "falta_en_base": ("no_esta_en_curvas",),
    "sin_flujo": ("flujos_vacios",),
    "hueco_de_curva": ("ajuste_sin_curva",),
    "tasa_sospechosa": ("moneda_flujo_contradice", "paridad_fuera_de_rango",
                        "sin_ejes", "sin_espejo_en_assets", "sin_tea_con_precio",
                        "tea_fuera_de_rango"),
    "sin_precio": ("no_suscripto", "precio_viejo", "sin_punta", "sin_simbolo"),
    "precio_moneda": ("cotiza_en_pesos", "pata_equivocada",
                      "precio_fuera_de_escala"),
    "dato_partido": ("copias_que_no_coinciden", "no_pude_chequear"),
    # SALUD arma la regla en tiempo de ejecución (`salud_<familia>`), así que la
    # lista no se puede declarar sin adivinar las familias. Vacío EXPLÍCITO: la
    # skill se muestra sin medición, que es honesto — mejor que un número que
    # cruza mal.
    "salud": (),
    # Los del SISTEMA: cada uno emite una sola regla, con su mismo nombre.
    "db_cambio": (), "latencia": (), "motor_caido": (),
    # La emitía y no la declaraba: sus votos se contaban sin poder decir POR QUÉ
    # causa acertó. Lo destapó el escáner nuevo del test (por AST).
    "tabla_quieta": ("sin_escribir",),
    "motor_ruidoso": ("rafaga", "machaca", "error_de_motor", "no_pude_leer"),
    "proveedor_caido": ("no_responde",),
    "recuperado": ("volvio",),
    "permiso_flojo": (),
}


# De qué habla cada detector. El TIPO dice cómo trabaja; esto dice sobre qué.
_DOMINIO_DETECTOR: dict[str, str] = {
    "falta_en_base": MERCADO, "sin_flujo": MERCADO, "tasa_sospechosa": MERCADO,
    "hueco_de_curva": MERCADO, "sin_precio": MERCADO, "precio_moneda": MERCADO,
    "salud": SISTEMA, "db_cambio": SISTEMA, "latencia": SISTEMA,
    "tabla_quieta": SISTEMA, "motor_caido": SISTEMA,
    "motor_ruidoso": SISTEMA, "proveedor_caido": SISTEMA,
    "recuperado": SISTEMA,
    # De MERCADO y no de sistema: lo que contesta es si un bono cotiza.
    "respuesta": MERCADO,
    "dato_partido": DATOS,
    "cron_desalineado": SISTEMA,
    "permiso_flojo": SEGURIDAD,
}

# Y de las ACCIONES y los EXPLICADORES, que tienen id propio.
# Los controles de datos: la mayoría son del NEGOCIO, pero varios miran mercado.
_DOMINIO_CONTROL: dict[str, str] = {
    "forwards_faltantes": MERCADO, "rf_sin_tasa": MERCADO,
    "titulos_sin_flujo": MERCADO, "patas_sin_precio": MERCADO,
    "patas_equivocadas": MERCADO,
    "patas_dolar_sin_pedir": MERCADO,
    "simbolos_cuarentena": MERCADO, "rf_valuada_x1": MERCADO,
    "comitentes_sin_nivel1": ADMIN, "contrapartes_pendientes": ADMIN,
}

_DOMINIO_ACCION: dict[str, str] = {
    "assets.cartera": DATOS, "assets.fci": DATOS,
    "contrapartes.alta": ADMIN, "avisar.responsable": ADMIN,
    "mercado.pedir_pata": MERCADO, "mercado.pata_dolar": MERCADO,
}


# Los EXPLICADORES, por id. Se declara y no se infiere del texto de la pregunta:
# «¿hay algún endpoint más lento?» contiene la palabra endpoint y caía en
# SEGURIDAD, cuando habla de rendimiento. Adivinar el dominio leyendo un título
# es el mismo tipo de heurística frágil que este proyecto ya paga en otros lados.
_DOMINIO_EXPLICADOR: dict[str, str] = {
    "rinde": MERCADO, "tna_futuros": MERCADO, "ytm_soberano": MERCADO,
    "breakeven": MERCADO, "pivots": MERCADO,
    "base": SISTEMA, "velocidad": SISTEMA, "todo_bien": SISTEMA,
    "protegidos": SEGURIDAD,
}


# ⚠️ **DÓNDE CORRE CADA DETECTOR — y por qué esto existe.**
#
# Hasta el 2026-08-19 tres detectores (`permiso_flojo`, `tabla_quieta`,
# `db_cambio`) corrían todas las noches y **solo imprimían en el log del job**:
# el hallazgo moría ahí. Y sin embargo esta misma tab decía «se ve en AV Agent →
# ENCONTRÓ», porque el `donde` estaba escrito fijo para todos.
#
# *Un catálogo que promete algo que la pantalla no da es peor que no tener
# catálogo*: manda a buscar a un lugar donde no está. Así que ahora cada detector
# declara EN QUÉ JOB corre, y un test exige que ese job **escriba hallazgos** —
# no que exista, que escriba. El horario NO se declara: se lee del crontab, que
# es la fuente real y no se puede desincronizar.
_DONDE_CORRE: dict[str, str] = {
    # La relevada nocturna (censa 1816 y deja una CORRIDA).
    "falta_en_base": "jobs.av_agent",
    "sin_flujo": "jobs.av_agent",
    "tasa_sospechosa": "jobs.av_agent",
    "hueco_de_curva": "jobs.av_agent",
    "salud": "jobs.av_agent",
    # El monitor de rueda (reemplaza lo suyo cada pasada).
    "sin_precio": "jobs.av_agent_live",
    "precio_moneda": "jobs.av_agent_live",
    "latencia": "jobs.av_agent_live",
    "motor_caido": "jobs.av_agent_live",
    "motor_ruidoso": "jobs.av_agent_live",
    "proveedor_caido": "jobs.av_agent_live",
    "recuperado": "jobs.av_agent_live",
    "respuesta": "jobs.av_agent_live",
    # El monitor del SISTEMA, de noche (reemplaza lo suyo cada pasada).
    "db_cambio": "jobs.db_tamano",
    "tabla_quieta": "jobs.db_tamano",
    "permiso_flojo": "jobs.db_tamano",
    "dato_partido": "jobs.db_tamano",
    "cron_desalineado": "jobs.db_tamano",
}


def _cada_cuanto(modulo: str) -> str:
    """El schedule REAL del crontab. No se escribe a mano a propósito."""
    try:
        from api.services import jobs_catalogo
        return " · ".join(jobs_catalogo.schedules_por_modulo().get(modulo) or [])
    except Exception:
        return ""


def _de_detectores() -> list[Skill]:
    """Darse cuenta solo. **Ninguno usa IA**, y eso es lo importante de esta
    lista: lo que encuentra el agente lo encuentra una función determinista que
    corre sola. El modelo aparece después, para leer patrones entre hallazgos."""
    from api.services import av_agent
    return [Skill(
        id=f"detectar.{tipo}", tipo=DETECTAR,
        nombre=_QUE_DETECTA.get(tipo, (tipo, ""))[0],
        que_hace=_QUE_DETECTA.get(tipo, ("", "sin describir"))[1],
        usa_ia=SIN_IA, dominio=_DOMINIO_DETECTOR.get(tipo, SISTEMA),
        donde="AV Agent → ENCONTRÓ",
        fuente="av_agent.ACCION_POR_TIPO",
        extra={"accion": av_agent.ACCION_POR_TIPO.get(tipo),
               # Que el catálogo diga CUÁNDO corre cada cosa es la mitad de la
               # respuesta a «¿esto se mantiene solo o hay que pedírselo?».
               "corre_en": _DONDE_CORRE.get(tipo, ""),
               "cada": _cada_cuanto(_DONDE_CORRE.get(tipo, "")),
               # QUÉ REGLAS emite, para poder cruzar la skill con su MEDICIÓN.
               # El eval set mide por REGLA (que es la unidad que después se
               # automatiza o no) y el catálogo lista por TIPO: sin este puente,
               # la tab muestra 40 capacidades y ningún número al lado.
               "reglas": sorted(_REGLAS_DETECTOR.get(tipo, ())),},
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
        usa_ia=SIN_IA, dominio=_DOMINIO_CONTROL.get(c.id, DATOS),
        donde="AV Agent → ENCONTRÓ",
        fuente="jobs.controles_datos.CONTROLES",
        extra={"resuelve": hc.POR_CONTROL.get(c.id)},
    ) for c in CONTROLES]


def catalogo() -> list[Skill]:
    """TODO lo que el agente sabe hacer, derivado de los registros reales."""
    out: list[Skill] = []
    for fn in (_de_detectores, _de_controles, _de_explicadores, _de_acciones,
               _de_capacidades):
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
    por_dominio: dict[str, list[dict]] = {d: [] for d in DOMINIOS}
    for s in skills:
        fila = {
            "id": s.id, "nombre": s.nombre, "que_hace": s.que_hace,
            "tipo": s.tipo, "dominio": s.dominio,
            "usa_ia": s.usa_ia, "para_que_la_ia": s.para_que_la_ia,
            "donde": s.donde, "fuente": s.fuente, "extra": s.extra}
        por_tipo.setdefault(s.tipo, []).append(fila)
        por_dominio.setdefault(s.dominio, []).append(fila)
    # Un dominio vacío no se muestra: un título con cero filas es ruido que
    # además sugiere que falta algo.
    por_dominio = {d: f for d, f in por_dominio.items() if f}
    return {
        "total": len(skills),
        "por_tipo": por_tipo,
        "por_dominio": por_dominio,
        "dominios": [d for d in DOMINIOS if d in por_dominio],
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
