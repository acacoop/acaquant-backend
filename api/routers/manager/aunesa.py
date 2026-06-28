"""Manager · Aunesa — endpoint exploratorio en vivo.

Thin wrapper sobre `api/services/aunesa_negocio.py`. Para discovery /
debugging desde el panel /manager. La vista de producción
/operaciones/negocio NO usa este endpoint — lee de SQL
operaciones.negocio_movimientos (escrita por jobs/negocio_movimientos.py).
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import aunesa_negocio as svc
from api.services._negocio_arancelables import OP_NO_ARANCELABLES
from api.services._negocio_futuros import EXCLUIR_UNIDADES_FUTUROS
from api.services.aunesa_aranceles import run_backfill
from core.pg_mirror import write_native
from core.postgres import get_pool

router = APIRouter()
logger = logging.getLogger("api.manager.aunesa")

_JOBS_TABLE = "aranceles_job_runs"
_STALE_S = 300  # 5 min sin update → marca el job como `stale` (proceso reiniciado).

# Columnas fijas de aranceles_job_runs; todo lo demás (cuentas, workers,
# cuentas_total/done, stats, ejemplos, errores, error) vive en el jsonb `data`.
_UI_FIXED_COLS = ("id", "status", "actor", "desde", "hasta", "apply",
                  "started_at", "updated_at", "finished_at")


@router.get("/aunesa/explorar")
def aunesa_explorar(
    fecha: str | None = Query(None, description="YYYY-MM-DD; default: hoy ART"),
    tipos_cuenta: str = Query("Comitente", description="tiposCuenta param de Aunesa"),
) -> dict[str, Any]:
    """Pega a Aunesa LIVE, devuelve consolidado + raw + categorías.

    Para uso exploratorio desde /manager. La fecha se interpreta como ART.
    """
    if fecha:
        try:
            d = datetime.strptime(fecha, "%Y-%m-%d").date()
        except ValueError as e:
            raise HTTPException(status_code=400,
                                detail=f"fecha mal formada: {fecha} (esperado YYYY-MM-DD)") from e
    else:
        d = (datetime.now(UTC) - timedelta(hours=3)).date()

    try:
        return svc.fetch_y_consolidar(fecha=d, tipos_cuenta=tipos_cuenta)
    except requests.exceptions.Timeout as e:
        raise HTTPException(status_code=504, detail=str(e)) from e
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        body = e.response.text[:300] if e.response is not None else str(e)
        raise HTTPException(status_code=status, detail=f"Aunesa: {body}") from e
    except Exception as e:
        logger.exception("aunesa explorar failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/aunesa/posicion")
def aunesa_posicion(
    id_cuenta: str = Query(..., description="ID de cuenta Aunesa, ej '805'"),
    desde: str | None = Query(
        None,
        description="Fecha YYYY-MM-DD que se manda como `desde` a Aunesa. Por la "
                    "regla H1, `desde=X` devuelve la posición del día hábil "
                    "ANTERIOR a X. Default: hoy (= último cierre hábil).",
    ),
) -> dict[str, Any]:
    """Pega a Aunesa LIVE y devuelve la posición valuada CRUDA de una cuenta.

    NO pasa por Valuaciones.AuM — es la respuesta directa del endpoint
    `posicionValuada` de Aunesa. Sirve para comparar lo que Aunesa manda
    (precio, cantidad por unidad) contra lo que el job terminó persistiendo
    en la base. `cantidad` viene con el signo nativo de Aunesa (el job la
    invierte al procesar).

    Devuelve solo los items con `informacion == "Acumulado"` (las
    posiciones; el resto de la respuesta son detalles intermedios).
    """
    # Import adentro: el cliente Aunesa vive en jobs/aum.py. Si fallara el
    # import, solo se cae este endpoint — no el resto de la API.
    try:
        from jobs.aum import autenticar, consultar_posicion
    except Exception as e:
        logger.exception("no se pudo importar el cliente Aunesa de jobs.aum")
        raise HTTPException(status_code=500,
                            detail=f"import cliente Aunesa: {e}") from e

    # Aunesa espera la fecha en DD/MM/YYYY. El frontend manda YYYY-MM-DD.
    if desde:
        try:
            desde_q = datetime.strptime(desde, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=f"fecha mal formada: {desde} (esperado YYYY-MM-DD)",
            ) from e
    else:
        # Regla H1: desde=hoy → Aunesa devuelve el último cierre hábil.
        desde_q = datetime.now().strftime("%d/%m/%Y")
    try:
        headers = autenticar()
        data, necesita_reauth = consultar_posicion(id_cuenta, headers, desde_q)
        if necesita_reauth:
            headers = autenticar()
            data, _ = consultar_posicion(id_cuenta, headers, desde_q)
    except requests.exceptions.Timeout as e:
        raise HTTPException(status_code=504, detail=f"Aunesa timeout: {e}") from e
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        body = e.response.text[:300] if e.response is not None else str(e)
        raise HTTPException(status_code=status, detail=f"Aunesa: {body}") from e
    except Exception as e:
        logger.exception("aunesa_posicion failed para %s", id_cuenta)
        raise HTTPException(status_code=500, detail=str(e)) from e

    if data is None:
        raise HTTPException(status_code=502,
                            detail="Aunesa devolvió respuesta vacía / no-200")

    items = data if isinstance(data, list) else []
    acumulado = [r for r in items if r.get("informacion") == "Acumulado"]
    return {
        "id_cuenta":   id_cuenta,
        "desde":       desde_q,
        "n_total":     len(items),
        "n_acumulado": len(acumulado),
        "posiciones":  acumulado,
    }


# ── BOLETOS → tab FALTANTES ───────────────────────────────────────────────────
# Lista los boletos en SQL operaciones.negocio_movimientos del rango pedido que
# NO tienen arancel (columna `arancel` null o ≤ 0). Sirve para detectar GAPs
# antes/después de correr el backfill. Filtra futuros DLR (unidad=USDL) —
# no tienen arancel propio y no entrarían igual.

_FALTANTES_COLS = (
    "comprobante", "id_cuenta", "cuenta", "to_char(fecha, 'YYYY-MM-DD') AS fecha",
    "categoria", "op", "informacion", "moneda", "ticker", "unidad",
    "importe", "arancel",
)


@router.get("/aunesa/boletos/faltantes")
def aunesa_boletos_faltantes(
    desde:    str        = Query(..., description="Concertación desde YYYY-MM-DD"),
    hasta:    str        = Query(..., description="Concertación hasta YYYY-MM-DD"),
    id_cuenta: str | None = Query(None, description="Restringir a una cuenta (id)"),
    limit:    int        = Query(2000, ge=1, le=20000, description="Tope filas devueltas"),
) -> dict[str, Any]:
    """Boletos sin arancel en el rango. Devuelve filas + agregado por cuenta+día.

    Considera "sin arancel": `arancel` no existe, es null, o ≤ 0. Match por
    `fecha` (string YYYY-MM-DD, índice). Excluye futuros DLR (USDL)
    automáticamente — los futuros no llevan arancel del proyecto.
    """
    try:
        date.fromisoformat(desde)
        date.fromisoformat(hasta)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"fecha mal formada: {e}") from e
    if desde > hasta:
        raise HTTPException(status_code=400, detail="desde > hasta")

    # Excluye:
    #   - futuros DLR (unidad=USDL) — no arancelables del proyecto.
    #   - ops no arancelables (Cash dividend, Interest payment, cauciones
    #     apertura, suscripciones/rescates FCI, etc) — los confirmó el user
    #     como "tratamiento sin arancel".
    # "Sin arancel" = columna null o ≤ 0. NULL-safe en los $nin → IS NULL OR <> ALL.
    where = (
        "fecha >= %(desde)s AND fecha <= %(hasta)s "
        "AND (arancel IS NULL OR arancel <= 0) "
        "AND (unidad IS NULL OR unidad <> ALL(%(futs)s)) "
        "AND (op IS NULL OR op <> ALL(%(ops)s))"
    )
    params: dict[str, Any] = {
        "desde": desde, "hasta": hasta,
        "futs": list(EXCLUIR_UNIDADES_FUTUROS), "ops": list(OP_NO_ARANCELABLES),
    }
    if id_cuenta:
        where += " AND id_cuenta = %(idc)s"
        params["idc"] = str(id_cuenta)

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        # Boletos detallados (capped).
        cur.execute(
            f"SELECT {', '.join(_FALTANTES_COLS)} FROM negocio_movimientos "
            f"WHERE {where} ORDER BY fecha DESC LIMIT %(lim)s",
            {**params, "lim": limit})
        boletos = cur.fetchall()
        for b in boletos:
            if b.get("importe") is not None:
                b["importe"] = float(b["importe"])
            if b.get("arancel") is not None:
                b["arancel"] = float(b["arancel"])

        # Agregado por (categoria, op) sobre TODO el rango — sin cap, para que
        # el resumen sea fiel aunque la tabla detallada esté truncada. Da el
        # mapa "qué tipos de mov" están rebotando el match, con cuántas cuentas
        # únicas (= ámbito del agujero).
        cur.execute(
            f"SELECT categoria, op, count(*) AS n, "
            f"SUM(abs(COALESCE(importe, 0))) AS importe_abs, "
            f"count(DISTINCT id_cuenta) AS n_cuentas "
            f"FROM negocio_movimientos WHERE {where} "
            f"GROUP BY categoria, op ORDER BY n DESC",
            params)
        por_categoria_op = cur.fetchall()

    n_total = sum(int(r["n"]) for r in por_categoria_op)
    return {
        "desde": desde, "hasta": hasta, "id_cuenta": id_cuenta,
        "n_total":   n_total,
        "truncado":  len(boletos) >= limit,
        "limit":     limit,
        "resumen": [
            {
                "categoria":   r.get("categoria"),
                "op":          r.get("op"),
                "n":           int(r["n"]),
                "importe_abs": float(r.get("importe_abs") or 0.0),
                "n_cuentas":   int(r.get("n_cuentas") or 0),
            }
            for r in por_categoria_op
        ],
        "boletos": boletos,
    }


# ── BOLETOS → tab BACKFILL ────────────────────────────────────────────────────
# Dispara el matching contra Aunesa /informes desde la UI. El job corre en un
# thread daemon del proceso `api.service` y persiste progreso en Mongo cada
# cuenta procesada. Si el proceso se reinicia, el doc queda con `updated_at`
# viejo → el GET de status lo marca como `stale` (5 min sin update) y el front
# permite re-disparar. NO usa systemd unit aparte: si en algún momento el
# volumen lo requiere, mover a `jobs/aranceles.py` + cron es 30 min más.


class BackfillReq(BaseModel):
    desde:   str        = Field(..., description="Concertación desde YYYY-MM-DD")
    hasta:   str        = Field(..., description="Concertación hasta YYYY-MM-DD")
    cuentas: list[str] | None = Field(
        default=None,
        description="Restringir a estas cuentas (id_cuenta). None = todas las del rango.",
    )
    workers: int        = Field(6, ge=1, le=20, description="Threads paralelos contra Aunesa.")
    apply:   bool       = Field(True, description="True = escribe; False = dry-run.")


def _ui_row(doc: dict) -> dict:
    """Parte el doc lógico del job en la fila SQL: columnas fijas + `data` jsonb
    con el resto. Cada escritura reescribe la fila completa (upsert por id)."""
    row = {k: doc.get(k) for k in _UI_FIXED_COLS}
    row["data"] = {k: v for k, v in doc.items() if k not in _UI_FIXED_COLS}
    return row


def _write_job(doc: dict) -> None:
    """Upsert de la fila completa en SQL (best-effort, nunca levanta)."""
    write_native(_JOBS_TABLE, ["id"], [_ui_row(doc)])


def _read_job(job_id: str) -> dict | None:
    """Lee la fila y la reconstruye al shape lógico (columnas fijas + spread del
    jsonb `data`), con los datetimes como objetos (se serializan después)."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {', '.join(_UI_FIXED_COLS)}, data FROM {_JOBS_TABLE} "
            f"WHERE id = %s",
            (job_id,))
        r = cur.fetchone()
    if r is None:
        return None
    data = r.pop("data") or {}
    return {**r, **data}


def _serialize_job(doc: dict | None) -> dict | None:
    """Lo devolvemos sin `id` (es UUID, ya está en `job_id`) y con datetimes ISO."""
    if doc is None:
        return None
    out = {k: v for k, v in doc.items() if k != "id"}
    for k in ("started_at", "updated_at", "finished_at"):
        if isinstance(out.get(k), datetime):
            out[k] = out[k].isoformat()
    return out


def _run_job(doc: dict, req: BackfillReq, desde_d: date, hasta_d: date) -> None:
    """Cuerpo del thread daemon. Reescribe la fila SQL completa a cada cuenta."""
    job_id = doc["id"]

    def on_progress(state: dict[str, Any]) -> None:
        doc["updated_at"]    = datetime.now(UTC)
        doc["cuentas_total"] = state["cuentas_total"]
        doc["cuentas_done"]  = state["cuentas_done"]
        doc["stats"] = {
            "inf":       state["inf"],
            "match":     state["match"],
            "sin_match": state["sin_match"],
            "escritos":  state["escritos"],
        }
        doc["ejemplos"] = state["ejemplos"]
        doc["errores"]  = state["errores"]
        _write_job(doc)

    try:
        run_backfill(
            desde=desde_d, hasta=hasta_d, cuentas=req.cuentas,
            workers=req.workers, apply=req.apply,
            on_progress=on_progress, progress_every=1,
        )
        doc["status"]      = "done"
        doc["finished_at"] = datetime.now(UTC)
        _write_job(doc)
    except Exception as e:
        logger.exception("backfill aranceles job %s falló", job_id)
        doc["status"]      = "error"
        doc["error"]       = str(e)
        doc["finished_at"] = datetime.now(UTC)
        _write_job(doc)


@router.post("/aunesa/boletos/backfill")
def boletos_backfill_start(
    req: BackfillReq = Body(...),
    actor: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Arranca el backfill de aranceles en background. Devuelve `job_id`.

    El frontend hace polling de `GET /aunesa/boletos/backfill/{job_id}` para
    ver el progreso. El job vive en un thread del proceso api.service — si la
    API se reinicia mid-job, el doc queda en `running` sin updates y el GET lo
    marca `stale` (5 min sin update).
    """
    try:
        desde_d = date.fromisoformat(req.desde)
        hasta_d = date.fromisoformat(req.hasta)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"fecha mal formada: {e}") from e
    if desde_d > hasta_d:
        raise HTTPException(status_code=400, detail="desde > hasta")

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    doc = {
        "id":            job_id,
        "status":        "running",
        "actor":         actor,
        "desde":         req.desde,
        "hasta":         req.hasta,
        "cuentas":       req.cuentas,
        "workers":       req.workers,
        "apply":         req.apply,
        "started_at":    now,
        "updated_at":    now,
        "finished_at":   None,
        "cuentas_total": 0,
        "cuentas_done":  0,
        "stats":         {"inf": 0, "match": 0, "sin_match": 0, "escritos": 0},
        "ejemplos":      [],
        "errores":       [],
        "error":         None,
    }
    _write_job(doc)

    threading.Thread(
        target=_run_job, args=(doc, req, desde_d, hasta_d), daemon=True,
        name=f"aranceles-{job_id[:8]}",
    ).start()

    return {"job_id": job_id, "status": "running"}


@router.get("/aunesa/boletos/backfill/{job_id}")
def boletos_backfill_status(job_id: str) -> dict[str, Any]:
    """Estado actual del job. Marca `stale` si lleva > 5 min sin update."""
    doc = _read_job(job_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"job_id desconocido: {job_id}")
    if doc.get("status") == "running":
        updated = doc.get("updated_at")
        if isinstance(updated, datetime):
            # timestamptz vuelve aware desde Postgres; si fuera naive, asumir UTC.
            updated = updated if updated.tzinfo else updated.replace(tzinfo=UTC)
            if datetime.now(UTC) - updated > timedelta(seconds=_STALE_S):
                doc["status"] = "stale"
    return _serialize_job(doc) or {}


@router.get("/aunesa/boletos/backfill")
def boletos_backfill_historial(limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
    """Últimos N jobs (más reciente primero). Para mostrar historial en UI.

    Excluye los campos pesados `ejemplos`/`errores` del jsonb `data` con el
    operador `-` de Postgres (mismo shape que la vieja proyección Mongo)."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {', '.join(_UI_FIXED_COLS)}, "
            f"(data - 'ejemplos' - 'errores') AS data FROM {_JOBS_TABLE} "
            f"ORDER BY started_at DESC LIMIT %s",
            (limit,))
        rows = cur.fetchall()
    out = []
    for r in rows:
        data = r.pop("data") or {}
        out.append(_serialize_job({**r, **data}))
    return [d for d in out if d]
