"""Tests del motor api/services/intraday.py (FIFO + parsing AR + intereses)."""
from api.services.intraday import IVA_RATE, _num, _parse_csv, fifo_pnl


def test_num_formato_ar():
    assert _num("11.630,000") == 11630.0
    assert _num("4.820,000") == 4820.0
    assert _num("385.029,000") == 385029.0
    assert _num("241.000,000") == 241000.0
    assert _num("50,000") == 50.0
    assert _num("") == 0.0


def test_fifo_long_cierre_parcial():
    # Compra 100@10, compra 100@12, vende 150@15.
    # Cierra 100@10 → (15-10)*100=500; 50@12 → (15-12)*50=150. Total 650.
    # Queda long 50 @12.
    realized, qty, wavg = fifo_pnl([(100, 10), (100, 12), (-150, 15)])
    assert realized == 650.0
    assert qty == 50
    assert wavg == 12.0


def test_fifo_short():
    # Vende 100@10 (abre short), compra 60@8 → (10-8)*60=120. Queda short 40 @10.
    realized, qty, wavg = fifo_pnl([(-100, 10), (60, 8)])
    assert realized == 120.0
    assert qty == -40
    assert wavg == 10.0


def test_fifo_vuelta_de_posicion():
    # Long 100@10, vende 150@12: cierra 100 →(12-10)*100=200, y abre short 50 @12.
    realized, qty, wavg = fifo_pnl([(100, 10), (-150, 12)])
    assert realized == 200.0
    assert qty == -50
    assert wavg == 12.0


def test_fifo_flat():
    # Compra 200@10, vende 200@11 → (11-10)*200=200, posición cerrada.
    realized, qty, _wavg = fifo_pnl([(200, 10), (-200, 11)])
    assert realized == 200.0
    assert qty == 0


def test_parse_excluye_cauciones():
    csv_text = (
        '"Hora","Especie","Lado","Precio","Cantidad","Cuenta","Monto","F.O.","Moneda"\n'
        '"24/06/2026 10:35:51","SPCX","Venta","4.820,000","50,000","805","241.000,000","Contado","PESOS"\n'
        '"24/06/2026 12:01:54","PESOS","Venta","20,700","385.029,000","805","385.247,360","Caucion","PESOS"\n'
    )
    trades = _parse_csv(csv_text)
    assert len(trades) == 1
    assert trades[0]["especie"] == "SPCX"
    assert trades[0]["signo"] == -1
    assert trades[0]["precio"] == 4820.0


def test_interes_iva_rate():
    # interés = monto*0.0007, IVA = interés*0.21.
    monto = 1_000_000
    interes = monto * 0.0007
    assert interes == 700.0
    assert interes * IVA_RATE == 147.0
