"""El eval set: voto HUMANO vs voto DERIVADO, y por qué no se mezclan.

`MIN_VOTOS` son 10 por causa. Llenarlo a mano son >100 clicks, y hasta que estén
la capa 1 del roadmap no habilita la 3 — o sea que la medición tarda semanas en
servir por una razón administrativa.

Pero el juicio ya está guardado: cada acción que un humano APROBÓ y salió bien es
alguien diciendo «la causa era la correcta». Lo que este archivo congela es
**hasta dónde llega esa deducción**.
"""
from __future__ import annotations

from api.services.av_agent_acciones import REGLA_DE_ACCION


def test_solo_se_derivan_las_acciones_de_UNA_sola_regla():
    """`arreglar_bono` resuelve `tasa_sospechosa`, que emite SEIS reglas. Elegirle
    una mediría la precisión de una regla con los votos de otra — peor que no
    tener el dato, porque además lo esconde detrás de un número."""
    assert "arreglar_bono" not in REGLA_DE_ACCION
    assert set(REGLA_DE_ACCION) == {"alta_bono", "completar_flujos", "crear_curva"}


def test_IGNORAR_no_es_un_voto():
    """«No me interesa este bono» no dice que el agente se haya equivocado: dice
    que el bono no importa. Contarlo como ✖ castigaría al detector por hacer bien
    su trabajo sobre un papel irrelevante."""
    assert "ignorar_ticker" not in REGLA_DE_ACCION
    assert "designorar" not in REGLA_DE_ACCION


def test_la_regla_derivada_es_la_que_el_detector_EMITE_de_verdad():
    """Si el mapa dijera una regla que ningún detector emite, los votos caerían en
    una causa fantasma y la precisión real quedaría midiéndose sin ellos."""
    import pathlib
    import re
    emitidas = set()
    for f in pathlib.Path("api/services").glob("*.py"):
        for m in re.finditer(r'_hallazgo\(\s*"[a-z_]+"\s*,\s*[^,]+,\s*"([a-z_]+)"',
                             f.read_text(encoding="utf-8")):
            emitidas.add(m.group(1))
    for accion, regla in REGLA_DE_ACCION.items():
        assert regla in emitidas, (
            f"{accion} deriva la regla «{regla}» y ningún detector la emite")


def test_un_tipo_con_VARIAS_reglas_no_puede_entrar_al_mapa():
    """La guarda que impide que alguien sume `arreglar_bono` sin pensarlo: si el
    tipo que resuelve emite más de una regla, la deducción no es válida."""
    import pathlib
    import re
    from collections import defaultdict
    por_tipo = defaultdict(set)
    for f in pathlib.Path("api/services").glob("*.py"):
        for m in re.finditer(
                r'_hallazgo\(\s*"([a-z_]+)"\s*,\s*[^,]+,\s*"([a-z_]+)"',
                f.read_text(encoding="utf-8")):
            por_tipo[m.group(1)].add(m.group(2))
    # Cada regla derivada tiene que ser la ÚNICA de su tipo.
    for accion, regla in REGLA_DE_ACCION.items():
        tipo = next(t for t, rs in por_tipo.items() if regla in rs)
        assert len(por_tipo[tipo]) == 1, (
            f"{accion} deriva «{regla}», pero su tipo «{tipo}» emite "
            f"{len(por_tipo[tipo])} reglas: la deducción es ambigua")


def test_el_voto_derivado_NO_abre_la_compuerta():
    """Si contara para `candidata_a_auto`, el agente podría habilitarse solo:
    propone, el humano aprueba por otra razón, y eso se lee como «acertó 10 de
    10». Los derivados dan contexto; la compuerta la abren los humanos."""
    import inspect

    from api.services import av_agent_evals
    src = inspect.getsource(av_agent_evals.resumen)
    assert "origen = 'humano'" in src
    # `candidata_a_auto` tiene que calcularse sobre el conteo HUMANO.
    assert "nh >= MIN_VOTOS and okh == nh" in src


def test_sembrar_dos_veces_no_duplica():
    """`ref` es único: re-correr el sembrado es idempotente y no infla el número."""
    import pathlib
    sql = pathlib.Path("sql/schema.sql").read_text(encoding="utf-8")
    assert "ux_av_agent_evals_ref" in sql
    import inspect

    from api.services import av_agent_evals
    assert "ON CONFLICT (ref)" in inspect.getsource(av_agent_evals.votar)
