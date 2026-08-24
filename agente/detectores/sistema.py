"""Detectores de SISTEMA. Doc: `docs/AGENT_2.0.md` §5.

Todos devuelven `list[Hallazgo]` o levantan `SinDatos`. **Ninguno escribe.**

⚠️ **Ninguno lleva su propio `try/except`**, y eso es el cambio de fondo. En el
agente viejo cada uno de los 19 contestaba por su cuenta «¿qué hago si no puedo
mirar?»: unos devolvían vacío en silencio, otros emitían un hallazgo que lo
decía, y uno reventaba a propósito. Los tres son defendibles; el problema es que
era **la misma decisión tomada 19 veces**, y la vigésima se iba a tomar mal.

Acá el detector mira y devuelve, o levanta `SinDatos`. **Qué significa un fallo
lo decide el motor**, una sola vez, para todos.
"""
from __future__ import annotations

import logging

from agente import reloj
from agente.tipos import Hallazgo, SinDatos

logger = logging.getLogger(__name__)


# ═══ salud ═════════════════════════════════════════════════════════════════
def salud(u: dict) -> list[Hallazgo]:
    """Jobs que no corrieron, corrieron con error, o dejaron el dato viejo.

    **No detecta: traduce.** El dueño de la evaluación es `api/services/salud`;
    acá se la lee y se convierte en hallazgos. Que sea un traductor está bien —
    lo que estaba mal es que declarara un arreglo sin tenerlo: su único botón
    vuelve a chequear, y **mirar no arregla** (§6.4). Por eso ahora es un AVISO.
    """
    from api.services import salud as motor
    try:
        chequeos = motor.evaluar()
    except Exception as e:
        raise SinDatos(f"el motor de salud no contestó: {e}") from e

    sev = {"error": "alta", "warn": "media"}
    out = []
    for c in chequeos or []:
        estado = (c.get("estado") or "").lower()
        if estado not in sev:
            continue                          # verde no es un hallazgo
        # El job que TERMINÓ BIEN pero anotó algo es otra cosa que el que falló:
        # se atiende distinto y por eso lleva su propia regla.
        regla = ("job_ok_con_avisos" if c.get("parcial")
                 else f"salud_{c.get('familia') or 'chequeo'}")
        out.append(Hallazgo(
            sujeto=str(c.get("id") or "?"), regla=regla, severidad=sev[estado],
            nombre=str(c.get("titulo") or c.get("id") or ""),
            problema=str(c.get("motivo") or "")[:900] or "no está en verde",
            que_hacer=("Relanzar el job y mirar su log. Si el problema es el "
                       "horario, el scheduler; si corrió y salió mal, el código."),
            evidencia={
                "chequeo_id": c.get("id"), "familia": c.get("familia"),
                "estado": estado, "n_casos": c.get("n"),
                "schedule": c.get("schedule"), "tabla": c.get("tabla"),
                "ultimo_at": c.get("ultimo_at"),
                "esperada_at": c.get("esperada_at"),
                # Separa las dos preguntas que se atienden distinto: si NO corrió
                # después de su horario el problema es el scheduler; si corrió y
                # salió mal, está adentro.
                "corrio_despues": c.get("corrio_despues"),
                "evidencia_salud": c.get("evidencia")}))
    return out


# ═══ motor_caido ═══════════════════════════════════════════════════════════
def motor_caido(u: dict) -> list[Hallazgo]:
    """Motores y jobs rotos **dentro de su ventana**.

    Fuera de rueda un motor no está caído: está apagado.

    ⚠️ **La hora sale del ÁRBOL, no del reloj del proceso.** Cuando algo se
    evalúa contra una foto, el tiempo tiene que salir de la foto — dos relojes
    juzgando la misma foto hacían fallar tres tests media hora por día.
    """
    from datetime import time as _t

    from api.services import diagnostico
    try:
        arbol = diagnostico.arbol()
    except Exception as e:
        raise SinDatos(f"no pude leer el árbol de diagnóstico: {e}") from e

    ahora = _hora_del_arbol(arbol)
    if arbol.get("en_rueda") and ahora is not None:
        abre: _t = diagnostico._APERTURA["rueda"]
        mins = (ahora.hour - abre.hour) * 60 + (ahora.minute - abre.minute)
        if 0 <= mins < int(u.get("gracia_arranque_min", 30)):
            return []                     # arrancando no es caído

    rotos = {"critico", "error", "sin_datos"}
    ignorar = {"fuera_rueda", "ok", "lento", "error_parse"}
    out = []
    for vista in arbol.get("vistas") or []:
        for grupo in vista.get("grupos") or []:
            for p in grupo.get("piezas") or []:
                estado = (p.get("estado") or "").strip()
                if estado in ignorar or estado not in rotos:
                    continue
                # `sin_datos` se decide ANTES de mirar la ventana en el árbol,
                # así que un motor de mercado que nunca escribió salía en ALTA a
                # las 3 de la mañana. Se corrige acá y no allá: ese estado lo
                # comparte la pantalla de DIAGNÓSTICO.
                if estado == "sin_datos" and not _en_su_ventana(p, ahora):
                    continue
                if _todavia_no_le_toco(p, ahora):
                    continue
                sev = "alta" if estado in ("critico", "error") else "media"
                nombre = str(p.get("nombre") or p.get("unidad") or "?")
                out.append(Hallazgo(
                    sujeto=str(p.get("unidad") or nombre), regla=f"pieza_{estado}",
                    severidad=sev, nombre=nombre,
                    problema=(f"{nombre}: {estado} · última señal "
                              f"{p.get('hace') or '—'} · ventana "
                              f"{p.get('ventana') or 'rueda'} · {reloj.hhmm()}"),
                    que_hacer=(f"Relanzar `{p.get('unidad') or nombre}` y mirar "
                               f"su log. Cadencia declarada: "
                               f"{p.get('cadencia') or '—'}."),
                    evidencia={"vista": vista.get("vista"), "tipo": p.get("tipo"),
                               "estado": estado, "unidad": p.get("unidad"),
                               "tabla": p.get("tabla"),
                               "cadencia": p.get("cadencia"),
                               "ventana": p.get("ventana"),
                               "ultimo_at": p.get("ultimo_at")}))
    # Lo más grave primero, y los motores antes que los jobs: un motor caído deja
    # a la mesa sin precios AHORA; un job se recupera en la corrida siguiente.
    out.sort(key=lambda h: (0 if h.severidad == "alta" else 1,
                            0 if h.evidencia.get("tipo") == "motor" else 1))
    return out


def _hora_del_arbol(arbol: dict):
    from datetime import datetime

    from core.tz import AR_TZ
    v = arbol.get("ahora") or arbol.get("at")
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)).astimezone(AR_TZ)
    except (TypeError, ValueError):
        return None


def _en_su_ventana(p: dict, ahora) -> bool:
    from api.services import diagnostico
    if ahora is None:
        return True
    return diagnostico._en_ventana(ahora, str(p.get("ventana") or "rueda"))


def _todavia_no_le_toco(p: dict, ahora) -> bool:
    """¿Estamos ANTES de la primera corrida posible de hoy?

    A las 11:13 un job que arranca 12:00 no está atrasado: no le tocó.

    ⚠️ **Hoy la hora de arranque sale de un REGEX sobre prosa** — cada pieza
    declara su cadencia como texto libre (`"cada 30m :05,:35 · 15-22 UTC L-V"`).
    Es la deuda que `AGENT_2.0.md` §5.1 deja anotada: el horario real vive en
    `deploy/crontab.txt` y en los units de systemd, que es la MISMA fuente que ya
    lee la habilidad `cron_desalineado`. Hasta entonces: ante cualquier duda
    devuelve `False` — avisar de más es mejor que callar un motor caído.
    """
    import re
    from datetime import datetime, timedelta
    from datetime import time as _t

    if ahora is None:
        return False
    m = re.search(r"(\d{1,2})\s*-\s*\d{1,2}\s*UTC", str(p.get("cadencia") or ""))
    if not m:
        return False
    utc = int(m.group(1))
    if not 0 <= utc <= 23:
        return False
    inicio = datetime.combine(ahora.date(), _t((utc - 3) % 24, 0),
                              tzinfo=ahora.tzinfo)
    if inicio > ahora:
        return True
    try:
        gracia = timedelta(seconds=float(p.get("umbral_s") or 0))
    except (TypeError, ValueError):
        gracia = timedelta(0)
    return ahora < inicio + gracia


# ═══ tabla_quieta ══════════════════════════════════════════════════════════
def tabla_quieta(u: dict) -> list[Hallazgo]:
    """Tablas que dejaron de escribir cuando deberían estar escribiendo.

    **El ritmo se MIDE observando la tabla, no lo declara nadie**: cubre las ~190
    que son un punto ciego total. Las 8 con contrato declarado las sigue mirando
    SALUD, que es más estricto porque el aprendido se acostumbra al problema.
    """
    from agente import tablas
    from core import escribe

    try:
        con_contrato = tablas._ya_tienen_contrato()
        con_ritmo = tablas.perfiles(solo_con_ritmo=True)
        vivo = tablas._ultimo_dato_vivo(con_ritmo)
    except Exception as e:
        raise SinDatos(f"no pude leer el perfil de las tablas: {e}") from e

    out = []
    for i, p in enumerate(con_ritmo):
        if i in vivo:
            p = {**p, "ultimo_dato": vivo[i]}
        nombre = f"{p['schema']}.{p['tabla']}"
        if nombre in con_contrato:
            continue
        f = tablas.frescura(p)
        if f["estado"] != "atrasada":
            continue
        # ⚠️ **UNA TABLA DE EVENTOS NO TIENE CADENCIA: TIENE OCASIONES.** Está
        # quieta porque no pasó nada, no porque algo esté roto, y no hay nada
        # que relanzar. `no_se` NO se saltea: ante la duda se sigue exigiendo.
        if escribe.la_dispara(nombre) == escribe.EVENTO:
            continue
        out.append(Hallazgo(
            sujeto=nombre, regla="sin_escribir",
            severidad="alta" if p["cadencia"] == "tiempo_real" else "media",
            problema=str(f["motivo"]),
            que_hacer=f"Relanzar {escribe.que_relanzar(nombre) or 'el job que la escribe'}.",
            evidencia={"cadencia": p["cadencia"], "col_fecha": p["col_fecha"],
                       "atraso_s": f["atraso_s"], "tope_s": f["tope_s"],
                       "ultimo_dato": f["ultimo_dato"], "filas": p["filas"],
                       # ¿El atraso se midió AHORA o salió de la foto del
                       # barrido? Sin esto, un veredicto viejo se lee igual que
                       # uno fresco.
                       "ultimo_vivo": i in vivo,
                       "la_escribe": escribe.quien_escribe(nombre),
                       "relanzar": escribe.que_relanzar(nombre)}))
    return out


# ═══ cron_desalineado ══════════════════════════════════════════════════════
def cron_desalineado(u: dict) -> list[Hallazgo]:
    """El crontab del repo contra el de la máquina, **en las dos direcciones**.

    `deploy.sh` no instala el crontab, así que un cron nuevo puede vivir en el
    repo y no ejecutarse nunca: no falla nada, el catálogo lo muestra igual, y el
    job no corrió.
    """
    from agente import crontab
    r = crontab.comparar()
    if not r.get("ok"):
        # **El silencio se lee igual que un verde.** Si la prueba no corrió hay
        # que decirlo — pero como AVISO, no cerrando nada.
        raise SinDatos("no pude leer el crontab de la máquina: no sé si los "
                       "crons del repo están instalados")

    out = []
    if (faltan := r["sin_instalar"]):
        out.append(Hallazgo(
            sujeto="crontab", regla="sin_instalar", severidad="alta",
            nombre="crons del repo que no corren",
            problema=f"{len(faltan)} cron(s) están en `deploy/crontab.txt` y NO "
                     f"en la máquina: no se ejecutan · {reloj.hhmm()}",
            que_hacer="Instalar el crontab del repo: "
                      "`crontab /root/TradingAV/deploy/crontab.txt`.",
            evidencia={"jobs": [crontab._que_job(x) for x in faltan],
                       "lineas": faltan[:20]}))
    if (sobran := r["sin_declarar"]):
        out.append(Hallazgo(
            sujeto="crontab", regla="sin_declarar", severidad="media",
            nombre="crons que corren sin estar declarados",
            problema=f"{len(sobran)} cron(s) corren en la máquina y el repo no "
                     f"los declara · {reloj.hhmm()}",
            que_hacer="Agregarlos a `deploy/crontab.txt` o sacarlos de la "
                      "máquina: la próxima instalación se los lleva puestos.",
            evidencia={"jobs": [crontab._que_job(x) for x in sobran],
                       "lineas": sobran[:20]}))
    return out


# ═══ latencia ══════════════════════════════════════════════════════════════
def latencia(u: dict) -> list[Hallazgo]:
    """Endpoints degradados contra SU PROPIA normalidad, y los que dan 5xx.

    No es un ranking de lentos: un ranking muestra lo lento y esa lista no
    cambia nunca. La referencia es la mediana de las propias horas previas del
    endpoint.
    """
    from agente import latencia as maq
    try:
        casos = maq.comparar()
    except Exception as e:
        raise SinDatos(f"no pude leer la telemetría: {e}") from e

    out = []
    for c in casos:
        if c["roto"]:
            out.append(Hallazgo(
                sujeto=c["endpoint"], regla="errores", severidad="alta",
                problema=f"{c['errores']} de {c['n']} requests fallaron (5xx) "
                         f"en las últimas {maq.VENTANA_H} h",
                que_hacer="Mirar el log del endpoint: un 5xx está roto tarde lo "
                          "que tarde, no pasa por la comparación con su normal.",
                evidencia={"errores": c["errores"], "requests": c["n"]}))
        if c["degradado"]:
            out.append(Hallazgo(
                sujeto=c["endpoint"], regla="mas_lento",
                severidad="alta" if (c["veces"] or 0) >= 5 else "media",
                problema=f"tarda {c['veces']}× su normal · {c['avg_ms']} ms "
                         f"contra {c['base_ms']} ms (pico {c['max_ms']} ms)",
                que_hacer="Perfilar ese endpoint: la referencia es su propia "
                          f"mediana de las {maq.BASE_H} h previas.",
                evidencia={"avg_ms": c["avg_ms"], "base_ms": c["base_ms"],
                           "veces": c["veces"], "max_ms": c["max_ms"],
                           "requests": c["n"]}))
    return out


# ═══ proveedor_caido ═══════════════════════════════════════════════════════
def proveedor_caido(u: dict) -> list[Hallazgo]:
    """Proveedores de afuera que no responden: Aunesa, 1816, Interbanking, BCRA.

    Se entera por el **rastro de las llamadas REALES**, no por un health check
    que contesta bien mientras el endpoint que usamos devuelve 500. Y la prueba
    activa solo corre **cuando ya hay una falla**: nunca en el camino feliz,
    porque 1816 cobra por llamada.
    """
    from core.proveedores import PROVEEDORES, estado
    try:
        filas = estado()
    except Exception as e:
        raise SinDatos(f"no pude leer el estado de los proveedores: {e}") from e

    ahora = reloj.ahora_utc()
    ventana = float(u.get("ventana_s", 20 * 60))
    minimo = int(u.get("minimo_fallos", 1))
    out = []
    for f in filas:
        if f.get("ok") or not (cuando := f.get("ultimo_error_at")):
            continue
        if cuando.tzinfo is None:
            from datetime import UTC
            cuando = cuando.replace(tzinfo=UTC)
        if ((ahora - cuando).total_seconds() > ventana
                or (f.get("fallos_seguidos") or 0) < minimo):
            continue
        p = PROVEEDORES.get(f["proveedor"])
        nombre = p.nombre if p else str(f["proveedor"]).upper()
        out.append(Hallazgo(
            sujeto=str(f["proveedor"]), regla="no_responde", severidad="alta",
            nombre=nombre,
            problema=f"{nombre} CAÍDO · {str(f.get('ultimo_error') or '')[:120]} "
                     f"· {reloj.hhmm(ahora)}",
            que_hacer=("Se arregla del otro lado. Verificar si es de ellos o si "
                       "se nos venció una credencial, y avisarle al back office."),
            evidencia={"fallos_seguidos": f.get("fallos_seguidos"),
                       "ultimo_error": f.get("ultimo_error"),
                       "ultimo_error_at": cuando.isoformat(),
                       "ultimo_ok_at": f.get("ultimo_ok_at"),
                       "rompe": p.rompe if p else ""}))
    return out


# ═══ db_peso ═══════════════════════════════════════════════════════════════
def db_peso(u: dict) -> list[Hallazgo]:
    """Tablas que crecieron fuera de lo suyo, **medido en vivo**.

    La medición y la serie viven en `agente/peso`: un detector mira y devuelve.
    El PESO de cada tabla no es un hallazgo —es información, y viaja aparte—;
    el hallazgo es lo que se movió.
    """
    from agente import peso

    try:
        hoy = peso.medir()
    except Exception as e:
        raise SinDatos(f"no pude medir el tamaño de la base: {e}") from e

    ayer = peso.de_hace(24)
    if not ayer:
        peso.guardar(hoy)
        # Sin referencia no se afirma nada. Es `[]` y no `SinDatos` porque la
        # medición SÍ corrió: simplemente todavía no hay contra qué comparar.
        return []

    total = sum(hoy.values()) or 1
    # El corte se auto-calibra sobre el tamaño real de la base: un umbral fijo
    # en MB envejece con la base y deja de significar nada.
    corte = max(int(total * 0.005), 20 * 1024 * 1024)
    out = []
    for tabla, b in hoy.items():
        prev = ayer.get(tabla)
        if prev is None or b <= prev or (b - prev) < corte:
            continue
        delta, pct = b - prev, round((b - prev) / prev * 100, 1) if prev else 100.0
        out.append(Hallazgo(
            sujeto=tabla, regla="crecio",
            severidad="alta" if pct >= 100 else "media",
            problema=f"creció {peso.mb(delta)} (+{pct}%) en 24 h · pasó de "
                     f"{peso.mb(prev)} a {peso.mb(b)} · {reloj.hhmm()}",
            que_hacer=f"Ver qué la está escribiendo. El corte para avisar es "
                      f"{peso.mb(corte)}, calculado sobre el tamaño real de la "
                      f"base — no un número fijo que envejece con ella.",
            evidencia={"bytes": b, "delta": delta, "pct": pct,
                       "corte_bytes": corte}))
    for tabla, b in ayer.items():
        if tabla not in hoy:
            out.append(Hallazgo(
                sujeto=tabla, regla="desaparecio", severidad="alta",
                problema=f"la tabla ya NO está · hace 24 h pesaba {peso.mb(b)}",
                que_hacer="Si fue a propósito, ignorala. Si no, alguien dropeó "
                          "algo.",
                evidencia={"bytes_antes": b}))
    peso.guardar(hoy)
    return out


# ═══ actividad ═════════════════════════════════════════════════════════════
def actividad(u: dict) -> list[Hallazgo]:
    """**LO QUE NO PUEDE PASAR EN UN DÍA NO HÁBIL.**

    Acá el razonamiento se invierte: no se mira si el dato está bien, se mira
    que NO HAYA dato nuevo. Sábado, domingo o feriado el mercado no abre, los
    motores están apagados por cron y nadie debería escribir precios.

    En día hábil devuelve `[]` **sin tocar la base**: el universo prohibido no
    existe.
    """
    from core.postgres import get_pool
    from core.tz import AR_TZ

    ahora = reloj.ahora_utc()
    if reloj.dia_habil(ahora):
        return []

    hoy_art = ahora.astimezone(AR_TZ).date()
    desde = reloj.arranco_el_dia(ahora)
    dia = {5: "sábado", 6: "domingo"}.get(hoy_art.weekday(), "feriado")
    out = []
    # ⚠️ Sin `try`: si la base no contesta, que levante. Tragarse el error acá
    # devolvería `[]`, la pasada quedaría como «ok» y cerraría hallazgos que
    # nadie volvió a mirar — la mentira optimista de siempre.
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*), max(updated_at) FROM mercado.market_snapshot "
                    "WHERE updated_at >= %s", (desde,))
        n, ultimo = cur.fetchone() or (0, None)
        if n:
            out.append(Hallazgo(
                sujeto="market_snapshot", regla="escribe_en_no_habil",
                severidad="alta",
                problema=f"hoy es {dia} y `mercado.market_snapshot` recibió {n} "
                         f"escrituras (última "
                         f"{ultimo.astimezone(AR_TZ).strftime('%H:%M') if ultimo else '—'})",
                que_hacer="Apagar el motor que quedó prendido o corregir el cron "
                          "que lo levanta.",
                evidencia={"escrituras": n, "dia": dia,
                           "ultima": ultimo.isoformat() if ultimo else None}))
        cur.execute("SELECT updated_at FROM operaciones.motor_heartbeat "
                    "WHERE id = 'current'")
        f = cur.fetchone()
        if f and f[0] and f[0] >= desde:
            out.append(Hallazgo(
                sujeto="motor_ordenes", regla="escribe_en_no_habil",
                severidad="alta",
                problema=f"hoy es {dia} y el heartbeat del motor de órdenes late "
                         f"({f[0].astimezone(AR_TZ).strftime('%H:%M')})",
                que_hacer="Apagar `motor_ordenes`: está corriendo con el mercado "
                          "cerrado.",
                evidencia={"latido": f[0].isoformat(), "dia": dia}))
    return out
