"""Clasificación de GASTOS BANCARIOS: qué movimiento cobró el banco.

Esto decide un número que el back office lee como plata, y falla **en silencio**:
si la clasificación se rompe no salta ninguna excepción — la columna muestra otro
total, y nadie tiene con qué darse cuenta. Por eso la lógica es PURA (sin base,
sin red) y vive con tests propios.

Las dos capas y su orden de precedencia son el modelo entero:
  · REGLA    = el conocimiento durable ("todo lo que tenga el código 830").
  · OVERRIDE = el parche puntual sobre UN movimiento. **Gana siempre.**
"""
from __future__ import annotations

import pytest

from api.services.bancos import CAMPOS_REGLA, clasificar

MOV = {
    "mov_hash": "h1", "cuenta_id": 1, "importe": 1500.0, "tipo": "D",
    "codigo_operacion_ib": "830", "codigo_operacion_banco": "00108",
    "descripcion_banco": "COMISION MANTENIMIENTO CUENTA",
    "descripcion_ib": "GASTOS",
}


def _regla(**kw):
    return {"id": 1, "campo": "codigo_ib", "operador": "igual", "valor": "830",
            "activa": True, **kw}


def test_regla_por_codigo_exacto():
    r = clasificar([MOV], [_regla()], {})["h1"]
    assert r["es_gasto"] is True and r["origen"] == "regla" and r["regla"] == 1


def test_regla_por_texto_contenido():
    """Con lo que van a arrancar: todavía no saben qué códigos usa cada banco,
    pero sí reconocen la palabra en la descripción."""
    r = _regla(campo="descripcion_banco", operador="contiene", valor="comision")
    assert clasificar([MOV], [r], {})["h1"]["es_gasto"] is True


def test_contiene_no_matchea_lo_que_no_contiene():
    r = _regla(campo="descripcion_banco", operador="contiene", valor="impuesto")
    assert clasificar([MOV], [r], {})["h1"]["es_gasto"] is False


def test_igual_no_matchea_parcialmente():
    """`igual` es igual. Si '83' matcheara a '830', una regla estrecha se comería
    media tabla sin que nadie lo pida."""
    assert clasificar([MOV], [_regla(valor="83")], {})["h1"]["es_gasto"] is False


def test_ignora_mayusculas_y_espacios():
    """La API manda '00108 ' y 'COMISION…'; nadie va a escribir una regla
    replicando esos espacios."""
    r = _regla(campo="descripcion_banco", operador="contiene", valor="  CoMiSiOn  ")
    assert clasificar([MOV], [r], {})["h1"]["es_gasto"] is True


def test_una_regla_inactiva_no_clasifica():
    assert clasificar([MOV], [_regla(activa=False)], {})["h1"]["es_gasto"] is False


def test_sin_reglas_no_es_gasto_y_sin_origen():
    r = clasificar([MOV], [], {})["h1"]
    assert r["es_gasto"] is False and r["origen"] is None


def test_el_override_GANA_sobre_la_regla():
    """EL invariante. Si una persona lo decidió, la máquina no se lo da vuelta."""
    r = clasificar([MOV], [_regla()], {"h1": False})["h1"]
    assert r["es_gasto"] is False, "la regla pisó una marca manual"
    assert r["origen"] == "manual"


def test_el_override_tambien_marca_lo_que_ninguna_regla_agarra():
    """El caso poco frecuente: no hay regla, el usuario lo marca en el momento."""
    r = clasificar([MOV], [], {"h1": True})["h1"]
    assert r["es_gasto"] is True and r["origen"] == "manual"


def test_un_campo_que_no_existe_no_matchea_nunca():
    """`campo` lo escribe un usuario. Si viajara a un WHERE sería una inyección;
    acá simplemente no matchea."""
    r = _regla(campo="importe; DROP TABLE bancos.movimientos", operador="igual", valor="1500")
    assert clasificar([MOV], [r], {})["h1"]["es_gasto"] is False


def test_una_regla_con_valor_vacio_no_agarra_todo():
    """Con `contiene`, un valor vacío está contenido en CUALQUIER texto: sin este
    corte, guardar una regla a medias marcaría todos los movimientos como gasto."""
    r = _regla(campo="descripcion_banco", operador="contiene", valor="   ")
    assert clasificar([MOV], [r], {})["h1"]["es_gasto"] is False


@pytest.mark.parametrize("campo", sorted(CAMPOS_REGLA))
def test_todos_los_campos_declarados_funcionan(campo):
    """Si se suma un campo a CAMPOS_REGLA y la columna no existe en el
    movimiento, la regla nunca matchearía y nadie se enteraría."""
    valor = MOV[CAMPOS_REGLA[campo]]
    r = _regla(campo=campo, operador="igual", valor=valor)
    assert clasificar([MOV], [r], {})["h1"]["es_gasto"] is True, (
        f"el campo '{campo}' está declarado pero no matchea ni con su propio valor"
    )
