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
    "sembrar_especies": "mercado.especies",
    "sembrar_tasa_1816": "mercado.tamar_1816",
}


def registrar(*, accion: str, objetivo: str, detalle: dict | None = None,
              antes: dict | None = None, origen: str = "respuesta",
              pregunta_id: int | None = None, por: str = "",
              ok: bool = True, error: str = "") -> None:
    """Anota una acción. **Nunca levanta** — ver decisión 3 del módulo."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mercado.av_agent_acciones "
                "(accion, destino, objetivo, detalle, antes, origen, pregunta_id, "
                " por, ok, error) "
                "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)",
                (accion, DESTINOS.get(accion, "?"), objetivo,
                 json.dumps(detalle or {}, ensure_ascii=False, default=str),
                 json.dumps(antes, ensure_ascii=False, default=str) if antes else None,
                 origen, pregunta_id, por or None, ok, (error or None)))
    except Exception as e:
        logger.warning("av_agent: no se pudo anotar la acción %s/%s: %s",
                       accion, objetivo, e)


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
