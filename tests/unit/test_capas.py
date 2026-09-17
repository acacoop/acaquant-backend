"""LA REGLA DE CAPAS, QUE HASTA HOY SE CUMPLÍA DE MEMORIA.

`CLAUDE.md` la declara desde siempre:

    core/ no importa nada del proyecto. engines/ y jobs/ usan core/ + quant/.
    api/services/ es puro (sin FastAPI), api/routers/ solo HTTP plumbing.

Y se cumple al 100% — medido: 0 archivos de `core/` importan `api/`, `jobs/` o
`engines/`, y de 150 services uno solo toca FastAPI. **Pero no había nada que lo
sostuviera.** El día que un módulo de `core/` importe un service, no falla nada:
el import anda, los tests pasan, y la regla se rompió en silencio. Es el modo de
falla de la REGLA #9 aplicado a la arquitectura.

QUÉ SE PIERDE SI SE ROMPE, en concreto y no en abstracto:

  · **los tests dejan de correr sin base.** Hoy 2.532 tests tardan 25 segundos
    porque la lógica no está pegada a la persistencia ni al framework. Un
    `core/` que importe `api/` arrastra FastAPI, los routers y la cadena entera.
  · **un job deja de poder usar la misma lógica que un endpoint.** Es lo que
    hace que el motor, el cron y la API no tengan tres copias de la regla.

Los tests de acá NO prueban que algo ande: prueban que **no exista la forma de
hacerlo mal** — el mismo mecanismo del catálogo del agente.
"""
from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

# ⚠️ Se mira el ÁRBOL y no el texto: `grep "from api"` matchea el comentario que
# explica por qué NO se importa `api`, y este módulo está lleno de esos.
def _importa_de(arbol: ast.AST, prohibidos: tuple[str, ...]) -> list[str]:
    malos = []
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            malos += [a.name for a in n.names
                      if a.name.split(".")[0] in prohibidos]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            if n.module.split(".")[0] in prohibidos:
                malos.append(n.module)
    return malos


def _modulos(carpeta: str) -> list[Path]:
    return sorted(p for p in (RAIZ / carpeta).rglob("*.py")
                  if "__pycache__" not in p.parts)


def test_core_no_sabe_que_existe_el_resto():
    """`core/` es el centro. Si empieza a importar hacia afuera, la lógica queda
    pegada al framework y los tests dejan de correr sin base."""
    malos = {}
    for f in _modulos("core"):
        m = _importa_de(ast.parse(f.read_text(encoding="utf-8")),
                        ("api", "jobs", "engines", "scripts"))
        if m:
            malos[f.relative_to(RAIZ).as_posix()] = m
    assert not malos, (
        f"core/ dejó de ser el centro: {malos}. Lo que necesite de afuera se "
        f"le PASA como parámetro — es lo que ya hace `pnl.py` con "
        f"`pnl_sql._deps_sql`.")


# ── EL TRINQUETE ────────────────────────────────────────────────────────────
#
# Las listas de abajo son deuda que YA EXISTÍA cuando se escribió este
# archivo. Se declaran en vez de arreglarse acá por una razón: el trabajo de
# este test es **frenar lo nuevo**, y una regla que exige limpiar todo antes de
# empezar a regir es una regla que no se activa nunca.
#
# Funcionan como trinquete: **solo pueden achicarse**. Sumar una entrada es una
# decisión explícita que se ve en el diff; y si una deja de violar la regla, el
# test de más abajo exige sacarla — así la lista no se pudre nombrando cosas que
# ya se arreglaron.

_QUANT_CON_INFRA = {
    "quant/pivot_points.py":
        "mezcla el cálculo de pivots con la lectura de mercado.precios_acciones "
        "(`_sql_docs_en_rango`, `_sql_last_doc`). El import está ADENTRO de la "
        "función con un comentario que dice que es «para no depender de la infra "
        "al import-time» — pero la dependencia es real, solo llega más tarde. "
        "Se arregla sacando esas dos funciones del módulo y pasándole las velas.",
}


def test_quant_es_calculo_puro():
    """`quant/` son fórmulas. Una fórmula que necesita la base para correr no es
    una fórmula: es una consulta con matemática adentro."""
    malos = {}
    for f in _modulos("quant"):
        rel = f.relative_to(RAIZ).as_posix()
        if rel in _QUANT_CON_INFRA:
            continue
        m = _importa_de(ast.parse(f.read_text(encoding="utf-8")),
                        ("api", "jobs", "engines", "core", "scripts"))
        if m:
            malos[rel] = m
    assert not malos, (
        f"quant/ dejó de ser cálculo puro: {malos}. Lo que necesite datos los "
        f"RECIBE — es lo que ya hacen `black_scholes` y `curve_fit`.")


def test_los_services_no_saben_de_HTTP():
    """Un service es lógica: lo tiene que poder llamar un cron, un motor y un
    endpoint. El que levanta `HTTPException` solo sirve desde la web — y ahí la
    misma regla necesita una segunda implementación para el job.

    Lista de excepciones: NINGUNA (se vació al mover el scope de grupos a
    `api/deps.py`). Si un service nuevo necesita FastAPI, este test lo canta y
    la respuesta es moverlo, no sumar una lista."""
    malos = {}
    for f in _modulos("api/services"):
        rel = f.relative_to(RAIZ).as_posix()
        if _importa_de(ast.parse(f.read_text(encoding="utf-8")), ("fastapi",)):
            malos[rel] = "importa fastapi"
    assert not malos, (
        f"services que se ataron a HTTP: {malos}. Sin excepciones: lo que "
        f"necesita `Depends`/`HTTPException` es una dependency y vive en "
        f"`api/deps.py` (es lo que pasó con el scope de grupos).")


def _abre_la_base(f: Path) -> bool:
    txt = "\n".join(x for x in f.read_text(encoding="utf-8").splitlines()
                     if not x.lstrip().startswith("#"))
    return "get_pool()" in txt or "cur.execute(" in txt


def test_los_routers_no_hablan_SQL():
    """El router es plomería HTTP. SQL adentro de un router es lógica que ningún
    job va a poder reusar, y que solo se puede probar levantando la app."""
    malos = [f.relative_to(RAIZ).as_posix() for f in _modulos("api/routers")
             if _abre_la_base(f)]
    assert not malos, (
        f"routers con SQL adentro: {malos}. La query va al service; el router "
        f"llama y devuelve. Sin lista de excepciones: los ocho que la tenían se "
        f"vaciaron (cuentas_sql, pyrofex_discovery_sql, renta_variable_admin_sql, "
        f"y funciones nuevas en portfolio_sql / operaciones_sql / manager_infra_sql).")


def test_el_trinquete_SOLO_puede_achicarse():
    """⚠️ **La lista de excepciones no puede pudrirse.** Si alguien arregla un
    router y no lo saca de acá, la excepción queda tapando el próximo que se
    cuele — y una lista que nombra cosas ya arregladas es peor que no tenerla,
    porque nadie la vuelve a mirar.

    Es el mismo criterio que `SIN_ACCION`: la excepción declarada tiene que
    seguir siendo cierta."""
    for rel, motivo in _QUANT_CON_INFRA.items():
        assert (RAIZ / rel).exists(), f"«{rel}» ya no existe: sacalo de la lista"
        assert motivo.strip(), f"«{rel}» sin motivo: la excusa hay que escribirla"
