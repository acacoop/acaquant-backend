"""Cuántas queries cuesta cada endpoint de INTERBANKING. Congelado.

## Por qué un test y no una revisión de vez en cuando

Contra Supabase, **cada roundtrip cuesta un peaje fijo de ~8,5ms** (medido en el
Droplet, `scripts/diag_ubicacion`) y ese peaje es 100% DISTANCIA — el pooler y
Postgres aportan ~0. O sea: **lo que importa NO es el plan de la query sino
cuántas son.** Una query de más sobre una tabla de 30 filas cuesta lo mismo que
una sobre una de 30 millones.

Y las queries de más no se notan: nadie ve 8ms. Se acumulan de a una, cada vez
que alguien agrega un campo a la respuesta llamando otra vez a la misma función
—que es exactamente lo que había pasado acá: `vista` leía los movimientos crudos
y el catálogo de reglas DOS veces cada uno, 4 queries donde alcanzaban 2.

Este test no prohíbe crecer: obliga a que crecer sea una decisión. Si el número
sube, alguien tiene que venir acá y cambiarlo a mano.

## Los topes

Las dos vistas POLLEAN cada 60s y por usuario, así que el costo se multiplica por
gente conectada. Estos números son con datos (una cuenta, un movimiento, una
regla): el camino completo, sin cortes tempranos.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from api.services import bancos as svc

# Si esto sube, NO es un test roto: es una query nueva por request. Subilo a
# mano solo si esa query hace falta de verdad.
TOPES = {"consolidado": 10, "vista": 9}

FECHA = date(2026, 8, 14)
_CUENTA = {"id": 1, "bank_number": "034", "bank_name": "Pata", "account_number": "20",
           "account_type": "CC", "currency": "ARS", "account_label": "A", "activa": True,
           "saldo_apertura": 1.0, "saldo_cierre": 2.0, "total_movimientos": 1,
           "saldo_banco": 2.0}
_CRUDO = {"mov_hash": "h1", "cuenta_id": 1, "importe": 10.0, "tipo": "D",
          "codigo_operacion_ib": "1", "codigo_operacion_banco": "1",
          "descripcion_banco": "COMISION", "descripcion_ib": "IVA"}
_PUB = {**_CRUDO, "fecha": FECHA, "fecha_proceso": None, "numero_extracto": "1",
        "correlativo": 1, "comprobante": 1, "sucursal": "1",
        "cuit_contraparte": None, "denominacion_contraparte": None}
_DIA = {"fecha": FECHA, "saldo_apertura": 1.0, "saldo_cierre": 2.0,
        "total_creditos": 1.0, "total_debitos": 1.0, "total_movimientos": 1,
        "cierra": True, "diferencia": 0.0, "sincronizado_at": datetime(2026, 8, 14),
        "movimientos_base": 1}
# El catálogo del desglose vive en la base desde el 2026-08-18: es UNA query más
# por request en cada vista (por eso los topes subieron de 9/8 a 10/9), y es el
# precio de que el equipo pueda editar las columnas sin pedir un deploy.
_BALDE = {"clave": "iva", "etiqueta": "IVA", "grupo": "concepto", "orden": 10,
          "matcher_id": 1, "campo": "descripcion_ib", "operador": "igual",
          "valor": "IVA"}
_REGLA = {"id": 1, "campo": "descripcion_banco", "operador": "contiene",
          "valor": "COMISION", "nota": "", "activa": True, "creado_por": "x",
          "creado_at": datetime(2026, 8, 14)}


def _contar(monkeypatch, fn, *args) -> list[str]:
    """Corre el endpoint con la base mockeada y devuelve las queries que hizo."""
    hechas: list[str] = []

    def _q(sql, params=None):
        t = " ".join(str(sql).split())
        hechas.append(t)
        if "FROM bancos.cuentas" in t:
            return [_CUENTA]
        if "gastos_baldes" in t:
            return [_BALDE]
        if "gastos_reglas" in t:
            return [_REGLA]
        if "gastos_overrides" in t:
            return []
        if "FROM bancos.movimientos" in t and "mov_hash, cuenta_id" in t:
            return [_CRUDO]
        if "FROM bancos.movimientos" in t:
            return [_PUB]
        if "extracto_dia e" in t:
            return [_DIA]
        if "sync_log" in t:
            return [{"corrida_at": datetime(2026, 8, 14), "cuentas": 1, "con_error": 0}]
        return []

    def _exec(sql, params=None):
        hechas.append(" ".join(str(sql).split()))
        return 1

    monkeypatch.setattr(svc, "_q", _q)
    monkeypatch.setattr(svc, "_exec", _exec)
    monkeypatch.setattr("core.roles.get_user_role", lambda *a, **k: "sales")
    fn(*args)
    return hechas


@pytest.mark.parametrize("nombre,fn,args", [
    ("consolidado", svc.consolidado, ("x@y", FECHA)),
    ("vista", svc.vista, ("x@y", 1, FECHA)),
])
def test_no_crece_la_cantidad_de_queries(monkeypatch, nombre, fn, args):
    hechas = _contar(monkeypatch, fn, *args)
    assert len(hechas) <= TOPES[nombre], (
        f"`{nombre}` pasó de {TOPES[nombre]} a {len(hechas)} queries por request.\n"
        f"La vista pollea cada 60s y por usuario, y cada roundtrip son ~8,5ms de "
        f"peaje fijo.\nQueries:\n  " + "\n  ".join(q[:90] for q in hechas)
    )


@pytest.mark.parametrize("nombre,fn,args", [
    ("consolidado", svc.consolidado, ("x@y", FECHA)),
    ("vista", svc.vista, ("x@y", 1, FECHA)),
])
def test_ninguna_query_se_repite(monkeypatch, nombre, fn, args):
    """Una query IDÉNTICA dos veces en el mismo request es siempre un descuido:
    el resultado ya estaba en memoria. Es la forma en que esto crece sin que
    nadie lo note — se agrega un campo a la respuesta llamando otra vez a la
    función que ya lo trajo."""
    hechas = _contar(monkeypatch, fn, *args)
    repetidas = {q for q in hechas if hechas.count(q) > 1}
    assert not repetidas, (
        f"`{nombre}` repite {len(repetidas)} query(s) en el mismo request:\n  "
        + "\n  ".join(q[:90] for q in repetidas)
    )
