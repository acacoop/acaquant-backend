"""jobs/curador.py — el ESPEJO del Agente Curador (etapa E1).

Doc madre: **`docs/AGENTE_CURADOR.md`**. Escribe `mercado.curador_hallazgos`.

**Qué hace.** Barre el universo de 1816, lo cruza contra `mercado.curvas` y
persiste los hallazgos de las tres preguntas del agente: qué bono existe en 1816
y no en nuestra base, cuál de los nuestros está sin flujo, y qué tasa está dando
mal. Los detectores viven en `api/services/curador.py` (lógica pura); este
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

Uso:
    python -m jobs.curador                        # alcance soberanos (default), persiste
    python -m jobs.curador --dry-run              # imprime todo, NO escribe ni una fila
    python -m jobs.curador --alcance todo         # los 887 de 1816, no solo soberanos
    python -m jobs.curador --detalle              # lista hallazgo por hallazgo
"""
from __future__ import annotations

import argparse
import json
import logging

from api.services import curador
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
            "INSERT INTO mercado.curador_hallazgos "
            "(alcance, tipo, ticker, regla, severidad, motivo, evidencia) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)", filas)
        cur.execute(
            "DELETE FROM mercado.curador_hallazgos WHERE corrida_at < ("
            "  SELECT min(c) FROM (SELECT DISTINCT corrida_at AS c "
            "    FROM mercado.curador_hallazgos ORDER BY c DESC LIMIT %s) t)",
            (_TTL_CORRIDAS,))
    return len(filas)


def _imprimir(res: dict, detalle: bool) -> None:
    u, resumen = res["universo"], res["resumen"]
    print(f"\n{'=' * 72}")
    print(f"CURADOR — espejo de integridad (alcance: {res['alcance']})")
    print("=" * 72)
    print(f"  Universo 1816: {u['1816']} tickers  ·  mercado.curvas: {u['mio']} bonos"
          f"  ·  con métricas del cierre: {u['con_metricas']}")
    if not u["assets_leidos"]:
        print("  ⚠ portafolio.assets no se pudo leer → la regla `sin_espejo_en_assets` "
              "NO corrió (no se marca lo que no se pudo mirar).")

    tipos = [("falta_en_base", "Están en 1816 y NO en mi base"),
             ("sin_flujo", "Míos SIN cronograma de flujos"),
             ("tasa_sospechosa", "Tasas que pueden estar mal")]
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Espejo de integridad de renta fija (E1)")
    ap.add_argument("--alcance", default="soberanos", choices=sorted(curador.ALCANCES),
                    help="qué emisores mirar (default: soberanos — decisión D1 del doc)")
    ap.add_argument("--dry-run", action="store_true",
                    help="releva e imprime, pero NO escribe ni una fila")
    ap.add_argument("--detalle", action="store_true",
                    help="lista hallazgo por hallazgo, no solo el resumen")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not mercado_1816.disponible():
        print("✗ falta MERCADO_1816_API_KEY en el .env — no se puede censar 1816.")
        return

    saldo_ini = None
    try:
        saldo_ini = (mercado_1816.balance() or {}).get("daily", {}).get("used")
    except Exception:
        pass

    from core.job_runs import JobRunLogger
    with JobRunLogger("curador") as jr:
        jr.log(f"censando 1816 (alcance={args.alcance}) — ~29 créditos, "
               "puede tardar 1-2 min por el throttle")
        res = curador.relevar(alcance=args.alcance)
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
            print(f"\n✔ {n} hallazgos persistidos en mercado.curador_hallazgos "
                  f"(se conservan las últimas {_TTL_CORRIDAS} corridas)")

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
