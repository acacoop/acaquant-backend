"""api/services/ons.py — maestro de ONs (Trading.BondsMaster) + sync a Curvas.

`Trading.BondsMaster` es la FUENTE editable de las ONs (panel Manager + seed
CLI). Este servicio centraliza:
  - el transform BondsMaster → doc de Trading.Curvas (curva='on_<sector>') que
    consumen el motor (motor_curvas) y la vista (renta_fija.listar_curva). Es
    el MISMO transform que usa `scripts/seed_curvas_ons.py` (sin divergencia).
  - el sync BondsMaster → Curvas (upsert + borra stale).
  - el CRUD para Manager (list / values / upsert / delete / set_sector).

Puro (sin FastAPI). Lectura con get_mongo_client_read(); escritura con
get_mongo_client(). El sector va codificado en la curva ('on_energia', etc.),
DB-driven (campo BondsMaster.sector). Ver `api/services/renta_fija.py`.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from pymongo import UpdateOne
from pymongo.errors import BulkWriteError

from api.services.assets_sql import assets_rows
from core.mongo import get_mongo_client, get_mongo_client_read
from core.postgres import get_pool

# Carteras (Valuaciones.Assets.CARTERA) que entran a la conciliación de ONs:
# HD (hard dollar) / DL (dollar linked).
CARTERAS_ON = {"HD", "DL"}

# Campos editables del maestro (lo que el form de Manager puede setear).
# `es_on`: si aparece en la vista ONs de Mercados (sync a Curvas on_*). False = solo
# base de flujos (bono común, no ensucia ONs). Default True (back-compat: lo que ya
# existe son ONs reales).
EDITABLES = ("emisor", "moneda_flujo", "tasa_cupon", "vencimiento", "sector", "tickers",
             "flujos", "es_on")


def slug_sector(raw) -> str:
    """Normaliza el sector que se carga en BondsMaster.sector a un slug para la
    curva: 'Energía' → 'energia', 'ON Finanzas' → 'finanzas'. Vacío → 'otros'."""
    s = (raw or "otros").strip().lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        s = s.replace(a, b)
    s = s.replace("on ", "").replace(" ", "_").strip("_")
    return s or "otros"


def _fecha_iso(raw) -> str | None:
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:10]
    return None


def _f(x, default: float = 0.0) -> float:
    """float() tolerante: None / '' / texto raro → default (no explota el sync)."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _fecha_flujo_iso(raw) -> str | None:
    """Fecha de un flujo → ISO 'YYYY-MM-DD'. Acepta datetime o string."""
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.strptime(raw.strip()[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None
    return None


def bondmaster_to_curva_doc(bm: dict) -> dict | None:
    """BondsMaster doc → doc de Trading.Curvas (curva='on_<sector>'). None si no
    se puede (sin asset o sin ticker). Pata canónica por moneda: USD → ticker D,
    ARS → ticker O. Flujos en shape nativo {fecha, amortizacion, interes,
    valor_residual} (el motor usa monto_flujo = amortizacion + interes)."""
    asset = bm.get("asset")
    moneda = (bm.get("moneda_flujo") or "USD").upper()
    tickers = bm.get("tickers") or {}
    ticker_full = tickers.get("USD") if moneda == "USD" else tickers.get("ARS")
    if not ticker_full:
        ticker_full = tickers.get("ARS") or tickers.get("USD")
    if not asset or not ticker_full:
        return None

    flujos = []
    for f in bm.get("flujos") or []:
        fi = _fecha_flujo_iso(f.get("fecha"))
        if not fi:
            continue
        flujos.append({
            "fecha": fi,
            "amortizacion": _f(f.get("amortizacion"), 0.0),
            "interes": _f(f.get("interes"), 0.0),
            "valor_residual": _f(f.get("valor_residual"), 100.0) or 100.0,
        })

    return {
        "ticker": ticker_full,
        "ticker_corto": asset,
        "curva": f"on_{slug_sector(bm.get('sector'))}",
        "moneda_flujo": moneda,
        "emisor": bm.get("emisor"),
        "sector": bm.get("sector") or "otros",
        "tasa_cupon": bm.get("tasa_cupon"),
        "valor_nominal": 100,
        "fecha_vencimiento": _fecha_iso(bm.get("vencimiento")),
        "flujos": flujos,
    }


def sync_ons_to_curvas() -> dict:
    """Sincroniza TODAS las ONs de BondsMaster → Trading.Curvas (upsert por
    ticker + borra las curva ^on que ya no estén). Idempotente. Devuelve counts.

    Lo llaman el panel Manager (tras cada mutación) y el seed CLI --commit."""
    read = get_mongo_client_read()["Trading"]["BondsMaster"]
    # Solo los marcados como ON (es_on != False) van a Curvas → vista ONs de Mercados.
    # Los comunes (es_on=False) quedan SOLO como base de flujos en BondsMaster.
    docs = [d for d in (bondmaster_to_curva_doc(b)
                        for b in read.find({}, {"_id": 0}) if b.get("es_on") is not False) if d]
    # Dedup por ticker_corto (la unique index de Curvas). El último gana.
    por_corto = {d["ticker_corto"]: d for d in docs if d.get("ticker_corto")}
    curvas = get_mongo_client()["Trading"]["Curvas"]
    # Upsert SOLO por ticker_corto (la clave única): siempre matchea el doc
    # existente del bono → actualiza en vez de insertar → NUNCA choca la unique
    # index (aunque cambie la pata canónica O↔D). Sin filtro de curva — ese
    # filtro causaba que no matcheara y terminara insertando + chocando (E11000).
    ops = [UpdateOne({"ticker_corto": tc}, {"$set": d}, upsert=True) for tc, d in por_corto.items()]
    sincronizadas = 0
    if ops:
        try:
            res = curvas.bulk_write(ops, ordered=False)
            sincronizadas = (res.upserted_count or 0) + (res.modified_count or 0)
        except BulkWriteError as e:
            # Por las dudas: ignoramos choques de unique key (11000) residuales;
            # el resto se aplica igual (ordered=False). Cualquier otro error sí sube.
            otros = [w for w in e.details.get("writeErrors", []) if w.get("code") != 11000]
            if otros:
                raise
            sincronizadas = e.details.get("nModified", 0) + e.details.get("nUpserted", 0)
    borradas = curvas.delete_many(
        {"curva": {"$regex": "^on"}, "ticker_corto": {"$nin": list(por_corto)}}).deleted_count
    return {"sincronizadas": sincronizadas, "borradas": borradas}


# ─────────────────────────────────────────────
# CRUD para Manager (panel TÍTULOS → ONs)
# ─────────────────────────────────────────────

def list_ons(sector: str | None = None, emisor: str | None = None) -> list[dict]:
    """ONs del maestro BondsMaster, con filtros opcionales, ordenadas por emisor."""
    read = get_mongo_client_read()["Trading"]["BondsMaster"]
    filtro: dict = {}
    if sector:
        filtro["sector"] = sector
    if emisor:
        filtro["emisor"] = emisor
    out = list(read.find(filtro, {"_id": 0}).limit(5000))
    out.sort(key=lambda d: ((d.get("emisor") or "").lower(), d.get("asset") or ""))
    return out


def ons_values() -> dict:
    """Valores únicos para filtros/autocomplete del panel."""
    read = get_mongo_client_read()["Trading"]["BondsMaster"]
    return {
        "emisores": sorted(e for e in read.distinct("emisor") if e),
        "sectores": sorted(s for s in read.distinct("sector") if s),
        "monedas": sorted(m for m in read.distinct("moneda_flujo") if m),
    }


def upsert_on(payload: dict, actor: str = "") -> dict:
    """Crea/edita una ON en BondsMaster (upsert por `asset`) y re-sincroniza
    Curvas. `payload` trae asset + los campos editables (incluido `flujos`,
    que reemplaza el array completo). Devuelve el doc guardado + el resultado
    del sync."""
    asset = (payload.get("asset") or "").strip()
    if not asset:
        raise ValueError("falta 'asset'")

    doc = {k: payload[k] for k in EDITABLES if k in payload}
    doc["asset"] = asset
    doc["actualizado_por"] = actor
    doc["actualizado_at"] = datetime.now(UTC)

    col = get_mongo_client()["Trading"]["BondsMaster"]
    col.update_one({"asset": asset}, {"$set": doc}, upsert=True)
    saved = col.find_one({"asset": asset}, {"_id": 0}) or {}
    sync = sync_ons_to_curvas()
    return {"on": saved, "sync": sync}


def delete_on(asset: str) -> dict:
    """Borra una ON de BondsMaster y la saca de Curvas (vía sync)."""
    asset = (asset or "").strip()
    if not asset:
        raise ValueError("falta 'asset'")
    col = get_mongo_client()["Trading"]["BondsMaster"]
    deleted = col.delete_one({"asset": asset}).deleted_count
    sync = sync_ons_to_curvas()
    return {"borrada": deleted, "sync": sync}


def set_sector(asset: str, sector: str, actor: str = "") -> dict:
    """Setea el sector de una ON (segmentación) y re-sincroniza. Live: el cambio
    de sector se refleja en la vista sin reiniciar motores."""
    asset = (asset or "").strip()
    if not asset:
        raise ValueError("falta 'asset'")
    col = get_mongo_client()["Trading"]["BondsMaster"]
    res = col.update_one(
        {"asset": asset},
        {"$set": {"sector": sector, "actualizado_por": actor, "actualizado_at": datetime.now(UTC)}})
    if res.matched_count == 0:
        raise ValueError(f"ON '{asset}' no existe")
    sync = sync_ons_to_curvas()
    saved = col.find_one({"asset": asset}, {"_id": 0}) or {}
    return {"on": saved, "sync": sync}


# ─────────────────────────────────────────────
# Parser de flujos (pegado de Excel / descarga oficial)
# ─────────────────────────────────────────────

def _on_num(s) -> float:
    """'4.75%' → 4.75 · '100.00' → 100 · '1.234,56' → 1234.56 (es-AR) · '' → 0."""
    if s is None:
        return 0.0
    t = str(s).replace("%", "").replace("$", "").replace(" ", "").strip()
    if "," in t and "." in t:        # 1.234,56 → 1234.56
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:                   # 1,89 → 1.89
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return 0.0


def _on_fecha(s) -> str | None:
    """'2026-08-06T00:00:00.000Z' → '2026-08-06' · '06/08/2026' → '2026-08-06'."""
    t = str(s or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", t)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        if len(y) == 2:
            y = "20" + y
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    return None


def parse_flujos_texto(text: str) -> dict:
    """Parsea flujos pegados de Excel a {flujos, tasa_cupon, vencimiento, formato}.

    Detecta dos formatos:
      - OFICIAL (descarga BYMA/IAMC): doble header con 'Flujo de fondos c/100 vn'.
        Usa la fecha 'Efectiva', los montos ABSOLUTOS de 'Flujo de fondos c/100
        vn' (Amortización col 6, Interés col 7), el 'Valor residual' (col 3) y
        la 'Tasa de interés' anual (col 4 → tasa_cupon).
      - SIMPLE: 4 columnas fecha · amortización · interés · residual.

    Delimitador: tab (pegado de Excel) o coma (CSV crudo). Saltea headers/filas
    sin fecha válida. `vencimiento` = fecha del último flujo."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return {"flujos": [], "tasa_cupon": None, "vencimiento": None, "formato": "vacio"}

    def cols(ln: str) -> list[str]:
        parts = ln.split("\t") if "\t" in ln else ln.split(",")
        return [c.strip() for c in parts]

    oficial = any("flujo de fondos" in ln.lower() for ln in lines[:3])
    flujos: list[dict] = []
    tasa: float | None = None

    for ln in lines:
        c = cols(ln)
        if oficial:
            if len(c) < 8:
                continue
            fecha = _on_fecha(c[1])
            if not fecha:               # filas de header
                continue
            flujos.append({
                "fecha": fecha,
                "amortizacion": _on_num(c[6]),
                "interes": _on_num(c[7]),
                "valor_residual": _on_num(c[3]) or 100.0,
            })
            if tasa is None:
                t = _on_num(c[4])
                tasa = round(t / 100.0, 6) if t else None
        else:
            fecha = _on_fecha(c[0]) if c else None
            if not fecha:
                continue
            flujos.append({
                "fecha": fecha,
                "amortizacion": _on_num(c[1]) if len(c) > 1 else 0.0,
                "interes": _on_num(c[2]) if len(c) > 2 else 0.0,
                "valor_residual": (_on_num(c[3]) if len(c) > 3 and c[3] else 100.0) or 100.0,
            })

    venc = flujos[-1]["fecha"] if flujos else None
    return {"flujos": flujos, "tasa_cupon": tasa, "vencimiento": venc,
            "formato": "oficial" if oficial else "simple"}


# ─────────────────────────────────────────────
# Conciliador de cobertura (AuM HD/DL vs Curvas)
# ─────────────────────────────────────────────

def _base_ticker(code: str | None) -> str:
    """'YM40D' → 'YM40' (saca la pata O/D/C). Deja igual lo que no aplica."""
    if not code or len(code) < 3:
        return code or ""
    return code[:-1] if code[-1] in ("O", "D", "C") else code


_RE_CODIGO = re.compile(r"^\s*(?:\[\d+\]\s*)?([A-Za-z0-9]+)")


def _codigo_de_unidad(unidad: str | None) -> str:
    """Extrae el código del string Aunesa: '[57187] OLC3O' → 'OLC3O',
    '[57785] MRCYO - ON GENE...' → 'MRCYO'. Sin prefijo lo deja igual.

    Necesario porque la unidad de tenencia trae '[id] CODE - descripción' y el
    Curvas usa el código limpio → sin extraerlo, el conciliador nunca matchea
    (el bono sigue apareciendo aunque ya esté en Curvas)."""
    m = _RE_CODIGO.match(unidad or "")
    return m.group(1).upper() if m else (unidad or "")


def conciliar() -> dict:
    """Gap de cobertura: instrumentos HD/DL que tienen los clientes (último AuM)
    y NO están en Trading.Curvas. Excluye los marcados como ignorados.

    Relación: AuM.unidad → Assets (CARTERA ∈ {HD,DL}) → ticker ↔ Curvas
    (match exacto o por base, para no marcar como faltante la otra pata O/D)."""
    read = get_mongo_client_read()
    trading = read["Trading"]

    # Tenencia (último snapshot) desde SQL portafolio.tenencia (aum='si').
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        f = cur.fetchone()[0]
        if not f:
            return {"gap": [], "resumen": {"total": 0, "cubiertas": 0, "faltan": 0,
                                           "ignoradas": 0, "snapshot": None}}
        cur.execute("SELECT DISTINCT unidad FROM portafolio.tenencia "
                    "WHERE fecha = %s AND aum = 'si' AND unidad IS NOT NULL", (f,))
        unidades = [r[0] for r in cur.fetchall()]
    fsnap = f.isoformat()

    assets = assets_rows(["TICKER", "EMISOR", "CARTERA"])   # SQL portafolio.assets
    by_unidad = {a.get("unidad"): a for a in assets if a.get("unidad")}
    by_ticker = {a.get("TICKER"): a for a in assets if a.get("TICKER")}

    curvas = list(trading["Curvas"].find({}, {"_id": 0, "ticker": 1, "ticker_corto": 1}))
    set_full = {c.get("ticker") for c in curvas if c.get("ticker")}
    set_corto = {c.get("ticker_corto") for c in curvas if c.get("ticker_corto")}
    set_base = {_base_ticker(c) for c in set_corto}
    ignoradas = {d.get("ticker") for d in trading["OnsIgnoradas"].find({}, {"_id": 0, "ticker": 1})}

    def cubierto(*cands) -> bool:
        for cand in cands:
            if cand and (cand in set_corto or cand in set_full or _base_ticker(cand) in set_base):
                return True
        return False

    total = cubiertas = 0
    gap = []
    for u in unidades:
        # La unidad de tenencia es '[id] CODE - desc'; el código limpio matchea Curvas
        # y sirve de fallback cuando el Asset no tiene TICKER cargado.
        codigo = _codigo_de_unidad(u)
        a = by_unidad.get(u) or by_ticker.get(u) or by_unidad.get(codigo) or by_ticker.get(codigo)
        if not a:
            continue
        cartera = (a.get("CARTERA") or "").strip().upper()
        if cartera not in CARTERAS_ON:
            continue
        total += 1
        ticker = a.get("TICKER") or codigo   # fallback al código embebido
        if cubierto(ticker, u, codigo):
            cubiertas += 1
            continue
        if ticker in ignoradas or u in ignoradas or codigo in ignoradas:
            continue
        gap.append({"unidad": u, "ticker": ticker, "emisor": a.get("EMISOR"), "cartera": cartera})

    gap.sort(key=lambda g: ((g["emisor"] or "").lower(), g["ticker"] or ""))
    return {
        "gap": gap,
        "resumen": {"total": total, "cubiertas": cubiertas, "faltan": len(gap),
                    "ignoradas": len(ignoradas), "snapshot": fsnap},
    }


def ignorar_concil(ticker: str, actor: str = "") -> dict:
    """Marca un ticker como 'no es ON' → no vuelve a aparecer en el gap."""
    ticker = (ticker or "").strip()
    if not ticker:
        raise ValueError("falta 'ticker'")
    get_mongo_client()["Trading"]["OnsIgnoradas"].update_one(
        {"ticker": ticker},
        {"$set": {"ticker": ticker, "ignorado_por": actor, "at": datetime.now(UTC)}},
        upsert=True)
    return {"ignorada": ticker}


def quitar_ignorar(ticker: str) -> dict:
    """Saca un ticker de la lista de ignorados → vuelve a conciliar."""
    deleted = get_mongo_client()["Trading"]["OnsIgnoradas"].delete_one(
        {"ticker": (ticker or "").strip()}).deleted_count
    return {"restauradas": deleted}
