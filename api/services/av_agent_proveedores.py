"""api/services/av_agent_proveedores.py — SI SE CAYÓ UNO DE AFUERA, EL AGENTE AVISA.

Doc madre: **`docs/AV_AGENT.md`** §0.ad.

Pedido del user (2026-08-20), mientras Aunesa devolvía HTTP 500 en su login:
*«esto es una funcionalidad que la vi de milagro… sí o sí el agente tiene que
detectar cuándo esto está caído, avisar y dar el motivo exacto»*.

El registro lo escribe `core/proveedores` desde adentro de los clientes HTTP —
cada llamada REAL deja su rastro, sin health checks (1816 cobra por llamada, y
un ping que contesta bien no prueba que el endpoint que usamos ande). Acá solo
se lee y se convierte en hallazgo.

**EL AVISO TIENE QUE DECIR TRES COSAS**, y las tres estaban en el cartel que el
user vio de milagro:

    1. QUIÉN se cayó          →  Aunesa (el custodio)
    2. QUÉ deja de andar      →  Tesorería sin los movimientos del día…
    3. EL MOTIVO EXACTO       →  HTTPError: 500 Server Error for url: …/login

La 3 es la que convierte un aviso en algo accionable: sin el error textual, el
que lo lee no puede distinguir «se cayó el proveedor» de «se nos vencieron las
credenciales», que se resuelven en lugares distintos y por personas distintas.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# ⚠️ **EL HALLAZGO VENCE.** Solo se canta si el último fallo es RECIENTE.
#
# Nadie apaga el registro cuando el proveedor se recupera: simplemente dejan de
# anotarse fallos. Sin esta ventana, un 500 de la semana pasada seguiría en la
# pantalla para siempre — y una pantalla con un problema viejo enseña a
# ignorarla, que es justo lo que pasó con los hallazgos de rueda (§0.u).
#
# 20 minutos: los daemons de Aunesa corren cada pocos minutos, así que una caída
# de verdad se re-anota sola varias veces dentro de la ventana. Si en 20 minutos
# nadie volvió a fallar, o se arregló o nadie lo está usando — en los dos casos
# no hay nada que avisar AHORA.
VENTANA_S = 20 * 60

# Un fallo aislado puede ser un timeout de red. Dos seguidos ya es el proveedor.
MINIMO_FALLOS = 1


def detectar_proveedores(*, ahora: datetime | None = None) -> list[dict]:
    """Los proveedores externos que están fallando **ahora**.

    **Nunca levanta**: corre adentro del monitor de rueda, junto a los detectores
    de precio, y una excepción acá apagaría el ciclo entero.
    """
    try:
        from core.proveedores import PROVEEDORES, estado
        filas = estado()
    except Exception as e:
        logger.warning("av_agent_proveedores: no pude leer el estado: %s", e)
        return []

    ahora = ahora or datetime.now(UTC)
    out = []
    for f in filas:
        if f.get("ok"):
            continue
        cuando = f.get("ultimo_error_at")
        if not cuando:
            continue
        if cuando.tzinfo is None:
            cuando = cuando.replace(tzinfo=UTC)
        hace = (ahora - cuando).total_seconds()
        if hace > VENTANA_S or (f.get("fallos_seguidos") or 0) < MINIMO_FALLOS:
            continue

        # ⚠️ **LA PRUEBA SOLO CORRE SI YA HAY UNA FALLA.** Nunca en el camino
        # feliz: 1816 cobra por llamada y un chequeo cada 5 minutos se come la
        # cuota. Acá el rastro ya dijo que algo falló, así que UNA request más
        # —sin credenciales— es barata y contesta la pregunta que sigue: **¿es de
        # ellos o es nuestro?** Es lo que pidió el user: *«el agente sí o sí
        # tiene que poder llamar a Aunesa para ver la conexión y entender el
        # error»*.
        prueba = _probar(f["proveedor"])
        # EL BARRIDO COMPLETO: ¿le pasa a las cinco APIs o a una sola? Es la
        # pregunta que pidió el user y la que decide qué hacer — con las cinco
        # caídas no hay nada que hacer de este lado; con una, el resto de los
        # datos sigue entrando y hay que decirlo o el equipo da por perdido el
        # día entero.
        #
        # ⚠️ Con FRENO (`_toca_barrer`): el monitor corre cada 5 minutos y el
        # barrido son 6 requests. Sin freno, una caída de una hora son 72
        # requests contra un proveedor que ya sabemos que está mal — que además
        # es la peor hora para agregarle carga.
        barrido = _barrer_si_toca(f["proveedor"])

        p = PROVEEDORES.get(f["proveedor"])
        nombre = p.nombre if p else (f["proveedor"] or "").upper()
        rompe = p.rompe if p else "no sé qué depende de él"
        veces = f.get("fallos_seguidos") or 1

        # ── EL AVISO VA CORTO ────────────────────────────────────────────
        # Regla del user (2026-08-20): *«decí AUNESA CAÍDO + motivo simple y
        # listo, nada de palabras raras ni tanto texto, con la hora»*. El
        # título entra de un vistazo; el cuerpo son renglones sueltos. El que
        # lo lee está por decidir algo, no por leer un informe.
        # ⚠️ **SEGUNDA PASADA (2026-08-20)**: el user vio el aviso real y marcó
        # dos cosas. (1) *«el texto del medio no me interesa, es sencillito:
        # decir solo los que fallan y punto»* — se iban tres renglones de
        # contexto («se cae X», «el resto entra bien», «lo cargado a mano sí
        # está») que no cambian ninguna decisión. (2) **NO TENÍA LA HORA**:
        # decía «falló hace 6 segundos», que a los diez minutos ya miente y no
        # se puede cruzar con nada.
        #
        # Queda: QUÉ falla · CUÁNDO empezó · CUÁNDO fue el último OK. Nada más.
        motivo = f"{nombre} CAÍDO · {_corto(f.get('ultimo_error'))}"
        lineas_ev = []
        rotos = _los_que_fallan(barrido)
        if rotos:
            lineas_ev.append("Falla: " + " · ".join(rotos))
        lineas_ev.append(
            f"Cayó {_fecha(f.get('ultimo_error_at')) or _hace(hace) + ' atrás'}"
            + (f" ({veces} veces)" if veces > 1 else "")
            + (f" · último OK {_fecha(f.get('ultimo_ok_at'))}"
               if f.get("ultimo_ok_at") else " · nunca contestó bien"))
        out.append({
            "tipo": "proveedor_caido", "ticker": f["proveedor"],
            "regla": "no_responde",
            # ALTA sin matices: no es un dato feo, es media aplicación andando a
            # ciegas. Y el que la usa no tiene forma de darse cuenta solo.
            "severidad": "alta", "motivo": motivo,
            "evidencia": {
                "texto": "\n".join(lineas_ev),
                "proveedor": f["proveedor"], "rompe": rompe,
                "prueba": prueba, "barrido": barrido,
                "error": f.get("ultimo_error"), "donde": f.get("donde"),
                "fallos_seguidos": veces, "hace_s": round(hace),
                "ultimo_ok_at": _fecha(f.get("ultimo_ok_at"))}})
    # El que más viene fallando, primero.
    out.sort(key=lambda h: -h["evidencia"]["fallos_seguidos"])
    # **Y SE AVISA DIRECTO**, no solo se deja en ENCONTRÓ: esa pantalla es
    # admin-only y el que sufre esto es el back office. El envío se auto-frena
    # por `tema` (uno por día y por proveedor), así que llamarlo en cada ciclo
    # no genera 84 mensajes.
    for h in out:
        h["evidencia"]["aviso"] = avisar_caida(
            h, barrido=h["evidencia"].get("barrido"))
    return out


def _los_que_fallan(barrido: dict | None) -> list[str]:
    """Los endpoints rotos, cortos. Lo ÚNICO del barrido que entra al aviso.

    El análisis en prosa («fallan 2 de 5, el resto entra bien») se dio de baja:
    la cuenta ya se ve en la lista y la frase ocupaba dos renglones para no
    cambiar ninguna decisión.
    """
    return [f"{x.get('para_que') or x.get('path') or '?'}"
            # `status` es None cuando ni siquiera hubo respuesta (timeout, DNS):
            # ahí no se inventa un código, se dice que no contestó.
            + (f" (HTTP {x['status']})" if x.get("status") else " (no contestó)")
            for x in ((barrido or {}).get("endpoints") or [])
            if not x.get("ok")]


def _probar(proveedor: str) -> dict | None:
    """La prueba activa, sin dejar que su fallo se lleve puesto el hallazgo.

    Si la prueba no se puede hacer, el aviso sale igual **sin** la línea de «de
    quién es»: media respuesta sirve, ninguna no.
    """
    try:
        from core.proveedores import probar
        r = probar(proveedor)
        return r if r.get("veredicto") != "no_se_puede_probar" else None
    except Exception as e:                                  # nunca hacia arriba
        logger.warning("av_agent_proveedores: la prueba falló (%s)", e)
        return None


# Cada cuánto se barren las cinco APIs mientras la caída sigue. El monitor corre
# cada 5 minutos; barrer cada 15 da tres fotos por hora, que alcanza de sobra
# para ver si algo se recupera, sin castigar a un servicio que ya está mal.
BARRER_CADA_S = 15 * 60
_ultimo_barrido: dict[str, tuple[float, dict]] = {}


def _barrer_si_toca(proveedor: str) -> dict | None:
    """El barrido completo, como mucho cada `BARRER_CADA_S`.

    Entre barridos se reusa el último: el aviso sigue diciendo el análisis, con
    la foto de hace unos minutos, que para «fallan las cinco» no cambia nada.
    """
    if proveedor != "aunesa":
        return None
    import time as _t
    cuando, previo = _ultimo_barrido.get(proveedor, (0.0, None))
    if previo is not None and _t.monotonic() - cuando < BARRER_CADA_S:
        return previo
    try:
        from core.proveedores import barrer_aunesa
        r = barrer_aunesa()
    except Exception as e:                                  # nunca hacia arriba
        logger.warning("av_agent_proveedores: el barrido falló (%s)", e)
        return previo
    _ultimo_barrido[proveedor] = (_t.monotonic(), r)
    return r


def barrer_ahora(proveedor: str = "aunesa") -> dict:
    """**A pedido**: probar las cinco APIs y devolver el análisis general."""
    from core.proveedores import barrer_aunesa
    if proveedor != "aunesa":
        return {"veredicto": "no_se_puede_probar",
                "analisis": "solo Aunesa tiene un barrido definido"}
    return barrer_aunesa()


def probar_ahora(proveedor: str = "aunesa") -> dict:
    """**A pedido**: probar la conexión y decir de quién es el problema.

    Lo usa el explicador del agente. A diferencia del detector, contesta también
    cuando el proveedor está sano — «¿anda Aunesa?» tiene que poder responderse
    que sí.
    """
    from core.proveedores import PROVEEDORES, probar

    r = probar(proveedor)
    p = PROVEEDORES.get(proveedor)
    return {**r,
            "nombre": p.nombre if p else proveedor,
            "rompe": p.rompe if p else None}


# El error de una excepción de Python trae el módulo, la URL entera y a veces un
# traceback. En el título entra solo la parte que dice QUÉ pasó.
def _corto(error: str | None, tope: int = 60) -> str:
    """La parte del error que dice QUÉ pasó, sin el tipo ni la URL.

    ⚠️ La primera versión partía por «:» y elegía el primer pedazo «que no
    hablara de una url» — y con `500 Server Error for url: https://…` (sin dos
    puntos después de «Error») elegía **`https`**. Demasiado ingenioso para algo
    que se resuelve cortando por delante y por detrás.
    """
    e = (error or "sin detalle").strip().splitlines()[0]
    # Por delante: el tipo de excepción, que no le dice nada a nadie.
    if ": " in e:
        e = e.split(": ", 1)[1]
    # Por detrás: la URL, que ocupa todo el renglón y ya se sabe cuál es.
    for corte in (" for url", " http", " en https"):
        if corte in e:
            e = e.split(corte, 1)[0]
    return e.strip(" :.")[:tope] or "sin detalle"


def _hace(seg: float) -> str:
    if seg < 120:
        return f"{int(seg)} segundos"
    if seg < 7200:
        return f"{int(seg / 60)} minutos"
    return f"{seg / 3600:.1f} horas"


def _fecha(dt) -> str | None:
    return dt.strftime("%d/%m %H:%M") if dt else None


def resumen() -> dict:
    """**¿Están andando los de afuera?** — para el explicador.

    Devuelve el estado de TODOS, no solo los caídos: la pregunta «¿anda Aunesa?»
    tiene que poder contestarse que sí.
    """
    try:
        from core.proveedores import PROVEEDORES, estado
        filas = {f["proveedor"]: f for f in estado()}
    except Exception as e:
        # «No pude preguntar» no es «están todos bien» (§0.s).
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "proveedores": [
        {"proveedor": k, "nombre": p.nombre, "rompe": p.rompe,
         # Sin fila = nunca falló desde que existe el registro. No es lo mismo
         # que «contestó bien recién», y por eso se dice distinto.
         "estado": ("sin registro" if k not in filas
                    else "anda" if filas[k]["ok"] else "no responde"),
         "ultimo_error": (filas.get(k) or {}).get("ultimo_error"),
         "ultimo_error_at": _fecha((filas.get(k) or {}).get("ultimo_error_at"))}
        for k, p in PROVEEDORES.items()]}


# ═══════════════════════════════════════════════════════════════════════════
# AVISAR DIRECTO — «además de encontrar, lo tiene que avisar»
# ═══════════════════════════════════════════════════════════════════════════
#
# Pedido del user (2026-08-20): *«esto lo tiene que avisar directamente además de
# encontrar»*. Y tiene razón por una diferencia concreta: **ENCONTRÓ es
# admin-only**. El que sufre que Aunesa esté caído es el back office, que ni
# siquiera ve la pantalla del agente. Un hallazgo que solo ve un admin no es un
# aviso para quien tiene que actuar.
#
# A QUIÉN: los que escriben en Tesorería (`operaciones.tesoreria_escritores`) más
# los admin. No es una lista nueva — es la gente que ya está habilitada a operar
# justo lo que se rompe, así que se mantiene sola cuando cambia el equipo.
#
# CUÁNDO: **una vez por día y por caída**, no por corrida. El monitor corre cada
# 5 minutos: avisar en cada ciclo serían 84 mensajes idénticos en una jornada, y
# el 84º informa menos que el primero. El `tema` lleva la fecha, así que el
# segundo envío del día no se apila mientras el primero siga abierto.

TEMA_AVISO = "proveedor_caido"


def avisar_caida(hallazgo: dict, *, barrido: dict | None = None) -> dict:
    """Le manda el aviso a quien lo sufre. Devuelve `{enviados, a, error}`.

    **Nunca levanta**: si el aviso falla, el hallazgo tiene que quedar igual.
    """
    try:
        from datetime import date

        from api.services import av_agent_mensajes as msg
        ev = hallazgo.get("evidencia") or {}
        # ⚠️ **NO se le antepone el análisis**: ya está adentro de `texto`, y
        # anteponerlo imprimía la MISMA frase dos veces — se vio en el aviso
        # real del 2026-08-20. Dos copias de la misma línea en un aviso corto
        # es lo que hace que se deje de leer.
        texto = ev.get("texto") or ""
        a = _a_quien()
        if not a:
            # Sin destinatario el aviso no existe, y hay que DECIRLO: un
            # «enviados: 0» silencioso se lee igual que «no hacía falta avisar».
            return {"enviados": 0, "a": [],
                    "error": "nadie a quien avisar: ni allowlist de Tesorería "
                             "ni admins"}
        tema = f"{TEMA_AVISO}:{hallazgo.get('ticker')}:{date.today().isoformat()}"
        asunto = hallazgo.get("motivo") or "Un proveedor externo no responde"
        r = msg.enviar_muchos(
            [{"para": e, "tema": tema, "asunto": asunto, "detalle": texto,
              "donde": "BACK OFFICE · TESORERÍA"} for e in a],
            por="av_agent_proveedores")
        return {"enviados": r.get("enviados", 0), "a": a,
                "error": r.get("error")}
    except Exception as e:                                  # nunca hacia arriba
        logger.warning("av_agent_proveedores: no pude avisar (%s)", e)
        return {"enviados": 0, "a": [], "error": str(e)[:200]}


def _a_quien() -> list[str]:
    """Los que escriben en Tesorería + los admin, sin repetidos.

    Las dos listas en UNA query: son dos tablas pero un solo viaje a Supabase, y
    lo que se paga por consulta es el peaje, no el plan.
    """
    from core.postgres import get_pool

    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT email FROM operaciones.tesoreria_escritores "
                "UNION "
                # Los admin SIEMPRE: si la allowlist está vacía o la tabla no
                # existe, el aviso no puede quedarse sin destinatario — es
                # justo el caso en que más importa que llegue.
                "SELECT email FROM manager.manager_users "
                "WHERE role = 'admin' AND coalesce(enabled, true)")
            emails = [r[0] for r in cur.fetchall() if r[0]]
    except Exception as e:
        logger.warning("av_agent_proveedores: sin destinatarios (%s)", e)
        return []
    vistos, out = set(), []
    for e in emails:
        k = (e or "").strip().lower()
        if k and k not in vistos:
            vistos.add(k)
            out.append(k)
    return out
