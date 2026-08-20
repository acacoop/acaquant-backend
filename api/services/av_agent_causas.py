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
    proveedor ahora o se espera.

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
PUEDEN_SER_CONSECUENCIA = ("salud", "motor_caido", "tabla_quieta",
                           "motor_ruidoso")

# El tipo que actúa de CAUSA.
CAUSAS = ("proveedor_caido",)

# El título entra en un renglón (mismo tope que `tests/unit/test_avisos_cortos`).
TOPE_MOTIVO = 88


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
    # ⚠️ Va PRIMERO y fuera del `if`: juntar el motor con su log no depende de
    # que haya un proveedor caído, y el corte de abajo se lo salteaba justo en el
    # caso normal — que es el 99% de los días.
    _juntar_el_motor_con_su_log(hallazgos)

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


# ── EL MOTOR CAÍDO Y SU LOG SON EL MISMO PROBLEMA ────────────────────────────
#
# En la corrida del 2026-08-20 `motor_cedears` salió DOS veces: una desde el
# árbol (`motor_caido · sin_datos`) y otra desde los logs (`motor_ruidoso ·
# ráfaga`). Son dos detectores mirando la misma pieza, y el que lee la lista
# cuenta dos motores rotos donde hay uno.
#
# Y lo que se pierde partiéndolo es justo lo que sirve: **el árbol dice QUE está
# roto, el log dice POR QUÉ**. Juntos son un aviso accionable; separados, uno es
# una queja y el otro un dato suelto.


def _pieza(nombre: str) -> str:
    """`motor_rofex (trades)` → `motor_rofex`. El árbol rotula la pieza, los logs
    nombran la unidad de systemd: para cruzarlos hay que sacar el paréntesis."""
    return (nombre or "").split("(")[0].strip().lower()


def _juntar_el_motor_con_su_log(hallazgos: list[dict]) -> None:
    caidos: dict[str, dict] = {}
    for h in hallazgos:
        if h.get("tipo") == "motor_caido":
            caidos.setdefault(_pieza(str(h.get("ticker") or "")), h)
    if not caidos:
        return

    for h in hallazgos:
        if h.get("tipo") != "motor_ruidoso":
            continue
        padre = caidos.get(_pieza(str(h.get("ticker") or "")))
        if padre is None or padre is h:
            continue
        _explica_al_caido(padre, h)
        _pasa_a_segundo_plano(h, padre)


def _explica_al_caido(padre: dict, log: dict) -> None:
    """El aviso que se ve suma el POR QUÉ, si entra en el renglón."""
    pat = " ".join(str((log.get("evidencia") or {}).get("patron") or "").split())
    if not pat:
        return
    motivo = str(padre.get("motivo") or "")
    hueco = TOPE_MOTIVO - len(motivo) - len(" · log: ")
    if hueco >= 12:
        corto = pat if len(pat) <= hueco else pat[:hueco - 1].rstrip() + "…"
        padre["motivo"] = f"{motivo} · log: {corto}"
    ev = padre.setdefault("evidencia", {})
    if isinstance(ev, dict):
        ev["log"] = pat
        ev["texto"] = (f"El log dice: {pat}\n" + str(ev.get("texto") or ""))


def _pasa_a_segundo_plano(h: dict, padre: dict) -> None:
    """El del log no se borra —es la prueba— pero deja de contar como otro roto."""
    h["mismo_problema_que"] = padre.get("ticker")
    h["severidad"] = {"alta": "media", "media": "baja"}.get(h.get("severidad"), "baja")
    ev = h.setdefault("evidencia", {})
    if isinstance(ev, dict):
        ev["mismo_problema_que"] = padre.get("ticker")
        ev["texto"] = (f"Es el log de «{padre.get('ticker')}», que ya está "
                       f"avisado como caído.\n" + str(ev.get("texto") or ""))


def _marcar(h: dict, causa: dict, prov: str) -> None:
    """El hijo: lo dice en el motivo y baja un escalón."""
    h["por_culpa_de"] = prov
    # CORTO: «— porque AUNESA está caído» y listo. El detalle de la caída ya
    # está en su propio aviso; repetirlo acá es el texto que el user no quiere.
    h["motivo"] = f"{h.get('motivo') or ''} — porque {prov.upper()} está caído".strip(" —")
    # Baja, no se apaga: el dato falta igual y alguien tiene que saberlo.
    h["severidad"] = {"alta": "media", "media": "baja"}.get(
        h.get("severidad"), "baja")
    ev = h.setdefault("evidencia", {})
    if isinstance(ev, dict):
        ev["por_culpa_de"] = prov
        ev["texto"] = (f"No es de esta pieza: depende de {prov.upper()}. "
                       f"Vuelve solo cuando vuelva {prov.upper()}.\n"
                       + str(ev.get("texto") or ""))


def _sumar_impacto(causa: dict, hijos: list[str]) -> None:
    """La causa: cuántos explica. Ese número ES el impacto."""
    ev = causa.setdefault("evidencia", {})
    if isinstance(ev, dict):
        ev["explica"] = hijos
        ev["texto"] = (f"Arrastra {len(hijos)}: {', '.join(hijos[:6])}"
                       + (f" +{len(hijos) - 6}" if len(hijos) > 6 else "") + "\n"
                       + str(ev.get("texto") or ""))
    causa["motivo"] = f"{causa.get('motivo') or ''} · arrastra {len(hijos)}"
