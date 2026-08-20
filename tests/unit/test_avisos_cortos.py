"""EL AVISO VA CORTO — la regla, congelada.

Pedido del user, dicho DOS veces (2026-08-19 y 2026-08-20):

    *«No quiero palabras fantasiosas, usá palabras normales que use una persona
    normal.»*
    *«Si está caído Aunesa decí AUNESA CAÍDO + motivo simple y listo. Nada de
    palabras raras ni tanto texto, con la hora de actualización. Lo mismo para
    todo.»*

**Se convierte en test porque ya se pidió dos veces.** Una preferencia que se
repite dejó de ser una preferencia: es un requisito, y los requisitos que solo
viven en un chat se pierden en el commit siguiente.

Lo que se mide no es el estilo —eso no se puede testear— sino las dos cosas que
lo arruinan y sí se pueden contar: **el largo** y **las palabras raras**.
"""
from __future__ import annotations

import inspect
import re

import pytest

# El título de un aviso se lee de un vistazo o no se lee. 90 caracteres es un
# renglón de la pantalla del agente.
TOPE_MOTIVO = 90
# Un renglón de cuerpo. Más que esto ya es un párrafo, y un párrafo no se lee
# cuando hay que decidir algo.
TOPE_RENGLON = 120

# Palabras que el user marcó, o de la misma familia: las que suenan a informe.
# No es una lista de estilo — es la lista de lo que hace que un aviso no se lea.
RARAS = ("custodio", "asimismo", "no obstante", "cabe destacar", "en virtud",
         "dicho sea", "por consiguiente", "resulta menester", "a los efectos")


def _textos_de(fn) -> list[str]:
    """Los strings que esa función arma para que los lea una persona."""
    src = inspect.getsource(fn)
    return re.findall(r'"([^"\\]{12,})"', src) + re.findall(r"'([^'\\]{12,})'", src)


# ── los catálogos que se escriben a mano ─────────────────────────────────────

def test_los_proveedores_se_describen_en_UN_renglon():
    from core.proveedores import PROVEEDORES
    for clave, p in PROVEEDORES.items():
        assert len(p.rompe) <= TOPE_RENGLON, (
            f"«{clave}» se explica en {len(p.rompe)} caracteres: eso es un "
            f"párrafo, y el que lo lee está por decidir algo")
        assert len(p.nombre) <= 20, f"«{clave}»: el nombre es un rótulo, no una frase"


def test_ninguna_PALABRA_RARA_en_lo_que_ve_una_persona():
    """«Custodio» lo marcó el user por nombre. El resto son de la misma familia:
    palabras que existen pero que nadie usa hablando."""
    import ast
    import pathlib
    archivos = ["core/proveedores.py", "api/services/av_agent_proveedores.py",
                "api/services/av_agent_causas.py",
                "api/services/av_agent_motores.py"]
    for ruta in archivos:
        arbol = ast.parse(pathlib.Path(ruta).read_text(encoding="utf-8"))
        # ⚠️ Se miran los strings del CÓDIGO, no los docstrings ni los
        # comentarios: ahí la palabra puede hacer falta justamente para explicar
        # por qué no se usa. Un regex sobre el archivo entero se comía los
        # docstrings y hacía fallar el test por su propia documentación — la
        # misma trampa que ya pagaron otros dos tests de este repo.
        # Por IDENTIDAD del nodo, no por su texto: `ast.get_docstring` devuelve
        # el docstring YA limpiado (sin la indentación), así que comparar por
        # valor no encuentra el original y el docstring del módulo se colaba.
        docs = set()
        for n in ast.walk(arbol):
            if not isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                  ast.AsyncFunctionDef)):
                continue
            cuerpo = getattr(n, "body", None) or []
            if (cuerpo and isinstance(cuerpo[0], ast.Expr)
                    and isinstance(cuerpo[0].value, ast.Constant)):
                docs.add(id(cuerpo[0].value))
        for n in ast.walk(arbol):
            if not (isinstance(n, ast.Constant) and isinstance(n.value, str)):
                continue
            if id(n) in docs or len(n.value) < 8:
                continue
            for rara in RARAS:
                assert rara not in n.value.lower(), (
                    f"«{rara}» en {ruta}: {n.value[:60]}")


# ── los avisos que se arman en vivo ──────────────────────────────────────────

def _hallazgos_de_ejemplo():
    """Un hallazgo de cada detector nuevo, con datos reales de producción."""
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    from api.services import av_agent_motores as mot
    from api.services import av_agent_proveedores as det
    from core import proveedores as pr

    fila = {"proveedor": "aunesa", "ok": False,
            "ultimo_error": "HTTPError: 500 Server Error for url: "
                            "https://aca.aunesa.com/Irmo/api/login",
            "ultimo_error_at": datetime.now(UTC) - timedelta(minutes=3),
            "ultimo_ok_at": datetime.now(UTC) - timedelta(hours=2),
            "fallos_seguidos": 7, "donde": "login",
            "actualizado_at": datetime.now(UTC)}
    with patch.object(pr, "estado", lambda: [fila]), \
         patch.object(det, "_probar", lambda p: None), \
         patch.object(det, "_barrer_si_toca", lambda p: None), \
         patch.object(det, "avisar_caida", lambda h, **k: {"enviados": 0}):
        out = list(det.detectar_proveedores())

    # Los tres patrones reales de la corrida del 2026-08-20.
    for u, pat, v, d, prio in (
            ("motor_cedears", "REST exception JSONDecodeError", 76, 180, 4),
            ("motor_options", "Expiries configuradas ya vencidas", 91, 24120, 4),
            ("motor_portfolio_snapshot", "símbolo inexistente", 1, 0, 3)):
        out.append(mot._hallazgo_log(
            {"unidad": u, "patron": pat, "veces": v, "primera": 0.0,
             "ultima": float(d), "peor": prio,
             "nivel": {2: "crit", 3: "error", 4: "warn"}[prio], "muestra": pat}))
    return out


@pytest.mark.parametrize("h", _hallazgos_de_ejemplo())
def test_el_MOTIVO_entra_en_un_renglon(h):
    """Es el título del aviso. Si no entra de un vistazo, no cumple su función."""
    assert len(h["motivo"]) <= TOPE_MOTIVO, (
        f"{len(h['motivo'])} caracteres: «{h['motivo']}»")


@pytest.mark.parametrize("h", _hallazgos_de_ejemplo())
def test_el_CUERPO_son_renglones_sueltos_y_no_parrafos(h):
    texto = (h.get("evidencia") or {}).get("texto") or ""
    assert "\n\n" not in texto, "hay párrafos separados por línea en blanco"
    for renglon in texto.splitlines():
        assert len(renglon) <= TOPE_RENGLON * 2, (
            f"renglón de {len(renglon)}: «{renglon[:70]}…»")


def test_el_aviso_de_un_PROVEEDOR_dice_QUIEN_CAIDO_y_el_MOTIVO():
    """El formato exacto que pidió el user: `AUNESA CAÍDO · 500 Server Error`.
    Nada de «Aunesa (el custodio) no responde (7 intentos)»."""
    h = _hallazgos_de_ejemplo()[0]
    assert h["motivo"].startswith("AUNESA CAÍDO · ")
    assert "500 Server Error" in h["motivo"]
    assert "http" not in h["motivo"].lower(), "la URL no va en el título"


def test_y_la_HORA_esta():
    """*«Con la hora de actualización y listo»*. Sin cuándo, un aviso no se puede
    ni creer ni descartar."""
    h = _hallazgos_de_ejemplo()[0]
    texto = h["evidencia"]["texto"]
    assert "hace" in texto and re.search(r"\d{1,2}:\d{2}", texto), texto


# ── EL MOTIVO TIENE QUE SER VOTABLE (2026-08-20) ─────────────────────────────
#
# *«No le pone hora ni nada… si vas a decir eso, para acertar me tenés que
# mostrar que falló en horarios donde debería funcionar; si no, no tiene
# validez»* (user).
#
# Y el detalle de pantalla que lo explica: **el botón ¿ACERTÓ? SÍ/NO está en la
# FILA**, y la fila muestra solo el motivo. Toda la evidencia vivía una pantalla
# más abajo, así que se pedía un voto sobre una frase sin datos — «hace rato que
# no produce» no se puede votar. Un eval set alimentado así mide la paciencia
# del que vota, no la puntería del agente.

# Los tipos donde el voto decide si el agente acertó. Un hallazgo de BONO trae su
# ticker y sus números por otro lado; estos son los del sistema, que sin la
# medición al lado son una opinión.
CON_EVIDENCIA = ("motor_caido", "tabla_quieta", "motor_ruidoso",
                 "proveedor_caido", "latencia")


def _motivos_del_sistema():
    from datetime import UTC, datetime
    from unittest.mock import patch

    from api.services import av_agent_contexto as ctx
    from api.services import av_agent_motores as mot

    arbol = {"en_rueda": True, "ahora_ar": "2026-08-20 14:22:00", "vistas": [
        {"vista": "MERCADOS", "grupos": [{"grupo": None, "piezas": [
            {"label": "motor_rofex (trades)", "tipo": "motor", "estado": "critico",
             "cadencia": "live", "hace": "hace 40 min", "umbral_s": 120,
             "ultima": None, "ventana": "rueda"},
            {"label": "motor_cedears", "tipo": "motor", "estado": "sin_datos",
             "cadencia": "cada 1m · 13-21 UTC L-V", "hace": "—", "umbral_s": 600,
             "ultima": None, "ventana": "rueda"},
            {"label": "tenencia (snapshot SQL)", "tipo": "job", "estado": "error",
             "cadencia": "diario 11:00 UTC", "hace": "hace 3 h",
             "umbral_s": 129600, "ultima": None, "ventana": "diario",
             "run_status": "error"}]}]}]}
    with patch("api.services.diagnostico.arbol", lambda: arbol), \
         patch.object(mot, "_prueba_del_log", lambda t, l: ""):
        out = [(h["tipo"], h["motivo"]) for h in mot.detectar_motores()]

    # Y una tabla quieta, que arma su motivo por otro camino.
    ahora = datetime(2026, 8, 20, 19, 22, tzinfo=UTC)
    f = ctx.frescura({"cadencia": "tiempo_real", "intervalo_p50_s": 30,
                      "ultimo_dato": datetime(2026, 8, 20, 14, 0, tzinfo=UTC)},
                     ahora=ahora)
    out.append(("tabla_quieta", f["motivo"]))
    return out


def test_ningun_motivo_del_SISTEMA_se_vota_sin_evidencia():
    """La regla, en una línea: **si se pide un voto, en la misma línea tiene que
    estar el número.** Cuánto hace, qué se esperaba, y la hora."""
    import re
    for tipo, motivo in _motivos_del_sistema():
        assert tipo in CON_EVIDENCIA, tipo
        assert any(c.isdigit() for c in motivo), (
            f"«{motivo}» no trae un solo número: no se puede votar")
        assert re.search(r"\d{1,2}:\d{2}", motivo), (
            f"«{motivo}» no dice la HORA — sin eso no se sabe si el problema "
            "es real ahora o si la pieza ni debería estar corriendo")


def test_y_dice_QUE_SE_ESPERABA():
    """Sin el «debía», el que vota tiene que saberse de memoria la cadencia de
    cada pieza — y entonces el voto lo emite quien ya conoce el sistema, que es
    justo al revés de para qué existe el aviso."""
    for _tipo, motivo in _motivos_del_sistema():
        assert ("esperado" in motivo or "es " in motivo), motivo


def test_los_motivos_del_sistema_ENTRAN_en_un_renglon():
    for _tipo, motivo in _motivos_del_sistema():
        assert len(motivo) <= TOPE_MOTIVO, f"{len(motivo)}: «{motivo}»"
