"""Tests de la vista ACA (api/services/aca.py) — docs/ACA.md.

Cubre las fórmulas que la planilla venía haciendo a mano y que ahora son la
única fuente de verdad del informe: el monto por activo, la regla de moneda que
parte Total Dolarizado / Total Pesos, el acumulado encadenado del histórico y el
agrupado de MÉTRICAS GENERALES.

Todo lo de acá es lógica PURA (no toca Postgres): son justo las funciones cuyo
error saldría publicado en un informe para gerencia sin que nadie lo note.
"""
from __future__ import annotations

import pytest

from api.services import aca

# ── Monto por activo ────────────────────────────────────────────────────────

def test_monto_renta_fija_va_en_paridad():
    """HD/DL/ARS cotizan por cada 100 de nominal → vn × px / 100.

    337.842.100 × 106,02 / 100 = 358.180.194,42. La planilla muestra
    358.174.794 para esa fila: la diferencia (0,0015%) es el precio redondeado a
    2 decimales en pantalla, no otra fórmula. Por eso `px` se guarda con la
    precisión que se tipee y el monto se deriva de ahí.
    """
    assert aca._monto_fila("HD", 337_842_100, 106.02, None) == pytest.approx(358_180_194.42)


def test_monto_fci_no_divide_por_cien():
    """Verificado contra la planilla: FCI IAM Performance Americas,
    84.903 × 1,16 = 98.487 (el informe dice exactamente 98.487)."""
    assert aca._monto_fila("FCI", 84_903, 1.16, None) == pytest.approx(98_487.48)


def test_monto_manual_gana_sobre_el_derivado():
    assert aca._monto_fila("HD", 100, 50, 12_345.0) == 12_345.0


def test_sin_precio_no_hay_monto():
    """None ≠ 0: 'todavía no cargado' y 'vale cero' son cosas distintas, y
    devolver 0 haría que un informe a medio cargar parezca completo."""
    assert aca._monto_fila("HD", 1_000, None, None) is None
    assert aca._monto_fila("HD", None, 100.0, None) is None


# ── Regla de moneda (Total Dolarizado / Total Pesos) ─────────────────────────

REGLAS = {
    "cartera": {"HD": "usd", "DL": "usd", "ARS": "ars"},
    "clase":   {"MM USD": "usd", "HD T1": "usd", "MM ARS": "ars",
                "ARS T1": "ars", "RENTA VARIABLE": "ars"},
}


def test_carteras_hd_y_dl_son_dolarizadas():
    assert aca._moneda_de("HD", "", REGLAS) == "usd"
    assert aca._moneda_de("DL", "", REGLAS) == "usd"
    assert aca._moneda_de("ARS", "TAMAR", REGLAS) == "ars"


def test_el_fci_se_parte_por_clase_de_activo():
    """Es la regla que el user confirmó contra los números de su planilla: el
    FCI sigue siendo su propia CARTERA, pero cada fondo suma al total de la
    moneda que le corresponde."""
    assert aca._moneda_de("FCI", "MM USD", REGLAS) == "usd"
    assert aca._moneda_de("FCI", "HD T1", REGLAS) == "usd"
    assert aca._moneda_de("FCI", "MM ARS", REGLAS) == "ars"
    assert aca._moneda_de("FCI", "RENTA VARIABLE", REGLAS) == "ars"


def test_la_clase_gana_sobre_la_cartera():
    """Sin esta precedencia el FCI no se podría partir sin sacarlo de su cartera."""
    reglas = {"cartera": {"FCI": "ars"}, "clase": {"MM USD": "usd"}}
    assert aca._moneda_de("FCI", "MM USD", reglas) == "usd"


def test_clase_desconocida_queda_sin_clasificar():
    """Default-deny del informe: un activo que ninguna regla ubica NO se
    reparte a dedo — sale listado para que alguien lo clasifique."""
    assert aca._moneda_de("FCI", "CLASE NUEVA", REGLAS) is None
    assert aca._moneda_de("", "", REGLAS) is None


def test_la_regla_no_distingue_mayusculas():
    assert aca._moneda_de("hd", "", REGLAS) == "usd"
    assert aca._moneda_de("FCI", "mm usd", REGLAS) == "usd"


# ── Acumulado encadenado del histórico ──────────────────────────────────────

def test_acumulado_encadena_como_la_planilla():
    """acum = (1 + acum_anterior) × (1 + mensual) − 1, que es la fórmula que el
    user arrastra en el Excel."""
    out = aca._acumular([0.02, 0.03])
    assert out[0] == pytest.approx(0.02)
    assert out[1] == pytest.approx(1.02 * 1.03 - 1)  # 0.0506


def test_acumulado_arrastra_los_meses_sin_dato():
    """Un mes vacío NO reinicia ni rompe la serie: 'no sé cuánto rindió' no es
    'rindió 0'. Es lo que hace la planilla con las columnas todavía en blanco."""
    out = aca._acumular([0.10, None, 0.10])
    assert out[1] == pytest.approx(0.10)
    assert out[2] == pytest.approx(1.10 * 1.10 - 1)


def test_antes_del_primer_dato_no_hay_acumulado():
    """None, no 0: todavía no arrancó la serie y un 0 se leería como 'no rindió'."""
    assert aca._acumular([None, None]) == [None, None]


def test_acumulado_soporta_meses_negativos():
    out = aca._acumular([0.10, -0.05])
    assert out[1] == pytest.approx(1.10 * 0.95 - 1)


# ── MÉTRICAS GENERALES — agrupado con catálogo ──────────────────────────────

FILAS = [
    {"emisor": "TESORO",      "monto": 800.0},
    {"emisor": "CREDICUOTAS", "monto": 200.0},
    {"emisor": "SORPRESA SA", "monto": 100.0},
]


def test_el_catalogo_muestra_las_filas_en_cero():
    """Que YPF valga 0 es información ('no tenemos nada de YPF'); que la fila
    desaparezca no dice nada."""
    out = aca._agrupar(FILAS, "emisor", ["TESORO", "YPF"], 1100.0)
    claves = {f["clave"]: f for f in out}
    assert claves["YPF"]["monto"] == 0.0
    assert claves["TESORO"]["share"] == pytest.approx(800 / 1100)


def test_lo_que_no_esta_en_el_catalogo_igual_aparece_marcado():
    """El catálogo agrega filas, NUNCA esconde plata: un emisor nuevo se ve,
    tagueado, en vez de evaporarse del informe."""
    out = aca._agrupar(FILAS, "emisor", ["TESORO"], 1100.0)
    extra = [f for f in out if f["fuera_catalogo"]]
    assert {f["clave"] for f in extra} == {"CREDICUOTAS", "SORPRESA SA"}


def test_denominador_cero_no_explota():
    out = aca._agrupar([], "emisor", ["TESORO"], 0.0)
    assert out[0]["monto"] == 0.0 and out[0]["share"] is None


# ── Validación de período ───────────────────────────────────────────────────

def test_periodo_valido():
    assert aca._norm_periodo(" 2026-07 ") == "2026-07"


@pytest.mark.parametrize("malo", ["2026-7", "202607", "2026-13", "2026-00", "", None])
def test_periodo_invalido(malo):
    with pytest.raises(ValueError):
        aca._norm_periodo(malo)


# ── RBAC: la vista NO puede filtrarse al portal invitado (REGLA #8) ─────────

def test_aca_es_modulo_canonico_y_no_del_invitado():
    from core.roles import INVITADO_MODULES, MODULES
    assert "aca" in MODULES
    assert "aca" not in INVITADO_MODULES, (
        "REGLA #8: la cartera propia es negocio de la casa — jamás al portal www")


def test_el_rol_default_no_ve_aca():
    """`sales` es DEFAULT_ROLE: todo email nuevo cae ahí. Si alguna vez alguien
    le cuelga `aca`, cualquier alta automática pasa a ver la cartera propia."""
    from core.roles import DEFAULT_MATRIX, DEFAULT_ROLE
    assert DEFAULT_ROLE == "sales"
    assert "aca" not in DEFAULT_MATRIX["sales"]
    assert "aca" in DEFAULT_MATRIX["empleado_aca"]


def test_api_aca_no_esta_en_la_allowlist_del_portal_invitado():
    from api.auth import path_permitido_invitado
    assert not path_permitido_invitado("/api/aca/vista")
    assert not path_permitido_invitado("/api/aca")


# ── El rol NO da escritura (empleado_aca es SOLO LECTURA) ───────────────────
# Congelado por pedido del user 2026-08-13: "los de empleado aca es solo
# lectura, no tienen que ver estas cosas". El permiso de escribir lo da la
# allowlist de Mesa de Dinero, NUNCA el rol — si alguien mañana cablea el rol
# acá, esto falla.

def test_el_rol_empleado_aca_no_da_escritura(monkeypatch):
    """`empleado_aca` ve la vista pero NO puede editarla."""
    from api.services import mesa_dinero
    monkeypatch.setattr("core.roles.get_user_role", lambda e: "empleado_aca")
    monkeypatch.setattr(mesa_dinero, "_q", lambda *a, **k: [])  # no está en la allowlist
    assert aca.puede_escribir("gerente@aca.com") is False


def test_admin_si_escribe(monkeypatch):
    from api.services import mesa_dinero
    monkeypatch.setattr("core.roles.get_user_role", lambda e: "admin")
    monkeypatch.setattr(mesa_dinero, "_q", lambda *a, **k: [])
    assert aca.puede_escribir("jefe@aca.com") is True


def test_la_allowlist_de_la_mesa_si_da_escritura(monkeypatch):
    """Un trader de la mesa escribe aunque su rol no sea empleado_aca."""
    from api.services import mesa_dinero
    monkeypatch.setattr("core.roles.get_user_role", lambda e: "trader")
    monkeypatch.setattr(mesa_dinero, "_q", lambda *a, **k: [{"?column?": 1}])
    assert aca.puede_escribir("trader@aca.com") is True


def test_toda_escritura_del_service_valida_el_permiso():
    """Ninguna función de escritura puede saltearse `_check_escritura`.

    Es el chequeo que sostiene el 'solo lectura': el front esconde los botones,
    pero lo que IMPIDE escribir es esto. Si alguien agrega una escritura nueva y
    se olvida del gate, este test lo caza.
    """
    import inspect
    fuente = inspect.getsource(aca)
    escrituras = [
        "guardar_periodo", "borrar_periodo", "guardar_activo", "borrar_activo",
        "clonar_periodo", "guardar_historico", "borrar_historico",
        "set_moneda_regla", "del_moneda_regla", "set_emisor_destacado",
        "del_emisor_destacado", "set_clase_destacada", "del_clase_destacada",
        "set_serie", "del_serie",
    ]
    for nombre in escrituras:
        assert hasattr(aca, nombre), f"{nombre} ya no existe — actualizá el test"
        cuerpo = fuente.split(f"def {nombre}(", 1)[1].split("\ndef ", 1)[0]
        assert "_check_escritura(actor)" in cuerpo, (
            f"{nombre} escribe SIN validar el permiso — un empleado_aca podría editarlo")


# ── El informe abre cuadro para CUALQUIER cartera cargada ──────────────────
# Incidente 2026-08-19: 3 títulos con cartera DEUDORES (que existe en el maestro
# de Manager → Títulos) sumaban al total pero no aparecían en ningún cuadro, y
# la vista los cantaba como huérfanos. La lista de carteras era una constante
# cerrada de 4, así que la única forma de agregar una era tocar el código —
# ni creando la cartera ni dándole regla de moneda se resolvía.

def _fila(cartera, monto=100.0):
    return {"cartera": cartera, "monto": monto}


def test_una_cartera_nueva_abre_su_propio_cuadro():
    filas = [_fila("ARS"), _fila("DEUDORES")]
    assert "DEUDORES" in aca._carteras_de(filas)


def test_las_cuatro_canonicas_van_siempre_y_primero():
    """Aunque cierren en cero: que una cartera valga 0 es información."""
    orden = aca._carteras_de([_fila("DEUDORES")])
    assert orden[:4] == list(aca.CARTERAS)
    assert orden == ["ARS", "DL", "HD", "FCI", "DEUDORES"]


def test_las_carteras_extra_van_ordenadas_y_sin_repetir():
    orden = aca._carteras_de([_fila("ZZZ"), _fila("DEUDORES"), _fila("DEUDORES")])
    assert orden[4:] == ["DEUDORES", "ZZZ"]


def test_la_cartera_se_normaliza():
    """El maestro puede tener ' deudores ' y el informe 'DEUDORES': una sola."""
    assert aca._carteras_de([_fila(" deudores "), _fila("DEUDORES")])[4:] == ["DEUDORES"]


def test_sin_ficha_no_inventa_una_cartera():
    """Un título que no existe en portafolio.assets no abre cuadro — es el
    único huérfano de verdad."""
    assert aca._carteras_de([_fila(None), _fila("")]) == list(aca.CARTERAS)


def test_el_label_de_una_cartera_nueva_no_queda_pelado():
    assert aca._label_cartera("ARS") == "Cartera Pesos"
    assert aca._label_cartera("DEUDORES") == "Cartera DEUDORES"


def test_una_cartera_nueva_NO_cotiza_en_paridad():
    """Congela la decisión: el divisor no se adivina. Una cartera fuera de
    ARS/DL/HD vale vn × px. Si alguna nueva cotiza en paridad hay que sumarla a
    _DIVISOR_PARIDAD a mano — errarle es un factor 100."""
    assert aca._monto_fila("DEUDORES", 1000, 80, None) == 80_000
    assert aca._monto_fila("ARS", 1000, 80, None) == 800


# ── NADA se completa solo desde otra fuente (2026-08-19) ───────────────────
# Decisión del user, textual: "nada de ACA tiene que ser automático… en el
# sentido de cargar datos solos de otras fuentes". El histórico tenía series con
# `fuente = macro_var:<SERIE>` que traían el mensual de `macro.series_macro`
# (A3500 ← DOLAR) y por eso esa celda aparecía llena sola. Se dio de baja.
# El ACUMULADO sigue derivándose: no importa un dato, encadena lo que se tipeó.

def test_el_historico_no_lee_ninguna_otra_fuente():
    """Ni `macro.series_macro` ni ningún otro origen automático."""
    import inspect
    fuente = inspect.getsource(aca)
    for prohibido in ("macro_var", "macro_pct", "_macro_mensual", "series_macro"):
        assert prohibido not in fuente, (
            f"volvió la automatización del histórico ({prohibido}): el mensual de "
            "ACA se tipea, punto")


def test_el_catalogo_de_series_no_expone_fuente_ni_escala():
    """Son columnas VESTIGIALES: si el payload las publica, el front vuelve a
    ofrecer un campo que no controla nada."""
    import inspect
    cuerpo = inspect.getsource(aca._series_catalogo)
    assert '"fuente"' not in cuerpo
    assert '"escala"' not in cuerpo


def test_set_serie_no_escribe_fuente_ni_escala():
    """Pinnea el set de columnas del INSERT: si vuelve `fuente`, vuelve la
    posibilidad de configurar un origen automático desde la pantalla."""
    import inspect
    cuerpo = inspect.getsource(aca.set_serie)
    assert "INSERT INTO aca.series (codigo, nombre, grupo, graficos, " in cuerpo
    assert 'payload.get("fuente")' not in cuerpo
    assert 'payload.get("escala")' not in cuerpo


def test_el_acumulado_sigue_siendo_derivado():
    """Lo ÚNICO automático que queda, y es correcto: sale de los mensuales
    tipeados, no de otra fuente."""
    assert aca._acumular([0.05, 0.05]) == pytest.approx([0.05, 0.1025])


def test_el_schema_normaliza_las_fuentes_viejas():
    """El seed solo corre con la tabla vacía, así que en prod la fila del A3500
    seguiría diciendo 'macro_var:DOLAR' — una afirmación falsa sobre lo que hace
    el sistema."""
    import pathlib
    sql = pathlib.Path("sql/schema.sql").read_text(encoding="utf-8")
    assert "UPDATE aca.series SET fuente = 'manual' WHERE fuente <> 'manual';" in sql
    assert "'macro_var:DOLAR'" not in sql, "el seed sigue sembrando una fuente automática"


# ── La tab Manager → ACA la da la ESCRITURA, no el umbrella `manager` ──────
# Decisión del user 2026-08-19: ya hay gente con permiso de escritura en la mesa
# que necesita cargar el histórico y NO es admin (ej. `asistente_comercial`).
# Antes la tab pedía el módulo `manager`, que es todo-o-nada: darlo abría JOBS,
# LOGS, USUARIOS y ROLES. Ahora el gate efectivo es (acceso a Manager) Y
# (escritura en ACA) — las dos condiciones, no una.

def test_la_tab_de_manager_la_gatea_la_escritura_y_no_el_modulo_manager():
    """El sub-router de la tab NO puede volver a colgarse de `require_module`."""
    import inspect

    from api.routers import manager as pkg
    fuente = inspect.getsource(pkg)
    assert "_ACA             = [Depends(verify_api_key), Depends(require_escritura_aca)]" in fuente
    assert "router.include_router(aca.router,         dependencies=_ACA)" in fuente, (
        "la tab Manager → ACA volvió al umbrella `manager`: un escritor de la mesa "
        "que no es admin deja de poder cargar el histórico")


def test_el_gate_efectivo_de_la_tab_exige_manager_Y_escritura():
    """Medido sobre la app montada, no sobre el código: las dos deps tienen que
    estar en TODAS las rutas de la tab. Sin la de módulo, un escritor de la mesa
    sin acceso a Manager entraría; sin la de escritura, cualquier admin-de-otra-
    tab configuraría el informe."""
    from api.main import app
    from api.superficie import rutas
    rs = [r for r in rutas(app) if r.path.startswith("/api/manager/aca/")]
    assert rs, "no hay rutas de la tab — cambió el prefijo"
    for r in rs:
        assert "require_escritura_aca" in r.gates, f"{r.path} sin gate de escritura"
        assert any(g.startswith("require_any_module_manager") for g in r.gates), (
            f"{r.path} quedó fuera del gate de acceso a Manager")


def test_empleado_aca_no_entra_a_la_tab_de_manager(monkeypatch):
    """El rol es SOLO LECTURA de la vista: ni escribe ni ve Manager."""
    from core.roles import DEFAULT_MATRIX
    modulos = DEFAULT_MATRIX["empleado_aca"]
    assert not any(m == "manager" or m.startswith("manager_") for m in modulos), (
        "empleado_aca no puede tener acceso a Manager")

    from api.services import mesa_dinero
    monkeypatch.setattr("core.roles.get_user_role", lambda e: "empleado_aca")
    monkeypatch.setattr(mesa_dinero, "_q", lambda *a, **k: [])
    assert aca.puede_ver_manager(email="gerente@aca.com") is False


def test_la_capacidad_del_nav_espeja_al_gate(monkeypatch):
    """`manager-aca` (lo que publica /api/me) tiene que valer lo mismo que el
    gate server-side, si no el front esconde una tab que el backend permite (o
    peor, muestra una que da 403)."""
    from api.services import mesa_dinero
    monkeypatch.setattr("core.roles.get_user_role", lambda e: "asistente_comercial")
    monkeypatch.setattr(mesa_dinero, "_q", lambda *a, **k: [{"?column?": 1}])
    aca.invalidar_permisos()
    assert aca.puede_ver_manager(email="asistente@aca.com") is True
    aca.invalidar_permisos()


def test_manager_aca_no_es_un_modulo_del_rbac():
    """Es una CAPACIDAD por allowlist, igual que `mesa-dinero`. Meterla en
    MODULES pondría un checkbox en ROLES Y PERMISOS que no controla nada."""
    from core.roles import MODULES
    assert "manager-aca" not in MODULES


def test_tocar_la_allowlist_de_la_mesa_purga_el_cache_de_aca():
    """El admin agrega a alguien y la persona recarga: tiene que ver la tab ya,
    no dentro de 60s (que se lee como 'no funcionó')."""
    import inspect

    from api.services import mesa_dinero
    cuerpo = inspect.getsource(mesa_dinero._invalidar_permisos)
    assert "aca.invalidar_permisos()" in cuerpo


# ── El contrato de /vista: `periodos` son OBJETOS, no strings ───────────────
# Regresión del 2026-08-13: `vista()` hacía `**graficos()` al final, y esa
# función devuelve su PROPIA clave `periodos` (el eje X de los charts, strings).
# El spread pisaba la lista con metadata que arma el selector de la barra, el
# front hacía `p.periodo` sobre un string y `undefined.split()` tumbaba la vista
# entera. Invisible hasta cargar el PRIMER informe: con las dos listas vacías el
# pisón no se notaba.

def _vista_mockeada(monkeypatch, con_periodo: str | None):
    """vista() con todo lo que toca la base reemplazado — testea el ARMADO."""
    monkeypatch.setattr(aca, "listar_periodos", lambda: {"periodos": [
        {"periodo": "2026-07", "fecha_informe": "2026-07-31", "mep": 1517.67,
         "a3500": 1488.45, "nota": "", "n_activos": 5}]})
    monkeypatch.setattr(aca, "graficos", lambda **kw: {
        "periodos": ["2026-06", "2026-07"],          # strings — el eje X
        "graficos": [{"grafico": "total_ars", "series": []}]})
    monkeypatch.setattr(aca, "puede_escribir", lambda e: False)
    monkeypatch.setattr(aca, "_ultimo_periodo", lambda: con_periodo)
    monkeypatch.setattr(aca, "resumen", lambda p: {"actual": {}, "anterior": None})
    monkeypatch.setattr(aca, "detalle", lambda p: {"bloques": []})
    monkeypatch.setattr(aca, "metricas", lambda p: {"por_clase": []})
    return aca.vista(email="x@y.com")


@pytest.mark.parametrize("con_periodo", ["2026-07", None])
def test_vista_no_pisa_periodos_con_el_eje_de_los_graficos(monkeypatch, con_periodo):
    """`periodos` tiene que seguir siendo la lista de OBJETOS del selector."""
    out = _vista_mockeada(monkeypatch, con_periodo)
    assert isinstance(out["periodos"], list)
    for p in out["periodos"]:
        assert isinstance(p, dict), (
            "`periodos` volvió a ser una lista de strings → el selector de la barra "
            "hace p.periodo sobre un string y la vista explota con undefined.split()")
        assert "periodo" in p and "fecha_informe" in p
    # El eje de los charts existe, pero en SU propia clave.
    assert out["periodos_grafico"] == ["2026-06", "2026-07"]
    assert out["graficos"] and out["graficos"][0]["grafico"] == "total_ars"


# ── Import de Excel: resolver el título y parsear los números ───────────────
# Las dos cosas donde un error entra SIN avisar: pegarle al título equivocado
# (queda otro papel en el informe de gerencia) o leer mal el número (precio
# dividido por mil y nadie lo nota hasta ver el total).

CATALOGO = [
    {"unidad": "[1] DHSIO",  "ticker": "DHSIO", "instrumento": "DHSIO CREDICUOTAS"},
    {"unidad": "[2] RMJ28",  "ticker": "RMJ28", "instrumento": "BONO MUN. ROSARIO"},
    {"unidad": "[3] GD30",   "ticker": "GD30",  "instrumento": "GLOBAL 2030"},
    {"unidad": "[4] GD30v2", "ticker": "GD30",  "instrumento": "GLOBAL 2030 BIS"},
]


def test_resuelve_por_ticker_exacto():
    assert aca._resolver_titulo("DHSIO", CATALOGO)[0] == "[1] DHSIO"
    assert aca._resolver_titulo(" dhsio ", CATALOGO)[0] == "[1] DHSIO"


def test_resuelve_el_ticker_pegado_al_nombre():
    """La planilla trae 'RMJ28 - BONO MUN. ROSARIO 26/06/28 $', no el ticker pelado."""
    u, motivo = aca._resolver_titulo("RMJ28 - BONO MUN. ROSARIO 26/06/28 $", CATALOGO)
    assert u == "[2] RMJ28" and "ticker" in motivo


def test_resuelve_por_unidad_exacta():
    assert aca._resolver_titulo("[3] GD30", CATALOGO)[0] == "[3] GD30"


def test_ticker_ambiguo_SI_se_importa_y_se_avisa():
    """Decisión del user 2026-08-13: "por más que sea ambiguo, si el ticker existe
    ponelo". Antes se descartaba la fila y eso dejaba afuera plata que SÍ existe
    por un problema de catálogo. Se elige DETERMINISTA (la primera por unidad) y
    se marca — elegir mal se corrige en un clic; que no aparezca, no se ve."""
    u, motivo = aca._resolver_titulo("GD30", CATALOGO)
    assert u == "[3] GD30", "sin pistas gana la primera por unidad ordenada"
    assert "AMBIGUO" in motivo and "[4] GD30v2" in motivo


def test_ticker_ambiguo_prefiere_la_unidad_YA_cargada():
    """Continuidad: si el período ya venía usando una de las dos, es esa. Sin
    esto, re-importar el mismo archivo podía cambiar de unidad mes a mes."""
    u, _ = aca._resolver_titulo("GD30", CATALOGO, ya_cargadas={"[4] GD30v2"})
    assert u == "[4] GD30v2"


def test_ticker_ambiguo_es_estable():
    """El MISMO archivo tiene que importar SIEMPRE igual — si no, dos corridas
    dan informes distintos y nadie sabe cuál es el bueno."""
    assert (aca._resolver_titulo("GD30", CATALOGO)[0]
            == aca._resolver_titulo("GD30", list(reversed(CATALOGO)))[0])


def test_titulo_desconocido_no_se_inventa():
    u, motivo = aca._resolver_titulo("PAPELINVENTADO", CATALOGO)
    assert u is None and "catálogo" in motivo
    assert aca._resolver_titulo("", CATALOGO)[0] is None


@pytest.mark.parametrize("crudo,esperado", [
    (106.02, 106.02),                 # ya viene numérico de SheetJS
    ("106,02", 106.02),               # es-AR con coma decimal
    ("337.842.100", 337_842_100.0),   # solo puntos = miles
    ("337842100", 337_842_100.0),
    ("1.234.567,89", 1_234_567.89),   # miles + decimal
    ("106.02", 106.02),               # un punto con 2 decimales = decimal
    ("2.776", 2776.0),                # un punto con 3 dígitos detrás = MILES
    ("-5,5", -5.5),
    ("$ 1.000", 1000.0),
    ("", None), (None, None), ("no es un número", None),
])
def test_parseo_de_numeros_del_excel(crudo, esperado):
    assert aca._num(crudo) == esperado


def test_el_bool_no_se_cuela_como_numero():
    """True es int en Python; si se colara, un tilde del Excel entraría como 1."""
    assert aca._num(True) is None
