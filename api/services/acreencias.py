"""api/services/acreencias.py — motor de acreencias (cobros futuros por cliente).

Proyecta, en función de las tenencias actuales (Valuaciones.AuM último snapshot)
y el calendario contractual de cada instrumento (Trading.Curvas.flujos), cuánto
va a cobrar cada cliente a futuro y en qué fecha.

  monto_cliente = cantidad (VN tenido) / 100 × monto_por_100VN del flujo

Es "bruto contractual": el cash que paga el bono por prospecto, en su moneda.
CER se ajusta al último CER publicado (estimación para flujos futuros). Solo
cubre instrumentos modelados en Curvas (lo no modelado no proyecta — se cierra
con el conciliador de Manager).

Capa pura (sin FastAPI). El cómputo lo persiste `jobs/acreencias.py` a
`CashFlow.Acreencias`; la vista lee esa colección (read funcs abajo).
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from api.db import get_db_cashflow, get_db_trading
from api.services.assets_sql import assets_rows
from core.postgres import get_pool


def _base_ticker(code: str | None) -> str:
    if not code or len(code) < 3:
        return code or ""
    return code[:-1] if code[-1] in ("O", "D", "C") else code


def _moneda_de(curva: str, doc: dict) -> str:
    if curva.startswith("on"):
        return (doc.get("moneda_flujo") or "USD").upper()
    if curva in ("soberanos", "dolar_linked"):
        return "USD"
    return "ARS"  # cer, tasa_fija, tamar, dual


def calendario_instrumentos() -> dict[str, dict]:
    """`{ticker_corto: {moneda, flujos: [{fecha, monto}]}}` — calendario contractual
    FUTURO por instrumento (monto por 100 VN). Cubre todas las curvas, incluida la
    familia `on_*` (montos absolutos como tasa_fija)."""
    from engines.curvas import (
        cargar_cer,
        cargar_dias_habiles,
        fecha_flujo,
        get_cer_liquidacion,
        monto_flujo,
        monto_flujo_cer,
        monto_flujo_soberano,
    )

    db = get_db_trading()
    docs = list(db["Curvas"].find(
        {}, {"_id": 0, "ticker_corto": 1, "ticker": 1, "curva": 1,
             "flujos": 1, "cer_emision": 1, "moneda_flujo": 1}))

    hay_cer = any((d.get("curva") == "cer") for d in docs)
    cer_dict = cargar_cer(db.client, dias=1200) if hay_cer else {}
    dias_habiles = cargar_dias_habiles(db.client) if hay_cer else []
    hoy = date.today()

    out: dict[str, dict] = {}
    for d in docs:
        tk = d.get("ticker_corto") or d.get("ticker")
        if not tk:
            continue
        curva = d.get("curva") or ""
        cer_emision = d.get("cer_emision")
        cal: list[dict] = []
        for f in d.get("flujos") or []:
            fd = fecha_flujo(f)
            if not fd or fd <= hoy:          # solo flujos futuros
                continue
            if curva == "soberanos" or curva == "dolar_linked":
                monto = monto_flujo_soberano(f, 100)
            elif curva == "cer":
                if not cer_emision:
                    continue
                cer_liq = get_cer_liquidacion(cer_dict, dias_habiles, fd.isoformat())
                if not cer_liq:
                    continue
                monto = monto_flujo_cer(f, 100) * cer_liq / float(cer_emision)
            else:                            # tasa_fija, on_*, tamar, dual
                monto = monto_flujo(f)
            if monto and monto > 0:
                cal.append({"fecha": fd.isoformat(), "monto": round(monto, 6)})
        if cal:
            cal.sort(key=lambda x: x["fecha"])
            out[tk] = {"moneda": _moneda_de(curva, d), "flujos": cal}
    return out


def computar_acreencias(dias_horizonte: int = 1825) -> list[dict]:
    """Proyección de cobros futuros por cliente. Cruza el último snapshot de AuM
    con el calendario contractual de cada instrumento. Devuelve un doc por
    (id_cuenta, fecha_pago, ticker). Horizonte: hasta `dias_horizonte` adelante."""
    cal = calendario_instrumentos()
    if not cal:
        return []

    # Resolver tenencia → ticker_corto del calendario (vía Assets.TICKER, con
    # match por base para la pata O/D), igual que el conciliador.
    assets = {a.get("unidad"): a for a in assets_rows(["TICKER", "EMISOR"])}  # SQL
    cal_base: dict[str, str] = {}
    for k in cal:
        cal_base.setdefault(_base_ticker(k), k)

    def resolver(unidad: str) -> str | None:
        a = assets.get(unidad)
        cands = ([a.get("TICKER")] if a else []) + [unidad]
        for c in cands:
            if not c:
                continue
            if c in cal:
                return c
            b = _base_ticker(c)
            if b in cal_base:
                return cal_base[b]
        return None

    # Denominación de clientes (id_cuenta → nombre) desde SQL clientes.cuentas.
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_cuenta, denominacion FROM cuentas")
        nombres = {r[0]: r[1] for r in cur.fetchall()}

    # Tenencia (último snapshot) desde SQL portafolio.tenencia (aum='si').
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        f = cur.fetchone()[0]
        if not f:
            return []
        cur.execute("SELECT id_cuenta, unidad, cantidad FROM portafolio.tenencia "
                    "WHERE fecha = %s AND aum = 'si'", (f,))
        holdings = [{"id_cuenta": r[0], "unidad": r[1], "cantidad": r[2]}
                    for r in cur.fetchall()]
    fsnap = f.isoformat()
    tope = (date.today() + timedelta(days=dias_horizonte)).isoformat()

    out: list[dict] = []
    for h in holdings:
        cantidad = h.get("cantidad") or 0
        if not cantidad:
            continue
        tk = resolver(h.get("unidad"))
        if not tk:
            continue
        inst = cal[tk]
        emisor = (assets.get(h.get("unidad")) or {}).get("EMISOR")
        idc = h.get("id_cuenta")
        for fl in inst["flujos"]:
            if fl["fecha"] > tope:
                break
            monto = round(cantidad / 100.0 * fl["monto"], 2)
            if not monto:
                continue
            out.append({
                "fecha_pago": fl["fecha"],
                "id_cuenta": idc,
                "cliente": nombres.get(idc) or h.get("cuenta"),
                "ticker": tk,
                "emisor": emisor,
                "moneda": inst["moneda"],
                "cantidad": cantidad,
                "monto": monto,
                "snapshot": fsnap,
                "generado_at": datetime.now(UTC),
            })
    return out


# ─────────────────────────────────────────────
# Control de calidad: bonos sin flujo (no proyectan acreencias ni valúan bien)
# ─────────────────────────────────────────────

_CARTERAS_BONO = ("ARS", "HD", "DL")  # renta fija (cotizan en paridad). El resto


def titulos_sin_flujo() -> list[dict]:
    """Assets de cartera ARS/HD/DL (BONOS) que NO tienen flujo futuro en
    Trading.Curvas → no proyectan cobros (acreencias) ni valúan por flujo.

    Control de calidad de dato (Manager): un bono SIEMPRE debe tener flujo.
    Clasifica el motivo y marca si está EN CARTERA hoy (prioridad de carga).
    Devuelve [{unidad, ticker, cartera, emisor, motivo, en_cartera}].
    """
    from engines.curvas import fecha_flujo

    bonos = [a for a in assets_rows(["CARTERA", "TICKER", "EMISOR"])
             if (a.get("CARTERA") or "").upper() in _CARTERAS_BONO]
    if not bonos:
        return []

    hoy = date.today()
    tk_idx: dict[str, tuple[int, int]] = {}   # ticker → (n_flujos, n_futuros)
    base_idx: dict[str, str] = {}
    for d in get_db_trading()["Curvas"].find(
            {}, {"_id": 0, "ticker_corto": 1, "ticker": 1, "flujos": 1}):
        flujos = d.get("flujos") or []
        n_fut = sum(1 for f in flujos if (fd := fecha_flujo(f)) and fd > hoy)
        for key in (d.get("ticker_corto"), d.get("ticker")):
            if key:
                tk_idx[key] = (len(flujos), n_fut)
                base_idx.setdefault(_base_ticker(key), key)

    # Unidades en cartera HOY (último snapshot aum='si') → prioridad.
    en_cartera: set[str] = set()
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        f = cur.fetchone()[0]
        if f:
            cur.execute("SELECT DISTINCT unidad FROM portafolio.tenencia "
                        "WHERE fecha = %s AND aum = 'si'", (f,))
            en_cartera = {r[0] for r in cur.fetchall() if r[0]}

    out: list[dict] = []
    for a in bonos:
        info = None
        for c in (a.get("TICKER"), a.get("unidad")):
            if not c:
                continue
            if c in tk_idx:
                info = tk_idx[c]; break
            b = _base_ticker(c)
            if b in base_idx:
                info = tk_idx[base_idx[b]]; break
        if info is None:
            motivo = "no está en Trading.Curvas"
        elif info[0] == 0:
            motivo = "en Curvas pero SIN flujos cargados"
        elif info[1] == 0:
            motivo = "flujos todos vencidos (sin futuros)"
        else:
            continue  # tiene flujo futuro → OK
        out.append({
            "unidad": a.get("unidad"), "ticker": a.get("TICKER") or None,
            "cartera": a.get("CARTERA"), "emisor": a.get("EMISOR") or None,
            "motivo": motivo, "en_cartera": a.get("unidad") in en_cartera,
        })
    # En cartera primero, después por cartera/unidad.
    out.sort(key=lambda x: (not x["en_cartera"], x["cartera"] or "", x["unidad"] or ""))
    return out


# ─────────────────────────────────────────────
# Lecturas para la vista (leen CashFlow.Acreencias precomputado)
# ─────────────────────────────────────────────

def _col():
    return get_db_cashflow()["Acreencias"]


def por_dia(desde: str | None = None, hasta: str | None = None) -> list[dict]:
    """Agregado por fecha de pago: total por moneda + #clientes + #pagos."""
    match: dict = {"fecha_pago": {"$gte": desde or date.today().isoformat()}}
    if hasta:
        match["fecha_pago"]["$lte"] = hasta
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {"fecha": "$fecha_pago", "moneda": "$moneda"},
            "monto": {"$sum": "$monto"},
            "cuentas": {"$addToSet": "$id_cuenta"},
            "n": {"$sum": 1}}},
        {"$group": {
            "_id": "$_id.fecha",
            "por_moneda": {"$push": {"moneda": "$_id.moneda", "monto": "$monto"}},
            "cuentas": {"$addToSet": "$cuentas"},
            "n": {"$sum": "$n"}}},
        {"$sort": {"_id": 1}},
    ]
    out = []
    for d in _col().aggregate(pipeline):
        cuentas = {c for grupo in d["cuentas"] for c in grupo}
        out.append({
            "fecha": d["_id"],
            "por_moneda": {m["moneda"]: round(m["monto"], 2) for m in d["por_moneda"]},
            "n_clientes": len(cuentas),
            "n_pagos": d["n"],
        })
    return out


def del_dia(fecha: str) -> list[dict]:
    """Quién cobra en una fecha y cuánto (por cliente·ticker)."""
    return list(_col().find({"fecha_pago": fecha}, {"_id": 0, "generado_at": 0}).sort("monto", -1))


def del_cliente(id_cuenta: str, desde: str | None = None) -> list[dict]:
    """Próximos cobros de un cliente, ordenados por fecha."""
    match = {"id_cuenta": id_cuenta, "fecha_pago": {"$gte": desde or date.today().isoformat()}}
    return list(_col().find(match, {"_id": 0, "generado_at": 0}).sort("fecha_pago", 1))
