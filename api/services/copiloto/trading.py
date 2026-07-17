"""copiloto/trading.py — vista TRADING (monitor intradía): tarjetas con pivots
live, libro, tape, movers, reloj de mercado y reglas. También expone
_estado_mercado, _sanear_params_trading y _fetch_trading para el VIGÍA."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from .base import _num, _wavg, _zona

logger = logging.getLogger(__name__)

_MAX_TARJETAS = 12


def _sanear_params_trading(params: dict | None) -> tuple[list[str], str | None, dict]:
    p = params if isinstance(params, dict) else {}
    tickers: list[str] = []
    for t in (p.get("tickers") or [])[:_MAX_TARJETAS]:
        t = str(t).strip().upper()
        if t and len(t) <= 12 and t.replace(".", "").isalnum() and t not in tickers:
            tickers.append(t)
    sel = str(p.get("seleccionado") or "").strip().upper()
    seleccionado = sel if sel in tickers else (tickers[0] if tickers else None)
    overrides: dict[str, dict] = {}
    ov_crudos = p.get("overrides") if isinstance(p.get("overrides"), dict) else {}
    for tk, ov in ov_crudos.items():
        tk = str(tk).strip().upper()
        if tk not in tickers or not isinstance(ov, dict):
            continue
        limpio = {}
        for k in ("high", "low", "close"):
            try:
                v = float(ov.get(k))
                if v > 0:
                    limpio[k] = v
            except (TypeError, ValueError):
                continue
        if limpio:
            overrides[tk] = limpio
    return tickers, seleccionado, overrides


def _px_dif(last: float | None, precio: float | None) -> str | None:
    """Celda 'precio (dif%)' — el mismo toggle PRECIO/DIF% de la vista, ya
    calculado (la política es cero aritmética del modelo)."""
    if precio is None:
        return None
    if not last:
        return f"{precio:.2f}"
    return f"{precio:.2f} ({(precio / last - 1) * 100:+.2f}%)"


def _fetch_trading(params: dict | None = None) -> list[dict]:
    """Las tarjetas del trader como filas: pivots de trading_pivots (live, sin
    cache) con los overrides del usuario re-aplicados server-side, cada nivel con
    su distancia al last YA calculada, zona actual, nivel más cercano y el día/
    rubro del papel (para la alineación de tendencia). GARANTÍA: toda card
    saneada aparece en el resultado — si un ticker no resuelve datos, entra igual
    como 'sin datos' (nunca se dropea en silencio: el modelo no puede negarle al
    trader una card que él tiene en pantalla)."""
    from api.services import scanner_sql, trading_pivots
    from quant.pivot_points import calcular

    tickers, seleccionado, overrides = _sanear_params_trading(params)
    if not tickers:
        return []
    try:
        por_ticker = {s.get("ticker_corto"): s for s in scanner_sql.get_cedears_scanner()}
    except Exception as e:
        logger.warning("copiloto trading: scanner para día/rubro falló (%s)", e)
        por_ticker = {}
    filas = []
    for it in trading_pivots.get_pivots(tickers=tickers):
        f = dict(it)
        tk = f.get("ticker")
        ov = overrides.get(tk) or {}
        h = ov.get("high", f.get("high"))
        lo = ov.get("low", f.get("low"))
        c = ov.get("close", f.get("close"))
        piv = f.get("pivots") or {}
        if ov and h and lo and c:
            piv = calcular(high=float(h), low=float(lo), close=float(c))
        last = f.get("last")
        f.update(high=h, low=lo, close=c)
        for k in ("pp", "r1", "r2", "r3", "s1", "s2", "s3"):
            f[k] = _px_dif(float(last) if last else None, piv.get(k))
        if last and piv.get("pp"):
            f["zona"] = _zona(float(last), piv)
            nombre, precio = min(
                ((n.upper(), p) for n, p in piv.items() if p),
                key=lambda np: abs(float(last) - np[1]),
            )
            dist = (precio / float(last) - 1) * 100
            f["nivel_cercano"] = f"{nombre} a {dist:+.2f}%"
            # numérico para el vigía (no va al TSV — campos con _)
            f["_nivel_nombre"], f["_nivel_precio"], f["_nivel_dist"] = nombre, precio, dist
        else:
            f["zona"] = None
            f["nivel_cercano"] = None
            f["_nivel_nombre"] = f["_nivel_precio"] = f["_nivel_dist"] = None
        s = por_ticker.get(tk) or {}
        f["dia_pct"] = s.get("vs_1d_pct")
        f["rubro"] = s.get("rubro")
        f["foco"] = tk == seleccionado
        filas.append(f)
    # Ninguna card se pierde: las que no resolvieron datos entran como "sin datos"
    # (así el copiloto reconoce la card en vez de jurar que no existe).
    resueltos = {str(f.get("ticker", "")).upper() for f in filas}
    for tk in tickers:
        if tk not in resueltos:
            filas.append({"ticker": tk, "zona": "sin datos ahora",
                          "foco": tk == seleccionado})
    return filas


def _sanear_posiciones(params: dict | None) -> list[dict]:
    """Posiciones abiertas del monitor INTRADAY (viajan del cliente porque son
    efímeras — el excel vive en su browser). Solo números validados."""
    p = params if isinstance(params, dict) else {}
    out = []
    for pos in (p.get("posiciones") or [])[:12]:
        if not isinstance(pos, dict):
            continue
        esp = str(pos.get("especie") or "").strip().upper()
        estado = str(pos.get("estado") or "").strip().upper()
        if not esp or len(esp) > 12 or estado not in ("LONG", "SHORT"):
            continue
        try:
            qty = abs(float(pos.get("qty")))
            precio = float(pos.get("precio"))
        except (TypeError, ValueError):
            continue
        if qty > 0 and precio > 0:
            out.append({"especie": esp, "estado": estado, "qty": qty, "precio": precio})
    return out


def _libro_resumen(ticker: str) -> list[str]:
    """Libro del activo enfocado, CI y 24hs: mejores puntas, spread y
    desbalance calculados por código (misma cuenta que el panel)."""
    from api.services import order_book

    partes = []
    for plazo in ("CI", "24hs"):
        try:
            ob = order_book.get_order_book(ticker, plazo)
        except Exception as e:
            logger.warning("copiloto trading: libro %s %s falló (%s)", ticker, plazo, e)
            continue
        book = (ob or {}).get("book") or {}
        bids, offers = book.get("bids") or [], book.get("offers") or []
        if not bids and not offers:
            continue
        tot_bid = sum(b.get("size") or 0 for b in bids)
        tot_off = sum(o.get("size") or 0 for o in offers)
        linea = f"[libro {ticker} {plazo}]"
        if bids:
            linea += f" mejor compra {_num(bids[0].get('price'))} x{bids[0].get('size')}"
        if offers:
            linea += f" · mejor venta {_num(offers[0].get('price'))} x{offers[0].get('size')}"
        if bids and offers:
            spread = float(offers[0]["price"]) - float(bids[0]["price"])
            linea += f" · spread {_num(spread)}"
        if tot_bid + tot_off:
            pct_bid = round(100 * tot_bid / (tot_bid + tot_off))
            # ambos lados YA calculados: el modelo no puede derivar 100−x
            # (verificación estricta) y el desbalance se cita por cualquiera
            linea += (f" · profundidad {tot_bid} nominales comprando vs {tot_off} vendiendo "
                      f"· desbalance {pct_bid}% comprador / {100 - pct_bid}% vendedor")
        partes.append(linea)
    return partes


def _tape_resumen(ticker: str) -> list[str]:
    """Time & sales de hoy resumido por código: volumen, presión BUY/SELL,
    rango y últimos trades."""
    from api.services import scanner

    try:
        trades = scanner.get_cedears_trades(ticker=ticker, limite=500) or []
    except Exception as e:
        logger.warning("copiloto trading: tape %s falló (%s)", ticker, e)
        return []
    if not trades:
        return [f"[tape {ticker}] sin trades en la rueda de hoy"]
    n = len(trades)
    monto = sum(t.get("money") or 0 for t in trades)
    compras = sum(t.get("money") or 0 for t in trades if t.get("side") == "BUY")
    ventas = sum(t.get("money") or 0 for t in trades if t.get("side") == "SELL")
    precios = [t.get("price") for t in trades if t.get("price")]
    ult = trades[:5]  # vienen desc por ts

    def _hora(t: dict) -> str:
        ts = str(t.get("timestamp") or "")
        return ts[11:16] if len(ts) >= 16 else ts

    lineas = [
        f"[tape {ticker}] {n} trades hoy · monto {_num(monto)} ARS · rango "
        f"{_num(min(precios))}-{_num(max(precios))}"
        + (f" · presión: {round(100 * compras / (compras + ventas))}% del monto fue "
           f"agresión compradora" if compras + ventas else ""),
        "últimos trades (hora precio x nominales lado): "
        + "; ".join(f"{_hora(t)} {_num(t.get('price'))} x{t.get('size')} {t.get('side')}"
                    for t in ult),
    ]
    return lineas


def _movers_resumen(umbral: float = 4.0) -> list[str]:
    """Movers ±umbral% del día (mismo criterio que el radar de la vista),
    filtrados por código."""
    from api.services import scanner_sql

    try:
        filas = scanner_sql.get_cedears_scanner()
    except Exception as e:
        logger.warning("copiloto trading: movers fallaron (%s)", e)
        return []
    movers = []
    for f in filas:
        d = f.get("vs_1d_pct")
        if d is not None and abs(d) >= umbral:
            movers.append((abs(d), f"{f.get('ticker_corto')} {d:+.1f}%"
                           + (f" ({f.get('rubro')})" if f.get("rubro") else "")))
    movers.sort(reverse=True)
    if not movers:
        return [f"[movers ±{umbral:.0f}%] ninguno hoy"]
    return [f"[movers ±{umbral:.0f}% del día] " + "; ".join(s for _, s in movers[:12])]


def _estado_mercado(ahora_art: datetime, es_habil: bool) -> tuple[str, str]:
    """(estado, lectura de disciplina) — el mapa horario de la mesa (user
    2026-07-12): rueda 10:30-17:00 ART solo hábiles; 13-16 el mercado está
    MUERTO y el asistente ayuda a NO operar (anti-overtrading). Puro para
    poder testearlo."""
    hora = ahora_art.hour + ahora_art.minute / 60
    if not es_habil:
        return ("CERRADO (no es día hábil)",
                "los datos que ves son de la última rueda — no hay nada que operar hoy")
    if hora < 10.5:
        return ("PRE-APERTURA (abre 10:30)",
                "todavía no abrió — el libro puede estar armándose, no saques conclusiones")
    if hora < 13:
        return ("RUEDA VIVA — tramo de la mañana",
                "el tramo con volumen real de la rueda")
    if hora < 16:
        return ("ZONA MUERTA (13 a 16)",
                "el mercado está MUERTO a esta hora: poco volumen, libro poco representativo, "
                "movimientos engañosos. Tu regla: NO operar en esta franja — si el usuario "
                "insinúa entrar ahora, recordáselo primero (anti-overtrading)")
    if hora < 17:
        return ("ÚLTIMO TRAMO (16 a 17)",
                "vuelve el volumen hacia el cierre — los movimientos valen de nuevo, "
                "pero cuidado con quedarse comprado sobre el final")
    return ("CERRADO (cerró 17:00)",
            "rueda terminada — los datos son el cierre de hoy, no hay nada que operar")


def _reloj_mercado() -> list[str]:
    from datetime import timedelta

    ahora_art = datetime.now(UTC) - timedelta(hours=3)
    es_habil = ahora_art.weekday() < 5
    if es_habil:
        try:
            from core.postgres import get_pool

            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1 FROM mercado.dias_habiles WHERE fecha = %s",
                            (ahora_art.date(),))
                es_habil = cur.fetchone() is not None
        except Exception as e:
            logger.warning("copiloto: dias_habiles no disponible (%s) — asumo hábil", e)
    estado, lectura = _estado_mercado(ahora_art, es_habil)
    dia = ("lunes", "martes", "miércoles", "jueves", "viernes",
           "sábado", "domingo")[ahora_art.weekday()]
    return [f"[reloj de mercado] {dia} {ahora_art:%H:%M} ART — {estado}. Lectura: {lectura}."]


def _tendencia_rubros_cards(filas_cards: list[dict]) -> list[str]:
    """Día del RUBRO de cada tarjeta (ponderado por monto ARS, por código):
    la pata 'rubro' del checklist de alineación de tendencia."""
    from api.services import scanner_sql

    rubros = {f.get("rubro") for f in filas_cards if f.get("rubro")}
    if not rubros:
        return []
    try:
        todas = scanner_sql.get_cedears_scanner()
    except Exception as e:
        logger.warning("copiloto trading: tendencia rubros falló (%s)", e)
        return []
    partes = []
    for rubro in sorted(rubros):
        grupo = [s for s in todas if s.get("rubro") == rubro]
        prom = _wavg([{**s, "adr_dollar_vol": s.get("total_money")} for s in grupo],
                     "vs_1d_pct")
        if prom is not None:
            partes.append(f"{rubro} {prom:+.2f}% hoy ({len(grupo)} papeles)")
    return ["[tendencia rubros de tus tarjetas] " + " · ".join(partes)] if partes else []


def _extras_trading(
    filas: list[dict], pregunta: str, historial: list[dict], params: dict | None = None
) -> list[str]:
    from api.services import scanner

    _tickers, seleccionado, _ov = _sanear_params_trading(params)
    partes: list[str] = []
    partes.extend(_reloj_mercado())
    posiciones = _sanear_posiciones(params)
    if posiciones:
        partes.append(
            "[mis posiciones abiertas — monitor INTRADAY] "
            + " · ".join(f"{p['estado']} {p['qty']:.0f} {p['especie']} a {p['precio']:.2f}"
                         for p in posiciones)
        )
    try:
        ccl = scanner.get_ccl_live() or {}
        if ccl.get("value") is not None:
            linea = f"[CCL live] {_num(ccl['value'])} ARS/USD"
            if ccl.get("vs_1d_pct") is not None:
                linea += f" · vs cierre anterior {ccl['vs_1d_pct']:+.2f}%"
            partes.append(linea)
    except Exception as e:
        logger.warning("copiloto trading: CCL falló (%s)", e)
    try:
        from api.services import market_sql

        for q in market_sql.quotes(symbols=["SPY", "QQQ"]) or []:
            if q.get("last") is not None:
                partes.append(f"[{q.get('symbol')}] {_num(q.get('last'))}"
                              + (f" ({q.get('pct_day'):+.2f}% hoy)"
                                 if q.get("pct_day") is not None else ""))
    except Exception as e:
        logger.warning("copiloto trading: SPY/QQQ fallaron (%s)", e)
    partes.extend(_tendencia_rubros_cards(filas))
    if seleccionado:
        partes.extend(_libro_resumen(seleccionado))
        partes.extend(_tape_resumen(seleccionado))
    partes.extend(_movers_resumen())
    return partes


_REGLAS_TRADING = """Sos el copiloto de la vista TRADING: acá el usuario OPERA en vivo. Sus \
tarjetas (la tabla) son los papeles que él eligió; "foco: si" es el que tiene en el chart, \
libro y tape. Precios en ARS del CEDEAR.

La tabla de tarjetas es EXACTAMENTE lo que el trader tiene en pantalla — no hay más ni \
menos. Si una card aparece con zona "sin datos ahora", ESA CARD EXISTE (el trader la tiene) \
pero no resolvió precio en este momento: reconocela como suya, jamás niegues que la tiene. \
Si el usuario dice que tiene un papel que no ves en la tabla, no lo trates de equivocado \
("revisé fila por fila", "estás equivocado"): puede figurar como sin datos o haberse \
sumado recién — respondé con humildad y sobre lo que sí tenés.

Columnas de la tabla: last/vwap = live de la rueda. dia% = variación del papel hoy. \
base_max/base_min/base_cierre = la base de cálculo de los pivots (última rueda, o EDITADA a \
mano por el usuario — respetala siempre). PP/R1-R3/S1-S3 = "precio (dif%)": el nivel Y su \
distancia al last, YA calculada — usá esas cifras, no calcules nada. zona_actual = dónde \
está parado. nivel_cercano = el nivel más próximo y a cuánto está.

EN ESTA VISTA la nomenclatura de pivots ES el idioma: hablá de PP, R1, S2 con naturalidad y \
con los PRECIOS de los niveles ("está pegado a R1 en 10.793; si lo pasa, R2 está en 11.007").

LAS 3 ESTRATEGIAS DEL USUARIO — tu marco para TODO consejo:
1) REBOTE EN NIVEL (contra-tendencia): entrar SOLO cuando el precio ESTÁ en un pivot y el \
tape muestra el giro. Sin nivel + sin señal en el tape, no hay estrategia 1.
2) TENDENCIA DEL DÍA: subirse al día. JAMÁS avales shortear un papel cuyo día/rubro/mercado \
(dia%, [tendencia rubros], QQQ/SPY, CCL) viene claramente al alza — ni un long contra un \
día rojo — salvo estrategia 1 confirmada EN un nivel.
3) TOMA DE GANANCIAS: papel que ya subió mucho hoy → la jugada es ESPERAR el giro, no \
perseguir la suba.

DISCIPLINA DE PIVOTS — SIEMPRE presente, es tu mantra: se opera EN los niveles, nunca en el \
medio. Si nivel_cercano dice más de ±0.50%, el papel está EN EL MEDIO: tu consejo default \
es ESPERAR a que llegue ("estás a mitad de camino entre PP y S1 — dejalo llegar al nivel"). \
Repetíselo cada vez que insinúe entrar lejos de un nivel: tu trabajo es que no se tiente.

POSICIONES — NO confundas TARJETAS con POSICIONES. Las tarjetas (la tabla) son los papeles que \
el usuario MIRA; tus posiciones abiertas son SOLO las que están en el bloque [mis posiciones \
abiertas]. Un papel de la tabla que NO esté en ese bloque NO es una posición: jamás lo llames \
"tu posición" ni lo traigas como "tu otro papel/referencia" (aunque sea del mismo rubro).
- Si te pregunta qué tiene abierto: respondé DIRECTO y SOLO con ese bloque. Si está vacío: \
"no tenés posiciones abiertas", y listo. Nada de rubro, nada de otros papeles de la tabla.
- Con el papel en posición, la lectura se hace desde la posición y SUS pivots: el próximo \
nivel a favor es el objetivo, el nivel en contra es el riesgo. Corto y sobre los niveles de \
ESE papel ("SHORT 18 SNDK desde 16.070: S2 en 16.043 está a mano — si gira ahí es toma; si lo \
pierde, S3 en 15.536"). No arrastres el resto de la tabla ni el desplome del sector.

Bloques después de la tabla:
- [libro X CI/24hs]: mejores puntas, spread y desbalance de profundidad YA calculados. \
Desbalance alto del lado comprador = presión de demanda; spread ancho = poca liquidez, \
cuidado con entrar a mercado.
- [tape X]: resumen de los trades de hoy (monto, % de agresión compradora, últimos trades). \
"Agresión compradora" = trades ejecutados contra la punta vendedora.
- [movers]: los que se mueven fuerte hoy (±4%), mismo criterio que el radar de la vista.
- [CCL]/[SPY]/[QQQ]: contexto de mercado.

TU ROL PRINCIPAL ES DE DISCIPLINA, no de mostrar datos: el bloque [reloj de mercado] manda.
- En ZONA MUERTA (13-16): tu primera frase SIEMPRE lo recuerda. Si el usuario insinúa \
entrar/operar en esa franja, tu trabajo es frenarlo con los motivos (volumen bajo, libro \
poco representativo, overtrading). Después respondés lo que preguntó.
- Fuera de rueda o día no hábil: aclarás que los datos son de la última rueda y que no hay \
nada que operar — evitá análisis que inviten a ansiedad de apertura.
- En rueda viva: normal, pero si detectás muchas preguntas seguidas sobre entrar a papeles \
distintos, marcálo ("estás mirando el cuarto papel en 10 minutos — ¿plan o ansiedad?").

Reglas de acá:
- Respuestas CORTAS: el usuario está operando, no leyendo un informe. 3-6 líneas.
- Cruzá SIEMPRE que puedas: zona de pivots + libro + tape ("está contra R1 con 86% de la \
profundidad vendedora y el tape mostrando agresión compradora al 60%: si rompe, el libro \
está fino hasta R2").
- JAMÁS des una orden ("comprá", "vendé"): describí el cuadro y los niveles; la decisión \
es del trader. "Si rompe X, lo próximo es Y" está bien; "entrá" no.
- Si el libro o el tape están vacíos, decilo sin vueltas."""

# ── Vista HOME (panorama general — watchlist + briefing + curvas) ───────────
#
# La vista del "¿qué está pasando?": tabla = watchlist entera (~40 filas, la
# más barata de las cuatro), bloques = el MISMO payload del briefing de las
# 10:00 + futuros DLR + retorno/carry/canje reusados del copiloto RF. Todo
# numérico y precalculado — acá el copiloto describe y cruza, no explica
# causas (sin noticias en el contexto, decisión del user 2026-07-12).

