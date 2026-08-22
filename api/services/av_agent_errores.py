"""api/services/av_agent_errores.py — TRADUCIR UN ERROR A CASTELLANO.

Doc madre: **`docs/AV_AGENT.md`** §0.ba.

EL PEDIDO
=========

El user (2026-08-21), mirando tres filas de motores en la pantalla:

    motor_curvas: <fecha>,<n> ERROR pg_mirror pg_mirror market_snapshot: de…

    *«Es imposible entender qué es el error, qué está pasando o qué pasó, si
    sigue pasando. Está el mensaje cortado, aparte todo súper técnico, no se
    entiende a qué está afectando de la app. Poner una línea de código y decir
    que no anda es inentendible.»*

Y la segunda mitad, que es la que define el diseño:

    *«Los motores y los jobs hacen cosas LINEALES, no son a interpretación.
    Siempre tienen que estar analizados.»*

Tiene razón en las dos. De un error de NUESTRO sistema hay que poder decir tres
cosas sin apretar ningún botón:

    QUÉ PASÓ       en castellano, no la línea del log
    A QUÉ AFECTA   qué pantalla o qué número queda mal por esto
    SI SIGUE       terminó, o está pasando ahora

LA REGLA PRIMERO, LA IA DESPUÉS
===============================

`FIRMAS` traduce lo conocido **sin gastar un token y sin poder alucinar**: son
nuestros propios motores y jobs, o sea un conjunto finito de formas de fallar.
Un `ModuleNotFoundError` significa siempre lo mismo.

La IA entra **solo donde la regla no supo** (el user: *«acá es donde hay que
meter un LLM que explique qué es el error y a qué afectó»*), y con dos guardas
que la hacen barata y segura:

  1. **Se explica el PATRÓN, no la fila.** El mismo error aparece 90 veces en
     6 horas; pagar una llamada por aparición sería absurdo. Se explica una vez
     por `(unidad, patrón)` y se **persiste** en `agente.av_agent_errores`: la
     segunda vez sale de la base. Es la misma idea que agrupar el log.
  2. **La IA NO decide a qué afecta.** Eso sale de la ficha declarada
     (`salud.JOBS[...]['alimenta']`, `QUE_HACE`), que es un hecho del sistema.
     Al modelo se le pide solo la traducción del error — inventar consecuencias
     es exactamente lo que haría desconfiar de todo el resto.

Si la IA no está disponible, se degrada a la línea cruda: **el texto feo es
mejor que ningún texto.**
"""
from __future__ import annotations

import hashlib
import logging
import re

logger = logging.getLogger(__name__)

# ── QUÉ HACE CADA MOTOR, en una línea ────────────────────────────────────────
#
# El equivalente de `salud.JOBS[...]['alimenta']` para los motores. Se declara
# porque es criterio (qué PANTALLA se rompe), no algo que se pueda derivar del
# nombre del módulo. Lo que sí se deriva —qué TABLAS escribe— sale de
# `core/escribe` y se usa de respaldo.
QUE_HACE: dict[str, str] = {
    "motor_curvas": "calcula la TEA, la paridad y la duration de cada bono → la "
                    "tabla de RENTA FIJA",
    "motor_rofex": "pide los precios a Primary y los escribe → **todos** los "
                   "precios en vivo de la app",
    "motor_cedears": "precios de CEDEARs y ADRs → el Scanner de RENTA VARIABLE",
    "motor_options": "griegas y volatilidad de opciones → la vista DERIVADOS",
    "motor_portfolio_snapshot": "valoriza las tenencias con el precio del "
                                "momento → PORTFOLIOS y el AuM en vivo",
    "motor_agro": "pizarra y precios agro → la vista AGRO",
    "motor_agro_opciones": "opciones agro → la vista AGRO",
    "motor_dolares": "MEP y CCL en vivo → el dólar de toda la app",
    "motor_caucion": "curva de cauciones → la vista de tasas",
    "motor_futuros_dlr": "futuros de dólar → DERIVADOS y el cálculo de forwards",
    "motor_ordenes": "manda y cancela órdenes → la vista OPERAR",
    "motor_estrategia": "la señal intradía → la tab ESTRATEGIA",
}


# ── FIRMAS CONOCIDAS ─────────────────────────────────────────────────────────
#
# `(patrones, qué pasó, hay que actuar)`. El orden importa: gana la primera que
# matchea, así que lo específico va antes que lo genérico.
#
# ⚠️ **`urgente` NO es la severidad del log.** Un WARNING repetido 90 veces
# porque el catálogo tiene opciones vencidas es ruido que se limpia cuando se
# pueda; un ERROR de escritura es plata que no se está guardando. Lo que decide
# es la CONSECUENCIA, no el nivel con que el programador lo escribió.
FIRMAS: tuple[tuple[tuple[str, ...], str, str, bool], ...] = (
    (("modulenotfounderror", "no module named"),
     "el job no arranca: le falta un módulo de Python",
     "se borró o se renombró algo que importaba. **No escribió nada** — no es "
     "que haya escrito a medias", True),
    (("importerror", "cannot import name"),
     "el job no arranca: un import quedó roto",
     "apunta a algo que ya no existe. **No escribió nada**", True),
    (("pg_mirror", "deadlock", "could not serialize"),
     "no pudo guardar en la base lo que calculó",
     "el dato se calculó bien y se perdió en la escritura", True),
    (("psycopg", "operationalerror", "connection to server", "pool"),
     "se cortó la conexión con la base",
     "mientras dure, nada de lo que produce se está guardando", True),
    (("ws ", "websocket", "reconnect", "reconectando"),
     "se cayó la conexión con el feed y está reintentando",
     "mientras tanto los precios de esa pantalla no se actualizan", True),
    (("429", "demasiadas solicitudes", "rate limit", "quota"),
     "el proveedor nos frenó por exceso de pedidos",
     "hay que espaciarlos: insistir empeora el bloqueo", True),
    (("401", "403", "unauthorized", "forbidden", "token", "credential"),
     "el proveedor rechazó la credencial",
     "el token venció o cambió: hasta renovarlo no entra un dato", True),
    (("timeout", "timed out"),
     "el proveedor no contestó a tiempo",
     "puede ser un pico de ellos: si no se repite, no hay nada que hacer", False),
    (("connectionerror", "unreachable", "dns", "name resolution"),
     "no se pudo llegar al proveedor",
     "es red, no es nuestro código", False),
    (("no space left", "disk full"),
     "se llenó el disco del Droplet",
     "**nada puede escribir hasta liberar espacio**", True),
    (("killed", "oom", "out of memory"),
     "el proceso murió por falta de memoria",
     "quedó cortado a la mitad: lo que no alcanzó a escribir se perdió", True),
    (("expiries", "vencida", "expirada"),
     "el catálogo tiene contratos ya vencidos",
     "no rompe nada: ensucia el log y gasta pedidos al pedo", False),
    (("sin trade", "sin punta", "no operó"),
     "un instrumento no operó ese día",
     "no hay precio de cierre para guardar. **No es un error del sistema**: es "
     "el mercado", False),
    (("keyerror", "typeerror", "attributeerror", "valueerror", "nonetype"),
     "un dato vino con otra forma y el código se cortó ahí",
     "algo cambió del lado de la fuente y nadie lo esperaba", True),
)


def por_regla(texto: str) -> tuple[str, str, bool] | None:
    """`(qué pasó, el matiz, hay que actuar)`. `None` = no la reconozco.

    ⚠️ **El `pasa` es CORTO a propósito**: va al título de la fila, que tiene 88
    caracteres contando el nombre del motor, la cuenta y la hora. Una frase de
    110 se corta con «…» y volvemos al problema original. El matiz va al cuerpo,
    donde sí hay lugar.
    """
    t = (texto or "").lower()
    for patrones, que_paso, mas, urge in FIRMAS:
        if any(p in t for p in patrones):
            return que_paso, mas, urge
    return None


def _modulos_del_label(label: str) -> list[str]:
    """`portafolio_diario` → `['portafolio_backfill']`, leyendo el crontab."""
    try:
        from api.services import jobs_catalogo as cat
        for c in cat._parse_crontab():
            if c["label"] == label:
                return [m.split(".")[-1] for m in c["modules"]]
    except Exception:
        pass
    return []


def a_que_afecta(unidad: str) -> str:
    """Qué se rompe en la app porque esta pieza falló.

    **Es un hecho declarado, no una opinión del modelo.** Sale de la ficha del
    job (`salud.JOBS`) o del motor (`QUE_HACE`), y de último recurso de las
    tablas que ese módulo escribe (derivadas de `core/escribe`).
    """
    u = (unidad or "").strip()
    if u in QUE_HACE:
        return QUE_HACE[u]
    corto = u.replace("motor_", "").replace("jobs.", "")
    try:
        from api.services.av_agent_salud import JOBS
        # ⚠️ **EL LABEL DEL CRON NO ES EL NOMBRE DEL MÓDULO.** El chequeo se
        # llama `portafolio_diario` y la ficha vive bajo `portafolio_backfill`:
        # sin resolverlo, el job MÁS caro de perder (el AuM entero) salía sin
        # decir a qué afecta. La resolución ya existe en `jobs_catalogo`, que
        # lee el crontab — se DELEGA, no se copia (REGLA #9).
        for clave in (u, corto, *_modulos_del_label(u)):
            f = JOBS.get(clave)
            if f and f.get("alimenta"):
                return str(f["alimenta"])
    except Exception:                       # nunca rompe por la ficha
        pass
    try:
        from core import escribe
        tablas = [t for t, mods in escribe._mapa().items()
                  if any(u in m or corto in m for m in mods)][:3]
        if tablas:
            return "escribe " + ", ".join(f"`{t}`" for t in tablas)
    except Exception:
        pass
    return ""


# ── LA MEMORIA: una explicación por patrón, no por fila ─────────────────────

def _clave(unidad: str, patron: str) -> str:
    return hashlib.sha256(f"{unidad}|{patron}".encode()).hexdigest()[:32]


def _guardada(clave: str) -> str:
    from core.postgres import get_pool
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT explicacion FROM agente.av_agent_errores "
                        "WHERE clave = %s", (clave,))
            f = cur.fetchone()
            return (f[0] or "") if f else ""
    except Exception as e:
        logger.debug("av_agent_errores: no pude leer la memoria (%s)", e)
        return ""


def _guardar(clave: str, unidad: str, patron: str, explicacion: str) -> None:
    from core.postgres import get_pool
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agente.av_agent_errores "
                "(clave, unidad, patron, explicacion, creado_at) "
                "VALUES (%s, %s, %s, %s, now()) "
                "ON CONFLICT (clave) DO UPDATE SET explicacion = EXCLUDED.explicacion",
                (clave, unidad[:80], patron[:400], explicacion[:400]))
    except Exception as e:
        logger.debug("av_agent_errores: no pude guardar la memoria (%s)", e)


_TAREA = "av_agent_error"

_SYSTEM = (
    "Sos el que traduce errores técnicos para una mesa de trading argentina. "
    "Te paso UNA línea de log de un sistema propio y tenés que decir QUÉ PASÓ "
    "en castellano llano, para alguien que NO programa.\n\n"
    "REGLAS:\n"
    "- UNA sola oración, máximo 20 palabras. Sin punto final.\n"
    "- Nada de nombres de clases, de archivos ni de excepciones de Python.\n"
    "- NO inventes a qué afecta ni qué hay que hacer: eso lo sabe el sistema, "
    "no vos. Limitate a decir qué falló.\n"
    "- Si de la línea no se entiende nada, contestá exactamente: NO SE ENTIENDE"
)


def _con_ia(unidad: str, muestra: str) -> str:
    from core import ai
    if not ai.disponible(_TAREA):
        return ""
    txt = (ai.completar(
        _TAREA, system=_SYSTEM,
        user=f"Componente: {unidad}\nLínea del log:\n{muestra[:600]}",
        detalle=f"traducir error de {unidad}") or "").strip()
    txt = re.sub(r"\s+", " ", txt).strip(" .")
    if not txt or "NO SE ENTIENDE" in txt.upper() or len(txt) > 240:
        return ""
    return txt


def explicar(unidad: str, patron: str, muestra: str = "") -> dict:
    """QUÉ PASÓ + A QUÉ AFECTA, ya resuelto. **Nunca levanta.**

    `fuente` dice de dónde salió cada explicación (`regla` · `ia` · `crudo`),
    porque una traducción de un modelo y un hecho declarado no valen lo mismo y
    la pantalla tiene que poder decirlo.
    """
    afecta = a_que_afecta(unidad)
    reglado = por_regla(f"{patron} {muestra}")
    if reglado:
        return {"pasa": reglado[0], "mas": reglado[1], "afecta": afecta,
                "urgente": reglado[2], "fuente": "regla"}

    clave = _clave(unidad, patron)
    guardado = _guardada(clave)
    if guardado:
        return {"pasa": guardado, "mas": "", "afecta": afecta, "urgente": True,
                "fuente": "ia"}

    texto = ""
    try:
        texto = _con_ia(unidad, muestra or patron)
    except Exception as e:                  # el traductor nunca rompe el aviso
        logger.warning("av_agent_errores: la IA falló (%s)", e)
    if texto:
        _guardar(clave, unidad, patron, texto)
        return {"pasa": texto, "mas": "", "afecta": afecta, "urgente": True,
                "fuente": "ia"}

    # ⚠️ **Degradación elegida**: sin traducción se muestra la línea cruda. Un
    # texto feo es mejor que ningún texto — y `fuente='crudo'` deja ver cuántos
    # patrones todavía no sabemos explicar, que es la lista de lo que falta.
    return {"pasa": (patron or "").strip()[:160], "mas": "", "afecta": afecta,
            "urgente": True, "fuente": "crudo"}
