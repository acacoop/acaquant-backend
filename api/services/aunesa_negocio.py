"""aunesa_negocio.py — service compartido para análisis del endpoint
/operaciones/consolidadosGenerales de Aunesa.

Centraliza todo lo aprendido durante el discovery:
  - Filtro pre-análisis (excluye OTC, USDL, Integración de garantías).
  - Parseo de `informacion` (boletos operativos, cauciones, solicitudes
    bilaterales FCI, acreencias).
  - Categorización en ~16 categorías operativas.
  - Inversión del signo (Aunesa devuelve perspectiva broker; consumers ven
    perspectiva cliente: + ingreso, - egreso).
  - Agrupación por comprobante con dedup específico:
      · solicitud_*_fci    → quedarse sólo con líneas estado=DIS.
      · FCI supermercado   → quedarse sólo con líneas dinero uso=GRAL.
      · acreencia          → priorizar líneas non-ARS (ARS suele ser
                              comisión/IIBB residual cuando hay USDC/USD).

Consumers:
  - api/routers/manager/aunesa.py — endpoint /manager/aunesa/explorar
    (admin, exploratorio en vivo).
  - jobs/negocio_movimientos.py — cron que persiste consolidados a
    CashFlow.NegocioMovimientos.
  - api/routers/operaciones.py — endpoint /operaciones/negocio (lee
    desde Mongo, no Aunesa directo).

Función pública principal:
  fetch_y_consolidar(fecha: date, tipos_cuenta: str = "Comitente") -> dict
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from typing import Any

import requests

import config

logger = logging.getLogger("api.services.aunesa_negocio")

AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
OPS_URL = "https://aca.aunesa.com/Irmo/api/operaciones/consolidadosGenerales"

PALABRAS_CLAVE_ACTUALES = ("deposito", "transferencia", "extraccion")
EXCLUIR_SUBSTRINGS = ("otc", "usdl", "integracion de garantias")
ACREENCIA_KEYS = ("partial redemption", "interest payment", "cash dividend")
MONEDAS = ("ARS", "USD", "USDL", "USDC")

SOLICITUD_FCI_CATS = ("solicitud_suscripcion_fci", "solicitud_rescate_fci")

# ─── Regex ──────────────────────────────────────────────────────────────────

PATTERN_BOLETO = re.compile(
    r"^(?P<op>.+?)\s+"
    r"\[(?P<ticker>[^\]]+)\]\s+"
    r"(?P<cantidad>[\d.,]+)@(?P<precio>[\d.,]+)\s+"
    r"\((?P<moneda>\w+)\s+(?P<plazo>[^)]+)\)"
)

PATTERN_CAUCION = re.compile(
    r"^Cauci[oó]n\s+(?P<rol>colocadora|tomadora)\s+"
    r"(?P<mon1>\w+)\s+(?P<monto>[\d.,]+)@(?P<tasa>[\d.,]+)%\s+"
    r"\((?P<moneda>\w+)\s+(?P<dias>\d+)\s+d[ií]as?\)\s+"
    r"\((?P<fase>Apertura|Cierre)\)",
    re.IGNORECASE,
)

PATTERN_SOLICITUD_FCI = re.compile(
    r"^Solicitud\s+de\s+(?P<accion>suscripci[oó]n|rescate)\s+de\s+FCI\s*-?\s*"
    r"\[(?P<id>\d+)\]\s*(?P<ticker>[\w./-]+)",
    re.IGNORECASE,
)

PATTERN_ACREENCIA_TICKER = re.compile(r"\bs/(?P<ticker>[\w./-]+)", re.IGNORECASE)


# ─── Helpers ────────────────────────────────────────────────────────────────


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
    try:
        return float(str(x).replace(".", "").replace(",", "."))
    except (ValueError, TypeError):
        return 0.0


def parse_informacion(s: str) -> dict[str, Any] | None:
    """Parsea el campo `informacion` con cascada de patrones:
       boleto → caución → solicitud FCI bilateral → acreencia.
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
            "precio":   _parse_num_ar(g["tasa"]),
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
            "cantidad": None,
            "precio":   None,
            "moneda":   "ARS",
            "plazo":    "Contado Inmediato",
            "fase":     None,
        }

    norm = _normalizar(s)
    if any(k in norm for k in ACREENCIA_KEYS):
        op = (
            "Interest payment" if "interest payment" in norm
            else "Cash dividend" if "cash dividend" in norm
            else "Partial redemption"
        )
        ticker = None
        m2 = PATTERN_ACREENCIA_TICKER.search(s)
        if m2:
            ticker = m2.group("ticker")
        return {
            "op":       op,
            "ticker":   ticker,
            "cantidad": None,
            "precio":   None,
            "moneda":   None,
            "plazo":    None,
            "fase":     None,
        }

    return None


def categorizar(informacion: str, parsed: dict | None) -> str:
    norm = _normalizar(informacion)

    if "caucion" in norm:
        rol = "tom" if "tomadora" in norm else "col" if "colocadora" in norm else None
        fase = "ap" if "apertura" in norm else "ci" if "cierre" in norm else None
        if rol and fase:
            return f"caucion_{rol}_{fase}"
        return "caucion_otro"

    if any(k in norm for k in ACREENCIA_KEYS):
        return "acreencia"

    if "solicitud de suscripcion" in norm:
        return "solicitud_suscripcion_fci"
    if "solicitud de rescate" in norm:
        return "solicitud_rescate_fci"

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

    if parsed:
        # _normalizar saca tildes + lowerea. Cubre "Licitación" / "Licitacion"
        # / "LICITACIÓN" / etc. de manera uniforme.
        op_norm = _normalizar(parsed["op"])
        # Compra primaria por licitación (BYMA / Tesoro): es funcionalmente
        # idéntica a una compra secundaria — entra en cost-basis igual.
        if op_norm.startswith("compra") or op_norm.startswith("licitaci"):
            return "compra"
        if op_norm.startswith("venta"):
            return "venta"
        if "suscripci" in op_norm:
            return "suscripcion_fci"
        if "rescate" in op_norm:
            return "rescate_fci"

    return "otro"


def es_capturado_filtro_actual(informacion: str) -> bool:
    norm = _normalizar(informacion)
    return any(p in norm for p in PALABRAS_CLAVE_ACTUALES)


def _excluir(mov: dict) -> bool:
    info_norm = _normalizar(mov.get("informacion") or "")
    cuenta_norm = _normalizar(mov.get("cuenta") or "")
    return any(s in info_norm or s in cuenta_norm for s in EXCLUIR_SUBSTRINGS)


def _enriquecer(mov: dict) -> dict:
    out = dict(mov)
    informacion = mov.get("informacion") or ""
    parsed = parse_informacion(informacion)
    out["_parsed"] = parsed
    out["_categoria"] = categorizar(informacion, parsed)
    out["_capturado"] = es_capturado_filtro_actual(informacion)
    raw_total = mov.get("total")
    try:
        out["_total_cliente"] = -float(raw_total) if raw_total is not None else None
    except (TypeError, ValueError):
        out["_total_cliente"] = None
    return out


def _es_linea_dinero(mov: dict) -> bool:
    return str(mov.get("unidad") or "").strip().upper() in MONEDAS


def agrupar_boletos(movimientos: list[dict]) -> list[dict]:
    """Agrupa por `comprobante` con dedup específico por categoría.
    Devuelve boletos consolidados con campos:
      comprobante, cuenta, fecha, informacion, categoria, capturado, op,
      ticker, cantidad, precio, importe, moneda, plazo, lugar, estado,
      n_lineas, lineas (raw).
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
        primer = lineas[0]
        parsed = primer.get("_parsed")
        categoria = primer.get("_categoria")

        titulos = [m for m in lineas if not _es_linea_dinero(m)]
        dineros = [m for m in lineas if _es_linea_dinero(m)]

        # Solicitudes FCI bilaterales: dedup DIS (tanto en títulos como dineros).
        if categoria in SOLICITUD_FCI_CATS:
            titulos_dis = [m for m in titulos if str(m.get("estado") or "").upper() == "DIS"]
            if titulos_dis:
                titulos = titulos_dis
            dineros_dis = [m for m in dineros if str(m.get("estado") or "").upper() == "DIS"]
            if dineros_dis:
                dineros = dineros_dis
        elif categoria == "acreencia" and len(dineros) > 1:
            no_ars = [m for m in dineros if str(m.get("unidad") or "").upper() != "ARS"]
            if no_ars:
                dineros = no_ars
        elif len(dineros) > 1:
            # Cuando un boleto en USD trae varias líneas de dinero (cash en USD
            # + comisión en ARS), nuestra suma "todo junto" produce un importe
            # mezclado en monedas distintas. Priorizamos la línea cuya unidad
            # matchea `_parsed.moneda` (la moneda del boleto, parseada del
            # texto "Compra [...] (USD 24hs)"). Si no hay match, fallback a
            # filtrar por uso=GRAL (la lógica vieja).
            parsed_moneda = (parsed or {}).get("moneda")
            if parsed_moneda:
                match_mon = [
                    m for m in dineros
                    if str(m.get("unidad") or "").strip().upper()
                       == parsed_moneda.upper()
                ]
                if match_mon:
                    dineros = match_mon
            if len(dineros) > 1:
                con_gral = [m for m in dineros if str(m.get("uso") or "").upper() == "GRAL"]
                if con_gral:
                    dineros = con_gral

        cantidad_titulo = sum(m.get("_total_cliente") or 0 for m in titulos)
        importe_dinero = sum(m.get("_total_cliente") or 0 for m in dineros)

        # TRD = op genérica de trading (similar a licitación, pero sin texto
        # "Compra"/"Venta" en `informacion`). Se categoriza por signo del
        # importe ya pesificado al cliente: importe < 0 (pagamos) = compra,
        # importe > 0 (recibimos) = venta. Sin esto cae en "otro" y el
        # motor PnL la ignora — bug de YFCOO comprado por TRD.
        if (
            categoria == "otro"
            and parsed
            and _normalizar(parsed.get("op") or "") == "trd"
        ):
            if importe_dinero < 0:
                categoria = "compra"
            elif importe_dinero > 0:
                categoria = "venta"

        ticker = (parsed or {}).get("ticker")
        if not ticker and titulos:
            unidad_str = str(titulos[0].get("unidad") or "")
            tm = re.match(r"^\[\d+\]\s*([\w./-]+)", unidad_str)
            if tm:
                ticker = tm.group(1)

        # La moneda del importe puede no coincidir con la del parsed cuando
        # priorizamos non-ARS en acreencias.
        moneda_importe = None
        if dineros:
            moneda_importe = str(dineros[0].get("unidad") or "").strip().upper()

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
            "moneda":           moneda_importe or (parsed or {}).get("moneda"),
            "plazo":            (parsed or {}).get("plazo"),
            "lugar":            primer.get("lugar"),
            "estado":           primer.get("estado"),
            "n_lineas":         len(lineas),
            "lineas":           lineas,
        })

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


# ─── Función pública principal ──────────────────────────────────────────────


def fetch_y_consolidar(
    fecha: date,
    tipos_cuenta: str = "Comitente",
    timeout_s: int = 180,
    retries: int = 3,
) -> dict[str, Any]:
    """Pega a Aunesa para `fecha`, aplica filtros + parseo + agrupación
    y devuelve dict con meta + tipos + categorias + movimientos + boletos.

    Raises:
        requests.HTTPError si la API responde != 200.
        requests.exceptions.Timeout tras `retries` intentos.
        RuntimeError si la respuesta no tiene shape esperada.
    """
    dia_str = fecha.strftime("%d/%m/%Y")
    headers = _autenticar()

    params = {
        "tiposCuenta":      tipos_cuenta,
        "concertacionDesde": dia_str,
        "concertacionHasta": dia_str,
    }

    resp = None
    last_err: Exception | None = None
    for intento in range(1, retries + 1):
        try:
            resp = requests.get(OPS_URL, params=params, headers=headers, timeout=timeout_s)
            break
        except requests.exceptions.Timeout as e:
            last_err = e
            logger.warning("aunesa fetch timeout intento %d/%d", intento, retries)
            continue
        except Exception as e:
            last_err = e
            break
    if resp is None:
        raise requests.exceptions.Timeout(f"timeout tras {retries} intentos: {last_err}")

    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise RuntimeError(f"shape inesperada: {type(data).__name__}")

    raw_total = len(data)
    data = [r for r in data if not _excluir(r)]
    excluidos_n = raw_total - len(data)

    movimientos = [_enriquecer(r) for r in data]
    boletos = agrupar_boletos(movimientos)

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
            "capturado": es_capturado_filtro_actual(tipo),
        }
        for tipo, n in counter_info.most_common()
    ]
    categorias = [
        {"categoria": c, "count": n}
        for c, n in counter_cat.most_common()
    ]

    return {
        "meta": {
            "fecha":              fecha.isoformat(),
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
