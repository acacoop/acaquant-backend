"""api/services/av_agent_memoria.py — LA MEMORIA DEL AGENTE.

Doc madre: **`docs/AV_AGENT.md`** (§0, el programa de IA).

**El pedido que le da origen** (user, 2026-08-17): *«todo lo que pase de ahora en
adelante tiene que servir para alimentar al modelo… no puede quedar nada
desperdiciado: tiene que quedar todo, cómo se va construyendo la solución, los
errores que detecto, cómo se fue modificando. Porque ahora es bonos, pero después
está SALUD y van a venir más cosas.»*

Y tenía delante el caso perfecto. En OLC3O el agente dijo, **en la misma
pantalla**:

    lente 7  →  «ninguna fuente local tiene un precio mayor que 0»
    paso 13  →  «precio 137.280,0000 (snapshot)»

No era un bono mal cargado: **era el agente contradiciéndose**, porque las lentes
recibían un dict sin la clave `precio`. Eso es más grave que un dato malo —
destruye la confianza en todo lo demás que dice, incluido lo que está bien. Y sin
este módulo, la lección de ese bug vivía en un chat y se perdía.

Tres piezas, y cada una tapa un agujero distinto:

**1. TRAZAS** (`av_agent_trazas`). Cada diagnóstico se guarda entero —las
observaciones, la causa, el veredicto, el estado del bono en ese momento—. Hasta
hoy se calculaba y se tiraba al cerrar el modal. Es la materia prima de todo lo
demás: sin el registro de lo que el agente dijo AYER no hay forma de saber si hoy
dice algo mejor.

**2. CONTRADICCIONES.** El agente se audita a sí mismo: si dos lentes afirman
cosas incompatibles sobre el MISMO hecho, se marca. **Una contradicción es un bug
del agente, no del bono**, y tiene que verse como tal — no mezclada entre los
hallazgos del instrumento.

**3. LECCIONES** (`av_agent_lecciones`). El patrón durable: qué se vio, qué
pasaba de verdad, qué se cambió y en qué commit. Se muestran EN el diagnóstico
cuando aplican, así lo aprendido llega al lugar donde hace falta en vez de quedar
en un doc que nadie abre en el momento justo.

⚠️ **Nada de esto decide nada todavía.** Es registro y contexto: el agente muestra
lo que ya se aprendió y quién lo aprendió, y la decisión sigue siendo humana.
Convertir una lección en una regla automática es un paso aparte y explícito.
"""
from __future__ import annotations

import json
import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)


# ── 1) CONTRADICCIONES: el agente auditándose a sí mismo ────────────────────
#
# Cada regla nombra DOS afirmaciones que no pueden convivir. Son deterministas y
# baratas, y corren sobre el resultado del propio análisis — o sea que el agente
# no puede publicar una incoherencia sin marcarla.
#
# **Por qué esto y no "revisar mejor el código"**: el bug de OLC3O pasó los tests,
# el lint y una lectura humana. Lo único que lo hubiera cazado es alguien
# comparando dos frases separadas por seis renglones en una pantalla larga. Eso lo
# hace una máquina mejor que una persona, y lo hace SIEMPRE.


def contradicciones(observaciones: list[dict], contexto: dict) -> list[dict]:
    """Incoherencias ENTRE lentes del mismo diagnóstico.

    `contexto` es lo que el diagnóstico sabe de verdad (precio, residual, paridad);
    las observaciones son lo que dijo. Cuando no coinciden, el que está mal es el
    agente.
    """
    por = {o.get("clave"): o for o in observaciones or []}
    out: list[dict] = []
    px = contexto.get("precio")
    tiene_precio = isinstance(px, int | float) and float(px) > 0

    def _afirma_sin_precio(o: dict) -> bool:
        """La lente AFIRMÓ que no hay precio. Se mira el hecho declarado y no el
        texto: comparar prosa se rompe con un sinónimo, y lo que hay que cazar son
        incoherencias que ya se le escaparon a una lectura humana."""
        return "precio" in (o.get("hechos") or {}) and not (o["hechos"]["precio"])

    o_px = por.get("precio") or {}
    if tiene_precio and _afirma_sin_precio(o_px):
        out.append({"id": "precio_fantasma",
                    "detalle": f"la lente del PRECIO dice que no hay ninguno, pero "
                               f"el diagnóstico está usando **{float(px):,.4f}**. "
                               "Una de las dos afirmaciones es falsa y las dos "
                               "salen del mismo request.",
                    "lentes": ["precio"]})
    if (not tiene_precio) and (o_px.get("hechos") or {}).get("precio"):
        out.append({"id": "precio_inventado",
                    "detalle": "la lente del PRECIO describe una escala de un "
                               "precio que el diagnóstico no tiene.",
                    "lentes": ["precio"]})

    o_par = por.get("paridad") or {}
    if (not tiene_precio) and (o_par.get("hechos") or {}).get("precio"):
        out.append({"id": "paridad_sin_precio",
                    "detalle": "la lente de la PARIDAD muestra la división con un "
                               "precio que la lente del precio dice que no existe.",
                    "lentes": ["paridad", "precio"]})

    o_tasa = por.get("tasa") or {}
    if tiene_precio and _afirma_sin_precio(o_tasa):
        out.append({"id": "tea_culpa_al_precio",
                    "detalle": "la lente de la TASA culpa a la falta de precio, "
                               "pero hay precio: la causa de que no haya TEA es "
                               "otra y esto manda a buscar donde no es.",
                    "lentes": ["tasa"]})
    return out


# ── 2) TRAZAS: lo que el agente dijo, guardado ──────────────────────────────


def registrar_traza(*, caso: str, dominio: str, causa: str, veredicto: str,
                    observaciones: list[dict], contexto: dict | None = None,
                    incoherencias: list[dict] | None = None,
                    por: str = "") -> int | None:
    """Guarda UN diagnóstico completo. Append-only, best-effort.

    **Nunca rompe la pantalla**: si la escritura falla se logea y el diagnóstico
    sigue. Un registro que puede tumbar la funcionalidad que registra se termina
    apagando, y ahí se pierde todo.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mercado.av_agent_trazas "
                "(caso, dominio, causa, veredicto, observaciones, contexto, "
                " incoherencias, por) VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,"
                " %s::jsonb,%s) RETURNING id",
                (caso.upper(), dominio, causa, veredicto,
                 json.dumps(observaciones or [], default=str),
                 json.dumps(contexto or {}, default=str),
                 json.dumps(incoherencias or [], default=str), por or None))
            return (cur.fetchone() or [None])[0]
    except Exception:
        logger.warning("av_agent_memoria: no se pudo registrar la traza de %s",
                       caso, exc_info=True)
        return None


def casos_parecidos(causa: str, *, excluir: str = "", limite: int = 5) -> list[dict]:
    """Otros casos donde el agente dijo LA MISMA causa, con lo que se votó.

    Es *exemplar learning* sin vectores ni modelos: la clave es la causa, que ya
    es una clasificación del propio agente. Vale porque contesta la pregunta que
    una persona haría primero — **«¿esto ya lo vimos?»** — y porque un caso
    parecido que salió bien es la mejor evidencia de que la propuesta sirve.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT t.caso, max(t.creado_at) AS visto, "
                "       count(*) FILTER (WHERE e.acierta) AS ok, "
                "       count(e.id) AS votos "
                "FROM mercado.av_agent_trazas t "
                "LEFT JOIN mercado.av_agent_evals e "
                "       ON e.caso = t.caso AND e.causa = t.causa "
                "WHERE t.causa = %s AND t.caso <> %s "
                "GROUP BY t.caso ORDER BY max(t.creado_at) DESC LIMIT %s",
                (causa, (excluir or "").upper(), limite))
            return [{"caso": r[0], "visto_at": r[1].isoformat() if r[1] else None,
                     "aciertos": int(r[2] or 0), "votos": int(r[3] or 0)}
                    for r in cur.fetchall()]
    except Exception:
        return []


# ── 3) LECCIONES: el patrón durable ─────────────────────────────────────────


def lecciones_de(causa: str, dominio: str = "") -> list[dict]:
    """Lo que ya aprendimos sobre esta causa.

    Se muestran DENTRO del diagnóstico y no en un doc aparte, a propósito: una
    lección sirve en el momento en que alguien está por decidir, no cuando se le
    ocurra ir a buscarla.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT slug, titulo, sintoma, causa_raiz, cambio, commit_sha, "
                "       detectado_por, creado_at FROM mercado.av_agent_lecciones "
                "WHERE activa AND (causa = %s OR causa = '') "
                "  AND (%s = '' OR dominio = %s OR dominio = '') "
                "ORDER BY creado_at DESC",
                (causa, dominio, dominio))
            return [{"slug": r[0], "titulo": r[1], "sintoma": r[2],
                     "causa_raiz": r[3], "cambio": r[4], "commit": r[5],
                     "detectado_por": r[6],
                     "creado_at": r[7].isoformat() if r[7] else None}
                    for r in cur.fetchall()]
    except Exception:
        return []


def guardar_leccion(*, slug: str, titulo: str, sintoma: str, causa_raiz: str,
                    cambio: str, causa: str = "", dominio: str = "",
                    commit_sha: str = "", detectado_por: str = "user") -> dict:
    """Anota (o actualiza) una lección. `slug` es la identidad: re-anotar la misma
    lección la ACTUALIZA en vez de duplicarla, porque una lección se refina."""
    if not (slug and titulo and cambio):
        return {"ok": False, "error": "faltan slug, título o cambio"}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mercado.av_agent_lecciones "
                "(slug, dominio, causa, titulo, sintoma, causa_raiz, cambio, "
                " commit_sha, detectado_por) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (slug) DO UPDATE SET titulo=EXCLUDED.titulo, "
                "  sintoma=EXCLUDED.sintoma, causa_raiz=EXCLUDED.causa_raiz, "
                "  cambio=EXCLUDED.cambio, commit_sha=EXCLUDED.commit_sha, "
                "  causa=EXCLUDED.causa, dominio=EXCLUDED.dominio",
                (slug, dominio, causa, titulo, sintoma, causa_raiz, cambio,
                 commit_sha or None, detectado_por))
        return {"ok": True, "slug": slug}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
