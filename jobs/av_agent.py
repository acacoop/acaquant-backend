"""jobs/av_agent.py — el ESPEJO del AV Agent (etapa E1).

Doc madre: **`docs/AV_AGENT.md`**. Escribe `mercado.av_agent_hallazgos`.

**Qué hace.** Barre el universo de 1816, lo cruza contra `mercado.curvas` y
persiste los hallazgos de las tres preguntas del agente: qué bono existe en 1816
y no en nuestra base, cuál de los nuestros está sin flujo, y qué tasa está dando
mal. Los detectores viven en `api/services/av_agent.py` (lógica pura); este
archivo es orquestación + persistencia.

**Lo que NO hace, a propósito:**
  · **No escribe en `mercado.curvas`.** Ni una fila. Solo su tabla propia.
  · **No llama a ningún LLM.** El diagnóstico con IA es E4, y va DESPUÉS de que
    estas reglas estén calibradas contra la realidad: ponerle un modelo encima a
    un detector que tira ruido solo produce ruido mejor redactado.
  · **No pide precios.** Ni `/indicadores` ni `/series`, que son los endpoints
    caros. Las tasas salen de `mercado.market_snapshot`, que ya calcula el motor.

**Costo medido: ~29 créditos por corrida** (1 `/curvas` + 28 `/instrumentos`),
contra un tope de 100.000/día. El censo respeta el throttle de 2,5 s del cliente,
así que la corrida tarda ~1-2 minutos: es un job de fondo, no de rueda.

**Para qué sirve la primera corrida.** Para CALIBRAR. El número que importa no es
cuántos hallazgos salen sino **cuántos son reales**: si de 20 tasas sospechosas
18 son ruido, las reglas están mal y se ajustan ANTES de seguir con E2. Por eso
el resumen imprime el desglose por REGLA — es el que dice cuál está mintiendo.

**El agente PREGUNTA** (E1.c). Cuando encuentra algo que no puede decidir solo
—"apareció TZXD8 en 1816, ¿lo damos de alta o no nos interesa?"— no se bloquea:
deja la pregunta anotada y sigue. Se contestan con `--responder`, cuando el user
pueda; responder **dispara el efecto** (un `ignorar` hace que ese ticker no
vuelva a salir nunca) y el agente **no repregunta** lo ya preguntado.

Uso:
    python -m jobs.av_agent                        # alcance soberanos (default), persiste
    python -m jobs.av_agent --dry-run              # imprime todo, NO escribe ni una fila
    python -m jobs.av_agent --alcance todo         # los 887 de 1816, no solo soberanos
    python -m jobs.av_agent --detalle              # lista hallazgo por hallazgo

    # las preguntas del agente (gratis: no releva, no pega a 1816)
    python -m jobs.av_agent --preguntas
    python -m jobs.av_agent --responder "3=alta,5-9=ignorar" --por vos@acaquant.com
    python -m jobs.av_agent --responder "7=ignorar" --nota "bono viejo, no lo operamos"
"""
from __future__ import annotations

import argparse
import json
import logging

from api.services import av_agent
from api.services import av_agent_preguntas as preg
from core import mercado_1816
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Cuántas corridas se conservan. El valor de la tabla es la HISTORIA (¿esto apareció
# hoy o está hace un mes?), pero esa historia se lee en semanas, no en años, y sin
# purga una corrida diaria de ~50 hallazgos son ~18k filas/año de datos que nadie
# consulta. Se purga en la misma transacción del insert (patrón de las fotos de
# Tesorería) para que no haga falta otro cron que se pueda olvidar de correr.
_TTL_CORRIDAS = 60


def persistir(res: dict) -> int:
    """Inserta los hallazgos de UNA corrida y purga las viejas. → filas escritas.

    Todo en una transacción: si el insert falla, no queda una corrida a medias que
    parezca "hoy no encontró nada" — que es la peor mentira posible en una
    herramienta de integridad."""
    hallazgos = res.get("hallazgos") or []
    if not hallazgos:
        return 0
    filas = [(res.get("alcance") or "", h["tipo"], h["ticker"], h["regla"],
              h["severidad"], h["motivo"], json.dumps(h.get("evidencia") or {},
                                                      ensure_ascii=False, default=str))
             for h in hallazgos]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO mercado.av_agent_hallazgos "
            "(alcance, tipo, ticker, regla, severidad, motivo, evidencia) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)", filas)
        cur.execute(
            "DELETE FROM mercado.av_agent_hallazgos WHERE corrida_at < ("
            "  SELECT min(c) FROM (SELECT DISTINCT corrida_at AS c "
            "    FROM mercado.av_agent_hallazgos ORDER BY c DESC LIMIT %s) t)",
            (_TTL_CORRIDAS,))
    return len(filas)


def _imprimir(res: dict, detalle: bool) -> None:
    u, resumen = res["universo"], res["resumen"]
    print(f"\n{'=' * 72}")
    print(f"AV AGENT — espejo de integridad (alcance: {res['alcance']})")
    print("=" * 72)
    print(f"  Universo 1816: {u['1816']} tickers  ·  mercado.curvas: {u['mio']} bonos"
          f"  ·  con métricas del cierre: {u['con_metricas']}")
    print(f"  En cartera (último AuM): {u.get('en_cartera', 0)}"
          f"  ·  ignorados por el user: {u.get('ignorados', 0)}")
    # Una fuente que no se pudo leer APAGA su regla, y eso hay que decirlo: si no,
    # una lista más corta se lee como "hay menos problemas" cuando en realidad es
    # "miré menos cosas".
    for ok, fuente, regla in ((u["assets_leidos"], "portafolio.assets", "sin_espejo_en_assets"),
                              (u.get("cartera_leida"), "portafolio.tenencia",
                               "sin_espejo_en_assets")):
        if not ok:
            print(f"  ⚠ {fuente} no se pudo leer → la regla `{regla}` NO corrió "
                  "(no se marca lo que no se pudo mirar).")

    # Los tipos se DERIVAN de lo que vino, no se listan a mano. La lista fija dejó
    # el tipo `hueco_de_curva` contado en el total pero fuera del resumen y del
    # detalle (2026-08-16): 59 hallazgos arriba y 58 en los bloques. Un hallazgo
    # que el reporte no imprime es un hallazgo que no existe — justo el modo de
    # falla que ese detector vino a denunciar.
    _ETIQUETAS = {
        "hueco_de_curva": "Le falta AL SISTEMA (no es un dato mal cargado)",
        "falta_en_base": "Están en 1816 y NO en mi base",
        "sin_flujo": "Míos SIN cronograma de flujos",
        "tasa_sospechosa": "Tasas que pueden estar mal",
    }
    _ORDEN = list(_ETIQUETAS)
    vistos = {h["tipo"] for h in res["hallazgos"]}
    tipos = [(t, _ETIQUETAS.get(t, t)) for t in _ORDEN if t in vistos]
    # Un tipo nuevo sin etiqueta igual se imprime, con su nombre crudo: preferimos
    # una fila fea a una fila que falta.
    tipos += [(t, t) for t in sorted(vistos) if t not in _ETIQUETAS]
    print(f"\n{'HALLAZGO':<38}{'N':>6}")
    print("─" * 44)
    for tipo, label in tipos:
        print(f"{label:<38}{resumen.get(tipo, 0):>6}")
    print("─" * 44)
    print(f"{'TOTAL':<38}{len(res['hallazgos']):>6}")

    # El desglose por REGLA es el que permite calibrar: una regla que se lleva
    # media lista es la primera sospechosa de estar tirando falsos positivos.
    reglas = sorted(((k.split(":", 1)[1], v) for k, v in resumen.items()
                     if k.startswith("regla:")), key=lambda x: -x[1])
    if reglas:
        print(f"\n{'POR REGLA (para calibrar)':<38}{'N':>6}")
        print("─" * 44)
        for r, n in reglas:
            print(f"{r:<38}{n:>6}")

    if detalle:
        for tipo, label in tipos:
            hs = [h for h in res["hallazgos"] if h["tipo"] == tipo]
            if not hs:
                continue
            print(f"\n── {label} ({len(hs)}) " + "─" * max(0, 48 - len(label)))
            for h in sorted(hs, key=lambda x: (x["severidad"], x["ticker"])):
                print(f"  [{h['severidad']:<5}] {h['ticker']:<10} {h['regla']}")
                print(f"           {h['motivo']}")


def _imprimir_preguntas(abiertas: list[dict]) -> None:
    """Las preguntas del agente, con el ID que se usa para contestarlas.

    Van al FINAL de la corrida y no al principio: primero lo que el agente hizo,
    después lo que necesita. Un agente que arranca pidiendo se siente un
    formulario."""
    if not abiertas:
        print("\n✔ El AV Agent no tiene preguntas abiertas.")
        return
    decisiones = [p for p in abiertas if p["tipo"] == "decision"]
    hallazgos = [p for p in abiertas if p["tipo"] != "decision"]

    print(f"\n{'=' * 72}\n❓ EL AV AGENT TE PREGUNTA ({len(abiertas)})")
    print("=" * 72)
    for grupo, titulo in ((decisiones, "DECISIONES DE DISEÑO (definen cómo trabaja)"),
                          (hallazgos, "SOBRE LO QUE ENCONTRÓ")):
        if not grupo:
            continue
        print(f"\n── {titulo} " + "─" * max(0, 50 - len(titulo)))
        for p in grupo:
            print(f"  [{p['id']:>3}] {p['pregunta']}")
            print(f"        → {' | '.join(p['opciones'])}")
    print("\n  Para contestar (acepta rangos, y podés contestar solo algunas):")
    ej = abiertas[0]["id"]
    print(f"    python -m jobs.av_agent --responder \"{ej}={ej and abiertas[0]['opciones'][0]}\"")
    if len(abiertas) > 2:
        ult = abiertas[-1]["id"]
        print(f"    python -m jobs.av_agent --responder \"{ej}=alta,{ej + 1}-{ult}=ignorar\"")
    print("  Lo que no contestes queda abierto: el agente NO vuelve a preguntarlo "
          "cada noche.")


def _imprimir_estado() -> None:
    """Qué se decidió y qué falta que surta efecto.

    Una decisión tomada que no se ve en ningún lado se siente como una decisión
    perdida: el user contestó 12 altas y no tenía dónde mirar qué pasó con ellas.
    """
    r = preg.resumen()
    pend = preg.pendientes_de_aplicar()
    print(f"\n{'=' * 72}\nAV AGENT — ESTADO DE LO DECIDIDO")
    print("=" * 72)
    print(f"  Preguntas abiertas: {r['abiertas']}  ·  respondidas: {r['respondidas']}")
    if not pend:
        print("\n  ✔ No queda ninguna respuesta sin aplicar.")
        return

    por_resp: dict[str, list[dict]] = {}
    for d in pend:
        por_resp.setdefault(d["respuesta"] or "?", []).append(d)

    print(f"\n  ⏳ {len(pend)} respuesta(s) GUARDADAS que todavía NO surtieron efecto:")
    for resp, filas in sorted(por_resp.items()):
        tks = ", ".join(sorted(d["ticker"] for d in filas))
        print(f"\n   «{resp}» ({len(filas)}): {tks}")
        if resp == "alta":
            print("     Falta E2: dar de alta necesita bajar el cuadro de flujos de "
                  "1816 y simular la TEA antes de escribir. Estas son exactamente "
                  "las que va a procesar.")
        else:
            print("     Su efecto lo aplica la etapa que corresponda.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Espejo de integridad de renta fija (E1)")
    ap.add_argument("--alcance", default="soberanos", choices=sorted(av_agent.ALCANCES),
                    help="qué emisores mirar (default: soberanos — decisión D1 del doc)")
    ap.add_argument("--dry-run", action="store_true",
                    help="releva e imprime, pero NO escribe ni una fila")
    ap.add_argument("--detalle", action="store_true",
                    help="lista hallazgo por hallazgo, no solo el resumen")
    ap.add_argument("--responder", metavar="ID=RESP",
                    help="contesta preguntas y aplica su efecto, sin relevar. "
                         "Acepta lista y rangos: \"3=alta,5-9=ignorar\"")
    ap.add_argument("--nota", default="",
                    help="el POR QUÉ de la respuesta (queda guardado; es lo que "
                         "el agente va a usar para no volver a proponerlo)")
    ap.add_argument("--por", default="", help="tu email, para la trazabilidad")
    ap.add_argument("--preguntas", action="store_true",
                    help="solo muestra las preguntas abiertas (0 créditos)")
    ap.add_argument("--estado", action="store_true",
                    help="qué contestaste y qué falta aplicar (0 créditos)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Contestar y ver preguntas NO releva: son gratis y no tocan a 1816. Sin esto,
    # responder tres preguntas costaría un censo entero y dos minutos de espera.
    if args.responder:
        try:
            r = preg.responder_lote(args.responder, por=args.por, nota=args.nota)
        except ValueError as e:
            print(f"✗ {e}")
            return
        print(f"✔ {r['ok']} respondida(s) · {r['omitidos']} id(s) inexistente(s) "
              f"· {r['fallidos']} con error")
        for d in r["detalle"]:
            if d.get("error"):
                print(f"   ✗ [{d['id']}] {d['error']}")
            elif d.get("ok") and d.get("aplicada"):
                print(f"   ✔ [{d['id']}] {d['respuesta']} — aplicado (no vuelve a salir)")
            elif d.get("ok"):
                print(f"   ✔ [{d['id']}] {d['respuesta']} — guardado "
                      "(el efecto lo aplica la etapa que corresponda)")
        _imprimir_preguntas(preg.abiertas())
        return

    if args.preguntas:
        _imprimir_preguntas(preg.abiertas())
        return

    if args.estado:
        _imprimir_estado()
        return

    if not mercado_1816.disponible():
        print("✗ falta MERCADO_1816_API_KEY en el .env — no se puede censar 1816.")
        return

    saldo_ini = None
    try:
        saldo_ini = (mercado_1816.balance() or {}).get("daily", {}).get("used")
    except Exception:
        pass

    from core.job_runs import JobRunLogger
    with JobRunLogger("av_agent") as jr:
        jr.log(f"censando 1816 (alcance={args.alcance}) — ~29 créditos, "
               "puede tardar 1-2 min por el throttle")
        res = av_agent.relevar(alcance=args.alcance)
        _imprimir(res, args.detalle)

        for k, v in res["resumen"].items():
            if not k.startswith("regla:"):
                jr.set_stat(k, v)
        jr.set_stat("hallazgos", len(res["hallazgos"]))
        jr.set_stat("alcance", args.alcance)
        jr.set_stat("universo_1816", res["universo"]["1816"])
        jr.set_stat("universo_mio", res["universo"]["mio"])

        # Que 1816 no conteste NO es "no encontré nada": es "no pude mirar", y la
        # diferencia es exactamente cómo un monitoreo miente en verde.
        if not res["universo"]["1816"]:
            jr.error("el censo de 1816 volvió VACÍO — no se pudo verificar contra "
                     "la fuente; los faltantes de esta corrida no son concluyentes")

        if args.dry_run:
            print("\n(DRY-RUN — no se escribió ninguna fila)")
        else:
            n = persistir(res)
            jr.set_stat("filas_persistidas", n)
            print(f"\n✔ {n} hallazgos persistidos en mercado.av_agent_hallazgos "
                  f"(se conservan las últimas {_TTL_CORRIDAS} corridas)")

        # Las PREGUNTAS se registran SIEMPRE, incluso en dry-run: no son un
        # resultado del relevamiento sino una conversación pendiente, y perderlas
        # porque la corrida fue de prueba obligaría a repetir el censo para
        # recuperarlas. `ON CONFLICT (clave)` hace que sea idempotente.
        try:
            nuevas = preg.registrar(preg.preguntas_de_hallazgos(res["hallazgos"]))
            nuevas += preg.registrar_decisiones(preg.DECISIONES_ABIERTAS)
            jr.set_stat("preguntas_nuevas", nuevas)
            estado = preg.resumen()
            jr.set_stat("preguntas_abiertas", estado["abiertas"])
            _imprimir_preguntas(preg.abiertas())
        except Exception as e:
            # Que falle el canal de preguntas no puede tirar la corrida: los
            # hallazgos ya están y valen por sí solos.
            jr.error(f"no se pudieron registrar las preguntas: {type(e).__name__}: {e}")

        if saldo_ini is not None:
            try:
                fin = (mercado_1816.balance() or {}).get("daily", {})
                usado = fin.get("used")
                if usado is not None:
                    jr.set_stat("creditos_1816", usado - saldo_ini)
                    print(f"  Créditos 1816 usados: {usado - saldo_ini} "
                          f"(día {usado}/{fin.get('limit')})")
            except Exception:
                pass


if __name__ == "__main__":
    main()
