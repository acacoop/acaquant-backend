"""`agente/vista.py` — el READ MODEL. Doc: `AGENT_2.0.md` §6.

**Las pantallas LEEN. No derivan.** Clase, estado, arreglo y nombre vienen
resueltos de acá; ningún contador se suma en el navegador.

En el agente viejo el badge «AHORA 92» lo sumaba el browser juntando cuatro
cosas de DOS endpoints con frescuras distintas (uno se refrescaba cada 20 s, el
otro se cargaba una sola vez al abrir), contadas sobre listas ya cortadas en 200
filas, leídas de una tabla distinta de la que dibujaba LA LISTA. Nada de eso se
podía verificar del lado del servidor.

Acá cada pantalla es UNA query sobre UNA vista.
"""
from __future__ import annotations

import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)


def _filas(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _serializar(f: dict) -> dict:
    for k, v in list(f.items()):
        if hasattr(v, "isoformat"):
            f[k] = v.isoformat()
        elif isinstance(v, (int, float, str, bool, dict, list)) or v is None:
            pass
        else:
            f[k] = str(v)
    return f


# ── AHORA ──────────────────────────────────────────────────────────────────
def ahora() -> dict:
    """Lo de HOY, sin leer, sin resolver. **Un COUNT sobre tres condiciones.**

    El badge y la lista salen de la misma query, así que no pueden decir cosas
    distintas — que es lo que pasaba cuando el badge se sumaba en el navegador.

    AHORA **se vacía sola**: un hallazgo nace con la fecha del día en que se
    detectó, y al día siguiente sale aunque nadie lo haya leído. Funciona porque
    un problema que persiste NO crea fila nueva (sube `veces`), así que su
    `detectado_at` sigue siendo el del día que apareció.
    """
    filas = [_serializar(f) for f in _filas("SELECT * FROM agente.v_ahora")]
    return {"total": len(filas), "filas": filas}


def marcar_leidos(ids: list[int], *, por: str = "") -> dict:
    """«Ya me enteré». **Lo saca de AHORA y de NINGÚN otro lado.**

    LEÍDO ≠ RESUELTO: el hallazgo sigue abierto, sigue en ENCONTRÓ y sigue con
    su botón. Marcar leído baja el ruido del día, no cierra nada.

    Idempotente (`AND leido_at IS NULL`): marcar dos veces no pisa quién fue el
    primero.
    """
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return {"ok": True, "marcados": 0}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE agente.hallazgos SET leido_at = now(), leido_por = %s "
                    "WHERE id = ANY(%s) AND leido_at IS NULL", (por, ids))
        return {"ok": True, "marcados": cur.rowcount or 0}


def desmarcar_leidos(ids: list[int]) -> dict:
    """Reversible desde la misma pantalla: leer no es una decisión importante y
    no tiene por qué ser irreversible."""
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return {"ok": True, "vueltos": 0}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE agente.hallazgos SET leido_at = NULL, leido_por = '' "
                    "WHERE id = ANY(%s)", (ids,))
        return {"ok": True, "vueltos": cur.rowcount or 0}


# ── ENCONTRÓ ───────────────────────────────────────────────────────────────
def encontro() -> dict:
    """Lo abierto que TIENE ARREGLO.

    ⚠️ Un AVISO no entra acá. En el agente viejo `salud` declaraba una acción y
    caía en la lista de trabajo, pero su puerta era de solo lectura: lo único
    que ofrecía era «↻ chequear ahora». **Un aviso con forma de trabajo.**
    """
    from agente import arreglos as arr

    filas = [_serializar(f) for f in _filas("SELECT * FROM agente.v_encontro")]
    cat = {a["id"]: a for a in arr.catalogo()}
    for f in filas:
        f["arreglo_titulo"] = (cat.get(f["arreglo"]) or {}).get("titulo", "")
        f["arreglo_donde"] = (cat.get(f["arreglo"]) or {}).get("donde", "")
    por_habilidad: dict[str, int] = {}
    for f in filas:
        por_habilidad[f["habilidad"]] = por_habilidad.get(f["habilidad"], 0) + 1
    return {"total": len(filas), "filas": filas, "por_habilidad": por_habilidad}


def ignorar(hallazgo_id: int, *, por: str = "", deshacer: bool = False) -> dict:
    """«No me interesa». Reversible, y **no es un arreglo**: esconde, no resuelve."""
    from agente import tipos
    with get_pool().connection() as conn, conn.cursor() as cur:
        if deshacer:
            cur.execute("UPDATE agente.hallazgos SET estado = %s "
                        "WHERE id = %s AND estado = %s",
                        (tipos.NUEVO, hallazgo_id, tipos.IGNORADO))
        else:
            cur.execute("UPDATE agente.hallazgos SET estado = %s, "
                        "  cerrado_por = %s WHERE id = %s AND estado = ANY(%s)",
                        (tipos.IGNORADO, por, hallazgo_id, list(tipos.ABIERTOS)))
        return {"ok": bool(cur.rowcount)}


# ── HISTORIAL ──────────────────────────────────────────────────────────────
LIMITE = 200


def historial(*, limite: int = LIMITE, desde_id: int | None = None,
              q: str = "", solo_malas: bool = False) -> dict:
    """El LIBRO, paginado **del backend**.

    En el agente viejo esto se armaba en el navegador juntando tres fuentes con
    topes distintos (100 acciones, 40 respuestas, 80 votos): cuando el más chico
    se agotaba, la línea de tiempo perdía un tipo de evento y no los otros, sin
    decirlo. Un día aparecía como que «solo tuvo acciones».

    Una fuente, un tope, declarado — y el filtro también es de acá: un buscador
    que solo mira lo que ya bajó no es un buscador.
    """
    limite = max(1, min(int(limite or LIMITE), 500))
    where, params = ["true"], []
    if desde_id:
        where.append("a.id < %s")
        params.append(int(desde_id))
    if solo_malas:
        where.append("a.ok = false")
    if (t := (q or "").strip()):
        where.append("(a.sujeto ILIKE %s OR a.arreglo ILIKE %s OR a.regla ILIKE %s "
                     " OR a.campo ILIKE %s OR a.donde ILIKE %s OR a.por ILIKE %s)")
        params += [f"%{t}%"] * 6

    filas = _filas(
        "SELECT a.*, h.estado AS estado_hoy, h.severidad "
        "  FROM agente.acciones a "
        "  LEFT JOIN agente.hallazgos h ON h.id = a.hallazgo_id "
        f" WHERE {' AND '.join(where)} "
        " ORDER BY a.id DESC LIMIT %s", (*params, limite + 1))
    hay_mas = len(filas) > limite
    filas = [_serializar(f) for f in filas[:limite]]
    return {"filas": filas, "hay_mas": hay_mas,
            "ultimo_id": filas[-1]["id"] if filas else None,
            "limite": limite}


# ── LAS REINCIDENCIAS ──────────────────────────────────────────────────────
def reincidencias(limite: int = 100) -> dict:
    """**La que debe estar VACÍA.** Si tiene filas, algo que dimos por arreglado
    se rompió de nuevo. No es una lista de trabajo: es una alarma."""
    filas = [_serializar(f) for f in _filas(
        "SELECT * FROM agente.reincidencias ORDER BY volvio_at DESC LIMIT %s",
        (int(limite),))]
    return {"total": len(filas), "filas": filas}


# ── TODO EN UN REQUEST ─────────────────────────────────────────────────────
def vista() -> dict:
    """Todo lo que el modal necesita. **UN request.**

    Y con UNA sola noción de «ahora»: el agente viejo mezclaba un endpoint que
    se refrescaba cada 20 s con otro que se cargaba al abrir, y la pantalla
    sumaba los dos números como si hablaran del mismo instante.
    """
    from agente import catalogo, motor
    return {
        "ok": True,
        "latido": motor.vivo(),
        "ahora": ahora(),
        "encontro": encontro(),
        "reincidencias": reincidencias(20),
        "habilidades": [_serializar(h) for h in catalogo.estado()],
    }
