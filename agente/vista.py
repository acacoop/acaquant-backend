"""`agente/vista.py` — el READ MODEL. Doc: `AGENT.md` §6.

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


def ignorar(hallazgo_id: int, *, por: str = "", motivo: str = "",
            deshacer: bool = False) -> dict:
    """«No me interesa». Reversible, y **no es un arreglo**: esconde, no resuelve.

    ⚠️⚠️ **SILENCIA EL PROBLEMA, NO LA FILA.** Marcar el hallazgo como
    `ignorado` —lo único que hacía antes— no alcanza y por eso el botón no
    servía: `registro._ver` busca por el TRÍO entre los ABIERTOS, un ignorado no
    está abierto, y en la pasada siguiente nacía una fila nueva en `nuevo`. El
    índice único tampoco chocaba, porque excluye `ignorado`. El bono descartado
    volvía a las dos horas como si fuera la primera vez.

    Van las DOS cosas y en este orden:

      1. la fila se marca `ignorado`, para que salga de la pantalla AHORA;
      2. el TRÍO entra en `agente.silenciados`, para que no vuelva a nacer.

    Sin (2) el botón miente; sin (1) el aviso sigue en la lista hasta la próxima
    corrida. **Permanente por defecto** (`hasta` NULL): el vencimiento se carga a
    mano en la base cuando alguien lo elige, no lo pone el código.
    """
    from agente import tipos
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT habilidad, sujeto, regla FROM agente.hallazgos "
                    " WHERE id = %s", (hallazgo_id,))
        if not (f := cur.fetchone()):
            return {"ok": False, "error": "ese hallazgo no existe"}
        trio = (f[0], f[1], f[2])

        if deshacer:
            cur.execute("DELETE FROM agente.silenciados "
                        " WHERE habilidad = %s AND sujeto = %s AND regla = %s", trio)
            cur.execute("UPDATE agente.hallazgos SET estado = %s, cerrado_por = '' "
                        " WHERE id = %s AND estado = %s",
                        (tipos.NUEVO, hallazgo_id, tipos.IGNORADO))
            return {"ok": True, "silenciado": False}

        cur.execute(
            "INSERT INTO agente.silenciados (habilidad, sujeto, regla, por, motivo) "
            "VALUES (%s,%s,%s,%s,%s) "
            # Re-silenciar lo ya silenciado no es un error: actualiza quién y por
            # qué, y **renueva el «para siempre»** limpiando un vencimiento que
            # hubiera quedado de antes.
            "ON CONFLICT (habilidad, sujeto, regla) DO UPDATE SET "
            "  por = EXCLUDED.por, motivo = EXCLUDED.motivo, "
            "  desde = now(), hasta = NULL",
            (*trio, por, motivo))
        cur.execute("UPDATE agente.hallazgos SET estado = %s, cerrado_por = %s "
                    " WHERE id = %s AND estado = ANY(%s)",
                    (tipos.IGNORADO, por, hallazgo_id, list(tipos.ABIERTOS)))
        return {"ok": True, "silenciado": True,
                "habilidad": trio[0], "sujeto": trio[1], "regla": trio[2]}


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

    # ⚠️⚠️ **EL ESTADO DEL HALLAZGO NO SIEMPRE HABLA DE ESTA LÍNEA.**
    #
    # User (2026-08-28), viendo nueve títulos que acababa de completar, cada uno
    # con su ✔ y su `emisor: — → OTROS`, y al lado «el aviso sigue abierto»:
    # *«¿por qué no pone CONFIRMADO? ¿qué tienen que ver los demás?»*.
    #
    # Y no tienen nada que ver. Cuando la acción es sobre UN título y el
    # hallazgo es de TODO el campo, el estado del hallazgo habla de los OTROS
    # —los que faltan— y en ese renglón se lee como una duda sobre la escritura
    # que la propia línea ya está afirmando.
    #
    # Se distingue con el dato, no con una lista: **si el sujeto de la acción es
    # distinto del sujeto del hallazgo, la acción es de un item y el hallazgo de
    # un grupo.** Ahí la línea contesta por sí sola y `estado_hoy` viaja vacío.
    filas = _filas(
        "SELECT a.*, h.severidad, "
        "       CASE WHEN h.id IS NULL OR h.sujeto IS DISTINCT FROM a.sujeto "
        "            THEN NULL ELSE h.estado END AS estado_hoy "
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
