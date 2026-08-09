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

from api.services._sql import _q
from api.services.jobs_catalogo import catalogo_jobs

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
    {"id": "dato:operaciones.operaciones", "titulo": "Boletos de operaciones",
     "tabla": "operaciones.operaciones", "columna": "fecha", "max_dias_habiles": 2,
     "detalle": "alimenta MOVIMIENTOS y el Tablero Comercial"},
    {"id": "dato:mercado.snapshots_cierre", "titulo": "Cierre de renta fija",
     "tabla": "mercado.snapshots_cierre", "columna": "fecha", "max_dias_habiles": 2,
     "detalle": "cierre por bono; sin esto las vistas se quedan en el día anterior"},
    {"id": "dato:mercado.precios_acciones", "titulo": "Velas diarias de ADRs",
     "tabla": "mercado.precios_acciones", "columna": "fecha", "max_dias_habiles": 3,
     "detalle": "alimenta el Scanner y las ZONAS de Trading"},
    {"id": "dato:macro.series_macro", "titulo": "Series macro (CER/dólar/tasas)",
     "tabla": "macro.series_macro", "columna": "fecha", "max_dias_habiles": 3,
     "detalle": "CER, BADLAR, TAMAR: entran a curvas y breakevens"},
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


def _chequeo_job(cron: dict, ahora: datetime) -> dict:
    """Un cron del crontab → un chequeo. El estado sale de las dos preguntas."""
    label = cron.get("label") or ",".join(cron.get("modules") or []) or "?"
    runs = cron.get("runs") or []
    ultimos = [(_iso((r.get("ultimo") or {}).get("started_at")), r) for r in runs]
    ultimos = [(t, r) for t, r in ultimos if t]
    ultimo_t = max((t for t, _ in ultimos), default=None)
    esperada = ultima_ejecucion_esperada(cron.get("schedule") or "")

    # (1) ¿corrió cuando debía? — la resta que nadie hacía.
    atrasado = False
    if esperada is not None:
        limite = esperada + timedelta(hours=MARGEN_ATRASO_H)
        atrasado = ahora > limite and (ultimo_t is None or ultimo_t < esperada)

    # (2) ¿salió bien? — el peor status entre los módulos del cron.
    estados = [(r.get("ultimo") or {}).get("status") for _, r in ultimos]
    fallo = any(s == "error" for s in estados)
    parcial = any(s == "partial" for s in estados)

    if not cron.get("instrumentado") and ultimo_t is None:
        # Sin JobRunLogger no se puede saber nada. No es rojo, pero tampoco verde:
        # es un punto ciego y tiene que verse como tal.
        estado, motivo = WARN, "sin instrumentar: no registra corridas"
    elif fallo:
        estado, motivo = ERROR, "la última corrida falló"
    elif atrasado:
        esp = esperada.strftime("%d/%m %H:%M") if esperada else "?"
        ult = ultimo_t.strftime("%d/%m %H:%M") if ultimo_t else "nunca"
        estado, motivo = ERROR, f"debía correr {esp} UTC y la última fue {ult}"
    elif parcial:
        estado, motivo = WARN, "la última corrida terminó con errores parciales"
    else:
        estado, motivo = OK, "al día"

    resumen = next((( r.get("ultimo") or {}).get("resumen") for _, r in ultimos
                    if (r.get("ultimo") or {}).get("resumen")), "")
    return {
        "id": f"job:{label}",
        "familia": "job",
        "titulo": label,
        "estado": estado,
        "motivo": motivo,
        "evidencia": resumen or motivo,
        "schedule": cron.get("schedule"),
        "ultimo_at": ultimo_t.isoformat() if ultimo_t else None,
        "esperada_at": esperada.isoformat() if esperada else None,
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
    try:
        rows = _q(f"SELECT MAX({c['columna']}) AS ultima FROM {c['tabla']}")
    except Exception as e:
        return {**base, "estado": WARN, "motivo": "no pude consultar la tabla",
                "evidencia": f"{type(e).__name__}: {e}", "ultimo_at": None}
    ultima = rows[0]["ultima"] if rows else None
    if ultima is None:
        return {**base, "estado": ERROR, "motivo": "la tabla está vacía",
                "evidencia": f"{c['tabla']} no tiene ninguna fila", "ultimo_at": None}
    ult_dt = datetime(ultima.year, ultima.month, ultima.day, tzinfo=UTC) \
        if not isinstance(ultima, datetime) else ultima
    atraso = _dias_habiles_atras(ult_dt, ahora)
    tope = int(c.get("max_dias_habiles", 2))
    estado = OK if atraso <= tope else ERROR
    return {**base, "estado": estado,
            "motivo": ("al día" if estado == OK
                       else f"el último dato es de hace {atraso} días hábiles"),
            "evidencia": (f"{c['tabla']}.{c['columna']} máximo = {ultima} "
                          f"(tolerancia: {tope} días hábiles)"),
            "ultimo_at": ult_dt.isoformat()}


# ── Evaluación completa ──────────────────────────────────────────────────────

def evaluar() -> list[dict]:
    """TODOS los chequeos, peor primero. Es la única función que arma el estado."""
    ahora = _ahora()
    out: list[dict] = []
    try:
        for cron in (catalogo_jobs().get("jobs") or []):
            out.append(_chequeo_job(cron, ahora))
    except Exception:
        _log.warning("salud: no pude evaluar los jobs", exc_info=True)
    for c in CONTRATOS:
        try:
            out.append(_chequeo_dato(c, ahora))
        except Exception:
            _log.warning("salud: no pude evaluar %s", c["id"], exc_info=True)
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
            "ORDER BY (ev.a = 'error') DESC, ev.at DESC LIMIT %(lim)s",
            {"e": e, "lim": max(1, min(int(limite), 100))})
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


def panel(email: str = "") -> dict:
    """TODO lo que necesita la pantalla, en UNA llamada: veredicto, chequeos (con su
    marca de silenciado) y lo pendiente de ver por este admin."""
    nuevas = sincronizar()          # registra transiciones antes de responder
    r = resumen()
    cfg = config()
    for c in r["chequeos"]:
        c["alertar"] = cfg.get(c["id"], {}).get("alertar", True)
        c["nota"] = cfg.get(c["id"], {}).get("nota")
    r["pendientes"] = pendientes(email)
    r["transiciones_nuevas"] = len(nuevas)
    return r
