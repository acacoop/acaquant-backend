"""`agente/clase.py` — LA CLASE DE ACTIVO, PROPUESTA Y CON SU FUENTE.

Doc: `docs/AGENT.md` §0.ei → habilidad `ficha_incompleta`, regla `sin_clase_activo`.

Mismo espíritu que `agente/emisor.py` (el patrón «proponer con fuente»), pero
SIN modelo por ahora: CINCO reglas DETERMINÍSTICAS (`core.clase_activo`)
proponen un valor y de dónde salió; la escritura sigue siendo la de siempre
(`agente.arreglos.CompletarFicha`) y una persona confirma — o, si la regla es
determinística y el valor ya existe en el catálogo, `CompletarFicha.solo` la
aplica sin que nadie apriete.

    REGLA    — cartera DERIVADOS con C/P al final → CALL/PUT OPCIONES
    REGLA    — DERIVADOS sin C/P: futuros/OTC de agro y dólar por el prefijo
    REGLA    — copia de la cartera (RENTA VARIABLE/HD/DL) → esa misma clase
    PRIMARY  — cartera FCI, ficha del fondo en Primary por NOMBRE
    CURVA    — cartera ARS, por los EJES del bono en el master de curvas

⚠️ **LISTA CERRADA, mismo invariante que `emisor.py`.** Una regla no inventa
grafías: si el valor que la regla derivaría (p. ej. `PUT OPCIONES`) todavía no
existe en `clase_activo`, la fila NO se escribe sola — viaja con
`propuesto=""` y una nota que dice qué hay que cargar una vez a mano. La
comparación es tolerante a grafía (`core.clase_activo.en_lista_cerrada`): si
`MAIZ`/`MAÍZ` ya normalizan igual, se escribe la grafía que YA está en la
base, nunca la de la regla.
"""
from __future__ import annotations

from core.clase_activo import (
    de_cartera,
    de_curva,
    de_derivado,
    de_fci,
    de_futuro,
    en_lista_cerrada,
    normalizar_nombre,
)

REGLA, PRIMARY, CURVA = "regla", "primary", "curva"

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


def _indice_master(master: list[dict] | None) -> dict[str, dict]:
    """`{ticker_corto.upper(): doc}` del master de `mercado.curvas`
    (`agente.fuentes.master()`). **El PRIMERO gana**, mismo criterio que
    `_indice_primary`. ⚠️ En el blob del master `ticker_corto` es el corto
    (`AL30`) — los nombres están cruzados, ver `agente/fuentes.py::master`."""
    out: dict[str, dict] = {}
    for d in master or []:
        clave = (d.get("ticker_corto") or "").strip().upper()
        if clave and clave not in out:
            out[clave] = d
    return out


def proponer(filas: list[dict], fichas_primary: list[dict] | None,
             usadas: list[str], master: list[dict] | None = None) -> list[dict]:
    """Cada fila con `propuesto`, `fuente` y `nota`. **PURA**: no toca base ni
    red — recibe todo lo que necesita, igual que `emisor.proponer`. `master`
    es la lista de docs de `mercado.curvas` (`agente.fuentes.master()`), para
    la regla CURVA.
    """
    indice = _indice_primary(fichas_primary or [])
    indice_master = _indice_master(master)
    out = []
    for f in filas:
        fila = {**f, "propuesto": "", "fuente": "", "nota": ""}
        cartera = (f.get("cartera") or "").strip().upper()
        if (v := de_derivado(cartera, f.get("unidad", ""), f.get("ticker", ""))
                or de_futuro(cartera, f.get("unidad", ""), f.get("ticker", ""))
                or de_cartera(cartera)):
            fila["propuesto"], fila["fuente"] = v, REGLA
        elif cartera in _CARTERAS_FCI:
            ficha = indice.get(normalizar_nombre(f.get("ticker") or ""))
            if ficha and (v := de_fci(ficha.get("subyacente", ""),
                                      ficha.get("moneda", ""))):
                fila["propuesto"], fila["fuente"] = v, PRIMARY
        else:
            doc = indice_master.get((f.get("ticker") or "").strip().upper())
            if doc and (v := de_curva(cartera, doc.get("moneda_eje") or "",
                                      doc.get("ajuste") or "",
                                      doc.get("ajuste_alt"))):
                fila["propuesto"], fila["fuente"] = v, CURVA

        # ⚠️ **LA LISTA CERRADA**, tolerante a grafía (`en_lista_cerrada`). Un
        # valor propuesto por regla solo se escribe si YA existe en
        # `clase_activo` —una regla no inventa grafías, mismo invariante que
        # el modelo en `emisor.py`— y se escribe con la grafía que YA está en
        # la base, no con la de la regla.
        if fila["propuesto"]:
            grafia = en_lista_cerrada(fila["propuesto"], usadas or [])
            if grafia:
                fila["propuesto"] = grafia
            else:
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
            if f["propuesto"] and f["fuente"] in (REGLA, PRIMARY, CURVA)]
