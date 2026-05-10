"""
diag_boletos_recepcion.py — listar boletos de "Recepción" en
CashFlow.NegocioMovimientos agrupados por la FORMA del informacion
(normalizando códigos únicos como *BIN161200039 o números largos).

Read-only. Reporta cuántos boletos hay de cada subtipo.

Uso:
    python -m scripts.diag_boletos_recepcion
"""
from __future__ import annotations

import re

from core.mongo import get_mongo_client

_PATTERN = "recepci"

# Normalizadores: reemplazan ruido único para que cada subtipo agrupe.
_RE_CODIGO_AST = re.compile(r"\*[A-Z0-9]+")     # *BIN161200039 → *XXX
_RE_NUM_LARGO  = re.compile(r"\d{4,}")          # 161200039     → NNN
_RE_FECHA      = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")  # 25/06/2024 → DD/MM/YYYY
_RE_WS         = re.compile(r"\s+")


def _normalize(s: str | None) -> str:
    if not s:
        return "<null>"
    s2 = _RE_CODIGO_AST.sub("*XXX", s)
    s2 = _RE_FECHA.sub("DD/MM/YYYY", s2)
    s2 = _RE_NUM_LARGO.sub("NNN", s2)
    s2 = _RE_WS.sub(" ", s2).strip()
    return s2


def main():
    client = get_mongo_client()
    coll = client["CashFlow"]["NegocioMovimientos"]

    match_filter = {
        "$or": [
            {"op": {"$regex": _PATTERN, "$options": "i"}},
            {"informacion": {"$regex": _PATTERN, "$options": "i"}},
        ]
    }
    total = coll.count_documents(match_filter)
    print(f"\nTotal boletos con 'recepci' en op O informacion: {total}")
    if total == 0:
        return

    # Pull mínimo (op, informacion, categoria) y normalizar en Python.
    cursor = coll.find(
        match_filter,
        {"_id": 0, "op": 1, "informacion": 1, "categoria": 1},
    )
    bucket: dict[tuple[str, str], dict[str, int]] = {}
    for d in cursor:
        op_n   = _normalize(d.get("op"))
        info_n = _normalize(d.get("informacion"))
        cat    = d.get("categoria") or "<null>"
        key = (op_n, info_n)
        cats = bucket.setdefault(key, {})
        cats[cat] = cats.get(cat, 0) + 1

    # Ordenar por count desc.
    filas = []
    for (op, info), cats in bucket.items():
        n_total = sum(cats.values())
        cat_str = ", ".join(f"{c}={n}" for c, n in sorted(cats.items(),
                                                           key=lambda x: -x[1]))
        filas.append((n_total, op, info, cat_str))
    filas.sort(key=lambda r: -r[0])

    print(f"\n{len(filas)} grupos (después de normalizar códigos únicos):\n")
    print(f"  {'count':>7}  {'op':<35}  informacion / categoria")
    print(f"  {'-' * 7}  {'-' * 35}  {'-' * 60}")
    for n, op, info, cat_str in filas:
        op_disp = (op[:33] + "..") if len(op) > 35 else op
        info_disp = (info[:60] + "..") if len(info) > 62 else info
        print(f"  {n:>7}  {op_disp:<35}  {info_disp}")
        print(f"  {'':>7}  {'':<35}  → {cat_str}")


if __name__ == "__main__":
    main()
