"""api/services/av_agent_pata.py — IR A BUSCAR LA PATA EN DÓLARES, Y AGREGARLA.

Doc madre: **`docs/AV_AGENT.md`** §0.x.

Pedido del user (2026-08-19), mirando los 43 hallazgos `cotiza_en_pesos`:
*«los de cotiza en pesos… no ofrece una solución o algo, nada. Le falta ahí una
feature que sepa ir a buscar y agregar»*.

**Y el hallazgo no solo no ofrecía salida: afirmaba de más.** Cerraba con «No
encontré una pata en dólares para este ticker» después de mirar UNA tabla
(`mercado.especies`), que se siembra a mano y a la que `jobs.validar_instrumentos`
le borra filas. Es la misma forma del error del AO29 (§0.v): *el agente no puede
concluir «no existe» desde una tabla que no es la fuente*.

LAS TRES PREGUNTAS, Y EN ESTE ORDEN
===================================

    1. ¿la tenemos sembrada?   → `mercado.especies`
    2. ¿existe en el mercado?  → `manager.pyrofex_instruments` (el catálogo de
                                 Primary: la única fuente que NO depende de
                                 ninguna decisión nuestra)
    3. ¿la estamos pidiendo?   → `mercado.adhoc_subscriptions` + `market_snapshot`

Recién con las tres contestadas «no hay pata en dólares» es una afirmación que se
puede sostener. Y si el catálogo de Primary no se pudo leer, el diagnóstico lo
**dice** en vez de contestar que no existe: *el silencio no es un verde* (§0.s).

QUÉ ARREGLA Y QUÉ NO — LA PARTE QUE NO SE PUEDE SALTEAR
=======================================================

Pedir la pata **no cambia lo que muestra la grilla**. La grilla dibuja
`mercado.curvas.instrumento`, y eso es la acción hermana —cambiar el símbolo del
master— que sigue **sin automatizarse** porque el motor arma su universo al
arrancar: se aplicaría, se verificaría en verde, y en pantalla no cambiaría nada
hasta la noche (§0.v).

Lo que esta puerta sí consigue, que es el paso que faltaba:

    hoy      no sabemos si esa pata cotiza — nadie la escucha
    después  la escuchamos, y en 5s sabemos si tiene precio y cuál es

O sea que convierte una ausencia sin significado en un dato. Y ese dato es
justamente el que decide si vale la pena el reinicio: apuntar el master a una
pata que tampoco opera sería cambiar un problema por otro.

**Cero red y cero créditos de 1816**: todo sale de Postgres.
"""
from __future__ import annotations

import logging

from api.services.av_agent_alta import INFO, NO_SE, OK, REVISAR
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# El vocabulario de estados de un paso sale de `av_agent_alta` y no se reescribe:
# la pantalla los pinta por string exacto, así que un «no_se» propio se vería
# gris sin dar ningún error — el modo de falla que este proyecto ya conoce.

# Los dos sufijos de especie en dólares, en orden de preferencia: MEP antes que
# cable, que es el mismo criterio de `core/especies.preferencia` (MEP es la que
# mira la mesa). No se reimplementa la clasificación — se reusa el sufijo.
_SUFIJOS_USD = ("D", "C")


def _corto(simbolo: str) -> str:
    """`MERV - XMEV - AO29D - 24hs` → `AO29D`."""
    partes = (simbolo or "").split(" - ")
    return partes[2].strip().upper() if len(partes) >= 3 else (simbolo or "").strip()


def _elegir(candidatos: list[str]) -> str:
    """Entre varias patas en dólares, la que la mesa mira: MEP sobre cable y
    24hs sobre CI — ahí está la liquidez, y por lo tanto el precio."""
    def orden(s: str) -> tuple:
        c = _corto(s)
        return (0 if c.endswith("D") else 1, 0 if s.rstrip().endswith("24hs") else 1, s)
    return sorted(candidatos, key=orden)[0] if candidatos else ""


def explicar(ticker: str) -> dict:
    """SOLO LECTURA: dónde está la pata en dólares de este bono, si es que está.

    Devuelve `pasos` con la misma forma que el resto de las puertas del agente
    para que la pantalla no tenga que aprender un formato nuevo, y `pedible` con
    el símbolo exacto cuando hay algo que hacer.
    """
    tk = (ticker or "").strip().upper()
    if not tk:
        return {"ok": False, "error": "sin ticker"}

    out: dict = {"ok": True, "ticker": tk, "pasos": [], "pedible": "",
                 "sembrar": False}
    pasos: list[dict] = out["pasos"]

    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT instrumento, moneda_eje, curva FROM mercado.curvas "
                        "WHERE upper(ticker) = %s", (tk,))
            r = cur.fetchone()
            if not r:
                return {"ok": False, "error": f"«{tk}» no está en mercado.curvas"}
            simbolo = (r[0] or "").strip()
            out["simbolo_master"] = simbolo
            out["moneda_eje"] = r[1]
            out["curva"] = r[2]
            base = _corto(simbolo)
            pasos.append({
                "clave": "master", "estado": INFO,
                "titulo": "Lo que el master pide hoy",
                "detalle": (f"`mercado.curvas.instrumento` = «{base or '—'}»"
                            f" · curva {r[2] or '—'} en {r[1] or '—'}. Es el símbolo "
                            f"que dibuja la grilla."),
            })

            # 1 · ¿la tenemos sembrada?
            cur.execute("SELECT simbolo FROM mercado.especies "
                        "WHERE upper(ticker) = %s AND upper(moneda) = 'USD'", (tk,))
            sembradas = [x for (x,) in cur.fetchall() if x]
            out["sembradas"] = sembradas

            # 3 · ¿la estamos pidiendo, y llegó algo? (una query, con las dos)
            candidatas = list(sembradas)
    except Exception as e:
        logger.exception("av_agent_pata: no se pudo leer la base: %s", e)
        return {"ok": False, "error": f"no se pudo leer la base: {type(e).__name__}"}

    # 2 · ¿existe en el catálogo de Primary? **La fuente que no depende de
    # nosotros.** Se consulta siempre, no solo cuando falta: si `especies` tiene
    # una y Primary tiene dos, decirlo es información.
    from api.services import av_agent
    primary = av_agent.simbolos_primary()
    if primary is None:
        pasos.append({
            "clave": "primary", "estado": NO_SE,
            "titulo": "No pude leer el catálogo de Primary",
            "detalle": ("`manager.pyrofex_instruments` no contestó o tiene menos "
                        "símbolos de los creíbles. **No sé** si existe una pata en "
                        "dólares — que no es lo mismo que decir que no existe. "
                        "Refrescarlo: `python -m scripts.discovery_pyrofex`."),
        })
        en_primary: list[str] = []
    else:
        en_primary = [x for suf in _SUFIJOS_USD for x in primary
                      if f" - {base}{suf} - " in x] if base else []
        candidatas = list({*candidatas, *en_primary})

    nuevas = [x for x in en_primary if x not in set(sembradas)]
    if sembradas:
        pasos.append({
            "clave": "especies", "estado": OK,
            "titulo": f"Sembrada: {len(sembradas)} pata(s) en dólares",
            "detalle": ", ".join(sorted(_corto(x) for x in sembradas)),
        })
    else:
        pasos.append({
            "clave": "especies", "estado": REVISAR,
            "titulo": "`mercado.especies` no tiene ninguna pata en dólares",
            "detalle": ("Por sí solo esto NO prueba que no exista: esa tabla se "
                        "siembra con `scripts.sembrar_especies` y "
                        "`jobs.validar_instrumentos` le borra lo que Primary no "
                        "lista. Por eso se pregunta también al catálogo."),
        })

    if nuevas:
        pasos.append({
            "clave": "primary", "estado": REVISAR,
            "titulo": "Primary SÍ la lista, y no la teníamos",
            "detalle": ("«" + ", ".join(sorted(_corto(x) for x in nuevas)) + "» está "
                        "en el catálogo de Primary y falta en `mercado.especies`. "
                        "Sembrarla y pedirla no reinicia nada."),
        })
    elif primary is not None and not candidatas:
        pasos.append({
            "clave": "primary", "estado": INFO,
            "titulo": "Primary tampoco lista una pata en dólares",
            "detalle": (f"Ni sembrada ni en el catálogo. «{base}» cotiza en pesos y "
                        f"no hay otra pata a la que apuntar: esto es el "
                        f"instrumento, no un dato mal cargado. La valuación sigue "
                        f"bien —el motor divide por el MEP—."),
        })

    elegida = _elegir(candidatas)
    if elegida:
        try:
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1 FROM mercado.adhoc_subscriptions "
                            "WHERE ticker = %s AND expires_at > now()", (elegida,))
                pedida = cur.fetchone() is not None
                cur.execute("SELECT last_price, updated_at FROM mercado.market_snapshot "
                            "WHERE ticker = %s", (elegida,))
                snap = cur.fetchone()
        except Exception as e:
            logger.warning("av_agent_pata: sin estado de suscripción (%s)", e)
            pedida, snap = False, None
        px = snap[0] if snap else None
        # ⚠️ **ESTAR EN EL SNAPSHOT NO ES LO MISMO QUE TENER PRECIO**, y confundirlos
        # es exactamente el error del AO29 (§0.v). Hay TRES estados y no dos:
        #
        #   no está en el snapshot   nadie la suscribe → la ausencia NO prueba nada
        #   está, sin precio         la escuchamos y el mercado no dio punta → iliquidez
        #   está, con precio         cotiza, y sabemos a cuánto
        #
        # El motor la puede estar suscribiendo desde el master o desde el universo
        # de portfolio sin que haya ningún adhoc, así que `pedida` sola no alcanza
        # para saber si estamos escuchando.
        out["en_snapshot"] = snap is not None
        out["pedida"] = pedida
        out["escuchando"] = bool(pedida or snap is not None)
        out["precio"] = float(px) if px is not None else None
        if px:
            pasos.append({
                "clave": "precio", "estado": OK,
                "titulo": f"«{_corto(elegida)}» ya tiene precio: {float(px):,.2f}",
                "detalle": (f"Última actualización {snap[1]}. La pata opera — lo que "
                            f"falta es que el master la use, y eso "
                            f"**requiere reiniciar `motor_rofex` fuera de rueda**."),
            })
        else:
            escuchando = out["escuchando"]
            pasos.append({
                "clave": "precio", "estado": REVISAR if escuchando else NO_SE,
                "titulo": (f"La escuchamos y no dio punta: «{_corto(elegida)}»"
                           if escuchando
                           else f"Nadie está escuchando «{_corto(elegida)}»"),
                "detalle": (("Está en el snapshot, así que el motor la pide y el "
                             "mercado no le puso precio: eso ES iliquidez. Si pasa "
                             "una rueda entera igual, la pata no cotiza — y recién "
                             "ahora se puede afirmar.") if escuchando else
                            "Por eso no tiene precio en nuestras tablas: **no es que "
                            "no cotice, es que no la estamos escuchando**. Pedirla "
                            "la levanta el `adhoc_watcher` en 5s, sin reiniciar."),
            })
        # Solo es «pedible» si hay algo que la pedida cambie. Que ya la estemos
        # escuchando cuenta: pedir de nuevo algo que el motor ya suscribe no
        # agrega un dato, y ofrecer un botón que no cambia nada es lo que rompe
        # la confianza en todos los demás.
        out["pedible"] = "" if out["escuchando"] else elegida
        out["sembrar"] = elegida not in set(sembradas)

    out["veredicto"] = (
        "sin_pata" if not elegida and primary is not None else
        "no_pude_mirar" if not elegida else
        "con_precio" if out.get("precio") else
        # «La escuchamos y no vino» — que es iliquidez y NO trabajo nuestro. Se
        # llama distinto de `hay_que_pedirla` porque se atienden distinto: una
        # tiene botón y la otra no tiene nada que hacer.
        "escuchada_sin_punta" if out.get("escuchando") else "hay_que_pedirla")
    return out


def pedir(ticker: str, por: str = "") -> dict:
    """SIEMBRA (si hace falta) y PIDE la pata en dólares. Se ve en 5 segundos.

    Las dos escrituras van por las puertas que ya existen —`core.especies` y
    `core.adhoc_subscriptions`— y no por SQL propio: un segundo camino a la misma
    escritura termina siempre con dos criterios que se contradicen, que es
    exactamente el bug del blob y la columna (§0.u).

    **No toca `mercado.curvas`.** Cambiar el símbolo del master es la acción
    hermana, sigue siendo manual, y se decide DESPUÉS —con el precio a la vista—.
    """
    d = explicar(ticker)
    if not d.get("ok"):
        return d
    simbolo = d.get("pedible") or ""
    if not simbolo:
        return {"ok": False, "ticker": d["ticker"], "veredicto": d.get("veredicto"),
                "error": ("no hay ninguna pata en dólares para pedir"
                          if d.get("veredicto") == "sin_pata"
                          else "ya la escuchamos y tiene precio: no hay nada que hacer"
                          if d.get("veredicto") == "con_precio"
                          else "ya la estamos escuchando y el mercado no le pone "
                               "punta: eso es iliquidez, no algo que pedir de nuevo"
                          if d.get("veredicto") == "escuchada_sin_punta"
                          else "no pude leer el catálogo de Primary, así que no "
                               "propongo nada")}

    hecho: list[str] = []
    # 1 · sembrar la especie, si la encontramos en Primary y no la teníamos.
    if d.get("sembrar"):
        from core import especies
        r = especies.sembrar_ticker(d["ticker"], moneda_bono=d.get("moneda_eje") or "")
        if not r.get("ok"):
            return {"ok": False, "ticker": d["ticker"],
                    "error": f"no se pudo sembrar la especie: {r.get('error')}"}
        hecho.append(f"sembradas {len(r.get('patas') or [])} pata(s) en "
                     f"`mercado.especies`")

    # 2 · pedirla. Aditivo: no rompe ninguna suscripción y no reinicia nada.
    from core import adhoc_subscriptions
    s = adhoc_subscriptions.subscribe(simbolo)
    if not s.get("ok"):
        return {"ok": False, "ticker": d["ticker"], "hecho": hecho,
                "error": f"no se pudo pedir «{_corto(simbolo)}»: {s.get('reason')}"}
    hecho.append(f"pedida «{_corto(simbolo)}» (el motor la levanta en 5s)")

    # 3 · verificar releyendo, que es lo único que convierte «apliqué» en «pasó».
    # Se exige que quede PEDIDA —lo que la acción controla—; que el precio llegue
    # lo decide el mercado, y el detalle lo dice sin disfrazarlo de éxito.
    v = explicar(ticker)
    return {"ok": True, "ticker": d["ticker"], "simbolo": simbolo,
            "corto": _corto(simbolo), "hecho": hecho, "por": por or None,
            "verificado": bool(v.get("pedida")),
            "precio": v.get("precio"),
            "detalle": (f"llegó precio: {v['precio']:,.2f}" if v.get("precio")
                        else "pedida; todavía sin precio — si pasa una rueda entera "
                             "y no llega, ESA pata no cotiza (antes no se podía "
                             "afirmar)"),
            "pasos": v.get("pasos") or []}
