"""tests/unit/test_ap5_margenes.py — el aplanado de MarginRequirementReport.

Los payloads son los EJEMPLOS DEL MANUAL del proveedor, copiados tal cual. Que
sean los del manual y no inventados importa: es el único contrato que tenemos
antes de ver la respuesta real de nuestro usuario.
"""
from __future__ import annotations

from core.postrade_margenes import aplanar_margenes, totales_por_moneda

# Manual pág. 117 — SIN desglose por grupo de producto.
SIN_DESGLOSE = [
    {
        "ClearingMemberCode": "123", "ClearingMember": "Agente", "Date": "2019-08-01",
        "Accounts": [{
            "CompensationAccount": "Agente", "CompensationAccountCode": "54500",
            "SubAccounts": [
                {"NettingAccount": "COMITENTE", "NettingAccountCode": "28596",
                 "References": [{"Reference": "Márgenes", "Currency": "Pesos",
                                 "Margin": -16800000.0, "OptionAmount": -111111.0,
                                 "InterTempAmount": -1253.55}]},
                {"NettingAccount": "COMITENTE2", "NettingAccountCode": "29192",
                 "References": [{"Reference": "Márgenes", "Currency": "Pesos",
                                 "Margin": -4200000.0, "OptionAmount": -111111.0,
                                 "InterTempAmount": -1253.55}]},
            ],
        }],
    },
    {
        "ClearingMemberCode": "123", "ClearingMember": "AGENTE", "Date": "2019-08-01",
        "Accounts": [{
            "CompensationAccount": " AGENTE", "CompensationAccountCode": "66000",
            "SubAccounts": [
                {"NettingAccount": "COMITENTE3", "NettingAccountCode": "58157",
                 "References": [{"Reference": "Márgenes", "Currency": "Pesos",
                                 "Margin": -445872000.0, "OptionAmount": -45671.0,
                                 "InterTempAmount": -897.55}]},
            ],
        }],
    },
]

# Manual pág. 119 — CON desglose (aparece `ProductGroup`).
CON_DESGLOSE = [
    {
        "ClearingMemberCode": "334", "ClearingMember": "AGENTE",
        "Date": "2019-08-01", "ProductGroup": "DLR",
        "Accounts": [{
            "CompensationAccount": "AGENTE", "CompensationAccountCode": "1234",
            "SubAccounts": [
                {"NettingAccount": "COMITENTE", "NettingAccountCode": "35901",
                 "References": [{"Reference": "Márgenes", "Currency": "Pesos",
                                 "Margin": -560000.0, "OptionAmount": -111111.0,
                                 "InterTempAmount": -1253.55}]},
                {"NettingAccount": "COMITENTE", "NettingAccountCode": "45484",
                 "References": [{"Reference": "Márgenes", "Currency": "Pesos",
                                 "Margin": -280000.0, "OptionAmount": -111111.0,
                                 "InterTempAmount": -1253.55}]},
            ],
        }],
    },
]


def test_aplana_los_cuatro_niveles_a_una_fila_por_hoja():
    """Un blob de cuatro niveles no se puede sumar ni filtrar sin re-parsearlo
    en cada consulta — la misma razón por la que se expandió `PositionQty`."""
    filas, stats = aplanar_margenes(SIN_DESGLOSE)
    assert len(filas) == 3
    assert stats == {"agentes": 2, "cuentas": 2, "subcuentas": 3, "referencias": 3,
                     "sin_cuentas": 0, "sin_subcuentas": 0, "sin_referencias": 0}


def test_la_fila_trae_el_COMITENTE_que_une_con_ap5_cuentas():
    """`NettingAccountCode` es la clave del comitente: sin eso el margen no se
    puede cruzar con el resto de la vista."""
    filas, _ = aplanar_margenes(SIN_DESGLOSE)
    assert [f["cuenta"] for f in filas] == ["28596", "29192", "58157"]
    assert filas[0]["cuenta_nombre"] == "COMITENTE"
    assert filas[0]["cuenta_compensacion_codigo"] == "54500"


def test_los_importes_se_preservan_CON_SU_SIGNO():
    """El manual los manda negativos. Darlos vuelta acá sería meter una decisión
    de presentación en la capa que trae el dato — y el día que venga un positivo
    real nadie entendería por qué cambió."""
    filas, _ = aplanar_margenes(SIN_DESGLOSE)
    assert filas[0]["margen"] == -16800000.0
    assert filas[0]["primas"] == -111111.0
    assert filas[0]["inter_temporal"] == -1253.55


def test_el_total_va_POR_MONEDA_y_no_hay_total_unico():
    filas, _ = aplanar_margenes(SIN_DESGLOSE)
    t = totales_por_moneda(filas)
    assert len(t) == 1
    assert t[0]["moneda"] == "Pesos"
    assert t[0]["margen"] == -466872000.0
    assert t[0]["cuentas"] == 3


def test_dos_monedas_NO_se_suman():
    """El agro liquida en Dólar MtR y el dólar futuro en Pesos: un total de las
    dos da un número y no significa nada."""
    mixto = [{
        "ClearingMember": "A", "Date": "2026-08-24",
        "Accounts": [{"CompensationAccountCode": "1", "SubAccounts": [
            {"NettingAccountCode": "10", "References": [
                {"Currency": "Pesos", "Margin": -100.0},
                {"Currency": "Dólar MtR", "Margin": -7.0},
            ]},
        ]}],
    }]
    filas, _ = aplanar_margenes(mixto)
    t = totales_por_moneda(filas)
    assert {x["moneda"]: x["margen"] for x in t} == {"Pesos": -100.0, "Dólar MtR": -7.0}


def test_viewDetails_true_trae_el_grupo_de_producto():
    """⚠️ Con desglose la MISMA cuenta aparece una vez por grupo: sumar las dos
    respuestas juntas contaría todo dos veces. El campo lo hace visible."""
    filas, _ = aplanar_margenes(CON_DESGLOSE)
    assert all(f["grupo_producto"] == "DLR" for f in filas)
    sin, _ = aplanar_margenes(SIN_DESGLOSE)
    assert all(f["grupo_producto"] == "" for f in sin)


def test_un_nivel_vacio_se_CUENTA_no_se_ignora():
    """Si la cámara deja de mandar `SubAccounts`, el total daría 0 — y sin el
    conteo se leería igual que 'hoy no hay márgenes'."""
    _, stats = aplanar_margenes([
        {"ClearingMember": "A", "Accounts": []},
        {"ClearingMember": "B", "Accounts": [{"CompensationAccountCode": "1", "SubAccounts": []}]},
        {"ClearingMember": "C", "Accounts": [{"CompensationAccountCode": "2", "SubAccounts": [
            {"NettingAccountCode": "9", "References": []}]}]},
    ])
    assert stats["sin_cuentas"] == 1
    assert stats["sin_subcuentas"] == 1
    assert stats["sin_referencias"] == 1


def test_importe_ausente_queda_NULL_y_no_cero():
    """'No informó' y 'no debe nada' son cosas distintas y solo una es
    afirmable."""
    filas, _ = aplanar_margenes([{
        "ClearingMember": "A", "Accounts": [{"CompensationAccountCode": "1",
            "SubAccounts": [{"NettingAccountCode": "10",
                             "References": [{"Currency": "Pesos", "Margin": -5.0}]}]}],
    }])
    assert filas[0]["margen"] == -5.0
    assert filas[0]["primas"] is None
    assert filas[0]["inter_temporal"] is None


def test_respuesta_vacia_o_rara_no_revienta():
    for entrada in ([], None, {}, "texto", [None, 1, "x"]):
        filas, _ = aplanar_margenes(entrada)
        assert filas == []
