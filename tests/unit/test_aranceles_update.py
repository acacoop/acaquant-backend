"""Guard del UPDATE de aranceles: que no se reescriba lo que no cambió.

`jobs.aranceles` corre CADA 30' sobre una ventana con solapamiento, así que la
mayoría de los boletos se reescribían con el mismo valor. Cada reescritura
inútil cuesta una versión nueva de fila + todos los índices + WAL + trabajo
para autovacuum: medido con pg_stat_statements el 2026-08-13, 517.183 UPDATE ×
16,0ms = 8.271s, el #2 de toda la base.

Estos tests fijan la FORMA del SQL (no hacen falta credenciales). El
comportamiento se verificó contra un Postgres 16 real: reescribir lo idéntico
no toca ninguna fila, cambiar arancel o el jsonb sí, y el jsonb con las claves
en otro orden NO cuenta como cambio (jsonb compara canónicamente).
"""
from __future__ import annotations

from api.services.aunesa_aranceles import _SQL_UPDATE


def test_update_no_reescribe_lo_igual():
    """Sin el guard, el job reescribe todo lo que toca aunque no haya cambiado."""
    sql = " ".join(_SQL_UPDATE.split())
    assert "IS DISTINCT FROM" in sql
    assert "arancel IS DISTINCT FROM" in sql
    assert "aranceles IS DISTINCT FROM" in sql


def test_update_castea_el_jsonb():
    """El cast NO es cosmético: `Json()` adapta a `json` y el operador
    `jsonb = json` NO EXISTE. Sin `::jsonb` el UPDATE tira UndefinedFunction y
    el job se cae cada 30 minutos. En el SET no hace falta (Postgres castea al
    asignar), en la comparación sí."""
    assert "%(aranceles)s::jsonb" in _SQL_UPDATE


def test_update_sigue_filtrando_por_comprobante():
    """El guard se SUMA al WHERE original — no lo reemplaza. Si se perdiera el
    filtro por comprobante, el UPDATE pisaría la tabla entera."""
    sql = " ".join(_SQL_UPDATE.split())
    assert "WHERE comprobante = %(comprobante)s" in sql
    assert sql.index("comprobante = %(comprobante)s") < sql.index("IS DISTINCT FROM")
