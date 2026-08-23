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
import re

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
    # Los que salían con «afecta: escribe manager.…» — el respaldo derivado de
    # `core/escribe`, que dice la TABLA pero no la PANTALLA. Acá va el criterio.
    "controles_datos": {
        "alimenta": "los CONTROLES de datos del agente (tab ENCONTRÓ). Sin esta "
                    "corrida no se re-verifica ninguna anomalía: lo que se "
                    "arregló sigue apareciendo y lo nuevo no aparece.",
        "depende_de": [], "reintentable": True},
    "cierre_canje": {
        "alimenta": "`mercado.canje_cierre` → el canje del día en ANALÍTICA. Sin "
                    "esto la pantalla muestra el canje de la rueda anterior.",
        "depende_de": ["Primary"], "reintentable": True},
    "forwards_zscore": {
        "alimenta": "`mercado.forwards_zscore` → el z-score de la matriz de "
                    "FORWARDS: sin esto no se puede decir si un forward está "
                    "caro o barato contra su propia historia.",
        "depende_de": [], "reintentable": True},
    "precios_acciones_daily": {
        "alimenta": "`mercado.precios_acciones` → el Scanner de RENTA VARIABLE "
                    "(retornos y estadística del subyacente).",
        "depende_de": ["Yahoo"], "reintentable": True},
    "seguimiento": {
        "alimenta": "la verificación de que los arreglos del agente AGUANTARON "
                    "(§0.ac). Sin esta corrida el eval set nunca recibe la "
                    "señal más fuerte que tiene: que el problema no volvió.",
        "depende_de": [], "reintentable": True},
    "guardrails": {
        "alimenta": "los invariantes de sanidad post-cierre. No alimenta una "
                    "pantalla: es el chequeo que avisa si algo quedó inconsistente.",
        "depende_de": [], "reintentable": True},
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
        "alimenta": "`agente.av_agent_hallazgos` → esta misma pantalla.",
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


def _lente_firma(c: dict, lecciones_por_slug: dict) -> dict | None:
    """¿El error se PARECE a algo que ya nos pasó?

    ⚠️ **DOS BUGS CORREGIDOS el 2026-08-19, y el primero es el que enseña algo.**

    1. Se comparaba por SUBSTRING, y el patrón `"429"` cae adentro de `"[42932]
       PBJ26: sin cartera"`. El control de assets salía diciendo «el proveedor
       nos RECHAZÓ por límite» — confiado, específico y completamente falso.
       Un número corto buscado como substring matchea cualquier cosa: ahora los
       patrones numéricos se buscan con LÍMITE DE PALABRA.
    2. Se miraba también `evidencia` y `motivo`. La evidencia de un control son
       DATOS (tickers, ids, nombres de bonos), no un mensaje de error — buscar
       firmas de fallas ahí es garantizar falsos positivos. Ahora solo se mira
       el texto que un JOB reportó como error.

    Y si no matchea nada, **no dice nada**. Antes explicaba que no había
    matcheado y sugería anotar una lección: relleno en cada tarjeta. El user:
    *«es mejor no decir nada que decir todo»*.
    """
    if (c.get("familia") or "") != "job":
        return None          # un control no tiene «texto de error» que comparar
    texto = " ".join(
        [str(e) for cor in _corridas(c)[:3] for e in (cor.get("errores") or [])]
        + [str(l) for cor in _corridas(c)[:2] for l in (cor.get("log") or [])[-15:]]
    ).lower()
    if not texto.strip():
        return None
    hits = []
    for patrones, que_es, slug in FIRMAS:
        for pat in patrones:
            # Los patrones que son puro número (429, 401) se buscan con límite de
            # palabra; los de texto, como substring — «timeout» adentro de
            # «ReadTimeout» tiene que matchear.
            encontrado = (re.search(rf"\b{re.escape(pat)}\b", texto) is not None
                          if pat.isdigit() else pat in texto)
            if encontrado:
                lec = lecciones_por_slug.get(slug)
                hits.append(f"**{que_es}**"
                            + (f" → ya lo aprendimos: *{lec['titulo']}*. {lec['cambio']}"
                               if lec else ""))
                break
    if not hits:
        return None
    return _paso("firma", "Ya nos pasó", REVISAR, "\n".join(hits),
                 tabla="agente.av_agent_lecciones")



# QUÉ ROMPE cada control cuando está en rojo, DÓNDE se corrige, y el ATAJO para
# ir a corregirlo. El texto sale de los docstrings de `jobs/controles_datos.py`,
# que es donde vive el criterio — escribirlo una vez y leerlo acá evita que la
# pantalla explique una cosa y el control mida otra.
CONTROLES: dict[str, dict] = {
    "patas_sin_precio": {
        "rompe": "la fila del bono sale ENTERA en «--»: el motor no le está "
                 "pidiendo el precio a ese símbolo, y como nuestras tablas solo "
                 "guardan lo suscrito, «sin precio» no significa «no cotiza»",
        "donde": "AV Agent → pedirla (el motor la levanta en 5s, sin reiniciar)",
        "url": "/manager"},
    "patas_dolar_sin_pedir": {
        "rompe": "la grilla muestra 156.270 al lado de bonos en dólares. La "
                 "valuación está bien —el motor divide por el MEP—, pero como "
                 "nadie escucha la pata en dólares no sabemos si opera, y sin "
                 "ese dato no se puede decidir apuntar el master ahí",
        "donde": "AV Agent → pedir la pata (el motor la levanta en 5s, sin "
                 "reiniciar)",
        "url": "/manager"},
    "dia_sin_dato": {
        "rompe": "el job de ese día no dejó el dato: las vistas que lo leen "
                 "muestran el día anterior SIN avisar, que es exactamente lo "
                 "que pasó el 2026-08-20 con el AuM (Aunesa devolvió 500 y "
                 "nadie se enteró)",
        "donde": "AV Agent → rehacer el día (corre el mismo job del cron, con "
                 "su lock, y verifica releyendo la tabla)",
        "url": "/manager"},
    "patas_equivocadas": {
        "rompe": "el master apunta a la pata en PESOS teniendo la de dólares: la "
                 "grilla muestra 156.270 al lado de bonos en dólares. La "
                 "valuación está bien —el motor divide por el MEP— pero el "
                 "símbolo está mal cargado, y el hallazgo vuelve todas las "
                 "ruedas hasta que se corrija",
        "donde": "AV Agent → apuntar el master (corrige el campo Y pide la pata: "
                 "se ve en 5s, sin reiniciar)",
        "url": "/manager"},
    "assets_ticker_partido": {
        "rompe": "el bono **sigue sumando al AuM** (ese join va por `unidad`) pero "
                 "el join `curvas.ticker → assets.ticker` no lo encuentra: "
                 "desaparece de flujos, acreencias y renta fija. No falla nada — "
                 "las dos tablas son coherentes consigo mismas, y por eso se "
                 "descubre mirando una pantalla. Medido: 2 de 2 eran UN carácter "
                 "tipeado a mano (la «O» escrita como cero, la «N» como «B»)",
        "donde": "AV Agent → corregir el TICKER (el valor sale de la propia "
                 "unidad, que la escribe Aunesa)",
        "url": "/manager"},
    "forwards_faltantes": {
        "rompe": "esos bonos no salen en la matriz de forwards de su curva",
        "donde": "Renta Fija → FORWARDS", "url": "/renta-fija"},
    "rf_sin_tasa": {
        "rompe": "cotizan pero sin TEA: quedan fuera de cualquier comparación",
        "donde": "Renta Fija → CURVAS", "url": "/renta-fija"},
    "assets_sin_cartera": {
        "rompe": "rompen el divisor de valuación: quedan SIN CLASIFICAR en el AuM",
        "donde": "Manager → TÍTULOS · ASSETS (columna CARTERA)",
        "url": "/manager?tab=assets"},
    "unidades_gemelas": {
        "rompe": "la misma unidad dos veces (espacios): la ficha se escribe en "
                 "una fila y la tenencia referencia la otra — REGLA #9",
        "donde": "scripts/diag_unidades_fantasma (--reparar con guardas)",
        "url": ""},
    "fci_incompletos": {
        "rompe": "salen SIN NOMBRE y se fusionan entre sí en /aum → FCI",
        "donde": "Manager → TÍTULOS · ASSETS (ticker y emisor del FCI)",
        "url": "/manager?tab=assets"},
    "titulos_sin_flujo": {
        "rompe": "esos bonos NO valúan: la posición queda sin precio en el AuM",
        "donde": "AV Agent → dar de alta la curva, o Manager → TÍTULOS",
        "url": "/manager?tab=titulos"},
    "rf_valuada_x1": {
        "rompe": "renta fija valuada SIN ÷100: la tenencia queda 100 veces inflada",
        "donde": "es un tipoTitulo nuevo de Aunesa fuera de TIPOS_DIVISOR_100",
        "url": ""},
    "comitentes_sin_nivel1": {
        "rompe": "quedan fuera de la segmentación y de los filtros madre",
        "donde": "Manager → CLIENTES (nivel_1)", "url": "/manager?tab=clientes"},
    "contrapartes_pendientes": {
        "rompe": "hay cuentas candidatas sin dar de alta como contraparte",
        "donde": "Manager → CONTRAPARTES", "url": "/manager?tab=contrapartes"},
    "ops_sin_tc": {
        "rompe": "boletos ARS sin `mep`: no se pueden dolarizar en OPERACIONES",
        "donde": "Operaciones → MOVIMIENTOS", "url": "/operaciones"},
    "simbolos_cuarentena": {
        "rompe": "Primary rechaza esos símbolos: se excluyen de las suscripciones "
                 "y esos papeles quedan sin precio",
        "donde": "Manager → TÍTULOS · ASSETS (corregir o dar de baja el símbolo)",
        "url": "/manager?tab=assets"},
}


def _lente_casos(c: dict, det: dict) -> dict | None:
    """**LOS CASOS, completos.** Es lo único que un control tiene para decir, y
    estaba resumido a tres ejemplos truncados a 400 caracteres.

    Un control que dice «8 anomalías» y muestra 3 obliga a irse a otra pantalla
    para ver las otras 5 — o sea que el diagnóstico no diagnostica: avisa.
    """
    if (c.get("familia") or "") != "control":
        return None
    an = det.get("anomalias") or []
    if not an:
        return _paso("casos", "Los casos", INFO,
                     (c.get("evidencia") or "").strip() or "sin detalle disponible.")
    from datetime import date
    hoy = date.today()

    def _dias(iso: str) -> int | None:
        try:
            return (hoy - date.fromisoformat(str(iso)[:10])).days
        except (TypeError, ValueError):
            return None

    lineas = []
    for a in an[:60]:
        item = str(a.get("item") or a.get("clave") or "?")
        # El detalle suele empezar repitiendo el item («[42932] PBJ26: sin
        # cartera» debajo de «[42932] PBJ26»). Se saca: leer dos veces lo mismo
        # es lo que hace que la lista parezca el doble de larga.
        desc = str(a.get("detalle") or a.get("motivo") or "").strip()
        if desc.startswith(item):
            desc = desc[len(item):].lstrip(" :·-")
        d = _dias(a.get("desde"))
        antig = (" · hoy" if d == 0 else f" · hace {d} d" if d is not None else "")
        lineas.append(f"  · {item}" + (f" — {desc}" if desc else "") + antig)
    mas = f"\n  … y {len(an) - 60} más" if len(an) > 60 else ""

    # **CUÁNDO SE VERIFICÓ POR ÚLTIMA VEZ.** Es el dato que faltaba y el que
    # cambia por completo cómo se lee la lista: «PBJ26 (2026-07-09)» parecía
    # «lo detectamos hace 40 días y nadie lo volvió a mirar». Es al revés — el
    # control recalcula TODO en cada corrida y marca resuelto lo que desaparece
    # (`_diff_y_persistir`), así que lo que sigue en la lista **se comprobó de
    # nuevo hoy**. Sin decirlo, el agente parecía un cementerio de avisos viejos.
    visto = max((str(a.get("visto") or "") for a in an), default="")
    dv = _dias(visto)
    # ⚠️ «el control no está corriendo» era FALSO y alarmista (2026-08-23): el
    # control corre por cron — que la comprobación tenga un día es su cadencia,
    # no una falla. Se dice la edad y CÓMO refrescar, sin inventar un incidente.
    cab = ("comprobados HOY" if dv == 0 else
           f"comprobados por última vez hace {dv} día{'s' if dv != 1 else ''} — "
           f"se re-verifican en la próxima corrida del control o con "
           f"↻ CHEQUEAR AHORA" if dv else "sin fecha de comprobación")
    return _paso("casos", f"Los {len(an)} casos · {cab}",
                 REVISAR, "\n".join(lineas) + mas,
                 tabla="manager.controles_datos")


def _lente_que_rompe(c: dict) -> dict | None:
    """QUÉ queda mal y DÓNDE se corrige. Dos frases; el resto era relleno."""
    familia = (c.get("familia") or "")
    if familia == "control":
        cid = str(c.get("id", "")).split(":", 1)[-1]
        f = CONTROLES.get(cid)
        if not f:
            return None
        # LA SALIDA, siempre dicha (ley del user 2026-08-23: «nada en ENCONTRÓ
        # sin solución»): si el control no tiene botón, el motivo declarado en
        # `hacer.SIN_ACCION` dice por dónde se arregla — un diagnóstico que
        # termina en «alguien lo mira» no es un diagnóstico.
        extra = ""
        try:
            from api.services.av_agent_hacer import SIN_ACCION
            if cid in SIN_ACCION:
                extra = f"\n\nPor qué no hay botón acá: {SIN_ACCION[cid]}."
        except Exception:
            pass
        return _paso("rompe", "Qué queda mal", REVISAR,
                     f"{f['rompe']}.{extra}", accion=f.get("url") or "",
                     tabla=f["donde"])
    ficha = JOBS.get(_job_id(c)) or {}
    if ficha.get("alimenta"):
        return _paso("rompe", "Qué queda mal", INFO, ficha["alimenta"])
    det = (c.get("detalle") or "").strip()
    return _paso("rompe", "Qué queda mal", INFO, det) if det else None


def _lente_corrio(c: dict) -> dict | None:
    """La resta que nadie hacía: ¿corrió CUANDO DEBÍA? Es la pregunta que dejó
    pasar 48 horas de backfill muerto con la card de AuM en verde.

    **Devuelve `None` cuando no aplica.** Antes contestaba «la pregunta no
    aplica» y ocupaba un renglón: un paso que dice que no tiene nada que decir es
    ruido, y diez de esos entierran al que sí lo tiene."""
    esperada, ultimo = c.get("esperada_at"), c.get("ultimo_at")
    if not esperada:
        return None
    if not ultimo:
        return _paso("corrio", "¿Corrió cuando debía?", REVISAR,
                     f"debía correr **{esperada}** y **no hay ninguna corrida "
                     "registrada**.", tabla="manager.job_runs")
    if ultimo < esperada:
        return _paso("corrio", "¿Corrió cuando debía?", REVISAR,
                     f"**NO corrió.** Debía {esperada}, la última fue {ultimo}. "
                     "Todo lo que sigue habla de datos viejos.",
                     tabla="manager.job_runs")
    return None      # corrió cuando debía: no hay nada que reportar


def _lente_salio_bien(c: dict) -> dict | None:
    """QUÉ falló, sin adornos. Si salió bien, no dice nada."""
    estado = (c.get("estado") or "").lower()
    if estado not in ("error", "warn"):
        return None
    if (c.get("familia") or "") == "control":
        return None          # en un control el «qué falló» son los CASOS
    ev = (c.get("evidencia") or "").strip()
    return _paso("salio_bien", "Qué falló", REVISAR,
                 f"{c.get('motivo')}." + (f"\n\n`{ev[:600]}`" if ev else ""),
                 tabla="manager.job_runs")


def _lente_dato_fresco(c: dict) -> dict | None:
    if (c.get("familia") or "") != "dato":
        return None
    return _paso("fresco", "¿El dato quedó fresco?",
                 OK if (c.get("estado") or "") == "ok" else REVISAR,
                 f"{c.get('motivo')}"
                 + (f" · {c.get('evidencia')}" if c.get("evidencia") else ""),
                 tabla=c.get("tabla") or "—")


def _lente_historial(c: dict, hist: list[dict]) -> dict | None:
    """**Solo habla si es un PATRÓN.** «Pasó una vez» no cambia ninguna decisión;
    «pasó seis veces» sí — deja de ser un incidente y pasa a ser un diseño que
    hay que cambiar."""
    caidas = [h for h in (hist or []) if (h.get("a") or "") in ("error", "warn")]
    if len(caidas) < _RECURRENTE:
        return None
    lineas = "\n".join(f"  · {str(h.get('at'))[:16]}: {h.get('motivo')}"
                        for h in hist[:5])
    return _paso("historial", f"Ya pasó {len(caidas)} veces", REVISAR,
                 "**Es un patrón, no un incidente.** Arreglarlo de nuevo a mano lo "
                 f"va a traer de vuelta.\n\n{lineas}",
                 tabla="manager.salud_eventos")


def _job_id(c: dict) -> str:
    """El nombre del job, del id del chequeo (`job:tamar_1816` → `tamar_1816`)."""
    return str(c.get("id", "")).split(":", 1)[-1].strip()


def recontrolar(control_id: str) -> dict:
    """**VOLVER A MIRAR ESTE CONTROL, AHORA.** Corre SOLO ese invariante.

    Pedido del user: *«que me digas que el último control es hace 1 día es
    inaceptable… tiene que tener una feature rápida que vaya a buscar esos en el
    momento, 1 x 1, y diga si está»*.

    Y se puede hacer barato porque cada control **ya es una función suelta**
    (`jobs/controles_datos.CONTROLES`): no hace falta correr los nueve para
    contestar por uno. La persistencia es la MISMA (`_diff_y_persistir`), así
    que un re-chequeo a mano y la corrida del cron dejan exactamente el mismo
    estado — si fueran dos caminos, el botón podría decir una cosa y el tablero
    otra al día siguiente.

    Devuelve cuántos siguen, cuántos se resolvieron y cuántos aparecieron.
    """
    cid = (control_id or "").strip().split(":", 1)[-1]
    try:
        from jobs.controles_datos import CONTROLES as REGISTRO
        from jobs.controles_datos import _diff_y_persistir
    except Exception as e:
        return {"ok": False, "error": f"no se pudo cargar el control: {e}"}
    ctl = next((c for c in REGISTRO if c.id == cid), None)
    if ctl is None:
        return {"ok": False, "error": f"no existe el control «{cid}»"}
    try:
        items = ctl.fn() or []
        r = _diff_y_persistir(cid, items)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    n_res = len(r.get("resueltos") or [])
    n_new = len(r.get("nuevos") or [])
    activos = r.get("activos", 0)

    # ⚠️⚠️ **EL RE-CHEQUEO DEVOLVÍA SOLO EL CONTEO, Y ESO SE LEÍA COMO QUE EL
    # AGENTE NO TIENE MEMORIA.** El user (2026-08-21), después de dar de alta las
    # 4 contrapartes y apretar el botón:
    #
    #     ↻ CHEQUEAR AHORA    →  «0 siguen · 4 se resolvieron»
    #     ...y abajo seguían los 4 casos listados, con «hace 7 d»
    #
    # > *«Ya los marqué como hechos y sigue figurando. El volver a chequear dice
    # > que sí pero no corta el resto del mensaje ni nada, mantiene todo en vez
    # > de decir que ya está resuelto. No tiene memoria de los cambios.»*
    #
    # **Y sí tenía memoria**: `_diff_y_persistir` los cerró correctamente en
    # `manager.controles_datos`. Lo que faltaba era DEVOLVER el diagnóstico
    # recalculado, así que la pantalla pintaba el conteo nuevo arriba de la
    # cadena vieja. Dos verdades contradiciéndose en la misma tarjeta se leen
    # como que el sistema no se enteró — que es peor que no haber puesto el
    # botón.
    #
    # Se recalcula acá y no en el front por la razón de siempre: el que sabe si
    # quedó resuelto es el que acaba de correr el control.
    salida = {"ok": True, "control_id": cid, "activos": activos,
              "resueltos": n_res, "nuevos": n_new,
              "resuelto": activos == 0,
              "texto": (f"{activos} siguen"
                        + (f" · {n_res} se resolvieron" if n_res else "")
                        + (f" · {n_new} nuevos" if n_new else "")
                        + (" · nada cambió" if not (n_res or n_new) else ""))}
    if activos == 0:
        salida["texto"] = ("✔ resuelto: no queda ninguno"
                           + (f" ({n_res} se cerraron recién)" if n_res else ""))
    try:
        salida["diagnostico"] = diagnosticar(f"control:{cid}")
    except Exception as e:                  # el conteo ya es útil por sí solo
        logger.warning("salud: no pude rehacer el diagnóstico de %s (%s)", cid, e)
    return salida


def _lente_puedo_hacerlo(c: dict, det: dict) -> dict | None:
    """**LO QUE EL AGENTE SABE HACER CON ESTO.** El paso que faltaba.

    Pedido del user (2026-08-19): *«ya sabés exacto qué campo de qué función hay
    que modificar… que el mismo agent aprenda a sugerir y que, si le das OK,
    actualice en el momento y luego controle que lo hizo bien en el mismo
    proceso»*.

    Este paso NO propone nada por sí solo: **declara la capacidad**. Proponer
    cuesta (una corrida del control, a veces una llamada al modelo) y no tiene
    sentido pagarlo cada vez que alguien abre un diagnóstico a mirar. El botón
    lo aprieta el que quiere hacerlo.

    Si el control no tiene acción, el paso **no se dibuja**. Un renglón que dice
    «para esto todavía no sé hacer nada» aparece nueve veces y entierra las dos
    veces que sí hay algo — la regla que ordena todas estas lentes.
    """
    if (c.get("familia") or "") != "control":
        return None
    cid = str(c.get("id", "")).split(":", 1)[-1]
    try:
        from api.services import av_agent_hacer as hacer
    except Exception:
        return None
    accion_id = hacer.POR_CONTROL.get(cid)
    if not accion_id:
        return None
    a = hacer.ACCIONES[accion_id]
    n = len(det.get("anomalias") or [])
    try:
        pend = len(hacer.pendientes(accion_id))
    except Exception:
        pend = 0
    # ── LO QUE YA SE HIZO, ANTES DE OFRECER HACERLO OTRA VEZ ────────────────
    #
    # ⚠️ El user (2026-08-22): *«yo antes ya le mandé el mail pero no me dice
    # AVISADO A LA PERSONA, y además no detecta bien qué avisó y qué no… dice IR
    # A ARREGLARLO, esperando… no es claro»*.
    #
    # La tarjeta ofrecía «avisarle a la persona» con el aviso YA en la bandeja
    # del destinatario. No estaba rota — **nunca miraba para atrás**. Y una
    # pantalla que no distingue «todavía no lo hice» de «ya está hecho y espero
    # respuesta» obliga a decidir a ciegas: o se manda dos veces lo mismo, o no
    # se manda nunca. Es el mismo modo de falla de siempre: nada falla, la
    # pantalla contesta con seguridad usando media verdad.
    avisado: list[dict] = []
    try:
        from api.services import av_agent_mensajes as msg
        avisado = msg.enviados_sobre(f"control:{cid}")
    except Exception as e:
        logger.warning("salud: no pude leer lo ya avisado de %s (%s)", cid, e)
    abiertos = [x for x in avisado if not x.get("resuelto")]

    cuerpo = (f"{a.titulo}. Escribe en {a.donde} — la misma puerta que usa la "
              f"pantalla, con tu OK y verificando después de escribir.")
    if abiertos:
        quienes = ", ".join(sorted({x["para"] for x in abiertos}))
        cuerpo = (f"YA AVISADO a {quienes} — el mensaje está en su bandeja y "
                  f"todavía no lo cerró. No hace falta volver a mandarlo: "
                  f"repetirlo no informa más.\n\n{cuerpo}")
    elif avisado:
        quienes = ", ".join(sorted({x["para"] for x in avisado}))
        cuerpo = (f"Ya se le avisó a {quienes} y lo dio por cerrado, pero el "
                  f"control SIGUE marcando casos.\n\n{cuerpo}")
    # ⚠️ **«Propuesta esperando tu OK» solo si NO hay un aviso abierto.** Con el
    # mensaje ya mandado, esa frase le pedía al user una decisión sobre algo que
    # él mismo ya había hecho — que es exactamente la confusión que reportó.
    if pend and not abiertos:
        cuerpo += (f"\n\nHay {pend} propuesta/s preparada/s: el agente ya sabe "
                   f"qué escribir y espera tu OK para hacerlo.")

    # El TÍTULO dice el estado, no la capacidad. «Esto lo sé hacer» con el
    # mensaje ya mandado se lee como que no se hizo nada.
    if abiertos:
        titulo = f"Ya avisado · esperando a {len(abiertos)} persona/s"
        nivel = OK
    else:
        titulo = f"Esto lo sé hacer · {n} caso/s" if n else "Esto lo sé hacer"
        nivel = INFO
    paso = _paso("hacer", titulo, nivel, cuerpo, tabla=a.donde)
    # El front dibuja el botón con esto. Va RESUELTO desde el backend para que la
    # pantalla no tenga que saber qué control tiene qué acción — el día que se
    # agregue una acción nueva aparece sola, sin tocar el front.
    paso["hacer"] = {"accion": accion_id, "titulo": a.titulo, "campo": a.campo,
                     "casos": n, "pendientes": (0 if abiertos else pend),
                     # Con esto el botón deja de decir «IR A ARREGLARLO» y pasa
                     # a decir «volver a avisar», que es lo que de verdad haría.
                     "avisado": abiertos, "avisado_cerrado": len(avisado) - len(abiertos)}
    return paso


def _lente_log_si_aplica(c: dict) -> dict | None:
    """El log de `manager.job_runs` es de JOBS. Preguntárselo por un control
    devolvía «no hay corridas registradas para este chequeo… es un punto ciego»
    — una advertencia inventada sobre algo que nunca tuvo que estar ahí."""
    if (c.get("familia") or "") != "job":
        return None
    return _lente_log(c)


def _lente_arreglo(c: dict) -> dict | None:
    """El COMANDO, y nada más. La explicación de por qué el agente todavía no lo
    ejecuta se decía en CADA tarjeta: repetida diez veces deja de leerse, y no
    cambia nada de lo que el que mira tiene que hacer."""
    familia = (c.get("familia") or "")
    jid = _job_id(c)
    modulo = (c.get("modulos") or [None])[0] or (f"jobs.{jid}" if jid else "")
    ficha = JOBS.get(jid) or {}
    if familia != "job" or not modulo:
        return None
    cuerpo = (f"```\ncd /root/TradingAV && source venv/bin/activate && "
              f"python -m {modulo}\n```")
    if "1816" in (ficha.get("depende_de") or []):
        cuerpo += ("\n\n⚠️ Depende de 1816 (cupo de 50 tokens/día). Si el cupo está "
                   "quemado la corrida falla igual: `python -m scripts.diag_1816_tokens`.")
    return _paso("arreglo", "Cómo se relanza", INFO, cuerpo,
                 tabla="deploy/crontab.txt")


def diagnosticar(chequeo_id: str) -> dict:
    """**El análisis completo de un chequeo de SALUD**, con la misma forma que el
    de un bono: pasos numerados + veredicto, para que el modal los dibuje igual.

    **Ninguna lente gasta un token.** La única que lo hacía leía el diagnóstico
    con IA que generaba el panel de SALUD, y ese panel se dio de baja el
    2026-08-19 (todo pasa por el agente). Sin panel nadie lo generaba, así que la
    lente iba a quedar muda para siempre — y el user ya la había desahuciado:
    *«acá tira cualquier cosa, cualquiera; no tiene nada que ver con el error…
    es mejor no decir nada a decir todo»*. La IA del agente vive donde sí aporta:
    el informe masivo (patrones sobre decenas de casos) y las propuestas de
    ACCIONES.
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
        det = salud.detalle(cid) or {}
        c = {**c, **det}
    except Exception:
        det = {}
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

    # **Orden: QUÉ pasa → QUÉ rompe → CÓMO se arregla.** Y las lentes que no
    # tienen nada que decir devuelven `None` y no se dibujan.
    #
    # Antes se dibujaban las diez SIEMPRE, y seis de ellas decían variantes de
    # «esta pregunta no aplica». El resultado era una pantalla que contaba el
    # WORKFLOW del agente en vez del problema — el user lo dijo así:
    # «literal no se entiende nada de nada… es demasiado texto, no se entiende
    # cuál es el problema». Un paso que no cambia ninguna decisión no es
    # transparencia: entierra al que sí la cambia.
    crudos = [
        _lente_casos(c, det),            # los casos, completos (controles)
        _lente_puedo_hacerlo(c, det),    # solo si el agente sabe arreglarlo
        _lente_salio_bien(c),            # qué falló (jobs)
        _lente_log_si_aplica(c),         # el log real (solo jobs)
        _lente_corrio(c),                # solo si NO corrió
        _lente_dato_fresco(c),           # solo si es contrato de frescura
        _lente_que_rompe(c),             # qué queda mal y dónde se corrige
        _lente_firma(c, por_slug),       # solo si matchea una lección
        _lente_historial(c, hist),       # solo si es un patrón
    ]
    crudos.append(_lente_arreglo(c))
    ps = [p for p in crudos if p]
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
    # ⚠️ **El veredicto de una puerta de SOLO LECTURA no puede hablar de aplicar.**
    # `_veredicto` está escrito para el alta de un bono, donde «se puede aplicar»
    # es la pregunta; acá no hay nada que aplicar y decía «se puede aplicar — no
    # falta ningún dato» arriba de una tarjeta que dice SOLO LECTURA. Dos frases
    # del mismo agente contradiciéndose en la misma pantalla.
    ver = _veredicto(ps)
    ver["puede_aplicar"] = False
    ver["puede_auto"] = False
    n_rev = ver["conteo"]["revisar"]
    ver["texto"] = ("sin hallazgos que revisar." if not n_rev else
                    f"{n_rev} cosa/s para mirar. El agente NO toca SALUD: "
                    "esto es un diagnóstico, la corrección la hace una persona.")
    return {"ok": True, "modo": "salud", "chequeo_id": cid,
            "titulo": c.get("titulo"), "familia": c.get("familia"),
            "estado": c.get("estado"), "motivo": c.get("motivo"),
            "chequeos": ps, "veredicto": ver}
