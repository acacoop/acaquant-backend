"""Golden/characterization tests del pipeline de datos crítico.

Doc: docs/OBSERVABILIDAD_ROBUSTEZ.md (commit 3). Red de regresión sobre el camino
que produce los números que ve la mesa: ingesta/normalización de operaciones,
categorización del negocio y las "fórmulas no inferibles" del AuM/PnL.

TÉCNICA: los outputs esperados fueron CONGELADOS corriendo las funciones REALES
sobre fixtures anonimizados (2026-07-18). Si un cambio futuro altera el
resultado, el test rompe A PROPÓSITO: eso es una señal para revisar, no para
"arreglar el test". REGLA DE ORO: jamás cambiar la lógica de producción para que
un golden pase.

⚠ HALLAZGO DE LA CHARACTERIZATION (marcado, NO tapado — decide el user):
`_to_float("1.500")` → 1.5: un punto ÚNICO se trata como decimal, pero en el
Excel es-AR histórico "1.500" casi siempre es MIL QUINIENTOS. Ambigüedad
intrínseca del formato (una cantidad "1.500" ≠ un precio "1.500"). El golden
congela el comportamiento ACTUAL; si se decide cambiar el parser, este test
rompe y obliga a revisar los datos ya ingestados con ese patrón.

Todo UNIT: cero SQL/red (maps y mep_cache inyectados; funciones puras).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from api.services import operaciones_informes as oi
from api.services.pnl import _aplicar_normalizer
from jobs._aum_filters import CUENTAS_SIN_ARS, is_excluded
from jobs.negocio_movimientos import _boleto_a_doc, _extract_id_cuenta

_FIXTURES = Path(__file__).parent / "fixtures" / "golden_operaciones.json"


def _fixture() -> dict:
    return json.loads(_FIXTURES.read_text(encoding="utf-8"))


# ── 1) Ingesta/normalización de operaciones (operaciones_informes) ───────────


def test_golden_normalizar_filas():
    fx = _fixture()
    for caso in fx["casos"]:
        assert oi.normalizar_fila(caso["cruda"]) == caso["esperado_normalizado"], caso["nombre"]


def test_golden_fila_sin_boleto_es_none():
    assert oi.normalizar_fila({"Cuenta": 44, "Denominacion": "SIN BOLETO"}) is None


def test_golden_enriquecer():
    fx = _fixture()
    maps = (fx["maps_enrich"]["catalogo"], fx["maps_enrich"]["niveles"])
    cache = dict(fx["maps_enrich"]["mep_cache"])
    for caso in fx["casos"]:
        doc = dict(caso["esperado_normalizado"])
        oi._aplicar_enrich(doc, maps, cache)
        for campo, esperado in caso["esperado_enriquecido_extra"].items():
            assert doc[campo] == esperado, f"{caso['nombre']}.{campo}"


def test_golden_otc_excluido():
    # NDF OTC / Opciones OTC no se ingestan; "Concurrencia OTC" SÍ (regla 2026-06-08)
    assert oi.es_otc_excluido("Rueda OTC NDF OTC - Compra") is True
    assert oi.es_otc_excluido("Rueda OTC Opciones OTC - Venta") is True
    assert oi.es_otc_excluido("Concurrencia OTC - Venta") is False
    assert oi.es_otc_excluido(None) is False


# ── 2) Categorización del negocio (negocio_movimientos) ──────────────────────


def test_golden_boleto_a_doc():
    ahora = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    boleto = {
        "comprobante": "B-777", "cuenta": "[805] CLIENTE EJEMPLO SA",
        "categoria": "compra", "op": "Compra Contado", "ticker": "AL30",
        "cantidad": 1500.0, "precio": 823.1, "importe": -1234650.0,
        "moneda": "ARS", "plazo": "24hs", "lugar": "BYMA",
        "estado": "Procesado", "informacion": "x", "n_lineas": 2,
    }
    doc = _boleto_a_doc(boleto, "2026-07-17", ahora, 1200.5)
    assert doc == {
        "fecha": "2026-07-17", "comprobante": "B-777",
        "cuenta": "[805] CLIENTE EJEMPLO SA", "id_cuenta": "805",
        "categoria": "compra", "op": "Compra Contado", "ticker": "AL30",
        "cantidad": 1500.0, "precio": 823.1, "importe": -1234650.0,
        "moneda": "ARS", "plazo": "24hs", "lugar": "BYMA",
        "estado": "Procesado", "informacion": "x", "n_lineas": 2,
        "mep": 1200.5, "ingestado_en": ahora,
    }


def test_golden_extract_id_cuenta():
    assert _extract_id_cuenta("[805] CLIENTE EJEMPLO SA") == "805"
    assert _extract_id_cuenta("SIN PREFIJO") is None
    assert _extract_id_cuenta(None) is None


# ── 3) Clasificación del AuM (_aum_filters) — las 5 reglas de exclusión ──────


def test_golden_aum_is_excluded():
    # regla 1: cash USD link
    assert is_excluded("[805] X", "USDL") is True
    # regla 2: OTC/CDC como substring en cuenta o unidad
    assert is_excluded("[9] FONDO OTC SA", "AL30") is True
    # regla 3: id_cuenta en contrapartes (inyectado — sin SQL)
    assert is_excluded("[7] Y", "GD30", id_cuenta="375",
                       contrapartes_ids=frozenset({"375"})) is True
    # regla 4: nombre de contraparte como palabra completa en la cuenta
    assert is_excluded("[8] FONDOX SA", "AL30",
                       contrapartes_names=frozenset({"FONDOX"})) is True
    # regla 5: ARS de las cuentas propias (se toma de la constante real)
    assert is_excluded(sorted(CUENTAS_SIN_ARS)[0], "ARS") is True
    # cuenta normal con un bono → NO se excluye
    assert is_excluded("[805] NORMAL", "AL30D") is False


# ── 4) Valuación por CARTERA (pnl._aplicar_normalizer — fórmula no inferible) ─


def test_golden_valuacion_por_cartera():
    # renta fija en paridad (HD/DL/ARS) → ÷100
    assert _aplicar_normalizer(85800.0, 10, cartera="HD") == 8580.0
    assert _aplicar_normalizer(823.1, 100, cartera="ARS") == 823.1
    # cash / FCI → precio × cantidad, JAMÁS ÷100
    assert _aplicar_normalizer(1500.0, 2, cartera="MONEDAS") == 3000.0
    assert _aplicar_normalizer(1500.0, 2, cartera="FCI") == 3000.0
    # futuros → (precio + 1) × cantidad
    assert _aplicar_normalizer(1560.5, 3, cartera="DERIVADOS", tipoTitulo="Futuros") == 4684.5
    # cartera vacía sin tipo → fallback precio × cantidad
    assert _aplicar_normalizer(50.0, 4, cartera="", tipoTitulo="") == 200.0
