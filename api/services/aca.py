"""api/services/aca.py — ACA · RESUMEN EJECUTIVO DE INVERSIONES (vista /aca).

La cartera propia de ACA contada para los gerentes. Reemplaza una planilla, y el
criterio de diseño es "Excel con las fórmulas ya puestas": todo lo que se puede
derivar se deriva ACÁ (una sola fuente de verdad, el front no recalcula nada) y
lo único que se tipea son los inputs que ninguna fuente del sistema tiene.

QUÉ SE CARGA A MANO (y por qué)
    · `px` de cada activo — el precio de corte del mes. Es LA razón de que esto
      sea manual: el informe es una foto de fin de mes y el last price live de
      hoy no sirve para reconstruirla. Se escribe y persiste.
    · `vn` — el nominal de la posición al cierre.
    · MEP y A3500 del informe.
    · Los rendimientos mensuales del histórico que vienen de afuera (Caspi) o
      que todavía no están automatizados (Badlar, Inflación).

QUÉ SALE SOLO
    · La FICHA de cada activo (cartera, emisor, calificación, clase de activo,
      vencimiento, ticker) → `portafolio.assets`, el catálogo que ya edita
      Manager → Títulos. NO se copia acá: se resuelve por `unidad` en cada
      lectura. Copiarla habría creado una segunda verdad que se desincroniza
      sola (el rebautizo de especies de Aunesa ya mostró lo caro que sale).
    · Monto por activo, total por cartera, % share, ponderaciones.
    · Valuación ARS / A3500 / USD MEP.
    · Total Dolarizado y Total Pesos (regla de moneda editable, ver abajo).
    · Métricas generales (por clase de activo y por emisor).
    · El acumulado de TODA serie del histórico.

REGLA DE MONEDA (Total Dolarizado / Total Pesos)
    `aca.moneda_regla`, editable en Manager → ACA. Dos scopes y el de `clase`
    gana sobre el de `cartera`: HD y DL son dólares, ARS son pesos, y el FCI
    —que tiene fondos de las dos— se abre por `clase_activo` (MM USD y HD T1 a
    dólares; MM ARS, ARS T1 y RENTA VARIABLE a pesos).
    Lo que no resuelve ninguna regla NO se reparte a dedo: cae en
    `sin_clasificar` y la vista lo muestra. Un activo con una clase nueva tiene
    que aparecer como pendiente, no colarse en el lado equivocado.

TODO ES CARGA MANUAL (2026-08-19)
    Ninguna celda de ACA se completa sola desde otra fuente del sistema. El
    histórico tenía series que traían el rendimiento mensual de las series macro
    (el A3500 salía de la variación del dólar): se dio de baja por decisión del
    user. Lo único DERIVADO que queda es el ACUMULADO, y sale de lo que se tipeó
    — no importa un número de ningún lado. Congelado por test; el detalle de qué
    se sacó está en docs/ACA.md §5.

PERMISOS
    LECTURA   → módulo `aca` (rol `empleado_aca`) ∪ admin ∪ escritores.
    ESCRITURA → allowlist de Mesa de Dinero (`operaciones.mesa_dinero_escritores`)
                + admin. Decisión del user: la mesa maneja la cuenta, y una
                segunda allowlist sería una lista más para desincronizar.

Servicio PURO (sin FastAPI). Toda escritura deja rastro en `aca.audit`.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from psycopg.types.json import Jsonb

from api.cache import cached, invalidate
from api.services._sql import _f, _q
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Las 4 carteras del informe, EN EL ORDEN en que se leen en pantalla, con el
# rótulo que usa la planilla (la cartera 'ARS' se titula "Cartera Pesos").
CARTERAS: tuple[str, ...] = ("ARS", "DL", "HD", "FCI")
CARTERA_LABEL: dict[str, str] = {
    "ARS": "Cartera Pesos",
    "DL":  "Cartera DL",
    "HD":  "Cartera HD",
    "FCI": "Cartera FCI",
}

# Divisor del monto por cartera — MISMA regla que la valuación del AuM
# (jobs/portafolio_backfill y pnl.py::_aplicar_normalizer): la renta fija cotiza
# en paridad (precio por cada 100 de nominal) y el resto cotiza por unidad.
# Verificado contra la planilla: FCI IAM Performance Americas, 84.903 × 1,16 =
# 98.487, que es exactamente el monto del informe.
_DIVISOR_PARIDAD = frozenset({"ARS", "DL", "HD"})

# Bloques de emisores de MÉTRICAS GENERALES. `privados` es una selección curada
# ("créditos privados más representativos"), no una partición: un emisor puede
# estar en HD y también acá.
BLOQUES_EMISOR: dict[str, str] = {
    "hd":       "CARTERA HD",
    "dl":       "CARTERA DL",
    "privados": "CREDITOS PRIVADOS MAS REPRESENTATIVOS",
}
# Carteras cuyas métricas se abren POR CLASE DE ACTIVO.
CARTERAS_POR_CLASE: tuple[str, ...] = ("FCI", "ARS")

# Gráficos de "Detalle de las carteras vs benchmarks" (columna `graficos` de
# aca.series). El título lo pone el front; acá solo vive la identidad.
GRAFICOS: tuple[str, ...] = ("total_ars", "total_usd", "pesos")

_MONEDAS = ("usd", "ars")
_TTL_PERMISO_S = 60


# ─────────────────────────────────────────────────────────────
# Infra
# ─────────────────────────────────────────────────────────────

def _exec(sql: str, params: dict | None = None) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        n = cur.rowcount or 0
        conn.commit()
        return n


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if hasattr(v, "is_finite"):  # Decimal
        return float(v)
    return v


def _audit(actor: str, action: str, target: str, data: dict | None = None) -> None:
    """Evento de auditoría — nunca rompe la escritura real."""
    try:
        _exec(
            "INSERT INTO aca.audit (ts, actor, action, target, data) "
            "VALUES (%(ts)s, %(actor)s, %(action)s, %(target)s, %(data)s)",
            {"ts": datetime.now(UTC), "actor": (actor or "").lower().strip() or None,
             "action": action, "target": target, "data": Jsonb(_jsonable(data or {}))},
        )
    except Exception:
        logger.exception("aca: audit insert falló (%s/%s)", action, target)


def _norm_periodo(periodo: str | None) -> str:
    """'YYYY-MM' validado. Formato lexicográficamente ordenable a propósito."""
    p = (periodo or "").strip()
    if len(p) != 7 or p[4] != "-" or not (p[:4].isdigit() and p[5:].isdigit()):
        raise ValueError(f"período inválido: {periodo!r} (se espera 'YYYY-MM')")
    if not 1 <= int(p[5:]) <= 12:
        raise ValueError(f"mes inválido en {periodo!r}")
    return p


# ─────────────────────────────────────────────────────────────
# Permisos
# ─────────────────────────────────────────────────────────────

def puede_escribir(email: str) -> bool:
    """True si el usuario puede EDITAR la vista ACA. Default-deny.

    Reusa la allowlist de Mesa de Dinero (decisión del user 2026-08-13: la mesa
    es la que maneja la cuenta de ACA). admin siempre, para no quedar afuera de
    la gestión."""
    from api.services import mesa_dinero
    return mesa_dinero.puede_escribir(email)


@cached(_TTL_PERMISO_S)
def puede_ver(email: str) -> bool:
    """True si el usuario puede VER la vista ACA. Default-deny.

    Es la UNIÓN de dos mecanismos, a propósito:
      · el módulo `aca` del RBAC (rol `empleado_aca`) — el acceso "por puesto",
        que es como el user lo pidió;
      · la allowlist de escritura — porque escribir implica ver: nadie puede
        cargar una foto en una pantalla que no ve, y con los dos permisos
        sueltos ese estado incoherente sería alcanzable.

    CACHEADO 60s: lo consulta `/api/me`, que corre en CADA navegación del front.
    """
    email_norm = (email or "").lower().strip()
    if not email_norm or email_norm == "anon" or email_norm.startswith("service:"):
        return False
    from core.roles import has_access
    if has_access(email_norm, "aca"):
        return True
    return puede_escribir(email_norm)


@cached(_TTL_PERMISO_S)
def puede_ver_manager(email: str) -> bool:
    """True si al usuario le corresponde la tab **Manager → ACA**. Default-deny.

    Es `puede_escribir` con cache: esa tab es CONFIGURACIÓN + carga del histórico,
    así que quien no puede escribir no tiene nada que hacer adentro. Existe aparte
    y CACHEADA porque la consume `/api/me`, que corre en CADA navegación del front
    — igual que `puede_ver`. El gate real del router usa `puede_escribir` SIN
    cache: un permiso de escritura revocado tiene que cortar en el acto, y como
    mucho el link del nav queda visible 60s más (entrar sin escribir da 403).

    NO chequea el acceso a Manager: eso ya lo hace el gate del paquete
    (`_MANAGER_BASE` en api/main.py) y la página /manager del front. El gate
    efectivo de la tab es (acceso a Manager) Y (escritura en ACA).
    """
    return puede_escribir(email)


def _check_escritura(actor: str) -> str:
    if not puede_escribir(actor):
        raise PermissionError("sin permiso de escritura en ACA")
    return (actor or "").lower().strip()


# ─────────────────────────────────────────────────────────────
# Catálogo de títulos (portafolio.assets) — la ficha NO se duplica
# ─────────────────────────────────────────────────────────────

def _fichas(unidades: list[str]) -> dict[str, dict]:
    """unidad → ficha del catálogo `portafolio.assets`.

    Una unidad cargada en un período y BORRADA después del catálogo devuelve
    ficha vacía en vez de desaparecer: el monto que ya se contó en un informe
    cerrado no puede evaporarse porque alguien limpió el maestro. Se marca
    `sin_ficha` para que la vista lo muestre.
    """
    if not unidades:
        return {}
    rows = _q(
        "SELECT unidad, cartera, clase_activo, emisor, ticker, instrumento, "
        "       calificacion, vencimiento "
        "FROM portafolio.assets WHERE unidad = ANY(%(u)s)",
        {"u": list(unidades)},
    )
    out = {r["unidad"]: {k: (v or "") for k, v in r.items()} for r in rows}
    for u in unidades:
        if u not in out:
            out[u] = {"unidad": u, "cartera": "", "clase_activo": "", "emisor": "",
                      "ticker": "", "instrumento": "", "calificacion": "",
                      "vencimiento": "", "sin_ficha": True}
    return out


def buscar_titulos(q: str = "", cartera: str | None = None, limite: int = 40) -> dict:
    """Buscador de títulos para agregar una fila al detalle (Manager → Títulos es
    la fuente). Devuelve la ficha completa así el front muestra qué va a entrar
    ANTES de agregarlo."""
    term = (q or "").strip()
    where = ["true"]
    params: dict[str, Any] = {"lim": max(1, min(int(limite or 40), 200))}
    if term:
        where.append("(unidad ILIKE %(q)s OR ticker ILIKE %(q)s OR emisor ILIKE %(q)s "
                     "OR instrumento ILIKE %(q)s)")
        params["q"] = f"%{term}%"
    if cartera:
        where.append("cartera = %(c)s")
        params["c"] = cartera
    rows = _q(
        "SELECT unidad, cartera, clase_activo, emisor, ticker, instrumento, "
        "       calificacion, vencimiento "
        f"FROM portafolio.assets WHERE {' AND '.join(where)} "
        "ORDER BY cartera NULLS LAST, ticker NULLS LAST, unidad LIMIT %(lim)s",
        params,
    )
    return {"titulos": [{k: (v or "") for k, v in r.items()} for r in rows]}


# ─────────────────────────────────────────────────────────────
# Regla de moneda
# ─────────────────────────────────────────────────────────────

def _reglas_moneda() -> dict[str, dict[str, str]]:
    """{'cartera': {clave: moneda}, 'clase': {clave: moneda}} — normalizado en
    MAYÚSCULAS para que 'MM Usd' y 'MM USD' no sean dos reglas distintas."""
    out: dict[str, dict[str, str]] = {"cartera": {}, "clase": {}}
    for r in _q("SELECT scope, clave, moneda FROM aca.moneda_regla"):
        scope = (r["scope"] or "").strip()
        if scope in out:
            out[scope][(r["clave"] or "").strip().upper()] = (r["moneda"] or "").strip().lower()
    return out


def _moneda_de(cartera: str, clase: str, reglas: dict[str, dict[str, str]]) -> str | None:
    """'usd' | 'ars' | None (sin clasificar).

    La regla de CLASE gana sobre la de CARTERA: es lo que permite que el FCI se
    parta por moneda sin sacarlo de su cartera.
    """
    por_clase = reglas["clase"].get((clase or "").strip().upper())
    if por_clase in _MONEDAS:
        return por_clase
    por_cartera = reglas["cartera"].get((cartera or "").strip().upper())
    return por_cartera if por_cartera in _MONEDAS else None


# ─────────────────────────────────────────────────────────────
# Períodos
# ─────────────────────────────────────────────────────────────

def listar_periodos() -> dict:
    rows = _q(
        "SELECT p.periodo, p.fecha_informe, p.mep, p.a3500, p.nota, "
        "       (SELECT count(*) FROM aca.activos a WHERE a.periodo = p.periodo) AS n_activos "
        "FROM aca.periodos p ORDER BY p.periodo DESC"
    )
    return {"periodos": [{
        "periodo": r["periodo"],
        "fecha_informe": r["fecha_informe"].isoformat() if r["fecha_informe"] else None,
        "mep": _f(r["mep"]), "a3500": _f(r["a3500"]),
        "nota": r["nota"] or "", "n_activos": int(r["n_activos"] or 0),
    } for r in rows]}


def _periodo_row(periodo: str) -> dict | None:
    rows = _q(
        "SELECT periodo, fecha_informe, mep, a3500, nota FROM aca.periodos "
        "WHERE periodo = %(p)s", {"p": periodo})
    return rows[0] if rows else None


def _ultimo_periodo() -> str | None:
    rows = _q("SELECT periodo FROM aca.periodos ORDER BY periodo DESC LIMIT 1")
    return rows[0]["periodo"] if rows else None


def _periodo_previo(periodo: str) -> str | None:
    """El período cargado inmediatamente anterior (no el mes calendario anterior:
    si falta un mes, el comparativo del informe igual tiene que mostrar algo)."""
    rows = _q("SELECT periodo FROM aca.periodos WHERE periodo < %(p)s "
              "ORDER BY periodo DESC LIMIT 1", {"p": periodo})
    return rows[0]["periodo"] if rows else None


def guardar_periodo(payload: dict, actor: str) -> dict:
    """Alta/edición de la cabecera del informe (fecha, MEP, A3500, nota)."""
    email = _check_escritura(actor)
    periodo = _norm_periodo(payload.get("periodo"))
    fecha = (payload.get("fecha_informe") or "").strip()
    if not fecha:
        raise ValueError("fecha_informe es obligatoria")
    try:
        fecha_d = date.fromisoformat(fecha)
    except ValueError as e:
        raise ValueError(f"fecha_informe inválida: {fecha!r} (se espera YYYY-MM-DD)") from e
    if fecha_d.strftime("%Y-%m") != periodo:
        raise ValueError(
            f"la fecha del informe ({fecha}) no cae dentro del período {periodo}")

    before = _periodo_row(periodo)
    _exec(
        "INSERT INTO aca.periodos (periodo, fecha_informe, mep, a3500, nota, "
        "                          creado_por, actualizado_por, actualizado_at) "
        "VALUES (%(p)s, %(f)s, %(mep)s, %(a)s, %(n)s, %(u)s, %(u)s, now()) "
        "ON CONFLICT (periodo) DO UPDATE SET fecha_informe = EXCLUDED.fecha_informe, "
        "  mep = EXCLUDED.mep, a3500 = EXCLUDED.a3500, nota = EXCLUDED.nota, "
        "  actualizado_por = EXCLUDED.actualizado_por, actualizado_at = now()",
        {"p": periodo, "f": fecha_d, "mep": payload.get("mep"),
         "a": payload.get("a3500"), "n": (payload.get("nota") or "").strip() or None,
         "u": email},
    )
    _audit(email, "guardar_periodo", periodo, {"before": before, "after": payload})
    return {"ok": True, "periodo": periodo}


def borrar_periodo(periodo: str, actor: str) -> dict:
    """Borra la foto completa (cabecera + activos). El histórico NO se toca: es
    una serie propia que no depende de que el informe del mes esté cargado."""
    email = _check_escritura(actor)
    p = _norm_periodo(periodo)
    before = _periodo_row(p)
    if not before:
        raise ValueError(f"el período {p} no existe")
    n = _exec("DELETE FROM aca.activos WHERE periodo = %(p)s", {"p": p})
    _exec("DELETE FROM aca.periodos WHERE periodo = %(p)s", {"p": p})
    _audit(email, "borrar_periodo", p, {"before": before, "activos_borrados": n})
    return {"ok": True, "periodo": p, "activos_borrados": n}


# ─────────────────────────────────────────────────────────────
# Detalle de activos
# ─────────────────────────────────────────────────────────────

def _monto_fila(cartera: str, vn: float | None, px: float | None,
                manual: float | None) -> float | None:
    """Monto del activo. El override manual gana; si no, vn × px con el divisor
    de la cartera. Sin vn o sin px no hay monto (None ≠ 0: "todavía no cargado"
    y "vale cero" son cosas distintas y la vista las pinta distinto)."""
    if manual is not None:
        return float(manual)
    if vn is None or px is None:
        return None
    divisor = 100.0 if (cartera or "").strip().upper() in _DIVISOR_PARIDAD else 1.0
    return float(vn) * float(px) / divisor


def _activos_periodo(periodo: str) -> list[dict]:
    """Filas del período con la ficha resuelta y el monto derivado. Base común de
    detalle, resumen y métricas — así los tres no pueden contradecirse."""
    rows = _q(
        "SELECT unidad, vn, px, monto, tasa, obs, orden FROM aca.activos "
        "WHERE periodo = %(p)s", {"p": periodo})
    fichas = _fichas([r["unidad"] for r in rows])
    out = []
    for r in rows:
        f = fichas.get(r["unidad"], {})
        cartera = (f.get("cartera") or "").strip().upper()
        vn, px, manual = _f(r["vn"]), _f(r["px"]), _f(r["monto"])
        out.append({
            "unidad":       r["unidad"],
            "ticker":       f.get("ticker") or "",
            "emisor":       f.get("emisor") or "",
            "calificacion": f.get("calificacion") or "",
            "clase_activo": f.get("clase_activo") or "",
            "instrumento":  f.get("instrumento") or "",
            "vencimiento":  f.get("vencimiento") or "",
            "cartera":      cartera,
            "sin_ficha":    bool(f.get("sin_ficha")),
            "vn": vn, "px": px,
            "monto":        _monto_fila(cartera, vn, px, manual),
            "monto_manual": manual,      # != None → el monto está forzado a mano
            "tasa":         r["tasa"] or "",
            "obs":          r["obs"] or "",
            "orden":        int(r["orden"] or 0),
        })
    out.sort(key=lambda a: (a["orden"], a["ticker"] or a["unidad"]))
    return out


def detalle(periodo: str) -> dict:
    """DETALLE DE ACTIVOS por cartera, con total y % share dentro de cada una."""
    p = _norm_periodo(periodo)
    filas = _activos_periodo(p)
    bloques = []
    for cartera in CARTERAS:
        propias = [a for a in filas if a["cartera"] == cartera]
        total = sum(a["monto"] or 0.0 for a in propias)
        for a in propias:
            a["share"] = ((a["monto"] or 0.0) / total) if total else None
        bloques.append({
            "cartera": cartera,
            "label":   CARTERA_LABEL.get(cartera, cartera),
            "total":   total,
            "filas":   propias,
        })
    # Activos cargados cuya ficha los manda a una cartera que el informe no tiene
    # (o que quedaron sin ficha). No se descartan en silencio: sin este bloque, un
    # título mal clasificado en Manager → Títulos desaparecería de la pantalla
    # pero seguiría existiendo en la base.
    huerfanos = [a for a in filas if a["cartera"] not in CARTERAS]
    return {
        "periodo": p,
        "bloques": bloques,
        "huerfanos": huerfanos,
        "total": sum(a["monto"] or 0.0 for a in filas),
    }


def guardar_activo(payload: dict, actor: str) -> dict:
    """Alta/edición de una fila del detalle. `unidad` identifica el título contra
    `portafolio.assets`; la ficha NO viaja en el payload (no es editable acá — se
    edita en Manager → Títulos, que es su dueño)."""
    email = _check_escritura(actor)
    p = _norm_periodo(payload.get("periodo"))
    unidad = (payload.get("unidad") or "").strip()
    if not unidad:
        raise ValueError("unidad es obligatoria")
    if not _periodo_row(p):
        raise ValueError(f"el período {p} no existe — creá el informe primero")
    if not _q("SELECT 1 FROM portafolio.assets WHERE unidad = %(u)s", {"u": unidad}):
        raise ValueError(
            f"el título {unidad!r} no está en el catálogo (Manager → Títulos). "
            "Cargalo ahí primero: la ficha (emisor, calificación, clase, "
            "vencimiento) sale de ese maestro, no de esta vista.")

    before = _q("SELECT vn, px, monto, tasa, obs, orden FROM aca.activos "
                "WHERE periodo = %(p)s AND unidad = %(u)s", {"p": p, "u": unidad})
    _exec(
        "INSERT INTO aca.activos (periodo, unidad, vn, px, monto, tasa, obs, orden, "
        "                         actualizado_por, actualizado_at) "
        "VALUES (%(p)s, %(u)s, %(vn)s, %(px)s, %(m)s, %(t)s, %(o)s, %(ord)s, %(a)s, now()) "
        "ON CONFLICT (periodo, unidad) DO UPDATE SET vn = EXCLUDED.vn, px = EXCLUDED.px, "
        "  monto = EXCLUDED.monto, tasa = EXCLUDED.tasa, obs = EXCLUDED.obs, "
        "  orden = EXCLUDED.orden, actualizado_por = EXCLUDED.actualizado_por, "
        "  actualizado_at = now()",
        {"p": p, "u": unidad, "vn": payload.get("vn"), "px": payload.get("px"),
         "m": payload.get("monto"), "t": (payload.get("tasa") or "").strip() or None,
         "o": (payload.get("obs") or "").strip() or None,
         "ord": int(payload.get("orden") or 0), "a": email},
    )
    _audit(email, "guardar_activo", f"{p}|{unidad}",
           {"before": before[0] if before else None, "after": payload})
    return {"ok": True, "periodo": p, "unidad": unidad}


def borrar_activo(periodo: str, unidad: str, actor: str) -> dict:
    email = _check_escritura(actor)
    p = _norm_periodo(periodo)
    u = (unidad or "").strip()
    before = _q("SELECT vn, px, monto, tasa, obs FROM aca.activos "
                "WHERE periodo = %(p)s AND unidad = %(u)s", {"p": p, "u": u})
    if not before:
        raise ValueError(f"{u!r} no está cargado en {p}")
    _exec("DELETE FROM aca.activos WHERE periodo = %(p)s AND unidad = %(u)s",
          {"p": p, "u": u})
    _audit(email, "borrar_activo", f"{p}|{u}", {"before": before[0]})
    return {"ok": True}


def clonar_periodo(destino: str, origen: str | None, actor: str) -> dict:
    """Copia la COMPOSICIÓN del período `origen` al `destino`.

    Copia las unidades, el VN y la observación; **NO copia el precio**. Es
    deliberado: `px` es el precio de corte del mes y arrastrarlo dejaría un
    informe que parece cargado y está mintiendo. Se clona el esqueleto para no
    volver a elegir 40 títulos a mano, y los precios se tipean.

    No pisa lo ya cargado en el destino (ON CONFLICT DO NOTHING): re-correrlo
    después de agregar una fila a mano es seguro.
    """
    email = _check_escritura(actor)
    p = _norm_periodo(destino)
    if not _periodo_row(p):
        raise ValueError(f"el período {p} no existe — creá el informe primero")
    src = _norm_periodo(origen) if origen else _periodo_previo(p)
    if not src:
        raise ValueError("no hay un período anterior para clonar")
    if src == p:
        raise ValueError("origen y destino son el mismo período")

    n = _exec(
        "INSERT INTO aca.activos (periodo, unidad, vn, px, monto, tasa, obs, orden, "
        "                         actualizado_por, actualizado_at) "
        "SELECT %(dst)s, unidad, vn, NULL, NULL, tasa, obs, orden, %(a)s, now() "
        "FROM aca.activos WHERE periodo = %(src)s "
        "ON CONFLICT (periodo, unidad) DO NOTHING",
        {"dst": p, "src": src, "a": email},
    )
    _audit(email, "clonar_periodo", p, {"origen": src, "filas": n})
    return {"ok": True, "periodo": p, "origen": src, "filas": n}


def _resolver_titulo(clave: str, catalogo: list[dict],
                     ya_cargadas: set[str] | None = None) -> tuple[str | None, str]:
    """Texto de una celda del Excel → `unidad` de `portafolio.assets`.

    Devuelve (unidad, motivo). unidad=None significa que NO se pudo resolver, y
    `motivo` explica por qué — el archivo del user tiene celdas como
    "RMJ28 - BONO MUN. ROSARIO 26/06/28 $", no la unidad interna.

    Orden de intentos, del más fuerte al más débil:
      1. `unidad` exacta (por si el Excel salió de un export nuestro).
      2. `ticker` exacto.
      3. el TICKER que aparece antes de un " - " ("RMJ28 - BONO …" → RMJ28).
      4. `instrumento` exacto.
    Todo case-insensitive y sin espacios de más.

    Si el ticker apunta a MÁS DE UNA unidad se elige una de forma determinista y
    se avisa (ver el bloque de abajo). Lo que NUNCA se hace es adivinar por
    parecido: un título que no está en el catálogo no se importa.
    """
    ya_cargadas = ya_cargadas or set()
    t = (clave or "").strip()
    if not t:
        return None, "celda vacía"
    tn = t.upper()
    # "RMJ28 - BONO MUN. ROSARIO 26/06/28 $" → "RMJ28"
    prefijo = tn.split(" - ", 1)[0].strip() if " - " in tn else None

    for campo, valor, etiqueta in (
        ("unidad", tn, "unidad"),
        ("ticker", tn, "ticker"),
        ("ticker", prefijo, "ticker"),
        ("instrumento", tn, "instrumento"),
    ):
        if not valor:
            continue
        hits = [a for a in catalogo if (a.get(campo) or "").strip().upper() == valor]
        if len(hits) == 1:
            return hits[0]["unidad"], f"por {etiqueta}"
        if len(hits) > 1:
            # AMBIGUO: el ticker existe pero apunta a más de una unidad. Decisión
            # del user (2026-08-13): "por más que sea ambiguo, si el ticker existe
            # ponelo". Antes se descartaba la fila, y eso dejaba afuera plata que
            # SÍ existe por un problema de catálogo — el remedio era peor.
            #
            # Se elige de forma DETERMINISTA y se avisa:
            #   1. la que YA está cargada en este período (continuidad: si el mes
            #      pasado se usó esa unidad, es esa);
            #   2. si no, la primera por `unidad` ordenada — arbitraria pero
            #      estable: el mismo archivo importa siempre igual.
            # La fila queda marcada `ambiguo` con los candidatos, y la pantalla la
            # muestra en ámbar para revisarla. Elegir mal es corregible en un clic;
            # que la plata no aparezca en el informe, no.
            elegida = next((h["unidad"] for h in hits if h["unidad"] in ya_cargadas), None)
            if not elegida:
                elegida = sorted(h["unidad"] for h in hits)[0]
            cands = ", ".join(sorted(h["unidad"] for h in hits)[:4])
            return elegida, f"AMBIGUO ({len(hits)} con ese {etiqueta}: {cands}) → elegí {elegida}"
    return None, "no está en el catálogo de Manager → Títulos"


def importar_activos(payload: dict, actor: str, dry_run: bool = True) -> dict:
    """Carga masiva del detalle desde un Excel. Con `dry_run` NO escribe nada.

    El front parsea el archivo y manda las filas ya normalizadas
    ({titulo, vn, px, tasa, obs}); acá se resuelve cada `titulo` contra
    `portafolio.assets` y se importa SOLO lo reconocido, como pidió el user.
    Las que no se reconocen se devuelven con su motivo — no se descartan en
    silencio ni se inventan.

    Lo que el Excel NO puede traer: emisor, calificación, clase de activo,
    vencimiento y cartera. Esos salen del maestro de Títulos aunque el archivo
    traiga otra cosa (§3 de docs/ACA.md: una sola ficha, una sola verdad). El
    MONTO tampoco se importa: se deriva de VN × Px, que es el punto de la vista.

    `dry_run=True` (default) devuelve exactamente el mismo informe que
    escribiría, para que la pantalla lo muestre ANTES de tocar nada.
    """
    email = _check_escritura(actor)
    p = _norm_periodo(payload.get("periodo"))
    if not _periodo_row(p):
        raise ValueError(f"el período {p} no existe — creá el informe primero")

    filas = payload.get("filas") or []
    if not isinstance(filas, list):
        raise ValueError("`filas` tiene que ser una lista")
    if len(filas) > 2000:
        raise ValueError(f"demasiadas filas ({len(filas)}); el máximo es 2000")

    catalogo = _q("SELECT unidad, ticker, instrumento, cartera, emisor, clase_activo "
                  "FROM portafolio.assets")
    ya_cargadas = {r["unidad"] for r in _q(
        "SELECT unidad FROM aca.activos WHERE periodo = %(p)s", {"p": p})}

    reconocidas: list[dict] = []
    ignoradas: list[dict] = []
    vistas: dict[str, int] = {}          # unidad → índice en `reconocidas`

    for i, f in enumerate(filas):
        titulo = str(f.get("titulo") or "").strip()
        unidad, motivo = _resolver_titulo(titulo, catalogo, ya_cargadas)
        if not unidad:
            ignoradas.append({"fila": i + 2, "titulo": titulo, "motivo": motivo})
            continue
        ficha = next((a for a in catalogo if a["unidad"] == unidad), {})
        item = {
            "fila": i + 2,                    # +2: fila 1 = encabezado del Excel
            "titulo": titulo,
            "unidad": unidad,
            "ticker": ficha.get("ticker") or "",
            "cartera": ficha.get("cartera") or "",
            "emisor": ficha.get("emisor") or "",
            "match": motivo,
            "ambiguo": motivo.startswith("AMBIGUO"),
            "vn": _num(f.get("vn")),
            "px": _num(f.get("px")),
            "tasa": (str(f.get("tasa") or "").strip() or None),
            "obs": (str(f.get("obs") or "").strip() or None),
            "pisa": unidad in ya_cargadas,    # el título ya estaba en el período
        }
        # El MISMO título dos veces en el archivo: gana la última, pero se avisa
        # (en un Excel armado a mano suele ser un copy-paste de más).
        if unidad in vistas:
            item["duplicado_de_fila"] = reconocidas[vistas[unidad]]["fila"]
            reconocidas[vistas[unidad]] = item
        else:
            vistas[unidad] = len(reconocidas)
            reconocidas.append(item)

    if not dry_run and reconocidas:
        with get_pool().connection() as conn, conn.cursor() as cur:
            for orden, it in enumerate(reconocidas, 1):
                cur.execute(
                    "INSERT INTO aca.activos (periodo, unidad, vn, px, tasa, obs, orden, "
                    "                         actualizado_por, actualizado_at) "
                    "VALUES (%(p)s, %(u)s, %(vn)s, %(px)s, %(t)s, %(o)s, %(ord)s, %(a)s, now()) "
                    "ON CONFLICT (periodo, unidad) DO UPDATE SET vn = EXCLUDED.vn, "
                    "  px = EXCLUDED.px, tasa = EXCLUDED.tasa, obs = EXCLUDED.obs, "
                    "  actualizado_por = EXCLUDED.actualizado_por, actualizado_at = now()",
                    {"p": p, "u": it["unidad"], "vn": it["vn"], "px": it["px"],
                     "t": it["tasa"], "o": it["obs"], "ord": orden, "a": email},
                )
            conn.commit()
        _audit(email, "importar_activos", p, {
            "importadas": len(reconocidas), "ignoradas": len(ignoradas),
            "unidades": [it["unidad"] for it in reconocidas],
        })

    return {
        "periodo": p,
        "dry_run": dry_run,
        "reconocidas": reconocidas,
        "ignoradas": ignoradas,
        "total_archivo": len(filas),
        "n_reconocidas": len(reconocidas),
        "n_ignoradas": len(ignoradas),
        "n_pisa": sum(1 for it in reconocidas if it["pisa"]),
        "n_ambiguas": sum(1 for it in reconocidas if it["ambiguo"]),
    }


def _num(v: Any) -> float | None:
    """Celda de Excel → float. Acepta el número nativo o el texto es-AR.

    '337.842.100' son miles y '106,02' es decimal. La heurística: si hay coma,
    la coma es el decimal y los puntos son agrupación; si solo hay puntos, son
    agrupación salvo que quede UN punto con 1-2 decimales detrás ('106.02').
    Sin esto, un archivo guardado con las celdas como texto entraba con el
    precio dividido por mil y nadie lo notaba hasta ver el informe.
    """
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v).strip().replace(" ", "").replace("$", "").replace("%", "")
    if not s:
        return None
    neg = s.startswith("-")
    s = s.lstrip("-+")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    elif "." in s:
        ent, dec = s.split(".")
        if len(dec) == 3 and len(ent) <= 3:   # '337.842' → miles, no decimales
            s = ent + dec
    try:
        f = float(s)
    except ValueError:
        return None
    return -f if neg else f


def precios_sugeridos(periodo: str) -> dict:
    """Último precio conocido de cada título del período, como REFERENCIA.

    NO escribe nada: el precio del informe se tipea (es el corte del mes, no el
    de hoy). Esto solo evita que alguien tenga que ir a buscar el orden de
    magnitud a otra pantalla, y dice de cuándo es cada número para que se vea
    si sirve o no.

    Fuente: `portafolio.tenencia` (la última fecha con precio para esa unidad),
    que es la misma que valúa el AuM.
    """
    p = _norm_periodo(periodo)
    unidades = [a["unidad"] for a in _activos_periodo(p)]
    if not unidades:
        return {"periodo": p, "precios": []}
    rows = _q(
        "SELECT DISTINCT ON (unidad) unidad, fecha, precio "
        "FROM portafolio.tenencia "
        "WHERE unidad = ANY(%(u)s) AND precio IS NOT NULL "
        "ORDER BY unidad, fecha DESC",
        {"u": unidades},
    )
    return {"periodo": p, "precios": [{
        "unidad": r["unidad"],
        "precio": _f(r["precio"]),
        "fecha":  r["fecha"].isoformat() if r["fecha"] else None,
    } for r in rows]}


# ─────────────────────────────────────────────────────────────
# Resumen ejecutivo
# ─────────────────────────────────────────────────────────────

def _resumen_de(periodo: str, reglas: dict[str, dict[str, str]]) -> dict:
    """Bloque de un período: cabecera, valuaciones, 4 carteras y los totales."""
    cab = _periodo_row(periodo) or {}
    filas = _activos_periodo(periodo)
    total = sum(a["monto"] or 0.0 for a in filas)

    por_cartera = {c: 0.0 for c in CARTERAS}
    otras = 0.0
    por_moneda = {"usd": 0.0, "ars": 0.0, "sin_clasificar": 0.0}
    clases_sin_regla: set[str] = set()
    for a in filas:
        monto = a["monto"] or 0.0
        if a["cartera"] in por_cartera:
            por_cartera[a["cartera"]] += monto
        else:
            otras += monto
        moneda = _moneda_de(a["cartera"], a["clase_activo"], reglas)
        if moneda:
            por_moneda[moneda] += monto
        else:
            por_moneda["sin_clasificar"] += monto
            clases_sin_regla.add(a["clase_activo"] or f"(cartera {a['cartera'] or '?'})")

    def _pond(monto: float) -> float | None:
        return (monto / total) if total else None

    mep, a3500 = _f(cab.get("mep")), _f(cab.get("a3500"))
    return {
        "periodo": periodo,
        "fecha_informe": (cab["fecha_informe"].isoformat()
                          if cab.get("fecha_informe") else None),
        "mep": mep, "a3500": a3500,
        "valuacion_ars":     total,
        # Las dos valuaciones en dólares son la misma plata a dos tipos de cambio.
        # None (no 0) si falta el TC: dividir por un dato que nadie cargó es
        # inventar el número más visible del informe.
        "valuacion_a3500":   (total / a3500) if a3500 else None,
        "valuacion_usd_mep": (total / mep) if mep else None,
        "carteras": [{
            "cartera": c, "label": CARTERA_LABEL.get(c, c),
            "monto": por_cartera[c], "ponderacion": _pond(por_cartera[c]),
        } for c in CARTERAS],
        "otras_carteras": {"monto": otras, "ponderacion": _pond(otras)} if otras else None,
        "total_dolarizado": {"monto": por_moneda["usd"], "ponderacion": _pond(por_moneda["usd"])},
        "total_pesos":      {"monto": por_moneda["ars"], "ponderacion": _pond(por_moneda["ars"])},
        # Plata que ninguna regla de moneda supo ubicar. Debería ser 0; si no lo
        # es, la vista lo canta y se arregla en Manager → ACA.
        "sin_clasificar": {
            "monto": por_moneda["sin_clasificar"],
            "ponderacion": _pond(por_moneda["sin_clasificar"]),
            "clases": sorted(clases_sin_regla),
        },
        "n_activos": len(filas),
    }


def resumen(periodo: str) -> dict:
    """RESUMEN EJECUTIVO del período + el bloque comparativo del mes anterior
    (los dos cuadros de la planilla: "Julio - 2026" y "Junio - 2026")."""
    p = _norm_periodo(periodo)
    reglas = _reglas_moneda()
    prev = _periodo_previo(p)
    return {
        "actual": _resumen_de(p, reglas),
        "anterior": _resumen_de(prev, reglas) if prev else None,
    }


# ─────────────────────────────────────────────────────────────
# Métricas generales
# ─────────────────────────────────────────────────────────────

def _agrupar(filas: list[dict], campo: str, catalogo: list[str],
             denominador: float) -> list[dict]:
    """Agrupa por `campo` mostrando SIEMPRE las claves del catálogo (aunque den
    cero) y agregando al final las que aparecieron y no están catalogadas,
    marcadas `fuera_catalogo`. El catálogo agrega filas, nunca esconde plata."""
    acum: dict[str, float] = {}
    for a in filas:
        clave = (a.get(campo) or "").strip()
        if clave:
            acum[clave] = acum.get(clave, 0.0) + (a["monto"] or 0.0)
    vistas = {k.upper(): k for k in acum}

    out = []
    usadas: set[str] = set()
    for clave in catalogo:
        real = vistas.get(clave.upper())
        if real:
            usadas.add(real)
        monto = acum.get(real, 0.0) if real else 0.0
        out.append({"clave": clave, "monto": monto,
                    "share": (monto / denominador) if denominador else None,
                    "fuera_catalogo": False})
    for clave, monto in sorted(acum.items(), key=lambda kv: -kv[1]):
        if clave in usadas:
            continue
        out.append({"clave": clave, "monto": monto,
                    "share": (monto / denominador) if denominador else None,
                    "fuera_catalogo": True})
    return out


def metricas(periodo: str) -> dict:
    """MÉTRICAS GENERALES: apertura por clase de activo (FCI y ARS) y por emisor
    (HD, DL y créditos privados).

    Denominadores, que NO son el mismo: las clases y los emisores de una cartera
    van sobre el total de ESA cartera (es su composición interna); los créditos
    privados van sobre la valuación TOTAL, porque la pregunta que responden es
    cuánto pesa ese riesgo en toda la cartera, no dentro de un bucket.
    """
    p = _norm_periodo(periodo)
    filas = _activos_periodo(p)
    total = sum(a["monto"] or 0.0 for a in filas)

    cat_clases: dict[str, list[str]] = {}
    for r in _q("SELECT cartera, clase, orden FROM aca.clase_destacada "
                "ORDER BY cartera, orden, clase"):
        cat_clases.setdefault((r["cartera"] or "").strip().upper(), []).append(
            (r["clase"] or "").strip())
    cat_emisores: dict[str, list[str]] = {}
    for r in _q("SELECT bloque, emisor, orden FROM aca.emisor_destacado "
                "ORDER BY bloque, orden, emisor"):
        cat_emisores.setdefault((r["bloque"] or "").strip().lower(), []).append(
            (r["emisor"] or "").strip())

    por_clase = []
    for cartera in CARTERAS_POR_CLASE:
        propias = [a for a in filas if a["cartera"] == cartera]
        tot = sum(a["monto"] or 0.0 for a in propias)
        por_clase.append({
            "cartera": cartera, "label": f"CARTERA {cartera}", "total": tot,
            "filas": _agrupar(propias, "clase_activo", cat_clases.get(cartera, []), tot),
        })

    por_emisor = []
    for bloque, label in BLOQUES_EMISOR.items():
        if bloque == "privados":
            # Selección curada sobre TODA la cartera: no se filtra por cartera ni
            # se derivan "los privados" excluyendo soberanos — quién es
            # representativo lo decide la mesa en Manager → ACA.
            base, denom = filas, total
        else:
            base = [a for a in filas if a["cartera"] == bloque.upper()]
            denom = sum(a["monto"] or 0.0 for a in base)
        catalogo = cat_emisores.get(bloque, [])
        agrupadas = _agrupar(base, "emisor", catalogo, denom)
        if bloque == "privados":
            # Acá el catálogo SÍ es la lista completa: mostrar todo emisor del
            # período convertiría "los más representativos" en "todos".
            agrupadas = [f for f in agrupadas if not f["fuera_catalogo"]]
        por_emisor.append({"bloque": bloque, "label": label, "total": denom,
                           "filas": agrupadas})

    return {"periodo": p, "por_clase": por_clase, "por_emisor": por_emisor,
            "total": total}


# ─────────────────────────────────────────────────────────────
# Histórico + gráficos
# ─────────────────────────────────────────────────────────────

def _series_catalogo(incluir_inactivas: bool = False) -> list[dict]:
    rows = _q(
        "SELECT codigo, nombre, grupo, graficos, color, orden, activo "
        "FROM aca.series " + ("" if incluir_inactivas else "WHERE activo ") +
        "ORDER BY orden, codigo")
    return [{
        "codigo": r["codigo"], "nombre": r["nombre"], "grupo": r["grupo"] or "",
        "graficos": list(r["graficos"] or []), "color": r["color"] or "",
        "orden": int(r["orden"] or 0), "activo": bool(r["activo"]),
    } for r in rows]


def _acumular(mensuales: list[float | None]) -> list[float | None]:
    """Acumulado encadenado: acum = (1 + acum_anterior) × (1 + mensual) − 1.

    Un mes SIN rendimiento cargado no rompe la cadena ni la reinicia: arrastra el
    acumulado anterior. Es lo que hace la planilla cuando la columna del mes
    todavía está vacía, y es lo correcto — "no sé cuánto rindió" no es "rindió 0".
    Antes del primer dato el acumulado es None (no 0): no hay serie todavía.
    """
    out: list[float | None] = []
    acum: float | None = None
    for m in mensuales:
        if m is not None:
            acum = (1.0 + (acum or 0.0)) * (1.0 + m) - 1.0
        out.append(acum)
    return out


def historico(desde: str | None = None, hasta: str | None = None) -> dict:
    """Planilla histórica completa: series × períodos, con el acumulado derivado.

    TODO el rendimiento MENSUAL es de carga manual, sin excepción (decisión del
    user 2026-08-19: "nada de ACA tiene que ser automático"). Lo único que se
    deriva es el ACUMULADO, y se deriva de lo que se tipeó — no trae un número
    de ninguna otra fuente del sistema. Ver docs/ACA.md §5.
    """
    where, params = [], {}
    if desde:
        where.append("periodo >= %(d)s")
        params["d"] = _norm_periodo(desde)
    if hasta:
        where.append("periodo <= %(h)s")
        params["h"] = _norm_periodo(hasta)
    filtro = (" WHERE " + " AND ".join(where)) if where else ""

    rows = _q("SELECT periodo, serie, monto, ingreso_retiro, mensual "
              f"FROM aca.historico{filtro}", params)
    periodos = sorted({r["periodo"] for r in rows} |
                      {r["periodo"] for r in _q("SELECT periodo FROM aca.periodos")})
    series = _series_catalogo()

    manual: dict[tuple[str, str], dict] = {
        (r["serie"], r["periodo"]): r for r in rows}

    valores: dict[str, dict[str, dict]] = {}
    for s in series:
        mensuales: list[float | None] = []
        celdas: list[dict] = []
        for p in periodos:
            m = manual.get((s["codigo"], p))
            val = _f(m["mensual"]) if m else None
            origen = "manual" if val is not None else None
            mensuales.append(val)
            celdas.append({
                "periodo": p, "mensual": val, "origen": origen,
                "monto": _f(m["monto"]) if m else None,
                "ingreso_retiro": _f(m["ingreso_retiro"]) if m else None,
            })
        for celda, acum in zip(celdas, _acumular(mensuales), strict=True):
            celda["acumulado"] = acum
        valores[s["codigo"]] = {c["periodo"]: c for c in celdas}

    return {"periodos": periodos, "series": series, "valores": valores}


def graficos(desde: str | None = None, hasta: str | None = None) -> dict:
    """Las 3 curvas de "Detalle de las carteras vs benchmarks", ya armadas: cada
    gráfico con sus series y el ACUMULADO por período (que es lo que se grafica —
    los gráficos de la planilla son de rendimiento acumulado, no mensual)."""
    h = historico(desde=desde, hasta=hasta)
    out = []
    for g in GRAFICOS:
        series = [s for s in h["series"] if g in s["graficos"]]
        out.append({
            "grafico": g,
            "series": [{
                "codigo": s["codigo"], "nombre": s["nombre"],
                "grupo": s["grupo"], "color": s["color"],
                "puntos": [{"periodo": p,
                            "acumulado": h["valores"][s["codigo"]][p]["acumulado"]}
                           for p in h["periodos"]],
            } for s in series],
        })
    return {"periodos": h["periodos"], "graficos": out}


def guardar_historico(payload: dict, actor: str) -> dict:
    """Celda del histórico: monto, ingreso/retiro y rendimiento mensual del mes.

    `mensual` viaja como FRACCIÓN (0,0245 = 2,45%). El acumulado NO se guarda: se
    deriva en la lectura, así no puede quedar contradiciendo a sus insumos.
    """
    email = _check_escritura(actor)
    p = _norm_periodo(payload.get("periodo"))
    serie = (payload.get("serie") or "").strip()
    if not _q("SELECT 1 FROM aca.series WHERE codigo = %(s)s", {"s": serie}):
        raise ValueError(f"serie desconocida: {serie!r}")
    before = _q("SELECT monto, ingreso_retiro, mensual FROM aca.historico "
                "WHERE periodo = %(p)s AND serie = %(s)s", {"p": p, "s": serie})
    _exec(
        "INSERT INTO aca.historico (periodo, serie, monto, ingreso_retiro, mensual, "
        "                           actualizado_por, actualizado_at) "
        "VALUES (%(p)s, %(s)s, %(m)s, %(ir)s, %(men)s, %(a)s, now()) "
        "ON CONFLICT (periodo, serie) DO UPDATE SET monto = EXCLUDED.monto, "
        "  ingreso_retiro = EXCLUDED.ingreso_retiro, mensual = EXCLUDED.mensual, "
        "  actualizado_por = EXCLUDED.actualizado_por, actualizado_at = now()",
        {"p": p, "s": serie, "m": payload.get("monto"),
         "ir": payload.get("ingreso_retiro"), "men": payload.get("mensual"),
         "a": email},
    )
    _audit(email, "guardar_historico", f"{p}|{serie}",
           {"before": before[0] if before else None, "after": payload})
    return {"ok": True, "periodo": p, "serie": serie}


def borrar_historico(periodo: str, serie: str, actor: str) -> dict:
    """Borra la celda. Distinto de guardarla en cero: sin fila, el acumulado
    arrastra el mes anterior; con un cero, multiplica por 1 (mismo resultado
    numérico hoy, pero el origen del dato deja de ser una afirmación)."""
    email = _check_escritura(actor)
    p = _norm_periodo(periodo)
    s = (serie or "").strip()
    before = _q("SELECT monto, ingreso_retiro, mensual FROM aca.historico "
                "WHERE periodo = %(p)s AND serie = %(s)s", {"p": p, "s": s})
    if not before:
        raise ValueError(f"no hay dato de {s!r} en {p}")
    _exec("DELETE FROM aca.historico WHERE periodo = %(p)s AND serie = %(s)s",
          {"p": p, "s": s})
    _audit(email, "borrar_historico", f"{p}|{s}", {"before": before[0]})
    return {"ok": True}


# ─────────────────────────────────────────────────────────────
# Vista completa — UN request
# ─────────────────────────────────────────────────────────────

def vista(periodo: str | None = None, email: str = "") -> dict:
    """TODA la pantalla en UN request: resumen + detalle + métricas + gráficos.

    Igual que `senebis.vista` (2026-08-13): los 4 bloques leen exactamente las
    mismas filas, y pedirlos por separado significaba repetir la misma query 4
    veces por refresh y por usuario. Con el peaje medido de ~8,5ms por
    round-trip a Supabase, agrupar es la diferencia entre ~6 viajes y ~20.
    """
    p = _norm_periodo(periodo) if periodo else _ultimo_periodo()

    # OJO — los gráficos se desarman a mano, NO con `**graficos()`.
    # `graficos()` devuelve su PROPIA clave `periodos` (el eje X: strings
    # 'YYYY-MM') y spreadearlo pisaba la lista de períodos con metadata que
    # arma el selector de la barra (objetos {periodo, fecha_informe, mep…}).
    # El front hacía `p.periodo` sobre un string → undefined.split() → pantalla
    # de error (2026-08-13). No se notó hasta cargar el PRIMER informe: con las
    # dos listas vacías, el pisón era invisible. Dos claves distintas para dos
    # cosas distintas.
    g = graficos()
    base = {
        "periodos": listar_periodos()["periodos"],   # objetos, para el selector
        "periodos_grafico": g["periodos"],           # strings, eje X de los charts
        "graficos": g["graficos"],
        "puede_escribir": puede_escribir(email),
        "carteras": [{"cartera": c, "label": CARTERA_LABEL[c]} for c in CARTERAS],
    }
    if not p:
        # Sin ningún informe cargado la vista tiene que explicarse sola, no
        # devolver un 404 que el front traduzca a "error".
        return {**base, "periodo": None, "resumen": None, "detalle": None,
                "metricas": None}
    return {
        **base,
        "periodo": p,
        "resumen": resumen(p),
        "detalle": detalle(p),
        "metricas": metricas(p),
    }


# ─────────────────────────────────────────────────────────────
# Catálogos (Manager → ACA)
# ─────────────────────────────────────────────────────────────

def catalogos() -> dict:
    """Todo lo configurable de la vista, para el panel de gestión."""
    reglas = _q("SELECT scope, clave, moneda FROM aca.moneda_regla "
                "ORDER BY scope, clave")
    emisores = _q("SELECT bloque, emisor, orden FROM aca.emisor_destacado "
                  "ORDER BY bloque, orden, emisor")
    clases = _q("SELECT cartera, clase, orden FROM aca.clase_destacada "
                "ORDER BY cartera, orden, clase")
    return {
        "moneda_reglas": [dict(r) for r in reglas],
        "emisores": [dict(r) for r in emisores],
        "clases": [dict(r) for r in clases],
        "series": _series_catalogo(incluir_inactivas=True),
        "bloques_emisor": [{"bloque": b, "label": l} for b, l in BLOQUES_EMISOR.items()],
        "carteras": [{"cartera": c, "label": CARTERA_LABEL[c]} for c in CARTERAS],
        "carteras_por_clase": list(CARTERAS_POR_CLASE),
        "graficos": list(GRAFICOS),
        # Valores que YA existen en el catálogo de títulos: sirve para que el
        # panel ofrezca lo real en vez de pedir que se tipee de memoria (y para
        # ver de un vistazo qué clase todavía no tiene regla de moneda).
        "clases_conocidas": [r["clase_activo"] for r in _q(
            "SELECT DISTINCT clase_activo FROM portafolio.assets "
            "WHERE clase_activo IS NOT NULL AND clase_activo <> '' "
            "ORDER BY clase_activo")],
        "emisores_conocidos": [r["emisor"] for r in _q(
            "SELECT DISTINCT emisor FROM portafolio.assets "
            "WHERE emisor IS NOT NULL AND emisor <> '' ORDER BY emisor")],
    }


def set_moneda_regla(scope: str, clave: str, moneda: str, actor: str) -> dict:
    email = _check_escritura(actor)
    sc, cl = (scope or "").strip().lower(), (clave or "").strip()
    mo = (moneda or "").strip().lower()
    if sc not in ("cartera", "clase"):
        raise ValueError("scope debe ser 'cartera' o 'clase'")
    if not cl:
        raise ValueError("clave es obligatoria")
    if mo not in _MONEDAS:
        raise ValueError("moneda debe ser 'usd' o 'ars'")
    _exec(
        "INSERT INTO aca.moneda_regla (scope, clave, moneda, actualizado_por, actualizado_at) "
        "VALUES (%(s)s, %(c)s, %(m)s, %(a)s, now()) "
        "ON CONFLICT (scope, clave) DO UPDATE SET moneda = EXCLUDED.moneda, "
        "  actualizado_por = EXCLUDED.actualizado_por, actualizado_at = now()",
        {"s": sc, "c": cl, "m": mo, "a": email},
    )
    _audit(email, "set_moneda_regla", f"{sc}|{cl}", {"moneda": mo})
    return {"ok": True}


def del_moneda_regla(scope: str, clave: str, actor: str) -> dict:
    email = _check_escritura(actor)
    n = _exec("DELETE FROM aca.moneda_regla WHERE scope = %(s)s AND clave = %(c)s",
              {"s": (scope or "").strip().lower(), "c": (clave or "").strip()})
    _audit(email, "del_moneda_regla", f"{scope}|{clave}", {"filas": n})
    return {"ok": True, "borradas": n}


def set_emisor_destacado(bloque: str, emisor: str, orden: int, actor: str) -> dict:
    email = _check_escritura(actor)
    b, e = (bloque or "").strip().lower(), (emisor or "").strip()
    if b not in BLOQUES_EMISOR:
        raise ValueError(f"bloque inválido: {bloque!r} ({', '.join(BLOQUES_EMISOR)})")
    if not e:
        raise ValueError("emisor es obligatorio")
    _exec("INSERT INTO aca.emisor_destacado (bloque, emisor, orden) "
          "VALUES (%(b)s, %(e)s, %(o)s) "
          "ON CONFLICT (bloque, emisor) DO UPDATE SET orden = EXCLUDED.orden",
          {"b": b, "e": e, "o": int(orden or 0)})
    _audit(email, "set_emisor_destacado", f"{b}|{e}", {"orden": orden})
    return {"ok": True}


def del_emisor_destacado(bloque: str, emisor: str, actor: str) -> dict:
    email = _check_escritura(actor)
    n = _exec("DELETE FROM aca.emisor_destacado WHERE bloque = %(b)s AND emisor = %(e)s",
              {"b": (bloque or "").strip().lower(), "e": (emisor or "").strip()})
    _audit(email, "del_emisor_destacado", f"{bloque}|{emisor}", {"filas": n})
    return {"ok": True, "borradas": n}


def set_clase_destacada(cartera: str, clase: str, orden: int, actor: str) -> dict:
    email = _check_escritura(actor)
    c, cl = (cartera or "").strip().upper(), (clase or "").strip()
    if not c or not cl:
        raise ValueError("cartera y clase son obligatorias")
    _exec("INSERT INTO aca.clase_destacada (cartera, clase, orden) "
          "VALUES (%(c)s, %(cl)s, %(o)s) "
          "ON CONFLICT (cartera, clase) DO UPDATE SET orden = EXCLUDED.orden",
          {"c": c, "cl": cl, "o": int(orden or 0)})
    _audit(email, "set_clase_destacada", f"{c}|{cl}", {"orden": orden})
    return {"ok": True}


def del_clase_destacada(cartera: str, clase: str, actor: str) -> dict:
    email = _check_escritura(actor)
    n = _exec("DELETE FROM aca.clase_destacada WHERE cartera = %(c)s AND clase = %(cl)s",
              {"c": (cartera or "").strip().upper(), "cl": (clase or "").strip()})
    _audit(email, "del_clase_destacada", f"{cartera}|{clase}", {"filas": n})
    return {"ok": True, "borradas": n}


def set_serie(payload: dict, actor: str) -> dict:
    """Alta/edición de una serie del histórico (columna de la planilla + línea de
    los gráficos)."""
    email = _check_escritura(actor)
    codigo = (payload.get("codigo") or "").strip().lower()
    if not codigo or not codigo.replace("_", "").isalnum():
        raise ValueError("codigo inválido (letras, números y '_')")
    nombre = (payload.get("nombre") or "").strip()
    if not nombre:
        raise ValueError("nombre es obligatorio")
    gs = [g for g in (payload.get("graficos") or []) if g in GRAFICOS]

    before = _q("SELECT * FROM aca.series WHERE codigo = %(c)s", {"c": codigo})
    _exec(
        # Las columnas `fuente`/`escala` quedan en su DEFAULT: son VESTIGIALES
        # de la automatización dada de baja. No se escriben ni se leen.
        "INSERT INTO aca.series (codigo, nombre, grupo, graficos, "
        "                        color, orden, activo) "
        "VALUES (%(c)s, %(n)s, %(g)s, %(gr)s, %(col)s, %(o)s, %(act)s) "
        "ON CONFLICT (codigo) DO UPDATE SET nombre = EXCLUDED.nombre, "
        "  grupo = EXCLUDED.grupo, "
        "  graficos = EXCLUDED.graficos, color = EXCLUDED.color, orden = EXCLUDED.orden, "
        "  activo = EXCLUDED.activo",
        {"c": codigo, "n": nombre, "g": (payload.get("grupo") or "").strip() or None,
         "gr": gs,
         "col": (payload.get("color") or "").strip() or None,
         "o": int(payload.get("orden") or 0),
         "act": bool(payload.get("activo", True))},
    )
    _audit(email, "set_serie", codigo,
           {"before": _jsonable(before[0]) if before else None, "after": payload})
    return {"ok": True, "codigo": codigo}


def del_serie(codigo: str, actor: str) -> dict:
    """Baja LÓGICA (activo=false). No se borra la fila: sus valores históricos
    siguen en `aca.historico` y borrar el catálogo los dejaría huérfanos."""
    email = _check_escritura(actor)
    c = (codigo or "").strip().lower()
    n = _exec("UPDATE aca.series SET activo = false WHERE codigo = %(c)s", {"c": c})
    if not n:
        raise ValueError(f"serie desconocida: {codigo!r}")
    _audit(email, "del_serie", c, {})
    return {"ok": True}


def invalidar_permisos() -> None:
    """Purga los caches de permisos (post-cambio de allowlist o de rol)."""
    invalidate("puede_ver")
    invalidate("puede_ver_manager")
