"""El .xlsx que genera `scripts/export_mayor_conciliador` lo tiene que poder leer
el CONCILIADOR sin tocarle nada.

⚠️ No se testea "que el archivo se genere": se testea que **los parsers REALES del
conciliador** (`_movimientos_del_mayor`, `_saldo_del_mayor`) lean de esa grilla lo
que corresponde. Un test que solo mirara las columnas generadas estaría probando
la copia del formato contra sí misma y pasaría igual el día que el backend cambie
de criterio.

Los importes son los 5 movimientos REALES de `101010100002 Banco Patagonia CC
304-100753595-000` del 19/08/2026 (corrida de `contabilidad/registrosContables`),
que netean 18.506.450,73 — el mismo número que muestra la hoja «Por cuenta».
"""
from __future__ import annotations

from api.services.bancos import _movimientos_del_mayor, _saldo_del_mayor
from scripts.export_mayor_conciliador import _grilla, _movimientos_de_la_cuenta

CTA = "101010100002"
NOM = "Banco Patagonia CC 304-100753595-000"
NETO_DEL_DIA = 18506450.73
INICIAL = 12345678.90


def _asiento(valuacion: str, comprobante: str, referencia: str, numero: str,
             *, cuenta: str = CTA, nombre: str = NOM) -> dict:
    return {
        "asientoID": "x", "numero": numero, "fechaAlta": "20/08/2026",
        "fechaConciliacion": "19/08/2026", "referencia": referencia,
        "movimientos": [{
            "movimientoID": "m", "codigoCuenta": cuenta, "nombreCuenta": nombre,
            "codigoUnidad": "ARS", "cantidad": valuacion, "factor": "1",
            "valuacion": valuacion, "cuentaID": "CTB", "numeroOperacion": "",
            "comprobante": comprobante, "codigoExp": "", "referencia": "detalle",
        }],
    }


REGISTROS = [
    _asiento("18711185.35", "NT 2026010519",
             "[Op. 1135000] Recepción de fondos de contraparte.", "-4893409"),
    _asiento("882442.15", "CD 2026004459",
             "[Op. 1135001] Depósito - Depósito CD 2026004459- Cta. 243/GRAL", "-4893414"),
    _asiento("132384.21", "CD 2026004459",
             "[Op. 1135002] Depósito - Depósito CD 2026004459- Cta. 243/GRAL", "-4893414"),
    _asiento("-119560.98", "", "[Op. 1135003] ([Cod.39] 00001-00 GASTOS BAN", "-4893659"),
    _asiento("-1100000.00", "", "Asiento contable", "-4892460"),
]


def _grilla_patagonia(inicial: float = INICIAL) -> list[list]:
    movs = _movimientos_de_la_cuenta(REGISTROS, CTA)
    return _grilla(movs, inicial, f"{CTA} {NOM}")


def test_el_conciliador_lee_todos_los_movimientos_y_ninguno_de_mas():
    det = _movimientos_del_mayor(_grilla_patagonia())
    assert len(det["movimientos"]) == 5
    assert det["avisos"] == []


def test_la_fila_de_saldo_inicial_no_se_cuenta_como_movimiento():
    """Va SIN fecha a propósito: `_movimientos_del_mayor` define movimiento como
    «fila con fecha». Con fecha, el arranque se sumaría al neto del día."""
    det = _movimientos_del_mayor(_grilla_patagonia())
    assert det["suma"] == NETO_DEL_DIA


def test_importe_es_debe_menos_haber_con_el_signo_de_valuacion():
    det = _movimientos_del_mayor(_grilla_patagonia())
    importes = sorted(m["importe"] for m in det["movimientos"])
    assert importes == sorted([18711185.35, 882442.15, 132384.21,
                               -119560.98, -1100000.00])


def test_el_cierre_del_mayor_es_saldo_inicial_mas_el_neto():
    """`_saldo_del_mayor` lee el ÚLTIMO valor de la columna Saldo. Es lo que se
    compara contra nuestro saldo, así que tiene que ser un SALDO y no el neto."""
    sal = _saldo_del_mayor(_grilla_patagonia())
    assert sal["valor"] == round(INICIAL + NETO_DEL_DIA, 2)
    assert sal["avisos"] == []


def test_sin_saldo_inicial_el_cierre_es_solo_el_neto():
    """Con arranque en cero el «cierre» NO es un saldo. El script lo avisa a los
    gritos; esto congela que efectivamente sale así y no otra cosa."""
    sal = _saldo_del_mayor(_grilla_patagonia(0.0))
    assert sal["valor"] == NETO_DEL_DIA


def test_el_concepto_agrupa_como_el_consolidado():
    """La `referencia` del asiento va tal cual porque `_grupo_mayor` la sabe
    cortar: los dos depósitos tienen que colapsar en UN grupo."""
    det = _movimientos_del_mayor(_grilla_patagonia())
    grupos = [m["grupo"] for m in det["movimientos"]]
    assert grupos.count("Depósito") == 2
    assert "Recepción de fondos de contraparte." in grupos


def test_solo_salen_los_movimientos_de_la_cuenta_pedida():
    otra = _asiento("999.99", "", "[Op. 1] Otra cosa", "-1",
                    cuenta="201050000002", nombre="Comitente - (GRAL) Operaciones")
    movs = _movimientos_de_la_cuenta([*REGISTROS, otra], CTA)
    assert len(movs) == 5
    assert {m["cuenta"] for m in movs} == {CTA}


def test_la_cuenta_tambien_se_encuentra_por_nombre():
    assert len(_movimientos_de_la_cuenta(REGISTROS, "patagonia")) == 5
