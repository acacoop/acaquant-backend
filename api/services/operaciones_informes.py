"""operaciones_informes.py — normalización + ingesta a CashFlow.Operaciones.

`CashFlow.Operaciones` es la FUENTE DE VERDAD de operaciones (desde la API
`informes` de Aunesa, que NO trae movimientos administrativos / FCI bilateral —
esos siguen en NegocioMovimientos). Schema mínimo, 10 campos:

  boleto (🔑 único), cuenta, concertacion, denominacion, tipo_operacion,
  instrumento, condiciones, cantidad, bruto, arancel

LEY #1: un boleto = un documento → índice ÚNICO sobre `boleto`.

Este service es puro (sin FastAPI). Lo usan:
  - api/routers/manager/operaciones.py (backfill por CSV desde la UI).
  - (futuro) el job de ingesta diaria desde la API informes — mismo normalizador.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime, timedelta

from api.services._mep import get_mep_for_date
from core.postgres import get_pool


def _niveles_por_cuenta() -> dict[str, dict]:
    """id_cuenta → {n1 (nivel_1), n3 (nivel_3)} desde SQL clientes.comitentes."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_cuenta, nivel_1, nivel_3 FROM comitentes "
                    "WHERE id_cuenta IS NOT NULL AND id_cuenta <> ''")
        return {str(idc).strip(): {"n1": n1 or "", "n3": n3 or ""}
                for idc, n1, n3 in cur.fetchall()}


# Header (normalizado: lower, sin acentos, sin separadores) → campo canónico.
# Cubre los nombres de la API informes y los del histórico (Excel en español).
_ALIASES: dict[str, str] = {
    "boleto": "boleto",
    "cuenta": "cuenta",
    "idcuenta": "cuenta",          # export con los nombres de columna SQL
    "nrocuenta": "cuenta",
    "concertacion": "concertacion",
    "fechaconcertacion": "concertacion",
    "denominacion": "denominacion",
    "tipodeoperacion": "tipo_operacion",
    "tipooperacion": "tipo_operacion",
    "instrumento": "instrumento",
    "condiciones": "condiciones",
    "cantidad": "cantidad",
    "cantidadtotal": "cantidad",
    "bruto": "bruto",
    "aranceles": "arancel",
    "arancel": "arancel",
    "tasa": "tasa",
}


def _norm_header(h: str) -> str:
    s = unicodedata.normalize("NFKD", str(h)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


# Boletos OTC que NO se ingestan (decisión 2026-06-08). Los `tipo_operacion`
# "Rueda OTC NDF OTC - Compra/Venta" y "Rueda OTC Opciones OTC - Compra/Venta"
# eran ~22% de la colección (107k docs, ~49 MB) bajo operacion="otro",
# mercado=None y volumen despreciable (~7,9M USD en 3,5 años) → ruido para la
# vista OPERACIONES. Se purgaron una vez y se bloquean acá para que no vuelvan
# a entrar. OJO: NO excluye "Concurrencia OTC".
_OTC_EXCLUIR_RE = re.compile(r"NDF\s*OTC|Opciones\s*OTC", re.IGNORECASE)


def es_otc_excluido(tipo_operacion: str | None) -> bool:
    """True si el boleto es NDF OTC / Opciones OTC (no se ingesta). Misma regla
    que usa el script de purga → mantener en sync si cambia el criterio."""
    return bool(tipo_operacion and _OTC_EXCLUIR_RE.search(tipo_operacion))


def _to_str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _to_cuenta(v) -> str | None:
    """Cuenta siempre como string sin '.0' (el Excel a veces la trae numérica)."""
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s or None


# Excel cuenta los días desde el 1899-12-30 (el "bug" del año bisiesto 1900 ya
# está compensado en ese ancla). Rango aceptado: 1990-01-01 .. 2100-12-31, para
# no confundir un número cualquiera con una fecha.
_EXCEL_EPOCH = date(1899, 12, 30)
_FECHA_RE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})")


def _from_excel_serial(n) -> str | None:
    try:
        dias = int(float(n))
    except (TypeError, ValueError):
        return None
    if not 32874 <= dias <= 73415:
        return None
    return (_EXCEL_EPOCH + timedelta(days=dias)).isoformat()


def _to_iso(raw) -> str | None:
    """A 'YYYY-MM-DD'. Acepta date/datetime, ISO (con o sin hora), DD/MM/YYYY
    (separador / - o .), año de 2 dígitos y el serial numérico de Excel.

    Un mismo Excel suele traer la columna MEZCLADA (filas tipeadas como fecha
    conviven con filas tipeadas como texto) → hay que cubrir todos los casos o
    la mitad de los boletos entra sin fecha y desaparece de las series.
    OJO: DD/MM, no MM/DD — un archivo en formato US cargaría el día por el mes.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, date):
        return raw.isoformat()
    if isinstance(raw, (int, float)):
        return _from_excel_serial(raw)
    s = str(raw).strip()
    if not s:
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    m = _FECHA_RE.match(s)
    if m:
        d, mes, y = (int(x) for x in m.groups())
        if y < 100:
            y += 2000 if y < 70 else 1900
        try:
            return date(y, mes, d).isoformat()
        except ValueError:
            return None
    if re.fullmatch(r"\d+(?:[.,]0+)?", s):
        return _from_excel_serial(s.replace(",", "."))
    return None


def _to_moneda(condiciones) -> str | None:
    """Moneda de la operación, derivada de `condiciones` ('ARS Inm', 'USD 24hs')."""
    if not condiciones:
        return None
    tok = str(condiciones).strip().upper().split()
    if not tok:
        return None
    m = tok[0]
    if m.startswith("USD"):
        return "USD"
    if m.startswith("ARS"):
        return "ARS"
    return m or None


def _to_float(raw) -> float | None:
    """Parsea número tolerando: prefijo de moneda ('ARS 248.90'), separadores
    AR ('82965,6' → 82965.6; '1.234.567,89' → 1234567.89) y punto decimal."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = re.sub(r"[^\d,.\-]", "", str(raw).strip())  # tira 'ARS', símbolos, espacios
    if not s or s in ("-", ".", ","):
        return None
    has_dot, has_comma = "." in s, "," in s
    if has_dot and has_comma:
        # el ÚLTIMO separador es el decimal.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")   # '.' miles, ',' decimal
        else:
            s = s.replace(",", "")                       # ',' miles, '.' decimal
    elif has_comma:
        s = s.replace(",", ".")                          # ',' decimal
    try:
        return float(s)
    except ValueError:
        return None


def normalizar_fila(row: dict) -> dict | None:
    """Fila cruda (header→valor) → doc canónico de 10 campos. None si no hay boleto."""
    canon: dict = {}
    for h, v in row.items():
        campo = _ALIASES.get(_norm_header(h))
        if campo and campo not in canon:
            canon[campo] = v

    boleto = _to_str(canon.get("boleto"))
    if not boleto:
        return None
    return {
        "boleto":         boleto,
        "cuenta":         _to_cuenta(canon.get("cuenta")),
        "concertacion":   _to_iso(canon.get("concertacion")),
        "denominacion":   _to_str(canon.get("denominacion")),
        "tipo_operacion": _to_str(canon.get("tipo_operacion")),
        "instrumento":    _to_str(canon.get("instrumento")),
        "condiciones":    _to_str(canon.get("condiciones")),
        "cantidad":       _to_float(canon.get("cantidad")),
        "bruto":          _to_float(canon.get("bruto")),
        "arancel":        _to_float(canon.get("arancel")),
        "tasa":           _to_float(canon.get("tasa")),
        "moneda":         _to_moneda(canon.get("condiciones")),
    }


def ensure_indexes(coll) -> None:
    """LEY #1: índice único parcial sobre boleto + índices de consulta."""
    coll.create_index(
        [("boleto", 1)], name="uq_boleto", unique=True,
        partialFilterExpression={"boleto": {"$type": ["string", "int", "long", "double"]}},
    )
    coll.create_index([("cuenta", 1), ("concertacion", -1)], name="cuenta_concertacion")
    coll.create_index([("concertacion", -1)], name="concertacion")
    # /ops/serie + /ops/aranceles + /ops/resumen filtran por moneda excluyendo
    # cierres de caución. Con `es_cierre` materializado, este índice deja que el
    # match (moneda + es_cierre) + group/sort por concertacion corran por índice
    # en vez de escanear la colección (antes: `$not /Cierre/` = COLLSCAN).
    coll.create_index([("moneda", 1), ("es_cierre", 1), ("concertacion", -1)],
                      name="moneda_escierre_concertacion")
    # /ops/agro: la serie es histórica (sin fecha) y los futuros agro son una
    # MINORÍA de los boletos → índice PARCIAL sobre el `commodity` materializado
    # (ver _clasificar_commodity) para que el match no escanee toda la colección.
    coll.create_index(
        [("commodity", 1), ("concertacion", -1)], name="commodity_concertacion",
        partialFilterExpression={"commodity": {"$in": ["SOJA", "TRIGO", "MAIZ"]}},
    )


def cargar_maps_enrich(db=None) -> tuple[dict, dict]:
    """Carga (catálogo tipo→{mercado,operacion}, id_cuenta→nivel_1) — para
    enriquecer inline en la ingesta diaria sin scan completo de la colección.

    SQL-native (decomiso Mongo): el catálogo se lee de `operaciones.tipos_operacion`
    (jsonb `data` con mercado/operacion), ya no de Mongo `CashFlow.TiposOperacion`.
    El param `db` se mantiene por compat de llamadas viejas pero se ignora."""
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT tipo_operacion, data->>'mercado', data->>'operacion' "
                    "FROM tipos_operacion")
        cat = {t: {"tipo_operacion": t, "mercado": m, "operacion": o}
               for t, m, o in cur.fetchall() if t}
    niveles = _niveles_por_cuenta()
    return cat, niveles


def clasificar_commodity(
    tipo_operacion: str | None, denominacion: str | None, instrumento: str | None,
) -> str | None:
    """Clasifica un boleto como agro (SOJA/TRIGO/MAIZ) o None.

    Es agro si:
      - FUTURO agro: tipo_operacion contiene 'Futuros' y NO 'Financieros', o
      - OPCIÓN agro: tipo_operacion contiene 'Opciones' y 'Agropecuario',
    y en ambos casos el instrumento matchea SOJ/TRI/MAI.

    OTC: por defecto excluye (ni denominación ni instrumento deben contener
    'OTC'). EXCEPCIÓN (2026-06-03): los 'Futuros/Opciones Agropecuarios -
    Compra/Venta' SON agro aunque la cuenta o el instrumento tengan 'OTC' — ese
    tipo es la señal autoritativa de agro, así que no los excluimos por OTC.
    (Las 'Opciones OTC' NDF ni siquiera se ingestan — ver _es_otc_excluir.)

    Materializado en la ingesta + replicado server-side en
    scripts/backfill_agro_tipo.py (mantener ambos en sync). El `tipo`
    futuro/opción lo da clasificar_tipo_agro (columna `tipo_agro`).
    """
    t = (tipo_operacion or "").upper()
    es_fut = "FUTUROS" in t and "FINANCIEROS" not in t
    es_opc = "OPCIONES" in t and "AGROPECUARIO" in t
    if not (es_fut or es_opc):
        return None
    inst = (instrumento or "").upper()
    # Excepción agro: los '... Agropecuarios - Compra/Venta' no se excluyen por OTC.
    agro_cv = "AGROPECUARIO" in t and ("COMPRA" in t or "VENTA" in t)
    if not agro_cv and ("OTC" in inst or "OTC" in (denominacion or "").upper()):
        return None
    if "SOJ" in inst:
        return "SOJA"
    if "TRI" in inst:
        return "TRIGO"
    if "MAI" in inst:
        return "MAIZ"
    return None


def clasificar_tipo_agro(tipo_operacion: str | None) -> str | None:
    """FUTURO | OPCION | None según el `tipo_operacion` de un boleto agro.

    Solo tiene sentido para boletos ya clasificados como agro (commodity no
    None): distingue la pata de derivado. Mismo criterio que clasificar_commodity."""
    t = (tipo_operacion or "").upper()
    if "FUTUROS" in t and "FINANCIEROS" not in t:
        return "FUTURO"
    if "OPCIONES" in t and "AGROPECUARIO" in t:
        return "OPCION"
    return None


def _mep_para_fecha(fecha_iso: str | None, cache: dict[str, float | None] | None) -> float | None:
    """MEP de la fecha (último de Valuaciones.Dolar <= fin del día), memoizado por
    fecha en `cache` → una sola lookup por día aunque haya miles de docs. `cache`
    None desactiva el estampado (no toca el campo)."""
    if cache is None or not fecha_iso:
        return None
    if fecha_iso not in cache:
        cache[fecha_iso] = get_mep_for_date(fecha_iso)
    return cache[fecha_iso]


def _aplicar_enrich(doc: dict, maps: tuple[dict, dict] | None,
                    mep_cache: dict[str, float | None] | None = None) -> None:
    """Setea mercado/operacion/segmento(=nivel_1)/nivel_3/commodity/mep (moneda ya
    viene del normalizador). `commodity` se materializa SIEMPRE (incluso sin
    `maps`) porque sólo depende de campos del propio boleto. `mep` (dólar de la
    `concertacion`) se estampa si se pasa `mep_cache` — igual snapshot que
    NegocioMovimientos, para dolarizar la vista MOVIMIENTOS."""
    doc["commodity"] = clasificar_commodity(
        doc.get("tipo_operacion"), doc.get("denominacion"), doc.get("instrumento"),
    )
    # tipo_agro (FUTURO/OPCION) solo para boletos ya clasificados como agro.
    doc["tipo_agro"] = clasificar_tipo_agro(doc.get("tipo_operacion")) if doc["commodity"] else None
    # es_cierre: cierre de caución (la apertura ya cuenta el volumen → no suma).
    # Materializado para que /ops/* filtre por índice en vez de un `$not /Cierre/`
    # (regex negada = scan completo). Replicado en backfill_es_cierre_operaciones.
    doc["es_cierre"] = "CIERRE" in (doc.get("tipo_operacion") or "").upper()
    if mep_cache is not None:
        doc["mep"] = _mep_para_fecha(doc.get("concertacion"), mep_cache)
    if not maps:
        return
    cat, niveles = maps
    c = cat.get(doc.get("tipo_operacion") or "")
    doc["mercado"] = (c or {}).get("mercado", "")
    doc["operacion"] = (c or {}).get("operacion", "otro")
    nv = niveles.get(str(doc.get("cuenta") or "").strip(), {})
    doc["segmento"] = nv.get("n1", "")
    doc["nivel_3"] = nv.get("n3", "")


_COLS_INGEST = """
 (boleto, concertacion, id_cuenta, denominacion, moneda, mercado, operacion,
  segmento, nivel_3, commodity, tipo_agro, es_cierre, bruto, arancel, mep, cantidad,
  instrumento, tipo_operacion, condiciones, tasa, ingestado_en)
VALUES
 (%(boleto)s, %(concertacion)s, %(id_cuenta)s, %(denominacion)s, %(moneda)s,
  %(mercado)s, %(operacion)s, %(segmento)s, %(nivel_3)s, %(commodity)s,
  %(tipo_agro)s, %(es_cierre)s, %(bruto)s, %(arancel)s, %(mep)s, %(cantidad)s,
  %(instrumento)s, %(tipo_operacion)s, %(condiciones)s, %(tasa)s, %(ingestado_en)s)
"""

_SQL_INGEST = f"""
INSERT INTO operaciones{_COLS_INGEST}
ON CONFLICT (boleto) DO UPDATE SET
 concertacion=EXCLUDED.concertacion, id_cuenta=EXCLUDED.id_cuenta,
 denominacion=EXCLUDED.denominacion, moneda=EXCLUDED.moneda,
 mercado=EXCLUDED.mercado, operacion=EXCLUDED.operacion,
 segmento=EXCLUDED.segmento, nivel_3=EXCLUDED.nivel_3, commodity=EXCLUDED.commodity,
 tipo_agro=EXCLUDED.tipo_agro,
 es_cierre=EXCLUDED.es_cierre, bruto=EXCLUDED.bruto, arancel=EXCLUDED.arancel,
 mep=EXCLUDED.mep, cantidad=EXCLUDED.cantidad, instrumento=EXCLUDED.instrumento,
 tipo_operacion=EXCLUDED.tipo_operacion, condiciones=EXCLUDED.condiciones,
 tasa=COALESCE(EXCLUDED.tasa, operaciones.tasa),
 ingestado_en=EXCLUDED.ingestado_en
"""
# OJO: `etapa` NO se toca en el UPDATE — la setea jobs/fci_bilateral (FCI bilateral).
# `tasa` va con COALESCE: la ingesta de Aunesa NO la trae, y sin el COALESCE cada
# corrida borraría la que dejó jobs/ops_tasa_mav.

# Modo "solo faltantes": nunca toca un boleto ya cargado.
_SQL_INSERT_FALTANTES = f"""
INSERT INTO operaciones{_COLS_INGEST}
ON CONFLICT (boleto) DO NOTHING
"""


def _row_to_sql_params(d: dict) -> dict:
    """Doc canónico enriquecido → params para _SQL_INGEST. `cuenta` (id numérico)
    mapea a la columna `id_cuenta`; `concertacion` ISO → date."""
    conc = d.get("concertacion")
    return {
        "boleto":         d["boleto"],
        "concertacion":   date.fromisoformat(conc) if conc else None,
        "id_cuenta":      d.get("cuenta"),
        "denominacion":   d.get("denominacion"),
        "moneda":         d.get("moneda"),
        "mercado":        d.get("mercado"),
        "operacion":      d.get("operacion"),
        "segmento":       d.get("segmento"),
        "nivel_3":        d.get("nivel_3"),
        "commodity":      d.get("commodity"),
        "tipo_agro":      d.get("tipo_agro"),
        "es_cierre":      d.get("es_cierre"),
        "bruto":          d.get("bruto"),
        "arancel":        d.get("arancel"),
        "mep":            d.get("mep"),
        "cantidad":       d.get("cantidad"),
        "instrumento":    d.get("instrumento"),
        "tipo_operacion": d.get("tipo_operacion"),
        "condiciones":    d.get("condiciones"),
        "tasa":           d.get("tasa"),
        "ingestado_en":   d.get("ingestado_en"),
    }


def ingestar_filas_sql(
    rows: list[dict], enrich_maps: tuple[dict, dict] | None = None,
) -> dict:
    """= ingestar_filas pero escribe SQL `operaciones.operaciones` (upsert por
    boleto). Migración CashFlow.Operaciones → SQL: el writer escribe SQL directo.

    Reusa el normalizador y el enrich (puros). Dedup por boleto en el lote
    (última gana). NO toca `etapa` (la pone fci_bilateral)."""
    ahora = datetime.now(UTC)
    mep_cache: dict[str, float | None] = {}
    por_boleto: dict[str, dict] = {}
    sin_boleto = 0
    otc_excluidas = 0
    for row in rows:
        doc = normalizar_fila(row)
        if doc is None:
            sin_boleto += 1
            continue
        if es_otc_excluido(doc.get("tipo_operacion")):
            otc_excluidas += 1
            continue
        doc["ingestado_en"] = ahora
        _aplicar_enrich(doc, enrich_maps, mep_cache)
        por_boleto[doc["boleto"]] = doc

    if not por_boleto:
        return {"recibidas": len(rows), "sin_boleto": sin_boleto,
                "otc_excluidas": otc_excluidas, "upsertadas": 0, "modificadas": 0}

    params = [_row_to_sql_params(d) for d in por_boleto.values()]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(_SQL_INGEST, params)
        conn.commit()
    # SQL no distingue insert vs update barato en executemany → reportamos el
    # total escrito como `upsertadas` (la métrica fina no la consume nadie).
    return {
        "recibidas":     len(rows),
        "sin_boleto":    sin_boleto,
        "otc_excluidas": otc_excluidas,
        "upsertadas":    len(params),
        "modificadas":   0,
    }


def _preparar_docs(
    rows: list[dict], enrich_maps: tuple[dict, dict] | None,
) -> tuple[dict[str, dict], dict[str, int]]:
    """Normaliza + enriquece un lote y lo dedupea por boleto (última gana).
    Devuelve (boleto → doc, contadores de descarte)."""
    ahora = datetime.now(UTC)
    mep_cache: dict[str, float | None] = {}
    por_boleto: dict[str, dict] = {}
    cnt = {"sin_boleto": 0, "otc_excluidas": 0, "duplicadas_archivo": 0}
    for row in rows:
        doc = normalizar_fila(row)
        if doc is None:
            cnt["sin_boleto"] += 1
            continue
        if es_otc_excluido(doc.get("tipo_operacion")):
            cnt["otc_excluidas"] += 1
            continue
        if doc["boleto"] in por_boleto:
            cnt["duplicadas_archivo"] += 1
        doc["ingestado_en"] = ahora
        _aplicar_enrich(doc, enrich_maps, mep_cache)
        por_boleto[doc["boleto"]] = doc
    return por_boleto, cnt


def ingestar_faltantes_sql(
    rows: list[dict], enrich_maps: tuple[dict, dict] | None = None,
    commit: bool = False,
) -> dict:
    """Carga SOLO los boletos que todavía NO están en operaciones.operaciones.

    A diferencia de `ingestar_filas_sql` (upsert), acá un boleto ya cargado se
    ignora por completo: el Excel nunca pisa lo que vino de Aunesa. Pensado para
    tapar huecos del histórico.

    `commit=False` previsualiza (no escribe) y reporta qué entraría, incluyendo
    los boletos que quedarían sin `mercado` (tipo_operacion fuera del catálogo)
    o sin `segmento` (cuenta que no está en clientes.comitentes) — esos ENTRAN
    igual, pero no se ven bien filtrados en las vistas hasta corregir el maestro.
    """
    por_boleto, cnt = _preparar_docs(rows, enrich_maps)
    out: dict = {
        "recibidas": len(rows), **cnt,
        "validas": len(por_boleto), "ya_existen": 0, "nuevos": 0,
        "insertados": 0, "commit": commit,
    }
    if not por_boleto:
        return out

    boletos = list(por_boleto)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT boleto FROM operaciones WHERE boleto = ANY(%s)", (boletos,))
        existentes = {b for (b,) in cur.fetchall()}
        nuevos = [d for b, d in por_boleto.items() if b not in existentes]
        out["ya_existen"] = len(existentes)
        out["nuevos"] = len(nuevos)
        if not nuevos:
            return out

        fechas = sorted(d["concertacion"] for d in nuevos if d.get("concertacion"))
        sin_fecha = [d["boleto"] for d in nuevos if not d.get("concertacion")]
        out.update({
            "sin_concertacion": len(sin_fecha),
            "boletos_sin_fecha": sin_fecha[:100],
            "desde": fechas[0] if fechas else None,
            "hasta": fechas[-1] if fechas else None,
            "bruto_ars": sum(d["bruto"] or 0 for d in nuevos if d.get("moneda") == "ARS"),
            "bruto_usd": sum(d["bruto"] or 0 for d in nuevos if d.get("moneda") == "USD"),
            "arancel_total": sum(d["arancel"] or 0 for d in nuevos),
            "sin_mercado": sorted({d["tipo_operacion"] for d in nuevos
                                   if not d.get("mercado") and d.get("tipo_operacion")}),
            "cuentas_sin_segmento": sorted({d["cuenta"] for d in nuevos
                                            if not d.get("segmento") and d.get("cuenta")}),
            "muestra": [{k: d.get(k) for k in
                         ("boleto", "concertacion", "cuenta", "moneda", "mercado",
                          "bruto", "arancel", "tasa", "tipo_operacion")}
                        for d in nuevos[:20]],
        })
        if not commit:
            return out

        cur.executemany(_SQL_INSERT_FALTANTES, [_row_to_sql_params(d) for d in nuevos])
        conn.commit()
    out["insertados"] = len(nuevos)
    return out


_SQL_UPDATE_FECHA = """
UPDATE operaciones
   SET concertacion = %(concertacion)s
 WHERE boleto = %(boleto)s
   AND concertacion IS DISTINCT FROM %(concertacion)s
"""


def corregir_fechas_sql(rows: list[dict], commit: bool = False) -> dict:
    """Corrige SOLO `concertacion` usando el Excel como fuente de verdad.

    Existe porque una carga histórica vieja interpretó las fechas en formato
    americano (MM/DD) y dejó filas con día y mes dados vuelta. La mezcla no se
    puede arreglar con una regla ciega (hay filas ambiguas que SÍ están bien),
    así que el arreglo va fila por fila contra el archivo.

    NO toca ninguna otra columna: el UPDATE setea `concertacion` y nada más.
    Idempotente (`IS DISTINCT FROM`): re-correrlo no reescribe lo ya correcto.
    """
    por_boleto: dict[str, str] = {}
    cnt = {"sin_boleto": 0, "sin_fecha_archivo": 0, "duplicadas_archivo": 0}
    for row in rows:
        doc = normalizar_fila(row)
        if doc is None:
            cnt["sin_boleto"] += 1
            continue
        if not doc.get("concertacion"):
            cnt["sin_fecha_archivo"] += 1
            continue
        if doc["boleto"] in por_boleto:
            cnt["duplicadas_archivo"] += 1
        por_boleto[doc["boleto"]] = doc["concertacion"]

    out: dict = {
        "recibidas": len(rows), **cnt, "con_fecha": len(por_boleto),
        "iguales": 0, "swap": 0, "otra_dif": 0, "sin_fecha_base": 0,
        "no_existen": 0, "a_corregir": 0, "corregidos": 0, "commit": commit,
        "muestra": [],
    }
    if not por_boleto:
        return out

    boletos = list(por_boleto)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT boleto, concertacion FROM operaciones WHERE boleto = ANY(%s)",
            (boletos,))
        en_base = dict(cur.fetchall())
        out["no_existen"] = len(por_boleto) - len(en_base)

        cambios: list[dict] = []
        for boleto, nueva_iso in por_boleto.items():
            if boleto not in en_base:
                continue
            actual = en_base[boleto]
            actual_iso = actual.isoformat() if actual else None
            if actual_iso == nueva_iso:
                out["iguales"] += 1
                continue
            if actual is None:
                tipo = "sin fecha"
                out["sin_fecha_base"] += 1
            elif (actual.year == int(nueva_iso[:4])
                  and actual.day == int(nueva_iso[5:7])
                  and actual.month == int(nueva_iso[8:10])):
                tipo = "dia/mes al reves"
                out["swap"] += 1
            else:
                tipo = "otra diferencia"
                out["otra_dif"] += 1
            cambios.append({"boleto": boleto, "actual": actual_iso,
                            "nueva": nueva_iso, "tipo": tipo})

        out["a_corregir"] = len(cambios)
        cambios.sort(key=lambda c: c["boleto"])
        out["muestra"] = cambios[:30]
        if not commit or not cambios:
            return out

        cur.executemany(_SQL_UPDATE_FECHA,
                        [{"boleto": c["boleto"], "concertacion": c["nueva"]}
                         for c in cambios])
        conn.commit()
    out["corregidos"] = len(cambios)
    return out


# enriquecer()/stats() (operaban Mongo CashFlow.Operaciones) ELIMINADAS — decomiso 2026-06-29:
# la colección está DROPEADA y el path vivo es SQL (ingestar_filas_sql + _aplicar_enrich →
# operaciones.operaciones). Eran dead code sin llamadores (manager/operaciones y el cron usan SQL).
