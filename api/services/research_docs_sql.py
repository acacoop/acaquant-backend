"""api/services/research_docs_sql.py — documentos manuales de REPORTES FINANCIEROS.

Puro (sin FastAPI). Lo escribe Manager (upload/borrar) y lo lee la vista Research
(listar/get_pdf). El PDF vive como bytea en research.documentos (ver schema).
docs/RESEARCH.md.
"""
from __future__ import annotations

import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)

_MAX_BYTES = 25 * 1024 * 1024  # 25 MB por documento (tope defensivo)


def listar() -> dict:
    """Metadata de los documentos (SIN los bytes del PDF). Más nuevo primero."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, titulo, fecha, tipo, fuente, comentario, nombre_archivo,"
                " autor, (archivo IS NOT NULL) AS tiene_pdf,"
                " octet_length(archivo) AS bytes, created_at"
                " FROM research.documentos ORDER BY fecha DESC, id DESC"
            )
            filas = cur.fetchall()
    except Exception as e:
        logger.warning("research_docs_sql.listar falló (%s)", e)
        return {"documentos": []}
    return {"documentos": [{
        "id": r[0], "titulo": r[1], "fecha": r[2].isoformat() if r[2] else None,
        "tipo": r[3], "fuente": r[4], "comentario": r[5], "nombre_archivo": r[6],
        "autor": r[7], "tiene_pdf": r[8], "bytes": r[9],
        "created_at": r[10].isoformat() if r[10] else None,
    } for r in filas]}


def get_pdf(doc_id: int) -> tuple[bytes, str, str] | None:
    """(bytes, mime, nombre_archivo) del PDF, o None si no existe / no tiene archivo."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT archivo, mime, nombre_archivo FROM research.documentos WHERE id = %s",
                (doc_id,),
            )
            row = cur.fetchone()
    except Exception as e:
        logger.warning("research_docs_sql.get_pdf falló (%s)", e)
        return None
    if not row or row[0] is None:
        return None
    data = bytes(row[0])
    return data, (row[1] or "application/pdf"), (row[2] or f"documento_{doc_id}.pdf")


def crear(*, titulo: str, fecha: str, tipo: str, fuente: str | None = None,
          comentario: str | None = None, archivo: bytes | None = None,
          mime: str | None = None, nombre_archivo: str | None = None,
          autor: str = "") -> dict:
    """Inserta un documento. tipo='pdf' requiere archivo; 'comentario' requiere texto."""
    titulo = (titulo or "").strip()
    if not titulo:
        raise ValueError("El título es obligatorio")
    if tipo not in ("pdf", "comentario"):
        raise ValueError("tipo debe ser 'pdf' o 'comentario'")
    if tipo == "pdf":
        if not archivo:
            raise ValueError("Falta el archivo PDF")
        if len(archivo) > _MAX_BYTES:
            raise ValueError(f"El archivo supera el máximo ({_MAX_BYTES // (1024 * 1024)} MB)")
    elif not (comentario or "").strip():
        raise ValueError("El comentario no puede estar vacío")

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO research.documentos (titulo, fecha, tipo, fuente, comentario,"
            " archivo, mime, nombre_archivo, autor)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (titulo, fecha, tipo, (fuente or None), (comentario or None),
             archivo, (mime or None), (nombre_archivo or None), autor or None),
        )
        new_id = cur.fetchone()[0]
    return {"ok": True, "id": new_id}


def borrar(doc_id: int) -> dict:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM research.documentos WHERE id = %s", (doc_id,))
        n = cur.rowcount
    if not n:
        raise ValueError("Documento no encontrado")
    return {"ok": True, "id": doc_id}
