"""api/services/ons.py — gestión de ONs, DIRECTO sobre Trading.Curvas (curva on_<sector>).

UNA sola base: las ONs viven en `Trading.Curvas` como `curva='on_<sector>'` (igual que
el resto de la renta fija — la `curva` decide la vista). BondsMaster fue RETIRADO
(Fase 3, 2026-06-22). Este servicio centraliza:
  - el transform PAYLOAD del editor → doc de Trading.Curvas (`bondmaster_to_curva_doc`),
    que consumen el motor (motor_curvas) y la vista (renta_fija.listar_curva).
  - el CRUD para Manager (list / values / upsert / delete / set_sector) sobre Curvas.
  - el conciliador de cobertura (AuM HD/DL vs Curvas).

Puro (sin FastAPI). SQL-native (decomiso Mongo): el master vive en `mercado.curvas`
(Postgres) — lectura vía `core.curvas_sql`, escritura vía `core.pg_mirror.write_native`.
La lista de tickers ignorados del conciliador vive en `mercado.ons_ignoradas` (SQL-native,
decomiso 2026-06-28). El sector va codificado en la curva ('on_energia', etc.), DB-driven.
Ver `api/services/renta_fija.py`.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime

from api.services.assets_sql import assets_rows
from core import curvas_ejes as ce
from core import curvas_sql
from core.pg_mirror import doc_iso, write_native
from core.postgres import get_pool

# Carteras (Valuaciones.Assets.CARTERA) que entran a la conciliación de ONs:
# HD (hard dollar) / DL (dollar linked).
CARTERAS_ON = {"HD", "DL"}

# Campos editables del maestro (lo que el form de Manager puede setear).
EDITABLES = ("emisor", "moneda_flujo", "tasa_cupon", "vencimiento", "sector", "tickers", "flujos")


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


# ── Espejo SQL-native de Trading.Curvas → mercado.curvas ─────────────────────
# Las ONs y bonos viven en mercado.curvas (Postgres). El doc COMPLETO va en la
# columna jsonb `data` (mismo shape que tenía Mongo: fechas como strings ISO);
# las columnas tipadas son SOLO para filtrar barato. Mapeo IDÉNTICO al de
# jobs/sync_postgres.py::sync_curvas (la fuente probada del puente Mongo→SQL).

def _row_date(v) -> date | None:
    """ISO str | datetime | date → date para columna date. None si inválido."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def curva_doc_to_row(doc: dict) -> dict:
    """Doc de Trading.Curvas → fila tipada para `mercado.curvas` (write_native).
    Columnas consultables + `flujos` (jsonb) + doc completo en `data` (datetime→ISO
    vía doc_iso). Campos ausentes → NULL; el reader (curvas_sql) usa `data`.

    OJO — las CLAVES de la izquierda son COLUMNAS y las de la derecha son claves del
    DOC, y desde el renombre de 2026-08-15 ya no coinciden: la columna `ticker` es el
    ticker del bono (lo que el doc llama `ticker_corto`) y la columna `instrumento`
    es el símbolo de mercado (lo que el doc llama `ticker`). El doc/blob NO se tocó
    a propósito: sus claves las leen ~500 lugares y se migran en el paso siguiente."""
    return {
        "ticker": (doc.get("ticker_corto") or "").strip(),
        "instrumento": doc.get("ticker") or None,
        "curva": doc.get("curva") or None,
        "tipo": doc.get("tipo") or None,
        "moneda_flujo": doc.get("moneda_flujo") or None,
        "valor_nominal": doc.get("valor_nominal"),
        "fecha_emision": _row_date(doc.get("fecha_emision")),
        "fecha_vencimiento": _row_date(doc.get("fecha_vencimiento")),
        "cupon_anual": doc.get("cupon_anual"),
        "cer_emision": doc.get("cer_emision"),
        "flujo_vencimiento": doc.get("flujo_vencimiento"),
        "emisor": doc.get("emisor") or None,
        "sector": doc.get("sector") or None,
        "flujos": doc_iso(doc.get("flujos") or []),
        "data": doc_iso(doc),
    }


# Columnas de `mercado.curvas` que YA NO las gobierna el blob `data`: las escribe
# OTRO proceso, directo a la columna, y el blob quedó viejo.
#
#   · `emisor` — lo estandariza `jobs/ficha_1816.py` con un UPDATE a la COLUMNA.
#   · `sector` — la industria del emisor, que deja de cargarse por bono.
#
# Sin esta lista el merge las pisaba, y de dos formas distintas, las dos MUDAS:
#   · en un bono NO-ON el blob no tiene `emisor` → editar cualquier campo desde
#     Manager escribía `emisor = NULL` y borraba lo que 1816 había estandarizado;
#   · en una ON el blob tiene el emisor VIEJO (`BCO.COMAFI`) → editar el sector
#     revertía la estandarización a la grafía vieja.
# En los dos casos la pantalla queda igual de prolija y el dato se perdió.
#
# Solo se tocan si el payload las trae EXPLÍCITAS (el alta de ONs manda `emisor`,
# `set_sector` manda `sector`): ahí la escritura es intencional, no un arrastre.
COLUMNAS_AJENAS_AL_BLOB = ("emisor", "sector")


def fila_a_escribir(existing: dict, doc: dict) -> dict:
    """La fila del upsert, sin pisar las columnas que el blob ya no gobierna. PURA.

    `_mirror` agrupa por el set de claves del dict y escribe `c=EXCLUDED.c` por cada
    columna PRESENTE — así que sacar la clave es exactamente "no tocar esa columna".
    """
    row = curva_doc_to_row({**existing, **doc})
    for col in COLUMNAS_AJENAS_AL_BLOB:
        if col not in doc:
            row.pop(col, None)
    return row


def _upsert_curva_doc(doc: dict, ejes: dict | None = None) -> dict:
    """Upsert $set-PARCIAL de un doc de curva en `mercado.curvas` (paridad con el
    `update_one({'$set': doc}, upsert=True)` de Mongo): mergea sobre el doc existente
    y reescribe la fila completa → no pierde campos que el payload no trae. Devuelve
    el doc guardado (releído de SQL).

    `ejes` (ya validado con `core.curvas_ejes.normalizar_ejes`) va **SOLO A LAS
    COLUMNAS, nunca al blob `data`**. Es deliberado: el blob es la forma vieja, la
    que ~500 lugares leen con las claves invertidas, y meterle campos nuevos sería
    agrandar el problema de las dos verdades justo cuando lo estamos cerrando. Los
    ejes nacen viviendo en UN solo lugar.
    """
    tc = (doc.get("ticker_corto") or "").strip()
    existing = curvas_sql.find_one(tc) or {}  # perf-ok: PERF004 — 1ª lectura = base del merge; la 2ª relee POST-write (contrato: devolver lo guardado). Mutación admin, corre poco.
    row = fila_a_escribir(existing, doc)
    if ejes:
        row.update({k: v for k, v in ejes.items() if k in ce.EJES_EDITABLES})
    write_native("mercado.curvas", ["ticker"], [row])
    curvas_sql.invalidar()   # refrescar el cache del master tras el alta/edición
    return curvas_sql.find_one(tc) or {}


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
        "tickers": {"ARS": tickers.get("ARS"), "USD": tickers.get("USD")},  # ambas patas
        "flujos": flujos,
    }


# ─────────────────────────────────────────────
# CRUD para Manager (panel TÍTULOS → ONs) — DIRECTO sobre Trading.Curvas (on_*).
# UNA sola base: las ONs viven en Curvas como curva on_<sector>. BondsMaster RETIRADO
# (Fase 3, 2026-06-22). `bondmaster_to_curva_doc` ahora transforma el PAYLOAD del editor
# (mismo shape) → doc de Curvas; ya no hay sync intermedio.
# ─────────────────────────────────────────────

def _curva_to_on(d: dict) -> dict:
    """Doc de Curvas on_* → shape ON que consume el panel (asset, tickers, vencimiento…)."""
    return {
        "asset": d.get("ticker_corto"),
        "emisor": d.get("emisor"),
        "sector": d.get("sector") or (str(d.get("curva") or "").replace("on_", "") or "otros"),
        "moneda_flujo": d.get("moneda_flujo"),
        "tasa_cupon": d.get("tasa_cupon"),
        "vencimiento": d.get("fecha_vencimiento"),
        "tickers": d.get("tickers") or {},
        "flujos": d.get("flujos") or [],
        "actualizado_por": d.get("actualizado_por"),
        "actualizado_at": d.get("actualizado_at"),
    }


def list_ons(sector: str | None = None, emisor: str | None = None) -> list[dict]:
    """ONs (curva on_*) de mercado.curvas, con filtros opcionales, ordenadas por emisor."""
    docs = curvas_sql.por_curva_like("on%")
    if sector:
        docs = [d for d in docs if d.get("sector") == sector]
    if emisor:
        docs = [d for d in docs if d.get("emisor") == emisor]
    out = [_curva_to_on(d) for d in docs]
    out.sort(key=lambda d: ((d.get("emisor") or "").lower(), d.get("asset") or ""))
    return out


def ons_values() -> dict:
    """Valores únicos (emisor/sector/moneda) de las ONs en Curvas — para los datalist."""
    docs = curvas_sql.por_curva_like("on%")
    return {
        "emisores": sorted({d["emisor"] for d in docs if d.get("emisor")}),
        "sectores": sorted({d["sector"] for d in docs if d.get("sector")}),
        "monedas": sorted({d["moneda_flujo"] for d in docs if d.get("moneda_flujo")}),
    }


def upsert_on(payload: dict, actor: str = "") -> dict:
    """Crea/edita una ON DIRECTO en Trading.Curvas (curva on_<sector>), upsert por
    ticker_corto (=`asset`). `payload` trae asset + campos editables (incl. flujos)."""
    asset = (payload.get("asset") or "").strip()
    if not asset:
        raise ValueError("falta 'asset'")
    doc = bondmaster_to_curva_doc({**payload, "asset": asset})
    if not doc:
        raise ValueError("falta el ticker (pata ARS o USD) — no se puede armar el doc de Curvas")
    doc["actualizado_por"] = actor
    doc["actualizado_at"] = datetime.now(UTC)
    saved = _upsert_curva_doc(doc)
    return {"on": _curva_to_on(saved)}


def delete_on(asset: str) -> dict:
    """Borra una ON de mercado.curvas (curva on_*) por ticker_corto."""
    asset = (asset or "").strip()
    if not asset:
        raise ValueError("falta 'asset'")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mercado.curvas WHERE ticker = %s "
                    "AND curva LIKE 'on%%'", (asset,))
        deleted = cur.rowcount or 0
    curvas_sql.invalidar()   # refrescar el cache del master tras la baja
    return {"borrada": deleted}


def set_sector(asset: str, sector: str, actor: str = "") -> dict:
    """Setea el sector de una ON → cambia la curva a on_<sector>. Live (sin reiniciar)."""
    asset = (asset or "").strip()
    if not asset:
        raise ValueError("falta 'asset'")
    doc = curvas_sql.find_one(asset)
    if not doc or not str(doc.get("curva") or "").startswith("on"):
        raise ValueError(f"ON '{asset}' no existe en Curvas")
    doc["sector"] = sector
    doc["curva"] = f"on_{slug_sector(sector)}"
    doc["actualizado_por"] = actor
    doc["actualizado_at"] = datetime.now(UTC)
    saved = _upsert_curva_doc(doc)
    return {"on": _curva_to_on(saved)}


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
    # Tenencia (último snapshot) + ignorados desde SQL.
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        f = cur.fetchone()[0]
        if not f:
            return {"gap": [], "resumen": {"total": 0, "cubiertas": 0, "faltan": 0,
                                           "ignoradas": 0, "snapshot": None}}
        cur.execute("SELECT DISTINCT unidad FROM portafolio.tenencia "
                    "WHERE fecha = %s AND aum = 'si' AND unidad IS NOT NULL", (f,))
        unidades = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT ticker FROM mercado.ons_ignoradas")
        ignoradas = {r[0] for r in cur.fetchall()}
    fsnap = f.isoformat()

    assets = assets_rows(["TICKER", "EMISOR", "CARTERA"])   # SQL portafolio.assets
    by_unidad = {a.get("unidad"): a for a in assets if a.get("unidad")}
    by_ticker = {a.get("TICKER"): a for a in assets if a.get("TICKER")}

    curvas = curvas_sql.cargar_todos()   # mercado.curvas (SQL)
    set_full = {c.get("ticker") for c in curvas if c.get("ticker")}
    set_corto = {c.get("ticker_corto") for c in curvas if c.get("ticker_corto")}
    set_base = {_base_ticker(c) for c in set_corto}

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
    """Marca un ticker como 'no es ON' → no vuelve a aparecer en el gap.
    Upsert SQL-native por ticker (mercado.ons_ignoradas)."""
    ticker = (ticker or "").strip()
    if not ticker:
        raise ValueError("falta 'ticker'")
    write_native("mercado.ons_ignoradas", ["ticker"], [
        {"ticker": ticker, "ignorado_por": actor, "at": datetime.now(UTC)}])
    return {"ignorada": ticker}


def quitar_ignorar(ticker: str) -> dict:
    """Saca un ticker de la lista de ignorados → vuelve a conciliar."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mercado.ons_ignoradas WHERE ticker = %s",
                    ((ticker or "").strip(),))
        deleted = cur.rowcount or 0
    return {"restauradas": deleted}


def listar_ignoradas() -> dict:
    """Lista los tickers marcados como ignorados en el conciliador (para poder
    restaurarlos desde la UI). Orden: más recientes primero."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, ignorado_por, at FROM mercado.ons_ignoradas "
                    "ORDER BY at DESC NULLS LAST")
        docs = [{"ticker": r[0], "ignorado_por": r[1],
                 "at": str(r[2])[:19] if r[2] is not None else None}
                for r in cur.fetchall()]
    return {"ignoradas": docs, "n": len(docs)}
