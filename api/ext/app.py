"""api/ext/app.py — la sub-app `/ext`. Superficie COMPLETA de la API externa.

Se monta en `api/main.py` con `app.mount("/ext", ext_app)` y sólo si
`EXT_JWT_SECRET` está configurado: sin esa variable la superficie no existe.

**Por qué sub-app y no un router más dentro de `/api`.** En `/api` un router
nuevo nace ALCANZABLE y hay que acordarse de gatearlo — de ahí que exista
`GUEST_PATH_PREFIXES` (REGLA #8), que es una allowlist que alguien debe
mantener. Acá el default-deny es topología: un router de la mesa es
físicamente inalcanzable desde `/ext`, sin lista ni test que recordar. De yapa,
el OpenAPI de `/ext` documenta SÓLO estos cuatro endpoints — el consumidor
externo no ve ni el nombre de las 541 rutas de la mesa.
"""
from __future__ import annotations

import logging
import time
from datetime import date

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from api.ext import db, lectura
from api.ext.auth import Cliente, cliente_actual, emitir_token, ip_del_request, resolver_cuentas
from api.ext.ratelimit import limiter
from config import EXT_MAX_LIMIT

logger = logging.getLogger(__name__)

_DESCRIPCION = """
API de **operaciones** para accionistas de ACA Valores.

Devuelve, boleto por boleto, las operaciones de las cuentas autorizadas para tu
credencial. Sólo lectura.

## Cómo autenticarse

1. Tu integración pega desde una IP autorizada, con el **service token de
   Cloudflare** en los headers `CF-Access-Client-Id` y `CF-Access-Client-Secret`.
   Sin eso el request no llega a nuestros servidores.
2. Cambiás tu **API key** por un **token de acceso**: `POST /v1/auth/token`.
3. Usás el token como `Authorization: Bearer <token>` en el resto de los
   endpoints. Dura 30 minutos; pedí uno nuevo cuando venza (o ante un `401`
   con `code: "token_expirado"`).

Tu API key es de larga duración y viaja **una sola vez por sesión**; el token es
el que viaja en cada request. Si necesitás rotar la key, pedinos una nueva: las
dos conviven mientras hacés el cambio, así no tenés que coordinar un corte.

## Cómo sincronizar sin perderte cambios

Un boleto **puede cambiar después** del día en que se operó: se completa un
dato, o **se anula**. Por eso hay dos modos de lectura:

* **Carga inicial** — `GET /v1/operaciones?desde=...&hasta=...`, paginando con
  `siguiente_cursor` hasta que `hay_mas` sea `false`.
* **Sincronización** — `GET /v1/operaciones?actualizado_desde=<timestamp de tu
  última corrida>`. Trae **todo lo que cambió**, incluidos los boletos
  **anulados** (llegan con `"anulado": true`): esos hay que darlos de baja de tu
  lado. Guardá el mayor `actualizado_en` que recibiste y usalo en la próxima
  corrida.

Si sólo pedís por fecha de operación, nunca te vas a enterar de una anulación
posterior.

## Cómo reproducir nuestros totales

* Las cauciones aparecen dos veces: la **apertura** y su **cierre**
  (`tipo_operacion` lo dice). El **volumen** operado cuenta sólo la apertura; el
  **arancel** de la caución, en cambio, viene **en el cierre**. Si sumás las dos
  filas duplicás el volumen.
* Los importes son números. Si vas a conciliar sumando decenas de miles de
  filas, sumá en **centavos** (enteros): es la forma estándar de que no se
  acumule la deriva decimal del punto flotante.
"""


# ── modelos de respuesta (son la documentación, no adorno) ───────────────────
class TokenOut(BaseModel):
    token: str = Field(description="Usalo como `Authorization: Bearer <token>`.")
    expira_en: int = Field(description="Segundos hasta que venza.")
    cliente: str = Field(description="Nombre del titular de la credencial.")


class CuentaOut(BaseModel):
    cuenta: str = Field(description="Identificador de comitente.")


class CuentasOut(BaseModel):
    cliente: str
    cuentas: list[CuentaOut]


class OperacionOut(BaseModel):
    boleto: str | None = Field(description="Identificador único y estable de la operación.")
    fecha: str | None = Field(description="Fecha de concertación (AAAA-MM-DD).")
    cuenta: str | None
    cuenta_nombre: str | None
    ticker: str | None = Field(
        description="Ticker del título: con esto relacionás la operación contra "
                    "tu propio catálogo. Puede venir `null` cuando el título "
                    "todavía no está en nuestro maestro.")
    tipo_operacion: str | None
    operacion: str | None = Field(description="Compra / Venta / Suscripción / Rescate / …")
    cantidad: float | None
    bruto: float | None = Field(description="Monto bruto de la operación, en `moneda`.")
    moneda: str | None
    mercado: str | None
    tasa: float | None = Field(
        description="Sólo boletos MAV (pagarés/cheques): tasa en PORCENTAJE "
                    "(6 = 6%). `null` no es 0 — 0 sería una tasa real.")
    mep: float | None = Field(
        description="Tipo de cambio del día del boleto, para dolarizar con el "
                    "mismo número que usamos nosotros.")
    anulado: bool = Field(description="Si es `true`, dala de baja de tu lado.")
    actualizado_en: str | None = Field(description="Última modificación (UTC). Es el reloj del cursor.")
    arancel: float | None = Field(default=None, description="Sólo si tu credencial lo incluye.")
    arancel_moneda: str | None = Field(default=None, description="Siempre `ARS`.")


class PaginacionOut(BaseModel):
    limit: int
    devueltas: int
    hay_mas: bool = Field(description="Si es `true`, volvé a pedir con `siguiente_cursor`.")
    siguiente_cursor: str | None = Field(
        description="Valor opaco: pasalo tal cual en `cursor`. No lo interpretes.")


class OperacionesOut(BaseModel):
    operaciones: list[OperacionOut]
    paginacion: PaginacionOut


class MetaOut(BaseModel):
    primera_operacion: str | None
    ultima_operacion: str | None = Field(
        description="Hasta acá llegan tus datos. Un día posterior a esta fecha "
                    "no es 'sin operaciones': es 'todavía no ingestado'.")
    ultima_ingesta: str | None = Field(description="Cuándo actualizamos por última vez (UTC).")
    total_operaciones: int


class TokenIn(BaseModel):
    apiKey: str = Field(description="La API key que te entregamos.")


# ── la app ───────────────────────────────────────────────────────────────────
ext_app = FastAPI(
    title="ACA Valores — API de Operaciones",
    version="1.0.0",
    description=_DESCRIPCION,
    # Sin `default_response_class=ORJSONResponse`: FastAPI ya serializa vía
    # Pydantic cuando hay `response_model` (que acá lo tienen todos), y pasarlo
    # emite un DeprecationWarning en cada request — ruido en la salida del smoke.
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    # La superficie es el contrato: nada de rutas sin documentar.
    openapi_tags=[
        {"name": "Auth", "description": "Obtener el token de acceso."},
        {"name": "Operaciones", "description": "Los datos."},
    ],
)
ext_app.state.limiter = limiter
ext_app.add_middleware(SlowAPIMiddleware)


@ext_app.exception_handler(RateLimitExceeded)
def _rate_limit(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": {"code": "rate_limit",
                            "message": f"límite alcanzado: {exc.detail}",
                            "retry_after_s": 60}},
    )


@ext_app.middleware("http")
async def _auditoria(request: Request, call_next):
    """Deja rastro de CADA request en `ext.requests_log`.

    Va en middleware y no en cada endpoint para que no se pueda olvidar: un
    endpoint nuevo queda auditado sin escribir una línea. Los datos de identidad
    los deja el endpoint en `request.state` (el middleware corre antes de la
    autenticación y no puede resolverlos por su cuenta).

    Nunca puede tumbar un request: `log_request` traga sus propios errores.
    """
    t0 = time.perf_counter()
    response = await call_next(request)
    if request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
        return response
    db.log_request(
        cliente_id=getattr(request.state, "cliente_id", None),
        prefijo=getattr(request.state, "prefijo", None),
        ip=ip_del_request(request),
        metodo=request.method,
        path=request.url.path,
        filtros=getattr(request.state, "filtros", None),
        filas=getattr(request.state, "filas", None),
        status=response.status_code,
        ms=int((time.perf_counter() - t0) * 1000),
    )
    return response


# ── endpoints ────────────────────────────────────────────────────────────────
@ext_app.post("/v1/auth/token", response_model=TokenOut, tags=["Auth"])
@limiter.limit("10/minute;100/hour")
def auth_token(request: Request, body: TokenIn) -> TokenOut:
    """Intercambia tu API key por un token de acceso temporal.

    Está limitado más fuerte que el resto a propósito: es el único endpoint
    donde se prueba una credencial de larga duración, así que es el único donde
    tiene sentido intentar a fuerza bruta.
    """
    token, ttl, fila = emitir_token(body.apiKey, ip_del_request(request))
    request.state.cliente_id = fila["cliente_id"]
    request.state.prefijo = fila["prefijo"]
    return TokenOut(token=token, expira_en=ttl, cliente=str(fila["nombre"]))


def _sellar(request: Request, cli: Cliente, filtros: dict | None = None) -> None:
    """Deja la identidad en `request.state` para que la auditoría la encuentre."""
    request.state.cliente_id = cli.id
    request.state.prefijo = cli.prefijo
    if filtros:
        request.state.filtros = filtros


@ext_app.get("/v1/cuentas", response_model=CuentasOut, tags=["Operaciones"])
def cuentas(request: Request, cli: Cliente = Depends(cliente_actual)) -> CuentasOut:
    """Qué cuentas alcanza tu credencial.

    Existe para que no tengas que preguntarnos por mail qué número te
    corresponde, y para que puedas verificar de un vistazo que tu integración
    ve exactamente lo que tiene que ver.
    """
    _sellar(request, cli)
    request.state.filas = len(cli.cuentas)
    return CuentasOut(cliente=cli.nombre, cuentas=[CuentaOut(cuenta=c) for c in cli.cuentas])


@ext_app.get("/v1/meta", response_model=MetaOut, tags=["Operaciones"])
def meta(request: Request, cli: Cliente = Depends(cliente_actual)) -> MetaOut:
    """Hasta cuándo hay datos tuyos y cuándo se actualizaron por última vez.

    Consultalo antes de concluir que un día no tuvo operaciones: si es posterior
    a `ultima_operacion`, todavía no llegó.
    """
    _sellar(request, cli)
    return MetaOut(**lectura.meta(cuentas=cli.cuentas))


@ext_app.get("/v1/operaciones", response_model=OperacionesOut, tags=["Operaciones"])
def operaciones(
    request: Request,
    cli: Cliente = Depends(cliente_actual),
    desde: date | None = Query(None, description="Fecha de concertación mínima (AAAA-MM-DD)."),
    hasta: date | None = Query(None, description="Fecha de concertación máxima (AAAA-MM-DD)."),
    actualizado_desde: str | None = Query(
        None,
        description="Timestamp ISO-8601 (UTC). Modo sincronización: devuelve todo "
                    "lo que cambió desde ese momento, **incluidos los anulados**."),
    cuenta: str | None = Query(
        None,
        description="Restringí a una o varias de TUS cuentas, separadas por coma. "
                    "Omitilo para traer todas. Pedir una cuenta ajena devuelve 403."),
    cursor: str | None = Query(None, description="`siguiente_cursor` de la página anterior."),
    limit: int = Query(lectura.LIMIT_DEFAULT, ge=1, le=EXT_MAX_LIMIT),
) -> OperacionesOut:
    """Tus operaciones, boleto por boleto.

    Dos modos, y conviene usar los dos: `desde`/`hasta` para la carga inicial,
    `actualizado_desde` para mantenerla al día. Ver la descripción de la API.
    """
    cuentas_pedidas = resolver_cuentas(cli, cuenta)
    filtros = {"desde": str(desde) if desde else None,
               "hasta": str(hasta) if hasta else None,
               "actualizado_desde": actualizado_desde,
               "cuenta": cuenta, "limit": limit, "cursor": bool(cursor)}
    _sellar(request, cli, filtros)

    try:
        res = lectura.operaciones(
            cuentas=cuentas_pedidas,
            desde=str(desde) if desde else None,
            hasta=str(hasta) if hasta else None,
            actualizado_desde=actualizado_desde,
            cursor=cursor,
            limit=limit,
            incluir_aranceles=cli.ver_aranceles,
        )
    except ValueError as e:
        return _mal_pedido(str(e))

    request.state.filas = len(res["operaciones"])
    return OperacionesOut(**res)


def _mal_pedido(mensaje: str):
    from fastapi import HTTPException
    raise HTTPException(status_code=400, detail={"code": "parametro_invalido", "message": mensaje})


@ext_app.get("/v1/health", include_in_schema=False)
def health():
    return {"status": "ok"}
