"""partner_api/odata.py — servicio OData v2 sobre ACAPortfolio.Cartera.

Capa OData ADITIVA: la REST (/v1/*) sigue intacta. SAP Datasphere (y otras
herramientas SAP) no consumen REST nativo pero SÍ OData → esto expone los MISMOS
datos de portfolio como un servicio OData v2 estándar.

Endpoints (prefix /odata):
    GET /odata/                 → service document (lista las entidades)         [abierto]
    GET /odata/$metadata        → EDMX (esquema de la entidad Portfolio)         [abierto]
    GET /odata/Portfolio        → las posiciones (datos)                         [Basic Auth]
    GET /odata/Portfolio/$count → conteo de filas (plain text)                   [Basic Auth]

Auth de los DATOS: HTTP Basic (usuario/password de ACAPortfolio.ApiUsers — los
MISMOS que ya usa el proveedor). El service document + $metadata quedan abiertos
(es solo el esquema, sin datos) para que la herramienta descubra siempre.

OData v2 a propósito (máxima compatibilidad SAP). Números tipados Edm.Double → en
el JSON salen como número (evita el quirk de Edm.Decimal-como-string de v2).
"""
from __future__ import annotations

import base64
import binascii
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from partner_api import store
from partner_api.ratelimit import limiter
from partner_api.security import verify_password

logger = logging.getLogger("partner_api.odata")
router = APIRouter(prefix="/odata", tags=["odata"])

NS = "Acaquant"        # namespace del esquema
ENTITY = "Portfolio"   # entidad / entity set

# Propiedades de la entidad: nombre (= campo Mongo) → tipo Edm. La key `ID` es
# sintética (fecha|id_cuenta|unidad) porque la posición no tiene id propio.
_PROPS: list[tuple[str, str]] = [
    ("fecha", "Edm.String"),
    ("id_cuenta", "Edm.String"),
    ("cuenta", "Edm.String"),
    ("unidad", "Edm.String"),
    ("cantidad", "Edm.Double"),
    ("precio", "Edm.Double"),
    ("valuacion", "Edm.Double"),
]
# Campos sobre los que se acepta un $filter `eq` (string).
_FILTERABLE = {"fecha", "id_cuenta", "unidad", "cuenta"}
_MAX_ROWS = 100_000    # tope de seguridad (se loguea si trunca)
_ODATA_HEADERS = {"DataServiceVersion": "2.0"}


# ── Auth: HTTP Basic contra ApiUsers ─────────────────────────────────────────
def _basic_auth(request: Request) -> str:
    """Valida HTTP Basic (usuario/password de ApiUsers). 401 con WWW-Authenticate
    si falta o es inválido → la herramienta reintenta con credenciales."""
    unauth = {"WWW-Authenticate": 'Basic realm="odata"'}
    hdr = request.headers.get("authorization", "")
    if not hdr.lower().startswith("basic "):
        raise HTTPException(401, "Falta autenticación Basic", headers=unauth)
    try:
        username, password = base64.b64decode(hdr[6:]).decode("utf-8").split(":", 1)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise HTTPException(401, "Auth Basic malformado", headers=unauth) from None
    username = username.strip()
    user = store.find_user(username)
    ok = verify_password(password, user["password_hash"]) if user else False
    if not user or not ok or not user.get("enabled", False):
        raise HTTPException(401, "usuario o password inválidos", headers=unauth)
    return username


def _key(d: dict) -> str:
    return f"{d.get('fecha')}|{d.get('id_cuenta')}|{d.get('unidad')}"


# ── $filter mínimo: `campo eq 'valor'` unidos por `and` ─────────────────────
_EQ_RE = re.compile(r"^\s*(\w+)\s+eq\s+'([^']*)'\s*$")


def _parse_filter(expr: str | None) -> dict:
    """$filter simple → filtro Mongo. Solo `campo eq 'valor'` (string) unidos por
    `and`. Cláusulas no soportadas se IGNORAN (no rompe la conexión)."""
    if not expr:
        return {}
    mongo: dict = {}
    for clause in re.split(r"\s+and\s+", expr, flags=re.IGNORECASE):
        m = _EQ_RE.match(clause)
        if not m:
            logger.info("odata $filter: cláusula ignorada (no soportada): %r", clause)
            continue
        field, val = m.group(1), m.group(2)
        if field == "ID":
            parts = val.split("|")
            if len(parts) == 3:
                mongo.update({"fecha": parts[0], "id_cuenta": parts[1], "unidad": parts[2]})
        elif field in _FILTERABLE:
            mongo[field] = val
    return mongo


# ── Metadata / service document (abiertos: solo esquema, sin datos) ──────────
@router.get("/")
def service_document() -> Response:
    """Service document OData v2 (XML) — lista los entity sets disponibles."""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<service xml:base="/odata/" xmlns="http://www.w3.org/2007/app" '
        'xmlns:atom="http://www.w3.org/2005/Atom">'
        '<workspace><atom:title>Default</atom:title>'
        f'<collection href="{ENTITY}"><atom:title>{ENTITY}</atom:title></collection>'
        '</workspace></service>'
    )
    return Response(xml, media_type="application/xml", headers=_ODATA_HEADERS)


@router.get("/$metadata")
def metadata() -> Response:
    """EDMX (esquema) OData v2 de la entidad Portfolio."""
    props = "".join(f'<Property Name="{n}" Type="{t}"/>' for n, t in _PROPS)
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<edmx:Edmx Version="1.0" xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">'
        '<edmx:DataServices m:DataServiceVersion="2.0" '
        'xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">'
        f'<Schema Namespace="{NS}" xmlns="http://schemas.microsoft.com/ado/2008/09/edm">'
        f'<EntityType Name="{ENTITY}"><Key><PropertyRef Name="ID"/></Key>'
        '<Property Name="ID" Type="Edm.String" Nullable="false"/>'
        f'{props}</EntityType>'
        '<EntityContainer Name="Container" m:IsDefaultEntityContainer="true">'
        f'<EntitySet Name="{ENTITY}" EntityType="{NS}.{ENTITY}"/>'
        '</EntityContainer></Schema></edmx:DataServices></edmx:Edmx>'
    )
    return Response(xml, media_type="application/xml", headers=_ODATA_HEADERS)


# ── Datos (Basic Auth) — vía store (dual-run Mongo/SQL, flag PARTNER_SQL) ─────
def _query(flt: dict, *, skip: int | None, top: int | None) -> list[dict]:
    docs = store.odata_query(flt, skip=skip, top=top, max_rows=_MAX_ROWS)
    if not top and len(docs) == _MAX_ROWS:
        logger.warning("odata Portfolio: se alcanzó el tope de %s filas (truncado)", _MAX_ROWS)
    return docs


@router.get("/Portfolio")
@limiter.limit("300/hour")
def portfolio_entityset(
    request: Request,
    response: Response,
    _user: str = Depends(_basic_auth),
    filter_: str | None = Query(None, alias="$filter"),
    top: int | None = Query(None, alias="$top", ge=0),
    skip: int | None = Query(None, alias="$skip", ge=0),
    select: str | None = Query(None, alias="$select"),
    inlinecount: str | None = Query(None, alias="$inlinecount"),
) -> dict:
    """Entity set Portfolio — posiciones de Cartera en formato OData v2 JSON."""
    response.headers["DataServiceVersion"] = "2.0"
    flt = _parse_filter(filter_)

    total = None
    if (inlinecount or "").lower() == "allpages":
        total = store.odata_count(flt)

    sel = {s.strip() for s in select.split(",")} if select else None
    results = []
    for d in _query(flt, skip=skip, top=top):
        row: dict[str, Any] = {"__metadata": {"type": f"{NS}.{ENTITY}"}, "ID": _key(d)}
        for name, _t in _PROPS:
            row[name] = d.get(name)
        if sel:
            row = {k: v for k, v in row.items() if k in sel or k in ("__metadata", "ID")}
        results.append(row)

    d_obj: dict[str, Any] = {"results": results}
    if total is not None:
        d_obj["__count"] = str(total)  # v2: __count es string
    return {"d": d_obj}


@router.get("/Portfolio/$count")
@limiter.limit("300/hour")
def portfolio_count(
    request: Request,
    _user: str = Depends(_basic_auth),
    filter_: str | None = Query(None, alias="$filter"),
) -> Response:
    """Conteo de filas (plain text) — OData v2 `$count`."""
    n = store.odata_count(_parse_filter(filter_))
    return Response(str(n), media_type="text/plain", headers=_ODATA_HEADERS)
