"""Controles deterministas sobre la respuesta final. Avisan, no bloquean: el
veredicto viaja al lado de la respuesta. No saben nada de las herramientas:
trabajan sobre textos (respuesta, contexto, pregunta, lo DADO, lo MOSTRADO).

Para qué existe: un modelo escribe igual de seguro un número que sacó de una
herramienta y uno que inventó, redondeó o sumó por su cuenta. Esto toma cada
número de la respuesta y pregunta «¿estaba en algo que le dimos?», y cada cita
`[E:ref:campo]` y pregunta «¿existe, y es del sujeto que dice?».

Cada hallazgo trae su explicación (`significa`, `que_hacer`): la pantalla la
muestra, no la inventa. Un inspector que grita por falsas alarmas se deja de
leer; por eso lo DADO (las cuentas habilitadas, la fecha) cuenta como fuente,
y un número que la tabla ya muestra no exige cita."""
from __future__ import annotations

import re

# Un entero chico puede ser una cuenta o un redondeo: no se exige fuente.
ENTEROS_LIBRES = 31
TOLERANCIA_PCT = 0.02
TOLERANCIA_MINIMA = 0.51
MAX_A_MOSTRAR = 8
# Cuántos caracteres antes de una cita tiene que aparecer su sujeto.
VENTANA_SUJETO = 180

_RE_NUMERO = re.compile(r"-?\d[\d.,]*\d|-?\d")
_RE_EVIDENCIA = re.compile(r"\[E:([0-9a-f]{12}):([A-Za-z0-9_.]+)\]")
# Solo espacio HORIZONTAL antes de la cita: `\s*` se comía el salto de línea
# cuando la cita arrancaba un renglón y fusionaba dos ítems en uno.
_RE_CITA_SUELTA = re.compile(r"[ \t]*\[E:[^\]]*\]")

_EXPLICA = {
    "numeros_fundados": (
        "Un número que no estaba en ningún resultado de herramienta, en la pregunta ni en lo que "
        "el sistema le dio. O lo calculó él, o lo inventó.",
        "No lo uses sin verificarlo contra la tabla o la herramienta.",
    ),
    "evidencia_sin_cita": (
        "Dijo números importantes en prosa sin señalar de qué resultado salen.",
        "Los números pueden ser correctos, pero no quedó auditado de dónde salieron: mirá el ciclo.",
    ),
    "evidencia_invalida": (
        "Citó una referencia que no existe o que no tiene ese campo: la cita no respalda nada.",
        "Tratá esa afirmación como no verificada.",
    ),
    "evidencia_sujeto": (
        "La cita existe, pero es de otro sujeto (otra cuenta, otro título) que el que nombra la frase.",
        "Puede haber cruzado datos de dos sujetos: verificá a quién pertenece cada número.",
    ),
}


def _hallazgo(control: str, clave: str, que_paso: str, detalle: list[str]) -> dict:
    significa, que_hacer = _EXPLICA[clave]
    return {"control": control, "que_paso": que_paso, "detalle": detalle[:MAX_A_MOSTRAR],
            "cuantos": len(detalle), "significa": significa, "que_hacer": que_hacer}


def _lecturas(token: str) -> set[float]:
    """Las lecturas posibles de un número escrito (coma o punto decimal)."""
    t = token.strip().rstrip(".,")
    if not t:
        return set()
    out: set[float] = set()
    for quitar, decimal in ((".", ","), (",", ".")):
        limpio = t.replace(quitar, "").replace(decimal, ".")
        try:
            out.add(float(limpio))
        except ValueError:
            pass
    try:
        out.add(float(re.sub(r"[.,]", "", t)))
    except ValueError:
        pass
    return out


def numeros(texto: str) -> list[tuple[str, set[float]]]:
    return [(m.group(0), _lecturas(m.group(0))) for m in _RE_NUMERO.finditer(texto or "")]


def _todos(*textos: str) -> set[float]:
    fuente: set[float] = set()
    for texto in textos:
        for _tok, vals in numeros(texto):
            fuente |= vals
    return fuente


def _es_entero_chico(valores: set[float]) -> bool:
    return all(abs(v) <= ENTEROS_LIBRES and float(v).is_integer() for v in valores)


def _fundado(valores: set[float], fuente: set[float]) -> bool:
    for v in valores:
        if v in fuente:
            return True
        if abs(v) <= ENTEROS_LIBRES and float(v).is_integer():
            return True
        tol = max(abs(v) * TOLERANCIA_PCT, TOLERANCIA_MINIMA)
        if any(abs(v - f) <= tol for f in fuente):
            return True
    return False


def numeros_fundados(respuesta: str, contexto: str, pregunta: str, dado: str = "") -> list[dict]:
    """Todo número de la respuesta tiene que salir de las herramientas, de la
    pregunta o de lo que el sistema le DIO (cuentas habilitadas, fecha), o ser
    un entero chico o un redondeo."""
    fuente = _todos(contexto, pregunta, dado)
    # Sobre la PROSA: una cita `[E:4043f1b4049a:total]` no es un número que el
    # modelo afirme, y sus dígitos se leían como «4043» y «4049» inventados.
    prosa = _RE_EVIDENCIA.sub("", respuesta or "")
    sueltos = [tok for tok, vals in numeros(prosa) if not _fundado(vals, fuente)]
    if not sueltos:
        return []
    return [_hallazgo("numeros_fundados", "numeros_fundados",
                      "hay números en la respuesta que no están en lo que se le dio", sueltos)]


CONTROLES = (numeros_fundados,)


def citas(respuesta: str | None) -> list[dict]:
    """Las citas `[E:ref:campo]` que escribió el modelo, en orden."""
    return [{"ref": m.group(1), "campo": m.group(2)}
            for m in _RE_EVIDENCIA.finditer(respuesta or "")]


def sin_citas(texto: str | None) -> str | None:
    """El texto para la PERSONA: sin las marcas de auditoría. La cita es para el
    control y el ciclo; en la frase que alguien lee es ruido."""
    if not texto or not _RE_EVIDENCIA.search(texto):
        return texto
    limpio = _RE_CITA_SUELTA.sub("", texto)
    limpio = re.sub(r"[ \t]+([.,;:)])", r"\1", limpio)
    limpio = re.sub(r"[ \t]{2,}", " ", limpio)
    return "\n".join(linea.strip() for linea in limpio.splitlines()).strip() or None


def evidencia_fundada(respuesta: str, evidencias: list[dict], mostrado: str = "") -> list[dict]:
    """Las citas tienen que existir y corresponder al sujeto y campo nombrados.
    Un número que la pantalla ya MUESTRA en una tabla no exige cita: la persona
    tiene la fuente a la vista."""
    if not evidencias:
        return []
    por_ref = {e.get("ref"): e for e in evidencias if isinstance(e, dict) and e.get("ref")}
    lista = list(_RE_EVIDENCIA.finditer(respuesta or ""))
    hallazgos: list[dict] = []
    prosa = _RE_EVIDENCIA.sub("", respuesta or "")
    a_la_vista = _todos(mostrado)
    importantes = [tok for tok, vals in numeros(prosa)
                   if not _es_entero_chico(vals) and not _fundado(vals, a_la_vista)]
    if importantes and not lista:
        return [_hallazgo("evidencia_fundada", "evidencia_sin_cita",
                          "la respuesta usa números importantes sin citar su evidencia", importantes)]
    invalidas: list[str] = []
    atribuciones: list[str] = []
    for cita in lista:
        ref, campo = cita.groups()
        evidencia = por_ref.get(ref)
        if evidencia is None or campo not in (evidencia.get("campos") or {}):
            invalidas.append(cita.group(0))
            continue
        sujeto = str(evidencia.get("sujeto") or "general")
        ventana = respuesta[max(0, cita.start() - VENTANA_SUJETO):cita.start()].casefold()
        if sujeto != "general" and sujeto.casefold() not in ventana:
            atribuciones.append(f"{cita.group(0)} es de {sujeto}")
    if invalidas:
        hallazgos.append(_hallazgo("evidencia_fundada", "evidencia_invalida",
                                   "hay referencias que no existen o no tienen ese campo", invalidas))
    if atribuciones:
        hallazgos.append(_hallazgo("evidencia_fundada", "evidencia_sujeto",
                                   "la evidencia no coincide con el sujeto atribuido", atribuciones))
    return hallazgos


def _fallo(nombre: str, e: Exception) -> dict:
    return {"control": nombre, "que_paso": f"el control falló: {type(e).__name__}: {e}",
            "detalle": [], "cuantos": 0, "significa": "El control no pudo correr; la respuesta "
            "no fue revisada.", "que_hacer": "Verificá los números a mano."}


def revisar(respuesta: str | None, *, contexto: str, pregunta: str,
            evidencias: list[dict] | None = None, dado: str = "", mostrado: str = "") -> dict:
    """Pasa la respuesta por todos los controles. Nunca levanta.

    `dado`: lo que el sistema le entregó al modelo aparte de los resultados
    (cuentas habilitadas, fecha). `mostrado`: lo que la pantalla ya dibuja en
    tablas. Los dos son texto: acá no se sabe de dónde salen."""
    if not respuesta:
        return {"ok": True, "hallazgos": [], "citas": []}
    hallazgos: list[dict] = []
    for control in CONTROLES:
        try:
            hallazgos += control(respuesta, contexto, pregunta, dado)
        except Exception as e:
            hallazgos.append(_fallo(getattr(control, "__name__", "?"), e))
    try:
        hallazgos += evidencia_fundada(respuesta, evidencias or [], mostrado)
    except Exception as e:
        hallazgos.append(_fallo("evidencia_fundada", e))
    return {"ok": not hallazgos, "hallazgos": hallazgos, "citas": citas(respuesta)}
