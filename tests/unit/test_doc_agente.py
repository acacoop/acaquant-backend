"""`docs/AGENT.md` es EL doc del agente, y su diario no puede volver a crecer.

## Por qué existe

Hasta el 2026-08-31 había DOS docs del agente: `AGENT_2.0.md` (la spec) y
`AV_AGENT.md` (el diario). Es el mismo modo de falla que el agente persigue en
los datos —dos copias del mismo tema sin árbitro— y falló igual: **no falló
nada**. Cada doc era coherente consigo mismo, y el que abría el equivocado leía
con confianza una descripción de código borrado.

## Los dos invariantes, y por qué son DOS

Una sola dirección no alcanza:

  1. **Toda cita `§0.x` del repo tiene que existir en el doc.** Sin esto, podar
     el diario manda a alguien a buscar una sección que no está.
  2. **Toda entrada del diario tiene que estar citada por algo.** Sin esto el
     diario vuelve a crecer solo, que es exactamente como llegó a 611 kB: nadie
     borra una historia, porque borrar historia se siente mal. La regla saca la
     decisión del gusto — vive si el código la necesita.
"""
from __future__ import annotations

import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parents[2]
DOC = RAIZ / "docs" / "AGENT.md"
_EXT = (".py", ".md", ".sql", ".yml", ".yaml", ".sh", ".txt")


def _citas_del_repo() -> dict[str, set[str]]:
    """→ {ancla: {archivos que la citan}}, mirando TODO el repo menos el doc."""
    out: dict[str, set[str]] = {}
    for p in RAIZ.rglob("*"):
        if p.is_dir() or p == DOC:
            continue
        if ".git" in p.parts or "__pycache__" in p.parts or p.suffix not in _EXT:
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for a in set(re.findall(r"§0\.([a-z]+)", txt)):
            out.setdefault(a, set()).add(str(p.relative_to(RAIZ)))
    return out


def _anclas_del_doc() -> set[str]:
    return set(re.findall(r"^#{3,4} 0\.([a-z]+) ", DOC.read_text(encoding="utf-8"), re.M))


def test_el_doc_unico_existe_y_los_dos_viejos_no():
    """Un doc del agente, no tres. Si alguien recrea uno de los viejos, el
    lector vuelve a tener dos verdades y ninguna que mande."""
    assert DOC.exists(), "falta docs/AGENT.md"
    for viejo in ("AGENT_2.0.md", "AV_AGENT.md"):
        assert not (RAIZ / "docs" / viejo).exists(), (
            f"volvió a aparecer docs/{viejo}: el agente tiene UN doc, "
            "`docs/AGENT.md` (parte A = cómo funciona, parte B = el diario)")


def test_ninguna_cita_del_repo_queda_colgada():
    faltan = {a: sorted(f)[:3] for a, f in _citas_del_repo().items()
              if a not in _anclas_del_doc()}
    assert not faltan, (
        "hay §0.x citadas que NO están en docs/AGENT.md — un ancla rota manda "
        f"a buscar algo que no existe:\n  {faltan}")


def test_ninguna_entrada_del_diario_sobra():
    """La regla que impide que el diario vuelva a 611 kB."""
    sobran = sorted(_anclas_del_doc() - set(_citas_del_repo()))
    assert not sobran, (
        "entradas del diario que no cita NADIE en el repo: "
        f"{['§0.'+a for a in sobran]}. O el código las nombra, o se borran "
        "(git las conserva). Es la regla de la PARTE B.")


def test_el_barrido_mira_algo():
    """Los tres tests de arriba pasan en verde con un doc vacío o una ruta
    equivocada: el mismo «no pude» disfrazado de «está bien»."""
    anclas, citas = _anclas_del_doc(), _citas_del_repo()
    assert len(anclas) > 15, f"solo {len(anclas)} anclas: ¿cambió el formato del header?"
    assert len(citas) > 15, f"solo {len(citas)} citas: ¿cambió la ruta o la extensión?"
