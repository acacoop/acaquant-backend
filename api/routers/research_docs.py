"""Router /api/research-docs — LECTURA de los documentos manuales de la vista
REPORTES FINANCIEROS (Research). Gate módulo `research` (interno, JAMÁS invitado —
REGLA #8). La ESCRITURA (subir/borrar) vive en api/routers/manager/documentos.py
(gate manager). docs/RESEARCH_FRED.md.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from api.auth import require_module
from api.services import research_docs_sql as svc

router = APIRouter(
    prefix="/api/research-docs",
    tags=["Research Docs"],
    dependencies=[Depends(require_module("research"))],
)


@router.get("/list")
def list_docs() -> dict:
    """Metadata de los documentos (sin los bytes del PDF)."""
    return svc.listar()


@router.get("/{doc_id}/pdf")
def get_pdf(doc_id: int) -> Response:
    """Sirve el PDF embebible (inline). El front lo mete en un iframe."""
    out = svc.get_pdf(doc_id)
    if out is None:
        raise HTTPException(404, "Documento sin PDF")
    data, mime, nombre = out
    return Response(content=data, media_type=mime,
                    headers={"Content-Disposition": f'inline; filename="{nombre}"'})
