"""jobs/fci_universo.py — el UNIVERSO del mercado FCI: qué fondos existen y de quién son.

Doc madre: `docs/FCI.md`. Corre 12:20 UTC L-V (después del discovery de Primary).

Cuatro pasos, siempre en este orden:

 1. GERENTES. Asegura en `mercado.fci_gerentes` los nombres con los que la mesa
    ya nombra a las sociedades gerentes: `clientes.contrapartes` (segmento
    Fondos) y `portafolio.assets.emisor` (cartera FCI). Solo INSERTA los que
    faltan (alias = el propio nombre + los conocidos de _ALIAS_CONOCIDOS); lo que
    la mesa editó (alias, seguida) no se toca. **Esta lista es el filtro duro**:
    un fondo cuya gerente no está acá, o está con seguida=false, no entra.

 2. PRIMARY. Una llamada `get_detailed_instruments`; de los ~776 con cficode
    CIO se queda con los que `core/fci_match.gerente_de` asigna a una gerente
    seguida. Upsert por `simbolo_primary` (nombre, moneda, tipo de renta, plazo).
    Los que Primary dejó de listar se marcan `activo=false` (no se borran).

 3. ASSETS (lo que la ALyC tiene). Por cada asset FCI:
      · con `instrumento` cargado → se linkea a la fila Primary de ese símbolo
        (`unidad`, `cafci`); si no existe esa fila, se crea con origen 'asset'.
      · sin `instrumento` → se busca por nombre normalizado entre las filas
        Primary. Match ÚNICO → se escribe `assets.instrumento` (la mesa pidió que
        el símbolo Primary viva ahí, que es donde se suscribe la market data) y se
        linkea. Cero o varios → fila propia con origen 'asset' (un bilateral, o
        uno que la mesa tiene que resolver a mano en Manager → TÍTULOS → ASSETS)
        y se lista en el log.
    La gerente de una fila linkeada es `assets.emisor` (la palabra de la mesa),
    aunque el prefijo diga otra cosa.

 4. CLASE. La `categoria` de la fila ES la clase de activo de Manager → ASSETS
    (MM ARS · ARS T1 · MM USD · HD T1 · RENTA VARIABLE): en los fondos linkeados
    se copia del asset (la mesa la edita ahí y nada más); en los que no tienen
    asset se sugiere con la misma regla que usa el agente (`core/clase_activo.de_fci`)
    solo donde está vacía.

Uso:
    python -m jobs.fci_universo            # todo
    python -m jobs.fci_universo --dry      # muestra qué haría, sin escribir
"""
from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime

import pyRofex

from core.fci_match import (
    alias_compartidos,
    gerente_de,
    nombre_desde_primary,
    normalizar,
    plazo_desde_settl,
    sugerir_categoria,
)
from core.job_runs import JobRunLogger
from core.postgres import get_pool
from core.rofex_session import inicializar_sesion

logger = logging.getLogger(__name__)

_CIO = "CIO"
_MIN_CIO = 200   # debajo de esto la foto de Primary es anómala → no se desactiva nada

# Alias que el nombre del fondo NO dice solo (marca ≠ sociedad). Se siembran una
# vez al crear la gerente; después son de la mesa (scripts/fci_admin gerente).
# Un alias vive en UNA sola gerente: si la mesa nombra a la misma sociedad de dos
# formas (TORONTO y BACS), el alias se carga a mano en una y el job avisa si queda
# compartido (`alias_compartidos`).
_ALIAS_CONOCIDOS: dict[str, list[str]] = {
    "TORONTO":  ["Toronto Trust"],
    "MARIVA":   ["MAF", "Mariva"],
    "IEB":      ["IEB", "Ciclo Nova"],
    "ONE618":   ["One618", "Consultatio"],
    "IAM":      ["IAM"],
    "MAX":      ["Max"],
    "STONEX":   ["StoneX", "Gainvest"],
    "MEGAQM":   ["MegaQM", "Megainver", "Quinquela"],
    "LOMBARD":  ["Lombard"],
    "COMPASS":  ["Compass", "Vinci Compass"],
    "CREDICOOP": ["1810"],
}


# La moneda de un fondo que NO está en Primary sale de su clase de activo (el
# vocabulario de assets ya la lleva adentro) o, si no tiene clase, de la moneda
# con que Aunesa lo valúa en la tenencia. Sin esto un bilateral queda sin moneda
# y el filtro ARS/USD de la vista lo esconde (bug 2026-09-11: Schroder = 0 filas).
_MONEDA_POR_CLASE = {"MM ARS": "ARS", "ARS T1": "ARS", "RENTA VARIABLE": "ARS",
                     "MM USD": "USD", "HD T1": "USD"}


def _monedas_tenencia(cur, unidades: list[str]) -> dict[str, str]:
    """unidad → moneda de la ÚLTIMA tenencia (últimos 45 días, sobre el índice (fecha, unidad))."""
    if not unidades:
        return {}
    cur.execute("SELECT DISTINCT ON (unidad) unidad, upper(moneda) FROM portafolio.tenencia "
                "WHERE fecha >= CURRENT_DATE - 45 AND unidad = ANY(%s) AND moneda IS NOT NULL "
                "ORDER BY unidad, fecha DESC", (unidades,))
    return {u: m for u, m in cur.fetchall() if m in ("ARS", "USD")}


def _sym(inst: dict) -> str | None:
    s = inst.get("symbol")
    if isinstance(s, str) and s:
        return s
    s = (inst.get("instrumentId") or {}).get("symbol")
    return s if isinstance(s, str) and s else None


# ── paso 1 ───────────────────────────────────────────────────────────────────

def _asegurar_gerentes(cur, dry: bool) -> tuple[int, dict[str, list[str]]]:
    cur.execute("SELECT upper(btrim(contraparte)) FROM clientes.contrapartes "
                "WHERE segmento = 'Fondos' AND contraparte IS NOT NULL AND btrim(contraparte) <> ''")
    de_cp = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT DISTINCT upper(btrim(emisor)) FROM portafolio.assets "
                "WHERE cartera IN ('FCI', 'CARTERA FCI') AND COALESCE(vigente, true) AND emisor IS NOT NULL "
                "AND btrim(emisor) <> '' AND upper(btrim(emisor)) NOT IN ('NO APLICA', 'N/A', '-')")
    de_assets = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT gerente FROM mercado.fci_gerentes")
    existentes = {r[0] for r in cur.fetchall()}
    nuevas = 0
    for g in sorted((de_cp | de_assets) - existentes):
        alias = _ALIAS_CONOCIDOS.get(g, [g.title()])
        if not dry:
            cur.execute("INSERT INTO mercado.fci_gerentes (gerente, alias, origen) "
                        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                        (g, alias, "contrapartes" if g in de_cp else "assets"))
        nuevas += 1
    cur.execute("SELECT gerente, alias FROM mercado.fci_gerentes WHERE seguida ORDER BY gerente")
    seguidas = {g: list(a or []) for g, a in cur.fetchall()}
    if dry:   # en dry las nuevas no están en la base: se agregan al mapa en memoria
        for g in (de_cp | de_assets) - existentes:
            seguidas.setdefault(g, _ALIAS_CONOCIDOS.get(g, [g.title()]))
    return nuevas, seguidas


# ── paso 2 ───────────────────────────────────────────────────────────────────

def _primary(cur, seguidas: dict[str, list[str]], dry: bool, jr: JobRunLogger) -> dict:
    inicializar_sesion()
    res = pyRofex.get_detailed_instruments()
    if not res or res.get("status") != "OK":
        raise RuntimeError(f"get_detailed_instruments: {(res or {}).get('status')}")
    cio = [i for i in (res.get("instruments") or []) if str(i.get("cficode") or "").startswith(_CIO)]
    jr.set_stat("primary_cio", len(cio))
    if len(cio) < _MIN_CIO:
        raise RuntimeError(f"foto de Primary anómala: {len(cio)} CIO (< {_MIN_CIO})")

    now = datetime.now(UTC)
    filas, sin_gerente = [], 0
    for i in cio:
        sym = _sym(i)
        if not sym:
            continue
        nombre = nombre_desde_primary(i)
        g = gerente_de(nombre, seguidas)
        if not g:
            sin_gerente += 1
            continue
        filas.append({
            "nombre": nombre, "nombre_norm": normalizar(nombre), "gerente": g,
            "simbolo_primary": sym, "primary_id": str(i.get("securityId") or "") or None,
            "moneda": i.get("currency"), "tipo_renta": i.get("underlying"),
            "plazo": plazo_desde_settl(i.get("settlType")),
        })
    jr.set_stat("primary_de_nuestras_gerentes", len(filas))
    jr.set_stat("primary_otras_gerentes", sin_gerente)
    if dry:
        return {"simbolos": {f["simbolo_primary"] for f in filas}, "filas": filas}

    for f in filas:
        cur.execute(
            "INSERT INTO mercado.fci (nombre, nombre_norm, gerente, simbolo_primary, primary_id,"
            " moneda, tipo_renta, plazo, origen, activo, actualizado_en) "
            "VALUES (%(nombre)s, %(nombre_norm)s, %(gerente)s, %(simbolo_primary)s, %(primary_id)s,"
            " %(moneda)s, %(tipo_renta)s, %(plazo)s, 'primary', true, %(now)s) "
            "ON CONFLICT (simbolo_primary) DO UPDATE SET "
            " nombre = EXCLUDED.nombre, nombre_norm = EXCLUDED.nombre_norm,"
            " primary_id = EXCLUDED.primary_id, moneda = EXCLUDED.moneda,"
            " tipo_renta = EXCLUDED.tipo_renta, plazo = EXCLUDED.plazo, activo = true,"
            # la gerente la manda el asset si está linkeado (paso 3); si no, el prefijo
            " gerente = CASE WHEN mercado.fci.unidad IS NULL THEN EXCLUDED.gerente ELSE mercado.fci.gerente END,"
            " actualizado_en = EXCLUDED.actualizado_en",
            {**f, "now": now})
    simbolos = [f["simbolo_primary"] for f in filas]
    cur.execute("UPDATE mercado.fci SET activo = false, actualizado_en = %s "
                "WHERE simbolo_primary IS NOT NULL AND activo AND unidad IS NULL "
                "AND NOT (simbolo_primary = ANY(%s))", (now, simbolos))
    jr.set_stat("primary_desactivados", cur.rowcount if cur.rowcount > 0 else 0)
    return {"simbolos": set(simbolos), "filas": filas}


# ── paso 3 ───────────────────────────────────────────────────────────────────

def _assets(cur, seguidas: dict[str, list[str]], primary: dict, dry: bool, jr: JobRunLogger) -> None:
    cur.execute("SELECT unidad, ticker, emisor, instrumento, cafci, clase_activo FROM portafolio.assets "
                "WHERE cartera IN ('FCI', 'CARTERA FCI') AND COALESCE(vigente, true) ORDER BY unidad")
    assets = cur.fetchall()
    por_norm: dict[str, list[dict]] = {}
    for f in primary["filas"]:
        por_norm.setdefault(f["nombre_norm"], []).append(f)
    now = datetime.now(UTC)
    monedas = _monedas_tenencia(cur, [a[0] for a in assets])
    linkeados = nuevos_link = bilaterales = ambiguos = fuera = conflictos = 0
    for unidad, ticker, emisor, instrumento, cafci, clase_activo in assets:
        clase = (clase_activo or "").strip().upper() or None
        if clase in ("NO APLICA", "N/A", "-"):
            clase = None
        moneda = _MONEDA_POR_CLASE.get(clase or "") or monedas.get(unidad)
        gerente = (emisor or "").strip().upper() or None
        if gerente and gerente not in seguidas:
            gerente_seg = gerente_de(ticker or unidad, seguidas)
            if not gerente_seg:
                fuera += 1            # emisor de una gerente no seguida → no entra
                continue
            gerente = gerente_seg
        nombre = (ticker or "").strip() or unidad
        norm = normalizar(nombre)
        sym = (instrumento or "").strip()
        if sym.upper() in ("", "NO APLICA", "-", "N/A"):
            sym = ""
        if not sym:
            cands = por_norm.get(norm) or []
            if len(cands) == 1:
                sym = cands[0]["simbolo_primary"]
                nuevos_link += 1
                if not dry:
                    cur.execute("UPDATE portafolio.assets SET instrumento = %s, actualizado_por = %s, "
                                "actualizado_at = %s WHERE unidad = %s AND "
                                "(instrumento IS NULL OR btrim(instrumento) = '' OR upper(instrumento) = 'NO APLICA')",
                                (sym, "jobs.fci_universo", now, unidad))
            elif len(cands) > 1:
                ambiguos += 1
                jr.log(f"   ambiguo: {unidad[:60]} → {[c['simbolo_primary'] for c in cands]}")
        if dry:
            linkeados += bool(sym)
            bilaterales += not sym
            continue
        if sym and sym in primary["simbolos"]:
            # la fila Primary existe: linkear. Si la unidad ya tenía una fila propia
            # (origen 'asset', de antes de conocer el símbolo), se FUSIONA: su serie
            # pasa a la fila Primary (sin pisar lo que ya tenga) y la vieja se borra.
            cur.execute("SELECT fci_id FROM mercado.fci WHERE simbolo_primary = %s", (sym,))
            destino = cur.fetchone()[0]
            cur.execute("SELECT fci_id, simbolo_primary FROM mercado.fci WHERE unidad = %s AND fci_id <> %s",
                        (unidad, destino))
            vieja = cur.fetchone()
            if vieja and vieja[1]:
                # La unidad ya está linkeada a OTRO fondo de Primary: dos assets con
                # el mismo símbolo, o un símbolo mal cargado. No se borra nada a
                # ciegas (REGLA #9): se avisa y la mesa lo resuelve en Manager.
                conflictos += 1
                jr.log(f"   conflicto: {unidad[:60]} ya linkeada a {vieja[1]!r}; no se mueve a {sym!r}")
                continue
            if vieja:
                cur.execute("INSERT INTO mercado.fci_vcp (fci_id, fecha, vcp, fuente) "
                            "SELECT %s, fecha, vcp, fuente FROM mercado.fci_vcp WHERE fci_id = %s "
                            "ON CONFLICT DO NOTHING", (destino, vieja[0]))
                cur.execute("DELETE FROM mercado.fci_vcp WHERE fci_id = %s", (vieja[0],))
                cur.execute("DELETE FROM mercado.fci WHERE fci_id = %s", (vieja[0],))
            # La CLASE la manda el asset (Manager → ASSETS es el único lugar donde
            # se edita): si está cargada, pisa la sugerencia del job.
            cur.execute("UPDATE mercado.fci SET unidad = %s, cafci = %s, gerente = COALESCE(%s, gerente), "
                        "categoria = COALESCE(%s, categoria), actualizado_en = %s WHERE fci_id = %s",
                        (unidad, cafci, gerente, clase, now, destino))
            linkeados += 1
        else:
            # bilateral (o símbolo que Primary hoy no lista): fila propia por unidad
            cur.execute(
                "INSERT INTO mercado.fci (nombre, nombre_norm, gerente, simbolo_primary, unidad, cafci,"
                " categoria, moneda, origen, activo, actualizado_en) "
                "VALUES (%s, %s, %s, NULL, %s, %s, %s, %s, 'asset', true, %s) "
                "ON CONFLICT (unidad) DO UPDATE SET nombre = EXCLUDED.nombre,"
                " nombre_norm = EXCLUDED.nombre_norm, gerente = COALESCE(EXCLUDED.gerente, mercado.fci.gerente),"
                " cafci = EXCLUDED.cafci, categoria = COALESCE(EXCLUDED.categoria, mercado.fci.categoria),"
                " moneda = COALESCE(EXCLUDED.moneda, mercado.fci.moneda),"
                " activo = true, actualizado_en = EXCLUDED.actualizado_en",
                (nombre, norm, gerente, unidad, cafci, clase, moneda, now))
            bilaterales += 1
    jr.set_stat("assets_fci", len(assets))
    jr.set_stat("assets_linkeados_primary", linkeados)
    jr.set_stat("assets_instrumento_completado", nuevos_link)
    jr.set_stat("assets_sin_primary", bilaterales)
    jr.set_stat("assets_ambiguos", ambiguos)
    jr.set_stat("assets_conflicto_simbolo", conflictos)
    jr.set_stat("assets_gerente_no_seguida", fuera)


# ── paso 4 ───────────────────────────────────────────────────────────────────

# Vocabulario que el job inventó en su primera versión (2026-09-10) y que NO es el
# de Manager → ASSETS. Se limpia en cada corrida (idempotente) para que la clase
# vuelva a sugerirse con el vocabulario de la mesa. Borrar cuando prod ya no lo tenga.
_VOCABULARIO_VIEJO = ("T+0 MONEY MARKET", "MONEY MARKET USD", "T+0", "T+1", "RENTA FIJA USD",
                      "RENTA MIXTA", "RENTA MIXTA USD", "T+0 LECAPS")


def _categorias(cur, dry: bool) -> int:
    if not dry:
        cur.execute("UPDATE mercado.fci SET categoria = NULL WHERE categoria = ANY(%s)",
                    (list(_VOCABULARIO_VIEJO),))
    cur.execute("SELECT fci_id, tipo_renta, plazo, moneda FROM mercado.fci WHERE categoria IS NULL")
    n = 0
    for fci_id, tr, plazo, mon in cur.fetchall():
        cat = sugerir_categoria(tr, plazo, mon)
        if cat:
            n += 1
            if not dry:
                cur.execute("UPDATE mercado.fci SET categoria = %s WHERE fci_id = %s AND categoria IS NULL",
                            (cat, fci_id))
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry", action="store_true", help="no escribe, solo reporta")
    a = ap.parse_args()
    with JobRunLogger("fci_universo") as jr, \
            get_pool().connection() as conn, conn.cursor() as cur:
        nuevas, seguidas = _asegurar_gerentes(cur, a.dry)
        jr.set_stat("gerentes_nuevas", nuevas)
        jr.set_stat("gerentes_seguidas", len(seguidas))
        compartidos = alias_compartidos(seguidas)
        jr.set_stat("alias_compartidos", len(compartidos))
        for al, gs in compartidos.items():
            jr.log(f"   ⚠️ alias '{al}' en {gs}: gana {gs[0]} — resolver con scripts/fci_admin gerente")
        if not seguidas:
            jr.error("no hay gerentes seguidas: cargar contrapartes (segmento Fondos) o assets FCI con emisor")
            return 1
        try:
            primary = _primary(cur, seguidas, a.dry, jr)
        except Exception as e:
            jr.error(f"Primary: {e}")
            conn.rollback()
            return 1
        _assets(cur, seguidas, primary, a.dry, jr)
        jr.set_stat("categorias_sugeridas", _categorias(cur, a.dry))
        if a.dry:
            conn.rollback()
            jr.log("DRY · gerentes seguidas: " + ", ".join(sorted(seguidas)))
        else:
            conn.commit()
        jr.log(f"fci_universo{' DRY' if a.dry else ''}: {jr.stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
