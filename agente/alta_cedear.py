"""`agente/alta_cedear.py` — dar de alta un CEDEAR ENTERO, verificado contra Primary.

Doc: `docs/AGENT.md` §0.dl. Gemelo chico de `agente/alta.py` (el alta de bonos).

**Qué resuelve.** Sumar un CEDEAR era un script a mano (`scripts/add_cedear`) que
escribía una fila y terminaba con «⚠️ reiniciá el motor». Nadie verificaba que
el símbolo EXISTIERA en Primary, nadie traía la historia del subyacente, y la
fila quedaba sin precio hasta el próximo arranque del motor sin que nada lo
dijera. Acá la cadena es UNA y se recorre entera, con la pregunta de cada
eslabón contestada ANTES de escribir:

    1. ¿Primary lista el símbolo?          → `alta.estado_simbolo` (foto + EN VIVO)
    2. ¿Tiene la FICHA de un CEDEAR?       → el `cficode` de los que YA tenemos
    3. ¿Ya está en el master?              → `mercado.cedears`
    4. ¿Quién le trae el subyacente?       → Yahoo (EOD) + Finnhub (ADR live)
    5. ¿El motor lo va a suscribir?        → el latido de `motor_cedears`

**La identidad es la FICHA, no el nombre (REGLA #9).** «Es un CEDEAR» no se
deduce de que el símbolo tenga la forma `MERV - XMEV - X - 24hs` (GGAL también
la tiene): se deduce de que Primary le ponga el MISMO `cficode` que a los
CEDEARs que ya tenemos cargados. Ese `cficode` **no está escrito en ningún
lado**: se CALIBRA leyendo la ficha de nuestros propios símbolos, así el día
que Primary cambie la codificación el detector se recalibra solo en vez de
quedarse callado comparando contra una constante vieja.

**Lo que NO hace, a propósito.** No decide el `rubro`, `es_ia`, `ric` ni el
`ratio`: eso lo carga la mesa en Manager → TÍTULOS → RENTA VARIABLE, igual que
siempre. Y no elige QUÉ CEDEARs sumar: Primary lista muchos más de los que la
mesa mira, así que el detector ofrece la lista y **una persona tilda**.
"""
from __future__ import annotations

import logging
from collections import Counter

from agente import libro

logger = logging.getLogger(__name__)

# El sujeto del hallazgo de faltantes. Es UNO por familia y no uno por CEDEAR:
# 300 CEDEARs que la mesa no pidió no son 300 problemas (§0.dl).
FAMILIA = "CEDEAR"
# Cuántos de NUESTROS CEDEARs tienen que compartir una ficha (cficode, plazo,
# moneda) para creerle. Uno solo puede ser una fila mal cargada.
MIN_PROPIOS = 3
# Cada cuánto relee el motor el master (`engines/motor_cedears`). Se nombra acá
# para que el veredicto diga «en ≤ N s» con el número real y no con uno inventado.
RELECTURA_MOTOR_S = 60
MUESTRA = 12


def _partes(simbolo: str) -> list[str]:
    return [p.strip() for p in (simbolo or "").split(" - ")]


def corto(simbolo: str) -> str:
    """`MERV - XMEV - NVDA - 24hs` → `NVDA`. Gramática del símbolo, no identidad."""
    p = _partes(simbolo)
    return (p[2] if len(p) >= 3 else simbolo or "").strip().upper()


def plazo(simbolo: str) -> str:
    p = _partes(simbolo)
    return p[3] if len(p) >= 4 else ""


# ── CANDIDATOS: lo que Primary lista con la ficha de un CEDEAR y no tenemos ───
def candidatos(master: list[dict], fichas: list[dict], *,
               min_propios: int = MIN_PROPIOS) -> dict:
    """PURA. Cruza el master contra las fichas de la foto de Primary.

    Devuelve:
      `filas`        los que faltan, con su ficha (para que una persona elija)
      `cficodes` / `plazos` / `monedas`   la ficha CALIBRADA con los nuestros
      `propios` / `reconocidos`           cuántos tenemos y cuántos lista Primary
      `no_cotizan`   los ACTIVOS del master que Primary NO lista (nunca van a
                     tener precio: el WS los filtra por la misma foto)

    Sin `cficodes` no hay candidatos: el que llama decide qué significa (el
    detector levanta `SinDatos`, porque «no reconocí la ficha» no es «no falta
    nada»).
    """
    con_pata = [m for m in master if m.get("ticker") and not m.get("sin_cedear")]
    propios = {m["ticker"] for m in con_pata}
    activos = {m["ticker"] for m in con_pata if m.get("activo")}
    por_simbolo = {f["simbolo"]: f for f in fichas if f.get("simbolo")}

    c_cfi: Counter = Counter()
    c_plazo: Counter = Counter()
    c_moneda: Counter = Counter()
    for s in propios:
        f = por_simbolo.get(s)
        if f is None:
            continue
        c_cfi[f.get("cficode") or ""] += 1
        c_plazo[plazo(s)] += 1
        c_moneda[f.get("moneda") or ""] += 1
    cficodes = {k for k, n in c_cfi.items() if k and n >= min_propios}
    plazos = {k for k, n in c_plazo.items() if k and n >= min_propios}
    monedas = {k for k, n in c_moneda.items() if k and n >= min_propios}

    filas = []
    if cficodes:
        for f in fichas:
            s = f.get("simbolo") or ""
            if f.get("cficode") not in cficodes or s in propios:
                continue
            if plazos and plazo(s) not in plazos:
                continue
            if monedas and (f.get("moneda") or "") not in monedas:
                continue
            filas.append({
                "unidad": corto(s), "simbolo": s, "cficode": f.get("cficode"),
                "moneda": f.get("moneda") or "",
                # Lo que Primary dice que es el subyacente. Se MUESTRA y no se
                # usa de default: no está medido qué pone ahí para un CEDEAR
                # (REGLA #2). El default del subyacente es el ticker corto, la
                # misma convención que tenía el script.
                "subyacente_primary": f.get("subyacente") or "",
                "segmento": f.get("segmento") or "",
            })
    filas.sort(key=lambda r: r["unidad"])
    return {
        "filas": filas,
        "cficodes": sorted(cficodes), "plazos": sorted(plazos),
        "monedas": sorted(monedas),
        "propios": len(propios), "reconocidos": sum(c_cfi.values()),
        "no_cotizan": sorted(s for s in activos if s not in por_simbolo),
    }


def listado() -> dict:
    """Los candidatos AHORA, leídos directo (no por `fuentes`: esto corre en el
    proceso de la API, donde la caché de una pasada no se refresca sola).
    Es lo que despliega `ver qué haría` y lo que `aplicar` vuelve a mirar."""
    from core import cedears_sql, instrumentos_validos
    master = cedears_sql.cargar_master()
    fichas = instrumentos_validos.fichas()
    if fichas is None:
        return {"ok": False,
                "error": "no pude leer la foto de Primary con sus fichas — sin eso "
                         "no se puede afirmar qué es un CEDEAR"}
    c = candidatos(master, fichas)
    if not c["cficodes"]:
        return {"ok": False,
                "error": (f"no reconozco la ficha de un CEDEAR en la foto: de "
                          f"{c['propios']} propios, Primary lista {c['reconocidos']} "
                          f"y ningún cficode llega a {MIN_PROPIOS}")}
    return {"ok": True, "master": master, "fichas": fichas, **c}


# ── LA CADENA de UN alta ────────────────────────────────────────────────────
def _motor() -> dict:
    """¿`motor_cedears` está vivo, y corre la versión que RELEE el master?

    Sin esto el alta escribía la fila y decía «reiniciá el motor» a mano. Con
    el relector (§0.dl) el motor suma lo nuevo solo, y acá se dice cuál de los
    tres casos es: lo toma en ≤ N s · corre código viejo · está apagado.
    """
    from agente import fuentes
    lat = fuentes.latidos()
    if lat is None:
        return {"estado": "no_se_puede_saber",
                "detalle": "no pude leer los latidos: no sé si el motor está corriendo"}
    m = next((v for k, v in lat.items() if "motor_cedears" in (k or "")), None)
    if m is None:
        return {"estado": "info",
                "detalle": "motor_cedears no late: lo toma al próximo arranque "
                           "(cron 13:20 UTC L-V)"}
    data = m.get("data") or {}
    hace = None
    try:
        from agente import reloj
        hace = int((reloj.ahora_utc() - m["latido_at"]).total_seconds())
    except Exception:
        pass
    if hace is not None and hace > 180:
        return {"estado": "info", "hace_s": hace,
                "detalle": f"motor_cedears no late hace {hace}s: lo toma al "
                           "próximo arranque (cron 13:20 UTC L-V)"}
    if data.get("relee_master"):
        return {"estado": "ok", "hace_s": hace,
                "detalle": f"motor_cedears vivo y releyendo el master: lo suscribe "
                           f"en ≤ {RELECTURA_MOTOR_S} s, sin reiniciar"}
    return {"estado": "revisar", "hace_s": hace,
            "detalle": "motor_cedears vivo pero corre código SIN relector: lo toma "
                       "al próximo arranque, o `systemctl try-restart "
                       "motor_cedears.service` fuera de rueda"}


def simular(ticker_corto: str, underlying: str | None = None) -> dict:
    """El pre-flight de UN CEDEAR: cada eslabón con su estado, y el veredicto.
    No escribe. `aplicar` lo vuelve a correr antes de escribir."""
    from agente import alta
    from core import cedears_sql

    tc = (ticker_corto or "").strip().upper()
    und = (underlying or tc).strip().upper()
    simbolo = cedears_sql.simbolo_de(tc)
    pasos: list[dict] = []

    # 1. Primary — foto y EN VIVO (la misma pregunta que el alta de bonos).
    est = alta.estado_simbolo(simbolo)
    con = est.get("conocido")
    pasos.append({"titulo": "Primary lista el símbolo", "tabla": simbolo,
                  "estado": ("ok" if con is True else
                             "no_se_puede_saber" if con is None else "bloquea"),
                  "detalle": est.get("nota", ""),
                  "aviso": ("la foto está vieja: el precio llega cuando el discovery "
                            "la refresque" if est.get("foto_vieja") else "")})

    # 2. La FICHA: ¿tiene el cficode de un CEDEAR? Solo se puede saber por la
    # foto; si está solo en vivo se dice que no se pudo mirar.
    lst = listado()
    ficha = None
    if lst.get("ok"):
        ficha = next((f for f in lst["fichas"] if f.get("simbolo") == simbolo), None)
    if ficha is None:
        pasos.append({"titulo": "Tiene la ficha de un CEDEAR", "tabla": "cficode",
                      "estado": "no_se_puede_saber",
                      "detalle": (lst.get("error") or
                                  "no está en la foto: no puedo ver su cficode")})
    elif ficha.get("cficode") in set(lst.get("cficodes") or []):
        pasos.append({"titulo": "Tiene la ficha de un CEDEAR", "tabla": "cficode",
                      "estado": "ok",
                      "detalle": (f"cficode {ficha['cficode']} · moneda "
                                  f"{ficha.get('moneda') or '?'} — la misma ficha "
                                  f"que los {lst['reconocidos']} CEDEARs que ya tenemos")})
    else:
        pasos.append({"titulo": "Tiene la ficha de un CEDEAR", "tabla": "cficode",
                      "estado": "bloquea",
                      "detalle": (f"Primary le pone cficode {ficha.get('cficode')!r} y "
                                  f"nuestros CEDEARs tienen {lst['cficodes']}: no es "
                                  "un CEDEAR (o es otra pata)")})

    # 3. El master.
    previo = next((m for m in (lst.get("master") or []) if m.get("ticker") == simbolo), None)
    if previo is None:
        pasos.append({"titulo": "No está en el master", "tabla": "mercado.cedears",
                      "estado": "ok", "detalle": "se crea la fila (activo=sí)"})
    else:
        pasos.append({"titulo": "Ya está en el master", "tabla": "mercado.cedears",
                      "estado": "info",
                      "detalle": (f"activo={'sí' if previo.get('activo') else 'no'} · "
                                  f"subyacente {previo.get('underlying')}: el alta lo "
                                  "reactiva y no pisa rubro/ratio/ric")})

    # 4. El subyacente: quién trae la historia y el ADR.
    pasos.append({"titulo": "Subyacente en USD", "tabla": und, "estado": "info",
                  "detalle": (f"al aplicar se piden los cierres desde 2024 a Yahoo "
                              f"(`mercado.precios_acciones`) y el quote a Finnhub "
                              f"(`mercado.adr_snapshot`) para «{und}»; si no "
                              "contestan se anota en el libro y el CEDEAR queda igual "
                              "con precio ARS")})

    # 5. El motor.
    mot = _motor()
    pasos.append({"titulo": "El motor lo suscribe", "tabla": "engines/motor_cedears",
                  "estado": mot["estado"], "detalle": mot["detalle"]})

    bloquean = [p["titulo"] for p in pasos if p["estado"] == "bloquea"]
    ciegos = [p["titulo"] for p in pasos if p["estado"] == "no_se_puede_saber"]
    # ⚠️ Sin poder afirmar que Primary lo lista NO se escribe: un CEDEAR que no
    # cotiza es una fila que el motor pide y el WS filtra, para siempre.
    puede = not bloquean and con is True
    if bloquean:
        texto = f"NO se puede aplicar — {len(bloquean)} paso/s lo bloquean: " + " · ".join(bloquean)
    elif con is not True:
        texto = "NO se puede aplicar — no pude confirmar que Primary lo liste (ni foto ni en vivo)"
    elif ciegos:
        texto = "Se puede aplicar; no pude mirar: " + " · ".join(ciegos)
    else:
        texto = "Se puede aplicar: la cadena cierra entera"
    return {"ok": True, "ticker_corto": tc, "simbolo": simbolo, "underlying": und,
            "pasos": pasos, "puede_aplicar": puede, "veredicto": texto,
            "foto_vieja": bool(est.get("foto_vieja")), "existia": previo is not None}


def aplicar(ticker_corto: str, underlying: str | None = None, *, actor: str = "") -> dict:
    """Escribe la cadena entera de UN CEDEAR, un eslabón por línea del libro.

    El orden importa: primero el master (es lo que hace que EXISTA para el
    motor y los jobs); después la historia del subyacente y el ADR, que son
    mejoras — si Yahoo o Finnhub no contestan, el CEDEAR ya está dado de alta
    y el job nocturno lo completa.
    """
    sim = simular(ticker_corto, underlying)
    if not sim["puede_aplicar"]:
        return {"ok": False, "ticker_corto": sim["ticker_corto"], "error": sim["veredicto"]}
    tc, simbolo, und = sim["ticker_corto"], sim["simbolo"], sim["underlying"]

    from core import cedears_sql
    r = cedears_sql.alta(tc, simbolo=simbolo, underlying=und, actor=actor)
    libro.registrar(accion="alta_cedear", objetivo=tc, destino="mercado.cedears",
                    campo="cedear", antes=r["antes"], despues=r["despues"],
                    por=actor, ok=True)
    salida = {"ok": True, "ticker_corto": tc, "simbolo": simbolo, "underlying": und,
              "existia": r["existia"], "foto_vieja": sim["foto_vieja"]}

    # Historia EOD del subyacente — por el MISMO writer que el job nocturno.
    try:
        from jobs.precios_acciones_daily import backfill_ticker
        n, err = backfill_ticker(und)
    except Exception as e:  # red, import, lo que sea: se anota, no frena
        n, err = 0, f"{type(e).__name__}: {e}"[:200]
    libro.registrar(accion="alta_cedear", objetivo=tc, destino="mercado.precios_acciones",
                    campo="eod", antes="sin historia", despues=f"{n} velas de {und}",
                    por=actor, ok=not err, error=err or "")
    salida["velas"] = n
    salida["velas_error"] = err or ""

    # ADR live del subyacente — por el MISMO writer que `jobs/adr_live`.
    try:
        from jobs.adr_live import upsert_uno
        ok_adr, det_adr = upsert_uno(und)
    except Exception as e:
        ok_adr, det_adr = False, f"{type(e).__name__}: {e}"[:200]
    libro.registrar(accion="alta_cedear", objetivo=tc, destino="mercado.adr_snapshot",
                    campo="adr", antes="sin quote", despues=det_adr if ok_adr else "",
                    por=actor, ok=ok_adr, error="" if ok_adr else det_adr)
    salida["adr"] = det_adr
    salida["adr_ok"] = ok_adr
    return salida


def aplicar_varios(datos: list[dict], *, actor: str = "") -> dict:
    """Lo que manda el listado: `[{unidad: ticker corto, valor: subyacente}]`.
    `valor` vacío = el subyacente es el mismo ticker. Cada CEDEAR se simula y
    se escribe por su cuenta: uno que Primary no lista no frena a los demás."""
    escritos, errores = [], []
    for d in datos or []:
        tc = str((d or {}).get("unidad") or "").strip().upper()
        if not tc:
            continue
        und = str((d or {}).get("valor") or "").strip().upper() or None
        try:
            r = aplicar(tc, und, actor=actor)
        except Exception as e:
            logger.exception("alta_cedear: %s reventó", tc)
            r = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
        if r.get("ok"):
            escritos.append(r)
        else:
            errores.append(f"{tc}: {r.get('error') or '?'}")
    return {"escritos": escritos, "errores": errores}
