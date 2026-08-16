"""api/services/bonos_admin.py — alta/edición de bonos NO-ON directo en Trading.Curvas.

Las ONs viven en BondsMaster y se sincronizan a Curvas (ver `ons.py`). Los bonos
soberanos / tasa_fija (Lecaps/Boncaps) / CER viven DIRECTO en `Trading.Curvas`
(no hay master intermedio). Este servicio les da el mismo CRUD que las ONs, para
que el panel Manager → TÍTULOS los gestione desde un solo lugar.

Dos formas de flujo:
  - BULLET (Lecap/Boncap tasa_fija): `flujo_vencimiento` (por 100 VN) al `fecha_vencimiento`.
    No lleva array de flujos. El motor de acreencias/valuación lo proyecta como pago único.
  - CRONOGRAMA (cupón / amortización): array `flujos` [{fecha, amortizacion, interes,
    valor_residual}] — mismo shape y parser que las ONs (`ons.parse_flujos_texto`).

Escritura directa a Curvas con upsert por `ticker_corto` (su clave única) → el motor
lo toma en el próximo loop. Puro (sin FastAPI).
"""
from __future__ import annotations

from datetime import UTC, datetime

from api.services.ons import _f, _fecha_flujo_iso, _fecha_iso, _upsert_curva_doc
from core import curvas_ejes as ce
from core import curvas_sql
from core.postgres import get_pool

# Curvas que gestiona este editor (las ONs van por ons.py; mercado las maneja el motor).
CURVAS_BONO = ("tasa_fija", "cer", "soberanos", "dolar_linked", "tamar", "dual")


def parse_flujos_bono(texto: str, tipo: str) -> dict:
    """Parsea flujos pegados de Excel (formato BYMA/IAMC 'c/100 vn' o simple,
    reutilizando el parser de ONs) y los DEVUELVE ya en la shape del TIPO de bono
    elegido — así el form de alta de bonos gana el "pegar Excel" que ya tienen las
    ONs, sin que el frontend tenga que conocer la conversión de shape.

    Mapeo por tipo (ver docs raíz 'mercado.curvas — shape de flujos'):
    - `bono` (tasa fija c/cupón) → {fecha, amortizacion, interes} (absoluto, directo).
    - `soberano`/`dolar_linked`/`cer` → {fecha, amortizacion_pct, cupon_sobre_residual}:
      en el formato 'c/100 vn' la amortización YA viene por 100 VN (= %) y el interés
      YA es el cupón sobre residual resuelto (NO se re-multiplica).
    - `dual` → {fecha, amortizacion_pct}.
    - otros/desconocido → shape absoluto del parser tal cual.

    Devuelve el mismo shape que `ons.parse_flujos_texto` ({flujos, tasa_cupon,
    vencimiento, formato}) con `flujos` ya convertido."""
    from api.services import ons

    base = ons.parse_flujos_texto(texto)
    t = (tipo or "").strip().lower()
    out: list[dict] = []
    for fl in base.get("flujos") or []:
        amort, interes = fl.get("amortizacion"), fl.get("interes")
        if t in ("soberano", "soberanos", "dolar_linked", "cer"):
            out.append({"fecha": fl["fecha"], "amortizacion_pct": amort,
                        "cupon_sobre_residual": interes})
        elif t == "dual":
            out.append({"fecha": fl["fecha"], "amortizacion_pct": amort})
        else:  # bono / tasa_fija / desconocido → absoluto directo
            out.append({"fecha": fl["fecha"], "amortizacion": amort, "interes": interes})
    return {**base, "flujos": out}


# Columnas que NO están en el blob `data` y el editor necesita LEER para poder
# editarlas sin borrarlas. `curvas_sql` hace `SELECT data`, o sea que devuelve la
# forma vieja: los ejes (que son column-only por diseño) y el `emisor` que
# estandariza `jobs/ficha_1816` (que escribe la columna) no vienen ahí.
#
# Sin este merge el editor cargaba el form con los ejes VACÍOS y al guardar los
# escribía vacíos: abrir un bono y apretar guardar le borraba la clasificación, y
# el bono desaparecía de su tabla sin un solo error.
_COLS_FUERA_DEL_BLOB = ("emisor", "emisor_tipo", "moneda_eje", "ajuste",
                        "ajuste_alt", "ley")


def _columnas_por_ticker(tickers: list[str]) -> dict[str, dict]:
    if not tickers:
        return {}
    cols = ", ".join(_COLS_FUERA_DEL_BLOB)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT ticker, {cols} FROM mercado.curvas "
                    f"WHERE ticker = ANY(%s)", (tickers,))
        nombres = [d[0] for d in cur.description]
        return {r[0]: dict(zip(nombres, r, strict=False)) for r in cur.fetchall()}


def list_bonos(curva: str | None = None) -> list[dict]:
    """**TODOS** los bonos de `mercado.curvas`, corporativos incluidos.

    Antes devolvía `no_corporativos()` porque las ONs tenían su propio editor. Al
    borrarse la vista `/ons` (2026-08-16) ese editor se fusionó acá: si el listado
    siguiera excluyéndolos, los ~140 corporativos quedarían en la base —
    alimentando `/renta-fija` y ACREENCIAS — sin **ninguna** pantalla donde
    editarlos. Ser corporativo es un EJE (`emisor_tipo`), no una familia aparte:
    el filtrado es del que mira, no del endpoint.

    Los docs vienen del blob; las columnas que el blob no tiene se mergean encima
    (ver `_COLS_FUERA_DEL_BLOB`). El blob NUNCA gana: si tuviera una copia vieja de
    `emisor`, la columna la pisa — que es el sentido de haberlo estandarizado.
    """
    out = curvas_sql.por_curva(curva) if curva else curvas_sql.cargar_todos()
    por_tk = _columnas_por_ticker([d["ticker_corto"] for d in out if d.get("ticker_corto")])
    for d in out:
        extra = por_tk.get(d.get("ticker_corto"))
        if extra:
            d.update({k: v for k, v in extra.items() if k != "ticker"})
    # Orden por VENCIMIENTO, no por `curva`: la columna dejó de ser la identidad del
    # bono y agrupar por ella ponía juntos papeles que ya no comparten nada.
    out.sort(key=lambda d: (str(d.get("fecha_vencimiento") or "9999"),
                            d.get("ticker_corto") or ""))
    return out


# Campos doc nivel-bono que el editor puede setear (según tipo).
_DOC_STR = ("tipo", "moneda_flujo", "tasa_referencia", "emisor")  # strings tal cual
_DOC_NUM = ("cer_emision", "cupon_anual")                  # numéricos
_DOC_FECHA = ("fecha_emision", "fecha_vencimiento")        # fechas ISO
# Campos numéricos de un FLUJO (pass-through; el subset depende del tipo de bono).
_FLUJO_NUM = ("amortizacion", "interes", "valor_residual", "amortizacion_pct",
              "cupon_sobre_residual", "residual_previo_pct", "cupon_anual")


def upsert_bono(payload: dict, actor: str = "") -> dict:
    """Crea/edita un bono directo en Trading.Curvas (upsert por `ticker_corto`).
    Guarda las MISMAS shapes de Trading.Curvas según el tipo (no se inventa nada):
      - lecap/boncap (tasa_fija) → bullet `flujo_vencimiento`.
      - cer  → flujos {fecha, amortizacion_pct, cupon_sobre_residual, residual_previo_pct} + cer_emision/cupon_anual.
      - dual/tamar → flujos {fecha, amortizacion_pct} + tasa_referencia.
      - tasa_fija con cupón → flujos {fecha, amortizacion, interes}.
      - soberanos → flujos {fecha, amortizacion_pct, cupon_sobre_residual}.
    """
    tc = (payload.get("ticker_corto") or "").strip()
    if not tc:
        raise ValueError("falta 'ticker_corto'")
    ticker = (payload.get("ticker") or "").strip()
    if not ticker:
        raise ValueError("falta 'ticker' (completo, ej 'MERV - XMEV - T30J6 - 24hs')")
    curva = (payload.get("curva") or "").strip()
    if curva not in CURVAS_BONO:
        raise ValueError(f"curva inválida: {curva!r} (válidas: {', '.join(CURVAS_BONO)})")

    doc: dict = {
        "ticker": ticker,
        "ticker_corto": tc,
        "curva": curva,
        "valor_nominal": _f(payload.get("valor_nominal"), 100.0) or 100.0,
        "actualizado_por": actor,
        "actualizado_at": datetime.now(UTC),
    }
    for k in _DOC_STR:
        v = payload.get(k)
        if v not in (None, ""):
            doc[k] = v.upper() if k == "moneda_flujo" else v
    for k in _DOC_NUM:
        if payload.get(k) is not None:
            doc[k] = _f(payload.get(k))
    for k in _DOC_FECHA:
        fi = _fecha_iso(payload.get(k))
        if fi:
            doc[k] = fi

    # Flujo: bullet (Lecap/Boncap) o cronograma (pass-through de los campos del tipo).
    fv = payload.get("flujo_vencimiento")
    flujos_in = payload.get("flujos")
    if flujos_in:
        flujos = []
        for fl in flujos_in:
            fi = _fecha_flujo_iso(fl.get("fecha"))
            if not fi:
                continue
            row: dict = {"fecha": fi}
            for ff in _FLUJO_NUM:
                if fl.get(ff) is not None:
                    row[ff] = _f(fl.get(ff))
            flujos.append(row)
        if not flujos:
            raise ValueError("los flujos no tienen ninguna fecha válida")
        doc["flujos"] = flujos
        doc["flujo_vencimiento"] = None   # cronograma → sin bullet
    elif fv is not None and _f(fv) > 0:
        if not doc.get("fecha_vencimiento"):
            raise ValueError("el bullet (flujo_vencimiento) necesita 'fecha_vencimiento'")
        doc["flujo_vencimiento"] = _f(fv)
    else:
        raise ValueError("falta el flujo: 'flujo_vencimiento' (bullet) o 'flujos' (cronograma)")

    # Los EJES (emisor_tipo/moneda_eje/ajuste/ajuste_alt/ley) van directo a las
    # COLUMNAS, no al doc: `normalizar_ejes` valida el dominio y, sobre todo, que
    # `ajuste_alt != ajuste` — un dual consigo mismo entra sin ruido y se ve bien.
    # Hasta hoy los ejes solo los escribía un script one-shot, así que un bono dado
    # de alta acá nacía SIN clasificar y no aparecía en la vista de renta fija.
    ejes = ce.normalizar_ejes(payload)
    saved = _upsert_curva_doc(doc, ejes)
    return {"bono": saved}


def bonos_sin_tasa() -> dict:
    """Bonos de mercado.curvas que tienen precio de pantalla pero NO tienen TEA
    calculada en el snapshot (last_price > 0 y tea IS NULL) — los que muestran "--".

    Es el "ver los errores" del panel de bonos: SQL puro (rápido, read-only), no
    recalcula. Para el POR QUÉ de cada uno usar el debug (/checks/debug-curva-tea).
    Un bono sin precio (nunca operó / motor no lo suscribe) NO cuenta como error acá.
    """
    from core import market_snapshot

    # A propósito NO incluye corporativos, aunque el listado de al lado sí: las ONs
    # son ilíquidas y su TEA falta por motivos normales (sin precio, sin flujo
    # cargado). Meterlas acá llenaría REVISAR de rojos que nadie va a accionar, y
    # un tablero que siempre está en rojo deja de mirarse. Si algún día se quiere,
    # es cambiar esta línea por `cargar_todos()`.
    docs = curvas_sql.no_corporativos()
    by_full = {d.get("ticker"): d for d in docs if d.get("ticker")}
    cols = market_snapshot.cols_map(list(by_full), ["last_price", "tea"])

    filas = []
    for full, d in by_full.items():
        m = cols.get(full) or {}
        lp = m.get("last_price")
        if lp and lp > 0 and m.get("tea") is None:
            filas.append({
                "ticker_corto": d.get("ticker_corto"),
                "ticker": full,
                "curva": d.get("curva"),
                "fecha_vencimiento": str(d.get("fecha_vencimiento") or "")[:10],
                "last_price": lp,
            })
    filas.sort(key=lambda x: (x.get("curva") or "", x.get("ticker_corto") or ""))
    return {"total": len(filas), "ok": not filas, "bonos": filas}


def delete_bono(ticker_corto: str) -> dict:
    """Baja un bono de mercado.curvas por ticker_corto.

    El guard `curva NOT LIKE 'on%'` se sacó al fusionar el editor de ONs acá: si
    siguiera, el botón «baja» de un corporativo diría OK y no borraría nada
    (`rowcount 0`), que es el peor de los dos fracasos posibles.
    """
    tc = (ticker_corto or "").strip()
    if not tc:
        raise ValueError("falta 'ticker_corto'")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mercado.curvas WHERE ticker = %s", (tc,))
        deleted = cur.rowcount or 0
    curvas_sql.invalidar()   # refrescar el cache del master tras la baja
    return {"borrado": deleted}
