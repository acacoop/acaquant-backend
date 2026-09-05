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


# ═══════════════════════════════════════════════════════════════════════════
# LOS CONTEOS ESCRITOS A MANO — la desincronización que no falla
# ═══════════════════════════════════════════════════════════════════════════
#
# ⚠️⚠️ **UN NÚMERO EN PROSA ES UNA COPIA SIN ÁRBITRO** (REGLA #9), y esta clase
# se rompió en cuatro lugares a la vez: el doc decía «las 24 habilidades»,
# `agente/redactar.py` decía «16 de 24», el router decía lo mismo y el rule del
# front decía «las 25» — con **27** en el catálogo. Ninguna de las cuatro
# fallaba: cada archivo era coherente consigo mismo, y el que leía cualquiera de
# ellos salía con un número inventado.
#
# El propio doc ya declara la regla en su encabezado —*«el número manda desde el
# código; si acá aparece un conteo, es una foto con fecha»*— pero declararla no
# la cumple. Esto la cumple.
#
# **Solo se mira «las/sus N habilidades».** Es la forma que AFIRMA cuántas hay.
# Queda afuera a propósito:
#   · el conteo de TABLAS — «cinco tablas» aparece hablando del schema `ap5` y
#     del top de peso de la base, así que el regex no puede distinguir el que
#     habla del agente del que no. Se cubre no escribiéndolo (`rules/agente.md`
#     manda a `sql/schema.sql`), no con un test que grita en falso;
#   · un hecho fechado del diario («12 habilidades reventaron escribiendo el
#     2026-08-24») — no es un conteo del catálogo, es qué pasó ese día;
#   · la PARTE B entera, donde un número viejo ES el punto.
_CONTEO = re.compile(r"(?:las|sus)\s+\*{0,2}(\d+)\*{0,2}\s+habilidades")

# Los números escritos con letra cuentan igual: un regex de dígitos no ve
# «las nueve habilidades», y el rule del agente ya había escrito así el conteo
# de tablas cuando quedó viejo.
_EN_LETRA = {"cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
             "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
             "catorce": 14, "quince": 15, "veinte": 20}
_CONTEO_LETRA = re.compile(
    r"(?:las|sus)\s+\*{0,2}(" + "|".join(_EN_LETRA) + r")\*{0,2}\s+habilidades")


def _parte_a() -> str:
    """Solo la especificación viva. La PARTE B es histórica y sus números son
    fotos con fecha: mirarlos sería exigirle al diario que se reescriba."""
    doc = DOC.read_text(encoding="utf-8")
    return doc[:doc.index("# PARTE B")]


def _donde_no_puede_haber_un_conteo_viejo() -> dict[str, str]:
    """Lo que se lee como si describiera el agente de HOY."""
    out = {"docs/AGENT.md (PARTE A)": _parte_a()}
    for rel in (".claude/rules/agente.md", "api/routers/agente.py"):
        out[rel] = (RAIZ / rel).read_text(encoding="utf-8")
    for f in sorted((RAIZ / "agente").rglob("*.py")):
        out[str(f.relative_to(RAIZ))] = f.read_text(encoding="utf-8")
    return out


def test_ningun_conteo_de_habilidades_quedo_viejo():
    """**El número manda desde `agente/catalogo.py`, no desde la prosa.**

    Si no coincide hay dos caminos, y los dos son mejores que actualizarlo a
    mano: **sacarlo** (casi siempre la frase funciona igual sin él) o mandar a
    leer la fuente. Actualizarlo deja el mismo problema para dentro de un mes.
    """
    from agente.catalogo import HABILIDADES

    real, mal = len(HABILIDADES), []
    for archivo, texto in _donde_no_puede_haber_un_conteo_viejo().items():
        for regex, traducir in ((_CONTEO, int), (_CONTEO_LETRA, _EN_LETRA.get)):
            for m in regex.finditer(texto):
                if traducir(m.group(1)) != real:
                    mal.append(f"{archivo}: dice «{m.group(0)}» y hay {real}")
    assert not mal, (
        "conteos que quedaron viejos —cada uno es una copia sin árbitro "
        "(REGLA #9), y ninguna falla sola:\n  " + "\n  ".join(mal)
        + "\n\nSacá el número o mandá a leer `agente/catalogo.py`.")


def test_la_guarda_de_conteos_mira_algo():
    """Un regex que no matchea nada pasa en verde para siempre."""
    from agente.catalogo import HABILIDADES

    assert len(HABILIDADES) > 10, "¿se vació el catálogo?"
    assert _CONTEO.search("son las 27 habilidades del catálogo")
    assert _CONTEO_LETRA.search("las nueve habilidades de SISTEMA")
    # Y un hecho fechado del diario NO tiene que entrar.
    assert not _CONTEO.search("12 habilidades reventaron escribiendo")
