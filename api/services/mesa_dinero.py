"""api/services/mesa_dinero.py — MESA DE DINERO (vista NEGOCIO → /mesa-dinero).

Registro MANUAL de las operaciones de la mesa (compra+venta del mismo activo)
en `operaciones.mesa_dinero` + TC diario manual (`mesa_dinero_tc`) para
dolarizar el resultado. El resultado DIARIO no se persiste: se deriva con
SUM(resultado) GROUP BY fecha (verificado contra la planilla de la mesa).

Derivados (una sola fuente de verdad, calculados acá — no confía en el front):
    monto     = vn × px / 100
    resultado = monto_venta − monto_compra   (o manual si el registro no tiene patas)
    pct       = resultado / monto_compra

Permisos (los DOS son allowlist per-usuario + admin; default-deny):
    LECTURA   → `operaciones.mesa_dinero_lectores` ∪ `mesa_dinero_escritores`.
                Escribir IMPLICA leer, así las listas no se contradicen.
    ESCRITURA → `operaciones.mesa_dinero_escritores`.
    Las dos las edita el admin en Manager → MESA.

    Por qué per-usuario y no un módulo del RBAC (decisión 2026-08-11): el
    criterio de acceso a esta vista es "estas personas", no "este puesto". Con
    un módulo habría que crear un rol por cada combinación de gente. Mismo
    patrón que `require_control_comercial`.

Catálogos (Manager → MESA):
    trader      → `operaciones.mesa_dinero_traders` (carga manual del admin).
    observacion → "Mesa" u operador comercial (`clientes.operadores`). Validado.

Trazabilidad: TODO cambio (op, TC, traders, escritores) inserta un evento en
`operaciones.mesa_dinero_audit` con before/after. Servicio puro (sin FastAPI).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from psycopg.types.json import Jsonb

from api.cache import cached, invalidate
from api.services._sql import _f, _q
from core.postgres import get_pool

OBSERVACION_MESA = "Mesa"

# TTL del permiso de LECTURA (lo pide /api/me en cada navegación — ver puede_ver).
_TTL_PERMISO_S = 60

# Campos editables de una operación (el resto es derivado o metadata de auditoría).
_CAMPOS_OP = (
    "fecha", "trader", "activo", "vn_compra", "px_compra",
    "vn_venta", "px_venta", "resultado", "cliente", "observacion",
)


def _exec(sql: str, params: dict) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        n = cur.rowcount
        conn.commit()
        return n


def _audit(actor: str, action: str, target: str, data: dict | None = None) -> None:
    """Evento de auditoría — nunca rompe la operación principal."""
    try:
        _exec(
            "INSERT INTO operaciones.mesa_dinero_audit (ts, actor, action, target, data) "
            "VALUES (%(ts)s, %(actor)s, %(action)s, %(target)s, %(data)s)",
            {"ts": datetime.now(UTC), "actor": actor or None, "action": action,
             "target": target, "data": Jsonb(_jsonable(data or {}))},
        )
    except Exception:  # el audit no bloquea la escritura real
        import logging
        logging.getLogger(__name__).exception("mesa_dinero: audit insert falló")


def _jsonable(v: Any) -> Any:
    """dict/list con date/datetime/Decimal → tipos serializables."""
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if hasattr(v, "is_finite"):  # Decimal
        return float(v)
    return v


# ─────────────────────────────────────────────────────────────
# Permisos de escritura (allowlist per-usuario + admin)
# ─────────────────────────────────────────────────────────────

def puede_escribir(email: str) -> bool:
    """True si el usuario puede ESCRIBIR en Mesa de Dinero. Default-deny.

    admin siempre (para no quedar afuera de la gestión); el resto solo si el
    admin lo agregó a la allowlist en Manager → MESA."""
    email_norm = (email or "").lower().strip()
    if not email_norm:
        return False
    from core.roles import get_user_role
    if get_user_role(email_norm) == "admin":
        return True
    rows = _q(
        "SELECT 1 FROM operaciones.mesa_dinero_escritores WHERE email = %(e)s",
        {"e": email_norm},
    )
    return bool(rows)


@cached(_TTL_PERMISO_S)
def puede_ver(email: str) -> bool:
    """True si el usuario puede VER la vista Mesa de Dinero. Default-deny.

    admin siempre; el resto si está en la allowlist de LECTURA **o** en la de
    ESCRITURA (escribir implica leer: nadie puede cargar en una vista que no
    ve, y con dos listas independientes ese estado incoherente sería posible).

    CACHEADO 60s a propósito: esto lo consulta `/api/me`, que corre en CADA
    navegación del front. Sin cache serían ~28ms de round-trip a Supabase
    sumados al hot path del RBAC (mismo hallazgo de telemetría que llevó a
    throttlear `last_seen_at`). El TTL empata con el del cache de roles, así
    que un alta/baja tarda hasta 60s en verse — igual que un cambio de rol.
    Las mutaciones de las allowlists invalidan esta entrada explícitamente.
    """
    email_norm = (email or "").lower().strip()
    if not email_norm:
        return False
    from core.roles import get_user_role
    if get_user_role(email_norm) == "admin":
        return True
    rows = _q(
        "SELECT 1 FROM operaciones.mesa_dinero_lectores  WHERE email = %(e)s "
        "UNION ALL "
        "SELECT 1 FROM operaciones.mesa_dinero_escritores WHERE email = %(e)s "
        "LIMIT 1",
        {"e": email_norm},
    )
    return bool(rows)


def _invalidar_permisos() -> None:
    """Tira el cache de `puede_ver` tras tocar cualquiera de las dos allowlists
    (las dos alimentan la misma respuesta) para que el alta/baja se vea ya."""
    invalidate("puede_ver")


# ─────────────────────────────────────────────────────────────
# Catálogos (opciones del formulario)
# ─────────────────────────────────────────────────────────────

def opciones(email: str = "") -> dict:
    """Opciones del formulario: traders válidos + observaciones válidas +
    clientes ya usados (sugerencias, no restrictivo) + si el usuario actual
    puede escribir (el front esconde la edición sin esto, pero el enforcement
    real es server-side en cada write)."""
    return {
        "traders": _traders_validos(),
        "observaciones": _observaciones_validas(),
        "clientes": _clientes_usados(),
        "puede_escribir": puede_escribir(email),
    }


def _traders_validos() -> list[str]:
    return [r["nombre"] for r in _q(
        "SELECT nombre FROM operaciones.mesa_dinero_traders ORDER BY nombre")]


def _clientes_usados() -> list[str]:
    """Clientes distintos ya cargados en mesa_dinero — sugerencias del form
    (suelen repetirse); NO es un catálogo cerrado, se puede tipear uno nuevo."""
    return [r["cliente"] for r in _q(
        "SELECT DISTINCT cliente FROM operaciones.mesa_dinero "
        "WHERE cliente IS NOT NULL AND cliente <> '' ORDER BY cliente")]


def _observaciones_validas() -> list[str]:
    """"Mesa" + operadores comerciales (clientes.operadores)."""
    ops = [r["nombre"] for r in _q(
        "SELECT nombre FROM clientes.operadores WHERE nombre IS NOT NULL ORDER BY nombre")]
    return [OBSERVACION_MESA, *ops]


# ─────────────────────────────────────────────────────────────
# Operaciones (CRUD)
# ─────────────────────────────────────────────────────────────

def _fila_op(r: dict) -> dict:
    return {
        "id": r["id"],
        "fecha": r["fecha"].isoformat() if r["fecha"] else None,
        "trader": r["trader"],
        "activo": r["activo"],
        "vn_compra": _f(r["vn_compra"]), "px_compra": _f(r["px_compra"]),
        "monto_compra": _f(r["monto_compra"]),
        "vn_venta": _f(r["vn_venta"]), "px_venta": _f(r["px_venta"]),
        "monto_venta": _f(r["monto_venta"]),
        "resultado": _f(r["resultado"]), "pct": _f(r["pct"]),
        "cliente": r["cliente"], "observacion": r["observacion"],
        "creado_por": r["creado_por"], "actualizado_por": r["actualizado_por"],
    }


def listar_ops(desde: str | None = None, hasta: str | None = None,
               trader: str | None = None) -> dict:
    """Operaciones del período (default: sin filtro = todas), más nuevas primero."""
    conds, params = [], {}
    if desde:
        conds.append("fecha >= %(desde)s")
        params["desde"] = desde
    if hasta:
        conds.append("fecha <= %(hasta)s")
        params["hasta"] = hasta
    if trader:
        conds.append("trader = %(trader)s")
        params["trader"] = trader
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    rows = _q(
        f"SELECT * FROM operaciones.mesa_dinero {where} ORDER BY fecha DESC, id DESC",
        params,
    )
    return {"total": len(rows), "operaciones": [_fila_op(r) for r in rows]}


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)


def _derivar(p: dict) -> dict:
    """Aplica las fórmulas sobre el payload ya validado. Devuelve los campos
    derivados (monto_compra/venta, resultado, pct)."""
    vn_c, px_c = _num(p.get("vn_compra")), _num(p.get("px_compra"))
    vn_v, px_v = _num(p.get("vn_venta")), _num(p.get("px_venta"))
    monto_c = vn_c * px_c / 100 if (vn_c is not None and px_c is not None) else None
    monto_v = vn_v * px_v / 100 if (vn_v is not None and px_v is not None) else None
    if monto_c is not None and monto_v is not None:
        resultado = monto_v - monto_c
    else:
        # Registro sin patas (ej. "Pase OPS"): resultado manual obligatorio.
        resultado = _num(p.get("resultado"))
        if resultado is None:
            raise ValueError(
                "sin patas compra+venta completas hay que informar 'resultado' manual")
    pct = (resultado / monto_c) if monto_c else None
    return {"monto_compra": monto_c, "monto_venta": monto_v,
            "resultado": resultado, "pct": pct}


def _validar(p: dict) -> None:
    if not p.get("fecha"):
        raise ValueError("falta 'fecha'")
    trader = (p.get("trader") or "").strip()
    if not trader:
        raise ValueError("falta 'trader'")
    if trader not in _traders_validos():
        raise ValueError(f"trader {trader!r} no está en el catálogo (Manager → MESA)")
    obs = (p.get("observacion") or "").strip()
    if obs and obs not in _observaciones_validas():
        raise ValueError(
            f"observación {obs!r} inválida: tiene que ser 'Mesa' o un operador comercial")


def crear_op(payload: dict, actor: str) -> dict:
    p = {k: payload.get(k) for k in _CAMPOS_OP}
    _validar(p)
    d = _derivar(p)
    row = {
        "fecha": p["fecha"], "trader": p["trader"].strip(),
        "activo": (p.get("activo") or "").strip().upper() or None,
        "vn_compra": _num(p.get("vn_compra")), "px_compra": _num(p.get("px_compra")),
        "vn_venta": _num(p.get("vn_venta")), "px_venta": _num(p.get("px_venta")),
        "cliente": (p.get("cliente") or "").strip() or None,
        "observacion": (p.get("observacion") or "").strip() or None,
        **d,
        "por": (actor or "").lower() or None, "at": datetime.now(UTC),
    }
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operaciones.mesa_dinero "
            "(fecha, trader, activo, vn_compra, px_compra, monto_compra, "
            " vn_venta, px_venta, monto_venta, resultado, pct, cliente, observacion, "
            " creado_por, creado_at, actualizado_por, actualizado_at) "
            "VALUES (%(fecha)s, %(trader)s, %(activo)s, %(vn_compra)s, %(px_compra)s, "
            " %(monto_compra)s, %(vn_venta)s, %(px_venta)s, %(monto_venta)s, "
            " %(resultado)s, %(pct)s, %(cliente)s, %(observacion)s, "
            " %(por)s, %(at)s, %(por)s, %(at)s) RETURNING id",
            row,
        )
        op_id = cur.fetchone()[0]
        conn.commit()
    _audit(actor, "create_op", str(op_id), {"after": row})
    return _get_op(op_id)


def _get_op(op_id: int) -> dict:
    rows = _q("SELECT * FROM operaciones.mesa_dinero WHERE id = %(id)s", {"id": op_id})
    if not rows:
        raise ValueError(f"operación {op_id} no existe")
    return _fila_op(rows[0])


def editar_op(op_id: int, payload: dict, actor: str) -> dict:
    before = _get_op(op_id)
    # Merge: lo que no viene en el payload se mantiene; los derivados se recalculan.
    p = {k: (payload[k] if k in payload else before.get(k)) for k in _CAMPOS_OP}
    _validar(p)
    d = _derivar(p)
    row = {
        "id": op_id,
        "fecha": p["fecha"], "trader": (p["trader"] or "").strip(),
        "activo": (p.get("activo") or "").strip().upper() or None,
        "vn_compra": _num(p.get("vn_compra")), "px_compra": _num(p.get("px_compra")),
        "vn_venta": _num(p.get("vn_venta")), "px_venta": _num(p.get("px_venta")),
        "cliente": (p.get("cliente") or "").strip() or None,
        "observacion": (p.get("observacion") or "").strip() or None,
        **d,
        "por": (actor or "").lower() or None, "at": datetime.now(UTC),
    }
    _exec(
        "UPDATE operaciones.mesa_dinero SET fecha=%(fecha)s, trader=%(trader)s, "
        "activo=%(activo)s, vn_compra=%(vn_compra)s, px_compra=%(px_compra)s, "
        "monto_compra=%(monto_compra)s, vn_venta=%(vn_venta)s, px_venta=%(px_venta)s, "
        "monto_venta=%(monto_venta)s, resultado=%(resultado)s, pct=%(pct)s, "
        "cliente=%(cliente)s, observacion=%(observacion)s, "
        "actualizado_por=%(por)s, actualizado_at=%(at)s WHERE id=%(id)s",
        row,
    )
    after = _get_op(op_id)
    _audit(actor, "update_op", str(op_id), {"before": before, "after": after})
    return after


def borrar_op(op_id: int, actor: str) -> dict:
    before = _get_op(op_id)
    n = _exec("DELETE FROM operaciones.mesa_dinero WHERE id = %(id)s", {"id": op_id})
    _audit(actor, "delete_op", str(op_id), {"before": before})
    return {"borrado": n}


# ─────────────────────────────────────────────────────────────
# Resultado diario (derivado) + TC manual
# ─────────────────────────────────────────────────────────────

def resumen(desde: str | None = None, hasta: str | None = None,
            trader: str | None = None) -> dict:
    """Resultado por día (SUM de las ops) + TC manual + resultado USD + acumulados.

    El resultado diario NO se persiste — es la suma de `resultado` de las ops de
    esa fecha (verificado contra la planilla de la mesa el 2026-07-29). USD solo
    si hay TC cargado para el día."""
    conds, params = [], {}
    if desde:
        conds.append("o.fecha >= %(desde)s")
        params["desde"] = desde
    if hasta:
        conds.append("o.fecha <= %(hasta)s")
        params["hasta"] = hasta
    if trader:
        conds.append("o.trader = %(trader)s")
        params["trader"] = trader
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    rows = _q(
        "SELECT o.fecha, SUM(o.resultado) AS resultado_ars, MAX(t.tc) AS tc "
        "FROM operaciones.mesa_dinero o "
        "LEFT JOIN operaciones.mesa_dinero_tc t ON t.fecha = o.fecha "
        f"{where} GROUP BY o.fecha ORDER BY o.fecha",
        params,
    )
    dias, acum_ars, acum_usd = [], 0.0, 0.0
    for r in rows:
        ars = _f(r["resultado_ars"]) or 0.0
        tc = _f(r["tc"])
        usd = (ars / tc) if tc else None
        acum_ars += ars
        if usd is not None:
            acum_usd += usd
        dias.append({
            "fecha": r["fecha"].isoformat(),
            "resultado_ars": ars, "tc": tc, "resultado_usd": usd,
            "acumulado_ars": acum_ars,
            "acumulado_usd": acum_usd if usd is not None else None,
        })
    return {
        "dias": dias,
        "total_ars": acum_ars,
        "total_usd": acum_usd,
    }


def resultados(desde: str | None = None, hasta: str | None = None,
               trader: str | None = None) -> dict:
    """Tab RESULTADOS: agregados del período por CLIENTE y por COMERCIAL
    (= observación: "Mesa" u operador), en ARS y USD.

    REGLA 50/50 (lado COMERCIAL): la observación dice QUIÉN generó el trade. Si no
    es "Mesa" es un operador comercial, y el resultado se REPARTE mitad y mitad
    entre ese operador y la Mesa. Sin esto el comercial queda inflado al 100% y la
    Mesa subvaluada. El reparto es SOLO de atribución: `total_ars`/`total_usd` y el
    lado por CLIENTE no cambian — la plata es la misma, cambia a quién se le imputa.
    Una observación vacía NO se reparte (no hay operador identificado): queda
    íntegra en "(sin observación)" en vez de regalarle la mitad a la Mesa.

    `n` sigue contando operaciones ORIGINADAS por ese comercial (no participaciones),
    así que la fila de Mesa puede tener resultado sin ops propias: lo aclara
    `desde_operadores_ars` / `desde_operadores_usd`, que es cuánto de la Mesa vino
    del 50% de los operadores.

    USD op por op con el TC manual del día (`mesa_dinero_tc`); las ops de días
    SIN TC no suman USD → `dias_sin_tc` avisa que el USD está incompleto.
    El lado comercial lista TODO el catálogo (Mesa + operadores) aunque estén
    en cero, como la planilla de la mesa."""
    conds, params = [], {}
    if desde:
        conds.append("o.fecha >= %(desde)s")
        params["desde"] = desde
    if hasta:
        conds.append("o.fecha <= %(hasta)s")
        params["hasta"] = hasta
    if trader:
        conds.append("o.trader = %(trader)s")
        params["trader"] = trader
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    rows = _q(
        "SELECT o.fecha, o.cliente, o.observacion, o.resultado, t.tc "
        "FROM operaciones.mesa_dinero o "
        "LEFT JOIN operaciones.mesa_dinero_tc t ON t.fecha = o.fecha "
        f"{where}",
        params,
    )

    def _bucket(d: dict, clave: str) -> dict:
        return d.setdefault(clave, {"resultado_ars": 0.0, "resultado_usd": 0.0, "n": 0,
                                    "desde_operadores_ars": 0.0, "desde_operadores_usd": 0.0})

    def _acum(bucket: dict, clave: str, ars: float, usd: float | None,
              cuenta_op: bool = True) -> None:
        b = _bucket(bucket, clave)
        b["resultado_ars"] += ars
        if usd is not None:
            b["resultado_usd"] += usd
        if cuenta_op:
            b["n"] += 1

    por_cliente: dict[str, dict] = {}
    por_comercial: dict[str, dict] = {}
    total_ars, total_usd, n_total = 0.0, 0.0, 0
    fechas_sin_tc: set = set()
    for r in rows:
        ars = _f(r["resultado"]) or 0.0
        tc = _f(r["tc"])
        usd = (ars / tc) if tc else None
        if tc is None:
            fechas_sin_tc.add(r["fecha"])
        _acum(por_cliente, (r["cliente"] or "").strip() or "(sin cliente)", ars, usd)
        # 50/50: el trade de un operador se reparte con la Mesa. Solo cambia la
        # ATRIBUCIÓN — el total del período es el mismo (mitad + mitad = uno).
        obs = (r["observacion"] or "").strip()
        if obs and obs != OBSERVACION_MESA:
            mitad_usd = (usd / 2) if usd is not None else None
            _acum(por_comercial, obs, ars / 2, mitad_usd)
            # La mitad de la Mesa NO cuenta como operación suya: la originó el operador.
            _acum(por_comercial, OBSERVACION_MESA, ars / 2, mitad_usd, cuenta_op=False)
            m = _bucket(por_comercial, OBSERVACION_MESA)
            m["desde_operadores_ars"] += ars / 2
            if mitad_usd is not None:
                m["desde_operadores_usd"] += mitad_usd
        else:
            _acum(por_comercial, obs or "(sin observación)", ars, usd)
        total_ars += ars
        if usd is not None:
            total_usd += usd
        n_total += 1

    # Comercial: catálogo completo (Mesa + operadores) aunque estén en cero.
    for obs in _observaciones_validas():
        _bucket(por_comercial, obs)

    clientes = [{"cliente": k, **v} for k, v in por_cliente.items()]
    clientes.sort(key=lambda x: x["resultado_ars"], reverse=True)
    comerciales = [{"observacion": k, **v} for k, v in por_comercial.items()]
    comerciales.sort(key=lambda x: (-x["resultado_ars"], x["observacion"]))
    return {
        "por_cliente": clientes,
        "por_comercial": comerciales,
        "total_ars": total_ars,
        "total_usd": total_usd,
        "n_total": n_total,
        "dias_sin_tc": len(fechas_sin_tc),
    }


def set_tc(fecha: str, tc: float, actor: str) -> dict:
    if not fecha:
        raise ValueError("falta 'fecha'")
    if tc is None or float(tc) <= 0:
        raise ValueError("'tc' tiene que ser un número positivo")
    before = _q("SELECT tc FROM operaciones.mesa_dinero_tc WHERE fecha = %(f)s", {"f": fecha})
    _exec(
        "INSERT INTO operaciones.mesa_dinero_tc (fecha, tc, actualizado_por, actualizado_at) "
        "VALUES (%(f)s, %(tc)s, %(por)s, %(at)s) "
        "ON CONFLICT (fecha) DO UPDATE SET tc = EXCLUDED.tc, "
        "actualizado_por = EXCLUDED.actualizado_por, actualizado_at = EXCLUDED.actualizado_at",
        {"f": fecha, "tc": float(tc), "por": (actor or "").lower() or None,
         "at": datetime.now(UTC)},
    )
    _audit(actor, "set_tc", fecha,
           {"before": {"tc": _f(before[0]["tc"]) if before else None},
            "after": {"tc": float(tc)}})
    return {"fecha": fecha, "tc": float(tc)}


# ─────────────────────────────────────────────────────────────
# Gestión (Manager → MESA): traders + escritores
# ─────────────────────────────────────────────────────────────

def listar_traders() -> dict:
    rows = _q("SELECT nombre, creado_por, creado_at FROM operaciones.mesa_dinero_traders "
              "ORDER BY nombre")
    return {"traders": _jsonable(rows)}


def agregar_trader(nombre: str, actor: str) -> dict:
    n = (nombre or "").strip()
    if not n:
        raise ValueError("falta 'nombre'")
    _exec(
        "INSERT INTO operaciones.mesa_dinero_traders (nombre, creado_por, creado_at) "
        "VALUES (%(n)s, %(por)s, %(at)s) ON CONFLICT (nombre) DO NOTHING",
        {"n": n, "por": (actor or "").lower() or None, "at": datetime.now(UTC)},
    )
    _audit(actor, "add_trader", n, {})
    return {"nombre": n}


def quitar_trader(nombre: str, actor: str) -> dict:
    n = (nombre or "").strip()
    if not n:
        raise ValueError("falta 'nombre'")
    borrado = _exec("DELETE FROM operaciones.mesa_dinero_traders WHERE nombre = %(n)s", {"n": n})
    _audit(actor, "remove_trader", n, {})
    return {"borrado": borrado}


def listar_escritores() -> dict:
    rows = _q("SELECT email, agregado_por, agregado_at FROM operaciones.mesa_dinero_escritores "
              "ORDER BY email")
    return {"escritores": _jsonable(rows)}


def candidatos_escritores(q: str = "", limit: int = 30) -> dict:
    """Usuarios de la app (manager.manager_users) que todavía no están en la allowlist."""
    term = f"%{(q or '').strip()}%"
    rows = _q(
        "SELECT u.email, u.role FROM manager.manager_users u "
        "WHERE u.email NOT IN (SELECT email FROM operaciones.mesa_dinero_escritores) "
        "AND u.email ILIKE %(t)s ORDER BY u.email LIMIT %(lim)s",
        {"t": term, "lim": limit},
    )
    return {"candidatos": rows}


def agregar_escritor(email: str, actor: str) -> dict:
    e = (email or "").lower().strip()
    if not e:
        raise ValueError("falta 'email'")
    _exec(
        "INSERT INTO operaciones.mesa_dinero_escritores (email, agregado_por, agregado_at) "
        "VALUES (%(e)s, %(por)s, %(at)s) ON CONFLICT (email) DO NOTHING",
        {"e": e, "por": (actor or "").lower() or None, "at": datetime.now(UTC)},
    )
    _audit(actor, "add_escritor", e, {})
    _invalidar_permisos()   # escribir implica ver → cambia también puede_ver
    return {"email": e}


def quitar_escritor(email: str, actor: str) -> dict:
    e = (email or "").lower().strip()
    if not e:
        raise ValueError("falta 'email'")
    borrado = _exec("DELETE FROM operaciones.mesa_dinero_escritores WHERE email = %(e)s", {"e": e})
    _audit(actor, "remove_escritor", e, {})
    _invalidar_permisos()
    return {"borrado": borrado}


# ─────────────────────────────────────────────────────────────
# Lectores (allowlist de ACCESO a la vista)
# ─────────────────────────────────────────────────────────────
# Misma forma que los escritores, tabla aparte. Un escritor NO necesita estar
# acá (la unión de puede_ver lo cubre), pero tampoco molesta si está.

def listar_lectores() -> dict:
    rows = _q("SELECT email, agregado_por, agregado_at FROM operaciones.mesa_dinero_lectores "
              "ORDER BY email")
    return {"escritores": _jsonable(rows)}   # clave `escritores`: el panel del front es el mismo


def candidatos_lectores(q: str = "", limit: int = 30) -> dict:
    """Usuarios de la app que todavía no tienen acceso (ni lectura ni escritura:
    ofrecer a alguien que ya escribe sería ofrecer un permiso que ya tiene)."""
    term = f"%{(q or '').strip()}%"
    rows = _q(
        "SELECT u.email, u.role FROM manager.manager_users u "
        "WHERE u.email NOT IN (SELECT email FROM operaciones.mesa_dinero_lectores) "
        "AND u.email NOT IN (SELECT email FROM operaciones.mesa_dinero_escritores) "
        "AND u.email ILIKE %(t)s ORDER BY u.email LIMIT %(lim)s",
        {"t": term, "lim": limit},
    )
    return {"candidatos": rows}


def agregar_lector(email: str, actor: str) -> dict:
    e = (email or "").lower().strip()
    if not e:
        raise ValueError("falta 'email'")
    _exec(
        "INSERT INTO operaciones.mesa_dinero_lectores (email, agregado_por, agregado_at) "
        "VALUES (%(e)s, %(por)s, %(at)s) ON CONFLICT (email) DO NOTHING",
        {"e": e, "por": (actor or "").lower() or None, "at": datetime.now(UTC)},
    )
    _audit(actor, "add_lector", e, {})
    _invalidar_permisos()
    return {"email": e}


def quitar_lector(email: str, actor: str) -> dict:
    """Baja del acceso. OJO: si el email además es ESCRITOR sigue viendo la
    vista (escribir implica leer) — se devuelve `sigue_viendo` para que el
    panel lo avise en vez de mentir con un permiso que no se revocó."""
    e = (email or "").lower().strip()
    if not e:
        raise ValueError("falta 'email'")
    borrado = _exec("DELETE FROM operaciones.mesa_dinero_lectores WHERE email = %(e)s", {"e": e})
    _audit(actor, "remove_lector", e, {})
    _invalidar_permisos()
    sigue = bool(_q(
        "SELECT 1 FROM operaciones.mesa_dinero_escritores WHERE email = %(e)s", {"e": e}))
    return {"borrado": borrado, "sigue_viendo": sigue}
