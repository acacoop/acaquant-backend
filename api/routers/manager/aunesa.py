"""Manager · Aunesa — exploradores de los endpoints de Aunesa.

Hoy:
  GET /api/manager/aunesa/explorar?fecha=YYYY-MM-DD
    → pega a /operaciones/consolidadosGenerales y devuelve:
       - movimientos (raw, con campos parseados + categoría + signo cliente).
       - boletos (consolidados por comprobante, listo para vista de negocio).
       - meta con stats por categoría.

El endpoint reusa la lógica de parseo + categorización que la vista futura
de /operaciones/negocio va a usar también — todo centralizado acá.

Reglas que aplica (basadas en hallazgos del exploratorio):
1. Excluye movimientos OTC de la cuenta o informacion (no aplican a mesa AR).
2. Parsea el campo `informacion` con regex que cubre boletos + FCI super.
3. Categoriza cada movimiento: compra/venta/suscripcion_fci/rescate_fci/
   solicitud_*_fci/acreencia/deposito/extraccion/transferencia/comision/otro.
4. Invierte el signo del total (Aunesa usa perspectiva broker, lo damos
   en perspectiva cliente: + ingreso, - egreso).
5. Agrupa por `comprobante`. Para boletos FCI supermercado deduplica las
   líneas duplicadas en moneda (filter uso=="GRAL").
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Query

import config

router = APIRouter()
logger = logging.getLogger("api.manager.aunesa")

AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
OPS_URL = "https://aca.aunesa.com/Irmo/api/operaciones/consolidadosGenerales"

PALABRAS_CLAVE_ACTUALES = ("deposito", "transferencia", "extraccion")
EXCLUIR_SUBSTRINGS = ("otc",)

# Acreencias = ingresos por tenencia (cupón, amortización, dividendo).
ACREENCIA_KEYS = ("partial redemption", "interest payment", "cash dividend")

MONEDAS = ("ARS", "USD", "USDL")

# Patrón regex para `informacion` de boletos operativos:
#   <OP> [<TICKER>] <CANTIDAD>@<PRECIO> (<MONEDA> <PLAZO>)
# Cubre Compra/Venta y Suscripción/Rescate provisional/final.
PATTERN_BOLETO = re.compile(
    r"^(?P<op>.+?)\s+"
    r"\[(?P<ticker>[^\]]+)\]\s+"
    r"(?P<cantidad>[\d.,]+)@(?P<precio>[\d.,]+)\s+"
    r"\((?P<moneda>\w+)\s+(?P<plazo>[^)]+)\)"
)

# Patrón para cauciones:
#   Caución <ROL> <MONEDA> <MONTO>@<TASA>% (<MONEDA> <DIAS> días) (<FASE>)
#   ej. "Caución colocadora ARS 41.945.608,00@23% (ARS 1 días) (Apertura)"
PATTERN_CAUCION = re.compile(
    r"^Cauci[oó]n\s+(?P<rol>colocadora|tomadora)\s+"
    r"(?P<mon1>\w+)\s+(?P<monto>[\d.,]+)@(?P<tasa>[\d.,]+)%\s+"
    r"\((?P<moneda>\w+)\s+(?P<dias>\d+)\s+d[ií]as?\)\s+"
    r"\((?P<fase>Apertura|Cierre)\)",
    re.IGNORECASE,
)

# Patrón para solicitudes bilaterales FCI:
#   Solicitud de <ACCIÓN> de FCI - [<ID>] <TICKER>
#   ej. "Solicitud de suscripción de FCI - [5567] CAFCI1671-5567"
PATTERN_SOLICITUD_FCI = re.compile(
    r"^Solicitud\s+de\s+(?P<accion>suscripci[oó]n|rescate)\s+de\s+FCI\s*-?\s*"
    r"\[(?P<id>\d+)\]\s*(?P<ticker>[\w./-]+)",
    re.IGNORECASE,
)

# Categorías que en realidad son operaciones bilaterales FCI (todas las
# líneas vienen como ARS, distinguidas por estado DIF/DIS).
SOLICITUD_FCI_CATS = ("solicitud_suscripcion_fci", "solicitud_rescate_fci")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _autenticar() -> dict[str, str]:
    resp = requests.post(
        AUTH_URL,
        json={
            "clientId": config.AUNESA_CLIENT_ID,
            "username": config.AUNESA_USERNAME,
            "password": config.AUNESA_PASSWORD,
        },
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json().get("token")
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def _normalizar(s: str) -> str:
    return (
        unicodedata.normalize("NFD", str(s))
        .encode("ascii", "ignore")
        .decode("utf-8")
        .lower()
    )


def _parse_num_ar(x: str) -> float:
    """Convierte número en formato AR (1.234,56) a float (1234.56)."""
    try:
        return float(str(x).replace(".", "").replace(",", "."))
    except (ValueError, TypeError):
        return 0.0


def _parse_informacion(s: str) -> dict[str, Any] | None:
    """Parsea el campo `informacion`. Intenta:
       1. patrón boleto (Compra/Venta/Susc/Rescate).
       2. patrón caución (Caución colocadora/tomadora ... Apertura/Cierre).
       Devuelve dict normalizado con `op`, `ticker`, `cantidad`, `precio`,
       `moneda`, `plazo`. Para cauciones agrega `fase` (Apertura/Cierre).
       None si no matchea ninguno.
    """
    if not s:
        return None

    m = PATTERN_BOLETO.match(s)
    if m:
        g = m.groupdict()
        return {
            "op":       g["op"].strip(),
            "ticker":   g["ticker"],
            "cantidad": _parse_num_ar(g["cantidad"]),
            "precio":   _parse_num_ar(g["precio"]),
            "moneda":   g["moneda"],
            "plazo":    g["plazo"].strip(),
            "fase":     None,
        }

    m = PATTERN_CAUCION.match(s)
    if m:
        g = m.groupdict()
        rol = g["rol"].lower()
        fase = g["fase"].capitalize()
        return {
            "op":       f"Caución {rol} · {fase}",
            "ticker":   None,
            "cantidad": _parse_num_ar(g["monto"]),
            "precio":   _parse_num_ar(g["tasa"]),    # acá precio = tasa (%)
            "moneda":   g["moneda"],
            "plazo":    f"{g['dias']} días",
            "fase":     fase,
        }

    m = PATTERN_SOLICITUD_FCI.match(s)
    if m:
        g = m.groupdict()
        accion = "suscripción" if "suscripci" in g["accion"].lower() else "rescate"
        return {
            "op":       f"Solicitud {accion} FCI",
            "ticker":   g["ticker"],
            "cantidad": None,    # se resuelve en _agrupar_boletos desde las líneas
            "precio":   None,
            "moneda":   "ARS",   # bilaterales siempre ARS por default
            "plazo":    "Contado Inmediato",
            "fase":     None,
        }

    return None


def _categorizar(informacion: str, parsed: dict | None) -> str:
    """Devuelve la categoría operativa del movimiento."""
    norm = _normalizar(informacion)

    # Cauciones (4 sub-categorías por rol × fase).
    if "caucion" in norm:
        rol = "tom" if "tomadora" in norm else "col" if "colocadora" in norm else None
        fase = "ap" if "apertura" in norm else "ci" if "cierre" in norm else None
        if rol and fase:
            return f"caucion_{rol}_{fase}"
        return "caucion_otro"

    # Acreencias por palabra clave (no parsean con regex de boleto).
    if any(k in norm for k in ACREENCIA_KEYS):
        return "acreencia"

    # FCI bilateral (palabra "Solicitud").
    if "solicitud de suscripcion" in norm:
        return "solicitud_suscripcion_fci"
    if "solicitud de rescate" in norm:
        return "solicitud_rescate_fci"

    # Categorías por palabra clave del filtro original.
    if "deposito" in norm:
        return "deposito"
    if "transferencia" in norm:
        return "transferencia"
    if "extraccion" in norm:
        return "extraccion"
    if "comision" in norm:
        return "comision"
    if any(k in norm for k in ("impuesto", "iibb", "deb/cred", "creb/deb")):
        return "impuesto"

    # Si parseó, deducir por la op.
    if parsed:
        op_low = parsed["op"].lower()
        if op_low.startswith("compra"):
            return "compra"
        if op_low.startswith("venta"):
            return "venta"
        if "suscripci" in op_low:
            return "suscripcion_fci"
        if "rescate" in op_low:
            return "rescate_fci"

    return "otro"


def _es_capturado_actual(informacion: str) -> bool:
    """¿Pasa el filtro actual de jobs/cashflow.py?"""
    norm = _normalizar(informacion)
    return any(p in norm for p in PALABRAS_CLAVE_ACTUALES)


def _excluir(mov: dict) -> bool:
    info_norm = _normalizar(mov.get("informacion") or "")
    cuenta_norm = _normalizar(mov.get("cuenta") or "")
    return any(s in info_norm or s in cuenta_norm for s in EXCLUIR_SUBSTRINGS)


def _enriquecer(mov: dict) -> dict:
    """Devuelve copia del mov con campos derivados:
       _parsed, _categoria, _capturado, _total_cliente.
    """
    out = dict(mov)
    informacion = mov.get("informacion") or ""
    parsed = _parse_informacion(informacion)
    out["_parsed"] = parsed
    out["_categoria"] = _categorizar(informacion, parsed)
    out["_capturado"] = _es_capturado_actual(informacion)
    raw_total = mov.get("total")
    try:
        out["_total_cliente"] = -float(raw_total) if raw_total is not None else None
    except (TypeError, ValueError):
        out["_total_cliente"] = None
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Agrupación por comprobante (vista de negocio)
# ─────────────────────────────────────────────────────────────────────────────


def _es_linea_dinero(mov: dict) -> bool:
    """¿Esta línea representa el lado dinero (no el título)?"""
    return str(mov.get("unidad") or "").strip().upper() in MONEDAS


def _agrupar_boletos(movimientos: list[dict]) -> list[dict]:
    """Agrupa por `comprobante` y consolida la operación lógica.

    Para cada comprobante:
      - Identifica la(s) línea(s) título (unidad != moneda).
      - Identifica la(s) línea(s) dinero (unidad in MONEDAS).
      - Para FCI supermercado: deduplica líneas dinero filtrando uso=="GRAL"
        (sólo si hay múltiples líneas dinero — si hay una sola la respeta).
      - Toma el parseado del primer mov (todos los del mismo boleto comparten
        `informacion`).
      - Calcula totales: cantidad_titulo, importe_dinero, ambos en perspectiva
        cliente (signo invertido).

    Devuelve lista de boletos consolidados, ordenada por categoría → cuenta →
    comprobante.
    """
    by_comp: dict[str, list[dict]] = defaultdict(list)
    sin_comprobante: list[dict] = []
    for m in movimientos:
        comp = m.get("comprobante")
        if comp:
            by_comp[comp].append(m)
        else:
            sin_comprobante.append(m)

    out: list[dict] = []

    for comp, lineas in by_comp.items():
        # Tomar metadata del primer mov para conocer la categoría.
        primer = lineas[0]
        parsed = primer.get("_parsed")
        categoria = primer.get("_categoria")

        titulos = [m for m in lineas if not _es_linea_dinero(m)]
        dineros = [m for m in lineas if _es_linea_dinero(m)]

        # ── Solicitudes FCI bilaterales: todas las líneas vienen ARS,
        # con estados DIF (asset, contable) y DIS (plata real).
        # Quedarnos sólo con DIS — esa es la plata efectiva del cliente.
        if categoria in SOLICITUD_FCI_CATS and len(dineros) > 1:
            dis = [m for m in dineros if str(m.get("estado") or "").upper() == "DIS"]
            if dis:
                dineros = dis

        # ── FCI supermercado: si hay múltiples dineros, conservar sólo uso=="GRAL".
        # (no aplica a bilaterales, ya filtrado arriba)
        elif len(dineros) > 1:
            con_gral = [m for m in dineros if str(m.get("uso") or "").upper() == "GRAL"]
            if con_gral:
                dineros = con_gral

        # Totales en perspectiva cliente (suma neta).
        cantidad_titulo = sum(m.get("_total_cliente") or 0 for m in titulos)
        importe_dinero = sum(m.get("_total_cliente") or 0 for m in dineros)

        # Ticker resuelto: del parseado o de la unidad de la línea título.
        ticker = (parsed or {}).get("ticker")
        if not ticker and titulos:
            unidad_str = str(titulos[0].get("unidad") or "")
            # Formato "[ID] TICKER - desc" o solo "TICKER"
            tm = re.match(r"^\[\d+\]\s*([\w./-]+)", unidad_str)
            if tm:
                ticker = tm.group(1)

        out.append({
            "comprobante":      comp,
            "cuenta":           primer.get("cuenta"),
            "fecha":            primer.get("fecha"),
            "informacion":      primer.get("informacion"),
            "categoria":        categoria,
            "capturado":        primer.get("_capturado"),
            "op":               (parsed or {}).get("op"),
            "ticker":           ticker,
            "cantidad":         cantidad_titulo if titulos else None,
            "precio":           (parsed or {}).get("precio"),
            "importe":          importe_dinero if dineros else None,
            "moneda":           (parsed or {}).get("moneda"),
            "plazo":            (parsed or {}).get("plazo"),
            "lugar":            primer.get("lugar"),
            "estado":           primer.get("estado"),
            "n_lineas":         len(lineas),
            "lineas":           lineas,
        })

    # Movimientos sin comprobante (extraño, los igual los devolvemos como
    # boleto de 1 línea para que aparezcan en la vista).
    for m in sin_comprobante:
        out.append({
            "comprobante":      None,
            "cuenta":           m.get("cuenta"),
            "fecha":            m.get("fecha"),
            "informacion":      m.get("informacion"),
            "categoria":        m.get("_categoria"),
            "capturado":        m.get("_capturado"),
            "op":               (m.get("_parsed") or {}).get("op"),
            "ticker":           (m.get("_parsed") or {}).get("ticker"),
            "cantidad":         None,
            "precio":           (m.get("_parsed") or {}).get("precio"),
            "importe":          m.get("_total_cliente"),
            "moneda":           (m.get("_parsed") or {}).get("moneda") or m.get("unidad"),
            "plazo":            (m.get("_parsed") or {}).get("plazo"),
            "lugar":            m.get("lugar"),
            "estado":           m.get("estado"),
            "n_lineas":         1,
            "lineas":           [m],
        })

    out.sort(key=lambda b: (
        b.get("categoria") or "z",
        str(b.get("cuenta") or ""),
        str(b.get("comprobante") or ""),
    ))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/aunesa/explorar")
def aunesa_explorar(
    fecha: str | None = Query(None, description="YYYY-MM-DD; default: hoy ART"),
    tipos_cuenta: str = Query(
        "Comitente",
        description="tipoCuenta param de Aunesa (default Comitente, igual que el cron actual)",
    ),
) -> dict[str, Any]:
    """Discovery + categorización del endpoint /operaciones/consolidadosGenerales.

    Devuelve:
      - meta: fecha, totales, % por categoría, palabras clave del filtro actual.
      - tipos: distribución por valor de `informacion` (raw).
      - categorias: distribución por categoría (compra/venta/suscripcion_fci/...).
      - movimientos: TODOS los movimientos (raw + flags + parseado).
      - boletos: agrupado por comprobante, consolidado y deduplicado (FCI super).
    """
    if fecha:
        try:
            d = datetime.strptime(fecha, "%Y-%m-%d").date()
        except ValueError as e:
            raise HTTPException(status_code=400,
                                detail=f"fecha mal formada: {fecha} (esperado YYYY-MM-DD)") from e
    else:
        d = (datetime.now(UTC) - timedelta(hours=3)).date()

    dia_str = d.strftime("%d/%m/%Y")

    try:
        headers = _autenticar()
    except Exception as e:
        logger.exception("aunesa explorar auth failed")
        raise HTTPException(status_code=502,
                            detail=f"falla de auth con Aunesa: {e}") from e

    params = {
        "tiposCuenta":      tipos_cuenta,
        "concertacionDesde": dia_str,
        "concertacionHasta": dia_str,
    }
    resp = None
    last_err: Exception | None = None
    for intento in range(1, 4):
        try:
            resp = requests.get(OPS_URL, params=params, headers=headers, timeout=180)
            break
        except requests.exceptions.Timeout as e:
            last_err = e
            logger.warning("timeout aunesa explorar intento %d/3", intento)
            continue
        except Exception as e:
            last_err = e
            break
    if resp is None:
        raise HTTPException(status_code=504, detail=f"timeout tras 3 intentos: {last_err}")
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"Aunesa: {resp.text[:300]}")

    data = resp.json()
    if not isinstance(data, list):
        raise HTTPException(status_code=502,
                            detail=f"Aunesa devolvió shape inesperado: {type(data).__name__}")

    # Filtro pre-análisis.
    raw_total = len(data)
    data = [r for r in data if not _excluir(r)]
    excluidos_n = raw_total - len(data)

    # Enriquecer cada movimiento.
    movimientos = [_enriquecer(r) for r in data]

    # Agrupar boletos.
    boletos = _agrupar_boletos(movimientos)

    # Distribuciones.
    counter_info: Counter[str] = Counter()
    counter_cat: Counter[str] = Counter()
    capturados_n = 0
    descartados_n = 0
    for m in movimientos:
        counter_info[m.get("informacion") or "(sin informacion)"] += 1
        counter_cat[m.get("_categoria") or "otro"] += 1
        if m.get("_capturado"):
            capturados_n += 1
        else:
            descartados_n += 1

    total = len(movimientos)
    pct_cap = round((capturados_n / total) * 100, 1) if total else 0.0
    pct_desc = round((descartados_n / total) * 100, 1) if total else 0.0

    tipos = [
        {
            "informacion": tipo,
            "count": n,
            "capturado": _es_capturado_actual(tipo),
        }
        for tipo, n in counter_info.most_common()
    ]
    categorias = [
        {"categoria": c, "count": n}
        for c, n in counter_cat.most_common()
    ]

    return {
        "meta": {
            "fecha":              d.isoformat(),
            "tiposCuenta":        tipos_cuenta,
            "raw_total":          raw_total,
            "excluidos":          excluidos_n,
            "total":              total,
            "capturados":         capturados_n,
            "descartados":        descartados_n,
            "pct_capturados":     pct_cap,
            "pct_descartados":    pct_desc,
            "n_boletos":          len(boletos),
            "palabras_clave_actuales": list(PALABRAS_CLAVE_ACTUALES),
            "excluir_substrings": list(EXCLUIR_SUBSTRINGS),
        },
        "tipos":       tipos,
        "categorias":  categorias,
        "movimientos": movimientos,
        "boletos":     boletos,
    }
