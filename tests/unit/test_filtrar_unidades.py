"""Tests de jobs/carteras.py::filtrar_unidades."""
import pandas as pd
import pytest

from jobs.carteras import filtrar_unidades


@pytest.mark.parametrize("unidad,debe_quedar", [
    # Exactas excluidas
    ("ARS", False),
    ("USDL", False),
    # Exactas similares que NO deben matchear
    ("ARSUD", True),
    ("USDLX", True),
    # Substrings excluidos
    ("[1] Depósito U$ 100000", False),
    ("Bonar OTC 2028", False),
    ("Lecap S31O5 OTC", False),
    ("ON 2024 clase A", False),
    ("GD30 2025", False),
    ("DLR futuro", False),
    # Unidades válidas
    ("AL30D", True),
    ("GD30", True),
    ("TX26", True),
    ("S31O5", True),
    ("GGAL - Banco Galicia", True),
])
def test_filtrar_unidades_por_regla(unidad, debe_quedar):
    df_in = pd.DataFrame({"unidad": [unidad], "precio": [100]})
    df_out = filtrar_unidades(df_in)
    if debe_quedar:
        assert len(df_out) == 1, f"'{unidad}' debería quedar"
        assert df_out["unidad"].iloc[0] == unidad
    else:
        assert len(df_out) == 0, f"'{unidad}' debería ser excluida"


def test_filtrar_unidades_preserva_orden_y_resto_de_columnas():
    df_in = pd.DataFrame({
        "unidad": ["AL30D", "ARS", "GD30", "DLR futuro", "TX26"],
        "precio": [100, 1, 200, 50, 300],
        "cantidad": [10, 100, 20, 5, 40],
    })
    df_out = filtrar_unidades(df_in)
    assert list(df_out["unidad"]) == ["AL30D", "GD30", "TX26"]
    assert list(df_out["precio"]) == [100, 200, 300]
    assert list(df_out["cantidad"]) == [10, 20, 40]


def test_filtrar_unidades_maneja_valores_no_string():
    """Unidades None / NaN / números no deben romper — se conservan."""
    df_in = pd.DataFrame({"unidad": [None, 42, "AL30D", "ARS"], "precio": [1, 2, 3, 4]})
    df_out = filtrar_unidades(df_in)
    # None y 42 no son strings → debe_excluir devuelve False → quedan
    assert len(df_out) == 3
    assert "ARS" not in df_out["unidad"].astype(str).tolist()


def test_filtrar_unidades_df_vacio():
    df_in = pd.DataFrame({"unidad": [], "precio": []})
    df_out = filtrar_unidades(df_in)
    assert len(df_out) == 0
