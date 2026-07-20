"""copiloto/ayuda.py — el GUÍA de la plataforma (pedido user 2026-07-20).

Asistente de navegación estilo DigitalOcean/Supabase: NO habla de datos, ni de
números, ni de mercado, ni del negocio — SOLO te lleva a donde querés ir
("¿dónde veo cuánto operó una cuenta?" → pasos concretos de menú/vista/filtro).

La "tabla" es el MAPA DEL PRODUCTO (curado a mano acá, lenguaje de negocio,
jamás nombres internos de sistemas). Al agregar/mover una vista en el frontend,
actualizar este mapa — es parte del contrato de doc vivo (docs/COPILOTO.md).

Se muestra en el header en TODAS las páginas que no tienen copiloto de datos
propio (reemplaza el slot vacío); los copilotos de datos pueden derivar acá
vía [[VISTA:ayuda]] cuando la pregunta es "cómo llego / dónde veo".
"""
from __future__ import annotations

import logging

from api.cache import cached

logger = logging.getLogger(__name__)

# Una fila por lugar navegable. `permiso` en lenguaje de negocio (los módulos
# los asigna el admin en Manager → Roles y Permisos).
_MAPA = [
    # ── vistas principales ──
    {"seccion": "Home", "ruta": "/", "menu": "HOME",
     "que_hay": "el panorama del día: watchlist de mercado (índices, dólares MEP/CCL/oficial, "
                "riesgo país, CUÁNTO PAGAN LAS CAUCIONES hoy, y los FUTUROS DE DÓLAR con su "
                "devaluación implícita), briefing de apertura de las 10:00, noticias y "
                "calendario económico",
     "permiso": "todos"},
    {"seccion": "Operar", "ruta": "/operar", "menu": "OPERAR",
     "que_hay": "operatoria de Dólar MEP: envío de órdenes, órdenes vivas, saldo por cuenta "
                "y brackets; también suscripción/rescate de FCI",
     "permiso": "restringido (módulo Operar)"},
    {"seccion": "Trading", "ruta": "/trading", "menu": "TRADING",
     "que_hay": "monitor intradía del trader: tarjetas con pivots por papel, chart en vivo, "
                "libro y time & sales, radar de movers; sub-pestañas INTRADAY (posiciones del "
                "día) y PNL HISTÓRICO (carga manual del resultado diario)",
     "permiso": "restringido (módulo Trading)"},
    {"seccion": "Research", "ruta": "/research", "menu": "RESEARCH",
     "que_hay": "research de mercado en 5 pestañas: RENTA FIJA ARGENTINA (series y spreads de "
                "bonos + los mails diarios de research), REPORTES FINANCIEROS (PDFs cargados "
                "por el equipo), BCRA (variables monetarias), DATOS INTERNACIONALES (tasas "
                "USA, commodities, liquidez) y RENTA VARIABLE INTERNACIONAL (acciones US en "
                "vivo)",
     "permiso": "toda la mesa"},
    # ── menú MERCADOS ──
    {"seccion": "Agro", "ruta": "/agro", "menu": "MERCADOS → Agro",
     "que_hay": "granos: pizarras y cámaras, futuros y opciones agro, cauciones/pases CON "
                "COBERTURA del agro (la tasa de caución general está en HOME), dólares del "
                "agro y volumen del mercado",
     "permiso": "toda la mesa"},
    {"seccion": "Derivados", "ruta": "/derivados", "menu": "MERCADOS → Derivados",
     "que_hay": "opciones financieras: tablero de opciones por papel, matriz de vencimientos, "
                "volatilidad y análisis",
     "permiso": "toda la mesa"},
    {"seccion": "Estrategia", "ruta": "/retorno", "menu": "MERCADOS → Estrategia",
     "que_hay": "análisis de retorno total de bonos, carry trade y canje (retorno/carry por "
                "bono contra dólares futuros)",
     "permiso": "toda la mesa"},
    {"seccion": "ONs", "ruta": "/ons", "menu": "MERCADOS → ONs",
     "que_hay": "obligaciones negociables por sector, con rendimiento y ficha por título",
     "permiso": "toda la mesa"},
    {"seccion": "Renta Fija", "ruta": "/renta-fija", "menu": "MERCADOS → Renta Fija",
     "que_hay": "las curvas de bonos (tasa fija, CER, soberanos en dólares), breakevens de "
                "inflación, tasas forward y fair value",
     "permiso": "toda la mesa"},
    {"seccion": "Renta Variable", "ruta": "/renta-variable", "menu": "MERCADOS → Renta Variable",
     "que_hay": "el scanner de CEDEARs: precios ARS y USD del subyacente, variaciones, "
                "liquidez, retornos por período y zonas de pivots",
     "permiso": "toda la mesa"},
    {"seccion": "Sintéticos", "ruta": "/sinteticos", "menu": "MERCADOS → Sintéticos",
     "que_hay": "tasas sintéticas armadas con futuros de dólar (colocador/tomador) y su "
                "comparación con las tasas de mercado",
     "permiso": "toda la mesa"},
    # ── menú NEGOCIO ──
    {"seccion": "AUM", "ruta": "/aum", "menu": "NEGOCIO → AUM",
     "que_hay": "los activos administrados: total valorizado, evolución diaria y apertura por "
                "cartera / tipo de activo / cuenta",
     "permiso": "restringido (módulo Portfolios)"},
    {"seccion": "Carteras", "ruta": "/valuaciones", "menu": "NEGOCIO → Carteras",
     "que_hay": "la cartera de cada cuenta: posiciones valorizadas, resultado por título "
                "(realizado, no realizado y cupones/rentas) y reportes por cliente",
     "permiso": "restringido (módulo Portfolios)"},
    {"seccion": "Contrapartes", "ruta": "/contrapartes", "menu": "NEGOCIO → Contrapartes",
     "que_hay": "el flujo operado contra cada contraparte del mercado",
     "permiso": "restringido (módulo Operaciones)"},
    {"seccion": "Operaciones", "ruta": "/operaciones", "menu": "NEGOCIO → Operaciones",
     "que_hay": "TODO lo operado: MOVIMIENTOS por día/mes con volumen y aranceles — acepta "
                "RANGO DE FECHAS y se filtra por moneda (ARS/USD), mercado, segmento (nivel "
                "1), segmento del boleto (nivel 3), tipo de operación, cuenta (buscador), "
                "operador, y ver solo/sin las cuentas propias de ACA VALORES; además la "
                "vista NEGOCIO (posiciones y resultado por cliente), el TABLERO COMERCIAL "
                "(estado de actividad de cada cuenta y su operador) y la pestaña AGRO "
                "(share de mercado). Los VALORES vigentes de cada filtro están en el bloque "
                "[filtros de Operaciones]",
     "permiso": "restringido (módulo Operaciones)"},
    {"seccion": "Operadores", "ruta": "/operadores", "menu": "NEGOCIO → Operadores",
     "que_hay": "el tablero por operador comercial: sus cuentas, actividad y objetivos",
     "permiso": "restringido (módulo Operaciones)"},
    {"seccion": "Referidos", "ruta": "/referidos", "menu": "NEGOCIO → Referidos",
     "que_hay": "las comisiones por saldos de FCI referidos (por gerente/cooperativa)",
     "permiso": "restringido (módulo Operaciones)"},
    # ── back office y administración ──
    {"seccion": "Back Office", "ruta": "/back-office", "menu": "BACK OFFICE",
     "que_hay": "acreencias (cupones, rentas y amortizaciones a cobrar por fecha), tesorería "
                "(ingresos y egresos del día) y tenencia valorizada histórica",
     "permiso": "restringido (módulo Back Office)"},
    {"seccion": "Manager", "ruta": "/manager", "menu": "MANAGER",
     "que_hay": "administración de la plataforma: usuarios y permisos (roles), altas y "
                "gestión de títulos, grupos de cuentas, clientes, diagnóstico y salud del "
                "sistema, observabilidad (incluida la IA) y documentos",
     "permiso": "solo administradores"},
    # ── recetas frecuentes (atajos que el guía puede recomendar directo) ──
    {"seccion": "RECETA: cuánto operó una cuenta", "ruta": "/operaciones",
     "menu": "NEGOCIO → Operaciones",
     "que_hay": "en MOVIMIENTOS elegís el rango de fechas y filtrás por la cuenta: ves "
                "volumen bruto y aranceles por día y por tipo de operación",
     "permiso": "restringido (módulo Operaciones)"},
    {"seccion": "RECETA: ver la cartera de un cliente", "ruta": "/valuaciones",
     "menu": "NEGOCIO → Carteras",
     "que_hay": "buscás la cuenta y ves posiciones valorizadas y el resultado por título; "
                "para el total administrado de todos, AUM",
     "permiso": "restringido (módulo Portfolios)"},
    {"seccion": "RECETA: seguir un bono / su rendimiento", "ruta": "/renta-fija",
     "menu": "MERCADOS → Renta Fija",
     "que_hay": "elegís la curva y el bono; para series históricas largas y spreads entre "
                "bonos, RESEARCH → RENTA FIJA ARGENTINA",
     "permiso": "toda la mesa"},
    {"seccion": "RECETA: qué dijo el research de 1816", "ruta": "/research",
     "menu": "RESEARCH → RENTA FIJA ARGENTINA",
     "que_hay": "los mails diarios están en el panel de reportes (acordeón, buscables); "
                "también podés preguntarle al copiloto de esa vista, que los cita con fecha",
     "permiso": "toda la mesa"},
    {"seccion": "RECETA: pedir un permiso que no tenés", "ruta": "/manager",
     "menu": "hablar con el administrador",
     "que_hay": "los permisos por vista los asigna el administrador en MANAGER → Roles y "
                "Permisos; si no ves una sección del menú, es porque tu rol no la tiene",
     "permiso": "—"},
]


def _fetch_ayuda(params: dict | None = None) -> list[dict]:
    return list(_MAPA)


@cached(ttl=3600)
def _filtros_operaciones() -> list[str]:
    """Los VALORES vigentes de los filtros de NEGOCIO → Operaciones, leídos en
    vivo de los mismos catálogos que usa la vista (pedido user 2026-07-20: el
    guía tiene que saber qué valores tiene cada filtro — ej. los mercados y
    segmentos disponibles). Cache 1h: los catálogos casi no cambian y el guía
    vive en todas las páginas. Best-effort: sin DB, el bloque no aparece y el
    guía describe los filtros sin valores."""
    from api.services import operaciones_sql

    partes = []
    try:
        m = (operaciones_sql.ops_mercados() or {}).get("mercados") or []
        if m:
            partes.append("mercado: " + ", ".join(m))
    except Exception as e:
        logger.warning("guía: mercados no disponibles (%s)", e)
    try:
        s = (operaciones_sql.ops_segmentos() or {}).get("segmentos") or []
        if s:
            partes.append("segmento (nivel 1): " + ", ".join(s))
    except Exception as e:
        logger.warning("guía: segmentos no disponibles (%s)", e)
    try:
        n3 = (operaciones_sql.ops_niveles3() or {}).get("niveles3") or []
        if n3:
            partes.append("segmento del boleto (nivel 3): " + ", ".join(n3))
    except Exception as e:
        logger.warning("guía: niveles3 no disponibles (%s)", e)
    if not partes:
        return []
    return ["[filtros de Operaciones — valores vigentes en los selectores de MOVIMIENTOS] "
            + " · ".join(partes)
            + " · moneda: ARS, USD · más: rango de fechas, tipo de operación, cuenta "
              "(buscador), operador, solo/sin cuentas ACA VALORES"]


def _extras_ayuda(
    filas: list[dict], pregunta: str, historial: list[dict], params: dict | None = None
) -> list[str]:
    return _filtros_operaciones()


_COLUMNAS_AYUDA = [
    ("seccion", "sección"), ("menu", "cómo llegar (menú)"), ("ruta", "ruta"),
    ("que_hay", "qué hay ahí"), ("permiso", "quién la ve"),
]

_REGLAS_AYUDA = """Sos el GUÍA de la plataforma — el asistente de navegación, como los de \
las páginas de documentación. Tu ÚNICO trabajo es llevar al usuario a donde quiere ir. La \
tabla es el mapa del producto: secciones, cómo llegar por el menú, qué hay en cada una y \
quién puede verla.

PROHIBICIÓN TOTAL (tu regla más importante): NO hablás de datos. Ni un precio, ni un \
número de mercado, ni información de cuentas o clientes, ni opiniones financieras, ni \
recomendaciones de inversión. Si te preguntan "¿cuánto operó la cuenta X?" NO respondés el \
dato (no lo tenés ni te importa): respondés CÓMO verlo — a qué vista ir, qué filtrar y qué \
van a encontrar. Si insisten por el dato, repetís amablemente que sos el guía y que el \
dato está en la vista.

Cómo respondés:
- Pasos concretos y cortos, en orden: menú → sección → pestaña → filtro. Ej: "1. Andá a \
NEGOCIO → Operaciones. 2. Elegí el rango de fechas. 3. Filtrá por la cuenta."
- NO inventes detalles visuales de la interfaz (posición de menús, íconos, "a la \
izquierda/derecha"): el menú de navegación es la BARRA SUPERIOR y eso es todo lo que \
afirmás del layout. Lo demás sale del mapa, no de tu imaginación.
- El bloque [filtros de Operaciones] trae los VALORES vigentes de los selectores de esa \
vista (mercados, segmentos, niveles): cuando pregunten "¿cuánto se operó en X?" o por un \
segmento, citá el valor exacto del filtro que corresponde ("filtrá mercado = BYMA"). Si \
un valor no está en ese bloque, no existe como filtro — no lo inventes.
- Si la vista tiene su propio asistente de datos (Home, Renta Fija, Renta Variable, Agro, \
Derivados, ONs, Trading, Research), avisá: "ahí arriba tenés el botón «Consultale a la IA» \
para preguntarle sobre esos datos".
- Si la sección es restringida, decilo sin drama: "esa vista requiere el permiso X — lo \
asigna el administrador en Manager".
- Si lo que piden NO existe en la plataforma, decilo derecho y ofrecé lo más parecido del \
mapa. No inventes vistas ni funcionalidades que no están en la tabla.
- Si la pregunta ES de datos/análisis y corresponde a una vista con asistente, derivá con \
[[VISTA:x]] como siempre.
- Tono: servicial y directo, 2-6 líneas. Nada de jerga técnica interna."""

_CHIPS_AYUDA = [
    {"label": "¿Qué puedo hacer acá?",
     "pregunta": "Dame un tour rápido: ¿qué secciones tiene la plataforma y para qué "
                 "sirve cada una?"},
    {"label": "¿Dónde veo lo operado?",
     "pregunta": "¿Dónde veo cuánto operó una cuenta y qué aranceles generó? Pasos "
                 "concretos."},
    {"label": "¿Dónde veo una cartera?",
     "pregunta": "¿Dónde veo la cartera valorizada de un cliente y su resultado? Pasos "
                 "concretos."},
    {"label": "No veo una sección",
     "pregunta": "En mi menú no aparece una sección que necesito, ¿por qué puede ser y "
                 "qué hago?"},
]
