"""Clasificación de GASTOS BANCARIOS: qué movimiento cobró el banco.

Esto decide un número que el back office lee como plata, y falla **en silencio**:
si la clasificación se rompe no salta ninguna excepción — la columna muestra otro
total, y nadie tiene con qué darse cuenta. Por eso la lógica es PURA (sin base,
sin red) y vive con tests propios.

Las dos capas y su orden de precedencia son el modelo entero:
  · REGLA    = el conocimiento durable ("todo lo que tenga el código 830").
  · OVERRIDE = el parche puntual sobre UN movimiento. **Gana siempre.**
"""
from __future__ import annotations

from datetime import date

import pytest

from api.services.bancos import (
    CAMPOS_REGLA,
    RESTO,
    clasificar,
    desglosar,
    semilla_catalogo,
)

# El catálogo con el que se prueba el desglose. Desde el 2026-08-18 los baldes
# viven en la BASE y los edita el equipo: acá se prueba la SEMILLA, que es lo que
# se carga la primera vez y el estado del que parte cualquier edición.
BALDES = semilla_catalogo()

MOV = {
    "mov_hash": "h1", "cuenta_id": 1, "importe": 1500.0, "tipo": "D",
    "codigo_operacion_ib": "830", "codigo_operacion_banco": "00108",
    "descripcion_banco": "COMISION MANTENIMIENTO CUENTA",
    "descripcion_ib": "GASTOS",
}


def _regla(**kw):
    return {"id": 1, "campo": "codigo_ib", "operador": "igual", "valor": "830",
            "activa": True, **kw}


def test_regla_por_codigo_exacto():
    r = clasificar([MOV], [_regla()], {})["h1"]
    assert r["es_gasto"] is True and r["origen"] == "regla" and r["regla"] == 1


def test_regla_por_texto_contenido():
    """Con lo que van a arrancar: todavía no saben qué códigos usa cada banco,
    pero sí reconocen la palabra en la descripción."""
    r = _regla(campo="descripcion_banco", operador="contiene", valor="comision")
    assert clasificar([MOV], [r], {})["h1"]["es_gasto"] is True


def test_contiene_no_matchea_lo_que_no_contiene():
    r = _regla(campo="descripcion_banco", operador="contiene", valor="impuesto")
    assert clasificar([MOV], [r], {})["h1"]["es_gasto"] is False


def test_igual_no_matchea_parcialmente():
    """`igual` es igual. Si '83' matcheara a '830', una regla estrecha se comería
    media tabla sin que nadie lo pida."""
    assert clasificar([MOV], [_regla(valor="83")], {})["h1"]["es_gasto"] is False


def test_ignora_mayusculas_y_espacios():
    """La API manda '00108 ' y 'COMISION…'; nadie va a escribir una regla
    replicando esos espacios."""
    r = _regla(campo="descripcion_banco", operador="contiene", valor="  CoMiSiOn  ")
    assert clasificar([MOV], [r], {})["h1"]["es_gasto"] is True


def test_una_regla_inactiva_no_clasifica():
    assert clasificar([MOV], [_regla(activa=False)], {})["h1"]["es_gasto"] is False


def test_sin_reglas_no_es_gasto_y_sin_origen():
    r = clasificar([MOV], [], {})["h1"]
    assert r["es_gasto"] is False and r["origen"] is None


def test_el_override_GANA_sobre_la_regla():
    """EL invariante. Si una persona lo decidió, la máquina no se lo da vuelta."""
    r = clasificar([MOV], [_regla()], {"h1": False})["h1"]
    assert r["es_gasto"] is False, "la regla pisó una marca manual"
    assert r["origen"] == "manual"


def test_el_override_tambien_marca_lo_que_ninguna_regla_agarra():
    """El caso poco frecuente: no hay regla, el usuario lo marca en el momento."""
    r = clasificar([MOV], [], {"h1": True})["h1"]
    assert r["es_gasto"] is True and r["origen"] == "manual"


def test_un_campo_que_no_existe_no_matchea_nunca():
    """`campo` lo escribe un usuario. Si viajara a un WHERE sería una inyección;
    acá simplemente no matchea."""
    r = _regla(campo="importe; DROP TABLE bancos.movimientos", operador="igual", valor="1500")
    assert clasificar([MOV], [r], {})["h1"]["es_gasto"] is False


def test_una_regla_con_valor_vacio_no_agarra_todo():
    """Con `contiene`, un valor vacío está contenido en CUALQUIER texto: sin este
    corte, guardar una regla a medias marcaría todos los movimientos como gasto."""
    r = _regla(campo="descripcion_banco", operador="contiene", valor="   ")
    assert clasificar([MOV], [r], {})["h1"]["es_gasto"] is False


@pytest.mark.parametrize("campo", sorted(CAMPOS_REGLA))
def test_todos_los_campos_declarados_funcionan(campo):
    """Si se suma un campo a CAMPOS_REGLA y la columna no existe en el
    movimiento, la regla nunca matchearía y nadie se enteraría."""
    valor = MOV[CAMPOS_REGLA[campo]]
    r = _regla(campo=campo, operador="igual", valor=valor)
    assert clasificar([MOV], [r], {})["h1"]["es_gasto"] is True, (
        f"el campo '{campo}' está declarado pero no matchea ni con su propio valor"
    )


# ── DESGLOSE ────────────────────────────────────────────────────────────────
#
# Es una separación de PRESENTACIÓN: no cambia ningún número, parte el que ya
# está. Pero si un movimiento cayera en dos baldes, el desglose daría MÁS que el
# total — y una fila que no cierra hace que nadie confíe en ninguna celda.

def _m(concepto="", descripcion=""):
    return {"descripcion_ib": concepto, "descripcion_banco": descripcion}


def test_IVA_no_se_come_a_IVAPERCEP():
    """EL caso que obliga a usar `igual` y no `contiene`: «IVA» es prefijo de
    «IVAPERCEP», así que con `contiene` la columna IVA mostraría de más y
    IVAPERCEP quedaría en cero."""
    assert desglosar(_m(concepto="IVA"), BALDES) == "iva"
    assert desglosar(_m(concepto="IVAPERCEP"), BALDES) == "ivapercep"
    assert desglosar(_m(concepto="IIBBPERCEP"), BALDES) == "iibbpercep"


def test_com_transf_matchea_por_contenido():
    """Este sí viene truncado, así que va por `contiene`."""
    assert desglosar(_m(concepto="COM.TRANSF"), BALDES) == "comtransf"


def test_el_impuesto_al_debito_tiene_DOS_grafias():
    """Patagonia escribe `IMP.DB/CR BANCARIOS P/DEB`; BIND, `LEY25413DB`. Es el
    mismo impuesto y tiene que caer en el mismo balde."""
    assert desglosar(_m(descripcion="IMP.DB/CR BANCARIOS P/DEB"), BALDES) == "imp_debito"
    assert desglosar(_m(descripcion="LEY25413DB"), BALDES) == "imp_debito"


def test_el_concepto_gana_sobre_la_descripcion():
    """Un movimiento con concepto IVA y descripción con SELLOS cuenta UNA vez y
    siempre del mismo lado. Si sumara en los dos, el desglose daría más que el
    total."""
    assert desglosar(_m(concepto="IVA", descripcion="IMPUESTO A LOS SELLOS"), BALDES) == "iva"


def test_lo_que_no_cae_en_ningun_balde_va_a_RESTO():
    """OTROS IMP son SOLO las 4 descripciones declaradas (decisión del back
    office), así que puede quedar gasto afuera de toda columna. Ese gasto NO se
    reparte a dedo: cae en RESTO y la vista lo canta."""
    assert desglosar(_m(concepto="GIROS/TRF", descripcion="COMISION RARA"), BALDES) == RESTO


def test_ningun_movimiento_cae_en_dos_baldes():
    """El invariante que hace que el desglose SUME: cada matcher declarado tiene
    que caer en SU balde y no en otro. Se prueba con el valor exacto de cada uno."""
    for balde in BALDES:
        for mt in balde["matchers"]:
            campo, valor = mt["campo"], mt["valor"]
            mov = _m(**{"concepto" if campo == "descripcion_ib" else "descripcion": valor})
            assert desglosar(mov, BALDES) == balde["clave"], (
                f"«{valor}» debería caer en {balde['clave']} y cayó en {desglosar(mov, BALDES)}"
            )


def test_com_transf_suma_las_TRES_grafias():
    """El mismo cobro llega de tres formas según el banco: como CONCEPTO
    abreviado, o escrito en la DESCRIPCIÓN de dos maneras distintas. Las tres
    tienen que sumar a la misma columna.

    Es el caso que obligó a mover el CAMPO adentro del matcher: un balde tiene
    que poder mirar `descripcion_ib` Y `descripcion_banco` a la vez."""
    assert desglosar(_m(concepto="COM.TRANSF"), BALDES) == "comtransf"
    assert desglosar(_m(descripcion="N/D - COMISIONES DATANET"), BALDES) == "comtransf"
    assert desglosar(_m(descripcion="N/D - COMISION ECHEQ CLEA"), BALDES) == "comtransf"


def test_com_transf_agarra_aunque_el_texto_siga():
    """La descripción viene truncada y con cola: `COMISION ECHEQ CLEA 4471`
    tiene que contar igual que `COMISION ECHEQ CLEA` pelado."""
    assert desglosar(_m(descripcion="N/D - COMISION ECHEQ CLEA 4471 XX"), BALDES) == "comtransf"


def test_el_IVA_de_una_comision_datanet_sigue_siendo_IVA():
    """⚠️ El que se rompe fácil. Un movimiento cuyo CONCEPTO es IVA y cuya
    DESCRIPCIÓN menciona la comisión es el IVA de esa comisión — no la comisión.
    Si cayera en COM.TRANSF, esa columna mostraría de más y IVA de menos, y el
    total seguiría dando bien: un error que no se ve."""
    mov = _m(concepto="IVA", descripcion="N/D - COMISIONES DATANET")
    assert desglosar(mov, BALDES) == "iva"


def test_las_claves_son_unicas():
    claves = [b["clave"] for b in BALDES]
    assert len(claves) == len(set(claves))
    assert RESTO not in claves


# --------------------------------------------------------------------------- #
# IGNORAR un movimiento — el equivalente al destildado por celda de Tesorería
# --------------------------------------------------------------------------- #
# El movimiento ignorado NO desaparece: sigue clasificado y sigue en la lista
# (tachado). Lo único que cambia es que **no suma**. Es lo que salva el número
# cuando el banco manda la misma comisión dos veces: borrar la fila haría que
# el detalle deje de coincidir con el extracto; ignorarla deja las dos a la
# vista y cuenta una.
def _gastos(monkeypatch, movs, overrides=None):
    from api.services import bancos as svc

    def _q(sql, params=None):
        t = " ".join(str(sql).split())
        if "gastos_reglas" in t:
            return [{"id": 1, "campo": "descripcion_ib", "operador": "igual",
                     "valor": "IVA", "activa": True}]
        if "gastos_overrides" in t:
            return [{"mov_hash": k, "es_gasto": v} for k, v in (overrides or {}).items()]
        return movs

    monkeypatch.setattr(svc, "_q", _q)
    return svc._gastos_bancarios(__import__("datetime").date(2026, 8, 14), semilla_catalogo())


def _crudo(h, ignorado=False, importe=100.0):
    return {"mov_hash": h, "cuenta_id": 1, "importe": importe, "tipo": "D",
            "codigo_operacion_ib": "1", "codigo_operacion_banco": "1",
            "descripcion_banco": "", "descripcion_ib": "IVA", "ignorado": ignorado}


def test_un_movimiento_ignorado_no_suma_al_gasto(monkeypatch):
    out = _gastos(monkeypatch, [_crudo("h1"), _crudo("h2", ignorado=True)])
    assert out[1]["total"] == 100.0, "el ignorado tiene que quedar afuera del total"
    assert out[1]["iva"] == 100.0, "y afuera de su balde del desglose"


def test_ignorar_gana_incluso_sobre_la_marca_manual(monkeypatch):
    """Marcar a mano «esto ES gasto» y después ignorarlo tiene que dar CERO.
    Son dos preguntas distintas: la marca dice QUÉ ES, ignorar dice SI CUENTA."""
    out = _gastos(monkeypatch, [_crudo("h1", ignorado=True)], overrides={"h1": True})
    assert out[1]["total"] == 0.0


def test_sin_ignorados_nada_cambia(monkeypatch):
    out = _gastos(monkeypatch, [_crudo("h1"), _crudo("h2")])
    assert out[1]["total"] == 200.0


# --------------------------------------------------------------------------- #
# ABM del DESGLOSE — el catálogo lo edita el equipo, así que se valida SERVER-SIDE
# --------------------------------------------------------------------------- #
# Lo que escribe un usuario termina decidiendo en qué columna cae plata. Validar
# en el formulario no alcanza: el endpoint es la única puerta que no se puede
# saltear.
def _svc_mock(monkeypatch, existe_balde=True):
    from api.services import bancos as svc

    monkeypatch.setattr(svc, "_q", lambda sql, params=None: (
        [{"clave": "x", "etiqueta": "X", "grupo": "otros", "orden": 10}]
        if "INSERT" in str(sql) or existe_balde else []))
    monkeypatch.setattr(svc, "_exec", lambda sql, params=None: 1)
    return svc


@pytest.mark.parametrize("etiqueta,esperada", [
    ("IMPUESTO A LOS SELLOS", "impuesto_a_los_sellos"),
    ("IMP.DB/CR P/DEB", "imp_db_cr_p_deb"),
    ("  Tasa   Liquidez  ", "tasa_liquidez"),
])
def test_la_clave_se_deriva_de_la_etiqueta(etiqueta, esperada):
    """Al usuario no se le pide un campo técnico que no significa nada para él:
    la clave —que es la que viaja en el JSON y la que referencian los matchers—
    sale del nombre que escribió."""
    from api.services.bancos import _slug
    assert _slug(etiqueta) == esperada


def test_no_se_puede_llamar_resto_a_un_balde(monkeypatch):
    """`resto` es lo que NO cae en ningún balde. Un balde con esa clave haría que
    la vista muestre dos cosas distintas con el mismo nombre."""
    svc = _svc_mock(monkeypatch)
    with pytest.raises(ValueError, match="reservado"):
        svc.guardar_balde("x@y", "resto", "otros", 10)


def test_un_grupo_inventado_se_rechaza(monkeypatch):
    svc = _svc_mock(monkeypatch)
    with pytest.raises(ValueError, match="Grupo inválido"):
        svc.guardar_balde("x@y", "Nueva", "columna_rara", 10)


def test_una_etiqueta_vacia_se_rechaza(monkeypatch):
    svc = _svc_mock(monkeypatch)
    with pytest.raises(ValueError, match="etiqueta"):
        svc.guardar_balde("x@y", "   ", "otros", 10)


def test_un_matcher_con_campo_que_no_existe_se_rechaza(monkeypatch):
    """Sin esto, el `campo` que escribió un usuario llegaría a un WHERE."""
    svc = _svc_mock(monkeypatch)
    with pytest.raises(ValueError, match="Campo inválido"):
        svc.agregar_matcher("x@y", "iva", "columna_inventada", "contiene", "IVA")


def test_un_matcher_vacio_se_rechaza(monkeypatch):
    """Un valor vacío matchearía con cualquier cosa — se comería todo el desglose."""
    svc = _svc_mock(monkeypatch)
    with pytest.raises(ValueError, match="vacío"):
        svc.agregar_matcher("x@y", "iva", "descripcion_ib", "igual", "  ")


def test_no_se_le_cuelga_una_grafia_a_un_balde_que_no_existe(monkeypatch):
    svc = _svc_mock(monkeypatch, existe_balde=False)
    with pytest.raises(ValueError, match="no existe"):
        svc.agregar_matcher("x@y", "fantasma", "descripcion_ib", "igual", "IVA")


def test_borrar_un_balde_no_cambia_ningun_total(monkeypatch):
    """Invariante del modelo: el desglose se DERIVA en la lectura, así que un
    balde menos solo manda sus movimientos a MOVIMIENTOS RESTANTES. El total de
    gastos no se toca."""
    from api.services import bancos as svc

    sin_sellos = [b for b in semilla_catalogo() if b["clave"] != "sellos"]
    mov = _m(descripcion="IMPUESTO A LOS SELLOS")
    assert svc.desglosar(mov, semilla_catalogo()) == "sellos"
    assert svc.desglosar(mov, sin_sellos) == RESTO


# --------------------------------------------------------------------------- #
# Validación de lo MANUAL — lo escribe una persona, se valida server-side
# --------------------------------------------------------------------------- #
def _svc_manual(monkeypatch, existe=True):
    from api.services import bancos as svc

    monkeypatch.setattr(svc, "_q", lambda sql, params=None: (
        [{"id": 1}] if existe else []))
    monkeypatch.setattr(svc, "_exec", lambda sql, params=None: 1)
    return svc


def test_un_movimiento_manual_sin_descripcion_se_rechaza(monkeypatch):
    """Dentro de un mes, un ajuste sin descripción es un número que nadie puede
    explicar — y estos ajustes mueven el saldo al cierre."""
    import datetime as _dt
    svc = _svc_manual(monkeypatch)
    with pytest.raises(ValueError, match="descripción"):
        svc.crear_movimiento_manual("x@y", 1, _dt.date(2026, 8, 14), "  ", 100, "C")


def test_un_movimiento_manual_en_cero_se_rechaza(monkeypatch):
    import datetime as _dt
    svc = _svc_manual(monkeypatch)
    with pytest.raises(ValueError, match="cero"):
        svc.crear_movimiento_manual("x@y", 1, _dt.date(2026, 8, 14), "ajuste", 0, "C")


def test_el_tipo_del_movimiento_manual_es_C_o_D(monkeypatch):
    """Sin esto entraría cualquier string y el signo del ajuste quedaría a
    criterio de un `else`."""
    import datetime as _dt
    svc = _svc_manual(monkeypatch)
    with pytest.raises(ValueError, match=r"C \(suma\) o D"):
        svc.crear_movimiento_manual("x@y", 1, _dt.date(2026, 8, 14), "ajuste", 10, "X")


def test_no_se_carga_un_movimiento_a_una_cuenta_que_no_existe(monkeypatch):
    import datetime as _dt
    svc = _svc_manual(monkeypatch, existe=False)
    with pytest.raises(ValueError, match="no existe"):
        svc.crear_movimiento_manual("x@y", 99, _dt.date(2026, 8, 14), "ajuste", 10, "C")


def test_una_cuenta_manual_necesita_banco_y_numero(monkeypatch):
    svc = _svc_manual(monkeypatch)
    with pytest.raises(ValueError, match="nombre del banco"):
        svc.crear_cuenta_manual("x@y", "  ", "123", "CC", "ARS")
    with pytest.raises(ValueError, match="número de cuenta"):
        svc.crear_cuenta_manual("x@y", "Banco X", "  ", "CC", "ARS")


def test_el_tipo_de_cuenta_se_valida(monkeypatch):
    svc = _svc_manual(monkeypatch)
    with pytest.raises(ValueError, match="Tipo inválido"):
        svc.crear_cuenta_manual("x@y", "Banco X", "123", "XX", "ARS")


# --------------------------------------------------------------------------- #
# El ORDEN resuelve los pisones — sin excepciones hardcodeadas
# --------------------------------------------------------------------------- #
# Caso real: un banco manda `IVA PERCEPCION RESOL GRAL` con el CONCEPTO en `IVA`.
# Como IVA se evalúa primero, se lo come — y la columna IVAPERCEP queda en cero
# mientras IVA muestra de más, con el TOTAL dando bien. El error invisible.
#
# La salida NO es una excepción en el código para ese texto: es subir IVAPERCEP
# arriba de IVA y darle un matcher por DESCRIPCIÓN. Estos tests congelan que eso
# funciona y —lo más importante— que no rompe el caso normal.
def _con_percep_arriba():
    """El mismo catálogo, con IVAPERCEP antes que IVA y su matcher de descripción."""
    baldes = semilla_catalogo()
    for b in baldes:
        if b["clave"] == "ivapercep":
            b["orden"] = 5
            b["matchers"].append({"id": 99, "campo": "descripcion_banco",
                                  "operador": "contiene",
                                  "valor": "IVA PERCEPCION RESOL"})
    return sorted(baldes, key=lambda b: b["orden"])


def test_el_orden_saca_a_IVAPERCEP_de_abajo_de_IVA():
    mov = _m(concepto="IVA", descripcion="IVA PERCEPCION RESOL GRAL 3337")
    assert desglosar(mov, semilla_catalogo()) == "iva", "así estaba: mal"
    assert desglosar(mov, _con_percep_arriba()) == "ivapercep"


def test_subir_IVAPERCEP_no_se_lleva_puesto_al_IVA_normal():
    """El que importa: el matcher nuevo mira la DESCRIPCIÓN, así que un IVA de
    verdad —concepto IVA, sin esa descripción— sigue cayendo en IVA aunque
    IVAPERCEP se evalúe primero. Si esto se rompiera, la solución del pisón
    habría creado un pisón nuevo."""
    assert desglosar(_m(concepto="IVA"), _con_percep_arriba()) == "iva"
    assert desglosar(_m(concepto="IVA", descripcion="N/D - COMISIONES DATANET"),
                     _con_percep_arriba()) == "iva"


def test_IVAPERCEP_por_concepto_sigue_andando():
    assert desglosar(_m(concepto="IVAPERCEP"), _con_percep_arriba()) == "ivapercep"


def test_reordenar_exige_la_lista_COMPLETA(monkeypatch):
    """Media lista dejaría columnas con el orden viejo y otras con el nuevo —
    empates silenciosos. Se manda todo o no se manda nada."""
    from api.services import bancos as svc

    monkeypatch.setattr(svc, "_q", lambda sql, params=None: [
        {"clave": "iva"}, {"clave": "ivapercep"}])
    monkeypatch.setattr(svc, "_exec", lambda sql, params=None: 1)
    with pytest.raises(ValueError, match="TODAS las columnas"):
        svc.reordenar_baldes("x@y", ["iva"])
    with pytest.raises(ValueError, match="TODAS las columnas"):
        svc.reordenar_baldes("x@y", ["iva", "iva"])


# --------------------------------------------------------------------------- #
# EL SIGNO DEL IMPORTE — la trampa que rompió el control de DIFERENCIAS
# --------------------------------------------------------------------------- #
# `bancos.movimientos.importe` viene YA FIRMADO de Interbanking: un débito llega
# NEGATIVO. El código asumía valor absoluto + `tipo`, así que le aplicaba el
# signo por SEGUNDA vez y la suma daba los VALORES ABSOLUTOS. Medido: una cuenta
# cuyos movimientos suman −500,53 devolvía 1.612.340.349,01.
#
# La regla que queda: `abs(importe)` con el signo de `tipo`. No es "sacar la
# negación de más" — es la única fórmula que da bien tanto si el banco firma el
# importe como si no, y eso importa porque son 9 bancos distintos.
def test_el_signo_sale_de_tipo_y_no_del_importe():
    from api.services.bancos import _firmado

    # El banco firma el importe (el caso que rompía).
    assert _firmado({"importe": -445_278_617.28, "tipo": "D"}) == -445_278_617.28
    # Y si mandara el mismo débito SIN firmar, tiene que dar lo mismo.
    assert _firmado({"importe": 445_278_617.28, "tipo": "D"}) == -445_278_617.28
    # Los créditos, en positivo en los dos casos.
    assert _firmado({"importe": 3_632_319.77, "tipo": "C"}) == 3_632_319.77
    assert _firmado({"importe": -3_632_319.77, "tipo": "C"}) == 3_632_319.77


def test_la_suma_del_dia_da_la_variacion_del_saldo():
    """El caso REAL que lo destapó, con los importes tal como los manda el banco:
    la suma tiene que dar −500,53 (la variación del saldo), no 1.612.340.349,01
    (la suma de los valores absolutos)."""
    from api.services.bancos import _firmado

    reales = [
        {"importe": 3_632_319.77, "tipo": "C"},
        {"importe": 360_891_307.00, "tipo": "C"},
        {"importe": 441_646_297.47, "tipo": "C"},
        {"importe": -86.73, "tipo": "D"},
        {"importe": -413.00, "tipo": "D"},
        {"importe": -360_891_307.76, "tipo": "D"},
        {"importe": -445_278_617.28, "tipo": "D"},
    ]
    assert round(sum(_firmado(m) for m in reales), 2) == -500.53


def test_un_gasto_suma_en_POSITIVO(monkeypatch):
    """La columna contesta «cuánto se llevó el banco», no «cuánto se movió el
    saldo»: un débito de 86,73 son 86,73 de gasto, no −86,73."""
    from api.services import bancos as svc

    def _q(sql, params=None):
        t = " ".join(str(sql).split())
        if "gastos_reglas" in t:
            return [{"id": 1, "campo": "descripcion_ib", "operador": "igual",
                     "valor": "IVA", "activa": True}]
        if "gastos_overrides" in t:
            return []
        return [{"mov_hash": "h1", "cuenta_id": 1, "importe": -86.73, "tipo": "D",
                 "codigo_operacion_ib": "1", "codigo_operacion_banco": "1",
                 "descripcion_banco": "", "descripcion_ib": "IVA", "ignorado": False}]

    monkeypatch.setattr(svc, "_q", _q)
    out = svc._gastos_bancarios(date(2026, 8, 18), semilla_catalogo())
    assert out[1]["total"] == 86.73
    assert out[1]["iva"] == 86.73


# --------------------------------------------------------------------------- #
# BORRAR una columna: solo si está VACÍA
# --------------------------------------------------------------------------- #
# Incidente 2026-08-19: alguien borró COM.TRANSF con sus tres textos de un clic y
# no se pudieron recuperar — la baja auditaba la clave y el nombre, y los
# matchers se iban por CASCADE sin quedar registrados. Dos errores en uno: un
# botón demasiado fácil de apretar y una auditoría que guardaba la mitad.
def test_no_se_borra_una_columna_CON_textos(monkeypatch):
    """El gesto destructivo tiene que ser deliberado: primero se sacan los textos
    de a uno —cada uno queda auditado— y recién ahí se puede borrar la columna."""
    from api.services import bancos as svc

    def _q(sql, params=None):
        t = " ".join(str(sql).split())
        if "gastos_balde_matchers" in t:
            return [{"id": 1, "campo": "descripcion_ib", "operador": "contiene",
                     "valor": "COM.TRANSF"}]
        return [{"clave": "comtransf", "etiqueta": "COM.TRANSF",
                 "grupo": "concepto", "orden": 40}]

    monkeypatch.setattr(svc, "_q", _q)
    monkeypatch.setattr(svc, "_exec", lambda sql, params=None: 1)
    with pytest.raises(ValueError, match="texto"):
        svc.borrar_balde("x@y", "comtransf")


def test_una_columna_VACIA_si_se_borra(monkeypatch):
    """El caso real de «me equivoqué al crearla»: sin textos no hay conocimiento
    que perder, y prohibirlo dejaría basura para siempre."""
    from api.services import bancos as svc

    borrados: list = []

    def _q(sql, params=None):
        if "gastos_balde_matchers" in " ".join(str(sql).split()):
            return []
        return [{"clave": "x", "etiqueta": "X", "grupo": "otros", "orden": 90}]

    monkeypatch.setattr(svc, "_q", _q)
    monkeypatch.setattr(svc, "_exec", lambda sql, params=None: borrados.append(sql) or 1)
    assert svc.borrar_balde("x@y", "x") is True
    assert any("DELETE" in str(x) for x in borrados)
