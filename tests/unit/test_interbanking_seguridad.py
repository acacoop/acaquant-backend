"""Invariantes de SEGURIDAD de la integración con Interbanking.

Son datos bancarios de la casa: cada uno de estos tests congela una decisión que,
si se rompe sin querer, no da error en runtime — simplemente empieza a filtrar
algo o a permitir algo. Por eso están acá y no como comentarios.
"""
from __future__ import annotations

import ast
import pathlib

from api.services import bancos
from core.roles import INVITADO_MODULES

RAIZ = pathlib.Path(__file__).resolve().parents[2]


def test_el_cliente_solo_hace_GET_salvo_el_token():
    """`core/interbanking.py` no puede escribir en Interbanking.

    Hoy la API que contratamos es de solo lectura, pero si mañana el proveedor
    publica endpoints de pago, nada impediría usarlos por descuido. La única
    llamada que no es GET tiene que ser la del token, dentro de `_pedir_token`.
    """
    arbol = ast.parse((RAIZ / "core" / "interbanking.py").read_text(encoding="utf-8"))

    infractores = []
    for fn in ast.walk(arbol):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for nodo in ast.walk(fn):
            if not isinstance(nodo, ast.Call) or not isinstance(nodo.func, ast.Attribute):
                continue
            metodo = nodo.func.attr
            objeto = getattr(nodo.func.value, "id", "")
            if objeto == "requests" and metodo in ("post", "put", "patch", "delete"):
                if fn.name != "_pedir_token":
                    infractores.append(f"{fn.name} → requests.{metodo}")

    assert not infractores, (
        "core/interbanking.py solo puede hacer GET (salvo el POST del token). "
        f"Encontrado: {infractores}"
    )


# La vista dejó de ser 100% read-only el 2026-08-18: se sumó la clasificación de
# GASTOS BANCARIOS, que escribe en tablas NUESTRAS (`bancos.gastos_*`). No toca
# el extracto, no toca el saldo y no sale a internet.
#
# Este set las ENUMERA una por una, a propósito. Un `assert <= {GET, POST, PUT}`
# habría dejado la puerta abierta a cualquier endpoint de escritura futuro sin
# que nadie lo decida; así, sumar uno obliga a tocar este test — que es el
# momento en que alguien se pregunta si corresponde.
ESCRITURAS_PERMITIDAS = {
    ("POST", "/api/back-office/interbanking/gastos/reglas"),
    ("DELETE", "/api/back-office/interbanking/gastos/reglas/{regla_id}"),
    ("PUT", "/api/back-office/interbanking/gastos/movimiento"),
}


def test_el_router_solo_escribe_la_clasificacion_de_gastos():
    from api.routers import interbanking

    escrituras = {
        (m, r.path)
        for r in interbanking.router.routes
        for m in getattr(r, "methods", set())
        if m in ("POST", "PUT", "PATCH", "DELETE")
    }
    assert escrituras == ESCRITURAS_PERMITIDAS, (
        "Cambió el conjunto de endpoints que ESCRIBEN en la tab INTERBANKING.\n"
        f"  esperado: {sorted(ESCRITURAS_PERMITIDAS)}\n"
        f"  encontré: {sorted(escrituras)}\n"
        "Si el endpoint nuevo corresponde, sumalo acá a mano."
    )


def test_toda_escritura_pasa_por_la_allowlist():
    """Ninguna de las tres puede escribir sin permiso.

    El gate NO está en el router como dependencia sino adentro de cada handler
    (`_exigir_escritura`), así que un test que mire `dependencies=` no lo vería.
    Se verifica leyendo la función: cada handler de escritura tiene que llamarlo.
    """
    import inspect

    from api.routers import interbanking

    for r in interbanking.router.routes:
        metodos = getattr(r, "methods", set())
        if not metodos & {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        fuente = inspect.getsource(r.endpoint)
        assert "_exigir_escritura" in fuente, (
            f"{r.path} escribe y no pasa por _exigir_escritura"
        )


def test_la_cuenta_publica_no_filtra_cbu_ni_cuit():
    """El NÚMERO sale entero; el CBU y el CUIT no salen nunca.

    El número se abrió el 2026-08-18 por decisión del user: son las cuentas de la
    casa, las mira el back office detrás de CF Access, y el número es lo que
    copian y pegan en otros sistemas.

    El CBU NO se abrió, y esa es la línea que este test cuida: el número
    IDENTIFICA la cuenta, el CBU es lo que hace falta para TRANSFERIRLE plata.
    Relajar uno no relaja el otro."""
    fila = {
        "id": 1, "bank_number": "034", "bank_name": "Patagonia",
        "account_number": "30410075359500020", "account_type": "CC", "currency": "ARS",
        "account_cbu": "0340000800000012345678", "account_cuit": "30712345678",
        "account_label": "ACA VALORES SA", "activa": True,
    }
    pub = bancos._cuenta_publica(fila)
    plano = str(pub)

    assert "account_cbu" not in pub and "account_cuit" not in pub
    assert fila["account_cbu"] not in plano, "se filtró el CBU"
    assert fila["account_cuit"] not in plano, "se filtró el CUIT de la cuenta"
    assert pub["numero"] == fila["account_number"], "el número tiene que salir ENTERO"


def test_el_cuit_de_la_contraparte_va_enmascarado():
    mov = bancos._movimiento_publico({
        "fecha": None, "fecha_proceso": None, "importe": 1000, "tipo": "C",
        "descripcion_banco": "TRANSFERENCIA", "descripcion_ib": "GIROS/TRF",
        "codigo_operacion_ib": "350", "numero_extracto": "211", "correlativo": 1,
        "comprobante": 0, "cuit_contraparte": "20123456789",
        "denominacion_contraparte": "PROVEEDOR SA",
    })
    assert "20123456789" not in str(mov), "el CUIT de la contraparte salió completo"
    assert mov["contraparte_cuit"] == "20-…-9"


def test_publica_sucursal_y_codigo_de_banco():
    """Las dos columnas que pidió el back office (2026-08-18).

    Estaban GUARDADAS desde la primera corrida y no se publicaban. El test las
    fija: son parte del contrato de la vista, no un extra que se pueda perder en
    un refactor de la proyección.
    """
    mov = bancos._movimiento_publico({
        "fecha": None, "fecha_proceso": None, "importe": 1, "tipo": "D",
        "codigo_operacion_ib": "350", "codigo_operacion_banco": "00108",
        "sucursal": " 304 ",
    })
    assert mov["codigo_banco"] == "00108"
    assert mov["sucursal"] == "304", "la sucursal viene con espacios de la API"


def test_el_cuit_corto_o_vacio_no_se_publica():
    for v in (None, "", "123"):
        assert bancos._cuit_enmascarado(v) is None


def test_back_office_jamas_al_portal_invitado():
    """REGLA #8. El invitado ve mercado y research; los bancos de la casa, nunca."""
    assert "back-office" not in INVITADO_MODULES
