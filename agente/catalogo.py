"""`agente/catalogo.py` — **LAS HABILIDADES.** Doc: `docs/AGENT.md` §3.

Sumar una habilidad es **una fila acá**. No hay que tocar un reloj, ni una lista
de tipos, ni un mapa de dominios, ni un test que recuerde declararla.

En el agente viejo, agregar un detector obligaba a tocar CINCO listas paralelas
en dos archivos: qué detecta · qué reglas emite · de qué dominio es · en qué job
corre · qué acción le corresponde. Diecinueve tipos por cinco listas son 95
celdas que nadie mantiene juntas. Y la prueba de que dolía ya estaba escrita en
los tests: había que EXIGIR por test que un tipo nuevo tuviera descripción y
declarara dominio. **Un test que existe para recordarte algo es la señal de que
el diseño no lo garantiza solo.**

⚠️ **`arreglos` va POR REGLA, no por habilidad.** Una habilidad puede tener
reglas de las dos clases: en `precio_moneda`, `pata_equivocada` se arregla con un
botón y `cotiza_en_pesos` es contexto. De ahí sale la CLASE, que nadie escribe:
con arreglo → `trabajo` (AHORA + ENCONTRÓ) · sin arreglo → `aviso` (solo AHORA).
"""
from __future__ import annotations

import logging

from agente.detectores import catalogo as cat
from agente.detectores import datos, mercado, sistema
from agente.tipos import INCIDENTE, INFORME, RECURRENTE, SIN_EPISODIOS, Habilidad
from agente.umbrales import PARIDAD_MAX, PARIDAD_MIN
from core.postgres import get_pool

logger = logging.getLogger(__name__)

_M, _H = 60, 3600

HABILIDADES: dict[str, Habilidad] = {h.nombre: h for h in (

    # ── MERCADO ────────────────────────────────────────────────────────────
    Habilidad(
        nombre="soberanos_faltantes", tipo="detector", dominio="MERCADO",
        que_mira="bonos que 1816 lista y no están en nuestro master",
        cada_segundos=2 * _H, ventana="rueda",
        correr=mercado.soberanos_faltantes, sujeto_es="bono",
        arreglos={"no_esta_en_curvas": "alta_bono"},
        # El sujeto es UN ticker: que el MISMO bono aparezca y desaparezca tres
        # veces en un mes sí es un patrón (el alta no pegó, o 1816 lo publica a
        # los saltos). Cuenta episodios.
        naturaleza={"no_esta_en_curvas": INCIDENTE}),

    Habilidad(
        nombre="bono_sin_flujo", tipo="detector", dominio="MERCADO",
        que_mira="bonos cargados sin cronograma de pagos: no valúan",
        cada_segundos=2 * _H, ventana="siempre",
        correr=mercado.bono_sin_flujo, sujeto_es="bono",
        arreglos={"sin_flujo": "alta_flujos"},
        # Sujeto = un ticker. Un bono que se queda sin cronograma tres veces es
        # la cadena de alta fallando, no el ritmo del catálogo.
        naturaleza={"sin_flujo": INCIDENTE}),

    # ⚠️ Reemplaza a `tasa_sospechosa`, que metía SEIS reglas bajo un nombre.
    # Es un AVISO a propósito: el agujero lo tapa el sistema solo (la lista de
    # prioridad le pide la tasa a 1816 cada 15 min), no una persona apretando.
    Habilidad(
        nombre="bono_sin_tasa", tipo="detector", dominio="MERCADO",
        que_mira="bonos que operan y a los que el motor no les calcula la TEA",
        cada_segundos=15 * _M, ventana="rueda",
        correr=mercado.bono_sin_tasa, sujeto_es="bono"),

    # ⚠️ **LA ÚNICA CON VENTANA `cierre`.** Corre UNA vez, 17:30 ART, con la
    # rueda cerrada: lo que se le pide al mercado deja de pedirse a las 17.
    Habilidad(
        nombre="tasas_al_cierre", tipo="detector", dominio="MERCADO",
        que_mira="rellena con 1816 lo que quedó sin tasa, y canta lo que ni así",
        cada_segundos=12 * _H, ventana="cierre",
        correr=mercado.tasas_al_cierre, sujeto_es="bono"),

    # `scripts/diag_tea_corp_hd` hecho habilidad (§0.do): la tabla TNA/TEA
    # nuestras contra las de 1816 en los corporativos hard dólar. UN hallazgo con
    # la tabla adentro, que el registro refresca cada corrida — sin umbral y sin
    # decir cuál está mal: compara, no concluye. Cuesta créditos de 1816
    # (tickers × 2 por corrida), por eso cada 2 h como `soberanos_faltantes`.
    # ⚠️ SIN `sujeto_es`: el sujeto es una familia, no un bono (como
    # `cedear_faltante`), y no hay partida de defunción de una tabla.
    Habilidad(
        nombre="tasa_vs_1816", tipo="detector", dominio="MERCADO",
        que_mira="corporativos hard dólar: la tabla de nuestra TNA/TEA contra la de 1816",
        cada_segundos=2 * _H, ventana="rueda",
        correr=mercado.tasa_vs_1816,
        # Nace por CALENDARIO (cada 2 h) y no porque algo se rompió: es un
        # informe, no un problema. Sin esto, la tabla misma se volvía
        # «crónica» a las tres corridas (`AGENT.md` §0.eg).
        naturaleza={"tabla": INFORME}),

    # `soberanos_faltantes` para las ONs en dólares (§0.dp). Dos diferencias, y
    # ninguna es de gusto: Primary es CONDICIÓN (sin foto no se ofrece nada), y
    # no tiene arreglo porque la rama `on` no convierte el cuadro sola todavía —
    # un botón que siempre bloquea enseña a no apretar.
    Habilidad(
        nombre="on_faltante", tipo="detector", dominio="MERCADO",
        que_mira="ONs hard dólar que 1816 lista, Primary cotiza y no están en el master",
        cada_segundos=2 * _H, ventana="rueda",
        correr=mercado.on_faltante, sujeto_es="bono",
        # ⚠️ El botón existe desde que la rama `on` entró en `RAMAS_AUTOMATICAS`
        # (§0.du): el paso `rama` dejó de bloquearlas a todas por adelantado y
        # cada bono lo juzga su propio cotejo contra 1816. Lo aplica sobre la
        # regla `no_esta_en_curvas` —las que la mesa TIENE y hoy no valúan—; la
        # fila de familia (`no_estan_en_curvas`) sigue sin arreglo, porque no hay
        # nada roto ahí y es una oferta de catálogo, no un problema.
        arreglos={"no_esta_en_curvas": "alta_bono",
                  # La fila de familia despliega la lista para TILDAR: 1816
                  # publica muchas más de las que la mesa quiere seguir, y el
                  # sistema sabe escribirlas todas pero no cuáles (§0.dv).
                  "no_estan_en_curvas": "alta_on"},
        # Solo esta regla, no la de familia (`no_estan_en_curvas` es una
        # OFERTA de catálogo para tildar, no un problema — nadie decide
        # solo qué ONs seguir).
        automatico={"no_esta_en_curvas":
                    "la mesa lo tiene en cartera y hoy no valúa; el "
                    "pre-flight baja el cuadro de 1816, lo coteja contra el "
                    "de ellos y solo escribe si cierra"},
        # ⚠️ **LAS DOS REGLAS SE VEN IGUAL Y SON DE NATURALEZA OPUESTA** (§0.ep).
        # `no_esta_en_curvas` habla de UN ticker que la mesa tiene en cartera y
        # hoy no valúa: si el mismo vuelve tres veces, algo pasa. La fila de
        # familia (`no_estan_en_curvas`, sujeto «ONs HARD DÓLAR») es la OFERTA
        # de catálogo: nace, se tilda, se vacía y vuelve a nacer cada vez que
        # 1816 publica una ON nueva. Sus episodios cuentan cuántas veces creció
        # el mercado — y la pantalla los mostraba como «⚠ crónico · 3× en 30d».
        naturaleza={"no_esta_en_curvas": INCIDENTE,
                    "no_estan_en_curvas": RECURRENTE}),

    # ⚠️ **LA ILIQUIDEZ NO ES UNA REGLA DE ESTA HABILIDAD: ES SU SILENCIO**
    # (§0.eu). De las cuatro causas de «este bono no tiene precio», TRES son
    # nuestras y las tres avisan `alta` (`sin_simbolo`, `simbolo_rechazado`,
    # `no_suscripto`). La cuarta —lo pedimos y el mercado no dio punta— no
    # emite nada: es un hecho del papel, no del sistema, y el detector lo
    # saltea. Que las tres primeras sigan existiendo es lo que hace que el
    # silencio SIGNIFIQUE iliquidez y no «no miramos».
    Habilidad(
        nombre="bono_sin_precio", tipo="detector", dominio="MERCADO",
        que_mira="bonos sin precio en rueda: avisa las 3 causas nuestras, calla la iliquidez",
        cada_segundos=5 * _M, ventana="rueda",
        correr=mercado.bono_sin_precio, sujeto_es="bono",
        umbrales={"precio_viejo_min": 20},
        # `sin_simbolo`, `simbolo_rechazado` y `precio_viejo` NO tienen arreglo,
        # y eso se DECLARA: hay algo que verificar, pero no un botón que escriba.
        arreglos={"no_suscripto": "pedir_pata"},
        # Sujeto = un ticker, y todas las reglas son sobre el feed de ESE
        # papel: repetirse es exactamente lo que hay que ver.
        naturaleza={"no_suscripto": INCIDENTE}),

    Habilidad(
        nombre="precio_moneda", tipo="detector", dominio="MERCADO",
        que_mira="bonos de curva USD cuyo precio llega en pesos",
        cada_segundos=5 * _M, ventana="rueda",
        correr=mercado.precio_moneda, sujeto_es="bono",
        # ⚠️ La banda NO se escribe acá: la lee `agente/umbrales.py`, que es de
        # donde también la toma la cadena de alta. Con el número repetido, el
        # detector podría marcar un bono que el alta considera sano y ninguna
        # de las dos mitades fallaría (REGLA #9).
        umbrales={"paridad_min": PARIDAD_MIN, "paridad_max": PARIDAD_MAX},
        arreglos={"pata_equivocada": "apuntar_pata",
                  "cotiza_en_pesos": "pata_dolar"},
        # Sujeto = un ticker. Un bono que cotiza en la moneda equivocada tres
        # veces en un mes es la pata mal apuntada volviendo, no un alta nueva.
        naturaleza={"pata_equivocada": INCIDENTE, "cotiza_en_pesos": INCIDENTE}),

    # ⚠️ **SIN `sujeto_es`, Y NO ES UN OLVIDO.** Su sujeto es un AJUSTE
    # (`badlar`, `tpm`), no un título: no hay nada que pueda «vencer», así que
    # no hay partida de defunción que buscar. Declararle `bono` haría que
    # `vigencia` fuera a buscar el ticker «BADLAR» a tres catálogos, no lo
    # encontrara, y contestara «no sé» — o sea nada, pero pagando la query y
    # dejando escrito que acá hay una caducidad que en realidad no existe.
    Habilidad(
        nombre="hueco_de_curva", tipo="detector", dominio="MERCADO",
        que_mira="ajustes que existen en el master y que la app no sabe mostrar",
        cada_segundos=6 * _H, ventana="siempre",
        correr=mercado.hueco_de_curva),

    # Los CEDEARs, por FICHA (§0.dl): calibra el cficode con los que ya
    # tenemos y busca los que faltan con esa misma ficha. Un hallazgo por
    # familia; la lista se tilda en ENCONTRÓ. La segunda regla no tiene
    # arreglo a propósito: corregir un símbolo o apagar un papel lo decide la
    # mesa en Manager, no un botón.
    # ⚠️ **SIN `sujeto_es`, Y TAMPOCO ES UN OLVIDO.** Un CEDEAR no es un bono:
    # no está en `mercado.curvas` ni en el catálogo de 1816, así que el
    # verificador de bonos contestaría «no sé» para todos. Y un tipo `cedear`
    # propio necesitaría una partida de defunción que hoy sería CIRCULAR: la
    # única señal de baja disponible es que Primary no lo liste, que es
    # exactamente lo que esta habilidad REPORTA como hallazgo — usarla para
    # caducar haría que el detector se cerrara sus propios hallazgos.
    # Además su sujeto es mixto: una FAMILIA en una regla y un ticker en la otra.
    Habilidad(
        nombre="cedear_faltante", tipo="detector", dominio="MERCADO",
        que_mira="CEDEARs que Primary lista (por ficha) y no tenemos, y los nuestros que Primary no lista",
        cada_segundos=6 * _H, ventana="siempre",
        correr=mercado.cedear_faltante,
        umbrales={"min_propios": 3},
        arreglos={"no_esta_en_master": "alta_cedear"},
        # Mismo par que `on_faltante` y por el mismo motivo (§0.ep): la primera
        # regla tiene por sujeto la FAMILIA («CEDEAR») y es una oferta que se
        # llena y se vacía; la segunda es UN símbolo que Primary dejó de listar
        # y sí es un problema de ese papel.
        naturaleza={"no_esta_en_master": RECURRENTE,
                    "no_cotiza_en_primary": INCIDENTE}),

    # ── SISTEMA ────────────────────────────────────────────────────────────
    Habilidad(
        nombre="salud", tipo="detector", dominio="SISTEMA",
        que_mira="jobs que no corrieron, fallaron o dejaron el dato viejo",
        cada_segundos=10 * _M, ventana="siempre",
        correr=sistema.salud),
    # ⚠️ **`salud` es un AVISO, y eso es la corrección de un bug real.** En el
    # agente viejo declaraba una acción —así que sus hallazgos caían en la lista
    # de trabajo— pero su puerta era de SOLO LECTURA: el botón APLICAR estaba
    # deshabilitado por diseño y lo único que ofrecía era «↻ chequear ahora».
    # **Mirar no arregla.** El que SÍ tiene botón es el job que no dejó su dato,
    # y ese lo canta `motor_caido` con la regla `job_sin_dato`.

    Habilidad(
        nombre="motor_caido", tipo="detector", dominio="SISTEMA",
        que_mira="motores y jobs rotos DENTRO de su ventana horaria",
        cada_segundos=2 * _M, ventana="siempre",
        correr=sistema.motor_caido,
        umbrales={"gracia_arranque_min": 30},
        # Solo el JOB declarado como relanzable tiene botón. Un motor no: la
        # regla que emite el detector ya distingue los dos casos, así que no
        # puede quedar una fila con un botón que siempre falla.
        arreglos={"job_sin_dato": "rehacer_job"},
        # El sujeto es UN job: repetirse es la definición misma de lo crónico —
        # es el caso para el que se inventó PATRONES.
        naturaleza={"job_sin_dato": INCIDENTE}),

    # Los PROCESOS, por su latido (core/latido.py, §0.da). El universo sale de
    # deploy/systemd + crontab: un motor nuevo se espera solo. Sin arreglo a
    # propósito: reiniciar en rueda lo decide la mesa; el que_hacer trae el
    # comando. `motor_caido` queda para los JOBS, que se juzgan por resultado.
    Habilidad(
        nombre="motor_latido", tipo="detector", dominio="SISTEMA",
        que_mira="cada proceso de systemd late solo: apagado, colgado, sin feed o mudo",
        cada_segundos=2 * _M, ventana="siempre",
        correr=sistema.motor_latido,
        umbrales={"tolerancia_s": 90, "gracia_arranque_s": 120, "feed_mudo_min": 10}),

    Habilidad(
        nombre="tabla_quieta", tipo="detector", dominio="SISTEMA",
        que_mira="tablas que dejaron de escribir — la cadencia se MIDE, no se declara",
        cada_segundos=30 * _M, ventana="siempre",
        correr=sistema.tabla_quieta),

    # La FOTO de Primary es lo que filtra el WS y el alta; la refresca un cron
    # (12:15 UTC L-V) y esto canta si un día no corrió. Sin arreglo a propósito:
    # sacar la foto necesita sesión pyRofex, y el daemon no la tiene. §0.cy.
    Habilidad(
        nombre="foto_primary", tipo="detector", dominio="SISTEMA",
        que_mira="que la foto del catálogo de Primary (la que filtra el WS y el alta) no quede vieja",
        cada_segundos=1 * _H, ventana="siempre",
        correr=sistema.foto_primary,
        umbrales={"gracia_min": 60}),

    # La otra foto: el catálogo de 1816, de donde leen el emisor, la grafía
    # TAMAR y el alta. Era manual (§0.df): ahora corre por cron y esto lo vigila.
    Habilidad(
        nombre="foto_1816", tipo="detector", dominio="SISTEMA",
        que_mira="que el catálogo de 1816 (emisor, grafía TAMAR, alta) no quede viejo",
        cada_segundos=1 * _H, ventana="siempre",
        correr=sistema.foto_1816,
        umbrales={"gracia_min": 60}),

    Habilidad(
        nombre="cron_desalineado", tipo="detector", dominio="SISTEMA",
        que_mira="el crontab del repo contra el de la máquina, en las dos direcciones",
        cada_segundos=30 * _M, ventana="siempre",
        correr=sistema.cron_desalineado),

    Habilidad(
        nombre="latencia", tipo="detector", dominio="SISTEMA",
        que_mira="endpoints degradados contra SU PROPIA normalidad, los 5xx, y las vistas ciegas",
        cada_segundos=10 * _M, ventana="siempre",
        correr=sistema.latencia,
        # `vista_ciega` (§0.dg): el pulso que manda una pantalla que no puede
        # refrescar. Su hermana `pantalla_tildada` es una habilidad APARTE (y no
        # una regla más de acá) para que no poder leer una fuente no apague las
        # otras dos que esta habilidad sí puede ver — ver §0.dm.
        umbrales={"pulso_ventana_min": 10}),

    # La OTRA mitad de «se me colgó la app», y la que no pasa por el servidor
    # NUNCA: el navegador trabado. Nada falla, no hay request ni excepción — si
    # no lo cuenta el propio navegador (`lib/tilde.ts` → `POST /api/pulso` con
    # `tipo='tilde'`), acá no se entera nadie. Sin arreglo, y declarado: el
    # agente no puede tocar la pestaña de nadie. Doc: §0.dm.
    Habilidad(
        nombre="pantalla_tildada", tipo="detector", dominio="SISTEMA",
        que_mira="pantallas donde el NAVEGADOR se clavó: cuánto, cuántas veces y si fue JS",
        cada_segundos=10 * _M, ventana="siempre",
        correr=sistema.pantalla_tildada,
        # Ventana más larga que la del pulso a propósito: una ceguera es AHORA
        # (10'), un tilde es un episodio de segundos que hay que JUNTAR para que
        # se vea el patrón — uno solo no dice nada, seis en una hora sí.
        umbrales={"tilde_ventana_min": 60}),

    Habilidad(
        nombre="proveedor_caido", tipo="detector", dominio="SISTEMA",
        que_mira="Aunesa, 1816, Interbanking y BCRA, por el rastro de las llamadas reales",
        cada_segundos=5 * _M, ventana="siempre",
        correr=sistema.proveedor_caido,
        umbrales={"ventana_s": 1200, "minimo_fallos": 1},
        # ⚠️ **EL PRIMER CASO DEL TRIAGE** (2026-09-04). 20 minutos, y el número
        # sale del propio detector: corre cada 5' y le alcanza UN fallo para
        # cantar, así que un hallazgo que sigue vivo a los 20' lo vieron cuatro
        # pasadas seguidas. Eso ya no es un parpadeo — es una caída.
        #
        # Aunesa el 04/09 fue justo el otro caso: se cayó 12:35, el hallazgo
        # nació 12:41, y a la tarde ya no existía. Investigarlo al nacer habría
        # sido pagar por algo que se arregló solo.
        investigar={"no_responde": 20 * _M}),

    Habilidad(
        nombre="db_peso", tipo="detector", dominio="SISTEMA",
        que_mira=("lo que creció fuera de lo suyo, las tablas que faltan, y el "
                  "peso total de la base dos veces por día (11 y 16, hora de "
                  "la mesa)"),
        cada_segundos=_H, ventana="siempre",
        correr=sistema.db_peso,
        # El peso total nace por CALENDARIO (11 y 16 ART), no porque algo se
        # rompió: es un informe, no un problema (`AGENT.md` §0.eg). Las demás
        # reglas de esta habilidad (`crecio`, `desaparecio`) SÍ son problemas y
        # quedan afuera: sin declarar, caen en INCIDENTE, que es lo que son.
        naturaleza={"peso_total_11": INFORME, "peso_total_16": INFORME}),

    Habilidad(
        nombre="actividad", tipo="detector", dominio="SISTEMA",
        que_mira="escrituras de mercado en día NO hábil: algo quedó prendido",
        cada_segundos=15 * _M, ventana="siempre",
        correr=sistema.actividad),

    # ── CATÁLOGO DE TÍTULOS ────────────────────────────────────────────────
    #
    # ⚠️ **UN hallazgo por CAMPO, no uno por título.** 379 títulos sin clase son
    # UN trabajo de carga, no 379 problemas. El control viejo emitía una anomalía
    # por fila —y por eso su lista no se leía—, y el agente la recibía aplastada
    # en un aviso genérico cuyo sujeto era el nombre del control. Los dos
    # defectos son opuestos y los dos hacen lo mismo: que nadie la mire.
    #
    # Reemplaza a `assets_sin_cartera` y `fci_incompletos` de
    # `jobs/controles_datos`, y los EXPANDE a `clase_activo` y `emisor`
    # (pedido del user 2026-08-27).
    Habilidad(
        nombre="ficha_incompleta", tipo="detector", dominio="DATOS",
        que_mira="títulos en carteras de clientes con la ficha sin completar",
        # Son cinco consultas contra `portafolio.assets`: no tocan la red, no
        # cuestan créditos y contestan en el acto. Con `6 * _H` un título nuevo
        # esperaba media jornada para que alguien lo viera; con `_H` el
        # ejecutor lo completa dentro de la hora.
        cada_segundos=_H, ventana="siempre",
        correr=cat.ficha_incompleta,
        # Las CUATRO reglas comparten arreglo: el listado editable es el mismo,
        # cambia la columna. Se declaran las cuatro igual —y no un `default`—
        # porque una regla nueva tiene que decidir explícitamente si lo tiene.
        arreglos={"sin_cartera": "completar_ficha",
                  "sin_clase_activo": "completar_ficha",
                  "sin_emisor": "completar_ficha",
                  "fci_sin_ticker": "completar_ficha"},
        # Las cuatro son TRABAJO RECURRENTE (§0.ek): el sujeto es el CAMPO
        # —un grupo que sube y baja de número—, y un título nuevo llega con la
        # ficha vacía siempre. No son crónicas: son el ritmo de la cartera.
        naturaleza={"sin_cartera": RECURRENTE, "sin_clase_activo": RECURRENTE,
                    "sin_emisor": RECURRENTE, "fci_sin_ticker": RECURRENTE},
        # Solo `sin_clase_activo`: son las CINCO reglas determinísticas de
        # `core/clase_activo.py` (derivados con C/P → CALL/PUT OPCIONES,
        # futuros y OTC de agro/dólar por el prefijo del contrato, copia
        # de cartera — RENTA VARIABLE/HD/DL —, FCI por Primary — Mercado de
        # Dinero → MM, Renta Fija → T1, Renta Variable —, y ARS por la CURVA
        # del bono en el master). Lo demás —el emisor, y la cartera para lo
        # que no cae en ninguna de estas cinco— sigue quedando para una
        # persona: no hay regla que lo resuelva sin criterio de la mesa.
        automatico={"sin_clase_activo":
                    "derivados C/P, futuros y OTC de agro/dólar por el "
                    "prefijo del contrato, copia de cartera (RENTA "
                    "VARIABLE/HD/DL), FCI por Primary y ARS por la curva del "
                    "master. Lo demás queda para una persona.",
                    "sin_cartera":
                    "reglas del job (pagarés, FCI, OTC/agro) + los ejes del "
                    "bono en el master o en el catálogo de 1816: "
                    "dolar_linked → DL, USD/EUR → HD, ARS → ARS. Lo demás "
                    "queda para una persona."}),

    # ── DATOS · SEGURIDAD ──────────────────────────────────────────────────
    # Lo que un job reporta sin escribir, declarado en `agente/reportes.py`
    # (§0.dd): una fila por stat. Sin arreglo: cada aviso dice qué hacer, y
    # lo que el job no corrige es porque no debe (la moneda, un conflicto).
    Habilidad(
        nombre="job_reporto", tipo="detector", dominio="DATOS",
        que_mira="lo que los jobs encontraron y no corrigieron: cada contador, con su lista",
        cada_segundos=1 * _H, ventana="siempre",
        correr=datos.job_reporto),

    # Lo que cada job trae de afuera contra lo que venía trayendo (§0.dk). Un
    # job que trae la mitad sale en verde: corrió, escribió algo. Cada job
    # declara su contador y su forma de crecer en `reportes.VOLUMENES`. Sin
    # arreglo: volver a correr, o mirar al proveedor, lo decide una persona.
    Habilidad(
        nombre="trajo_poco", tipo="detector", dominio="DATOS",
        que_mira="lo que cada job trae de afuera, contra lo que venía trayendo",
        cada_segundos=1 * _H, ventana="habil",
        correr=datos.trajo_poco,
        umbrales={"corte": 0.5, "min_corridas": 5, "minimo_referencia": 20, "ventana": 10}),

    Habilidad(
        nombre="dato_partido", tipo="detector", dominio="DATOS",
        que_mira="dos copias del mismo dato que dejaron de decir lo mismo",
        cada_segundos=_H, ventana="siempre",
        correr=datos.dato_partido,
        # Solo el duplicado que declara `arreglo_sql` tiene botón (§0.dc); el
        # que declara `arreglo_manual` sale como `copias_a_mano`, un aviso con
        # la instrucción, y `no_pude_chequear` es un aviso sobre el chequeo.
        arreglos={"copias_que_no_coinciden": "arbitrar_copia"},
        # El sujeto es UN duplicado declarado en `core/duplicados`: que las dos
        # copias se separen tres veces en un mes es justo lo que hay que ver.
        naturaleza={"copias_que_no_coinciden": INCIDENTE}),

    # LAS CONTRAPARTES QUE NO ESTÁN CARGADAS (§0.er). El conciliador de Manager
    # busca por el NOMBRE de las contrapartes que ya tenemos, así que sólo
    # encuentra más cuentas de las conocidas: medido el 2026-09-08, de 615
    # cuentas activas sin decidir encontraba CERO. Esta habilidad busca por el
    # `tipo_cliente` de Aunesa, que es la señal que ese código lee y tira.
    #
    # ⚠️ **SIN `automatico`, y no es un olvido.** Dar de alta una contraparte la
    # SACA del AuM: un robot no decide de qué cuenta deja de contarse la plata.
    Habilidad(
        nombre="contraparte_faltante", tipo="detector", dominio="DATOS",
        que_mira="cuentas institucionales activas que no están cargadas como contraparte",
        # El sync de comitentes corre 14, 17 y 21 UTC; con media hora, una cuenta
        # nueva se ve el mismo día que entra. Son dos consultas por índice sobre
        # 1.900 filas: no toca la red ni cuesta créditos.
        cada_segundos=30 * _M, ventana="siempre",
        correr=datos.contraparte_faltante,
        # Cuántas hacen falta para molestar. En 1 porque una sola contraparte sin
        # cargar ya ensucia el AuM; se sube en caliente si resulta ruidoso.
        umbrales={"min_para_avisar": 1},
        arreglos={"sin_contraparte": "alta_contraparte"},
        # RECURRENTE y no incidente (§0.ep): el sujeto es la FAMILIA «CONTRAPARTES
        # NUEVAS», una fila que se llena y se vacía. Que nazca cuatro veces por
        # mes mide cuántas cuentas institucionales abrió la mesa, no algo mal
        # configurado.
        naturaleza={"sin_contraparte": RECURRENTE}),

    Habilidad(
        nombre="permiso_flojo", tipo="detector", dominio="SEGURIDAD",
        que_mira="endpoints sin gate, y —probando de verdad— los que contestan igual",
        cada_segundos=6 * _H, ventana="siempre",
        correr=datos.permiso_flojo),
)}


def sincronizar() -> dict:
    """Deja la tabla igual al código. **El código manda sobre la identidad**
    (qué hace, de qué dominio es, su ritmo por defecto); la BASE manda sobre lo
    que se edita en caliente: `activa` y `umbrales`.

    Una habilidad que se saca del código queda `activa = false` en la tabla en
    vez de borrarse: sus hallazgos históricos la referencian.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        for h in HABILIDADES.values():
            cur.execute(
                "INSERT INTO agente.habilidades "
                " (nombre, tipo, dominio, que_mira, usa_ia, cada_segundos, "
                "  ventana, sujeto_es, umbrales) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (nombre) DO UPDATE SET "
                "  tipo = EXCLUDED.tipo, dominio = EXCLUDED.dominio, "
                "  que_mira = EXCLUDED.que_mira, usa_ia = EXCLUDED.usa_ia, "
                "  cada_segundos = EXCLUDED.cada_segundos, "
                "  ventana = EXCLUDED.ventana, sujeto_es = EXCLUDED.sujeto_es",
                (h.nombre, h.tipo, h.dominio, h.que_mira, h.usa_ia,
                 h.cada_segundos, h.ventana, h.sujeto_es,
                 __import__("json").dumps(h.umbrales)))
        cur.execute("UPDATE agente.habilidades SET activa = false "
                    "WHERE nombre <> ALL(%s)", (list(HABILIDADES),))
    return {"ok": True, "habilidades": len(HABILIDADES)}


def estado() -> list[dict]:
    """El catálogo con sus contadores, tal como lo dibuja la pantalla."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM agente.v_habilidades")
        cols = [d[0] for d in cur.description]
        filas = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    for f in filas:
        h = HABILIDADES.get(f["nombre"])
        # La CLASE de una habilidad es el resumen de sus reglas, y se DERIVA.
        f["arreglos"] = dict(h.arreglos) if h else {}
        f["automatico"] = dict(h.automatico) if h else {}
        f["naturaleza"] = dict(h.naturaleza) if h else {}
        f["clase"] = ("trabajo" if (h and h.arreglos) else "aviso")
    return filas


def _de_naturaleza(*cuales: str) -> list[tuple[str, str]]:
    """Los pares (habilidad, regla) de una o varias naturalezas. **La única
    lectura de `naturaleza`**: si cada pantalla armara la suya, el día que se
    agregue un valor una se enteraría y la otra no (REGLA #9)."""
    return [(h.nombre, r) for h in HABILIDADES.values()
            for r, nat in h.naturaleza.items() if nat in cuales]


def informes() -> list[tuple[str, str]]:
    """Las reglas que son INFORMES, no problemas (`AGENT.md` §0.eg): nacen por
    calendario y por eso aparecen todos los días."""
    return _de_naturaleza(INFORME)


def recurrentes() -> list[tuple[str, str]]:
    """Las reglas que son TRABAJO RECURRENTE por diseño (`AGENT.md` §0.ek y
    §0.ep): su sujeto es un GRUPO que se llena y se vacía —el campo de una
    ficha, la familia «ONs HARD DÓLAR»— y nace de nuevo cada vez que el
    universo crece."""
    return _de_naturaleza(RECURRENTE)


def sin_episodios() -> list[tuple[str, str]]:
    """Todo lo que NO cuenta episodios ni entra en PATRONES. Lo crónico queda
    para lo que sí es un patrón: un job, un motor, un feed, un ticker — un
    sujeto que es UNA COSA y no un grupo (`AGENT.md` §0.ep)."""
    return _de_naturaleza(*SIN_EPISODIOS)


def umbrales_de(nombre: str) -> dict:
    """Los umbrales EFECTIVOS: los del código, pisados por los de la base."""
    h = HABILIDADES.get(nombre)
    base = dict(h.umbrales) if h else {}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT umbrales FROM agente.habilidades WHERE nombre = %s",
                        (nombre,))
            f = cur.fetchone()
        if f and f[0]:
            base.update(dict(f[0]))
    except Exception as e:
        logger.warning("catalogo: sin umbrales de %s en la base (%s)", nombre, e)
    return base
