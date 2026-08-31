"""`scripts/` no puede volver a crecer en silencio.

## El problema, dicho sin vueltas

**Para un script que corre una PERSONA, el repo no puede probar que se usa.** Que
nadie lo importe es lo NORMAL, no la señal — y por eso durante meses la única
defensa fue que alguien se acordara de borrar. Nadie se acuerda: cerrar un tema y
volver al archivo son dos actos distintos, y el segundo no tiene quien lo pida.
Medido el 2026-08-28: **143 scripts, 87 de ellos `diag_*`**.

Hubo un `scripts/diag_scripts_muertos.py` que estimaba esto con heurísticas y él
mismo admitía que «no puede decidir solo». Se borró: un diagnóstico que hay que
acordarse de correr tiene el mismo problema que el que quería resolver.

## La regla, que sí se puede chequear

Un script vive en `scripts/` si cumple **al menos una**:

  1. **Algo AUTOMÁTICO lo corre** — el crontab, la CI, un hook o una skill de
     `.claude/`, o un test lo importa.
  2. **El CÓDIGO manda a correrlo** — un mensaje de error o un docstring que le
     dice al operador «correr `python -m scripts.x`». Si el script no está, el
     que lee ese mensaje queda sin salida.
  3. **ESCRIBE** — altas, cargas, siembras, migraciones, exports, DDL. Eso no es
     un diagnóstico: es la superficie operativa del sistema.
  4. **Está declarado abajo con un motivo en una línea.**

Lo que no cumple ninguna es un `diag_*` read-only que nadie corre. Se borra: git
lo tiene, y un diag se reescribe en diez minutos **con el contexto de hoy**, que
es mejor que uno de hace tres meses con el contexto de entonces.

## Por qué la allowlist es una lista escrita a mano y no otra heurística

Las tres primeras condiciones las mide la máquina. La cuarta es el lugar donde
una persona dice «este me sirve **por esto**», y el costo de escribir el motivo
es exactamente el filtro: si no se puede escribir en una línea por qué existe,
no existe. La lista completa entra en una pantalla y se revisa de un vistazo —
que es lo que ninguna carpeta de 143 archivos permite.
"""
from __future__ import annotations

import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = RAIZ / "scripts"

# ── (4) LOS DECLARADOS. Read-only, nadie automático los corre, y se quedan
#        igual. El motivo es obligatorio: es el filtro.
HERRAMIENTAS: dict[str, str] = {
    # feeds y superficies que no tienen otro productor
    "eikon_feed_simple":     "EL productor del feed Eikon: corre en la PC de la oficina con Workspace "
                             "abierto y alimenta /api/ingest/eikon/*. Sin esto la tab REUTERS no tiene datos.",
    "ext_ver":               "inspector de la API externa (/ext): qué ve un accionista con SU token.",
    # contratos y auditoría (dan un veredicto, no una opinión)
    "audit_superficie_http": "inventario de la superficie HTTP por categoría — la contracara de audit_rbac.",
    "check_proxies_next":    "cruza los routers del backend contra los proxies de Next: un handler que falta "
                             "deja el panel vacío EN SILENCIO. Tiene --strict; debería estar en CI.",
    "diag_entorno":          "instalado vs. pineado en requirements.txt. Un drift acá no falla: cambia el "
                             "comportamiento y nadie lo ve.",
    # monitoreo de integraciones vivas
    "healthcheck_sql":       "ejecuta el reader REAL de cada dominio contra Postgres. Es el smoke de después "
                             "de un deploy grande o un cambio de schema.",
    "diag_interbanking":     "smoke de las 5 APIs de Interbanking con datos reales. Distinto de «respondió», "
                             "que es lo único que mira el detector `proveedor_caido`.",
    "diag_postrade_auth":    "⚠️ Postrade NO está en `core/proveedores.PROVEEDORES`: el agente NO lo vigila, "
                             "y es la API que PUEDE OPERAR. Esto es lo único que contesta «¿entramos?».",
    "diag_postrade_metodos": "qué métodos de Postrade nos habilitaron. Cambia cuando ACyRSA toca permisos, "
                             "y el reclamo se hace con esta salida.",
    # performance (REGLA #5 los llama recurrentes)
    "diag_mayor_mapeo":      "lo nombra `diag_mayor_estado` (que SÍ corre por cron) en su salida al "
                             "operador: «a otra cuenta: python -m scripts.diag_mayor_mapeo lo separa».",
    "diag_costo_real":       "dónde se va el tiempo, cruzando latencia de endpoints con el costo dentro de "
                             "Postgres. La mitad de BASE no la cubre ningún detector.",
    "profile_motor":         "perfila un motor always-on en vivo con py-spy SIN reiniciarlo — reiniciar en "
                             "rueda corta el feed de precios de la mesa.",
    # build
    "lock_deps":             "repinea requirements.txt fotografiando el venv que YA corre en prod "
                             "(no resuelve contra PyPI, que pinearía a la última). Es lo que evita que un "
                             "`pip install` traiga una versión nueva y tumbe el boot en el restart.",
    "sellar_cierres":        "siembra el histórico de cierres de Interbanking (one-shot re-corrible).",
}

_RE_ESCRIBE = re.compile(
    r"\bINSERT\s+INTO\b|\bUPDATE\s+\w|\bDELETE\s+FROM\b|\bCREATE\s+(?:TABLE|SCHEMA|INDEX)\b|"
    r"\bDROP\s+(?:TABLE|SCHEMA)\b|\bALTER\s+TABLE\b|\bTRUNCATE\b|\bVACUUM\b|"
    r"write_native|append_native|write_hist|replace_native|merge_jsonb_native|"
    r"\.to_excel\(|\.save\(|open\([^)]*[\"']w", re.I)
_RE_MANDA = re.compile(r"correr|refrescarlo|sembrar|siembra|chequeo previo|python -m|lo separa", re.I)


def _nombres() -> set[str]:
    return {p.stem for p in SCRIPTS.glob("*.py") if p.stem != "__init__"}


def _corridos_por_algo_automatico() -> set[str]:
    """crontab · CI · deploy.sh · hooks y skills de .claude · tests."""
    out: set[str] = set()
    fuentes = [RAIZ / f for f in ("deploy/crontab.txt", ".github/workflows/ci.yml", "deploy/deploy.sh")]
    fuentes += [p for p in (RAIZ / ".claude").rglob("*") if p.is_file()]
    # ⚠️ **ESTE ARCHIVO SE EXCLUYE, y no es un detalle.** La allowlist de abajo
    # nombra scripts (`scripts.diag_mayor_mapeo` adentro de un motivo), así que
    # sin esta línea el test se leía a sí mismo como evidencia: cada entrada
    # declarada quedaba además "corrida por algo automático", y la condición ①
    # pasaba a ser cierta por el solo hecho de estar en la lista. Un chequeo que
    # se cita a sí mismo no chequea nada.
    fuentes += [p for p in (RAIZ / "tests").rglob("*.py")
                if "__pycache__" not in p.parts and p.name != "test_scripts.py"]
    for p in fuentes:
        if not p.exists():
            continue
        out |= set(re.findall(r"scripts[./](\w+)", p.read_text(encoding="utf-8", errors="ignore")))
    return out


def _mandados_por_el_codigo() -> set[str]:
    """Un mensaje de error o un docstring que le dice al operador que lo corra."""
    out: set[str] = set()
    for d in ("api", "core", "jobs", "engines", "agente"):
        for p in (RAIZ / d).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            for linea in p.read_text(encoding="utf-8", errors="ignore").split("\n"):
                if _RE_MANDA.search(linea):
                    out |= set(re.findall(r"scripts[./](\w+)", linea))
    return out


def _escriben() -> set[str]:
    return {p.stem for p in SCRIPTS.glob("*.py")
            if p.stem != "__init__" and _RE_ESCRIBE.search(p.read_text(encoding="utf-8", errors="ignore"))}


def test_todo_script_justifica_su_lugar():
    """La regla. Un `diag_*` nuevo que nadie corre falla acá, no dentro de un año."""
    justificados = (_corridos_por_algo_automatico() | _mandados_por_el_codigo()
                    | _escriben() | set(HERRAMIENTAS))
    sobran = sorted(_nombres() - justificados)
    assert not sobran, (
        "estos scripts no cumplen NINGUNA de las cuatro condiciones:\n  "
        + "\n  ".join(sobran)
        + "\n\nO algo los corre, o el código los nombra, o escriben, o se declaran en "
          "HERRAMIENTAS de este archivo con un motivo de una línea. Si no se puede "
          "escribir el motivo, no hay motivo: borralos (git los conserva).")


def test_la_allowlist_no_tiene_fantasmas():
    """Una lista que nombra archivos que no existen es la misma basura que
    denuncia — y encima hace creer que hay una herramienta que no está."""
    fantasmas = sorted(set(HERRAMIENTAS) - _nombres())
    assert not fantasmas, (
        f"HERRAMIENTAS declara scripts que ya no existen: {fantasmas}. "
        "Al borrar un script, sacá también su renglón.")


def test_todo_declarado_explica_por_que():
    """El motivo es el filtro, así que tiene que ser un motivo y no un rótulo."""
    flojos = [n for n, m in HERRAMIENTAS.items() if len(m.strip()) < 40]
    assert not flojos, (
        f"estos declaran un motivo demasiado corto para ser uno: {flojos}")


def test_el_barrido_mira_algo():
    """Los tres de arriba pasan en verde con la carpeta vacía o la ruta cambiada:
    el mismo «no pude» disfrazado de «está todo bien»."""
    assert len(_nombres()) > 20, f"solo {len(_nombres())} scripts: ¿cambió la ruta?"
    assert _corridos_por_algo_automatico(), "cero scripts corridos por cron/CI: ¿cambió el formato?"
    assert _escriben(), "cero scripts que escriben: ¿cambió el detector?"
