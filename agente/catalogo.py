"""`agente/catalogo.py` — **LAS HABILIDADES.** Doc: `docs/AGENT_2.0.md` §3.

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
        correr=mercado.soberanos_faltantes,
        arreglos={"no_esta_en_curvas": "alta_bono"}),

    Habilidad(
        nombre="bono_sin_flujo", tipo="detector", dominio="MERCADO",
        que_mira="bonos cargados sin cronograma de pagos: no valúan",
        cada_segundos=2 * _H, ventana="siempre",
        correr=mercado.bono_sin_flujo,
        arreglos={"sin_flujo": "alta_flujos"}),

    # ⚠️ Reemplaza a `tasa_sospechosa`, que metía SEIS reglas bajo un nombre.
    # Es un AVISO a propósito: el agujero lo tapa el sistema solo (la lista de
    # prioridad le pide la tasa a 1816 cada 15 min), no una persona apretando.
    Habilidad(
        nombre="bono_sin_tasa", tipo="detector", dominio="MERCADO",
        que_mira="bonos que operan y a los que el motor no les calcula la TEA",
        cada_segundos=15 * _M, ventana="rueda",
        correr=mercado.bono_sin_tasa),

    # ⚠️ **LA ÚNICA CON VENTANA `cierre`.** Corre UNA vez, 17:30 ART, con la
    # rueda cerrada: lo que se le pide al mercado deja de pedirse a las 17.
    Habilidad(
        nombre="tasas_al_cierre", tipo="detector", dominio="MERCADO",
        que_mira="rellena con 1816 lo que quedó sin tasa, y canta lo que ni así",
        cada_segundos=12 * _H, ventana="cierre",
        correr=mercado.tasas_al_cierre),

    Habilidad(
        nombre="bono_sin_precio", tipo="detector", dominio="MERCADO",
        que_mira="bonos sin precio en rueda, separando las cuatro causas",
        cada_segundos=5 * _M, ventana="rueda",
        correr=mercado.bono_sin_precio,
        umbrales={"precio_viejo_min": 20},
        # `sin_punta` y `precio_viejo` NO tienen arreglo, y eso se DECLARA: son
        # datos sobre el papel, no sobre el sistema.
        arreglos={"no_suscripto": "pedir_pata"}),

    Habilidad(
        nombre="precio_moneda", tipo="detector", dominio="MERCADO",
        que_mira="bonos de curva USD cuyo precio llega en pesos",
        cada_segundos=5 * _M, ventana="rueda",
        correr=mercado.precio_moneda,
        umbrales={"paridad_min": 40, "paridad_max": 160},
        arreglos={"pata_equivocada": "apuntar_pata",
                  "cotiza_en_pesos": "pata_dolar"}),

    Habilidad(
        nombre="hueco_de_curva", tipo="detector", dominio="MERCADO",
        que_mira="ajustes que existen en el master y que la app no sabe mostrar",
        cada_segundos=6 * _H, ventana="siempre",
        correr=mercado.hueco_de_curva),

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

    Habilidad(
        nombre="tabla_quieta", tipo="detector", dominio="SISTEMA",
        que_mira="tablas que dejaron de escribir — la cadencia se MIDE, no se declara",
        cada_segundos=30 * _M, ventana="siempre",
        correr=sistema.tabla_quieta),

    Habilidad(
        nombre="cron_desalineado", tipo="detector", dominio="SISTEMA",
        que_mira="el crontab del repo contra el de la máquina, en las dos direcciones",
        cada_segundos=30 * _M, ventana="siempre",
        correr=sistema.cron_desalineado),

    Habilidad(
        nombre="latencia", tipo="detector", dominio="SISTEMA",
        que_mira="endpoints degradados contra SU PROPIA normalidad, y los 5xx",
        cada_segundos=10 * _M, ventana="siempre",
        correr=sistema.latencia),

    Habilidad(
        nombre="proveedor_caido", tipo="detector", dominio="SISTEMA",
        que_mira="Aunesa, 1816, Interbanking y BCRA, por el rastro de las llamadas reales",
        cada_segundos=5 * _M, ventana="siempre",
        correr=sistema.proveedor_caido,
        umbrales={"ventana_s": 1200, "minimo_fallos": 1}),

    Habilidad(
        nombre="db_peso", tipo="detector", dominio="SISTEMA",
        que_mira="tablas que crecieron fuera de lo suyo, medido en vivo",
        cada_segundos=_H, ventana="siempre",
        correr=sistema.db_peso),

    Habilidad(
        nombre="actividad", tipo="detector", dominio="SISTEMA",
        que_mira="escrituras de mercado en día NO hábil: algo quedó prendido",
        cada_segundos=15 * _M, ventana="siempre",
        correr=sistema.actividad),

    # ── DATOS · SEGURIDAD ──────────────────────────────────────────────────
    Habilidad(
        nombre="dato_partido", tipo="detector", dominio="DATOS",
        que_mira="dos copias del mismo dato que dejaron de decir lo mismo",
        cada_segundos=_H, ventana="siempre",
        correr=datos.dato_partido),

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
                "  ventana, umbrales) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (nombre) DO UPDATE SET "
                "  tipo = EXCLUDED.tipo, dominio = EXCLUDED.dominio, "
                "  que_mira = EXCLUDED.que_mira, usa_ia = EXCLUDED.usa_ia, "
                "  cada_segundos = EXCLUDED.cada_segundos, "
                "  ventana = EXCLUDED.ventana",
                (h.nombre, h.tipo, h.dominio, h.que_mira, h.usa_ia,
                 h.cada_segundos, h.ventana,
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
