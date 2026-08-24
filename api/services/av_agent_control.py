"""api/services/av_agent_control.py — EL TABLERO DE CONTROL del AV Agent.

Doc madre: **`docs/AV_AGENT.md`** §0.h.

**Qué contesta.** *«¿Puedo frenarlo? ¿De qué está leyendo? ¿Cuánto le queda?»* —
las tres preguntas que el modal no podía contestar, porque los 15 endpoints del
agente actúan sobre UN hallazgo y ninguno actúa sobre EL AGENTE.

Tres cosas viven acá:

1. **LA PARADA** — un interruptor que corta las ESCRITURAS y deja intacta la
   lectura. Frenar diagnosticando es exactamente lo que uno quiere mientras
   investiga: si la parada apagara todo, el primer reflejo ante una duda sería
   quedarse sin la herramienta justo cuando hace falta.
2. **LAS FUENTES** — de dónde lee cada cosa y en qué estado está. Es la
   generalización del bug del briefing (2026-08-18): una fuente degradada se
   notaba recién al leer un resultado raro, y para entonces uno ya está
   debuggeando el resultado en vez de la fuente.
3. **EL PRESUPUESTO** — créditos de 1816 y tokens de IA.

⚠️ **NINGUNA lectura de acá pega a la red.** El estado del token sale de la fila
compartida en `manager.tokens_externos` y el catálogo de la tabla local. Un
tablero que gasta un crédito cada vez que se mira es un tablero que se deja de
mirar — y peor, uno que consume el recurso que vino a cuidar (la misma lección
que dejó el backoff contra la cuota, `docs/AV_AGENT.md`).
"""
from __future__ import annotations

import logging
import time

from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Cache in-process del estado de la parada. Dos motivos, y el segundo importa
# más: (1) se consulta en CADA escritura y el peaje a Supabase es ~8,5ms;
# (2) **si la base no contesta, se sigue usando el último valor conocido** — sin
# esto una parada activa se evaporaba ante un blip de red, o sea que el
# interruptor mentía justo cuando el sistema está peor.
_CACHE_TTL_S = 5.0
_cache: tuple[float, dict] | None = None

# El default cuando NUNCA se pudo leer (proceso recién arrancado, o la tabla
# todavía no existe porque el schema no se aplicó): **se permite escribir**.
#
# Es una decisión, no un descuido. La parada NO es un control de seguridad —eso
# lo dan `require_admin` y el humano que aprueba cada escritura— sino una
# comodidad para frenar al agente. Fallar cerrado dejaría al agente inutilizable
# ante cualquier problema de la base… que es la MISMA base donde el agente
# escribe, así que la escritura fallaría igual. Fallar abierto no agrega riesgo
# real y evita que un deploy sin schema aplicado rompa lo que ya andaba.
_SIN_DATO = {"parada": False, "motivo": "", "por": "", "cambiado_at": None,
             "leido": False}


def _leer_parada() -> dict:
    """La fila de control, cacheada. Nunca levanta."""
    global _cache
    ahora = time.time()
    if _cache and ahora - _cache[0] < _CACHE_TTL_S:
        return _cache[1]
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT parada, motivo, por, cambiado_at "
                        "FROM agente.av_agent_control WHERE id")
            r = cur.fetchone()
        est = ({"parada": bool(r[0]), "motivo": r[1] or "", "por": r[2] or "",
                "cambiado_at": r[3].isoformat() if r[3] else None, "leido": True}
               if r else dict(_SIN_DATO))
        _cache = (ahora, est)
        return est
    except Exception as e:
        # Se sigue con el último valor CONOCIDO: una parada activa no se levanta
        # sola porque la base tosió.
        if _cache:
            logger.warning("av_agent_control: no se pudo releer (%s); sigue el "
                           "último estado conocido (parada=%s)", e, _cache[1]["parada"])
            return _cache[1]
        logger.warning("av_agent_control: sin estado (%s) → se permite escribir", e)
        return dict(_SIN_DATO)


def guardia(accion: str) -> dict | None:
    """**El portero de toda escritura del agente.** `None` = puede pasar; un dict
    = la respuesta de rechazo, con la misma forma que devuelve un `aplicar`
    fallido para que el modal la muestre sin código nuevo.

    Va al PRINCIPIO de cada `aplicar*`, antes de tocar nada. Que sea una función
    y no un `if` copiado es lo que permite testear de una sola vez que ninguna
    puerta se olvidó de llamarla (`test_av_agent_control.py`).
    """
    est = _leer_parada()
    if not est["parada"]:
        return None
    quien = f" (la puso {est['por']})" if est.get("por") else ""
    motivo = f": {est['motivo']}" if est.get("motivo") else ""
    logger.warning("av_agent: PARADA activa — se rechaza %s", accion)
    return {"ok": False, "parada": True,
            "error": f"EL AGENTE ESTÁ FRENADO{quien}{motivo}. "
                     f"Se puede diagnosticar, pero no escribir."}


# ── QUÉ **NO** FRENA LA PARADA, y es una decisión ───────────────────────────
#
# Un test barre TODOS los módulos `av_agent_*` buscando escrituras y exige que
# la función que las hace llame a `guardia()` **o esté declarada acá con su
# motivo**. Es el mismo mecanismo que `av_agent_hacer.SIN_ACCION`: no se puede
# agregar una puerta sin decidir, y la decisión queda escrita.
#
# La regla que ordena la lista: **la parada corta las escrituras de DATO, no el
# triaje ni la comunicación.** Frenar al agente tiene que dejarte seguir
# trabajando —mirar, descartar, enterarte de que un proveedor se cayó—; si te
# deja sin la herramienta, el primer reflejo ante una duda es no frenarlo.
SIN_GUARDIA: dict[str, str] = {
    # ── MEMORIA PROPIA del agente. No es dato de negocio: es lo que el agente
    # sabe de sí mismo, y frenarlo no puede dejarlo ciego (además el delta de
    # mañana necesita la foto de hoy — saltear una noche rompe la medición).
    "av_agent_db.sacar_foto":
        "foto del tamaño de la base (manager.db_tamano) — memoria del agente",
    "av_agent_contexto.barrer":
        "perfil de cadencia por tabla (manager.tabla_perfil) — memoria del agente",
    "av_agent_seguridad.sacar_foto":
        "foto de la superficie HTTP (manager.superficie_dia) — memoria del agente",
    # ── COMUNICACIÓN. Avisar que Aunesa se cayó no es escribir un dato: es la
    # señal que hace que alguien reaccione. Callarla mientras el agente está
    # frenado es justo al revés de lo que uno quiere de un freno.
    "av_agent_mensajes.enviar_muchos":
        "manda un aviso a una persona — la parada corta datos, no avisos",
    "av_agent_proveedores.avisar_caida":
        "avisa que un proveedor se cayó — enterarse no puede depender del freno",
    "av_agent_recuperados._avisar_vuelta":
        "avisa que un proveedor volvió — si salió la mala, sale la buena",
    # ── CUBIERTA POR SU LLAMADOR. Rama interna de `aplicar_arreglo`, que sí
    # consulta la parada antes de simular.
    "av_agent_alta._aplicar_parche_local":
        "la cubre `aplicar_arreglo`, que llama a guardia() antes de simular",
    # ── CUBIERTAS POR `av_agent_hacer.aplicar()`, el único camino a la
    # escritura de las 10 acciones (también desde `uno(aplicar_ya=True)`).
    "av_agent_hacer.AccionCartera.aplicar": "la cubre av_agent_hacer.aplicar()",
    "av_agent_hacer.AccionFci.aplicar": "la cubre av_agent_hacer.aplicar()",
    "av_agent_hacer.AccionPedirPata.aplicar": "la cubre av_agent_hacer.aplicar()",
    "av_agent_hacer.AccionApuntarPata.aplicar": "la cubre av_agent_hacer.aplicar()",
    "av_agent_hacer.AccionTickerAsset.aplicar": "la cubre av_agent_hacer.aplicar()",
}


def set_parada(*, activa: bool, motivo: str = "", por: str = "") -> dict:
    """Prende o apaga la parada. Queda en el LIBRO DE ACCIONES: frenar al agente
    es una acción sobre el sistema como cualquier otra, y sin registro no se
    puede reconstruir por qué estuvo quieto tres días."""
    global _cache
    motivo = (motivo or "").strip()[:400]
    if activa and not motivo:
        return {"ok": False, "error": "Para frenar al agente hace falta un motivo — "
                                      "sin él, el que lo encuentre frenado no sabe si "
                                      "puede reanudarlo."}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE agente.av_agent_control SET parada = %s, motivo = %s, "
                        "por = %s, cambiado_at = now() WHERE id",
                        (activa, motivo, por or ""))
            if cur.rowcount == 0:      # la fila semilla del schema no está
                cur.execute("INSERT INTO agente.av_agent_control (id, parada, motivo, por) "
                            "VALUES (true, %s, %s, %s)", (activa, motivo, por or ""))
            conn.commit()
    except Exception as e:
        return {"ok": False, "error": f"no se pudo cambiar la parada: {e}"}
    _cache = None                       # el próximo lector ve el valor nuevo
    from api.services import av_agent_acciones as acc
    acc.registrar(accion="parada" if activa else "reanudar",
                  objetivo="av_agent", detalle={"motivo": motivo},
                  origen="api", por=por)
    return {"ok": True, "parada": activa, "motivo": motivo, "por": por}


# ── Las FUENTES ─────────────────────────────────────────────────────────────
#
# Cada una dice su ESTADO y su EVIDENCIA. `estado` usa el mismo vocabulario que
# el pre-flight (`ok` / `revisar` / `bloquea`) para que el modal las pinte con
# los colores que ya tiene y nadie tenga que aprender una segunda convención.

def _fuente(clave: str, titulo: str, estado: str, detalle: str,
            para: str = "") -> dict:
    return {"clave": clave, "titulo": titulo, "estado": estado,
            "detalle": detalle, "para": para}


def _fuente_1816() -> dict:
    from core import mercado_1816
    try:
        if not mercado_1816.disponible():
            return _fuente("1816", "1816 (proveedor)", "bloquea",
                           "sin credenciales configuradas",
                           "el universo, el cronograma y el precio de referencia")
        t = mercado_1816.estado_token()
        if t["token_vigente"]:
            h = t["expira_en_s"] // 3600
            m = (t["expira_en_s"] % 3600) // 60
            return _fuente("1816", "1816 (proveedor)", "ok",
                           f"token vigente, vence en {h}h {m:02d}m · "
                           f"logins de hoy {t['logins_hoy']}/{t['tope']}",
                           "el universo, el cronograma y el precio de referencia")
        return _fuente("1816", "1816 (proveedor)", "revisar",
                       f"SIN token vigente — el próximo pedido va a intentar un "
                       f"login (llevamos {t['logins_hoy']}/{t['tope']} hoy, y el "
                       f"plan permite 50 tokens por día)",
                       "el universo, el cronograma y el precio de referencia")
    except Exception as e:
        return _fuente("1816", "1816 (proveedor)", "revisar", f"no se pudo leer: {e}")


def _fuente_base() -> dict:
    """Postgres, medido. El número importa: el peaje de CADA query es distancia
    pura (Droplet nyc1 ↔ Supabase us-east-1), así que lo que se paga es la
    CANTIDAD de queries, no su plan."""
    try:
        t0 = time.perf_counter()
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        ms = (time.perf_counter() - t0) * 1000
        est = "ok" if ms < 50 else "revisar"
        return _fuente("base", "Postgres / Supabase", est, f"ida y vuelta {ms:.1f} ms",
                       "todo")
    except Exception as e:
        return _fuente("base", "Postgres / Supabase", "bloquea", str(e)[:120], "todo")


def _una(sql: str) -> tuple | None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()


def _fuente_catalogo() -> dict:
    """El catálogo local de 1816 — lo que salva al agente cuando el proveedor no
    contesta (`universo_local`)."""
    try:
        r = _una("SELECT count(*) FROM research.mkt_1816_instrumentos")
        n = r[0] if r else 0
        est = "ok" if n > 500 else "revisar"
        return _fuente("catalogo", "Catálogo local de 1816", est,
                       f"{n} instrumentos "
                       + ("" if n > 500 else "— refrescar con "
                          "`jobs.mercado_1816_discovery --apply --catalogo`"),
                       "la ficha del bono y el universo cuando 1816 no contesta")
    except Exception as e:
        return _fuente("catalogo", "Catálogo local de 1816", "revisar", str(e)[:120])


def _fuente_precios() -> dict:
    try:
        r = _una("SELECT count(*), max(updated_at) FROM mercado.market_snapshot")
        n, ult = (r or (0, None))
        det = f"{n} símbolos"
        est = "ok"
        if ult:
            import datetime as dt
            edad = (dt.datetime.now(dt.UTC) - ult).total_seconds() / 60
            det += f" · último update hace {edad:.0f} min"
            if edad > 120:
                est = "revisar"
        return _fuente("precios", "Precios live (market_snapshot)", est, det,
                       "la paridad y la TEA con las que se juzga un bono")
    except Exception as e:
        return _fuente("precios", "Precios live (market_snapshot)", "revisar", str(e)[:120])


def _fuente_corrida() -> dict:
    """Cuándo relevó el agente por última vez. Una foto sin fecha miente en
    silencio — el mismo criterio que la cabecera del modal."""
    try:
        r = _una("SELECT max(corrida_at) FROM agente.av_agent_hallazgos")
        ult = r[0] if r else None
        if not ult:
            return _fuente("corrida", "Última relevada", "revisar", "nunca corrió")
        import datetime as dt
        h = (dt.datetime.now(dt.UTC) - ult).total_seconds() / 3600
        return _fuente("corrida", "Última relevada", "ok" if h < 30 else "revisar",
                       f"hace {h:.0f} h ({ult.isoformat(timespec='minutes')})",
                       "todo lo que se ve en ENCONTRÓ")
    except Exception as e:
        return _fuente("corrida", "Última relevada", "revisar", str(e)[:120])


def fuentes() -> list[dict]:
    """Todas las fuentes, cada una en su `try`: que una se caiga no puede dejar
    el tablero en blanco — es justo cuando más se lo necesita."""
    out = []
    for f in (_fuente_base, _fuente_1816, _fuente_catalogo, _fuente_precios,
              _fuente_corrida):
        try:
            out.append(f())
        except Exception as e:            # defensa de última línea
            logger.warning("av_agent_control: fuente %s rota: %s", f.__name__, e)
    return out


def estado() -> dict:
    """Todo el tablero en UN request. El modal no puede pedir cinco cosas para
    dibujar una pantalla: el peaje por query es fijo y se paga por usuario."""
    p = _leer_parada()
    fs = fuentes()
    return {
        "ok": True,
        "parada": p,
        "fuentes": fs,
        # El resumen es del BACKEND: si lo calculara el front, dos pantallas
        # podrían disentir sobre si el sistema está sano.
        "resumen": {
            "bloquea": sum(1 for f in fs if f["estado"] == "bloquea"),
            "revisar": sum(1 for f in fs if f["estado"] == "revisar"),
            "ok": sum(1 for f in fs if f["estado"] == "ok"),
        },
    }
