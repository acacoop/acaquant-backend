"""api/services/av_agent_tipos.py — **EL CATÁLOGO DE LO QUE EL AGENTE SABE VER.**

Doc madre: `docs/AV_AGENT.md` §0.dm.

POR QUÉ EXISTE
==============

Agregar un detector obligaba a tocar **cinco listas paralelas**, a mano, en dos
archivos distintos: qué detecta · qué reglas emite · de qué dominio es · en qué
job corre · qué acción le corresponde. Diecinueve tipos por cinco listas son
**95 celdas** que nadie mantiene junto y que se mantienen por convención.

Y la evidencia de que dolía ya estaba escrita en los tests: había que exigir por
test que un tipo nuevo tuviera descripción, que declarara dominio, que dijera en
qué job corre. **Un test que existe para recordarte algo es la señal de que el
diseño no lo garantiza solo.**

El contraste vivía en el mismo repo: para sumar una ACCIÓN se escribe una clase
y se la agrega a una tupla — `POR_CONTROL` se deriva sola y no hace falta ningún
test de «¿está declarada?», porque la clase ES el registro. Esto le da a los
detectores el mismo trato.

CÓMO SE USA
===========

Sumar un tipo es **una fila acá**. Los cinco mapas se derivan de esta tabla y
conservan sus nombres, así que ningún consumidor cambió:

    av_agent_skills._QUE_DETECTA · _REGLAS_DETECTOR · _DOMINIO_DETECTOR
                   _DONDE_CORRE
    av_agent.ACCION_POR_TIPO · EN_AHORA_SIEMPRE · TIPOS_NOTICIA

⚠️ **`en_ahora` y `noticia` siguen siendo OPT-IN, y eso es a propósito.** Los dos
default en `False`: un tipo nuevo NO entra a AHORA ni se convierte en noticia
salvo que alguien lo escriba. AHORA deja de ser AHORA si se llena. Lo único que
cambió es que la declaración vive al lado del tipo en vez de en una tupla suelta
al final de un archivo de 2.100 líneas — y las tuplas que ya existían se derivan
de acá, así que no pueden separarse de esta tabla sin que un test lo cante.

⚠️ **Este módulo no importa NADA del proyecto.** Es una tabla de datos, y tiene
que poder ser leída tanto por `av_agent` como por `av_agent_skills` —que importa
al primero— sin abrir un ciclo.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tipo:
    """UNA familia de hallazgo, con todo lo que hay que saber de ella.

    `titulo` y `detalle` son lo que muestra la tab SKILLS: qué mira y por qué
    importa, en castellano. `reglas` son las causas concretas que ese detector
    puede emitir — lo que se vota en el eval set.
    """

    tipo: str
    titulo: str
    detalle: str
    dominio: str
    donde_corre: str
    # Qué puerta puede arreglarlo. Vacío = todavía no hay acción para esto, y
    # eso es un dato: la pantalla lo dice en vez de dejar la fila muda.
    accion: str = ""
    # ⚠️ **TRES ESTADOS, no dos, y la diferencia es de negocio:**
    #   None → no se declaró. Lo caza el test del catálogo.
    #   ()   → declarado VACÍO a propósito: la skill se muestra sin medición.
    #   (…)  → las causas que este detector emite y que se votan en el eval set.
    # Colapsarlo a «vacío o no» borraba la segunda, y con ella la diferencia
    # entre «esto no se mide porque lo decidimos» y «alguien se olvidó».
    reglas: tuple[str, ...] | None = None
    # Se muestra en AHORA mientras esté abierto, sea o no novedad del día. Solo
    # para lo que puede estar roto EN ESTE MOMENTO (motores, proveedores): con
    # el corte por novedad, cuanto más tiempo llevaba roto menos se veía.
    en_ahora: bool = False
    # Observación sin accionable: su casa es AHORA (el noticiero), no LA LISTA.
    noticia: bool = False

    def __post_init__(self) -> None:
        """Un tipo sin dominio o sin job es un tipo que la pantalla no puede
        ubicar. Se rechaza al construir — o sea al importar — y no cuando
        alguien abre la tab y encuentra una fila en blanco."""
        for campo in ("tipo", "titulo", "detalle", "dominio", "donde_corre"):
            if not str(getattr(self, campo) or "").strip():
                raise ValueError(
                    f"el tipo «{self.tipo or '(sin nombre)'}» no declara "
                    f"«{campo}»: sin eso la tab SKILLS lo muestra vacío y "
                    f"nadie sabe qué mira ni dónde corre")


TIPOS: dict[str, Tipo] = {t.tipo: t for t in (
    Tipo("falta_en_base",
         titulo="Bonos que nos faltan",
         detalle="los que 1816 lista y no están en el master",
         dominio="MERCADO", donde_corre="jobs.av_agent",
         accion="alta", reglas=("no_esta_en_curvas",)),
    Tipo("sin_flujo",
         titulo="Bonos sin cronograma",
         detalle="están cargados pero sin flujos de pago: no valúan",
         dominio="MERCADO", donde_corre="jobs.av_agent",
         accion="flujos", reglas=("flujos_vacios",)),
    Tipo("tasa_sospechosa",
         titulo="Tasas que no cierran",
         detalle="se apartan de las de 1816 más de lo tolerable",
         dominio="MERCADO", donde_corre="jobs.av_agent",
         accion="arreglo", reglas=("moneda_flujo_contradice", "paridad_fuera_de_rango", "sin_ejes", "sin_espejo_en_assets", "sin_tea_con_precio", "tea_fuera_de_rango")),
    Tipo("hueco_de_curva",
         titulo="Ajustes sin curva",
         detalle="existen en el master y no tienen pill: esos bonos quedan invisibles sin"
                 " dar ningún error",
         dominio="MERCADO", donde_corre="jobs.av_agent",
         reglas=("ajuste_sin_curva",)),
    Tipo("salud",
         titulo="Jobs que fallaron",
         detalle="no corrieron, salieron con error o dejaron el dato viejo",
         dominio="SISTEMA", donde_corre="jobs.av_agent",
         accion="salud",
         reglas=()),
    Tipo("sin_precio",
         titulo="Bonos sin precio, en rueda",
         detalle="distingue las 4 causas y mide en TIEMPO DE MERCADO: sin símbolo cargado ·"
                 " nadie lo suscribió · suscripto sin punta · el precio dejó de moverse",
         dominio="MERCADO", donde_corre="jobs.av_agent_centinela",
         accion="sin_precio", reglas=("no_suscripto", "precio_viejo", "sin_punta", "sin_simbolo")),
    Tipo("precio_moneda",
         titulo="Precios en la moneda equivocada",
         detalle="bonos de curva USD que muestran pesos, separando el que suscribe la PATA"
                 " EQUIVOCADA del que cotiza así de verdad — y para eso busca la pata en"
                 " dólares en DOS fuentes: `mercado.especies` y, si ahí no está, el catálogo"
                 " de Primary (mirar una sola no alcanza para decir que no existe)",
         dominio="MERCADO", donde_corre="jobs.av_agent_centinela",
         accion="pata", reglas=("cotiza_en_pesos", "pata_equivocada", "precio_fuera_de_escala")),
    Tipo("actividad",
         titulo="Actividad en día no hábil",
         detalle="sábado, domingo o feriado el mercado no abre y NADA debería escribir"
                 " precios: si `market_snapshot` recibe escrituras o el motor de órdenes"
                 " late, algo quedó prendido o un cron corre cuando no debe — acá el hallazgo"
                 " es la actividad misma, no lo que el dato diga",
         dominio="SISTEMA", donde_corre="jobs.av_agent_centinela",
         en_ahora=True),
    Tipo("cron_desalineado",
         titulo="Crons del repo que no corren",
         detalle="compara `deploy/crontab.txt` —que TODO el sistema trata como la fuente de"
                 " verdad— contra el crontab REAL de la máquina. `deploy.sh` no lo instala,"
                 " así que un cron nuevo puede vivir en el repo y no ejecutarse nunca: no"
                 " falla nada, el catálogo lo muestra igual y el job no corrió. Mira las dos"
                 " direcciones (lo que falta instalar y lo que corre sin estar declarado) y"
                 " si no puede leer el crontab lo DICE en vez de callarse",
         dominio="SISTEMA", donde_corre="jobs.av_agent_sistema"),
    Tipo("respuesta",
         titulo="La respuesta a lo que se pidió",
         detalle="cierra el círculo de una acción cuyo efecto NO es inmediato. Cuando se"
                 " pide la pata en dólares de un bono, si esa pata cotiza o no lo contesta el"
                 " mercado y no nosotros: acá se relee en cada pasada de rueda y se canta el"
                 " veredicto, incluido el «no cotiza» — que es el que cierra el tema. La"
                 " espera se mide en tiempo de mercado abierto, así que un «no» nunca es"
                 " impaciencia",
         dominio="MERCADO", donde_corre="jobs.av_agent_centinela"),
    Tipo("dato_partido",
         titulo="Copias del mismo dato que no coinciden",
         detalle="los datos que viven en más de un lugar y dejaron de decir lo mismo."
                 " Detecta la CLASE de bug, no el caso: cuando dos copias se separan NO falla"
                 " nada —cada mitad sigue coherente— y el sistema contesta con seguridad"
                 " usando la equivocada. El registro de qué está duplicado y quién manda se"
                 " DECLARA en `core/duplicados`",
         dominio="DATOS", donde_corre="jobs.av_agent_sistema",
         reglas=("copias_que_no_coinciden", "no_pude_chequear")),
    Tipo("db_cambio",
         titulo="La base cambió",
         detalle="tablas nuevas, las que crecieron de golpe y las que desaparecieron,"
                 " comparando la foto de hoy contra la de ayer",
         dominio="SISTEMA", donde_corre="jobs.av_agent_sistema",
         noticia=True,
         reglas=()),
    Tipo("latencia",
         titulo="Endpoints degradados",
         detalle="los que se pusieron lentos contra SU PROPIA normalidad (no un ranking de"
                 " los más lentos) y los que devuelven 5xx",
         dominio="SISTEMA", donde_corre="jobs.av_agent_centinela",
         reglas=()),
    Tipo("tabla_quieta",
         titulo="Tablas que dejaron de escribir",
         detalle="la cadencia de cada una se MIDE observándola, no la declara nadie, y el"
                 " atraso se cuenta en tiempo de mercado",
         dominio="SISTEMA", donde_corre="jobs.av_agent_sistema",
         reglas=("sin_escribir",), noticia=True),
    Tipo("motor_caido",
         titulo="Motores y jobs caídos",
         detalle="solo DENTRO de su ventana horaria: fuera de rueda un motor no está caído,"
                 " está apagado",
         dominio="SISTEMA", donde_corre="jobs.av_agent_centinela",
         en_ahora=True,
         reglas=()),
    Tipo("motor_ruidoso",
         titulo="Lo que los motores vienen diciendo",
         detalle="no si PRODUCEN (eso es el de arriba) sino si se están rompiendo mientras"
                 " producen. Distingue la RÁFAGA —algo fallando en loop ahora— de lo que"
                 " MACHACA todo el día, que es una config rota que nadie mira: la cuenta sola"
                 " no las separa, 76 veces en 3 minutos y 91 en 7 horas son dos problemas"
                 " distintos",
         dominio="SISTEMA", donde_corre="jobs.av_agent_centinela",
         reglas=("rafaga", "machaca", "error_de_motor", "no_pude_leer"), en_ahora=True),
    Tipo("recuperado",
         titulo="Avisa también cuando algo VUELVE",
         detalle="hasta ahora, si un motor caído volvía, su aviso simplemente dejaba de"
                 " escribirse: el que estaba esperando no se enteraba nunca. Compara la"
                 " corrida de ahora con la anterior y canta lo que se arregló. Dura 30"
                 " minutos: una buena noticia envejece más rápido que una mala",
         dominio="SISTEMA", donde_corre="jobs.av_agent_centinela",
         reglas=("volvio",)),
    Tipo("proveedor_caido",
         titulo="Proveedores de afuera que no responden",
         detalle="Aunesa, 1816, Interbanking, BCRA. Se entera por el rastro de las llamadas"
                 " REALES (no gasta créditos de 1816 ni se cree un health check que contesta"
                 " bien mientras el endpoint que usamos devuelve 500) y, cuando ya hay una"
                 " falla, LLAMA: prueba sin credenciales para separar «se cayeron ellos» de"
                 " «se nos venció una clave», y barre las 5 APIs de Aunesa para decir si es"
                 " su servicio entero o un endpoint. Avisa DIRECTO al back office, que es"
                 " quien lo sufre y no ve la pantalla del agente",
         dominio="SISTEMA", donde_corre="jobs.av_agent_centinela",
         reglas=("no_responde",), en_ahora=True),
    Tipo("permiso_flojo",
         titulo="Permisos que están solo en los papeles",
         detalle="endpoints sin gate, y —probando de verdad, sin credenciales— los que"
                 " contestan igual: el borde no aplica lo que el código declara",
         dominio="SEGURIDAD", donde_corre="jobs.db_tamano",
         reglas=()),
)}


# ── LOS MAPAS DERIVADOS ─────────────────────────────────────────────────────
#
# Se derivan y no se copian: el día que alguien agregue una fila arriba, las
# siete salen actualizadas solas. Es el mismo mecanismo que `POR_CONTROL` con
# las acciones — y la razón por la que ahí nunca hizo falta un test de «¿está
# declarado?».

def que_detecta() -> dict[str, tuple[str, str]]:
    return {t.tipo: (t.titulo, t.detalle) for t in TIPOS.values()}


def reglas_por_tipo() -> dict[str, tuple[str, ...]]:
    """Los que DECLARARON reglas — incluidos los que declararon **ninguna**.

    La clave ausente y la clave con tupla vacía significan cosas distintas
    (ver `Tipo.reglas`), así que acá entra todo lo que no sea `None`."""
    return {t.tipo: t.reglas for t in TIPOS.values() if t.reglas is not None}


def dominio_por_tipo() -> dict[str, str]:
    return {t.tipo: t.dominio for t in TIPOS.values()}


def donde_corre() -> dict[str, str]:
    return {t.tipo: t.donde_corre for t in TIPOS.values()}


def accion_por_tipo() -> dict[str, str | None]:
    """⚠️ **Con TODAS las claves, y `None` donde no hay acción.** Que la clave
    exista es lo que permite preguntar «¿este tipo tiene puerta?» y contestar
    «no» — distinto de «no sé qué es este tipo»."""
    return {t.tipo: (t.accion or None) for t in TIPOS.values()}


def en_ahora_siempre() -> tuple[str, ...]:
    return tuple(t.tipo for t in TIPOS.values() if t.en_ahora)


def tipos_noticia() -> tuple[str, ...]:
    return tuple(t.tipo for t in TIPOS.values() if t.noticia)
