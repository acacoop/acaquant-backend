"""POST/GET/PATCH/DELETE /api/manager/intel/* — ingesta de reportes macro.

Flujo: upload PDF/texto → /intel/extract (Gemini JSON mode devuelve
preview) → user edita → /intel/save persiste. El último IntelDoc
confirmado se inyecta al contexto del asistente vía api/agent/context.py.
"""
from __future__ import annotations

import io
from datetime import UTC, datetime
from datetime import date as _date

from bson import ObjectId
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from api.routers.manager._common import _AR_TZ
from core.mongo import get_mongo_client

router = APIRouter()


def _intel_coll():
    # Writable porque hay inserts/updates/deletes.
    return get_mongo_client()["Manager"]["IntelDocs"]


def _oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"id inválido: {e}") from e


def _serialize_intel_doc(d: dict) -> dict:
    d = dict(d)
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    for k in ("fecha", "created_at", "updated_at", "confirmed_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].astimezone(UTC).isoformat()
        elif isinstance(d.get(k), _date):
            d[k] = d[k].isoformat()
    return d


def _extract_pdf_text(content: bytes) -> str:
    """Parsea un PDF en bytes y devuelve el texto concatenado."""
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail="pypdf no instalado en el server. Correr pip install pypdf.",
        ) from e
    try:
        reader = PdfReader(io.BytesIO(content))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n\n".join(parts).strip()
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"no pude leer el PDF: {e}",
        ) from e


class IntelSaveRequest(BaseModel):
    fuente: str
    fecha: str | None = None  # YYYY-MM-DD
    titulo: str | None = None
    raw_text: str
    extracted: dict


@router.post("/intel/extract")
async def intel_extract(
    fuente: str = Form(...),
    fecha: str | None = Form(None),
    titulo: str | None = Form(None),
    texto: str | None = Form(None),
    pdf: UploadFile | None = File(None),
):
    """Extrae variables estructuradas del texto o PDF. NO persiste todavía;
    devuelve el preview para que el usuario confirme/edite y después guarde."""
    from api.agent.intel_extraction import ExtractionError, extract_intel

    raw_text = (texto or "").strip()
    if pdf is not None:
        content = await pdf.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="PDF > 10 MB")
        pdf_text = _extract_pdf_text(content)
        if raw_text and pdf_text:
            raw_text = pdf_text + "\n\n---\n\n" + raw_text
        elif pdf_text:
            raw_text = pdf_text

    if not raw_text:
        raise HTTPException(status_code=400, detail="no se recibió texto ni PDF con contenido")

    try:
        extracted = extract_intel(raw_text)
    except ExtractionError as e:
        raise HTTPException(status_code=502, detail=f"error extrayendo: {e}") from e

    return {
        "fuente": fuente,
        "fecha": fecha or datetime.now(_AR_TZ).date().isoformat(),
        "titulo": titulo or "",
        "raw_text": raw_text,
        "extracted": extracted,
        "chars": len(raw_text),
    }


@router.post("/intel/save")
def intel_save(req: IntelSaveRequest):
    """Guarda un IntelDoc confirmado en Manager.IntelDocs."""
    doc = {
        "fuente": req.fuente.strip(),
        "fecha": req.fecha or datetime.now(_AR_TZ).date().isoformat(),
        "titulo": (req.titulo or "").strip(),
        "raw_text": req.raw_text,
        "extracted": req.extracted,
        "confirmed": True,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "confirmed_at": datetime.now(UTC),
    }
    result = _intel_coll().insert_one(doc)
    doc["_id"] = result.inserted_id
    return _serialize_intel_doc(doc)


@router.get("/intel")
def intel_list(
    limit: int = Query(50, ge=1, le=200),
    fuente: str | None = Query(None),
):
    """Lista los IntelDocs ordenados por fecha desc."""
    filtro: dict = {}
    if fuente:
        filtro["fuente"] = fuente
    cur = (
        _intel_coll()
        .find(filtro)
        .sort([("fecha", -1), ("created_at", -1)])
        .limit(limit)
    )
    return [_serialize_intel_doc(d) for d in cur]


@router.get("/intel/latest")
def intel_latest():
    """Último IntelDoc confirmado, usado por context.py para inyectar en el prompt."""
    doc = _intel_coll().find_one(
        {"confirmed": True},
        sort=[("fecha", -1), ("created_at", -1)],
    )
    if not doc:
        return {}
    return _serialize_intel_doc(doc)


@router.get("/intel/{intel_id}")
def intel_get(intel_id: str):
    doc = _intel_coll().find_one({"_id": _oid(intel_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="IntelDoc no encontrado")
    return _serialize_intel_doc(doc)


class IntelPatchRequest(BaseModel):
    fuente: str | None = None
    fecha: str | None = None
    titulo: str | None = None
    extracted: dict | None = None


@router.patch("/intel/{intel_id}")
def intel_patch(intel_id: str, req: IntelPatchRequest):
    update: dict = {"updated_at": datetime.now(UTC)}
    if req.fuente is not None:     update["fuente"] = req.fuente.strip()
    if req.fecha is not None:      update["fecha"] = req.fecha
    if req.titulo is not None:     update["titulo"] = req.titulo.strip()
    if req.extracted is not None:  update["extracted"] = req.extracted

    result = _intel_coll().update_one({"_id": _oid(intel_id)}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="IntelDoc no encontrado")
    doc = _intel_coll().find_one({"_id": _oid(intel_id)})
    return _serialize_intel_doc(doc) if doc else {}


@router.delete("/intel/{intel_id}")
def intel_delete(intel_id: str):
    result = _intel_coll().delete_one({"_id": _oid(intel_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="IntelDoc no encontrado")
    return {"ok": True}
