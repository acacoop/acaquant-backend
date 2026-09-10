"""El histórico de opciones suma el cierre diario (options_data_hist) a los ticks de hoy.

Cubre las dos funciones puras de `api/services/opciones_sql.py` que hacen la mezcla: qué
días entran (los que no tienen intradía y en los que el contrato operó) y con qué shape.
"""

from datetime import date, datetime, time

from api.services.opciones_sql import (
    buckets_diarios_estrategia,
    filas_diarias_contrato,
)

HIST = [
    ("2026-09-08", "GFGC72000C", {"last": 300.0, "ev": 1_000_000, "strike": 7200, "tipo": "CALL",
                                  "spot": 7000.0, "delta": 0.5, "iv": 0.45}),
    ("2026-09-09", "GFGC72000C", {"last": 310.0, "ev": 0, "strike": 7200, "tipo": "CALL",
                                  "spot": 7050.0}),                       # no operó
    ("2026-09-10", "GFGC72000C", {"last": 353.2, "ev": 2_000_000, "strike": 7200, "tipo": "CALL",
                                  "spot": 7100.0}),                       # tiene intradía
    ("2026-09-08", "GFGV72000C", {"last": 120.0, "strike": 7200, "tipo": "PUT",
                                  "spot": 7000.0}),                       # sin `ev`: vale last>0
]


def test_filas_diarias_saltea_intradia_y_dias_sin_operar():
    out = filas_diarias_contrato(
        [r for r in HIST if r[1] == "GFGC72000C"], dias_excluidos={date(2026, 9, 10)}
    )
    assert [r["timestamp"] for r in out] == [datetime.combine(date(2026, 9, 8), time(17, 0))]
    fila = out[0]
    assert fila["instrumento"] == "GFGC72000C"
    assert fila["last"] == 300.0 and fila["spot"] == 7000.0 and fila["delta"] == 0.5
    # Mismo shape que un tick intradía: los campos que el rollup no tiene van en None.
    assert fila["bid"] is None and fila["offer"] is None and fila["last_timestamp"] is None


def test_filas_diarias_sin_ev_usa_last():
    out = filas_diarias_contrato([HIST[3]], dias_excluidos=set())
    assert len(out) == 1 and out[0]["last"] == 120.0


def test_buckets_diarios_pone_bid_offer_iguales_a_last():
    buckets = buckets_diarios_estrategia(HIST, dias_excluidos={date(2026, 9, 10)})
    assert set(buckets) == {datetime.combine(date(2026, 9, 8), time(17, 0))}
    docs = buckets[datetime.combine(date(2026, 9, 8), time(17, 0))]
    assert {d["symbol"] for d in docs} == {"GFGC72000C", "GFGV72000C"}
    call = next(d for d in docs if d["tipo"] == "CALL")
    # bid = offer = last → el pricing de estrategia_desde_buckets lo toma como líquido y
    # usa el cierre como precio en las dos puntas.
    assert call["bid"] == call["offer"] == call["last"] == 300.0
    assert call["strike"] == 7200 and call["spot"] == 7000.0
