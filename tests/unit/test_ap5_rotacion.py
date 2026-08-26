"""El acumulado que ROTA: T-1 + DIARIA = ACUMULADO.

Lo que congelan estos tests es UN invariante, y es el que hace que el modelo
sea usable: **re-correr el job no puede duplicar el acumulado**. Sin él, el
número queda al doble y nada falla — la pantalla se ve igual y el total sigue
siendo plausible.
"""
from datetime import date

import pytest

from api.services.ap5_rotacion import Estado, rotar

L, M, X = date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26)


def test_la_suma_es_la_regla():
    e = Estado(fecha=M, t1_pesos=1000.0, diaria_pesos=300.0)
    assert e.acum_pesos == 1300.0


def test_dia_nuevo_ROTA_el_acumulado_de_ayer_a_t1():
    previo = Estado(fecha=L, t1_pesos=1000.0, diaria_pesos=300.0)  # acum 1300
    e, motivo = rotar(previo, M, 50.0, 0.0)
    assert motivo == "rota"
    assert e.t1_pesos == 1300.0, "el acumulado de ayer tiene que ser el t1 de hoy"
    assert e.diaria_pesos == 50.0
    assert e.acum_pesos == 1350.0


@pytest.mark.parametrize("veces", [2, 3, 4])
def test_RE_CORRER_EL_MISMO_DIA_NO_DUPLICA(veces):
    """El 2026-08-25 el job corrió CUATRO veces. Si cada corrida rotara, el
    acumulado quedaría 4× más grande — y sin fallar."""
    e = Estado(fecha=L, t1_pesos=1000.0, diaria_pesos=300.0)
    e, _ = rotar(e, M, 50.0, 0.0)
    esperado = e.acum_pesos
    for _ in range(veces - 1):
        e, motivo = rotar(e, M, 50.0, 0.0)
        assert motivo == "recalcula"
    assert e.acum_pesos == esperado
    assert e.t1_pesos == 1300.0, "el t1 no se toca en una re-corrida"


def test_mismo_dia_SI_refresca_la_diaria_corregida():
    """La cámara puede corregir un día. Recalcular no es 'no hacer nada'."""
    e = Estado(fecha=M, t1_pesos=1000.0, diaria_pesos=50.0)
    e, motivo = rotar(e, M, 80.0, 0.0)
    assert motivo == "recalcula"
    assert e.diaria_pesos == 80.0
    assert e.acum_pesos == 1080.0


def test_un_dia_VIEJO_no_mueve_nada():
    """`--fecha` de un día pasado es normal (re-pedirle a la cámara un día que
    se borró). Rotar hacia atrás dejaría el acumulado contando otra cosa."""
    e = Estado(fecha=X, t1_pesos=1300.0, diaria_pesos=50.0)
    nuevo, motivo = rotar(e, L, 999.0, 999.0)
    assert motivo == "ignora"
    assert nuevo == e


def test_las_dos_monedas_rotan_por_separado():
    e = Estado(fecha=L, t1_pesos=100.0, t1_mtr=7.0,
               diaria_pesos=10.0, diaria_mtr=3.0)
    e, _ = rotar(e, M, 1.0, 0.5)
    assert (e.t1_pesos, e.t1_mtr) == (110.0, 10.0)
    assert (e.acum_pesos, e.acum_mtr) == (111.0, 10.5)


def test_una_cuenta_sin_estado_previo_arranca_en_cero():
    e, motivo = rotar(Estado(fecha=None), M, 42.0, 0.0)
    assert motivo == "rota"
    assert e.t1_pesos == 0.0
    assert e.acum_pesos == 42.0
