"""core/dependencias.py — DE QUÉ DEPENDE CADA PIEZA, sin escribir una lista.

Doc madre: **`docs/AV_AGENT.md`** §0.af.

Pedido del user (2026-08-20): *«los jobs, ¿a dónde apuntan? Ej: a Aunesa…
¿Aunesa está caído? Listo, avisar que dio error porque está caído Aunesa.
Adelantarte: no solamente avisar, sino que el aviso sea con más contexto»*.

**El agente ya veía las dos cosas y no las relacionaba.** En la misma pantalla:

    proveedor_caido   Aunesa no responde
    salud_job         portafolio_diario: la última corrida falló   ×80
    salud_job         tenencia (snapshot SQL): la última corrida falló

Tres avisos, un solo problema. Quien los lee tiene que saber de memoria que
`portafolio_diario` le pega a Aunesa para atar el cabo — y si no lo sabe, sale a
buscar un bug que no existe.

CÓMO SE SABE DE QUÉ DEPENDE UN JOB: SE LEE EL CÓDIGO
====================================================

**No hay ninguna tabla que mantener.** Un job que le pega a Aunesa lo hace
importando `core.aunesa`, así que la dependencia YA está escrita — en el
`import`. Acá se lee el árbol de sintaxis y se deriva.

Es la misma ley que el resto del contexto del agente (§0.r): *el inventario sale
del catálogo, no de una lista*. Una lista a mano se queda vieja el día que
alguien agrega un job y no se acuerda de anotarlo, **y no avisa** — justo el modo
de falla que este módulo existe para tapar.

Se sigue **un nivel de indirección**: `jobs/x` → `api.services.tesoreria` →
`core.aunesa` también cuenta. Más niveles empiezan a arrastrar dependencias que
no son de verdad (todo termina importando `core.postgres`).
"""
from __future__ import annotations

import ast
import functools
import logging
import pathlib

logger = logging.getLogger(__name__)

# Qué módulo del repo significa «le pega a este de afuera». Se declara solo esto
# —la punta— y el resto se deriva siguiendo los imports.
CLIENTE_DE: dict[str, str] = {
    "core.aunesa": "aunesa",
    "core.mercado_1816": "1816",
    "core.interbanking": "interbanking",
    # ⚠️ `bcra_api`, no `bcra`. La primera versión decía `core.bcra` —que no
    # existe— y por eso la dependencia del BCRA no se detectaba nunca. Un
    # catálogo a mano que nombra un módulo inexistente **no da error, da
    # silencio**, así que hay un test que lo cruza contra el repo.
    "core.bcra_api": "bcra",
}

# ⚠️ **Y NO TODOS PASAN POR EL CLIENTE ÚNICO.** Medido: `jobs/aum.py`,
# `jobs/cashflow.py`, `jobs/sync_comitentes.py` y `api/services/aunesa_negocio.py`
# le pegan a Aunesa con su propio `requests`. Eso tiene dos consecuencias: sus
# fallas **no dejan rastro** en `core/proveedores` (§0.ad), y por el import no se
# los puede relacionar con una caída.
#
# Migrarlos al cliente es lo correcto y es otro trabajo. Mientras tanto la
# dependencia se detecta igual **por el host que mencionan en el código**: el
# `import` es una pista, la URL es otra, y las dos están escritas.
HOST_DE: dict[str, str] = {
    "aca.aunesa.com": "aunesa",
    "api.1816.com.ar": "1816",
    "interbanking.com.ar": "interbanking",
    "api.bcra.gob.ar": "bcra",
}

# Hasta dónde se sigue la cadena de imports. Uno: `jobs/x` → `services/y` →
# `core.aunesa`. Con dos o más, todo depende de todo (cualquier módulo llega a
# `core.postgres`) y la correlación empieza a inventar causas.
PROFUNDIDAD = 1

_RAICES = ("jobs", "engines", "api")


def _modulo(ruta: pathlib.Path) -> str:
    return ".".join(ruta.with_suffix("").parts)


@functools.lru_cache(maxsize=1)
def _importa() -> dict[str, set[str]]:
    """`módulo → módulos del repo que importa`. Se lee una vez por proceso."""
    grafo: dict[str, set[str]] = {}
    base = pathlib.Path(__file__).resolve().parent.parent
    for raiz in _RAICES:
        for f in (base / raiz).rglob("*.py"):
            try:
                texto = f.read_text(encoding="utf-8")
                arbol = ast.parse(texto)
            except (SyntaxError, OSError):
                continue
            nombres: set[str] = set()
            # La URL cuenta como dependencia declarada: se guarda con un prefijo
            # para no confundirla con un import de verdad.
            nombres |= {f"host:{h}" for h in HOST_DE if h in texto}
            for n in ast.walk(arbol):
                if isinstance(n, ast.Import):
                    nombres |= {a.name for a in n.names}
                elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                    nombres.add(n.module)
                    # `from core import aunesa` — el módulo real es el de adentro.
                    nombres |= {f"{n.module}.{a.name}" for a in n.names}
            grafo[_modulo(f.relative_to(base))] = {
                x for x in nombres
                if x.startswith("host:") or x.split(".")[0] in _RAICES
                or x.startswith("core")}
    return grafo


@functools.lru_cache(maxsize=512)
def proveedores_de(modulo: str) -> frozenset[str]:
    """De qué proveedores EXTERNOS depende ese módulo (directo o a un salto)."""
    grafo = _importa()
    vistos, frontera, out = set(), {modulo}, set()
    for _ in range(PROFUNDIDAD + 1):
        siguiente: set[str] = set()
        for m in frontera:
            if m in vistos:
                continue
            vistos.add(m)
            for imp in grafo.get(m, set()):
                if imp in CLIENTE_DE:
                    out.add(CLIENTE_DE[imp])
                elif imp.startswith("host:"):
                    out.add(HOST_DE[imp[5:]])
                siguiente.add(imp)
        frontera = siguiente
    return frozenset(out)


def de_quien_depende(nombre: str) -> frozenset[str]:
    """Lo mismo, pero desde el NOMBRE que usa el agente (`portafolio_diario`,
    `job:tenencia`, `motor_curvas`).

    El agente nombra las piezas como las nombra el crontab o el registro de
    diagnóstico, no con la ruta del archivo. Traducir acá y no en cada caller es
    lo que evita que cada uno invente su propia forma de adivinar el módulo.
    """
    limpio = (nombre or "").strip().split(":")[-1].strip()
    if not limpio:
        return frozenset()
    grafo = _importa()
    # Se prueban las formas en que puede estar escrito, de la más específica a la
    # más laxa. Sin candidato NO se inventa: mejor no correlacionar que atribuir
    # una caída al job equivocado.
    for candidato in (f"jobs.{limpio}", f"engines.{limpio}",
                      f"engines.{limpio.removeprefix('motor_')}",
                      limpio):
        if candidato in grafo:
            return proveedores_de(candidato)

    # ⚠️ **EL LABEL DEL CRON NO ES EL NOMBRE DEL MÓDULO** (2026-08-20). SALUD
    # nombra sus chequeos `job:<label del crontab>`, y ese label es libre:
    # `portafolio_diario` corre `jobs.portafolio_backfill`, y un `*_chain` corre
    # VARIOS módulos. Por eso `de_quien_depende("job:portafolio_diario")` daba
    # vacío y la correlación no ataba el cabo — justo el caso que la motivó:
    #
    #     proveedor_caido   Aunesa no responde
    #     salud_job         portafolio_diario: la última corrida falló
    #
    # El AuM no se escribió el 2026-08-20 por un 500 de Aunesa y el aviso no
    # decía por qué. La traducción label → módulos ya existe en el parser del
    # crontab (`core.crontab`, el mismo que usa `jobs_catalogo`): se DELEGA,
    # no se copia.
    return _por_el_crontab(limpio)


def _por_el_crontab(label: str) -> frozenset[str]:
    """Un label del crontab → de qué dependen TODOS los módulos que corre.

    Un `*_chain` es una sola línea con varios `-m jobs.x`: si cualquiera de ellos
    le pega a un proveedor, la corrida entera depende de ese proveedor.
    """
    try:
        from core.crontab import parse_crontab
        crons = parse_crontab()
    except Exception as e:                      # el crontab no puede tumbar esto
        logger.debug("dependencias: sin crontab (%s)", e)
        return frozenset()
    out: set[str] = set()
    for c in crons:
        if (c.get("label") or "").strip() != label:
            continue
        for mod in c.get("modules") or []:
            out |= proveedores_de(mod)
    return frozenset(out)
