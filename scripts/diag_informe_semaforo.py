"""Por qué ACTIVAS + ENFRIÁNDOSE del INFORME da 0 — diag READ-ONLY.

Contesta tres cosas, en este orden, y la primera que falle explica todo:

  1. ¿Está deployado el código nuevo? (`ctas_semaforo` en la respuesta del servicio).
     Si NO, el Informe recibe el campo vacío y el gráfico lo dibujaba en cero:
     la respuesta es `cd /root/TradingAV && git pull && bash deploy/deploy.sh`.
  2. ¿Qué devuelve el INFORME por segmento? (cuentas, ctas ops, semáforo).
  3. ¿Coincide con ANÁLISIS COMERCIAL, que cuenta lo mismo por otro camino?
     Si el Informe da 0 y Análisis no, la diferencia está en la query del Informe y
     el diag imprime la ventana y el conteo crudo para ubicarla.

No escribe nada. Correr:  python -m scripts.diag_informe_semaforo
"""
from __future__ import annotations

from api.services import comercial_sql as cs
from api.services._sql import _q


def main() -> None:
    print("=" * 78)
    print("DIAG — ACTIVAS + ENFRIÁNDOSE en el INFORME")
    print("=" * 78)

    # 1) ¿Corre el código nuevo?
    tiene_const = hasattr(cs, "DIAS_ACTIVA") and hasattr(cs, "DIAS_DORMIDA")
    print(f"\n[1] Código nuevo cargado: {'SÍ' if tiene_const else 'NO'}")
    if not tiene_const:
        print("    → el proceso está corriendo una versión vieja de comercial_sql.")
        print("    → cd /root/TradingAV && git pull && bash deploy/deploy.sh")
        return
    print(f"    umbrales: ACTIVA ≤ {cs.DIAS_ACTIVA} días · DORMIDA > {cs.DIAS_DORMIDA} días")

    # 2) Lo que ve el INFORME, sin ningún filtro (= lo que muestra la pantalla al abrir).
    r = cs.informe_cuentas_por_segmento()
    hay_campo = all("ctas_semaforo" in s for s in r["segmentos"]) if r["segmentos"] else False
    print(f"\n[2] INFORME — corte {r['mes']} · {len(r['segmentos'])} segmentos")
    print(f"    campo `ctas_semaforo` presente: {'SÍ' if hay_campo else 'NO'}")
    print(f"    {'SEGMENTO':<26} {'CUENTAS':>8} {'OPS MES':>8} {'OPS AÑO':>8} {'SEMÁFORO':>9}")
    for s in r["segmentos"]:
        print(f"    {s['segmento'][:26]:<26} {s['n']:>8} {s['ctas_ops']:>8} "
              f"{s['ctas_ops_ano']:>8} {s.get('ctas_semaforo', '—'):>9}")
    print(f"    {'TOTAL':<26} {r['total']:>8} {r['total_ctas_ops']:>8} "
          f"{r['total_ctas_ops_ano']:>8} {r.get('total_ctas_semaforo', '—'):>9}")

    if r.get("total_ctas_semaforo"):
        print("\n    → el backend SÍ devuelve el número. Si la pantalla muestra 0, lo que")
        print("      está viejo es el front (Vercel) o quedó cacheada la respuesta: F5.")

    # 3) El MISMO número por el camino de ANÁLISIS COMERCIAL (otra query, otra ruta).
    a = cs.analisis_comercial(operador=[])
    act = sum(1 for c in a["clientes"] if c["estado"] == "ACTIVA")
    enf = sum(1 for c in a["clientes"] if c["estado"] == "ENFRIANDOSE")
    print(f"\n[3] ANÁLISIS COMERCIAL — {len(a['clientes'])} clientes")
    print(f"    ACTIVAS {act} + ENFRIÁNDOSE {enf} = {act + enf}")
    print(f"    INFORME dice: {r.get('total_ctas_semaforo', '—')}")
    if r.get("total_ctas_semaforo") != act + enf:
        print("    ⚠️ NO COINCIDEN. Los dos caminos cuentan distinto — ver [4].")

    # 4) Conteo CRUDO con la misma ventana, sin pasar por ninguna de las dos funciones.
    corte = cs._hoy_art()
    ini = corte - __import__("datetime").timedelta(days=cs.DIAS_DORMIDA)
    crudo = _q(
        "SELECT count(DISTINCT o.id_cuenta) AS n FROM operaciones o "
        "JOIN comitentes c ON c.id_cuenta = o.id_cuenta AND c.estado = 'Activa' "
        "WHERE o.anulado_en IS NULL AND o.concertacion >= %(ini)s AND o.concertacion <= %(fin)s",
        {"ini": ini, "fin": corte})[0]["n"]
    print(f"\n[4] CRUDO — cuentas activas con ≥1 boleto no anulado en [{ini}, {corte}]: {crudo}")
    print("    (el Informe usa como corte el FIN del mes en curso, así que su ventana")
    print("     arranca un poco antes y puede dar un número algo mayor que este)")
    print()


if __name__ == "__main__":
    main()
