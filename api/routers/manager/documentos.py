"""Manager sub-router — carga de documentos manuales de REPORTES FINANCIEROS.

Tab `/manager → DOCUMENTOS`: subir PDFs (ej. el "Semanal") y comentarios que NO
llegan por mail. Gate `manager` (se aplica en el paquete). La LECTURA la hace la
vista Research (api/routers/research_docs.py, gate research). docs/RESEARCH_FRED.md.

Endpoints (prefix /api/manager lo agrega el paquete):
  GET    /documentos        → lista (metadata)
  POST   /documentos        → alta (PDF en base64 o comentario)
  DELETE /documentos/{id}   → baja
"""
from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import research_docs_sql as svc

router = APIRouter()


@router.get("/documentos")
def list_documentos() -> dict:
    return svc.listar()


class _DocNew(BaseModel):
    titulo: str = Field(..., min_length=1, max_length=300)
    fecha: str = Field(..., description="YYYY-MM-DD del contenido")
    tipo: str = Field(..., pattern="^(pdf|comentario)$")
    fuente: str | None = Field(None, max_length=120)
    comentario: str | None = None
    archivo_b64: str | None = Field(None, description="PDF en base64 (tipo=pdf)")
    nombre_archivo: str | None = Field(None, max_length=300)


@router.post("/documentos")
def add_documento(req: _DocNew = Body(...), actor: str = Depends(get_user_email)) -> dict:
    archivo: bytes | None = None
    if req.tipo == "pdf":
        if not req.archivo_b64:
            raise HTTPException(400, "Falta el archivo PDF")
        b64 = req.archivo_b64.split(",", 1)[-1]  # tolera prefijo data:...;base64,
        try:
            archivo = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise HTTPException(400, "El archivo no es base64 válido") from e
    try:
        return svc.crear(
            titulo=req.titulo, fecha=req.fecha, tipo=req.tipo, fuente=req.fuente,
            comentario=req.comentario, archivo=archivo,
            mime="application/pdf" if req.tipo == "pdf" else None,
            nombre_archivo=req.nombre_archivo, autor=actor or "",
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/documentos/{doc_id}")
def delete_documento(doc_id: int) -> dict:
    try:
        return svc.borrar(doc_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
