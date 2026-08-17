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

import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)

_COLS_H = ["tipo", "ticker", "regla", "severidad", "motivo", "evidencia"]
_ORDEN_SEV = {"alta": 0, "media": 1, "baja": 2}


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def avisos() -> list[dict]:
    """**Lo que quedó para hacer A MANO.** Pedido del user (2026-08-17):

        *«Está bien que se cargue sin CER de emisión. Solamente tiene que haber
        una sección acá en el agente que se llame AVISOS, y todo lo que aparezca
        ahí es para hacer manual. A los CER les perdonamos: igual me saca el
        laburo de cargarlo en la base, me lo deja sencillo, solo poner el CER de
        emisión y nada más.»*

    Es el cambio de postura que hace útil al agente: en vez de negarse a hacer el
    95% del trabajo porque no puede hacer el 5%, **hace el 95% y deja anotado el
    5%**.

    **NO se persiste, se DERIVA** — mismo criterio que el modelo de SALUD. Un
    aviso guardado hay que acordarse de cerrarlo, y una lista de pendientes que
    nadie limpia se deja de mirar a la semana. Acá **el aviso ES la condición**:
    en cuanto cargás el `cer_emision`, la fila desaparece sola. No hay botón de
    «resuelto» porque no hace falta, y no puede quedar desactualizada.

    Alcance: **los bonos que dio de alta el agente**, no todo el master. El agente
    avisa de SU propio trabajo; auditar los 221 bonos cargados a mano en dos años
    es otra pregunta y merece su propia pantalla.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            # Un solo viaje: los tickers que aplicó el agente cruzados contra el
            # estado ACTUAL del master. El peaje a Supabase es fijo por query
            # (~8,5 ms), así que lo que importa es la cantidad, no el plan.
            cur.execute("""
                SELECT c.ticker, c.ajuste, c.emisor, c.data->>'cer_emision', a.ts
                  FROM mercado.curvas c
                  JOIN (SELECT objetivo, MAX(ts) AS ts
                          FROM mercado.av_agent_acciones
                         WHERE accion = 'alta_bono' AND ok
                         GROUP BY objetivo) a ON a.objetivo = c.ticker
                 ORDER BY a.ts DESC
            """)
            filas = cur.fetchall()
    except Exception as e:
        logger.warning("av_agent: no se pudieron derivar los avisos: %s", e)
        return []

    out: list[dict] = []
    for ticker, ajuste, emisor, cer, ts in filas:
        alta = ts.isoformat() if ts else None
        # Un CER sin `cer_emision` no muestra tasa: `engines/curvas.py:438` sale
        # con solo duration. Es el aviso que motivó toda esta sección.
        if (ajuste or "") == "cer" and not (_num(cer) or 0) > 0:
            out.append({"ticker": ticker, "clave": "cer_emision",
                        "que_hacer": "Cargar el CER de emisión",
                        "por_que": "sin ese número el bono no muestra tasa",
                        "donde": "Manager → Títulos", "alta_at": alta})
        # El emisor lo estandariza `jobs/ficha_1816`, pero si 1816 no lo publica
        # queda vacío — y cualquier reporte agrupado por emisor lo deja afuera sin
        # avisar, que es la clase de error que no se ve porque las filas suman.
        if not (emisor or "").strip():
            out.append({"ticker": ticker, "clave": "emisor",
                        "que_hacer": "Cargar el emisor",
                        "por_que": "los reportes agrupados por emisor lo dejan afuera",
                        "donde": "Manager → Títulos", "alta_at": alta})
    return out


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
        # AVISOS: lo que el agente dejó listo salvo un dato que solo puede poner
        # una persona. Derivado en vivo → se cierra solo al cargar el número.
        "avisos": avisos(),
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
