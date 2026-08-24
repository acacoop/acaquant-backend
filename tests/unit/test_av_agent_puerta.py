"""LA PUERTA ÚNICA — nada escribe lo que el agente encontró, salvo un lugar.

El user (2026-08-24): *«la arquitectura del agente está muy mal, hay bugs por
todos lados, procesos independientes, no hay una lógica, cada día es peor»*.

Y el número le daba la razón: **seis puertas escribiendo estado**, cada una con
su criterio. Lo caro no eran los bugs que ya habían salido sino los que
faltaban — **nada obligaba a una funcionalidad nueva a usar el pipeline que ya
existía**, así que cada feature inauguraba su séptima puerta.

Cerrar bugs de a uno no arregla eso. Lo arregla que **no haya dónde
equivocarse**, y eso es exactamente lo que estos tests hacen cumplir. Son la
Fase 1 del plan (`AV_AGENT.md` M.8) convertida en algo que bloquea el merge.
"""
from __future__ import annotations

import inspect
import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parents[2]
PUERTA = "api/services/av_agent_registro.py"


def _fuentes(*dirs: str):
    for d in dirs:
        for f in sorted((RAIZ / d).rglob("*.py")):
            yield f.relative_to(RAIZ).as_posix(), f.read_text(encoding="utf-8")


def _codigo(txt: str) -> str:
    """Sin las líneas de comentario: los comentarios que EXPLICAN por qué algo
    ya no se hace nombran justamente lo prohibido, y ese texto hay que
    conservarlo."""
    return "\n".join(l for l in txt.splitlines() if not l.lstrip().startswith("#"))


# ── UNA sola puerta escribe la FOTO ─────────────────────────────────────────

def test_NADIE_escribe_la_foto_fuera_de_la_puerta():
    """`agente.av_agent_hallazgos` se escribe en UN archivo. Si mañana aparece
    un `INSERT` en otro lado, ese camino no va a escribir el objeto, ni la
    clave, ni respetar la guarda de `evaluados` — que es exactamente cómo se
    llegó a tener trece familias de hallazgos sin memoria."""
    culpables = []
    for arch, txt in _fuentes("api", "jobs", "core"):
        if arch == PUERTA:
            continue
        c = _codigo(txt)
        for verbo in ("INSERT INTO agente.av_agent_hallazgos",
                      "DELETE FROM agente.av_agent_hallazgos",
                      "UPDATE agente.av_agent_hallazgos"):
            if verbo in c:
                culpables.append(f"{arch}: {verbo}")
    assert not culpables, (
        f"escriben la foto fuera de la puerta: {culpables}. Usá "
        f"`av_agent_registro.guardar(alcance, hallazgos, evaluados=...)`.")


def test_la_puerta_escribe_LAS_DOS_cosas():
    """La foto y el objeto salen de la MISMA llamada. Eran dos escrituras del
    mismo hecho hechas por separado, y por eso podían quedar desincronizadas —
    AHORA y ENCONTRÓ contando distinto del mismo problema."""
    from api.services import av_agent_registro as reg
    src = inspect.getsource(reg.guardar)
    assert "av_agent_items.sincronizar(" in src
    assert "_reemplazar(" in src and "_corrida(" in src


def test_el_espejo_va_SIEMPRE_aunque_no_haya_hallazgos():
    """El día que una corrida no encuentra nada es el día en que hay MÁS para
    cerrar. `persistir()` cortaba con `return 0` antes de llegar al espejo."""
    from api.services import av_agent_registro as reg
    src = inspect.getsource(reg.guardar)
    # entre el early-return por alcance vacío y el sincronizar no puede haber
    # ningún otro `return` condicionado por la cantidad de hallazgos
    cuerpo = src[src.index("hallazgos = list("):src.index("sincronizar(")]
    assert "if not hallazgos" not in cuerpo


def test_el_MODO_se_deriva_y_no_es_un_parametro():
    """Corrida vs reemplazo sale de `alcance in ALCANCES_VIVOS`, que ya era la
    regla. Un parámetro sería una séptima forma de equivocarse."""
    from api.services import av_agent_registro as reg
    assert "ALCANCES_VIVOS" in inspect.getsource(reg.guardar)
    assert "modo" not in inspect.signature(reg.guardar).parameters


def test_un_alcance_vacio_se_RECHAZA():
    """Sin alcance no se sabe qué se está reemplazando ni de qué corrida es —
    y un reemplazo con el alcance equivocado borra la corrida de otro."""
    from api.services import av_agent_registro as reg
    assert reg.guardar("", [{"tipo": "x"}])["ok"] is False


# ── UNA sola identidad ──────────────────────────────────────────────────────

def test_la_CLAVE_no_se_arma_a_mano_en_ningun_lado():
    """Tres implementaciones de la identidad costaron: el mismo bono como dos
    objetos, la memoria inalcanzable en trece familias, y un voto que solo podía
    decir «aguantó». La arma `clave_de_problema` y nadie más."""
    patron = re.compile(r"""["']\|["']\s*\+|\|\{|\}\|""")
    culpables = []
    for arch, txt in _fuentes("api/services", "jobs"):
        if "av_agent" not in arch or arch.endswith("ciclo.py"):
            continue
        for n, linea in enumerate(_codigo(txt).splitlines(), 1):
            if patron.search(linea) and "clave" in linea.lower():
                culpables.append(f"{arch}:{n}")
    assert not culpables, f"arman la identidad a mano: {culpables}"


def test_NADIE_recalcula_la_identidad_en_SQL():
    """El JOIN del centinela hacía `lower(sujeto) || '|' || lower(regla)`, sin
    `causa_canonica` y sin el caso del sujeto vacío. Fallaba en silencio."""
    for arch, txt in _fuentes("api", "jobs"):
        c = _codigo(txt)
        assert "|| '|' ||" not in c, f"{arch} recalcula la clave en SQL"


# ── LA GUARDA que evita la mentira optimista ────────────────────────────────

def test_TODO_el_que_registra_declara_QUE_MIRO():
    """Sin `evaluados` no se cierra nada (es la degradación correcta), pero un
    productor que no lo pasa **nunca cierra** y su lista crece para siempre."""
    llamadas = []
    for arch, txt in _fuentes("api", "jobs"):
        if arch == PUERTA:
            continue
        for m in re.finditer(r"registro\.guardar\((.{0,300})", _codigo(txt), re.S):
            llamadas.append((arch, m.group(1)))
    assert llamadas, "nadie llama a la puerta — ¿se renombró?"
    sin_guarda = [a for a, args in llamadas if "evaluados" not in args]
    assert not sin_guarda, (
        f"registran sin declarar qué miraron: {sin_guarda}. Sin `evaluados` "
        f"nunca van a cerrar un hallazgo resuelto.")


def test_los_CUATRO_productores_pasan_por_la_puerta():
    """La relevada, el monitor de rueda (daemon y a mano) y el del sistema."""
    esperados = {
        "jobs/av_agent.py",                        # la relevada nocturna
        "jobs/av_agent_live.py",                   # el monitor, a mano
        "jobs/db_tamano.py",                       # la superficie HTTP (diaria)
        "jobs/av_agent_sistema.py",                # el monitor del sistema (10 min)
        "api/services/av_agent_centinela.py",      # el daemon
    }
    usan = {a for a, txt in _fuentes("api", "jobs")
            if "registro.guardar(" in _codigo(txt)}
    assert esperados <= usan, f"no usan la puerta: {esperados - usan}"


# ── Y LA DEUDA QUE ESTO CIERRA ──────────────────────────────────────────────

def test_el_monitor_del_SISTEMA_ahora_crea_objetos():
    """⚠️ Deuda #1. Sus cinco tipos (`db_cambio`, `tabla_quieta`,
    `permiso_flojo`, `dato_partido`, `cron_desalineado`) escribían la foto y
    **nunca** `sincronizar`: sin DNI, sin antigüedad, sin «volvió», y IGNORAR no
    los escondía. Violaban REGLA #10.1 en silencio.

    No se arregló acordándose: se arregló porque ahora hay una sola puerta."""
    for f in ("jobs/av_agent_sistema.py", "jobs/db_tamano.py"):
        src = (RAIZ / f).read_text(encoding="utf-8")
        assert "registro.guardar(" in src, f
        assert "evaluados=" in src, f


def test_un_detector_CAIDO_del_sistema_no_declara_lo_suyo():
    """Cada uno corre en su propio `try`: declarar la lista completa haría que
    un detector caído cerrara todo lo suyo por ausencia (§0.be)."""
    import jobs.av_agent_sistema as j
    assert j._seguro(lambda: (_ for _ in ()).throw(RuntimeError("boom")), "x") is None
    assert j._seguro(lambda: [{"a": 1}], "x") == [{"a": 1}]
    src = inspect.getsource(j.detectores)
    assert "if piezas is None" in src and "continue" in src


def test_los_DOS_alcances_del_sistema_NO_se_pisan():
    """⚠️ El reemplazo pisa TODO lo de su alcance, así que dos jobs con ritmos
    distintos no pueden compartirlo: el de 10 minutos borraría los hallazgos
    del nocturno apenas corriera — sin error y sin log, con la pantalla
    mostrando menos de lo que hay."""
    from api.services import av_agent

    rapido = (RAIZ / "jobs/av_agent_sistema.py").read_text(encoding="utf-8")
    lento = (RAIZ / "jobs/db_tamano.py").read_text(encoding="utf-8")
    assert 'guardar("sistema"' in rapido and 'guardar("superficie"' not in rapido
    assert 'guardar("superficie"' in lento and 'guardar("sistema"' not in lento
    # Y los dos tienen que estar declarados como de REEMPLAZO: si `superficie`
    # no estuviera, entraría como CORRIDA y se llevaría puesto el `max(
    # corrida_at)` de la relevada nocturna — el bug que la constante previene.
    for a in ("sistema", "superficie"):
        assert a in av_agent.ALCANCES_VIVOS, a
