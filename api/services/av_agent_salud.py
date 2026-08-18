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



# ── QUÉ ALIMENTA CADA JOB, y de qué depende ─────────────────────────────────
#
# El diagnóstico de `mercado_1816_series` decía, textual: *«no se puede precisar
# qué vista o función queda tocada porque el chequeo no declara qué alimenta»*.
# Es honesto, y también es un agujero con arreglo trivial: **nadie lo escribió
# nunca**. Sin eso, una alerta no se puede priorizar — no es lo mismo «un cron
# falló» que «la vista RESEARCH quedó con datos del viernes».
#
# `depende_de` es la otra mitad, y es la que convierte al agente en algo útil: si
# el job usa 1816 y hoy sabemos que 1816 tiene una **cuota de 50 tokens por día**,
# el agente puede conectar el síntoma con una lección YA aprendida en vez de
# mandar a leer logs a ciegas.
#
# Vive acá y no en `salud.py` a propósito: es conocimiento del AGENTE sobre el
# sistema, no parte de la evaluación. El día que el catálogo de jobs declare esto
# por su cuenta, se borra de acá.
JOBS: dict[str, dict] = {
    "mercado_1816_series": {
        "alimenta": "`research.mkt_1816_series` → la vista RESEARCH (series "
                    "históricas EOD de 1816). Sin esta corrida, RESEARCH muestra "
                    "los datos de la última rueda que sí entró.",
        "depende_de": ["1816"], "reintentable": True},
    "tamar_1816": {
        "alimenta": "`mercado.tamar_1816` → la TEA y el MARGEN de los bonos con "
                    "pata TAMAR en la vista de renta fija.",
        "depende_de": ["1816"], "reintentable": True},
    "ficha_1816": {
        "alimenta": "el EMISOR estandarizado en `mercado.curvas` y "
                    "`portafolio.assets` (agrupar por emisor cuenta mal sin esto).",
        "depende_de": [], "reintentable": True},
    "mercado_1816_discovery": {
        "alimenta": "`research.mkt_1816_instrumentos` → el catálogo local de 1816, "
                    "que es de donde el AV Agent saca la ficha sin gastar créditos.",
        "depende_de": ["1816"], "reintentable": True},
    "research_mail": {
        "alimenta": "`ia.research` → el mail diario de 1816 en la vista RESEARCH.",
        "depende_de": ["IMAP"], "reintentable": True},
    "portafolio_backfill": {
        "alimenta": "`portafolio.tenencia` → **el AuM entero**, Portfolios y el "
                    "motor de PnL. Es el job más caro de perder.",
        "depende_de": ["Aunesa"], "reintentable": True},
    "snapshot_cierre": {
        "alimenta": "`mercado.snapshots_cierre` → el precio de cierre por bono, "
                    "que es el fallback de precio del PnL y del AV Agent.",
        "depende_de": ["motores"], "reintentable": True},
    "negocio_movimientos": {
        "alimenta": "`operaciones.negocio_movimientos` → el cost-basis del PnL y "
                    "la vista NEGOCIO.",
        "depende_de": ["Aunesa"], "reintentable": True},
    "bcra": {
        "alimenta": "`macro.series_macro` (CER) → la valuación de todos los bonos "
                    "CER.",
        "depende_de": ["BCRA"], "reintentable": True},
    "av_agent": {
        "alimenta": "`mercado.av_agent_hallazgos` → esta misma pantalla.",
        "depende_de": ["1816"], "reintentable": True},
}

# Firmas de error CONOCIDAS → qué son y con qué lección se conectan. Es
# `Root Cause Analysis` de manual: el patrón en el texto del error es la evidencia
# más barata que existe, y hasta hoy nadie la miraba.
FIRMAS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("429", "demasiadas solicitudes", "rate limit"),
     "el proveedor nos RECHAZÓ por límite",
     "backoff-contra-una-cuota"),
    (("auth", "token", "unauthorized", "401"),
     "problema de CREDENCIAL o de token",
     "backoff-contra-una-cuota"),
    (("timeout", "timed out", "read timeout"),
     "el proveedor no contestó a tiempo", ""),
    (("connection", "connectionerror", "conexión", "unreachable", "dns"),
     "no se pudo llegar al proveedor (red)", ""),
    (("no space left", "disk"), "el disco del Droplet", ""),
    (("killed", "oom", "memory"), "el proceso murió por memoria", ""),
)


def _corridas(c: dict) -> list[dict]:
    return (c.get("corridas") or []) if isinstance(c.get("corridas"), list) else []


def _lente_log(c: dict) -> dict:
    """**EL LOG DE VERDAD.** `salud.detalle()` ya traía los errores y las últimas
    40 líneas del `JobRunLogger`, y el agente no las leía: mandaba a "revisar los
    logs del scheduler" cuando los tenía a mano.

    Un diagnóstico que manda a buscar lo que ya tiene enfrente no es un
    diagnóstico: es una derivación.
    """
    corridas = _corridas(c)
    if not corridas:
        return _paso("log", "El log de las últimas corridas", NO_SE,
                     "no hay corridas registradas para este chequeo. Si el job "
                     "corre pero no usa `JobRunLogger`, **es un punto ciego**: no "
                     "se puede afirmar nada de algo que no deja rastro.",
                     tabla="manager.job_runs")
    ult = corridas[0]
    errores = ult.get("errores") or []
    log = ult.get("log") or []
    cuerpo = (f"última corrida: **{ult.get('status')}** · {ult.get('inicio')} · "
              f"{ult.get('elapsed_s')}s · stats {ult.get('stats') or '—'}")
    if errores:
        cuerpo += "\n\n**ERRORES que reportó el job:**\n" + "\n".join(
            f"  · `{str(e)[:300]}`" for e in errores[:6])
    if log:
        cuerpo += "\n\n**Últimas líneas del log:**\n" + "\n".join(
            f"  `{str(l)[:220]}`" for l in log[-8:])
    if not errores and not log:
        cuerpo += ("\n\nLa corrida no dejó errores ni log. Si el chequeo está en "
                   "rojo por ATRASO, entonces el job **no llegó a arrancar** — el "
                   "problema está en el scheduler, no adentro del job.")
    return _paso("log", "El log de las últimas corridas",
                 REVISAR if errores else INFO, cuerpo, tabla="manager.job_runs")


def _lente_firma(c: dict, lecciones_por_slug: dict) -> dict:
    """¿El error se PARECE a algo que ya nos pasó?

    Acá se cierra el bucle de la memoria: el texto del error es la evidencia más
    barata que existe, y cruzarlo contra las lecciones convierte «revisá los logs»
    en «esto es lo mismo de la vez pasada, y así se resolvió».
    """
    texto = " ".join(
        [str(e) for cor in _corridas(c)[:3] for e in (cor.get("errores") or [])]
        + [str(l) for cor in _corridas(c)[:2] for l in (cor.get("log") or [])[-15:]]
        + [str(c.get("evidencia") or ""), str(c.get("motivo") or "")]).lower()
    if not texto.strip():
        return _paso("firma", "¿Se parece a algo que ya nos pasó?", NO_SE,
                     "no hay texto de error para comparar.", tabla="—")
    hits = []
    for patrones, que_es, slug in FIRMAS:
        if any(pat in texto for pat in patrones):
            lec = lecciones_por_slug.get(slug)
            hits.append(f"**{que_es}**"
                        + (f" → ya lo aprendimos: *{lec['titulo']}*. {lec['cambio']}"
                           if lec else " (todavía sin lección asociada)"))
    if not hits:
        return _paso("firma", "¿Se parece a algo que ya nos pasó?", INFO,
                     "el texto del error no matchea ninguna firma conocida. Si "
                     "resulta ser algo recurrente, **anotarlo como lección** es lo "
                     "que hace que la próxima vez se reconozca solo.", tabla="—")
    return _paso("firma", "¿Se parece a algo que ya nos pasó?", REVISAR,
                 "\n\n".join(hits), tabla="mercado.av_agent_lecciones")


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


def _job_id(c: dict) -> str:
    """El nombre del job, del id del chequeo (`job:tamar_1816` → `tamar_1816`)."""
    return str(c.get("id", "")).split(":", 1)[-1].strip()


def _lente_aguas_abajo(c: dict) -> dict:
    """Qué se rompe DESPUÉS. Es la diferencia entre «un job falló» y «el AuM de hoy
    está mal», y es lo que decide si esto se mira ahora o el lunes."""
    det = (c.get("detalle") or "").strip()
    if det:
        return _paso("aguas_abajo", "Qué se rompe aguas abajo", INFO, det,
                     tabla=c.get("tabla") or "—")
    ficha = JOBS.get(_job_id(c)) or {}
    if ficha.get("alimenta"):
        dep = ", ".join(ficha.get("depende_de") or []) or "nada externo"
        return _paso("aguas_abajo", "Qué se rompe aguas abajo", INFO,
                     ficha["alimenta"] + f"\n\nDepende de: **{dep}**.",
                     tabla="av_agent_salud.JOBS")
    mods = ", ".join(c.get("modulos") or [])
    return _paso("aguas_abajo", "Qué se rompe aguas abajo", NO_SE,
                 "este job **no declara qué alimenta**, así que no se puede decir "
                 "qué queda mal cuando falla — y sin eso una alerta no se puede "
                 "priorizar. Declararlo es UNA línea en `av_agent_salud.JOBS`."
                 + (f" (módulos: {mods})" if mods else ""),
                 tabla="av_agent_salud.JOBS")


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
    """Qué HARÍA falta para arreglarlo, **con el comando exacto**.

    Nombrar la acción es el paso previo a ejecutarla, y es lo que permite discutir
    si está bien ANTES de darle el botón. Pero «relanzar el job» no es una acción:
    es una categoría. La acción es el comando que hay que tipear, y darlo escrito
    es la diferencia entre un diagnóstico y una tarea.
    """
    familia = (c.get("familia") or "")
    jid = _job_id(c)
    mods = [str(m).rsplit(".", 1)[-1] for m in (c.get("modulos") or [])]
    modulo = (c.get("modulos") or [None])[0] or (f"jobs.{jid}" if jid else "")
    ficha = JOBS.get(jid) or {}

    if familia == "job" and modulo:
        cuerpo = ("**Relanzar la corrida:**\n\n```\ncd /root/TradingAV && "
                  f"source venv/bin/activate && python -m {modulo}\n```\n\n"
                  "Después, este chequeo tiene que quedar en verde solo: SALUD lo "
                  "reevalúa en vivo contra `manager.job_runs`.")
        if "1816" in (ficha.get("depende_de") or []):
            cuerpo += ("\n\n⚠️ Este job **depende de 1816**, que tiene un cupo de "
                       "**50 tokens por día**. Antes de relanzarlo conviene mirar "
                       "`python -m scripts.diag_1816_tokens`: si el cupo está "
                       "quemado, la corrida va a fallar igual y va a gastar más.")
    elif familia == "dato":
        cuerpo = ("Correr el job que llena esa tabla para la fecha faltante. La "
                  "tabla es `" + str(c.get("tabla") or "?") + "`; el job que la "
                  "escribe está en `deploy/crontab.txt`.")
    else:
        cuerpo = ("Revisar el invariante: el arreglo depende de qué dato lo rompe, "
                  "así que no hay un comando único.")
    return _paso("arreglo", "Qué haría falta para arreglarlo", INFO,
                 cuerpo + "\n\n**El agente todavía NO lo ejecuta.** Relanzar un job "
                 "tiene efectos afuera de `mercado.curvas` y se habilita cuando el "
                 "eval set diga que este diagnóstico acierta — primero ver, después "
                 "simular, después escribir." + (f" (módulos: {', '.join(mods)})"
                                                 if len(mods) > 1 else ""),
                 tabla="deploy/crontab.txt")


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

    from api.services import av_agent_memoria as mem

    # Las lecciones YA aprendidas, indexadas para que la lente de firmas pueda
    # conectar un error con la vez que ya lo resolvimos.
    lecciones = mem.lecciones_de("", "") + mem.lecciones_de("", "salud")
    por_slug = {l["slug"]: l for l in lecciones}

    ps = [_lente_que_es(c), _lente_corrio(c), _lente_salio_bien(c),
          _lente_dato_fresco(c), _lente_log(c), _lente_firma(c, por_slug),
          _lente_historial(c, hist), _lente_aguas_abajo(c)]
    if con_ia:
        ps.append(_lente_ia(c, cid))
    ps.append(_lente_arreglo(c))
    for i, p in enumerate(ps, 1):
        p["n"] = i

    # La TRAZA también acá: un diagnóstico de SALUD es tan registrable como uno de
    # un bono, y sin eso la mitad del aprendizaje del sistema quedaría afuera.
    mem.registrar_traza(caso=cid, dominio="salud",
                        causa=f"salud_{c.get('familia') or '?'}",
                        veredicto=(c.get("estado") or ""),
                        observaciones=[{"clave": p["clave"], "estado": p["estado"],
                                        "detalle": p["detalle"]} for p in ps],
                        contexto={"familia": c.get("familia"),
                                  "motivo": c.get("motivo"),
                                  "schedule": c.get("schedule")})
    return {"ok": True, "modo": "salud", "chequeo_id": cid,
            "titulo": c.get("titulo"), "familia": c.get("familia"),
            "estado": c.get("estado"), "motivo": c.get("motivo"),
            "chequeos": ps, "veredicto": _veredicto(ps)}
