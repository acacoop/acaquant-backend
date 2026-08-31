"""ACA VALORES RETORNO TOTAL — parseo del informe + carga idempotente.

Lo que congela, y por qué cada cosa costó pensarla:

1. El parser encuentra los DATOS por el header ('Fondo' en la col 0), no por un
   número de fila fijo — el informe trae título + header de 2 filas + un blanco.
2. Un archivo que NO es el informe se rechaza ANTES de tocar la base. El modo de
   falla que importa no es el error: es escribir medio mes y abortar.
3. Una fila SIN fecha de concertación se DESCARTA y se cuenta. `periodo` es la
   clave del reemplazo (NOT NULL): dejarla pasar volteaba la importación entera
   del mes por una línea de pie de página.
4. `dry_run` no escribe NADA. Es lo que mira el admin antes de confirmar, así
   que si escribiera sería peor que no existir.
5. El endpoint de carga es ADMIN-ONLY. Es la única escritura de la vista que no
   pasa por la allowlist de escritores.
"""
from __future__ import annotations

import io

import pytest

from api.services import acavalores_retorno as svc

_ENCABEZADO = ["Fondo", "Operación", "Fecha Concertación", "Plazo",
               "Fecha Liquidación", "Papel Número", "Papel Descripción",
               "Depositario", "Valor Nominal", "Moneda Símbolo", "Precio",
               "Bruto", "Gastos", "ISIN", "Agente", "Liq Total", "Papel Código",
               "Liq Neto", "Liq Precio", "Fondo Neto", "Tipo Especie"]


def _excel(filas: list[list]) -> bytes:
    """Arma un .xlsx con la MISMA estructura del informe: título, header de dos
    filas, una fila en blanco, y después los datos."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["ACA VALORES — OPERACIONES BURSÁTILES"])
    ws.append(_ENCABEZADO)                      # la fila que el parser busca
    ws.append(["Nombre"] + [""] * 20)           # subheader
    ws.append([""] * 21)                        # blanco
    for f in filas:
        ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _fila(fecha="15/08/2026", operacion="Compra", agente="ALyC S.A.",
          papel="GOB NACIONAL", vn=1000, bruto=250_000.5):
    return ["ACA R.TOTAL", operacion, fecha, 1, fecha, "12345", papel,
            "CVSA", vn, "$", 98.5, bruto, 12.3, "ARS123", agente,
            250_012.8, "AL30", 250_000.5, 98.5, 250_000.5, "TITULOS PUBLICOS"]


def test_parsea_las_21_columnas_y_deriva_el_periodo():
    filas = svc.parsear(_excel([_fila(), _fila(fecha="20/08/2026")]))
    assert len(filas) == 2
    assert filas[0]["periodo"] == "2026-08"
    assert filas[0]["fondo"] == "ACA R.TOTAL"
    assert filas[0]["agente_descripcion"] == "ALyC S.A."
    assert filas[0]["bruto"] == 250_000.5
    assert filas[0]["fecha_concertacion"].isoformat() == "2026-08-15"


def test_archivo_que_no_es_el_informe_se_rechaza_sin_tocar_la_base():
    """Menos columnas de las esperadas → ValueError, no un import a medias."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["cualquier", "otra", "cosa"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError, match="columnas"):
        svc.parsear(buf.getvalue())


def test_extension_no_excel_se_rechaza_antes_de_parsear(monkeypatch):
    """El chequeo de extensión corre PRIMERO: un PDF no llega ni a pandas."""
    def _explota(*a, **k):
        raise AssertionError("no debería haberse intentado parsear")

    monkeypatch.setattr(svc, "parsear", _explota)
    with pytest.raises(ValueError, match="Extensión"):
        svc.importar(b"%PDF-1.7", archivo="informe.pdf")


def test_dry_run_no_escribe_y_cuenta_lo_que_reemplazaria(monkeypatch):
    monkeypatch.setattr(svc, "_q", lambda *a, **k: [{"n": 287}])

    def _sin_pool(*a, **k):
        raise AssertionError("dry_run no puede abrir una conexión de escritura")

    monkeypatch.setattr(svc, "get_pool", _sin_pool)
    res = svc.importar(_excel([_fila(), _fila()]), archivo="OP.xls", dry_run=True)
    assert res["dry_run"] is True
    assert res["filas"] == 2
    assert res["periodos"] == ["2026-08"]
    assert res["detalle"][0]["existentes"] == 287   # lo que hoy hay y se pisaría
    assert res["borradas"] == 0


def test_fila_sin_fecha_se_descarta_y_se_reporta(monkeypatch):
    """`periodo` es NOT NULL: una fila sin fecha no puede entrar al INSERT."""
    monkeypatch.setattr(svc, "_q", lambda *a, **k: [{"n": 0}])
    monkeypatch.setattr(svc, "get_pool", lambda: (_ for _ in ()).throw(
        AssertionError("dry_run no escribe")))
    res = svc.importar(_excel([_fila(), _fila(fecha="")]), archivo="OP.xls", dry_run=True)
    assert res["filas"] == 1
    assert res["sin_fecha"] == 1


def test_sin_ninguna_fecha_no_importa_nada(monkeypatch):
    """Sin período no hay clave de reemplazo → esas filas quedarían huérfanas
    para siempre (ninguna re-importación posterior las alcanzaría)."""
    monkeypatch.setattr(svc, "_q", lambda *a, **k: [{"n": 0}])
    with pytest.raises(ValueError, match="período"):
        svc.importar(_excel([_fila(fecha=""), _fila(fecha="")]), archivo="OP.xls")


def test_la_carga_del_informe_es_admin_only():
    """La ÚNICA escritura de Mesa de Dinero que no pasa por la allowlist de
    escritores: importar reemplaza un mes entero de un fondo."""
    from api.routers import mesa_dinero as router_mod

    gates = {
        (r.path, dep.dependency.__name__)
        for r in router_mod.router.routes
        for dep in getattr(r, "dependencies", [])
    }
    assert ("/api/mesa-dinero/retorno/import", "require_admin") in gates
