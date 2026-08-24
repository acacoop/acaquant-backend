"""La ley de la CONFIRMACIÓN — «no miré» ≠ «sigue pasando» (2026-08-24).

El user, con cuatro motores en ROTO AHORA fechados tres días antes: *«es
inaceptable que AHORA muestre cosas que no sean del día actual»*.

No había un bug en la cadena, y por eso nadie lo veía. El guard de `evaluados`
impide cerrar un hallazgo cuyo detector no corrió — correcto, porque cerrar sin
mirar deja el tablero en verde el día que está más ciego. Pero la fila quedaba
abierta con su motivo congelado y la pantalla la publicaba afirmando «está roto
AHORA», con el latido en verde al lado porque el daemon sí estaba vivo.

Estos tests congelan las DOS mitades de la misma ley:

    al ESCRIBIR   «no miré» ≠ «no hay nada»     → no cerrar   (ya estaba)
    al LEER       «no miré» ≠ «sigue pasando»   → no afirmar  (esto)
"""
from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

from api.services import av_agent_centinela as cen
from api.services import av_agent_registro as registro

RAIZ = Path(__file__).resolve().parents[2]


def _hace(segundos: float) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=segundos)


# ── El cálculo ──────────────────────────────────────────────────────────────

def test_confirmado_recien_se_puede_afirmar():
    conf = {"motor_ruidoso": _hace(60)}
    assert registro.sin_confirmar("motor_ruidoso", conf) is None


def test_confirmado_hace_mucho_NO_se_puede_afirmar():
    edad = registro.sin_confirmar(
        "motor_ruidoso", {"motor_ruidoso": _hace(registro.CONFIRMACION_S + 60)})
    assert edad is not None and edad > registro.CONFIRMACION_S


def test_NUNCA_confirmado_no_es_lo_mismo_que_confirmado_hace_mucho():
    """Un tipo que nunca se pudo mirar y uno que se dejó de mirar son estados
    distintos, y la pantalla los tiene que poder decir distinto."""
    assert registro.sin_confirmar("jamas_visto", {}) == float("inf")


def test_si_el_registro_no_se_puede_leer_NADA_se_afirma():
    """Degradación elegida: sin el registro, ningún tipo está confirmado. La
    pantalla va a decir «no lo pude verificar», que es exactamente la verdad —
    lo contrario (afirmar por default) es el falso rojo que esto viene a matar."""
    src = inspect.getsource(registro.confirmados)
    assert "return {}" in src, "el fallback tiene que ser vacío, no None"


def test_la_ventana_se_deriva_de_la_cadencia_de_la_foto():
    """Tres pasadas perdidas, igual que `CICLOS_PERDIDOS` para el latido. Si
    alguien cambia el ritmo del daemon, este número tiene que seguirlo — un
    umbral clavado es cómo el semáforo y el reloj se desincronizan."""
    assert registro.CONFIRMACION_S == 3 * cen.SEGUNDOS_ENTRE_FOTOS


# ── La aplicación en AHORA ──────────────────────────────────────────────────

_FILA = {"clave": "motor_rofex|sin_datos", "tipo": "motor_caido",
         "sujeto": "motor_rofex", "regla": "sin_datos", "severidad": "alta",
         "motivo": "no corre", "abierto_at": "2026-08-21T12:00:00+00:00",
         "ultimo_at": "2026-08-21T12:00:00+00:00"}


def _lo_de_hoy(conf: dict, monkeypatch) -> dict:
    monkeypatch.setattr(registro, "confirmados", lambda: conf)
    return cen._lo_de_hoy([dict(_FILA)], [], habil=True)


def test_lo_confirmado_va_a_ROTO_AHORA(monkeypatch):
    r = _lo_de_hoy({"motor_caido": _hace(30)}, monkeypatch)
    assert [f["clave"] for f in r["roto"]] == ["motor_rofex|sin_datos"]
    assert r["sin_confirmar"] == []


def test_lo_NO_confirmado_sale_de_ROTO_AHORA(monkeypatch):
    """El caso exacto del user: la fila del 21/08 deja de afirmar «roto ahora»."""
    r = _lo_de_hoy({"motor_caido": _hace(3 * 86400)}, monkeypatch)
    assert r["roto"] == []
    assert [f["clave"] for f in r["sin_confirmar"]] == ["motor_rofex|sin_datos"]


def test_lo_NO_confirmado_NO_SE_ESCONDE(monkeypatch):
    """⚠️ La salida no es borrar la fila: el silencio se lee igual que un verde
    (§0.s). Sale a su propio bloque, diciendo hace cuánto que nadie la mira."""
    r = _lo_de_hoy({"motor_caido": _hace(3 * 86400)}, monkeypatch)
    assert r["sin_confirmar"][0]["sin_confirmar_s"] > registro.CONFIRMACION_S


def test_lo_NO_confirmado_NO_cuenta_como_novedad(monkeypatch):
    """Es una advertencia sobre el AGENTE («esto no lo estoy mirando»), no sobre
    el sistema. Sumarla al contador diría que pasó algo hoy, y no pasó nada:
    justamente no se sabe."""
    r = _lo_de_hoy({"motor_caido": _hace(3 * 86400)}, monkeypatch)
    assert r["novedades"] == 0


def test_lo_NO_confirmado_TAMPOCO_reaparece_como_aparecio(monkeypatch):
    """La misma fila en dos bloques de la misma pantalla se lee como dos
    problemas — el bug que `claves_roto` ya prevenía para `roto`."""
    hoy = dict(_FILA, abierto_at=datetime.now(UTC).isoformat())
    monkeypatch.setattr(registro, "confirmados", lambda: {})
    r = cen._lo_de_hoy([hoy], [], habil=True)
    assert r["aparecio"] == [] and len(r["sin_confirmar"]) == 1


# ── Que no haya una segunda forma de contestar la misma pregunta ────────────

def test_SOLO_la_puerta_escribe_quien_confirmo():
    """`evaluados` ya es un parámetro obligatorio de `guardar`: la única puerta
    que escribe hallazgos es la única que sabe qué se alcanzó a mirar. Un
    registro aparte sería la séptima puerta (REGLA #10.5)."""
    malos = []
    for f in list((RAIZ / "api").rglob("*.py")) + list((RAIZ / "jobs").rglob("*.py")):
        if f.name == "av_agent_registro.py":
            continue
        txt = "\n".join(x for x in f.read_text(encoding="utf-8").splitlines()
                        if not x.lstrip().startswith("#"))
        if "av_agent_evaluado" in txt:
            malos.append(str(f.relative_to(RAIZ)))
    assert not malos, f"escriben el registro de confirmación por afuera: {malos}"


def test_la_puerta_anota_lo_evaluado_en_TODA_llamada():
    """Si `_anotar_evaluados` viviera adentro de una rama, un modo de escritura
    quedaría sin sellar y sus tipos parecerían no confirmados para siempre."""
    arbol = ast.parse(inspect.getsource(registro.guardar))
    llamadas = [n for n in ast.walk(arbol)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "_anotar_evaluados"]
    assert len(llamadas) == 1
    # …y en el cuerpo de la función, no adentro de un `if`/`try`.
    cuerpo = arbol.body[0].body
    assert any(isinstance(s, ast.Assign)
               and isinstance(s.value, ast.Call)
               and getattr(s.value.func, "id", "") == "_anotar_evaluados"
               for s in cuerpo), "tiene que correr en TODA llamada"
