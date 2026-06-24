"""Manager sub-router — Títulos → Renta Variable (CEDEARs: rubro + es_ia).

Editor del catálogo de clasificación de CEDEARs (rubro de negocio + flag ecosistema IA).
Análogo a la segmentación de clientes: el `rubro` NO se escribe libre — se elige del catálogo
`mercado.rubros` o se crea con POST /rubro. Todo SQL-native. Gate `manager_titulos`.

  GET   /api/manager/renta-variable          → grid de CEDEARs (ticker, nombre, rubro, es_ia)
  GET   /api/manager/renta-variable/rubros   → catálogo de rubros (dropdown)
  POST  /api/manager/renta-variable/rubro    → crear un rubro nuevo
  PATCH /api/manager/renta-variable          → setear rubro/es_ia de un CEDEAR (ticker en body)
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from core.postgres import get_pool

router = APIRouter()


@router.get("/renta-variable")
def listar_renta_variable() -> list[dict]:
    """Todos los CEDEARs con su clasificación. `nombre` sale del data jsonb del master."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT ticker, ticker_corto, underlying, activo, rubro, es_ia, "
            "  data->>'nombre' AS nombre "
            "FROM mercado.cedears ORDER BY ticker_corto")
        return cur.fetchall()


@router.get("/renta-variable/rubros")
def listar_rubros() -> list[dict]:
    """Catálogo controlado de rubros (para el dropdown del editor)."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT rubro, es_ia_def FROM mercado.rubros ORDER BY rubro")
        return cur.fetchall()


class _RubroNuevo(BaseModel):
    rubro: str = Field(..., min_length=1, max_length=80)
    es_ia_def: bool = False


@router.post("/renta-variable/rubro")
def crear_rubro(req: _RubroNuevo = Body(...)) -> dict:
    """Crea un rubro nuevo en el catálogo (idempotente — si existe, no rompe)."""
    rub = req.rubro.strip()
    if not rub:
        raise HTTPException(400, "rubro vacío")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mercado.rubros (rubro, es_ia_def) VALUES (%s, %s) "
            "ON CONFLICT (rubro) DO NOTHING", (rub, req.es_ia_def))
        conn.commit()
    return {"ok": True, "rubro": rub}


class _CedearPatch(BaseModel):
    ticker: str = Field(..., max_length=60)     # ticker BYMA completo (PK de mercado.cedears)
    rubro: str | None = None                    # debe existir en mercado.rubros (None = vaciar)
    es_ia: bool | None = None


@router.patch("/renta-variable")
def patch_cedear(req: _CedearPatch = Body(...)) -> dict:
    """Setea rubro/es_ia de un CEDEAR. El rubro NO se escribe libre: tiene que estar en el
    catálogo (si no, 400 → crearlo primero con POST /rubro)."""
    sets: dict = {}
    if "rubro" in req.model_fields_set:
        rub = (req.rubro or "").strip() or None
        if rub is not None:
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1 FROM mercado.rubros WHERE rubro = %s", (rub,))
                if cur.fetchone() is None:
                    raise HTTPException(
                        400, f"rubro inexistente: {rub!r} — crealo primero con POST /rubro")
        sets["rubro"] = rub
    if "es_ia" in req.model_fields_set:
        sets["es_ia"] = req.es_ia
    if not sets:
        raise HTTPException(400, "body sin campos editables (rubro / es_ia)")
    cols = ", ".join(f"{k} = %({k})s" for k in sets)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE mercado.cedears SET {cols} WHERE ticker = %(ticker)s",
                    {**sets, "ticker": req.ticker})
        matched = cur.rowcount
        conn.commit()
    if matched == 0:
        raise HTTPException(404, f"ticker no encontrado: {req.ticker!r}")
    return {"ok": True, "ticker": req.ticker, **sets}
