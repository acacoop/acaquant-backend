"""Controles deterministas sobre la respuesta final. Avisan, no bloquean: el
veredicto viaja al lado de la respuesta. No saben nada de las herramientas:
trabajan sobre tres textos (respuesta, contexto, pregunta)."""
from __future__ import annotations

import re

# Un entero chico puede ser una cuenta o un redondeo: no se exige fuente.
ENTEROS_LIBRES = 31
TOLERANCIA_PCT = 0.02
TOLERANCIA_MINIMA = 0.51
MAX_A_MOSTRAR = 8

_RE_NUMERO = re.compile(r"-?\d[\d.,]*\d|-?\d")
_RE_EVIDENCIA = re.compile(r"\[E:([0-9a-f]{12}):([A-Za-z0-9_.]+)\]")


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


def numeros_fundados(respuesta: str, contexto: str, pregunta: str) -> list[dict]:
    """Todo número de la respuesta tiene que salir de las herramientas, de la
    pregunta, o ser un entero chico o un redondeo."""
    fuente: set[float] = set()
    for _tok, vals in numeros(contexto) + numeros(pregunta):
        fuente |= vals
    sueltos = [tok for tok, vals in numeros(respuesta) if not _fundado(vals, fuente)]
    if not sueltos:
        return []
    return [{
        "control": "numeros_fundados",
        "que_paso": "hay números en la respuesta que no están en lo que se le dio",
        "detalle": sueltos[:MAX_A_MOSTRAR],
        "cuantos": len(sueltos),
    }]


CONTROLES = (numeros_fundados,)


def evidencia_fundada(respuesta: str, evidencias: list[dict]) -> list[dict]:
    """Las citas tienen que existir y corresponder al sujeto y campo nombrados."""
    if not evidencias:
        return []
    por_ref = {e.get("ref"): e for e in evidencias if isinstance(e, dict) and e.get("ref")}
    citas = list(_RE_EVIDENCIA.finditer(respuesta or ""))
    hallazgos: list[dict] = []
    sin_citas = _RE_EVIDENCIA.sub("", respuesta or "")
    numeros_importantes = [token for token, valores in numeros(sin_citas)
                           if not all(abs(v) <= ENTEROS_LIBRES and float(v).is_integer()
                                      for v in valores)]
    if numeros_importantes and not citas:
        hallazgos.append({
            "control": "evidencia_fundada",
            "que_paso": "la respuesta usa números importantes sin citar su evidencia",
            "detalle": numeros_importantes[:MAX_A_MOSTRAR],
            "cuantos": len(numeros_importantes),
        })
        return hallazgos
    invalidas: list[str] = []
    atribuciones: list[str] = []
    for cita in citas:
        ref, campo = cita.groups()
        evidencia = por_ref.get(ref)
        if evidencia is None or campo not in (evidencia.get("campos") or {}):
            invalidas.append(cita.group(0))
            continue
        sujeto = str(evidencia.get("sujeto") or "general")
        contexto_cita = respuesta[max(0, cita.start() - 180):cita.start()].casefold()
        if sujeto != "general" and sujeto.casefold() not in contexto_cita:
            atribuciones.append(f"{cita.group(0)} esperaba sujeto {sujeto}")
    if invalidas:
        hallazgos.append({
            "control": "evidencia_fundada", "que_paso": "hay referencias que no existen o no tienen ese campo",
            "detalle": invalidas[:MAX_A_MOSTRAR], "cuantos": len(invalidas),
        })
    if atribuciones:
        hallazgos.append({
            "control": "evidencia_fundada", "que_paso": "la evidencia no coincide con el sujeto atribuido",
            "detalle": atribuciones[:MAX_A_MOSTRAR], "cuantos": len(atribuciones),
        })
    return hallazgos


def revisar(respuesta: str | None, *, contexto: str, pregunta: str,
            evidencias: list[dict] | None = None) -> dict:
    """Pasa la respuesta por todos los controles. Nunca levanta."""
    if not respuesta:
        return {"ok": True, "hallazgos": []}
    hallazgos: list[dict] = []
    for control in CONTROLES:
        try:
            hallazgos += control(respuesta, contexto, pregunta)
        except Exception as e:
            hallazgos.append({
                "control": getattr(control, "__name__", "?"),
                "que_paso": f"el control falló: {type(e).__name__}: {e}",
                "detalle": [], "cuantos": 0,
            })
    try:
        hallazgos += evidencia_fundada(respuesta, evidencias or [])
    except Exception as e:
        hallazgos.append({
            "control": "evidencia_fundada",
            "que_paso": f"el control falló: {type(e).__name__}: {e}",
            "detalle": [], "cuantos": 0,
        })
    return {"ok": not hallazgos, "hallazgos": hallazgos}
