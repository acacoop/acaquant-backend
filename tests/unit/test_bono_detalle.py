"""Tests del cronograma de la FICHA DE BONO (api/services/bono_detalle.py) — puros.

**Qué se protege acá y por qué.** El master NO guarda los flujos con un shape
único: un soberano los tiene en `amortizacion_pct` + `cupon_sobre_residual`, una
ON en `amortizacion` + `interes`, y un CER en los primeros pero con otra
semántica. Cuál se usa lo decide la RAMA del motor (`engines.curvas.rama_calculo`).

Si el service sumara `amortizacion + interes` para todos —que es lo que uno
escribe sin mirar— los soberanos y los CER darían **CERO** y el modal mostraría un
bono que no paga nada al lado de una TEA del 12%. No tira, no avisa: dibuja un
gráfico vacío que parece un dato. Estos tests son los que hacen que eso falle.

`cronograma` es pura sobre el doc del master → se testea sin base.
"""
from __future__ import annotations

from datetime import date, timedelta

from api.services.bono_detalle import cronograma

FUT = (date.today() + timedelta(days=400)).isoformat()
PAS = (date.today() - timedelta(days=400)).isoformat()


def _doc(**kw):
    """Doc del master con los EJES puestos (sin ejes, `rama_calculo` cae a la
    palabra vieja y no estaríamos testeando lo que corre en prod)."""
    base = {"ticker_corto": "XX", "ticker": "MERV - XMEV - XX - 24hs",
            "valor_nominal": 100, "fecha_vencimiento": FUT}
    base.update(kw)
    return base


def test_soberano_usa_amortizacion_pct_y_cupon_sobre_residual():
    """La trampa principal: con la fórmula de las ONs este bono daría 0."""
    rama, fl = cronograma(_doc(
        emisor_tipo="soberano", moneda_eje="USD", ajuste="fija", ley="ny",
        flujos=[{"fecha": FUT, "amortizacion_pct": 20.0,
                 "cupon_sobre_residual": 0.27, "residual_previo_pct": 72.0}],
    ))
    assert rama == "soberanos"
    assert len(fl) == 1
    # 20% de 100 VN = 20 de amortización, + 0.27 de cupón (YA resuelto por el
    # prospecto: NO se re-multiplica por el residual — ver monto_flujo_soberano).
    assert fl[0]["amortizacion"] == 20.0
    assert round(fl[0]["interes"], 4) == 0.27
    assert round(fl[0]["monto"], 4) == 20.27


def test_corporativo_usa_amortizacion_e_interes_absolutos():
    """La rama ON: montos absolutos por 100 VN, otros nombres de campo."""
    rama, fl = cronograma(_doc(
        emisor_tipo="corporativo", moneda_eje="USD", ajuste="fija",
        flujos=[{"fecha": FUT, "amortizacion": 33.33, "interes": 2.5}],
    ))
    assert rama == "on"
    assert fl[0]["amortizacion"] == 33.33
    assert fl[0]["interes"] == 2.5
    assert round(fl[0]["monto"], 4) == 35.83


def test_cer_el_cupon_es_una_TASA_y_si_se_multiplica_por_el_residual():
    """⚠️ **`cupon_sobre_residual` significa DOS COSAS DISTINTAS según la rama, y
    solo la rama lo distingue.** Es la trampa más cara de este archivo:

        rama CER       → es una TASA (fracción). `monto_flujo_cer` la multiplica
                         por `residual_previo_pct` y por el VN.
        rama SOBERANOS → es un MONTO ya resuelto en moneda por 100 VN. Volver a
                         multiplicarlo por el residual infla el cupón ~70×.

    Mismo nombre de campo, dos semánticas, y ninguna validación que las separe:
    despachar mal acá no da error, da un cronograma verosímil y equivocado. Por
    eso el service NO elige la fórmula, se la pregunta a `rama_calculo`.

    Este test congela el comportamiento REAL del motor (`engines/curvas.py`), que
    es el que produce la TEA que la mesa viene mirando y que está validada contra
    1816. NO es una opinión sobre cuál semántica es la correcta.
    """
    # Cupón semestral 2% sobre un residual del 100% → 2 por cada 100 VN.
    rama, fl = cronograma(_doc(
        emisor_tipo="soberano", moneda_eje="ARS", ajuste="cer", cer_emision=100,
        flujos=[{"fecha": FUT, "amortizacion_pct": 100.0,
                 "cupon_sobre_residual": 0.02, "residual_previo_pct": 100.0}],
    ))
    assert rama == "cer"
    assert fl[0]["amortizacion"] == 100.0
    assert round(fl[0]["interes"], 4) == 2.0
    assert round(fl[0]["monto"], 4) == 102.0

    # Y el residual SÍ entra: con la mitad amortizada, el mismo cupón paga la mitad.
    _, fl2 = cronograma(_doc(
        emisor_tipo="soberano", moneda_eje="ARS", ajuste="cer", cer_emision=100,
        flujos=[{"fecha": FUT, "amortizacion_pct": 0.0,
                 "cupon_sobre_residual": 0.02, "residual_previo_pct": 50.0}],
    ))
    assert round(fl2[0]["interes"], 4) == 1.0


def test_bullet_sin_array_de_flujos_sale_del_flujo_vencimiento():
    """Media pill TASA FIJA son Lecaps/Boncaps: pagan todo al final y el master
    NO les guarda `flujos`. Sin este caso el modal abriría VACÍO, y 'sin
    cronograma cargado' se vería igual que 'paga todo al vencimiento'."""
    rama, fl = cronograma(_doc(
        emisor_tipo="soberano", moneda_eje="ARS", ajuste="fija",
        flujos=[], flujo_vencimiento=143.5,
    ))
    assert rama == "tasa_fija"
    assert len(fl) == 1
    assert fl[0]["bullet"] is True
    assert fl[0]["monto"] == 143.5
    assert fl[0]["fecha"] == FUT
    assert fl[0]["futuro"] is True


def test_bullet_no_pisa_un_cronograma_que_si_existe():
    """El fallback es SOLO para los que no tienen flujos. Si el bono tiene
    cronograma, `flujo_vencimiento` no puede agregar un pago fantasma."""
    _, fl = cronograma(_doc(
        emisor_tipo="corporativo", moneda_eje="ARS", ajuste="fija",
        flujos=[{"fecha": FUT, "amortizacion": 100.0, "interes": 5.0}],
        flujo_vencimiento=999.0,
    ))
    assert len(fl) == 1
    assert fl[0]["monto"] == 105.0


def test_marca_futuro_y_ordena_por_fecha():
    """El pasado NO se borra (el perfil de amortización completo es legible), se
    MARCA — y el orden lo pone el service, no el que dibuja."""
    _, fl = cronograma(_doc(
        emisor_tipo="corporativo", moneda_eje="ARS", ajuste="fija",
        flujos=[{"fecha": FUT, "amortizacion": 50.0, "interes": 1.0},
                {"fecha": PAS, "amortizacion": 50.0, "interes": 2.0}],
    ))
    assert [f["fecha"] for f in fl] == [PAS, FUT]
    assert [f["futuro"] for f in fl] == [False, True]


def test_bono_sin_flujos_ni_pago_final_devuelve_lista_vacia():
    """Vacío es una respuesta válida y honesta. Lo que no puede es inventar un
    pago para que el gráfico tenga algo que dibujar."""
    _, fl = cronograma(_doc(
        emisor_tipo="corporativo", moneda_eje="ARS", ajuste="fija", flujos=[],
    ))
    assert fl == []
