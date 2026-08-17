"""api/services/av_agent_vista.py — la vista /av-agent en UN request (E1.d).

Doc madre: **`docs/AV_AGENT.md`**.

Sirve la pantalla entera de una: última corrida de hallazgos + preguntas abiertas
+ lo ya decidido. Un solo request y no cinco, por la misma razón que la vista de
SENEBIS y la de ACA: cada roundtrip a Supabase paga un peaje fijo de ~8,5 ms de
DISTANCIA (Droplet en nyc1 ↔ base en us-east-1), así que lo que importa es la
CANTIDAD de queries, no su plan.

**Los hallazgos se sirven de la ÚLTIMA CORRIDA, no en vivo.** Relevar cuesta ~29
créditos y 1-2 minutos de throttle: hacerlo en el request de una pantalla que se
abre diez veces por día sería absurdo. La vista muestra la foto y dice **cuándo**
se sacó — que es la diferencia entre un dato viejo y un dato viejo que miente.
"""
from __future__ import annotations

from core.postgres import get_pool

_COLS_H = ["tipo", "ticker", "regla", "severidad", "motivo", "evidencia"]
_ORDEN_SEV = {"alta": 0, "media": 1, "baja": 2}


def _hallazgos_ultima_corrida() -> tuple[list[dict], str | None]:
    """Los hallazgos de la corrida MÁS RECIENTE + su timestamp.

    Se filtra por `corrida_at = (SELECT max(...))` y no por fecha: dos corridas
    del mismo día son dos fotos distintas, y mezclarlas mostraría hallazgos que
    ya se arreglaron al lado de los actuales."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(corrida_at) FROM mercado.av_agent_hallazgos")
        fila = cur.fetchone()
        corrida = fila[0] if fila else None
        if not corrida:
            return [], None
        cur.execute(
            f"SELECT {', '.join(_COLS_H)} FROM mercado.av_agent_hallazgos "
            "WHERE corrida_at = %s", (corrida,))
        filas = [dict(zip(_COLS_H, r, strict=False)) for r in cur.fetchall()]
    filas.sort(key=lambda h: (_ORDEN_SEV.get(h["severidad"], 9), h["ticker"]))
    return filas, corrida.isoformat()


def _ignorados() -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, motivo, por, creado_at "
                    "FROM mercado.av_agent_ignorados ORDER BY creado_at DESC")
        return [{"ticker": r[0], "motivo": r[1], "por": r[2],
                 "creado_at": r[3].isoformat() if r[3] else None}
                for r in cur.fetchall()]


def _decididas(limite: int = 40) -> list[dict]:
    """Lo ya contestado, lo más reciente primero.

    Se muestra en la vista a propósito: sin el historial, el agente parece
    empezar de cero cada vez y no hay forma de ver qué criterio se viene
    aplicando — ni de arrepentirse de una decisión."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, clave, tipo, pregunta, respuesta, nota, respondida_por, "
            "respondida_at, aplicada_at FROM mercado.av_agent_preguntas "
            "WHERE estado = 'respondida' ORDER BY respondida_at DESC NULLS LAST "
            "LIMIT %s", (limite,))
        cols = ["id", "clave", "tipo", "pregunta", "respuesta", "nota",
                "respondida_por", "respondida_at", "aplicada_at"]
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r, strict=False))
            for k in ("respondida_at", "aplicada_at"):
                d[k] = d[k].isoformat() if d[k] else None
            out.append(d)
        return out


def vista() -> dict:
    """Todo lo que la pantalla necesita, en un request."""
    from api.services import av_agent_acciones as acciones
    from api.services import av_agent_preguntas as preg

    hallazgos, corrida_at = _hallazgos_ultima_corrida()
    abiertas = preg.abiertas()

    por_tipo: dict[str, int] = {}
    por_regla: dict[str, int] = {}
    for h in hallazgos:
        por_tipo[h["tipo"]] = por_tipo.get(h["tipo"], 0) + 1
        por_regla[h["regla"]] = por_regla.get(h["regla"], 0) + 1

    return {
        "corrida_at": corrida_at,
        # Lo contestado que TODAVÍA no surtió efecto. Sin esto, el user contesta
        # 12 altas y no tiene dónde mirar qué pasó con ellas — una decisión que no
        # se ve en ningún lado se siente como una decisión perdida.
        "pendientes": preg.pendientes_de_aplicar(),
        # El LIBRO: qué escribió el agente, cuándo y en qué tabla. Una escritura
        # automática sin libro es una escritura que nadie puede auditar.
        "acciones": acciones.listar(),
        "hallazgos": hallazgos,
        "por_tipo": por_tipo,
        "por_regla": por_regla,
        "preguntas": [p for p in abiertas if p["tipo"] != "decision"],
        "decisiones": [p for p in abiertas if p["tipo"] == "decision"],
        "decididas": _decididas(),
        "ignorados": _ignorados(),
        # Lo que el agente TODAVÍA no sabe hacer, dicho por él mismo. Comunicar
        # las capacidades es parte del contrato con el usuario: sin esto, un
        # "alta" que queda guardado sin aplicarse se lee como que el botón no
        # anduvo.
        "capacidades": {
            "puede_ignorar": True,
            # E2 ya existe: SIMULAR baja el cuadro de 1816, calcula la TEA en seco
            # y corre el pre-flight; APLICAR escribe por `upsert_bono`. Decía
            # `False` con el motivo "es la etapa E2" cuando E2 ya estaba hecha —
            # una capacidad mal declarada hace que el botón se lea como roto,
            # justo el problema que este campo venía a evitar.
            "puede_dar_de_alta": True,
            "motivo_alta": ("SIMULAR no escribe nada: baja el cuadro de 1816, "
                            "calcula la TEA que tendría y valida la cadena entera "
                            "(especie, símbolo en Primary, suscripción, snapshot). "
                            "APLICAR solo se ofrece si esa cadena no tiene ningún "
                            "paso bloqueado. Las ramas sin conversión inequívoca "
                            "(tamar, dólar-linked, ajustes sin fórmula) se simulan "
                            "igual, pero las carga un humano."),
        },
    }
