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
from agente.tipos import Habilidad
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
        arreglos={"no_esta_en_curvas": "alta_bono"}),

    Habilidad(
        nombre="bono_sin_flujo", tipo="detector", dominio="MERCADO",
        que_mira="bonos cargados sin cronograma de pagos: no valúan",
        cada_segundos=2 * _H, ventana="siempre",
        correr=mercado.bono_sin_flujo, sujeto_es="bono",
        arreglos={"sin_flujo": "alta_flujos"}),

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

    # `scripts/diag_tea_corp_hd` hecho habilidad (§0.do): nuestra TNA/TEA contra
    # la de 1816 en los corporativos hard dólar, un hallazgo por bono que se
    # aparta más de `bps`. Cuesta créditos de 1816 (tickers × 2 por corrida), por
    # eso cada 2 h como `soberanos_faltantes`. Sin arreglo: se mira el cuadro.
    Habilidad(
        nombre="tasa_vs_1816", tipo="detector", dominio="MERCADO",
        que_mira="corporativos hard dólar: nuestra TNA/TEA contra la de 1816",
        cada_segundos=2 * _H, ventana="rueda",
        correr=mercado.tasa_vs_1816, sujeto_es="bono",
        umbrales={"bps": 150.0}),

    # `soberanos_faltantes` para las ONs en dólares (§0.dp). Dos diferencias, y
    # ninguna es de gusto: Primary es CONDICIÓN (sin foto no se ofrece nada), y
    # no tiene arreglo porque la rama `on` no convierte el cuadro sola todavía —
    # un botón que siempre bloquea enseña a no apretar.
    Habilidad(
        nombre="on_faltante", tipo="detector", dominio="MERCADO",
        que_mira="ONs hard dólar que 1816 lista, Primary cotiza y no están en el master",
        cada_segundos=2 * _H, ventana="rueda",
        correr=mercado.on_faltante, sujeto_es="bono"),

    Habilidad(
        nombre="bono_sin_precio", tipo="detector", dominio="MERCADO",
        que_mira="bonos sin precio en rueda, separando las cuatro causas",
        cada_segundos=5 * _M, ventana="rueda",
        correr=mercado.bono_sin_precio, sujeto_es="bono",
        umbrales={"precio_viejo_min": 20},
        # `sin_punta` y `precio_viejo` NO tienen arreglo, y eso se DECLARA: son
        # datos sobre el papel, no sobre el sistema.
        arreglos={"no_suscripto": "pedir_pata"}),

    Habilidad(
        nombre="precio_moneda", tipo="detector", dominio="MERCADO",
        que_mira="bonos de curva USD cuyo precio llega en pesos",
        cada_segundos=5 * _M, ventana="rueda",
        correr=mercado.precio_moneda, sujeto_es="bono",
        umbrales={"paridad_min": 40, "paridad_max": 160},
        arreglos={"pata_equivocada": "apuntar_pata",
                  "cotiza_en_pesos": "pata_dolar"}),

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
        arreglos={"no_esta_en_master": "alta_cedear"}),

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
        arreglos={"job_sin_dato": "rehacer_job"}),

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
        correr=sistema.db_peso),

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
        cada_segundos=6 * _H, ventana="siempre",
        correr=cat.ficha_incompleta,
        # Las CUATRO reglas comparten arreglo: el listado editable es el mismo,
        # cambia la columna. Se declaran las cuatro igual —y no un `default`—
        # porque una regla nueva tiene que decidir explícitamente si lo tiene.
        arreglos={"sin_cartera": "completar_ficha",
                  "sin_clase_activo": "completar_ficha",
                  "sin_emisor": "completar_ficha",
                  "fci_sin_ticker": "completar_ficha"}),

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
        arreglos={"copias_que_no_coinciden": "arbitrar_copia"}),

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
        f["clase"] = ("trabajo" if (h and h.arreglos) else "aviso")
    return filas


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
