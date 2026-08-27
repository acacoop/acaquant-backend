"""api/services/salud.py — UN modelo único de CHEQUEO para toda la observabilidad.

Por qué existe (incidente 2026-08-07): el backfill de tenencias falló dos días
seguidos y nadie se enteró. No fue por falta de datos —había `job_runs`,
`controles_datos`, `latencia_endpoints`, el árbol de diagnóstico y hasta triage con
IA— sino porque estaban repartidos en seis pantallas que hay que ir a mirar, y
porque **ninguna respondía la pregunta que importaba**: la card de AuM estaba en
VERDE con el job muerto hacía 48 h, porque mostraba cómo salieron las corridas que
hubo, no si el sistema estaba sano.

De un job importan TRES preguntas y antes solo se miraba la segunda:

  1. ¿corrió CUANDO DEBÍA?   ← nadie hacía esta resta (el agujero del 07/08)
  2. ¿salió bien?            ← lo único que se miraba
  3. ¿dejó el DATO fresco?   ← no existía

Este módulo las unifica bajo un solo concepto —el CHEQUEO— y NO agrega una fuente
nueva: deriva todo de lo que ya está (`deploy/crontab.txt` vía `jobs_catalogo`,
`manager.job_runs`, y los contratos de frescura de acá abajo). Un cron nuevo aparece
solo; lo único que se declara a mano es qué dato tiene que quedar fresco, que es
justamente lo que ninguna máquina puede adivinar.

Servicio PURO (sin FastAPI).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from api.cache import cached, invalidate
from api.services._sql import _q
from api.services.jobs_catalogo import catalogo_jobs
from core.tz import AR_TZ, hora_ar

_log = logging.getLogger(__name__)

# Estados, de menor a mayor gravedad. El orden ES la prioridad de la pantalla.
OK, WARN, ERROR = "ok", "warn", "error"
_PESO = {ERROR: 0, WARN: 1, OK: 2}

# Margen sobre la hora esperada antes de cantar ATRASO. Un job que arranca 11:00 y
# tarda 22 minutos no está atrasado; uno que no corrió en 3 horas, sí.
MARGEN_ATRASO_H = 3
# Un job puede fallar y recuperarse en la próxima corrida. Solo interesa el ÚLTIMO
# estado, pero si el último run es viejísimo el job está abandonado, no "ok".
ABANDONO_DIAS = 8


# ── CONTRATOS DE FRESCURA ────────────────────────────────────────────────────
#
# Lo ÚNICO que se declara a mano, y a propósito: el sistema no puede adivinar que
# `portafolio.tenencia` tiene que tener el último día hábil. Es un chequeo sobre el
# RESULTADO, no sobre el proceso — agarra el fallo aunque el job mienta, aunque
# nadie lo haya instrumentado, o aunque alguien borre datos por afuera.
#
# Arranca con lo crítico. Sumar una tabla es UNA línea.
CONTRATOS: list[dict[str, Any]] = [
    {"id": "dato:portafolio.tenencia", "titulo": "Tenencias diarias (AuM)",
     "tabla": "portafolio.tenencia", "columna": "fecha", "max_dias_habiles": 2,
     "detalle": "alimenta AuM, Tenencia Valorizada y Títulos en Alquiler"},
    # OJO: la fecha del boleto es `concertacion`, no `fecha` (sql/schema.sql:251).
    # Se escribió mal la primera vez y el chequeo salía "no pude consultar la tabla":
    # un contrato roto se ve casi igual que un dato atrasado, y manda a buscar el
    # problema al lugar equivocado.
    {"id": "dato:operaciones.operaciones", "titulo": "Boletos de operaciones",
     "tabla": "operaciones.operaciones", "columna": "concertacion", "max_dias_habiles": 2,
     "detalle": "alimenta MOVIMIENTOS y el Tablero Comercial"},
    {"id": "dato:mercado.snapshots_cierre", "titulo": "Cierre de renta fija",
     "tabla": "mercado.snapshots_cierre", "columna": "fecha", "max_dias_habiles": 2,
     "detalle": "cierre por bono; sin esto las vistas se quedan en el día anterior"},
    {"id": "dato:mercado.precios_acciones", "titulo": "Velas diarias de ADRs",
     "tabla": "mercado.precios_acciones", "columna": "fecha", "max_dias_habiles": 3,
     "detalle": "alimenta el Scanner y las ZONAS de Trading"},
    # ⚠️ `macro.series_macro` NO se chequea entera: se chequea SERIE POR SERIE.
    #
    # El contrato original miraba `MAX(fecha)` de la tabla, que contiene DOLAR, CER,
    # BADLAR, TAMAR, RiesgoPais e Inflación mezcladas — así que **respondía por la
    # serie MÁS FRESCA**. Con el dólar actualizándose todos los días, el chequeo
    # salía VERDE aunque el CER estuviera congelado hace meses (detectado
    # 2026-08-17 por el user: *«es cualquiera que no lo detecte»*).
    #
    # Es el mismo anti-patrón que ya apareció cinco veces en este proyecto: **un
    # agregado que tapa el detalle**. Un MAX sobre una tabla con N series
    # independientes no dice nada sobre ninguna de ellas.
    #
    # Se declaran EXPLÍCITAS y no agrupando por `serie` a propósito: agrupar
    # generaría un chequeo rojo por cada serie mensual, discontinuada o
    # experimental que alguien haya escrito alguna vez. Sumar una es UNA línea.
    *[{"id": f"dato:macro.series_macro:{serie}",
       "titulo": f"Serie macro {serie}",
       "tabla": "macro.series_macro", "columna": "fecha",
       "filtro": {"columna": "serie", "valor": serie},
       "max_dias_habiles": tope, "detalle": detalle}
      for serie, tope, detalle in (
          # El CER es FORWARD (`jobs.bcra --today` pide hoy+21d), así que un CER
          # sano tiene el máximo por DELANTE de hoy. Si quedó atrás, ya falla.
          ("CER", 3, "divisor de TODOS los bonos CER: valuación, TEA y breakevens"),
          ("DOLAR", 3, "A3500 del BCRA: pesifica el AuM y las series de ACA"),
          ("BADLAR", 5, "tasa de referencia de la curva BADLAR"),
          ("TAMAR", 5, "tasa de referencia de los bonos TAMAR"),
      )],
]


def _ahora() -> datetime:
    return datetime.now(UTC)


# ── ¿Cuándo tendría que haber corrido? ───────────────────────────────────────

def _match_campo(campo: str, valor: int) -> bool:
    """Un campo de cron (`*`, `5`, `1-5`, `*/4`, `1,3`) contra un valor."""
    campo = campo.strip()
    if campo in ("*", "?"):
        return True
    for parte in campo.split(","):
        parte = parte.strip()
        if parte.startswith("*/"):
            try:
                if valor % int(parte[2:]) == 0:
                    return True
            except ValueError:
                continue
        elif "-" in parte:
            try:
                a, b = (int(x) for x in parte.split("-", 1))
                if a <= valor <= b:
                    return True
            except ValueError:
                continue
        else:
            try:
                if int(parte) == valor:
                    return True
            except ValueError:
                continue
    return False


def _matchea(cron: str, t: datetime) -> bool:
    """¿El cron de 5 campos dispara en el minuto `t` (UTC)?"""
    campos = cron.split()
    if len(campos) < 5:
        return False
    minuto, hora, dom, mes, dow = campos[:5]
    # cron: domingo puede ser 0 o 7; Python: lunes=0..domingo=6.
    dow_cron = (t.weekday() + 1) % 7
    return (_match_campo(minuto, t.minute) and _match_campo(hora, t.hour)
            and _match_campo(dom, t.day) and _match_campo(mes, t.month)
            and (_match_campo(dow, dow_cron)
                 or (dow_cron == 0 and _match_campo(dow, 7))))


def ultima_ejecucion_esperada(cron: str, ahora: datetime | None = None) -> datetime | None:
    """El último minuto en que ese cron TENÍA que dispararse.

    Se busca hacia atrás minuto a minuto (tope 8 días). Es fuerza bruta, pero son
    ~11.500 comparaciones de enteros: microsegundos, y evita meter una dependencia
    nueva (croniter) para una cuenta que se hace en 20 líneas.
    """
    t = (ahora or _ahora()).replace(second=0, microsecond=0)
    for _ in range(ABANDONO_DIAS * 24 * 60):
        if _matchea(cron, t):
            return t
        t -= timedelta(minutes=1)
    return None


# ── Chequeos de JOBS (¿corrió cuando debía? ¿salió bien?) ────────────────────

def _iso(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except Exception:
        return None


def _edad(cuando: datetime | None, ahora: datetime) -> str:
    """« · hace 3 días» cuando el hecho NO es de hoy. Vacío si es de hoy.

    Se calcula en DÍAS DE CALENDARIO ARGENTINO y no en horas de reloj: lo que el
    que mira quiere saber es «¿esto es de hoy o de otro día?», y 20 horas puede
    ser cualquiera de las dos. Un job de anoche a las 23 y uno de esta mañana a
    las 7 distan 8 horas y son días distintos; uno de ayer a las 8 y uno de hoy
    a las 4 distan 20 y también.
    """
    if cuando is None:
        return ""
    d = (ahora.astimezone(AR_TZ).date() - cuando.astimezone(AR_TZ).date()).days
    if d <= 0:
        return ""
    return " · hace 1 día" if d == 1 else f" · hace {d} días"


def _chequeo_job(cron: dict, ahora: datetime) -> dict:
    """Un cron del crontab → un chequeo. El estado sale de las dos preguntas."""
    label = cron.get("label") or ",".join(cron.get("modules") or []) or "?"
    runs = cron.get("runs") or []
    ultimos = [(_iso((r.get("ultimo") or {}).get("started_at")), r) for r in runs]
    ultimos = [(t, r) for t, r in ultimos if t]
    ultimo_t = max((t for t, _ in ultimos), default=None)
    # ⚠️ **`ahora` SE PASA** (fix 2026-08-19). La función recibía un reloj y
    # después leía OTRO (el del sistema, vía el default de
    # `ultima_ejecucion_esperada`). En prod los dos coinciden —`evaluar()` pasa
    # `_ahora()`— así que el bug estaba dormido; lo que sí rompía era el test
    # fundacional de este módulo (el job en `ok` sin correr hace dos días), que
    # congela el reloj y comparaba una esperada de HOY contra un `ahora` de agosto.
    #
    # Es la misma forma que el bug del blob y la columna (§0.u): **dos
    # representaciones del mismo dato sin árbitro**. Mientras coinciden no falla
    # nada, y el día que se separan cada mitad sigue siendo coherente por su
    # cuenta. Acá el árbitro es el parámetro: si te pasan un reloj, es ESE.
    esperada = ultima_ejecucion_esperada(cron.get("schedule") or "", ahora)

    # (1) ¿corrió cuando debía? — la resta que nadie hacía.
    atrasado = False
    if esperada is not None:
        limite = esperada + timedelta(hours=MARGEN_ATRASO_H)
        atrasado = ahora > limite and (ultimo_t is None or ultimo_t < esperada)

    # (2) ¿salió bien? — el peor status entre los módulos del cron.
    estados = [(r.get("ultimo") or {}).get("status") for _, r in ultimos]
    fallo = any(s == "error" for s in estados)
    parcial = any(s == "partial" for s in estados)

    # ⚠️ **LA FRESCURA DEL AVISO** (user, 2026-08-19): *«si este job dio error hace
    # 22 h… ir al día siguiente y validar: ¿funcionó bien después de su horario de
    # arranque? Si ya dio OK, no volver a avisar. Los avisos y las alertas no
    # pueden quedar desactualizados.»*
    #
    # El motivo decía **«la última corrida falló»** sin decir CUÁNDO fue esa
    # corrida, y la pantalla mostraba al lado «hace 22 h» —que es cuándo apareció
    # el aviso, no cuándo se verificó—. Las dos cosas juntas se leen como «esto es
    # de anteayer», y son preguntas distintas que hay que poder separar:
    #
    #     ¿corrió después de lo que debía?   → si NO, el problema es el scheduler
    #     ¿esa corrida salió bien?           → si SÍ, no hay aviso que dar
    #
    # Ahora el motivo lleva la HORA de la corrida que lo justifica, así el que
    # mira puede decidir sin abrir nada si el aviso es de hoy o quedó viejo. Y
    # `corrio_despues` deja explícito lo que antes había que deducir.
    # ⚠️ **HORA ARGENTINA, y sin la etiqueta «UTC»** (§0.br). Esto decía
    # `ultimo_t.strftime(...)` sobre un datetime en UTC y el mensaje agregaba
    # «UTC» al final: la mesa leía «16:30» cuando acá eran las 13:30. Pedirle a
    # alguien que reste tres horas para ubicar un hecho es garantizar que lo
    # ubique mal. El formateo vive UNA vez, en `core.tz.hora_ar`.
    ult_txt = hora_ar(ultimo_t)
    corrio_despues = bool(ultimo_t and esperada and ultimo_t >= esperada)
    # ⚠️⚠️ **«HACE CUÁNTO» VA EN LA FRASE** (user, 2026-08-24, un lunes al
    # mediodía viendo cuatro avisos fechados el viernes 21/08): *«cosas del
    # 21/08, dios mío… te vengo diciendo hace horas que no quiero ver cosas
    # viejas»*.
    #
    # La frase decía «la corrida de 21/08 19:00 falló» y al lado la pantalla
    # ponía la hora en que el AGENTE miró (12:04 de hoy). Las dos son ciertas y
    # juntas mienten: se leen como un problema de recién. Y el dato que faltaba
    # es el que decide si hay que hacer algo AHORA — **un job L-V que falló el
    # viernes y todavía no le tocó correr de nuevo no es un incendio de hoy**,
    # es una falla que sigue pendiente y que se va a repetir esta noche.
    #
    # No se esconde: se FECHA. Esconderla sería la mentira opuesta.
    edad = _edad(ultimo_t, ahora)
    if not cron.get("instrumentado") and ultimo_t is None:
        # Sin JobRunLogger no se puede saber nada. No es rojo, pero tampoco verde:
        # es un punto ciego y tiene que verse como tal.
        estado, motivo = WARN, "sin instrumentar: no registra corridas"
    elif fallo:
        estado, motivo = ERROR, f"la corrida de {ult_txt} falló{edad}"
    elif atrasado:
        esp = hora_ar(esperada, vacio="?")
        estado, motivo = ERROR, f"debía correr {esp} y la última fue {ult_txt}"
    elif parcial:
        # ⚠️⚠️ **UN `partial` NO ES UN JOB ROTO** (mismo reporte del user:
        # *«cuando por ejemplo cierre_canje ESTÁ FUNCIONANDO, el otro también»*).
        #
        # Y tenía razón, literalmente: `partial` es el status que pone
        # `JobRunLogger` cuando el job **terminó** y en el camino llamó a
        # `run.error()` una o más veces. O sea que corrió, hizo su trabajo y
        # dejó anotado algo no fatal. Tratarlo como problema convertía un
        # apunte del propio job en trabajo pendiente para una persona — y
        # encima uno que no se puede cerrar, porque no hay nada que arreglar.
        #
        # Pasa a ser **AVISO**: se sigue viendo (con la fecha), se sigue
        # contando, y deja de pedir trabajo. La diferencia la hace `parcial`,
        # que viaja en el chequeo para que el detector no tenga que adivinarlo
        # leyendo el texto del motivo — que es como ya se rompió esto antes.
        estado, motivo = WARN, (f"la corrida de {ult_txt} terminó bien pero "
                                f"dejó errores anotados{edad}")
    else:
        estado, motivo = OK, "al día"

    resumen = next((( r.get("ultimo") or {}).get("resumen") for _, r in ultimos
                    if (r.get("ultimo") or {}).get("resumen")), "")
    return {
        # ⚠️⚠️ **LOS ERRORES DE VERDAD, EN EL CHEQUEO** (2026-08-24). El agente
        # ya sabía traducir un error de log a castellano (`_motivo_salud` →
        # `av_agent_errores.explicar`) y **nunca lo hacía para un job**: esa
        # función busca los errores en `c["corridas"]` y este chequeo no traía
        # ninguna. O sea que la fila decía siempre la misma frase mecánica —
        # «terminó con errores parciales»— sin decir CUÁL, que es lo único que
        # sirve. El user: *«es todo muy mecánico, no va»*.
        #
        # El dato ya estaba a un campo de distancia: `jobs_catalogo` guarda el
        # primer error en `ultimo.resumen`. Se pasa con la forma que
        # `_primer_error` ya sabe leer, así ninguna de las dos puntas cambia.
        "corridas": [{"errors": [resumen]}] if resumen and (fallo or parcial) else [],
        # **Terminó, pero dejó apuntes.** Lo lee el detector para no meterlo en
        # la lista de trabajo. Va como campo y no como texto en el motivo:
        # decidir mirando una frase es cómo se rompen estas cosas (REGLA #9).
        "parcial": bool(parcial and not fallo and not atrasado),
        "id": f"job:{label}",
        "familia": "job",
        "titulo": label,
        "estado": estado,
        "motivo": motivo,
        "evidencia": resumen or motivo,
        "schedule": cron.get("schedule"),
        "ultimo_at": ultimo_t.isoformat() if ultimo_t else None,
        "esperada_at": esperada.isoformat() if esperada else None,
        # **¿La corrida que estamos juzgando es posterior a su horario?** Sin
        # esto, «falló» y «no corrió» se ven iguales desde afuera y se atienden
        # distinto: uno es un bug adentro del job, el otro es el scheduler.
        "corrio_despues": corrio_despues,
        "modulos": [r.get("modulo") for r in runs],
    }


# ── Chequeos de DATOS (¿el resultado quedó fresco?) ──────────────────────────

def _dias_habiles_atras(desde: datetime, hasta: datetime) -> int:
    """Días hábiles entre dos fechas (sin feriados: acá alcanza con L-V)."""
    d, n = desde.date(), 0
    while d < hasta.date():
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _chequeo_dato(c: dict, ahora: datetime) -> dict:
    base = {"id": c["id"], "familia": "dato", "titulo": c["titulo"],
            "detalle": c.get("detalle", ""), "tabla": c["tabla"]}
    filtro = c.get("filtro") or {}
    try:
        if filtro:
            # El VALOR va parametrizado; tabla y columna salen de CONTRATOS, que es
            # config nuestra y no entrada de usuario.
            rows = _q(f"SELECT MAX({c['columna']}) AS ultima FROM {c['tabla']} "
                      f"WHERE {filtro['columna']} = %(v)s", {"v": filtro["valor"]})
        else:
            rows = _q(f"SELECT MAX({c['columna']}) AS ultima FROM {c['tabla']}")
    except Exception as e:
        return {**base, "estado": WARN, "motivo": "no pude consultar la tabla",
                "evidencia": f"{type(e).__name__}: {e}", "ultimo_at": None}
    ultima = rows[0]["ultima"] if rows else None
    if ultima is None:
        return {**base, "estado": ERROR,
                "motivo": ("no hay ni una fila de esta serie" if filtro
                           else "la tabla está vacía"),
                "evidencia": (f"{c['tabla']} no tiene filas con "
                              f"{filtro['columna']} = {filtro['valor']!r}" if filtro
                              else f"{c['tabla']} no tiene ninguna fila"),
                "ultimo_at": None}
    ult_dt = datetime(ultima.year, ultima.month, ultima.day, tzinfo=UTC) \
        if not isinstance(ultima, datetime) else ultima
    atraso = _dias_habiles_atras(ult_dt, ahora)
    tope = int(c.get("max_dias_habiles", 2))
    estado = OK if atraso <= tope else ERROR
    return {**base, "estado": estado,
            "motivo": ("al día" if estado == OK
                       else f"el último dato es de hace {atraso} días hábiles"),
            "evidencia": (f"{c['tabla']}.{c['columna']} máximo = {ultima}"
                          + (f" para {filtro['columna']} = {filtro['valor']!r}"
                             if filtro else "")
                          + f" (tolerancia: {tope} días hábiles)"),
            "ultimo_at": ult_dt.isoformat()}


# ── Evaluación completa ──────────────────────────────────────────────────────

# ── Chequeos de CONTROLES de datos (calidad del negocio) ─────────────────────
#
# La tab CONTROLES vivía aparte con un "!146" que nadie podía atender — un contador
# así es ruido, no señal. Se absorbe como UNA FAMILIA MÁS de chequeos: mismo modelo,
# misma pantalla, mismo silenciado, mismo historial. La regla del rediseño es que NO
# haya observabilidad fuera de SALUD.
#
# Cada control_id es su propio chequeo, así se puede silenciar uno sin perder el
# resto: hoy hay controles accionables (una cuenta sin segmentar) mezclados con
# ruido que nadie va a mirar, y el contador único los tapaba a todos.

def _chequeo_ia() -> dict | None:
    """**LO ÚNICO DE LA OBSERVABILIDAD DE IA QUE VALE LA PENA MIRAR** — y por eso
    es un chequeo y no una pantalla.

    El user, mirando el panel de Manager → OBSERVABILIDAD → IA (2026-08-19):
    *«¿qué sentido tiene toda esta parte de IA ahora? Más allá de saber el
    crédito disponible —que tampoco es relevante— el resto ocupa espacio nada
    más… estoy haciendo limpieza de cosas que no servían para absolutamente nada
    y no tenían uso alguno»*. Tenía razón: el historial de 874 llamadas no lo
    abrió nunca, y un tablero que hay que ir a abrir es un tablero que no se abre
    — el MISMO argumento que fundó SALUD.

    Pero de todo ese panel hay DOS números que sí importan, y no porque sean
    lindos de ver: porque si se rompen, **nadie se entera**.

      · el GASTO — un bug que llame al modelo en loop quema el presupuesto de un
        día en minutos, y el síntoma es que la IA deja de contestar «sin razón»;
      · los ERRORES — llamadas que fallan calladas dejan al agente mudo con la
        pantalla en verde, que es exactamente el modo de falla que SALUD existe
        para cazar.

    Así que se invierte: en vez de una pantalla que hay que visitar, **un chequeo
    que te busca**. Verde mientras el gasto y los errores estén en rango; si no,
    aparece solo en el agente como cualquier otro problema.

    Umbral del gasto en **80%** y no en 100: avisar cuando ya no queda
    presupuesto no sirve de nada — para entonces la IA ya está caída.
    """
    try:
        from api.services import ia_obs
        d = ia_obs.observabilidad(dias=1, limit=1)
    except Exception:
        _log.warning("salud: no pude leer la observabilidad de IA", exc_info=True)
        return None
    hoy = d.get("hoy") or {}
    usados = int(hoy.get("tokens_total") or 0)
    tope = int(hoy.get("presupuesto_dia") or 0)
    errores = int(hoy.get("errores") or 0)
    llamadas = int(hoy.get("llamadas") or 0)
    pct = round(100 * usados / tope, 1) if tope else 0.0

    if tope and pct >= 80:
        estado, motivo = ERROR, f"{pct}% del presupuesto de IA consumido hoy"
    elif errores and llamadas and errores / llamadas >= 0.5:
        estado, motivo = ERROR, f"{errores} de {llamadas} llamadas de IA fallaron hoy"
    elif errores:
        estado, motivo = WARN, f"{errores} llamada/s de IA fallaron hoy"
    else:
        estado, motivo = OK, (f"{llamadas} llamada/s, {pct}% del presupuesto"
                              if llamadas else "sin llamadas hoy")
    return {
        "id": "ia:gateway", "familia": "dato", "titulo": "Gateway de IA",
        "estado": estado, "motivo": motivo,
        "evidencia": f"{usados:,} tokens de {tope:,} · {llamadas} llamadas · "
                     f"{errores} errores".replace(",", "."),
        "detalle": "si se agota el presupuesto o fallan las llamadas, el agente "
                   "se queda mudo sin avisar",
        "tabla": "ia.trazas",
    }


def _titulo_control(cid: str) -> str:
    """El nombre humano del control, del catálogo que ya lo declara.

    Cae al id con guiones bajos convertidos SOLO si el control no está en el
    catálogo — que puede pasar con uno viejo cuya fila todavía vive en la tabla.
    Es la degradación correcta: un nombre feo es mejor que una fila sin nombre.
    """
    try:
        from jobs.controles_datos import CONTROLES
        for c in CONTROLES:
            if c.id == cid:
                return c.titulo
    except Exception:      # pragma: no cover - la pantalla no se cae por esto
        _log.warning("salud: no pude leer el catálogo de controles", exc_info=True)
    return cid.replace("_", " ")


def _chequeos_controles() -> list[dict]:
    try:
        from api.services.controles_sql import listar_controles
        data = listar_controles(incluir_resueltos_dias=0)
    except Exception:
        _log.warning("salud: no pude leer los controles de datos", exc_info=True)
        return []
    out: list[dict] = []
    for cid, grupo in (data.get("controles") or {}).items():
        activos = grupo.get("activos") or []
        if not activos:
            continue
        # Los controles NO son ERROR: son deuda de datos, no el sistema caído. Un
        # comitente sin segmentar no rompe nada — hay que corregirlo, no correr.
        ejemplos = " · ".join(str(a.get("detalle") or a.get("item"))[:60]
                              for a in activos[:3])
        out.append({
            "id": f"control:{cid}",
            "familia": "control",
            # ⚠️⚠️ **EL NOMBRE LEGIBLE YA EXISTÍA Y NADIE LO LEÍA** (§0.bq).
            # Acá decía `cid.replace("_", " ")`, o sea que la pantalla mostraba
            # el ID del control («comitentes sin nivel1») cuando el catálogo
            # tiene, desde siempre, «Comitentes activos sin nivel 1». En una
            # columna angosta el ID se cortaba —`control:comitentes_sin_nive…`—
            # y la fila dejaba de decir qué es.
            #
            # No hace falta un LLM para esto: el texto ya está escrito por quien
            # dio de alta el control. Se lee del catálogo, que es la fuente.
            "titulo": _titulo_control(cid),
            "estado": WARN,
            "motivo": f"{len(activos)} anomalía{'s' if len(activos) != 1 else ''} sin resolver",
            "evidencia": ejemplos or "(sin detalle)",
            "detalle": "control de calidad de datos",
            "n": len(activos),
            "ultimo_at": data.get("ultima_corrida"),
        })
    return out


def _jobs_sin_duplicar(chequeos: list[dict]) -> list[dict]:
    """UN job es UN chequeo, aunque tenga varias líneas de cron.

    El caso que lo motivó fue `av_agent_live` (ya borrado): corría con dos
    entradas del crontab —una para el arranque de la rueda y otra para el resto—
    y como el id es `job:<label>` la pantalla mostraba **la misma fila dos veces,
    idéntica**. El job no está más; la regla sí, porque el patrón de un job con
    dos líneas de cron sigue existiendo.
    Un tablero que repite un problema hace pensar que son dos, y en un listado de
    17 eso no es cosmético: cambia la cuenta.

    Se queda el estado MENOS alarmante de los duplicados, no el peor: dos crons
    son dos ventanas del mismo job, así que haber corrido en cualquiera de ellas
    significa que corrió. Quedarse con la ventana más estricta reportaría atraso
    de algo que ya se ejecutó. Los horarios se juntan para que la fila siga
    diciendo la verdad completa.
    """
    por_id: dict[str, dict] = {}
    for c in chequeos:
        prev = por_id.get(c["id"])
        if prev is None:
            por_id[c["id"]] = c
            continue
        mejor, otro = ((c, prev) if _PESO.get(c["estado"], 3) < _PESO.get(prev["estado"], 3)
                       else (prev, c))
        horarios = [h for h in (mejor.get("schedule"), otro.get("schedule")) if h]
        por_id[c["id"]] = {**mejor, "schedule": "  ·  ".join(dict.fromkeys(horarios))}
    return list(por_id.values())


def evaluar() -> list[dict]:
    """TODOS los chequeos, peor primero. Es la única función que arma el estado."""
    ahora = _ahora()
    out: list[dict] = []
    try:
        out.extend(_jobs_sin_duplicar(
            [_chequeo_job(cron, ahora)
             for cron in (catalogo_jobs().get("jobs") or [])]))
    except Exception:
        _log.warning("salud: no pude evaluar los jobs", exc_info=True)
    for c in CONTRATOS:
        try:
            out.append(_chequeo_dato(c, ahora))
        except Exception:
            _log.warning("salud: no pude evaluar %s", c["id"], exc_info=True)
    ia = _chequeo_ia()
    if ia:
        out.append(ia)
    out.extend(_chequeos_controles())
    out.sort(key=lambda c: (_PESO.get(c["estado"], 3), c["titulo"]))
    return out


def resumen() -> dict:
    """Lo que necesita la pantalla: el veredicto de UNA línea + el detalle."""
    chequeos = evaluar()
    conteo = {OK: 0, WARN: 0, ERROR: 0}
    for c in chequeos:
        conteo[c["estado"]] = conteo.get(c["estado"], 0) + 1
    return {
        "veredicto": (ERROR if conteo[ERROR] else WARN if conteo[WARN] else OK),
        "conteo": conteo,
        "chequeos": chequeos,
        "evaluado_at": _ahora().isoformat(),
    }


# ──────────────────────────────────────────────────────────────────────────────
# PERSISTENCIA: transiciones, alertas y vistos.
#
# El estado ACTUAL no se guarda (se evalúa en vivo). Lo que se persiste es lo que
# no se puede recalcular más tarde: CUÁNDO cambió y con qué evidencia — porque una
# vez que el job vuelve a correr, el motivo de la falla ya no está en ningún lado.
# Eso es "el log" de cada chequeo.
# ──────────────────────────────────────────────────────────────────────────────

_T_EVENTOS = "manager.salud_eventos"
_T_CONFIG = "manager.salud_config"
_T_VISTOS = "manager.salud_vistos"


def _exec_salud(sql: str, params: dict) -> int:
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        n = cur.rowcount
        conn.commit()
    return n


def _estados_previos() -> dict[str, str]:
    """Último estado registrado de cada chequeo (para detectar la transición)."""
    try:
        rows = _q(f"SELECT DISTINCT ON (chequeo_id) chequeo_id, a FROM {_T_EVENTOS} "
                  "ORDER BY chequeo_id, at DESC")
    except Exception:
        _log.warning("salud: no pude leer los estados previos", exc_info=True)
        return {}
    return {r["chequeo_id"]: r["a"] for r in rows}


def config() -> dict[str, dict]:
    """{chequeo_id: {alertar, nota}}. Lo que no está configurado ALERTA por default:
    un chequeo nuevo tiene que avisar sin que nadie lo dé de alta."""
    try:
        rows = _q(f"SELECT chequeo_id, alertar, nota FROM {_T_CONFIG}")
    except Exception:
        return {}
    return {r["chequeo_id"]: {"alertar": bool(r["alertar"]), "nota": r["nota"]}
            for r in rows}


def set_alerta(chequeo_id: str, alertar: bool, actor: str, nota: str = "") -> dict:
    """Silenciar/reactivar un chequeo. Silenciar NO lo saca de la pantalla — sigue
    rojo en la lista; lo único que deja de hacer es abrir el modal."""
    cid = (chequeo_id or "").strip()
    if not cid:
        raise ValueError("falta el chequeo")
    _exec_salud(
        f"INSERT INTO {_T_CONFIG} (chequeo_id, alertar, nota, actualizado_por, "
        "actualizado_at) VALUES (%(c)s, %(a)s, %(n)s, %(p)s, now()) "
        "ON CONFLICT (chequeo_id) DO UPDATE SET alertar = EXCLUDED.alertar, "
        "nota = EXCLUDED.nota, actualizado_por = EXCLUDED.actualizado_por, "
        "actualizado_at = EXCLUDED.actualizado_at",
        {"c": cid, "a": bool(alertar), "n": (nota or "").strip() or None,
         "p": (actor or "").lower() or None})
    # `panel()` está cacheado 30s: sin esto, silenciar un chequeo no se vería
    # hasta que venciera el TTL y parecería que el botón no hizo nada.
    invalidate("panel")
    return {"chequeo_id": cid, "alertar": bool(alertar)}


def sincronizar() -> list[dict]:
    """Evalúa y registra SOLO las transiciones. Idempotente: si nada cambió de
    estado, no escribe una fila. Devuelve las transiciones nuevas."""
    chequeos = evaluar()
    previos = _estados_previos()
    nuevas: list[dict] = []
    for c in chequeos:
        antes = previos.get(c["id"])
        if antes == c["estado"]:
            continue
        try:
            _exec_salud(
                f"INSERT INTO {_T_EVENTOS} (chequeo_id, familia, titulo, de, a, "
                "motivo, evidencia) VALUES (%(c)s, %(f)s, %(t)s, %(de)s, %(a)s, "
                "%(m)s, %(e)s)",
                {"c": c["id"], "f": c.get("familia"), "t": c.get("titulo"),
                 "de": antes, "a": c["estado"], "m": c.get("motivo"),
                 "e": str(c.get("evidencia") or "")[:2000]})
            nuevas.append({**c, "de": antes})
        except Exception:
            _log.warning("salud: no pude registrar la transición de %s", c["id"],
                         exc_info=True)
    return nuevas


def pendientes(email: str, limite: int = 20) -> list[dict]:
    """Transiciones A PROBLEMA que este admin todavía no vio y que no están
    silenciadas. Es lo que dispara el modal.

    Solo empeoramientos: que algo se ARREGLE no justifica interrumpir a nadie.
    """
    e = (email or "").lower().strip()
    if not e:
        return []
    try:
        rows = _q(
            f"SELECT ev.id, ev.chequeo_id, ev.familia, ev.titulo, ev.de, ev.a, "
            f"ev.motivo, ev.evidencia, ev.at FROM {_T_EVENTOS} ev "
            f"LEFT JOIN {_T_VISTOS} v ON v.evento_id = ev.id AND v.email = %(e)s "
            f"LEFT JOIN {_T_CONFIG} cf ON cf.chequeo_id = ev.chequeo_id "
            "WHERE v.evento_id IS NULL AND ev.a <> 'ok' "
            "AND COALESCE(cf.alertar, true) "
            # PERSISTENCIA: solo interrumpe lo que ya se demostró que no se arregla
            # solo. Un job que falla y se recupera en la corrida siguiente no abre
            # ningún modal — si avisara de cada hipo, en una semana lo cerrás sin leer.
            "AND ev.at < now() - make_interval(mins => %(persis)s) "
            # Y que siga roto AHORA: si hubo un evento posterior, ya cambió de estado.
            f"AND NOT EXISTS (SELECT 1 FROM {_T_EVENTOS} e2 "
            "                 WHERE e2.chequeo_id = ev.chequeo_id AND e2.at > ev.at) "
            "ORDER BY (ev.a = 'error') DESC, ev.at DESC LIMIT %(lim)s",
            {"e": e, "lim": max(1, min(int(limite), 100)),
             "persis": PERSISTENCIA_MIN})
    except Exception:
        _log.warning("salud: no pude leer los pendientes", exc_info=True)
        return []
    return [{"id": int(r["id"]), "chequeo_id": r["chequeo_id"],
             "familia": r["familia"], "titulo": r["titulo"], "de": r["de"],
             "a": r["a"], "motivo": r["motivo"], "evidencia": r["evidencia"],
             "at": r["at"].isoformat() if r["at"] else None} for r in rows]


def marcar_vistos(email: str, ids: list[int] | None = None) -> dict:
    """`ids` vacío = marcar TODO lo pendiente (el botón 'entendido' del modal)."""
    e = (email or "").lower().strip()
    if not e:
        raise ValueError("falta el email")
    if ids:
        n = _exec_salud(
            f"INSERT INTO {_T_VISTOS} (email, evento_id) "
            "SELECT %(e)s, x FROM unnest(%(ids)s::bigint[]) AS x "
            "ON CONFLICT DO NOTHING", {"e": e, "ids": [int(i) for i in ids]})
    else:
        n = _exec_salud(
            f"INSERT INTO {_T_VISTOS} (email, evento_id) "
            f"SELECT %(e)s, id FROM {_T_EVENTOS} WHERE a <> 'ok' "
            "ON CONFLICT DO NOTHING", {"e": e})
    invalidate("panel")   # `pendientes` vive adentro de panel() — ver set_alerta
    return {"vistos": n}


def historial(chequeo_id: str = "", limite: int = 50) -> list[dict]:
    """El LOG de un chequeo (o de todo): cuándo se rompió, cuándo volvió."""
    try:
        rows = _q(
            f"SELECT id, chequeo_id, familia, titulo, de, a, motivo, evidencia, at "
            f"FROM {_T_EVENTOS} WHERE (%(c)s = '' OR chequeo_id = %(c)s) "
            "ORDER BY at DESC LIMIT %(lim)s",
            {"c": (chequeo_id or "").strip(), "lim": max(1, min(int(limite), 500))})
    except Exception:
        return []
    return [{"id": int(r["id"]), "chequeo_id": r["chequeo_id"], "familia": r["familia"],
             "titulo": r["titulo"], "de": r["de"], "a": r["a"], "motivo": r["motivo"],
             "evidencia": r["evidencia"],
             "at": r["at"].isoformat() if r["at"] else None} for r in rows]


@cached(ttl=30)
def panel(email: str = "") -> dict:
    """TODO lo que necesita la pantalla, en UNA llamada: veredicto, chequeos (con su
    marca de silenciado) y lo pendiente de ver por este admin.

    **Por qué está cacheado** (medido 2026-08-11): el layout monta DOS componentes
    que piden esto al abrir CUALQUIER pantalla — `SaludBoton` pide `/salud` y
    `SaludAlertasModal` pide `/salud?solo_problemas=true`. Los dos terminan acá con
    el mismo `email`, así que se computaba dos veces lo mismo: **1450 + 1432 ms
    medidos en el browser, 2,9 segundos por carga de página** que ningún usuario
    pidió. Con el TTL, la segunda sale gratis.

    El TTL de 30s es corto a propósito: los dos componentes pollean cada 5 minutos,
    así que el poll SIEMPRE cae fuera del cache y `sincronizar()` sigue corriendo con
    la misma frecuencia de antes. Lo único que se ahorra son las llamadas
    simultáneas — las dos del montaje, y las de varios admins entrando a la vez.

    Las mutaciones (`set_alerta`, `marcar_vistos`) invalidan esta entrada, así que
    silenciar un chequeo o marcar algo como visto se refleja en el acto.
    """
    nuevas = sincronizar()          # registra transiciones antes de responder
    r = resumen()
    cfg = config()
    for c in r["chequeos"]:
        c["alertar"] = cfg.get(c["id"], {}).get("alertar", True)
        c["nota"] = cfg.get(c["id"], {}).get("nota")
    r["pendientes"] = pendientes(email)
    r["transiciones_nuevas"] = len(nuevas)
    return r


# ──────────────────────────────────────────────────────────────────────────────
# REACTIVO, PERO NO RUIDOSO: solo lo que PERSISTE
#
# El pedido fue "que aparezca cuando pase algo grave y ya demostremos que no se
# soluciona". Avisar al primer error no sirve: los jobs fallan y se recuperan solos
# en la corrida siguiente, y un modal que salta por cada hipo se cierra sin leer.
#
# Un problema se considera CONFIRMADO cuando sigue roto después de PERSISTENCIA_MIN.
# Es exactamente el caso del viernes: falló a las 11:00 y a las 11:30 seguía fallando
# — ahí ya no era un hipo, era un incidente.
# ──────────────────────────────────────────────────────────────────────────────

PERSISTENCIA_MIN = 30       # minutos rotos antes de confirmar (y recién ahí, avisar)


def confirmados(limite: int = 20) -> list[dict]:
    """Chequeos rotos que YA se demostró que no se arreglan solos.

    Devuelve la transición a problema más reciente de cada chequeo que sigue mal
    y que lleva más de `PERSISTENCIA_MIN` minutos así.
    """
    try:
        rows = _q(
            f"SELECT DISTINCT ON (ev.chequeo_id) ev.id, ev.chequeo_id, ev.familia, "
            f"ev.titulo, ev.de, ev.a, ev.motivo, ev.evidencia, ev.at, "
            "EXTRACT(EPOCH FROM (now() - ev.at))/60 AS minutos "
            f"FROM {_T_EVENTOS} ev "
            f"LEFT JOIN {_T_CONFIG} cf ON cf.chequeo_id = ev.chequeo_id "
            "WHERE ev.a <> 'ok' AND COALESCE(cf.alertar, true) "
            "  AND ev.at < now() - make_interval(mins => %(min)s) "
            # Que no haya un evento POSTERIOR devolviéndolo a ok: si volvió, se arregló.
            f"  AND NOT EXISTS (SELECT 1 FROM {_T_EVENTOS} e2 "
            "                   WHERE e2.chequeo_id = ev.chequeo_id AND e2.at > ev.at) "
            "ORDER BY ev.chequeo_id, ev.at DESC LIMIT %(lim)s",
            {"min": PERSISTENCIA_MIN, "lim": max(1, min(int(limite), 100))})
    except Exception:
        _log.warning("salud: no pude leer los confirmados", exc_info=True)
        return []
    return [{"id": int(r["id"]), "chequeo_id": r["chequeo_id"], "familia": r["familia"],
             "titulo": r["titulo"], "de": r["de"], "a": r["a"], "motivo": r["motivo"],
             "evidencia": r["evidencia"], "minutos": int(r["minutos"] or 0),
             "at": r["at"].isoformat() if r["at"] else None} for r in rows]



# ──────────────────────────────────────────────────────────────────────────────
# DETALLE de un chequeo — lo que reemplaza a las tabs JOBS y CONTROLES.
#
# Regla: NADA vacío ni incomprensible. Si algo salta como alerta, acá tiene que
# estar el log completo, el código de error y las cifras — no un título de colores.
# Cada familia trae lo suyo, y todo sale de la MISMA fuente que evaluó el chequeo,
# así el detalle no puede contradecir al estado.
# ──────────────────────────────────────────────────────────────────────────────

def _detalle_job(chequeo: dict) -> dict:
    """Las últimas corridas COMPLETAS: stats, errores y el log del JobRunLogger.

    Es exactamente lo que mostraba la tab JOBS → HISTORIAL, pero colgado del
    incidente en vez de en una pantalla aparte.
    """
    tipos: list[str] = []
    for mod in (chequeo.get("modulos") or []):
        tipos.append(str(mod).rsplit(".", 1)[-1])
    # El label del cron suele ser el tipo con el que loguea (y `aum` es el alias
    # histórico del backfill de tenencias, ver jobs_catalogo._ALIAS_TIPO).
    tipos.append(str(chequeo.get("id", "")).split(":", 1)[-1])
    try:
        rows = _q(
            "SELECT tipo, status, started_at, finished_at, data FROM manager.job_runs "
            "WHERE tipo = ANY(%(t)s) ORDER BY started_at DESC LIMIT 15",
            {"t": list({t for t in tipos if t})})
    except Exception as e:
        return {"tipo": "job", "error": f"{type(e).__name__}: {e}", "corridas": []}
    corridas = []
    for r in rows:
        d = r["data"] if isinstance(r["data"], dict) else {}
        corridas.append({
            "tipo": r["tipo"], "status": r["status"],
            "inicio": r["started_at"].isoformat() if r["started_at"] else None,
            "fin": r["finished_at"].isoformat() if r["finished_at"] else None,
            "elapsed_s": d.get("elapsed_s"),
            "stats": d.get("stats") or {},
            # Los errores COMPLETOS y el log del job: es lo que se venía perdiendo.
            "errores": d.get("errors") or [],
            "log": (d.get("log") or [])[-40:],
        })
    return {"tipo": "job", "corridas": corridas,
            "explicacion": (
                "Cada fila es una corrida del cron. `stats` dice cuánto procesó "
                "(una corrida en 'ok' con stats vacío o en cero es sospechosa), "
                "`errores` es lo que el job reportó, y `log` sus últimas líneas.")}


def _detalle_dato(chequeo: dict) -> dict:
    """Las últimas fechas cargadas de la tabla + cuántas filas tiene cada una."""
    c = next((x for x in CONTRATOS if x["id"] == chequeo.get("id")), None)
    if not c:
        return {"tipo": "dato", "error": "el contrato ya no existe", "fechas": []}
    try:
        rows = _q(f"SELECT {c['columna']} AS fecha, COUNT(*) AS filas "
                  f"FROM {c['tabla']} GROUP BY {c['columna']} "
                  f"ORDER BY {c['columna']} DESC LIMIT 12")
    except Exception as e:
        return {"tipo": "dato", "tabla": c["tabla"], "columna": c["columna"],
                "error": f"{type(e).__name__}: {e}", "fechas": []}
    return {
        "tipo": "dato", "tabla": c["tabla"], "columna": c["columna"],
        "tolerancia_dias_habiles": c.get("max_dias_habiles"),
        "fechas": [{"fecha": str(r["fecha"]), "filas": int(r["filas"])} for r in rows],
        "explicacion": (
            f"Últimas fechas de {c['tabla']}. El chequeo se pone en rojo cuando la "
            f"más nueva queda a más de {c.get('max_dias_habiles')} días hábiles de hoy. "
            "Un salto en la cantidad de filas también avisa: si un día cargó la mitad, "
            "algo se cortó a mitad de camino."),
    }


def _detalle_control(chequeo: dict) -> dict:
    """TODAS las anomalías del control, con su ítem y su detalle."""
    cid = str(chequeo.get("id", "")).split(":", 1)[-1]
    try:
        from api.services.controles_sql import listar_controles
        grupo = (listar_controles(incluir_resueltos_dias=7).get("controles") or {}).get(cid, {})
    except Exception as e:
        return {"tipo": "control", "error": f"{type(e).__name__}: {e}", "anomalias": []}
    return {
        "tipo": "control", "control_id": cid,
        "anomalias": grupo.get("activos") or [],
        "resueltas_7d": len(grupo.get("resueltos") or []),
        "explicacion": (
            "Cada fila es un caso concreto que hay que corregir en los datos. `desde` "
            "es cuándo se detectó por primera vez: si lleva semanas, nadie lo está "
            "mirando. Se resuelven corrigiendo el dato — el control las marca solas "
            "en la próxima corrida."),
    }


def detalle(chequeo_id: str) -> dict:
    """TODO lo que hay detrás de un chequeo. Reemplaza a las tabs JOBS y CONTROLES.

    El detalle sale de la misma fuente que evaluó el chequeo, así no puede
    contradecir al estado que se ve en la pantalla.
    """
    cid = (chequeo_id or "").strip()
    if not cid:
        raise ValueError("falta el chequeo")
    ch = next((c for c in evaluar() if c["id"] == cid), None)
    if ch is None:
        raise ValueError(f"no existe el chequeo {cid}")
    familia = ch.get("familia")
    if familia == "job":
        cuerpo = _detalle_job(ch)
    elif familia == "dato":
        cuerpo = _detalle_dato(ch)
    elif familia == "control":
        cuerpo = _detalle_control(ch)
    else:
        cuerpo = {"tipo": familia or "?"}
    return {"chequeo": ch, "historial": historial(chequeo_id=cid, limite=20), **cuerpo}
