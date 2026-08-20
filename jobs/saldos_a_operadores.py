"""jobs/saldos_a_operadores.py — a cada operador, los saldos de SUS comitentes.

Doc madre: **`docs/AV_AGENT.md`** §0.ab.

Pedido del user (2026-08-19): *«que a las 16:45 todos los días hábiles envíe
mensajes vía la app a todos los users que tengan saldos positivos o negativos por
moneda en saldos de cuentas comitentes… que sepa los saldos de las comitentes y a
qué operador pertenece, que sepa qué es un operador y quiénes son»*.

Es el PRIMER consumidor de `av_agent_mensajes`, y por eso importa: prueba que la
capacidad sirve para algo que no se escribió pensando en ella. Cero IA — todo
sale de la base, que es de lo que el user avisó: *«tampoco termina de ser IA
esto»*.

CÓMO SE ARMA
============

    portafolio.control_saldos   el saldo LIQUIDADO de hoy por cuenta y moneda
              ↓ id_cuenta
    clientes.comitentes         qué operador atiende esa cuenta
              ↓ operador_email
    clientes.operadores         quién es y cuál es su mail

TRES DECISIONES QUE LO HACEN ÚTIL Y NO RUIDO
=============================================

1. **UN mensaje por operador**, no uno por cuenta. Un operador con 40 comitentes
   no necesita 40 pings: necesita su lista. Es la misma regla que ya rige
   `avisar.responsable` («200 pings no son 200 avisos, son un aviso ignorado»).

2. **Solo lo que no es cero.** Un saldo en cero es el estado normal y mandarlo
   convierte el mensaje en un reporte que nadie abre. Lo que se avisa es lo que
   pide una decisión: descubiertos y sobrantes.

3. **Las cuentas SIN operador no desaparecen.** No le llegan a nadie por
   definición, así que van al log y al stat del job. Un envío que solo cuenta lo
   que mandó esconde justo lo que quedó sin dueño.

PROBARLO SIN MOLESTAR A NADIE
==============================

    python -m jobs.saldos_a_operadores --a vos@acavalores.com.ar

Manda TODO a una sola persona, con `[PRUEBA]` en el asunto. Sin esto, probar un
aviso masivo significa escribirle a la mesa entera para ver si el modal se ve
bien.

⚠️ **El signo YA viene corregido** en `control_saldos` (Aunesa manda las tenencias
al revés y el daemon lo arregla): negativo = DESCUBIERTO real, no un falso
negativo por caución sin vencer. No re-invertir.
"""
from __future__ import annotations

import logging

from core.job_runs import JobRunLogger

logger = logging.getLogger(__name__)

TEMA = "saldos_comitentes"
# Por debajo de esto no se avisa: es polvo contable, no una decisión.
MINIMO = 1.0


def _saldos_de_hoy() -> tuple[list[dict], int, int]:
    """Los saldos != 0 con su operador, **tal cual los ve la pantalla**.

    ⚠️ **NO se escribe una query propia sobre `control_saldos`.** La primera
    versión de este job lo hizo y salió distinto de la vista en tres cosas:
    usaba `current_date` en vez de `MAX(fecha)` (así que un día sin corrida del
    daemon devolvía vacío en vez de la última foto), no excluía los `nivel_5`
    CDC/OTC, y tenía un mínimo propio.

    El user lo marcó: *«tiene que trabajar con los datos reales, no puede haber
    algo distinto que en la vista»*. Y el motivo de fondo es más que prolijidad:
    si el aviso y la pantalla no coinciden, el operador no sabe cuál creer y deja
    de creerle a las dos.

    Devuelve (filas con operador, cuántas sin operador, cuántas se ocultaron).
    """
    from api.services import titulos_negativos as tn

    d = tn.saldos_del_dia()
    if not d.get("disponible"):
        return [], 0, 0
    ocultas = int(d.get("ocultas", 0)) + int(d.get("ocultas_manual", 0))
    con, sin = [], set()
    for f in d.get("filas") or []:
        saldo = float(f.get("cantidad") or 0)
        if abs(saldo) < MINIMO:
            continue
        email = (f.get("operador_email") or "").strip().lower()
        if not email:
            # **No desaparecen**: a éstas no le llegan a nadie, y esconderlas
            # sería el mismo silencio que este job persigue.
            sin.add(f["id_cuenta"])
            continue
        con.append({"operador": email, "id_cuenta": f["id_cuenta"],
                    "cuenta": f.get("cuenta") or f["id_cuenta"],
                    "moneda": f.get("ticker") or "?", "saldo": saldo})
    return con, len(sin), ocultas


# ⚠️ **EL TOP 5 POR CUADRANTE, Y POR QUÉ CAMBIA LO QUE SE MANDA.**
#
# El user pidió la vista en cuatro cuadrantes (ARS/USD × positivos/negativos) con
# el top 5 de cada uno. Eso **no es solo un cambio de pantalla**: si la tabla
# mostrara 20 de 435 filas, las otras 415 no se podrían tildar y el aviso no se
# cerraría nunca — quedaría abierto para siempre pidiendo algo imposible.
#
# Así que el aviso LLEVA esas 20 y no las 435. Y está bien que sea así: **435
# cuentas no son un aviso, son un reporte**, y un aviso que pide 435 acciones se
# cierra sin leer. Lo que se manda es lo que hay que atender hoy.
#
# Lo que queda afuera **se dice** (va en el detalle, con dónde verlo). Truncar en
# silencio se lee como «esto es todo lo que hay», que es la mentira que este
# proyecto persigue en todos lados.
TOP = 5

# ARS a la izquierda, DÓLARES a la derecha. `USDL` y `USDC` son dólares también
# (cable y billete): agruparlos con USD evita que desaparezcan de la vista, y la
# fila muestra igual la moneda exacta para que nadie los confunda.
def _grupo(moneda: str) -> str:
    return "ARS" if (moneda or "").upper() == "ARS" else "USD"


def _armar(filas: list[dict]) -> tuple[str, list[dict], int]:
    """(asunto, las 20 filas del modal, cuántas quedaron afuera).

    Cuatro cuadrantes: ARS/USD × positivos/negativos, top 5 de cada uno por
    tamaño. El más negativo primero abajo y el más positivo primero arriba — en
    los dos casos, lo que más pesa arriba de su bloque.
    """
    negativos = [f for f in filas if f["saldo"] < 0]
    cuentas = len({f["id_cuenta"] for f in filas})
    asunto = (f"{len(negativos)} cuenta(s) tuyas EN DESCUBIERTO"
              if negativos else
              f"{cuentas} cuenta(s) tuyas con saldo para revisar")

    tabla: list[dict] = []
    for grupo in ("ARS", "USD"):
        delg = [f for f in filas if _grupo(f["moneda"]) == grupo]
        for signo in ("positivo", "negativo"):
            cuadrante = [f for f in delg
                         if (f["saldo"] > 0) == (signo == "positivo")]
            # El que más pesa, primero: entre los positivos el mayor, entre los
            # negativos el más negativo. `abs` sirve para los dos.
            cuadrante.sort(key=lambda f: -abs(f["saldo"]))
            for f in cuadrante[:TOP]:
                tabla.append({
                    # La CLAVE identifica la fila entre corridas: si el job
                    # vuelve a correr, las que ya se tildaron NO se pierden.
                    "clave": f"{f['id_cuenta']}:{f['moneda']}",
                    "etiqueta": f["cuenta"] or f["id_cuenta"],
                    "datos": {"cuenta": f["cuenta"] or f["id_cuenta"],
                              "id_cuenta": f["id_cuenta"],
                              "moneda": f["moneda"],
                              "saldo": round(f["saldo"], 2),
                              "grupo": grupo, "signo": signo},
                })
    return asunto, tabla, len(filas) - len(tabla)


def main() -> int:
    # ── MODO PRUEBA ─────────────────────────────────────────────────────────
    # `--a vos@…` manda TODO a una sola persona en vez de a cada operador. Es
    # para poder ver el modal con datos reales sin escribirle a nadie más — y
    # sin eso, probar un aviso masivo significa molestar a la mesa.
    import sys
    prueba = ""
    if "--a" in sys.argv:
        i = sys.argv.index("--a")
        prueba = sys.argv[i + 1].strip().lower() if i + 1 < len(sys.argv) else ""
        if "@" not in prueba:
            print("--a necesita un email")
            return 1

    with JobRunLogger("saldos_a_operadores") as jr:
        from api.services import av_agent_mensajes as msg

        filas, huerfanas, ocultas = _saldos_de_hoy()
        if not filas:
            print("no hay saldos distintos de cero hoy — no se manda nada")
            jr.set_stat("enviados", 0)
            jr.set_stat("sin_operador", huerfanas)
            jr.set_stat("ocultas", ocultas)
            return 0

        por_operador: dict[str, list[dict]] = {}
        for f in filas:
            por_operador.setdefault(f["operador"], []).append(f)
        if prueba:
            # Todo junto a una sola persona, y se DICE en el asunto para que
            # nadie confunda una prueba con el aviso real.
            por_operador = {prueba: filas}
            print(f"MODO PRUEBA — todo va a {prueba}, nadie más recibe nada\n")

        # El tema lleva LA FECHA: el mensaje de hoy es uno nuevo aunque el de
        # ayer siga abierto. Sin eso, un operador que no cierra el suyo dejaría
        # de recibir — y el que más los acumula es justo el que más los necesita.
        from datetime import date
        tema = f"{TEMA}:{date.today().isoformat()}"

        enviados = fallaron = 0
        for email, suyas in por_operador.items():
            asunto, tabla, afuera = _armar(suyas)
            # **Va como TABLA y con `interrumpe`**: el user lo pidió explícito —
            # «tiene que ser como el modal de briefing, aparece en la pantalla y
            # te hace hacer algo para continuar, no que aparezca en el cuerpo del
            # agente como si nada».
            #
            # Y VENCE al cierre de la jornada: un aviso de saldos vale HOY;
            # mañana el mercado abre con otros números y pedir acción sobre la
            # foto de ayer es peor que no avisar.
            r = msg.enviar_tabla(
                para=email, tema=f"{tema}:prueba" if prueba else tema,
                asunto=f"[PRUEBA] {asunto}" if prueba else asunto, filas=tabla,
                # **Lo que queda afuera se DICE.** Truncar en silencio se lee
                # como «esto es todo lo que hay».
                detalle=("Las 5 más grandes de cada bloque. Marcá cada una a "
                         "medida que la resolvés — vale por hoy."
                         + (f" Hay {afuera} cuenta(s) más con saldo: están en "
                            f"SALDOS DE CUENTAS." if afuera > 0 else "")),
                donde="SALDOS DE CUENTAS", por="jobs.saldos_a_operadores",
                interrumpe=True)
            if r.get("ok"):
                enviados += 1
            else:
                fallaron += 1
                print(f"   ✖ {email}: {r.get('error')}")

        jr.set_stat("enviados", enviados)
        jr.set_stat("operadores", len(por_operador))
        jr.set_stat("sin_operador", huerfanas)
        jr.set_stat("ocultas", ocultas)
        if fallaron:
            jr.set_stat("fallaron", fallaron)
        print(f"✔ {enviados} operador(es) avisado(s) sobre "
              f"{len({f['id_cuenta'] for f in filas})} cuenta(s)")
        if huerfanas:
            print(f"\n⚠ {huerfanas} cuenta(s) con saldo NO tienen operador "
                  f"asignado: a ésas no le llegan a nadie.")
        if ocultas:
            # Las mismas que la pantalla esconde (nivel_5 CDC/OTC + las que
            # alguien ocultó a mano). Se DICE cuántas son: el aviso y la vista
            # tienen que poder contrastarse.
            print(f"  ({ocultas} cuenta(s) ocultas, igual que en la vista)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
