"""El sync de Interbanking NO puede pisar lo que escribió una persona.

Congela el incidente del 2026-08-20: `account_label` estaba en el `DO UPDATE` del
upsert de `bancos.cuentas`, así que **cada corrida del job la reemplazaba** por la
denominación del titular que manda Interbanking. El back office había nombrado 38
cuentas a mano («PATA ACDI», «VALO CERA ARS») y las usaba para relacionarlas con
el mayor contable: lo que se borraba no era una decoración, era un MAPEO. Y se
borraba sin un solo error.

El test mira el SQL en vez de la base porque es ahí donde vive la decisión: la
lista de columnas del `DO UPDATE` ES la política de «qué manda el proveedor y qué
mandamos nosotros». Agregar una columna a esa lista es un cambio de política, y
tiene que costar romper un test.
"""
from __future__ import annotations

import inspect
import re

from jobs import interbanking_sync


def _set_del_upsert() -> str:
    """El bloque `DO UPDATE SET … RETURNING` del upsert de cuentas."""
    fuente = inspect.getsource(interbanking_sync.sincronizar_cuentas)
    m = re.search(r"DO UPDATE SET(.*?)RETURNING", fuente, re.S)
    assert m, "no encontré el DO UPDATE SET del upsert de bancos.cuentas"
    # Sin los comentarios de Python, que mencionan las columnas al explicarlas.
    return "\n".join(l for l in m.group(1).splitlines() if not l.strip().startswith("#"))


def test_el_job_no_pisa_la_etiqueta_manual():
    """`account_label` es el nombre OPERATIVO que pone el back office."""
    assert "account_label" not in _set_del_upsert()


def test_el_job_no_pisa_el_codigo_contable():
    """`codigo_contable` liga la cuenta con el mayor y se carga a mano: si el job
    lo pisara, la conciliación empezaría a comparar contra la cuenta equivocada."""
    assert "codigo_contable" not in _set_del_upsert()


def test_el_job_no_pisa_el_origen():
    """Una cuenta cargada a mano sigue marcada como manual aunque Interbanking
    después la informe."""
    assert "origen" not in _set_del_upsert()


def test_el_job_sigue_actualizando_lo_que_es_del_proveedor():
    """El contrapeso: esto NO es «no actualices nada». Los datos que son del
    banco tienen que seguir refrescándose, si no el arreglo de arriba congelaría
    la cuenta entera."""
    bloque = _set_del_upsert()
    for columna in ("bank_name", "account_cbu", "account_cuit", "ultima_vez", "raw"):
        assert columna in bloque, f"{columna} debería seguir actualizándose"


def test_la_etiqueta_se_toma_al_descubrir_la_cuenta():
    """En el INSERT sí va: una cuenta nueva estrena el nombre del proveedor, que
    es mejor que quedar sin nombre hasta que alguien la bautice."""
    fuente = inspect.getsource(interbanking_sync.sincronizar_cuentas)
    insert = fuente[:fuente.index("ON CONFLICT")]
    assert "account_label" in insert
