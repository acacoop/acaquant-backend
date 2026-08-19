"""api/services/av_agent_acciones.py — el LIBRO DE ACCIONES del AV Agent.

Doc madre: **`docs/AV_AGENT.md`**. Escribe `mercado.av_agent_acciones`.

**Qué contesta.** *"¿Qué tocó el agente, cuándo, en qué tabla, por pedido de
quién, y qué había antes?"* — en un solo lugar y en una sola línea por acción.

Hoy esa información existe pero **desparramada**: `av_agent_preguntas.aplicada_at`
dice cuándo surtió una respuesta, `av_agent_ignorados.creado_at` cuándo se ignoró
un ticker, `curvas_catalogo.creada_at` cuándo nació una curva. Ninguna contesta
la pregunta completa, y para reconstruirla hay que cruzar tres tablas sabiendo de
antemano qué buscar.

**Por qué es obligatorio y no un lujo.** En cuanto el agente escriba en
`mercado.curvas` (E2), una escritura automática sin libro es una escritura que
nadie puede auditar ni revertir. El momento de construirlo es ANTES de esa etapa,
no después del primer susto.

**Tres decisiones que lo hacen servir:**

1. **Se anotan también los FALLOS** (`ok=False` + `error`). Un libro que solo
   registra los éxitos hace parecer que el agente nunca se equivoca, y esconde
   justo el caso que uno va a querer investigar.
2. **`antes` guarda el estado previo.** Sin eso, "revertir" es una promesa y no
   una función.
3. **Registrar NUNCA rompe la acción.** Si el libro falla, la escritura real ya
   pasó y no se deshace por un problema de auditoría — se loguea y sigue. El
   orden inverso (fallar la acción porque no se pudo anotar) sería peor: dejaría
   al usuario sin la función y sin el registro.
"""
from __future__ import annotations

import json
import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)

_COLS = ["id", "ts", "accion", "destino", "objetivo", "detalle", "antes",
         "origen", "pregunta_id", "por", "ok", "error"]

# Qué escribe cada acción y DÓNDE. Tenerlo acá —y no como string suelto en cada
# caller— evita que la misma acción se anote con dos nombres distintos según
# quién la llame, que es como un libro de auditoría deja de ser consultable.
DESTINOS = {
    "ignorar_ticker": "mercado.av_agent_ignorados",
    "designorar": "mercado.av_agent_ignorados",
    "crear_curva": "mercado.curvas_catalogo",
    "alta_bono": "mercado.curvas",
    # El nombre en PLURAL es el que usa `aplicar_flujos` — acá figuraba en
    # singular, así que la acción se anotaba con destino «?» y el libro dejaba de
    # decir dónde escribió. No falla nada: solo deja de servir.
    "completar_flujos": "mercado.curvas",
    "arreglar_bono": "mercado.curvas",
    "sembrar_especies": "mercado.especies",
    "sembrar_tasa_1816": "mercado.tamar_1816",
}


# ⚠️ **QUÉ REGLA JUZGÓ EL HUMANO al aprobar cada acción** (2026-08-19).
#
# El libro registraba QUÉ se hizo (`alta_bono`) y no QUÉ CAUSA lo motivó, así que
# desde el historial era imposible saber sobre qué diagnóstico se estaba
# opinando. Cada aprobación —que ES un juicio sobre si el agente acertó— se
# tiraba, y el eval set tenía que llenarse a mano de cero.
#
# Solo entran las acciones cuyo tipo emite **UNA sola regla**: ahí «aprobó el
# alta» equivale sin ambigüedad a «la causa `no_esta_en_curvas` era correcta».
# `arreglar_bono` NO está y no puede estar: su tipo (`tasa_sospechosa`) emite
# SEIS reglas y no se sabe cuál se estaba juzgando. Inventarla sería peor que no
# tener el dato — mediría la precisión de una regla con los votos de otra.
REGLA_DE_ACCION: dict[str, str] = {
    "alta_bono": "no_esta_en_curvas",       # tipo falta_en_base
    "completar_flujos": "flujos_vacios",    # tipo sin_flujo
    "crear_curva": "ajuste_sin_curva",      # tipo hueco_de_curva
}
# En qué dominio del eval set cae cada una.
_DOMINIO_DE_ACCION = {"alta_bono": "bono", "completar_flujos": "bono",
                      "crear_curva": "bono"}


def registrar(*, accion: str, objetivo: str, detalle: dict | None = None,
              antes: dict | None = None, origen: str = "respuesta",
              pregunta_id: int | None = None, por: str = "",
              ok: bool = True, error: str = "", regla: str = "") -> None:
    """Anota una acción. **Nunca levanta** — ver decisión 3 del módulo."""
    regla = (regla or REGLA_DE_ACCION.get(accion, "")).strip()
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mercado.av_agent_acciones "
                "(accion, destino, objetivo, detalle, antes, origen, pregunta_id, "
                " por, ok, error, regla) "
                "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s) "
                "RETURNING id",
                (accion, DESTINOS.get(accion, "?"), objetivo,
                 json.dumps(detalle or {}, ensure_ascii=False, default=str),
                 json.dumps(antes, ensure_ascii=False, default=str) if antes else None,
                 origen, pregunta_id, por or None, ok, (error or None),
                 regla or None))
            fila = cur.fetchone()
    except Exception as e:
        logger.warning("av_agent: no se pudo anotar la acción %s/%s: %s",
                       accion, objetivo, e)
        return
    # EL VOTO DERIVADO. Va DESPUÉS de anotar y en su propio try por el mismo
    # motivo que el libro entero: la escritura real ya pasó y no se deshace por
    # un problema de medición.
    if ok and por and regla and fila:
        _votar_derivado(accion=accion, objetivo=objetivo, regla=regla,
                        por=por, accion_id=fila[0])


def _votar_derivado(*, accion: str, objetivo: str, regla: str, por: str,
                    accion_id: int) -> None:
    """Una acción APROBADA por un humano y que salió bien **es un juicio**: quien
    la aprobó estaba diciendo que la causa del agente era la correcta.

    Se anota como `derivado`, nunca como `humano`, y no cuenta para
    `candidata_a_auto`: es una señal real pero más débil que un click, y
    mezclarlas dejaría al agente habilitándose solo.
    """
    try:
        from api.services import av_agent_evals
        av_agent_evals.votar(
            caso=objetivo, dominio=_DOMINIO_DE_ACCION.get(accion, "bono"),
            causa=regla, acierta=True, por=por, origen="derivado",
            ref=f"accion:{accion_id}",
            nota=f"derivado: {por} aprobó «{accion}» y salió bien")
    except Exception as e:
        logger.warning("av_agent: no se pudo derivar el voto de la acción %s: %s",
                       accion_id, e)


def listar(limite: int = 100) -> list[dict]:
    """Las últimas acciones, la más reciente primero."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(_COLS)} FROM mercado.av_agent_acciones "
                        "ORDER BY ts DESC LIMIT %s", (limite,))
            out = []
            for r in cur.fetchall():
                d = dict(zip(_COLS, r, strict=False))
                d["ts"] = d["ts"].isoformat() if d["ts"] else None
                out.append(d)
            return out
    except Exception as e:
        logger.warning("av_agent: no se pudo leer el libro de acciones: %s", e)
        return []
