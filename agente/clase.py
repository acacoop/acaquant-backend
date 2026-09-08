"""`agente/clase.py` — LA CLASE DE ACTIVO, PROPUESTA Y CON SU FUENTE.

Doc: `docs/AGENT.md` §0.ei → habilidad `ficha_incompleta`, regla `sin_clase_activo`.

Mismo espíritu que `agente/emisor.py` (el patrón «proponer con fuente»), pero
SIN modelo por ahora: dos reglas DETERMINÍSTICAS (`core.clase_activo`)
proponen un valor y de dónde salió; la escritura sigue siendo la de siempre
(`agente.arreglos.CompletarFicha`) y una persona confirma — o, si la regla es
determinística y el valor ya existe en el catálogo, `CompletarFicha.solo` la
aplica sin que nadie apriete.

    REGLA    — cartera DERIVADOS con C/P al final → CALL/PUT OPCIONES
    PRIMARY  — cartera FCI, ficha del fondo en Primary por NOMBRE

⚠️ **LISTA CERRADA, mismo invariante que `emisor.py`.** Una regla no inventa
grafías: si el valor que la regla derivaría (p. ej. `PUT OPCIONES`) todavía no
existe en `clase_activo`, la fila NO se escribe sola — viaja con
`propuesto=""` y una nota que dice qué hay que cargar una vez a mano.
"""
from __future__ import annotations

from core.clase_activo import de_derivado, de_fci, normalizar_nombre

REGLA, PRIMARY = "regla", "primary"

_CARTERAS_FCI = {"FCI", "CARTERA FCI"}


def _indice_primary(fichas: list[dict]) -> dict[str, dict]:
    """`{normalizar_nombre(simbolo): ficha}`. **El PRIMERO gana** ante un
    nombre repetido: un criterio estable, y no el orden en que Postgres
    devuelva las filas (misma razón que `emisor.por_nombre`)."""
    out: dict[str, dict] = {}
    for f in fichas or []:
        clave = normalizar_nombre(f.get("simbolo") or "")
        if clave and clave not in out:
            out[clave] = f
    return out


def proponer(filas: list[dict], fichas_primary: list[dict] | None,
             usadas: list[str]) -> list[dict]:
    """Cada fila con `propuesto`, `fuente` y `nota`. **PURA**: no toca base ni
    red — recibe todo lo que necesita, igual que `emisor.proponer`.
    """
    indice = _indice_primary(fichas_primary or [])
    permitidos = set(usadas or [])
    out = []
    for f in filas:
        fila = {**f, "propuesto": "", "fuente": "", "nota": ""}
        cartera = (f.get("cartera") or "").strip().upper()
        if (v := de_derivado(cartera, f.get("unidad", ""), f.get("ticker", ""))):
            fila["propuesto"], fila["fuente"] = v, REGLA
        elif cartera in _CARTERAS_FCI:
            ficha = indice.get(normalizar_nombre(f.get("ticker") or ""))
            if ficha and (v := de_fci(ficha.get("subyacente", ""),
                                      ficha.get("moneda", ""))):
                fila["propuesto"], fila["fuente"] = v, PRIMARY

        # ⚠️ **LA LISTA CERRADA.** Un valor propuesto por regla solo se escribe
        # si YA existe en `clase_activo`: una regla no inventa grafías (mismo
        # invariante que el modelo en `emisor.py`).
        if fila["propuesto"] and fila["propuesto"] not in permitidos:
            valor = fila["propuesto"]
            fila["propuesto"], fila["fuente"] = "", ""
            fila["nota"] = (f"la regla dice «{valor}», pero ese valor todavía "
                            "no existe en clase_activo: cargalo una vez a mano")
        out.append(fila)
    return out


def deterministas(filas_propuestas: list[dict]) -> list[dict]:
    """`[{"unidad", "valor"}]` de lo que una regla determinística propuso Y
    superó la lista cerrada. Es lo único que `CompletarFicha.solo` puede
    escribir SIN que nadie apriete: lo del modelo nunca va por acá (§0.ei)."""
    return [{"unidad": f["unidad"], "valor": f["propuesto"]}
            for f in filas_propuestas
            if f["propuesto"] and f["fuente"] in (REGLA, PRIMARY)]
