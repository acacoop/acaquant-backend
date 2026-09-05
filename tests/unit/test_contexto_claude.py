"""El contexto que Claude carga en cada sesión tiene TECHO, y el techo solo baja.

## Por qué existe

`CLAUDE.md` llegó a 608 líneas / 55 kB (unos 14.000 tokens en CADA turno) porque
cada incidente dejaba su historia ahí: «el 2026-08-19 pasó X, medido Y». Anthropic
recomienda menos de 200 líneas y advierte que un CLAUDE.md inflado hace que Claude
IGNORE las instrucciones — que es exactamente lo que pasaba con la REGLA #0.

Pedirlo por escrito no alcanzó (ya estaba pedido). Esto lo convierte en un test:
falla en el mismo commit que infla el archivo, y CI bloquea el merge.

## Qué fija

1. **Techo de líneas y bytes** para la raíz, los `CLAUDE.md` de subcarpeta y cada
   regla de `.claude/rules/`. Los números de abajo SOLO BAJAN: subirlos es un diff
   visible en un test, no un párrafo más que nadie ve.
2. **La raíz no lleva fechas.** Si tiene fecha es historia, y la historia vive en
   el doc de su dominio (`docs/`), no en contexto.
3. **Cada `paths:` de una regla matchea al menos un archivo.** Sin esto, renombrar
   `curvas_sql.py` mata la regla en silencio: no falla nada, deja de cargarse.
   Es la REGLA #9 aplicada a la configuración.
4. **Cada regla figura en `.claude/INDEX.md`**, que es el índice humano.
"""
from __future__ import annotations

import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parents[2]
CLAUDE_RAIZ = RAIZ / "CLAUDE.md"
RULES = RAIZ / ".claude" / "rules"
INDEX = RAIZ / ".claude" / "INDEX.md"

# (líneas, bytes). Solo bajan.
TECHO_RAIZ = (200, 16_000)
TECHO_SUBCARPETA = (200, 16_000)
TECHO_REGLA = (250, 32_000)

_FECHA = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_GLOB = re.compile(r'^\s*-\s*"?([^"\n]+?)"?\s*$', re.M)


def _medida(p: pathlib.Path) -> tuple[int, int]:
    b = p.read_bytes()
    return b.count(b"\n") + (0 if b.endswith(b"\n") else 1), len(b)


def _paths_de(regla: pathlib.Path) -> list[str]:
    m = _FRONTMATTER.match(regla.read_text(encoding="utf-8"))
    if not m or "paths:" not in m.group(1):
        return []
    bloque = m.group(1).split("paths:", 1)[1]
    return [g.strip() for g in _GLOB.findall(bloque) if g.strip()]


def _reglas() -> list[pathlib.Path]:
    return sorted(RULES.glob("*.md"))


def test_raiz_bajo_el_techo():
    lineas, bytes_ = _medida(CLAUDE_RAIZ)
    assert lineas <= TECHO_RAIZ[0], (
        f"CLAUDE.md tiene {lineas} líneas (techo {TECHO_RAIZ[0]}). No se achica la "
        "letra: lo que es de un dominio va a .claude/rules/<dominio>.md, lo "
        "histórico a docs/."
    )
    assert bytes_ <= TECHO_RAIZ[1], f"CLAUDE.md pesa {bytes_} bytes (techo {TECHO_RAIZ[1]})"


def test_raiz_sin_fechas():
    fechas = _FECHA.findall(CLAUDE_RAIZ.read_text(encoding="utf-8"))
    assert not fechas, (
        f"CLAUDE.md raíz tiene fechas {sorted(set(fechas))}: si tiene fecha es "
        "historia, y la historia vive en el doc de su dominio (docs/), no en contexto."
    )


def test_subcarpetas_bajo_el_techo():
    subs = sorted(p for p in RAIZ.glob("*/CLAUDE.md") if ".venv" not in p.parts)
    assert subs, "se esperaban CLAUDE.md de subcarpeta (api/, engines/, jobs/, scripts/)"
    for p in subs:
        lineas, bytes_ = _medida(p)
        rel = p.relative_to(RAIZ)
        assert lineas <= TECHO_SUBCARPETA[0], f"{rel}: {lineas} líneas (techo {TECHO_SUBCARPETA[0]})"
        assert bytes_ <= TECHO_SUBCARPETA[1], f"{rel}: {bytes_} bytes (techo {TECHO_SUBCARPETA[1]})"


def test_reglas_bajo_el_techo():
    reglas = _reglas()
    assert reglas, ".claude/rules/ vacío — se esperaban reglas por dominio"
    for p in reglas:
        lineas, bytes_ = _medida(p)
        assert lineas <= TECHO_REGLA[0], f"{p.name}: {lineas} líneas (techo {TECHO_REGLA[0]})"
        assert bytes_ <= TECHO_REGLA[1], f"{p.name}: {bytes_} bytes (techo {TECHO_REGLA[1]})"


def test_reglas_con_paths_apuntan_a_archivos_que_existen():
    """Una regla scopeada a un archivo renombrado deja de cargarse SIN fallar."""
    for p in _reglas():
        for g in _paths_de(p):
            assert "{" not in g and "}" not in g, (
                f"{p.name}: el glob {g!r} usa llaves; este test no las expande — "
                "escribí un glob por línea."
            )
            assert any(RAIZ.glob(g)), (
                f"{p.name}: el glob {g!r} no matchea ningún archivo del repo. "
                "¿Se renombró? La regla quedó muerta en silencio."
            )


def test_reglas_figuran_en_el_index():
    index = INDEX.read_text(encoding="utf-8")
    faltan = [p.stem for p in _reglas() if f"`{p.stem}`" not in index]
    assert not faltan, f"reglas sin fila en .claude/INDEX.md: {faltan}"
