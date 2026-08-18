"""api/services/av_agent_salud.py — SALUD, razonada por el AV AGENT.

Doc madre: **`docs/AV_AGENT.md`** (el doc único del programa de IA).

**Por qué existe** (user, 2026-08-17): *«quiero que el agente abarque tareas de
SALUD y que, así como simulamos y hacemos cosas de bonos, también aprenda a
resolver»*. Y antes que eso: *«no hay que tener muchos documentos y cosas
desparramadas»* — el AV Agent **es** el proyecto de IA, y SALUD entra adentro en
vez de vivir en su propia isla.

**Un chequeo y un hallazgo son el mismo objeto.** Los dos son algo que se evalúa,
tiene estado, guarda la evidencia congelada y le pide una decisión a alguien; lo
único distinto es el sujeto (un bono o un job). Tenerlos separados obligaba a
mirar dos pantallas para contestar UNA pregunta —«¿está sano el sistema?»— y
ninguna de las dos la contestaba entera.

**Cada módulo aporta lo que al otro le falta**, y por eso la fusión no es
cosmética:

    AV AGENT  →  razonamiento DETERMINISTA (las lentes), y sabe arreglar
    SALUD     →  el HISTORIAL de cada chequeo, y un diagnóstico con IA

Acá se juntan con la regla que ordena todo el programa: **el LLM va donde hay
ambigüedad y lenguaje; nunca donde hay aritmética.** Siete lentes deterministas
contestan solas y gratis; la octava —leer el log y opinar— es la única que gasta
tokens, y solo si las otras no alcanzaron.

⚠️ **ESTE MÓDULO NO ESCRIBE NADA.** Ni en SALUD ni en ningún lado. SALUD sigue
siendo el dueño de la evaluación y acá se la lee: si el agente escribiera su
propio estado habría dos verdades sobre si el sistema está sano, que es
exactamente el problema que la fusión viene a eliminar. Los ARREGLOS (relanzar un
job, correr un backfill) son el paso siguiente y se habilitan de a uno, cuando el
eval set diga que el diagnóstico acierta — el mismo camino que hizo la puerta de
bonos: primero ver, después simular, después escribir.
"""
from __future__ import annotations

import logging

# El vocabulario de pasos se IMPORTA, no se copia: es el mismo contrato que ya
# dibuja el modal (cinco estados, `frena`/`frena_auto` resueltos en el backend) y
# dos definiciones terminarían divergiendo — que es el bug que este agente ya se
# comió tres veces. Que SALUD y los bonos se vean IGUAL en la pantalla no es
# estética: es lo que hace que una sola cabeza pueda leer las dos cosas.
from api.services.av_agent_alta import (
    INFO,
    NO_SE,
    OK,
    REVISAR,
    _paso,
    _veredicto,
)

logger = logging.getLogger(__name__)

# Cuántas veces tiene que haber aparecido un chequeo para llamarlo RECURRENTE. Un
# problema que vuelve no se arregla igual que uno nuevo: el nuevo se atiende, el
# que vuelve se investiga.
_RECURRENTE = 3


def _lente_que_es(c: dict) -> dict:
    familia = c.get("familia") or "?"
    que = {"job": "un CRON: corre solo y deja un dato",
           "dato": "un CONTRATO DE FRESCURA: una tabla que tiene que estar al día",
           "control": "un CONTROL DE DATOS: un invariante que se verifica"}
    return _paso("que_es", "Qué es este chequeo", INFO,
                 f"**{c.get('titulo')}** — {que.get(familia, familia)}."
                 + (f" Cron `{c.get('schedule')}`." if c.get("schedule") else "")
                 + (f" Tabla `{c.get('tabla')}`." if c.get("tabla") else "")
                 + (f" Módulos: {', '.join(c.get('modulos') or [])}."
                    if c.get("modulos") else ""),
                 tabla="manager.job_runs · deploy/crontab.txt")


def _lente_corrio(c: dict) -> dict:
    """La resta que nadie hacía: ¿corrió CUANDO DEBÍA? Es la pregunta que dejó
    pasar 48 horas de backfill muerto con la card de AuM en verde."""
    esperada, ultimo = c.get("esperada_at"), c.get("ultimo_at")
    if not esperada:
        return _paso("corrio", "¿Corrió cuando debía?", NO_SE,
                     "este chequeo no tiene horario esperado (no sale de un cron), "
                     "así que la pregunta no aplica.", tabla="deploy/crontab.txt")
    if not ultimo:
        return _paso("corrio", "¿Corrió cuando debía?", REVISAR,
                     f"debía correr **{esperada}** y **no hay ninguna corrida "
                     "registrada**. O nunca corrió, o corre sin instrumentar — y "
                     "de lo que no se registra no se puede afirmar que está bien.",
                     tabla="manager.job_runs")
    atrasado = ultimo < esperada
    return _paso("corrio", "¿Corrió cuando debía?", REVISAR if atrasado else OK,
                 f"esperada **{esperada}** · última **{ultimo}**"
                 + (" → **no corrió**. Todo lo que sigue habla de datos viejos."
                    if atrasado else " → al día."),
                 tabla="manager.job_runs")


def _lente_salio_bien(c: dict) -> dict:
    estado = (c.get("estado") or "").lower()
    ev = (c.get("evidencia") or "").strip()
    if estado == "error":
        return _paso("salio_bien", "¿Salió bien?", REVISAR,
                     f"**no**: {c.get('motivo')}."
                     + (f"\n\nEvidencia: `{ev[:400]}`" if ev else ""),
                     tabla="manager.job_runs")
    if estado == "warn":
        return _paso("salio_bien", "¿Salió bien?", REVISAR,
                     f"**a medias**: {c.get('motivo')}. Un parcial no es un fallo, "
                     "pero tampoco es verde: algo del trabajo no se hizo."
                     + (f"\n\nEvidencia: `{ev[:400]}`" if ev else ""),
                     tabla="manager.job_runs")
    return _paso("salio_bien", "¿Salió bien?", OK, c.get("motivo") or "sin errores",
                 tabla="manager.job_runs")


def _lente_dato_fresco(c: dict) -> dict:
    if (c.get("familia") or "") != "dato":
        return _paso("fresco", "¿Dejó el dato fresco?", INFO,
                     "no es un contrato de frescura: lo que este chequeo mira es "
                     "la CORRIDA, no el contenido de una tabla.",
                     tabla="—")
    return _paso("fresco", "¿Dejó el dato fresco?",
                 OK if (c.get("estado") or "") == "ok" else REVISAR,
                 f"{c.get('motivo')}"
                 + (f" · {c.get('evidencia')}" if c.get("evidencia") else ""),
                 tabla=c.get("tabla") or "—")


def _lente_historial(c: dict, hist: list[dict]) -> dict:
    """**Lo que separa «otra vez lo mismo» de «algo nuevo»** — y son dos problemas
    distintos: el nuevo se atiende, el que vuelve se investiga de raíz."""
    if not hist:
        return _paso("historial", "¿Ya pasó antes?", INFO,
                     "no hay transiciones registradas de este chequeo: es la "
                     "primera vez que se lo ve cambiar de estado, o el registro "
                     "empezó después.", tabla="manager.salud_eventos")
    caidas = [h for h in hist if (h.get("a") or "") in ("error", "warn")]
    n = len(caidas)
    lineas = "\n".join(f"  · {h.get('at')}: {h.get('de') or '—'} → {h.get('a')} "
                       f"({h.get('motivo')})" for h in hist[:6])
    if n >= _RECURRENTE:
        return _paso("historial", "¿Ya pasó antes?", REVISAR,
                     f"**sí, {n} veces** en el historial registrado. Esto no es un "
                     "incidente: es un patrón, y arreglarlo de nuevo a mano lo va a "
                     f"traer de vuelta.\n\n{lineas}",
                     tabla="manager.salud_eventos")
    return _paso("historial", "¿Ya pasó antes?", INFO,
                 f"{n} caída/s registrada/s.\n\n{lineas}",
                 tabla="manager.salud_eventos")


def _lente_aguas_abajo(c: dict) -> dict:
    """Qué se rompe DESPUÉS. Es la diferencia entre «un job falló» y «el AuM de hoy
    está mal», que es lo que decide si esto se mira ahora o el lunes."""
    det = (c.get("detalle") or "").strip()
    mods = ", ".join(c.get("modulos") or [])
    if det:
        return _paso("aguas_abajo", "Qué se rompe aguas abajo", INFO, det,
                     tabla=c.get("tabla") or "—")
    return _paso("aguas_abajo", "Qué se rompe aguas abajo", NO_SE,
                 "este chequeo **no declara qué alimenta**, así que no se puede "
                 "decir qué queda mal cuando falla. Declararlo es una línea en el "
                 "catálogo y es lo que convierte una alerta en una prioridad."
                 + (f" (módulos: {mods})" if mods else ""),
                 tabla="api/services/salud.py::CONTRATOS")


def _lente_ia(c: dict, chequeo_id: str) -> dict:
    """**La única lente que gasta tokens**, y va última a propósito.

    Las siete de arriba contestan con aritmética y son gratis; esta lee el log en
    prosa y opina, que es justamente donde un modelo aporta y una regla no. Si no
    hay diagnóstico —sin key, sin presupuesto, sin evento confirmado— la pantalla
    **no se rompe**: las otras siete ya dijeron lo suyo.
    """
    try:
        from api.services import salud
        d = salud.diagnostico(chequeo_id)
    except Exception as e:
        return _paso("ia", "Lectura del incidente (IA)", NO_SE,
                     f"no se pudo pedir el diagnóstico ({type(e).__name__}). El "
                     "resto del análisis no depende de esto.", tabla="ia.trazas")
    txt = (d or {}).get("texto")
    if not txt:
        return _paso("ia", "Lectura del incidente (IA)", INFO,
                     (d or {}).get("motivo") or "sin diagnóstico disponible.",
                     tabla="ia.trazas")
    return _paso("ia", "Lectura del incidente (IA)", INFO, txt,
                 tabla="ia.trazas" + ("  ·  cacheado" if d.get("cacheado") else ""))


def _lente_arreglo(c: dict) -> dict:
    """Qué HARÍA falta para arreglarlo — todavía sin hacerlo.

    Es el mismo lugar que ocupaba «QUÉ SE VA A PISAR» en la puerta de bonos antes
    de que existiera el arreglo: nombrar la acción es el paso previo a ejecutarla,
    y es lo que permite discutir si está bien ANTES de darle el botón.
    """
    familia = (c.get("familia") or "")
    mods = ", ".join(c.get("modulos") or []) or "el módulo del cron"
    propuesta = {
        "job": f"relanzar `{mods}` y verificar que la corrida quede en `ok`",
        "dato": "correr el job que llena esa tabla para la fecha faltante",
        "control": "revisar el invariante: el arreglo depende de qué dato lo rompe",
    }.get(familia, "sin acción tipificada todavía")
    return _paso("arreglo", "Qué haría falta para arreglarlo", INFO,
                 f"{propuesta}.\n\n**El agente todavía NO lo ejecuta**: relanzar un "
                 "job tiene efectos afuera de `mercado.curvas` y se habilita cuando "
                 "el eval set diga que este diagnóstico acierta. Primero ver, "
                 "después simular, después escribir — el mismo camino que hizo la "
                 "puerta de bonos.",
                 tabla="—")


def diagnosticar(chequeo_id: str, *, con_ia: bool = True) -> dict:
    """**El análisis completo de un chequeo de SALUD**, con la misma forma que el
    de un bono: pasos numerados + veredicto, para que el modal los dibuje igual.

    `con_ia=False` corre las siete lentes deterministas y **no gasta un token**.
    """
    from api.services import salud

    cid = (chequeo_id or "").strip()
    if not cid:
        return {"ok": False, "error": "falta el chequeo"}
    try:
        c = next((x for x in salud.evaluar() if x.get("id") == cid), None)
    except Exception as e:
        return {"ok": False, "error": f"no se pudo evaluar SALUD: {e}"}
    if c is None:
        return {"ok": False, "chequeo_id": cid,
                "error": "ese chequeo ya no existe — puede haberse arreglado solo"}

    # El detalle trae lo que el chequeo base no lleva (logs, cifras, qué alimenta).
    try:
        c = {**c, **(salud.detalle(cid) or {})}
    except Exception:
        logger.debug("av_agent_salud: sin detalle de %s", cid)
    try:
        hist = salud.historial(chequeo_id=cid, limite=12)
    except Exception:
        hist = []

    ps = [_lente_que_es(c), _lente_corrio(c), _lente_salio_bien(c),
          _lente_dato_fresco(c), _lente_historial(c, hist), _lente_aguas_abajo(c)]
    if con_ia:
        ps.append(_lente_ia(c, cid))
    ps.append(_lente_arreglo(c))
    for i, p in enumerate(ps, 1):
        p["n"] = i
    return {"ok": True, "modo": "salud", "chequeo_id": cid,
            "titulo": c.get("titulo"), "familia": c.get("familia"),
            "estado": c.get("estado"), "motivo": c.get("motivo"),
            "chequeos": ps, "veredicto": _veredicto(ps)}
