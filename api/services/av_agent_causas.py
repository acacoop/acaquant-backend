"""api/services/av_agent_causas.py — TRES AVISOS, UN SOLO PROBLEMA.

Doc madre: **`docs/AV_AGENT.md`** §0.af.

Pedido del user (2026-08-20): *«los jobs, ¿a dónde apuntan? Ej: a Aunesa…
¿Aunesa está caído? Listo, avisar que dio error PORQUE está caído Aunesa.
Adelantarte: no solamente avisar, sino que el aviso sea con más contexto»*.

**El agente ya veía las dos cosas y no las relacionaba.** En la misma pantalla:

    proveedor_caido   Aunesa no responde
    salud_job         portafolio_diario: la última corrida falló   ×80
    salud_job         tenencia (snapshot SQL): la última corrida falló

Son tres avisos y un solo problema. Para atar el cabo hay que saberse de memoria
que `portafolio_diario` le pega a Aunesa — y el que no lo sabe sale a buscar un
bug que no existe. Eso es tiempo perdido en la peor hora.

CÓMO FUNCIONA
=============

`core/dependencias` responde «¿de qué depende esta pieza?» leyendo el código (el
import o la URL — las dos están escritas, no hay lista que mantener). Acá se
cruza con lo que está caído AHORA:

    si el job X depende de Aunesa · y hay un hallazgo de Aunesa caído
    → el hallazgo de X pasa a ser CONSECUENCIA, no causa

**QUÉ CAMBIA UN HALLAZGO QUE ES CONSECUENCIA**, y por qué cada cosa:

  · **el motivo lo dice**: «…porque Aunesa no responde». Es lo único que el
    user pidió y lo que ahorra la búsqueda inútil.
  · **baja de severidad**: no se apaga. Apagarlo sería mentir —el dato falta
    igual— pero dejarlo en ALTA junto a la causa hace que la pantalla muestre
    tres incendios donde hay uno.
  · **la CAUSA sube y dice a cuántos explica**: «y por esto fallaron otros 4».
    Ese número es la medida real del impacto, y es lo que decide si se llama al
    custodio ahora o se espera.

⚠️ **NO SE INVENTA UNA CAUSA.** Se relaciona solo cuando la dependencia está
escrita en el código y el proveedor está caído **en la misma ventana**. Atribuir
de más es peor que no atribuir: un job que falla por su propio bug, marcado como
«culpa de Aunesa», es un bug que nadie va a arreglar nunca.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Qué tipos de hallazgo pueden ser CONSECUENCIA de un proveedor caído. Los de
# bonos no: que a un bono le falte el eje no lo causa Aunesa, y relacionarlos
# sería exactamente la atribución de más que arruina la confianza.
PUEDEN_SER_CONSECUENCIA = ("salud", "motor_caido", "tabla_quieta")

# El tipo que actúa de CAUSA. Hoy uno solo; la estructura admite más (un motor
# caído explica una tabla quieta) y ese es el paso siguiente.
CAUSAS = ("proveedor_caido",)


def correlacionar(hallazgos: list[dict]) -> list[dict]:
    """Marca qué hallazgos son consecuencia de otro. Devuelve la MISMA lista.

    **Nunca levanta**: corre al final del relevamiento y un fallo acá no puede
    borrar los hallazgos que ya se juntaron.
    """
    try:
        return _correlacionar(hallazgos or [])
    except Exception as e:
        logger.warning("av_agent_causas: no pude correlacionar (%s)", e)
        return hallazgos or []


def _correlacionar(hallazgos: list[dict]) -> list[dict]:
    caidos = {(h.get("ticker") or "").strip().lower(): h
              for h in hallazgos if h.get("tipo") in CAUSAS}
    if not caidos:
        return hallazgos

    from core.dependencias import de_quien_depende

    explica: dict[str, list[str]] = {k: [] for k in caidos}
    for h in hallazgos:
        if h.get("tipo") not in PUEDEN_SER_CONSECUENCIA:
            continue
        pieza = str(h.get("ticker") or "")
        for prov in de_quien_depende(pieza):
            if prov not in caidos:
                continue
            _marcar(h, caidos[prov], prov)
            explica[prov].append(pieza)
            break               # con una causa alcanza; dos confunden

    for prov, hijos in explica.items():
        if hijos:
            _sumar_impacto(caidos[prov], hijos)
    return hallazgos


def _marcar(h: dict, causa: dict, prov: str) -> None:
    """El hijo: lo dice en el motivo y baja un escalón."""
    nombre = causa.get("motivo") or f"{prov} no responde"
    h["por_culpa_de"] = prov
    h["motivo"] = f"{h.get('motivo') or ''} — porque {nombre}".strip(" —")
    # Baja, no se apaga: el dato falta igual y alguien tiene que saberlo.
    h["severidad"] = {"alta": "media", "media": "baja"}.get(
        h.get("severidad"), "baja")
    ev = h.setdefault("evidencia", {})
    if isinstance(ev, dict):
        ev["por_culpa_de"] = prov
        ev["texto"] = (
            f"NO ES UN PROBLEMA DE ESTA PIEZA: depende de {prov}, que está "
            f"caído ahora. Se arregla solo cuando el proveedor vuelva; si no "
            f"vuelve solo, el que hay que mirar es el otro aviso.\n\n"
            + str(ev.get("texto") or ""))


def _sumar_impacto(causa: dict, hijos: list[str]) -> None:
    """La causa: cuántos explica. Ese número ES el impacto."""
    ev = causa.setdefault("evidencia", {})
    if isinstance(ev, dict):
        ev["explica"] = hijos
        ev["texto"] = (
            f"IMPACTO MEDIDO: por esto están fallando {len(hijos)} pieza(s) más "
            f"— {', '.join(hijos[:8])}"
            + (f" y {len(hijos) - 8} más" if len(hijos) > 8 else "") + ".\n\n"
            + str(ev.get("texto") or ""))
    causa["motivo"] = (f"{causa.get('motivo') or ''} · y por esto fallaron "
                       f"{len(hijos)} pieza(s) más")
