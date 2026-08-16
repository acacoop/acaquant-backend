"""api/services/av_agent_preguntas.py — el AV AGENT le PREGUNTA al humano (E1.c).

Doc madre: **`docs/AV_AGENT.md`**.

**El problema que resuelve.** Un agente que necesita una decisión y no tiene cómo
pedirla se bloquea, y bloquea al proyecto: cada duda había que resolverla en un
chat, a mano, y la respuesta se perdía ahí. Acá el agente **convierte la duda en
una pregunta, la deja anotada y sigue con lo que sí puede hacer**. El user
contesta cuando puede — no cuando el agente corre.

**Tres propiedades que la hacen funcionar, y ninguna es opcional:**

1. **No repregunta.** La `clave` es única y estable (`falta:TZXD8`). Una
   herramienta que vuelve a preguntar lo mismo todas las noches se deja de leer,
   igual que una lista que repite lo descartado.
2. **Responder DISPARA un efecto**, no anota una opinión: `ignorar` escribe en
   `mercado.av_agent_ignorados` y ese ticker no vuelve a salir nunca. Por eso
   `aplicada_at` es distinto de `respondida_at` — una respuesta cuyo efecto falló
   no puede quedar como si hubiera surtido.
3. **La pregunta se entiende sola.** Se lee semanas después de escrita, sin el
   hilo de chat que la originó: por eso lleva el contexto congelado adentro.

**Sin IA todavía, a propósito.** Las preguntas se arman deterministas desde los
hallazgos. Cuando llegue E4, el modelo va a redactarlas mejor y a agrupar las que
son la misma pregunta — pero el MECANISMO tiene que existir antes, porque es el
que convierte las respuestas en datos y no en mensajes.
"""
from __future__ import annotations

import json
from typing import Any

from core.postgres import get_pool

# Qué puede contestarse a una pregunta de hallazgo, y qué hace cada respuesta.
# `despues` existe para que "no sé todavía" sea una respuesta legítima: sin ella
# la única forma de no decidir es no contestar, y entonces no se distingue "lo
# pensé y lo dejo para después" de "no lo vi".
RESPUESTAS_FALTA = ("alta", "ignorar", "despues")


def _row(r, cols: list[str]) -> dict:
    return dict(zip(cols, r, strict=False))


# ── Generación ───────────────────────────────────────────────────────────────


def preguntas_de_hallazgos(hallazgos: list[dict]) -> list[dict]:
    """Hallazgos → preguntas candidatas (PURA: sin base, testeable sin Postgres).

    Hoy solo pregunta por los **faltantes**, y es deliberado: es el único hallazgo
    donde la decisión es del negocio y no técnica. *"¿Este bono nuevo nos
    interesa?"* no lo puede contestar ninguna regla — depende de si la mesa lo
    opera. En cambio *"¿por qué este bono tiene paridad 150.000%?"* NO es una
    pregunta para el user: es trabajo del agente (E4), y mandársela sería
    delegarle el laburo que vino a hacer.
    """
    out: list[dict] = []
    for h in hallazgos:
        if h.get("tipo") != "falta_en_base":
            continue
        tk = h["ticker"]
        ev = h.get("evidencia") or {}
        out.append({
            "clave": f"falta:{tk}",
            "tipo": "hallazgo",
            "pregunta": _texto_falta(tk, ev),
            "opciones": list(RESPUESTAS_FALTA),
            "contexto": ev,
        })
    return out


def _texto_falta(tk: str, ev: dict) -> str:
    """La pregunta de un faltante, con el contexto para poder contestarla.

    **Un ticker solo no es una pregunta contestable.** `M31G6` no le dice nada a
    nadie: hace falta QUIÉN emite y QUÉ es. El emisor y la denominación vienen
    del catálogo de 1816 (`emisorNombre` / `denominacion`), que el censo ya trae
    en el mismo crédito — no cuesta una llamada más.

    Y lo primero de todo es **si la casa ya lo tiene**: un bono en la tenencia
    que no está en `mercado.curvas` no valúa, así que ahí la respuesta deja de
    ser una preferencia y pasa a ser un arreglo pendiente."""
    partes: list[str] = []
    if ev.get("en_cartera"):
        partes.append("⚠ LO TENÉS EN CARTERA (hoy no valúa)")
    emisor = (ev.get("emisor") or "").strip()
    if emisor:
        partes.append(emisor)
    den = (ev.get("denominacion") or "").strip()
    # La denominación repite el ticker en muchos casos ("AL30 - BONAR 2030"): se
    # muestra solo si agrega algo, si no es ruido en una lista de 24.
    if den and den.upper() != tk.upper():
        partes.append(den)
    curva = ev.get("curva_1816") or "?"
    partes.append(f"1816: «{curva}»")
    if ev.get("moneda"):
        partes.append(str(ev["moneda"]))
    vto = ev.get("vencimiento_1816")
    if vto:
        partes.append(f"vence {str(vto)[:10]}")
    return f"{tk} — " + " · ".join(partes) + ". ¿Lo damos de alta o no nos interesa?"


def registrar(preguntas: list[dict]) -> int:
    """Inserta las nuevas y REFRESCA el texto de las que siguen abiertas.
    → cuántas filas se tocaron.

    Dos reglas que conviven y no se contradicen:

    · **No se repregunta**: la `clave` es única, así que una pregunta ya hecha no
      genera otra fila.
    · **Pero sí se mejora el enunciado**: si el agente aprende a decirlo mejor
      (el 2026-08-16 se le sumaron emisor, denominación y «lo tenés en cartera»),
      las que están ABIERTAS tienen que reflejarlo. Con `DO NOTHING` las 21 que ya
      estaban se quedaban con el texto viejo para siempre y había que borrarlas a
      mano para verlas bien.

    **`WHERE estado = 'abierta'` es la parte que no se puede saltear**: una
    pregunta ya respondida se congela con el texto y el contexto que tenía cuando
    se contestó. Reescribirla haría que el historial diga que se decidió sobre
    una evidencia que en ese momento no existía."""
    if not preguntas:
        return 0
    filas = [(p["clave"], p["tipo"], p["pregunta"],
              json.dumps(p["opciones"], ensure_ascii=False),
              json.dumps(p.get("contexto") or {}, ensure_ascii=False, default=str))
             for p in preguntas]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO mercado.av_agent_preguntas "
            "(clave, tipo, pregunta, opciones, contexto) "
            "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb) "
            "ON CONFLICT (clave) DO UPDATE SET "
            "  pregunta = EXCLUDED.pregunta, opciones = EXCLUDED.opciones, "
            "  contexto = EXCLUDED.contexto "
            "WHERE mercado.av_agent_preguntas.estado = 'abierta'",
            filas)
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def registrar_decisiones(decisiones: list[dict]) -> int:
    """Las decisiones de DISEÑO del propio agente (alcance, política de conflicto).

    Su efecto no es automático: lo aplica el desarrollo siguiente. Se guardan
    igual para que la decisión quede asentada donde el agente la va a buscar, y
    no perdida en un chat."""
    return registrar([{**d, "tipo": "decision"} for d in decisiones])


# ── Lectura ──────────────────────────────────────────────────────────────────


_COLS = ["id", "clave", "tipo", "pregunta", "opciones", "contexto", "estado",
         "respuesta", "nota", "respondida_por", "respondida_at", "aplicada_at"]


def abiertas(limite: int = 200) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(_COLS)} FROM mercado.av_agent_preguntas "
                    "WHERE estado = 'abierta' ORDER BY tipo DESC, id LIMIT %s", (limite,))
        return [_row(r, _COLS) for r in cur.fetchall()]


def resumen() -> dict:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT estado, count(*) FROM mercado.av_agent_preguntas "
                    "GROUP BY estado")
        d = dict(cur.fetchall())
    return {"abiertas": d.get("abierta", 0), "respondidas": d.get("respondida", 0)}


# ── Respuesta + EFECTO ───────────────────────────────────────────────────────


class RespuestaInvalida(ValueError):
    """La respuesta no está entre las opciones de esa pregunta."""


def responder(id_pregunta: int, respuesta: str, *, por: str = "",
              nota: str = "") -> dict:
    """Guarda la respuesta y **aplica su efecto**. → la pregunta actualizada.

    El orden importa: primero el efecto, después el sello. Si el efecto falla, la
    pregunta queda respondida pero con `aplicada_at` en NULL y eso se ve — es la
    misma distinción que hace `saldo_cargado` en Tesorería entre "cargado en cero"
    y "sin cargar". Marcar como aplicada algo que no surtió es la clase de mentira
    que después nadie encuentra.
    """
    resp = (respuesta or "").strip().lower()
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(_COLS)} FROM mercado.av_agent_preguntas "
                    "WHERE id = %s", (id_pregunta,))
        fila = cur.fetchone()
    if not fila:
        raise RespuestaInvalida(f"no existe la pregunta {id_pregunta}")
    p = _row(fila, _COLS)
    opciones = p.get("opciones") or []
    if resp not in opciones:
        raise RespuestaInvalida(
            f"«{respuesta}» no es una opción de la pregunta {id_pregunta} "
            f"({', '.join(opciones)})")

    aplicada = _aplicar_efecto(p, resp, por=por, nota=nota)

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mercado.av_agent_preguntas SET estado = 'respondida', "
            "respuesta = %s, nota = %s, respondida_por = %s, respondida_at = now(), "
            "aplicada_at = CASE WHEN %s THEN now() ELSE NULL END WHERE id = %s",
            (resp, nota or None, por or None, aplicada, id_pregunta))
    return {**p, "estado": "respondida", "respuesta": resp, "aplicada": aplicada}


def _aplicar_efecto(p: dict, resp: str, *, por: str, nota: str) -> bool:
    """→ True si la respuesta surtió un efecto CONCRETO en la base.

    `alta` y `despues` devuelven False HOY y no es un bug: dar de alta un bono
    necesita bajar su cuadro de flujos y simular su TEA, que es E2. La respuesta
    queda guardada y el día que exista E2, esas son exactamente las que procesa.
    Decir que se aplicó algo que todavía no se puede hacer sería peor que decir
    que no."""
    if p.get("tipo") != "hallazgo" or not p.get("clave", "").startswith("falta:"):
        return False
    if resp != "ignorar":
        return False
    ticker = p["clave"].split(":", 1)[1]
    motivo = nota or "el user lo marcó como «no nos interesa» desde el AV Agent"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mercado.av_agent_ignorados (ticker, motivo, por) "
            "VALUES (%s, %s, %s) ON CONFLICT (ticker) DO NOTHING",
            (ticker.upper(), motivo, por or None))
    return True


def designorar(ticker: str, *, por: str = "") -> dict:
    """Deshace un «no me interesa»: saca el ticker de la lista y **reabre su
    pregunta**, para que el agente vuelva a proponerlo.

    Reabrir la pregunta es la mitad que importa: sin eso el ticker volvería a
    salir como hallazgo pero sin nada que contestar, y la decisión quedaría
    colgada entre "ya la contesté" y "no la contesté".

    **La reversibilidad es lo que hace barata la decisión.** Si `ignorar` fuera
    irreversible desde la app, la respuesta segura pasaría a ser no contestar
    nada — y el canal de preguntas dejaría de usarse."""
    tk = (ticker or "").strip().upper()
    if not tk:
        raise ValueError("ticker vacío")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mercado.av_agent_ignorados WHERE ticker = %s", (tk,))
        borrado = cur.rowcount or 0
        cur.execute(
            "UPDATE mercado.av_agent_preguntas SET estado = 'abierta', "
            "respuesta = NULL, respondida_at = NULL, aplicada_at = NULL, "
            "nota = NULL, respondida_por = %s WHERE clave = %s",
            (por or None, f"falta:{tk}"))
        reabierta = (cur.rowcount or 0) > 0
    return {"ok": True, "ticker": tk, "borrado": borrado > 0, "reabierta": reabierta}


# ── Parseo del comando del user ──────────────────────────────────────────────


def parsear_respuestas(texto: str) -> list[tuple[int, str]]:
    """`"3=alta,5-9=ignorar"` → `[(3,'alta'), (5,'ignorar'), …]`. PURA.

    Acepta RANGOS porque la forma real de contestar 24 preguntas de faltantes es
    "estas cinco sí, el resto no", y obligar a tipear 24 asignaciones en la
    consola web del Droplet garantiza que no se conteste nunca (REGLA #0: lo que
    es doloroso de tipear ahí, no se usa).
    """
    out: list[tuple[int, str]] = []
    for parte in (texto or "").split(","):
        parte = parte.strip()
        if not parte:
            continue
        if "=" not in parte:
            raise ValueError(f"«{parte}» no tiene la forma ID=respuesta")
        izq, resp = parte.split("=", 1)
        izq, resp = izq.strip(), resp.strip().lower()
        if not resp:
            raise ValueError(f"«{parte}» no tiene respuesta")
        if "-" in izq:
            a, b = izq.split("-", 1)
            try:
                desde, hasta = int(a), int(b)
            except ValueError as e:
                raise ValueError(f"rango inválido en «{parte}»") from e
            if hasta < desde:
                raise ValueError(f"rango al revés en «{parte}»")
            out += [(i, resp) for i in range(desde, hasta + 1)]
        else:
            try:
                out.append((int(izq), resp))
            except ValueError as e:
                raise ValueError(f"«{izq}» no es un id") from e
    return out


def responder_lote(texto: str, *, por: str = "", nota: str = "") -> dict:
    """Aplica un lote y **nunca corta a la mitad**: una respuesta inválida no
    puede impedir que las otras 23 se guarden. Devuelve qué entró y qué no —
    con el motivo de cada rechazo, porque un lote que dice "falló" sin decir cuál
    obliga a repetirlo entero.

    Un id que no existe (típico de un rango que se pasa) se cuenta como `omitido`,
    no como error: es la forma natural de decir "de la 5 a la 30, todas ignorar"."""
    resultados: list[dict] = []
    ok = omitidos = fallidos = 0
    for id_p, resp in parsear_respuestas(texto):
        try:
            r = responder(id_p, resp, por=por, nota=nota)
            ok += 1
            resultados.append({"id": id_p, "respuesta": resp, "ok": True,
                               "aplicada": r.get("aplicada", False)})
        except RespuestaInvalida as e:
            if "no existe" in str(e):
                omitidos += 1
                resultados.append({"id": id_p, "ok": False, "omitido": True})
            else:
                fallidos += 1
                resultados.append({"id": id_p, "ok": False, "error": str(e)})
        except Exception as e:  # la base falló: se reporta, no se traga
            fallidos += 1
            resultados.append({"id": id_p, "ok": False, "error": f"{type(e).__name__}: {e}"})
    return {"ok": ok, "omitidos": omitidos, "fallidos": fallidos,
            "detalle": resultados}


# ── Las decisiones de DISEÑO abiertas (docs/AV_AGENT.md §4) ──────────────────
#
# Viven acá y no solo en el doc para que el agente pueda PREGUNTARLAS: una
# decisión que solo existe en un markdown depende de que alguien lo lea.
DECISIONES_ABIERTAS: list[dict[str, Any]] = [
    {"clave": "decision:alcance",
     "pregunta": ("¿Qué universo querés que mire el agente? «soberanos» son 104 "
                  "en 1816; «todo» son 887 (667 corporativos, donde la escala del "
                  "flujo varía mucho más)."),
     "opciones": ["soberanos", "no_corporativos", "todo"]},
    {"clave": "decision:conflicto",
     "pregunta": ("Cuando nuestro cuadro de flujos y el de 1816 difieren (caso "
                  "AER9O: 48.215,20 contra 49.710,88, un 3,1%), ¿1816 PISA "
                  "nuestro dato o solo lo reporta para que decidas vos?"),
     "opciones": ["pisa", "reporta"]},
    {"clave": "decision:diagnostico",
     "pregunta": ("El diagnóstico con IA (E4): ¿barre las 221 curvas todas las "
                  "noches buscando tasas raras, o es una herramienta que le "
                  "apuntás a un bono cuando ves algo?"),
     "opciones": ["barrido", "apuntado"]},
]
