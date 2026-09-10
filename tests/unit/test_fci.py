"""FCI — lo que se congela sin base ni Primary (docs/FCI.md).

  · el MATCH por nombre (`core/fci_match`): normalizar tres grafías del mismo
    fondo a la misma clave, y asignar la gerente por alias más largo;
  · la CONVENCIÓN de rendimientos (`api/services/fci_sql`);
  · la fecha del LA y la prioridad de fuentes (`jobs/fci_vcp`);
  · el RBAC: `fci` es canónico, lo tiene la mesa y NO el invitado.
"""
from __future__ import annotations

from datetime import date

import pytest

from api.auth import get_module_for_path
from api.services import fci_sql
from core import fci_match, roles
from jobs.fci_vcp import _SQL_UPSERT, fecha_del_la

# ── match por nombre ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("grafia", [
    "FCI Adcap Cobertura - Clase A",
    "ADCAP COBERTURA F.C.I. Clase A",
    "[3367] CAFCI1216-3367 - Adcap Cobertura - Clase A",
    "  Adcap  Cobertura – Clase A ",
])
def test_normalizar_lleva_las_grafias_a_la_misma_clave(grafia):
    assert fci_match.normalizar(grafia) == "adcap cobertura clase a"


def test_normalizar_saca_acentos_y_ley_27743():
    assert fci_match.normalizar("Adcap Ahorro Dinámico - Clase Ley N° 27.743") == "adcap ahorro dinamico clase"
    assert fci_match.normalizar(None) == "" and fci_match.normalizar("  ") == ""


def test_nombre_desde_primary_prefiere_la_descripcion_sin_fci():
    assert fci_match.nombre_desde_primary(
        {"securityDescription": "FCI IEB Retorno Total - Clase D",
         "instrumentId": {"symbol": " IEB Retorno Total - Clase D"}}) == "IEB Retorno Total - Clase D"
    assert fci_match.nombre_desde_primary({"instrumentId": {"symbol": "ADBAICA AR"}}) == "ADBAICA AR"


def test_plazo_desde_settl_es_fix():
    assert [fci_match.plazo_desde_settl(s) for s in ("1", 2, "3", "4", None, "9")] == [0, 1, 2, 3, None, None]


def test_gerente_de_gana_el_alias_mas_largo_y_exige_palabra_entera():
    alias = {"TORONTO": ["Toronto Trust"], "MAX": ["Max"], "MAXIMA": ["Maxima"], "IEB": ["Ciclo Nova"]}
    assert fci_match.gerente_de("Toronto Trust Ahorro - Clase B", alias) == "TORONTO"
    assert fci_match.gerente_de("Max Renta Fija Latam - Clase E", alias) == "MAX"
    assert fci_match.gerente_de("Maxima Renta", alias) == "MAXIMA"      # 'Max' no matchea 'Maxima'
    assert fci_match.gerente_de("Ciclo Nova Ahorro - Clase E", alias) == "IEB"
    assert fci_match.gerente_de("Schroder Liquidez - Clase B", alias) is None
    assert fci_match.gerente_de("", alias) is None


def test_sugerir_categoria_solo_lo_obvio():
    s = fci_match.sugerir_categoria
    assert s("Mercado de Dinero", 0, "ARS") == "T+0 MONEY MARKET"
    assert s("Mercado de Dinero", 0, "USD") == "MONEY MARKET USD"
    assert s("Renta Fija", 1, "ARS") == "T+1"
    assert s("Renta Fija", 0, "ARS") == "T+0"
    assert s("Renta Fija", 2, "ARS") is None          # no se inventa
    assert s("Renta Fija", 2, "USD") == "RENTA FIJA USD"
    assert s("Renta Variable", 2, "ARS") == "RENTA VARIABLE"
    assert s(None, None, None) is None


# ── rendimientos ─────────────────────────────────────────────────────────────


def test_rendimiento_y_tna():
    assert fci_sql.rendimiento(101.0, 100.0) == pytest.approx(0.01)
    assert fci_sql.rendimiento(100.0, None) is None and fci_sql.rendimiento(100.0, 0) is None
    assert fci_sql.tna(0.0170, 30) == pytest.approx(0.2068, abs=1e-4)   # =+G13/30*365
    assert fci_sql.tna(None, 30) is None


def test_fila_arma_todos_los_rendimientos_desde_los_anclas():
    r = (7, "Adcap Cobertura - Clase A", "ADCAP", "T+1", "ARS", "Renta Fija", 1,
         "ADBAICA AR", "[3367] CAFCI1216-3367 - Adcap Cobertura - Clase A", "CAFCI1216-3367", "primary",
         date(2026, 9, 7), 110.0, "primary",
         109.0, date(2026, 9, 4), 108.0, 105.0, 100.0, 107.0, 104.0, 102.0, 90.0)
    fila = fci_sql._fila(r)
    assert fila["en_tenencia"] is True and fila["fuente"] == "primary"
    assert fila["r_1d"] == pytest.approx(110 / 109 - 1)
    assert fila["r_wtd"] == pytest.approx(110 / 108 - 1)
    assert fila["r_ytd"] == pytest.approx(0.10)
    assert fila["r_365d"] == pytest.approx(110 / 90 - 1)
    assert fila["tna_30d"] == pytest.approx((110 / 104 - 1) * 365 / 30)
    assert fila["fecha"] == "2026-09-07" and fila["fecha_1d"] == "2026-09-04"


def test_fila_sin_vcp_da_none_sin_romper():
    r = (1, "X", "G", None, "USD", None, None, None, None, None, "asset",
         None, None, None, None, None, None, None, None, None, None, None, None)
    fila = fci_sql._fila(r)
    assert fila["vcp"] is None and fila["en_tenencia"] is False
    assert all(fila[k] is None for k in ("r_1d", "r_wtd", "r_mtd", "r_ytd", "tna_30d"))


def test_orden_categorias():
    assert sorted(["ZZZ", None, "CER", "T+0 MONEY MARKET"], key=fci_sql._orden_categoria) == \
        ["T+0 MONEY MARKET", "CER", "ZZZ", None]


# ── el job ───────────────────────────────────────────────────────────────────


def test_fecha_del_la_es_la_del_timestamp_en_art():
    # 1789074601235 ms = 2026-09-10 13:50 UTC = 10:50 ART → 2026-09-10 (medido en el diag)
    assert fecha_del_la(1789074601235, date(2026, 9, 11)) == date(2026, 9, 10)
    # 01:30 UTC del día 11 = 22:30 ART del 10 → sigue siendo el 10
    assert fecha_del_la(1789090200000, date(2026, 9, 11)) == date(2026, 9, 10)
    assert fecha_del_la(None, date(2026, 9, 11)) == date(2026, 9, 11)


def test_el_upsert_respeta_la_prioridad_de_fuentes():
    # primary > tenencia > manual: una fuente más débil no pisa una más fuerte.
    assert "WHEN 'primary' THEN 3" in _SQL_UPSERT and "WHEN 'tenencia' THEN 2" in _SQL_UPSERT
    assert "<= CASE EXCLUDED.fuente" in _SQL_UPSERT


# ── RBAC ─────────────────────────────────────────────────────────────────────


def test_fci_es_modulo_de_la_mesa_y_no_del_invitado():
    assert "fci" in roles.MODULES
    for rol in ("admin", "trader", "sales", "empleado_aca", "asistente_comercial"):
        assert "fci" in roles.DEFAULT_MATRIX[rol], rol
    assert "fci" not in roles.DEFAULT_MATRIX["back_office"]
    assert "fci" not in roles.INVITADO_MODULES        # default-deny (REGLA #8)


def test_prefijo_api_fci_mapea_al_modulo_fci():
    assert get_module_for_path("/api/fci/tabla") == "fci"
    assert get_module_for_path("/api/fci/fondo/7") == "fci"


def test_alias_compartido_se_detecta_y_el_desempate_es_reproducible():
    alias = {"TORONTO": ["Toronto Trust"], "BACS": ["Toronto Trust"], "IAM": []}
    assert fci_match.alias_compartidos(alias) == {"toronto trust": ["BACS", "TORONTO"]}
    # empate de longitud → gana la gerente alfabéticamente menor, siempre la misma
    assert fci_match.gerente_de("Toronto Trust Ahorro - Clase B", alias) == "BACS"
    assert fci_match.gerente_de("Toronto Trust Ahorro - Clase B", dict(reversed(list(alias.items())))) == "BACS"
    assert fci_match.alias_compartidos({"IAM": ["IAM"], "MAX": ["Max"]}) == {}
