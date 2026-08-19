"""api/services/av_agent_sin_precio.py — POR QUÉ un bono no tiene precio.

Doc madre: **`docs/AV_AGENT.md`** §0.l.

Pedido del user (2026-08-18, señalando AO29 con toda la fila en `--`): *«los
bonos no pueden estar sin un last price así porque sí. Puede ser porque no tiene
liquidez o porque el bono ni siquiera se llegó a conectar en Primary… y más este
bono puntual, que sé que algo mal hay, y no hay diagnóstico, no hay aviso. En los
logs de hoy del motor ROFEX ni siquiera veo un error de esto, como que ni
siquiera llegó a pedirse»*.

**Esa observación sobre el log es la pista, no un detalle.** El filtro de
símbolos (`core/instrumentos_validos`, aplicado en `core/websocket.py::
agregar_suscripciones`) SÍ escribe un warning cuando descarta algo:

    WS motor_rofex: N símbolo(s) NO existen en Primary — no se suscriben: …

Así que un papel sin precio **y sin ese warning** no fue descartado por el
filtro: no llegó hasta él. Y eso deja una sola familia de explicaciones — el
motor nunca pidió ese símbolo. Que el log esté limpio ES evidencia.

**Las causas son distintas y se arreglan distinto**, por eso el detector no
alcanza y hace falta una cadena:

    sin_simbolo        el master no tiene símbolo de mercado → nunca se pide,
                       y por eso tampoco hay warning: el filtro no lo ve.
    fuera_de_primary   el símbolo existe acá pero no en el catálogo de Primary →
                       el filtro lo descarta, CON warning en el log del motor.
    pata_equivocada    se pide una pata sin actividad mientras OTRA del mismo
                       ticker sí tiene precio. El dato está, se pide el símbolo
                       de al lado.
    nunca_opero        se pide bien, existe en Primary, y nunca hubo un trade:
                       **iliquidez, no un error.** Distinguirlo importa tanto
                       como los otros: perseguir un bug que no existe cuesta más
                       que el papel que no opera.
    sin_actividad_hoy  operó antes, hoy no. Con el mercado abierto es una
                       observación; cerrado, es lo normal.

**Cero red y cero créditos**: todo sale de `mercado.curvas`, `mercado.especies`,
`manager.pyrofex_instruments`, `mercado.market_snapshot` y `mercado.timesales`.
"""
from __future__ import annotations

import logging

from api.services.av_agent_alta import (
    INFO,
    NO_SE,
    OK,
    REVISAR,
    _paso,
    _veredicto,
)
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Las causas, con lo que hay que hacer con cada una. `nuestro` distingue lo que
# se arregla en NUESTRA base de lo que depende del mercado — es la diferencia
# entre un bug y un papel ilíquido, y confundirlas es lo que hace perder tardes.
CAUSAS_SIN_PRECIO: dict[str, dict] = {
    "sin_simbolo": {
        "titulo": "El master no tiene símbolo de mercado",
        "arreglo": "cargar `instrumento` en mercado.curvas (o sembrarlo desde "
                   "mercado.especies con `scripts.sembrar_especies`)",
        "nuestro": True},
    "fuera_de_primary": {
        "titulo": "El símbolo no existe en el catálogo de Primary",
        "arreglo": "refrescar el discovery (`scripts.discovery_pyrofex`) y, si "
                   "sigue sin estar, corregir el símbolo: el papel cambió de "
                   "código o nunca se listó con ese nombre",
        "nuestro": True},
    "pata_equivocada": {
        "titulo": "Se pide una pata sin actividad y otra SÍ tiene precio",
        "arreglo": "apuntar `mercado.curvas.instrumento` a la pata que opera",
        "nuestro": True},
    "nunca_opero": {
        "titulo": "Nunca operó — es iliquidez, no un error",
        "arreglo": "nada que arreglar: el papel no tiene mercado",
        "nuestro": False},
    "sin_actividad_hoy": {
        "titulo": "Operó antes, hoy todavía no",
        "arreglo": "esperar: es el mercado, no el sistema",
        "nuestro": False},
    "tiene_precio": {
        "titulo": "Tiene precio",
        "arreglo": "nada", "nuestro": False},
}


def _fila(tk: str) -> dict:
    """Todo lo que hace falta, en UNA conexión. El peaje a Supabase se paga por
    viaje: seis lentes con su propia conexión serían seis viajes para una
    pantalla que tiene que abrir rápido."""
    out: dict = {"ticker": tk}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT instrumento, curva, moneda_eje, ticker "
                    "FROM mercado.curvas WHERE upper(ticker) = %s", (tk,))
        r = cur.fetchone()
        out["en_master"] = bool(r)
        out["simbolo"] = (r[0] or "").strip() if r else ""
        out["curva"] = r[1] if r else None
        out["moneda_eje"] = r[2] if r else None

        cur.execute("SELECT simbolo, especie, moneda, plazo, es_default, validado "
                    "FROM mercado.especies WHERE upper(ticker) = %s "
                    "ORDER BY es_default DESC, simbolo", (tk,))
        out["patas"] = [{"simbolo": a, "especie": b, "moneda": c, "plazo": d,
                         "default": e, "validado": f}
                        for a, b, c, d, e, f in cur.fetchall()]

        # El precio de TODAS las patas: si una tiene y la que pedimos no, la
        # causa es cuál se pide, no que el papel no opere.
        todos = [p["simbolo"] for p in out["patas"]]
        if out["simbolo"]:
            todos.append(out["simbolo"])
        out["precios"] = {}
        if todos:
            cur.execute("SELECT ticker, last_price, updated_at "
                        "FROM mercado.market_snapshot WHERE ticker = ANY(%s)",
                        (list(set(todos)),))
            out["precios"] = {a: {"last_price": float(b) if b is not None else None,
                                  "updated_at": c} for a, b, c in cur.fetchall()}

        # ¿El catálogo REAL de Primary lo conoce? Es la misma fuente que usa el
        # filtro del WS — preguntarle a otra daría un criterio distinto.
        out["en_primary"] = None
        if out["simbolo"]:
            try:
                from core.instrumentos_validos import validos
                v = validos()
                # `None` = el criterio no está disponible y NO se filtra nada.
                # Decirlo es distinto de decir «no está»: sin catálogo no se
                # puede afirmar ninguna de las dos cosas.
                out["en_primary"] = (out["simbolo"] in v) if v is not None else None
                out["catalogo"] = len(v) if v is not None else 0
            except Exception as e:
                logger.warning("sin_precio: no se pudo leer el catálogo: %s", e)

        # ¿Alguna vez operó? El tape tiene retención corta, así que un vacío ahí
        # no prueba nada solo: se cruza con el cierre persistido.
        cur.execute("SELECT count(*) FROM mercado.timesales WHERE ticker = ANY(%s)",
                    (list(set(todos)) or [""],))
        out["trades_recientes"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM mercado.snapshots_cierre "
                    "WHERE upper(ticker) = %s", (tk,))
        out["cierres"] = cur.fetchone()[0]
    return out


# ── Las lentes ──────────────────────────────────────────────────────────────

def _lente_master(d: dict) -> dict:
    if not d["en_master"]:
        return _paso("master", "¿Está en el master?", REVISAR,
                     f"**{d['ticker']} no está en `mercado.curvas`.** Si lo ves en "
                     f"la vista, viene de otro lado.", tabla="mercado.curvas")
    return _paso("master", "¿Está en el master?", OK,
                 f"Sí, en la curva **{d['curva'] or '—'}** "
                 f"(moneda_eje {d['moneda_eje'] or '—'}).", tabla="mercado.curvas")


def _lente_simbolo(d: dict) -> dict:
    """**La lente que explica el log limpio.** Sin símbolo el motor no pide
    nada, y como no pide nada el filtro no descarta nada: no hay warning. El
    silencio del log no es que no haya problema — es el problema."""
    if not d["simbolo"]:
        patas = ", ".join(p["simbolo"].split(" - ")[2] for p in d["patas"][:4]
                          if " - " in p["simbolo"])
        return _paso("simbolo", "¿Qué símbolo le pide el motor?", REVISAR,
                     "**Ninguno: el master no tiene símbolo de mercado.** Por eso "
                     "el motor nunca lo pide, y por eso tampoco hay un warning en "
                     "el log — el filtro de símbolos solo avisa de lo que llega "
                     "hasta él."
                     + (f" `mercado.especies` sí conoce patas para este ticker: "
                        f"{patas}." if patas else " Y tampoco hay patas cargadas."),
                     tabla="mercado.curvas.instrumento")
    return _paso("simbolo", "¿Qué símbolo le pide el motor?", OK,
                 f"«{d['simbolo']}».", tabla="mercado.curvas.instrumento")


def _lente_primary(d: dict) -> dict:
    if not d["simbolo"]:
        return _paso("primary", "¿Ese símbolo existe en Primary?", NO_SE,
                     "No hay símbolo que buscar.", tabla="manager.pyrofex_instruments")
    if d.get("en_primary") is None:
        return _paso("primary", "¿Ese símbolo existe en Primary?", NO_SE,
                     "El catálogo de Primary no está disponible, así que el filtro "
                     "**no está filtrando nada** (degradación elegida: filtrar de "
                     "más deja la mesa sin precios). No se puede afirmar ni que "
                     "existe ni que no.", tabla="manager.pyrofex_instruments")
    if not d["en_primary"]:
        return _paso("primary", "¿Ese símbolo existe en Primary?", REVISAR,
                     f"**No.** El catálogo tiene {d.get('catalogo', 0):,} símbolos y "
                     f"«{d['simbolo']}» no está entre ellos → el filtro lo descarta "
                     f"y el motor NO se suscribe. Esto **sí** deja un warning en el "
                     f"log: «NO existen en Primary — no se suscriben».",
                     tabla="manager.pyrofex_instruments")
    return _paso("primary", "¿Ese símbolo existe en Primary?", OK,
                 f"Sí, está en el catálogo ({d.get('catalogo', 0):,} símbolos). "
                 f"El motor se suscribe.", tabla="manager.pyrofex_instruments")


def _lente_patas(d: dict) -> dict:
    """La comparación que decide entre «el papel no opera» y «pedimos el símbolo
    de al lado». Es la misma idea del control cruzado del arreglo: no se afirma
    la causa, se muestra el contraste que la prueba."""
    if not d["patas"]:
        return _paso("patas", "Las patas del ticker", INFO,
                     "No hay patas en `mercado.especies` para este ticker.",
                     tabla="mercado.especies")
    filas = []
    for p in d["patas"]:
        px = (d["precios"].get(p["simbolo"]) or {}).get("last_price")
        corto = p["simbolo"].split(" - ")[2] if " - " in p["simbolo"] else p["simbolo"]
        filas.append(f"{corto} ({p['especie'] or '?'}/{p['plazo'] or '?'})"
                     f"{' ←se pide' if p['simbolo'] == d['simbolo'] else ''}: "
                     + (f"{px:,.2f}" if px else "sin precio"))
    con_precio = [p for p in d["patas"]
                  if (d["precios"].get(p["simbolo"]) or {}).get("last_price")]
    pedida_sin = not (d["precios"].get(d["simbolo"]) or {}).get("last_price")
    if con_precio and pedida_sin:
        return _paso("patas", "Las patas del ticker", REVISAR,
                     "**Otra pata SÍ tiene precio y la que pedimos no.** " +
                     " · ".join(filas) + ". El dato existe: lo que está mal es "
                     "cuál símbolo se pide.", tabla="mercado.especies")
    return _paso("patas", "Las patas del ticker", INFO, " · ".join(filas),
                 tabla="mercado.especies")


def _lente_historia(d: dict) -> dict:
    if d["cierres"] == 0 and d["trades_recientes"] == 0:
        return _paso("historia", "¿Operó alguna vez?", REVISAR,
                     "**Nunca**: ni un cierre persistido ni un trade en el tape. "
                     "Si el símbolo es correcto y existe en Primary, esto es "
                     "ILIQUIDEZ y no un error — el papel no tiene mercado.",
                     tabla="mercado.snapshots_cierre · mercado.timesales")
    return _paso("historia", "¿Operó alguna vez?", OK,
                 f"Sí: {d['cierres']} cierre/s persistidos y "
                 f"{d['trades_recientes']} trade/s en el tape reciente. "
                 f"O sea que el papel opera — lo de hoy es puntual.",
                 tabla="mercado.snapshots_cierre · mercado.timesales")


def _causa(d: dict) -> str:
    """La causa, ordenada **aguas arriba**: la primera condición que se cumple
    gana, porque las de abajo son consecuencia suya. Sin ese orden, un bono sin
    símbolo saldría como «nunca operó» — que es cierto y no sirve para nada."""
    if (d["precios"].get(d["simbolo"]) or {}).get("last_price"):
        return "tiene_precio"
    if not d["simbolo"]:
        return "sin_simbolo"
    if d.get("en_primary") is False:
        return "fuera_de_primary"
    if any((d["precios"].get(p["simbolo"]) or {}).get("last_price")
           for p in d["patas"] if p["simbolo"] != d["simbolo"]):
        return "pata_equivocada"
    if d["cierres"] == 0 and d["trades_recientes"] == 0:
        return "nunca_opero"
    return "sin_actividad_hoy"


def diagnosticar(ticker: str) -> dict:
    """La cadena completa para UN bono sin precio. Cero red, cero créditos."""
    tk = (ticker or "").strip().upper()
    if len(tk) < 2:
        return {"ok": False, "error": f"ticker inválido: {ticker!r}"}
    try:
        d = _fila(tk)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    pasos = [_lente_master(d), _lente_simbolo(d), _lente_primary(d),
             _lente_patas(d), _lente_historia(d)]
    causa = _causa(d)
    c = CAUSAS_SIN_PRECIO[causa]
    pasos.append(_paso(
        "conclusion", "⇒ LA CONCLUSIÓN",
        OK if causa in ("tiene_precio", "nunca_opero", "sin_actividad_hoy") else REVISAR,
        f"**{c['titulo']}**"
        + (f"\n\nArreglo: {c['arreglo']}" if c["nuestro"] else
           f"\n\n{c['arreglo']} — **no es un bug nuestro.** Distinguirlo importa: "
           f"perseguir un problema que no existe cuesta más que el papel que no "
           f"opera.")))
    for i, p in enumerate(pasos, 1):
        p["n"] = i

    from api.services import av_agent_memoria as mem
    mem.registrar_traza(caso=tk, dominio="sin_precio", causa=causa,
                        veredicto=causa,
                        observaciones=[{"clave": p["clave"], "estado": p["estado"],
                                        "detalle": p["detalle"]} for p in pasos],
                        contexto={"simbolo": d["simbolo"], "curva": d["curva"],
                                  "patas": len(d["patas"])})
    return {"ok": True, "modo": "sin_precio", "ticker": tk, "causa": causa,
            "titulo": c["titulo"], "nuestro": c["nuestro"],
            "simbolo": d["simbolo"], "chequeos": pasos,
            "veredicto": _veredicto(pasos)}
