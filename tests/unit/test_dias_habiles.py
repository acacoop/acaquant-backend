"""Tests de jobs/dias_habiles.py — generación de calendario hábil argentino."""

from datetime import date

from jobs.dias_habiles import generar_dias_habiles


def test_excluye_fines_de_semana():
    dias = generar_dias_habiles(2025)
    for d_iso in dias:
        d = date.fromisoformat(d_iso)
        assert d.weekday() < 5, f"{d_iso} es fin de semana"


def test_excluye_navidad():
    dias = generar_dias_habiles(2025)
    assert "2025-12-25" not in dias


def test_excluye_año_nuevo():
    dias = generar_dias_habiles(2025)
    assert "2025-01-01" not in dias


def test_excluye_25_de_mayo():
    dias = generar_dias_habiles(2025)
    assert "2025-05-25" not in dias


def test_cantidad_razonable():
    # 2025: aproximadamente 245-252 días hábiles (365 - ~104 weekend - ~13 feriados)
    dias = generar_dias_habiles(2025)
    assert 240 <= len(dias) <= 255


def test_orden_ascendente():
    dias = generar_dias_habiles(2025)
    assert dias == sorted(dias)


def test_formato_iso():
    dias = generar_dias_habiles(2025)
    for d in dias[:10]:
        date.fromisoformat(d)  # no debe tirar
        assert d.startswith("2025-")
