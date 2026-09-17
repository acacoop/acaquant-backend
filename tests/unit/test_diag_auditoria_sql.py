from __future__ import annotations

import re

from scripts import diag_auditoria_sql as diag


class _Descripcion:
    def __init__(self, name: str):
        self.name = name


class _Cursor:
    description = [_Descripcion("valor")]

    def __init__(self, fallar_en: str | None = None):
        self.fallar_en = fallar_en
        self.sqls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql: str, params=()):
        self.sqls.append(sql.strip())
        if self.fallar_en and self.fallar_en in sql:
            raise RuntimeError("sin permiso")

    def fetchall(self):
        return [(1,)]


class _Conexion:
    def __init__(self, fallar_en: str | None = None):
        self.fallar_en = fallar_en
        self.cursores: list[_Cursor] = []
        self.rollbacks = 0
        self.cerrada = False

    def cursor(self):
        cursor = _Cursor(self.fallar_en)
        self.cursores.append(cursor)
        return cursor

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.cerrada = True


def test_consultas_solo_usan_catalogo_y_estadisticas():
    prohibido = re.compile(r"\b(insert|update|delete|merge|alter|drop|create|truncate)\b", re.I)

    for consulta in diag.CONSULTAS:
        assert consulta.sql.lstrip().upper().startswith("SELECT")
        assert not prohibido.search(consulta.sql), consulta.nombre
        assert "pg_stat_statements" not in consulta.sql.lower()


def test_fotografia_aísla_cada_seccion_en_read_only(monkeypatch):
    conexion = _Conexion()
    monkeypatch.setattr(diag, "_connect", lambda: conexion)
    monkeypatch.setattr(diag.schema_sql, "schemas", lambda: frozenset({"mercado"}))
    monkeypatch.setattr(
        diag.schema_sql, "tablas", lambda: frozenset({"mercado.market_snapshot"})
    )

    resultado = diag.fotografiar()

    assert resultado["errores"] == {}
    assert set(resultado["secciones"]) == {q.nombre for q in diag.CONSULTAS}
    assert resultado["schema_esperado"]["tablas"] == ["mercado.market_snapshot"]
    assert conexion.rollbacks == len(diag.CONSULTAS)
    assert conexion.cerrada
    assert all(cursor.sqls[0] == "BEGIN READ ONLY" for cursor in conexion.cursores)
    assert all("statement_timeout" in cursor.sqls[1] for cursor in conexion.cursores)
    assert all("lock_timeout" in cursor.sqls[2] for cursor in conexion.cursores)


def test_un_error_de_catalogo_no_interrumpe_las_otras_secciones(monkeypatch):
    conexion = _Conexion(fallar_en="FROM pg_sequence")
    monkeypatch.setattr(diag, "_connect", lambda: conexion)
    monkeypatch.setattr(diag.schema_sql, "schemas", lambda: frozenset({"mercado"}))
    monkeypatch.setattr(diag.schema_sql, "tablas", frozenset)

    resultado = diag.fotografiar()

    assert resultado["errores"]["secuencias"]["tipo"] == "RuntimeError"
    assert "servidor" in resultado["secciones"]
    assert "triggers" in resultado["secciones"]
    assert conexion.rollbacks == len(diag.CONSULTAS)