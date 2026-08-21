"""LOS IMPORTS DE `scripts/` TAMBIÉN TIENEN QUE EXISTIR.

⚠️⚠️ **REGLA #1 cubría `api/` y dejaba `scripts/` afuera.** El hook de pre-push
valida `from api.main import app` — que es lo que tumba la API entera — pero un
script se descubre roto **cuando el user lo corre en el Droplet**, que es
justo el momento en que menos sirve:

    ImportError: cannot import name 'get_ultimo_mep' from 'core.dolar_sql'

Pasó el 2026-08-22 con `diag_pesos_no_detectados`: la función vivía en
`api.services.macro`. Un `git pull`, un deploy y un traceback — por un símbolo.

**Y `ruff` no lo agarra**: es análisis estático de nombres, no resuelve el
módulo. Tampoco lo agarra un `import scripts.x`, porque los scripts importan
ADENTRO de `main()` a propósito (para no pagar el arranque de la app en un
diag). Hay que resolverlos a mano: eso hace este test.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

# Solo lo NUESTRO. Un `import openpyxl` que falta en el contenedor de CI no es
# un import roto — es una dependencia opcional, y hacer fallar el test por eso
# lo convertiría en ruido que alguien va a terminar salteando.
_NUESTROS = ("api", "core", "jobs", "engines", "quant", "scripts", "config")

_RAIZ = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS = sorted((_RAIZ / "scripts").glob("*.py"))


def _resuelve(modulo: str, nombre: str) -> str | None:
    """→ el motivo si NO resuelve, o `None` si está bien."""
    try:
        m = importlib.import_module(modulo)
    except ModuleNotFoundError as e:
        if not str(e.name or "").startswith(_NUESTROS):
            return None            # dependencia de terceros ausente: no es esto
        return f"no existe el módulo «{modulo}» ({e})"
    except Exception as e:         # un import con efecto secundario que falla
        return f"«{modulo}» no se puede importar: {type(e).__name__}: {e}"
    if hasattr(m, nombre):
        return None
    # `from core import curvas_sql` — el nombre es un SUBMÓDULO y no un
    # atributo hasta que alguien lo importa.
    try:
        importlib.import_module(f"{modulo}.{nombre}")
        return None
    except Exception:
        return f"«{modulo}» no tiene «{nombre}»"


@pytest.mark.parametrize("archivo", _SCRIPTS, ids=lambda p: p.name)
def test_los_imports_del_script_existen(archivo: pathlib.Path):
    arbol = ast.parse(archivo.read_text(encoding="utf-8"))
    fallas: list[str] = []
    for n in ast.walk(arbol):
        if not isinstance(n, ast.ImportFrom) or n.level:      # relativos: no
            continue
        modulo = n.module or ""
        if not modulo.startswith(_NUESTROS):
            continue
        for alias in n.names:
            if alias.name == "*":
                continue
            motivo = _resuelve(modulo, alias.name)
            if motivo:
                fallas.append(f"línea {n.lineno}: {motivo}")
    assert not fallas, (
        f"{archivo.name} importa cosas que no existen:\n  " + "\n  ".join(fallas))


def test_el_barrido_mira_algo():
    """Un test parametrizado sobre una lista vacía pasa en verde sin mirar
    nada — el mismo «no pude» disfrazado de «está bien» que el agente persigue
    en sus detectores."""
    assert len(_SCRIPTS) > 10, f"solo {len(_SCRIPTS)} scripts: ¿cambió la ruta?"
