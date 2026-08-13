"""El DDL de la tabla de params se ejecuta UNA vez por proceso, no por lectura.

`_get_param_doc` se llama 8 veces en cada request de /api/derivados/agro —el
endpoint #1 de la plataforma— y cada una corría un `CREATE TABLE IF NOT EXISTS`
antes del SELECT: 8 viajes a la base por request para preguntar si existe una
tabla que existe desde el día uno (~68ms con el peaje medido de 8.5ms).

Lo encontró cProfile: 66 llamadas a `psycopg.connection.wait` en un solo
request, que es donde el driver se queda ESPERANDO a la base.
"""
from __future__ import annotations

from api.services import camara_cereales as cc


class _CursorFalso:
    def __init__(self) -> None:
        self.sqls: list[str] = []

    def execute(self, sql, params=None):
        self.sqls.append(sql)


def test_ddl_solo_la_primera_vez(monkeypatch):
    # Arranca como un proceso nuevo.
    monkeypatch.setattr(cc, "_tabla_lista", False)
    cur = _CursorFalso()
    cc._ensure_tasas_table(cur)
    cc._ensure_tasas_table(cur)
    cc._ensure_tasas_table(cur)
    assert len(cur.sqls) == 1, "el DDL tiene que correr una sola vez por proceso"
    assert "CREATE TABLE IF NOT EXISTS" in cur.sqls[0]


def test_si_el_ddl_falla_no_queda_marcado(monkeypatch):
    """Si la creación falla, el flag NO se prende: el próximo intento reintenta.
    (Se prende DESPUÉS del execute, no antes — este test lo fija.)"""
    monkeypatch.setattr(cc, "_tabla_lista", False)

    class _Explota:
        def execute(self, sql, params=None):
            raise RuntimeError("sin permisos")

    try:
        cc._ensure_tasas_table(_Explota())
    except RuntimeError:
        pass
    assert cc._tabla_lista is False

    cur = _CursorFalso()
    cc._ensure_tasas_table(cur)
    assert len(cur.sqls) == 1, "tras un fallo, el siguiente intento sí ejecuta"
