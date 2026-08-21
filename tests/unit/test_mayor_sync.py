"""`jobs/mayor_sync`: qué se guarda del mayor y qué NO.

Lo que se testea son las decisiones que no se pueden leer del código de un
vistazo y que fallan en silencio si se rompen: qué día se pide, qué movimientos
entran, y las guardas que evitan que un día bueno se pise con basura.
"""
from __future__ import annotations

from datetime import date

import pytest

from jobs import mayor_sync

CTA_PATA = "101010100002"
CTA_VALO = "101010200005"
MAPEO = {CTA_PATA: 7, CTA_VALO: 9}
DIA = date(2026, 8, 19)


def _mov(codigo, valuacion, *, mid="1", nombre="Banco Patagonia CC 304-100753595-000"):
    return {"movimientoID": mid, "codigoCuenta": codigo, "nombreCuenta": nombre,
            "codigoUnidad": "ARS", "cantidad": valuacion, "factor": "1",
            "valuacion": valuacion, "cuentaID": "CTB", "numeroOperacion": "1135703",
            "comprobante": "NT 2026010519", "codigoExp": "", "referencia": "detalle"}


def _asiento(movs, *, numero="-4893409", referencia="[Op. 1135703] Depósito - Depósito CD 1- Cta. 243/GRAL"):
    return {"asientoID": "1098137", "numero": numero, "fechaAlta": "20/08/2026",
            "fechaConciliacion": "19/08/2026", "referencia": referencia,
            "movimientos": movs}


# ── qué entra ────────────────────────────────────────────────────────────────

def test_solo_entran_las_cuentas_mapeadas():
    """De los ~23.600 movimientos del día, la enorme mayoría NO son bancarios."""
    registros = [_asiento([
        _mov(CTA_PATA, "18711185.35", mid="1"),
        _mov("201050000002", "16929080.00", mid="2", nombre="Comitente - (GRAL) Operaciones"),
        _mov("201030000001", "-2547.93", mid="3", nombre="IVA DF"),
        _mov(CTA_VALO, "500.00", mid="4", nombre="Banco de Valores ARS"),
    ])]
    r = mayor_sync._extraer(registros, MAPEO, DIA)
    assert r["movimientos_api"] == 4
    assert len(r["filas"]) == 2
    assert {f[5] for f in r["filas"]} == {CTA_PATA, CTA_VALO}


def test_el_importe_conserva_el_signo_de_valuacion():
    """`importe` = Debe − Haber, que es como ya lo espera el conciliador."""
    registros = [_asiento([_mov(CTA_PATA, "-1100000.00", mid="9")])]
    fila = mayor_sync._extraer(registros, MAPEO, DIA)["filas"][0]
    assert fila[8] == -1100000.00


def test_el_concepto_es_la_referencia_del_asiento():
    """Es el texto que `_grupo_mayor` sabe cortar para el CONSOLIDADO. Si se
    guardara la referencia del movimiento, el agrupado saldría distinto."""
    registros = [_asiento([_mov(CTA_PATA, "100.00")])]
    fila = mayor_sync._extraer(registros, MAPEO, DIA)["filas"][0]
    assert fila[9].startswith("[Op. 1135703] Depósito")


def test_guarda_las_dos_fechas_y_no_las_confunde():
    """`fecha_conciliacion` 19/08 y `fecha_alta` 20/08: el asiento se carga al día
    siguiente, y confundirlas correría todo un día."""
    registros = [_asiento([_mov(CTA_PATA, "100.00")])]
    fila = mayor_sync._extraer(registros, MAPEO, DIA)["filas"][0]
    assert fila[3] == date(2026, 8, 19)
    assert fila[4] == date(2026, 8, 20)


# ── las tres guardas ─────────────────────────────────────────────────────────

def test_avisa_de_cuentas_bancarias_sin_mapear():
    """Sus movimientos se descartan, y esa plata aparecería como diferencia de
    conciliación. Tiene que quedar dicho, no tragarse en silencio."""
    registros = [_asiento([
        _mov(CTA_PATA, "100.00", mid="1"),
        _mov("101010200099", "500.00", mid="2", nombre="BANCO GALICIA ACDI ARS"),
    ])]
    r = mayor_sync._extraer(registros, MAPEO, DIA)
    assert r["sin_mapear"] == {"101010200099": "BANCO GALICIA ACDI ARS"}
    assert len(r["filas"]) == 1


def test_no_avisa_por_cuentas_que_no_son_bancos():
    """Sin este filtro el aviso listaría las ~80 cuentas contables que no son
    bancos y nadie lo leería."""
    registros = [_asiento([
        _mov("201050000002", "1.00", mid="2", nombre="Comitente - (GRAL) Operaciones"),
        _mov("101010100004", "2.00", mid="3", nombre="Asignación a inversiones (Regularizadora)"),
    ])]
    assert mayor_sync._extraer(registros, MAPEO, DIA)["sin_mapear"] == {}


def test_deduplica_movimiento_id_repetido():
    """Es la PK: un duplicado mataría el insert a mitad de camino."""
    registros = [_asiento([_mov(CTA_PATA, "100.00", mid="777"),
                           _mov(CTA_PATA, "100.00", mid="777")])]
    r = mayor_sync._extraer(registros, MAPEO, DIA)
    assert len(r["filas"]) == 1
    assert r["duplicados"] == 1


def test_sin_mapeo_cargado_no_corre(monkeypatch):
    """Con el mapeo vacío guardaría CERO y el día quedaría indistinguible de
    «no hubo movimientos»."""
    monkeypatch.setattr(mayor_sync, "_mapeo", dict)
    with pytest.raises(RuntimeError, match="codigo_contable"):
        mayor_sync.run(DIA)


def test_no_vacia_un_dia_que_tenia_movimientos(monkeypatch):
    """Anulación real y respuesta rota se ven igual desde acá. Ante la duda se
    conserva: recuperar un día borrado es mucho más caro."""
    monkeypatch.setattr(mayor_sync, "_mapeo", lambda: MAPEO)
    monkeypatch.setattr(mayor_sync, "_guardados", lambda d: 40)
    monkeypatch.setattr(mayor_sync.aunesa, "get",
                        lambda *a, **k: _Resp({"registros": []}))
    aplicado = []
    monkeypatch.setattr(mayor_sync, "_reemplazar",
                        lambda d, f: aplicado.append(f))

    stats = mayor_sync.run(DIA)
    assert stats["aplicado"] is False
    assert aplicado == []

    stats = mayor_sync.run(DIA, forzar=True)
    assert stats["aplicado"] is True


def test_un_dia_vacio_que_ya_estaba_vacio_si_se_aplica(monkeypatch):
    """La guarda es contra PERDER datos, no contra los días sin movimientos."""
    monkeypatch.setattr(mayor_sync, "_mapeo", lambda: MAPEO)
    monkeypatch.setattr(mayor_sync, "_guardados", lambda d: 0)
    monkeypatch.setattr(mayor_sync.aunesa, "get",
                        lambda *a, **k: _Resp({"registros": []}))
    monkeypatch.setattr(mayor_sync, "_reemplazar", lambda d, f: len(f))
    assert mayor_sync.run(DIA)["aplicado"] is True


# ── la fecha ─────────────────────────────────────────────────────────────────

def test_pide_el_mismo_dia_que_muestra_la_vista():
    """Si derivaran, el job traería el mayor de un día y la pantalla mostraría
    el banco de otro — y la diferencia sería del calendario, no de la plata."""
    from api.services.bancos import fecha_default

    assert mayor_sync.fecha_objetivo() == fecha_default()


def test_la_fecha_se_parsea_como_dd_mm_yyyy():
    """`08/09/2026` es 8 de SEPTIEMBRE. Leerlo al revés movería el día tres meses."""
    assert mayor_sync._fecha_iso("08/09/2026") == date(2026, 9, 8)
    assert mayor_sync._fecha_iso("") is None
    assert mayor_sync._fecha_iso("2026-09-08") is None


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
        self.text = ""

    def json(self):
        return self._payload
