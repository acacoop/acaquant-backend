"""El TABLERO DE CONTROL del AV Agent: la parada y las fuentes.

Lo que se prueba acá no es el happy path (que la parada frene): es que **no se
pueda olvidar de aplicarla**. Una puerta de escritura nueva que no llame al
guardia no rompe ningún test obvio — simplemente escribe con el agente frenado,
en silencio, que es exactamente el modo de falla que la parada vino a eliminar.
"""
from __future__ import annotations

import inspect
import re

from api.services import av_agent_alta, av_agent_control

# ── LO IMPORTANTE: ninguna puerta se saltea el guardia ──────────────────────

def _escrituras() -> list[tuple[str, bool]]:
    """Toda función de `api/services/av_agent_*.py` que ESCRIBE, y si llama al
    guardia. Se DERIVA del código — una lista a mano se queda vieja la primera
    vez que alguien tiene apuro, que es exactamente cómo nació este agujero.

    Cuenta como escritura un `INSERT/UPDATE/DELETE` contra un schema de negocio
    o una llamada a una de las puertas conocidas de escritura de la app.
    """
    import ast
    import pathlib

    puertas = {"set_campos", "sembrar_ticker", "subscribe", "upsert_bono",
               "crear", "enviar", "enviar_muchos", "enviar_tabla"}
    esquemas = ("mercado.", "portafolio.", "clientes.", "operaciones.",
                "manager.", "valuaciones.")

    def _escribe(fn, src: str) -> bool:
        seg = ast.get_source_segment(src, fn) or ""
        up = seg.upper()
        for verbo in ("INSERT INTO ", "UPDATE ", "DELETE FROM "):
            i = 0
            while (i := up.find(verbo, i)) != -1:
                if seg[i + len(verbo):].lstrip().lower().startswith(esquemas):
                    return True
                i += 1
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                f = n.func
                nom = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if nom in puertas:
                    return True
        return False

    raiz = pathlib.Path(__file__).resolve().parents[2] / "api" / "services"
    out = []
    for arch in sorted(raiz.glob("av_agent*.py")):
        src = arch.read_text(encoding="utf-8")
        for nodo in ast.parse(src).body:
            if isinstance(nodo, ast.ClassDef):
                pares = [(f"{nodo.name}.{f.name}", f) for f in nodo.body
                         if isinstance(f, ast.FunctionDef)]
            elif isinstance(nodo, ast.FunctionDef):
                pares = [(nodo.name, nodo)]
            else:
                continue
            for nombre, fn in pares:
                if not _escribe(fn, src):
                    continue
                seg = ast.get_source_segment(src, fn) or ""
                out.append((f"{arch.stem}.{nombre}", "guardia(" in seg))
    return out


def test_toda_puerta_de_escritura_llama_al_guardia():
    """**Ninguna escritura del agente se saltea la parada** — en TODOS los
    módulos, no solo en el que la inventó.

    ⚠️⚠️ Este test escaneaba únicamente `av_agent_alta`, y por eso no vio que
    las 10 acciones de `av_agent_hacer` —las que están detrás del botón
    ARREGLAR de cada fila— **escribían con el agente frenado**. El docstring de
    arriba ya lo decía: *«una puerta nueva que no llame al guardia no rompe
    ningún test obvio»*. Era literal, y pasó.

    Lo que no llama al guardia tiene que estar DECLARADO en
    `av_agent_control.SIN_GUARDIA` con su motivo — mismo mecanismo que
    `av_agent_hacer.SIN_ACCION`: no se puede agregar una puerta sin decidir.
    """
    escrituras = _escrituras()
    assert len(escrituras) >= 15, (
        f"el escáner encontró solo {len(escrituras)} escrituras — algo se rompió "
        f"en la detección y este test estaría pasando sin mirar nada")
    sueltas = [n for n, tiene in escrituras
               if not tiene and n not in av_agent_control.SIN_GUARDIA]
    assert not sueltas, (
        f"estas puertas escriben sin consultar la PARADA: {sueltas}. Agregá "
        f"`if (frenado := av_agent_control.guardia('<accion>')): return frenado` "
        f"al principio, o declarala en `av_agent_control.SIN_GUARDIA` con el "
        f"motivo por el que la parada no la alcanza.")


def test_las_puertas_declaradas_SIN_GUARDIA_existen_de_verdad():
    """Una excepción a un nombre que ya no existe es una excepción que tapa a la
    próxima función que se llame igual."""
    reales = {n for n, _ in _escrituras()}
    fantasma = [n for n in av_agent_control.SIN_GUARDIA if n not in reales]
    assert not fantasma, (
        f"declaradas en SIN_GUARDIA pero no escriben (o no existen): {fantasma}")


def test_toda_excepcion_dice_POR_QUE():
    for nombre, motivo in av_agent_control.SIN_GUARDIA.items():
        assert len(motivo.strip()) > 20, (
            f"«{nombre}» está exenta sin un motivo que se pueda discutir")


def test_las_diez_acciones_pasan_por_UN_solo_camino():
    """`av_agent_hacer.aplicar()` es el choke point de las 10 acciones — también
    de `uno(aplicar_ya=True)`. Si alguien abre un segundo camino, la parada se
    vuelve a agujerear por ahí."""
    from api.services import av_agent_hacer
    src_ap = inspect.getsource(av_agent_hacer.aplicar)
    assert "guardia(" in src_ap
    # y el guardia va ANTES de leer/escribir nada
    assert src_ap.index("guardia(") < src_ap.index("_filas(")
    assert "aplicar([" in inspect.getsource(av_agent_hacer.uno), (
        "`uno()` dejó de aplicar por `aplicar()`: hay un segundo camino a la "
        "escritura y la parada no lo cubre")


def test_el_guardia_se_llama_antes_de_simular():
    """El orden importa: chequear la parada DESPUÉS de simular gastaría créditos
    de 1816 para después rechazar la escritura."""
    for n in ("aplicar", "aplicar_flujos", "aplicar_arreglo"):
        src = inspect.getsource(getattr(av_agent_alta, n))
        i_guardia = src.index("guardia(")
        m = re.search(r"\bsim = simular", src)
        assert m and i_guardia < m.start(), (
            f"{n}: la parada se chequea después de simular — se gastan créditos "
            f"de 1816 para nada")


# ── El guardia, en sí ───────────────────────────────────────────────────────

def test_sin_parada_deja_pasar(monkeypatch):
    monkeypatch.setattr(av_agent_control, "_leer_parada",
                        lambda: {"parada": False, "motivo": "", "por": "", "leido": True})
    assert av_agent_control.guardia("alta_bono") is None


def test_con_parada_devuelve_el_rechazo_con_forma_de_respuesta(monkeypatch):
    """El rechazo viaja con la MISMA forma que un `aplicar` fallido — así el
    modal lo muestra sin una rama nueva."""
    monkeypatch.setattr(av_agent_control, "_leer_parada",
                        lambda: {"parada": True, "motivo": "estoy revisando la escala",
                                 "por": "nico@aca", "leido": True})
    r = av_agent_control.guardia("arreglar_bono")
    assert r is not None
    assert r["ok"] is False and r["parada"] is True
    # El motivo y el autor van EN el mensaje: el que se lo encuentra frenado
    # tiene que poder decidir si lo reanuda sin ir a preguntar.
    assert "nico@aca" in r["error"] and "estoy revisando la escala" in r["error"]


def test_frenar_exige_motivo():
    r = av_agent_control.set_parada(activa=True, motivo="   ", por="x@y")
    assert r["ok"] is False and "motivo" in r["error"]


# ── La degradación, que es una decisión y no un descuido ────────────────────

def test_sin_haber_leido_nunca_se_permite_escribir(monkeypatch):
    """Proceso recién arrancado o schema sin aplicar → se deja pasar. La parada
    no es un control de seguridad (eso lo dan `require_admin` y el humano que
    aprueba), y fallar cerrado rompería el agente ante un problema de la MISMA
    base donde escribe."""
    monkeypatch.setattr(av_agent_control, "_cache", None)
    monkeypatch.setattr(av_agent_control, "get_pool",
                        lambda: (_ for _ in ()).throw(RuntimeError("sin base")))
    assert av_agent_control.guardia("alta_bono") is None


def test_una_parada_activa_sobrevive_a_un_blip_de_la_base(monkeypatch):
    """Lo contrario del anterior, y es el caso que importa: si ya se sabía que
    estaba frenado, un error de lectura NO lo levanta."""
    import time
    monkeypatch.setattr(av_agent_control, "_cache",
                        (time.time() - 999, {"parada": True, "motivo": "m",
                                             "por": "p", "leido": True}))
    monkeypatch.setattr(av_agent_control, "get_pool",
                        lambda: (_ for _ in ()).throw(RuntimeError("blip")))
    assert av_agent_control.guardia("alta_bono") is not None


# ── Las fuentes ─────────────────────────────────────────────────────────────

def test_las_fuentes_usan_el_vocabulario_del_preflight():
    """Mismo vocabulario de estados que la cadena, para que el modal las pinte
    con los colores que ya tiene y nadie aprenda una segunda convención."""
    src = inspect.getsource(av_agent_control)
    for estado in ('"ok"', '"revisar"', '"bloquea"'):
        assert estado in src


def test_ninguna_fuente_pega_a_la_red():
    """Un tablero que gasta un crédito cada vez que se mira consume justo el
    recurso que vino a cuidar."""
    src = inspect.getsource(av_agent_control)
    for prohibido in ("mercado_1816.censar", "mercado_1816.balance",
                      "mercado_1816.cashflow", "mercado_1816.indicadores"):
        assert prohibido not in src, f"{prohibido} pega a 1816 y cuesta créditos"
