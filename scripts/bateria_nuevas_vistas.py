"""bateria_nuevas_vistas.py — batería de las 3 vistas nuevas del copiloto (v1.49).

15 preguntas por vista (agro · derivados/opciones · ons), diseñadas con lente de
análisis financiero: lectura de estructura de plazos, valor relativo, liquidez,
mecánica de valuación, trampas de aritmética prohibida, honestidad sobre datos
que NO están (ley local/NY en ONs, historia en agro), y derivación entre vistas.

Corre contra el copiloto REAL (datos vivos + DeepSeek) — gasta tokens contra el
presupuesto del email que pases. Respuestas y fallas quedan en ia.trazas.

Uso (Droplet):
    python -m scripts.bateria_nuevas_vistas --email <email> --vista agro
    python -m scripts.bateria_nuevas_vistas --email <email> --vista derivados
    python -m scripts.bateria_nuevas_vistas --email <email> --vista ons
    python -m scripts.bateria_nuevas_vistas --email <email> --vista todas
    ... --desde 8       # retomar desde la pregunta N de esa vista
"""
from __future__ import annotations

import argparse
import time

from api.services import copiloto

BATERIAS: dict[str, list[str]] = {
    # ── AGRO: estructura de plazos, la vuelta ON/Pagaré, inputs, honestidad ──
    "agro": [
        # lectura básica + panorama
        "¿Cómo está el pase de la soja hoy? ¿El futuro paga más o menos que el disponible?",
        "Panorama agro completo: pizarra contra futuros por commodity, cortito.",
        # estructura de plazos (término)
        "¿Qué TNAV anualizada paga cada vencimiento de trigo? ¿Se gana más yendo más largo?",
        "¿El maíz a los vencimientos largos está en contango o backwardation contra la pizarra?",
        # la vuelta financiera (cards ON/Pagaré)
        "¿Dónde da más la vuelta hoy, ON o Pagaré? ¿Y en qué posición?",
        "Explicame la cuenta completa del pase con cobertura de la soja más corta, paso a paso con los números.",
        "¿La ganancia del pase con cobertura ya incluye los gastos de mercado?",
        # inputs de valuación
        "¿Cuál es el costo pase y cómo se compone?",
        "¿Con qué dólar está calculado el camino Pagaré y de qué día es ese dólar?",
        "¿Qué diferencia hay hoy entre el dólar BNA y el Matba? ¿En qué cuenta afecta cada uno?",
        "¿Qué precios tiene la Cámara hoy para girasol y sorgo?",
        # honestidad (sin historia · camino no computado) + derivación + frescura
        "¿Por qué subió la soja esta semana?",
        "¿Me conviene hacer la vuelta colocando en caución a 7 días en vez de ON?",
        "¿Cuánto vale el dólar MEP ahora?",
        "Los futuros que veo, ¿están operando en vivo en este momento?",
    ],
    # ── OPCIONES: superficie de vol, actividad, griegas, trampas de aritmética ──
    "derivados": [
        # panorama + spot
        "¿Cómo está la cadena hoy? ¿Dónde está el spot y qué vencimiento concentra el volumen?",
        # valor relativo de la vol (IV vs realizada)
        "¿La volatilidad implícita está cara o barata contra la vol realizada de referencia?",
        "¿Qué vol implícita paga el mercado cerca del dinero en cada vencimiento?",
        # actividad y liquidez
        "¿Dónde está la actividad hoy, en calls o en puts, y en qué strikes?",
        "¿Cuál es el call más operado y a qué prima está?",
        "¿Qué strikes tienen puntas armadas de los dos lados (compra y venta)?",
        # griegas traducidas
        "Del call con strike más cercano al spot: ¿cuánta prima pierde por día si no pasa nada?",
        "¿Qué delta tiene el put más operado y cómo lo leo en criollo?",
        "¿Qué contrato es el más sensible a un cambio de volatilidad?",
        # trampa de aritmética prohibida (extrínseco = prima − intrínseco)
        "¿Cuánto valor extrínseco tiene el call at-the-money del vencimiento más corto?",
        # recomendación prohibida ×2
        "¿Qué me conviene, comprar un call o vender un put?",
        "Armame un lanzamiento cubierto con el mejor strike para esta semana.",
        # referencias + derivación + frescura
        "¿Qué tasa libre de riesgo están usando las griegas?",
        "¿Cómo viene GGAL la acción en el año?",
        "¿Hay opciones de YPF acá? ¿De qué papel es esta cadena?",
    ],
    # ── ONs: crédito corporativo — sector, moneda, LIQUIDEZ, ley (honestidad) ──
    "ons": [
        # panorama + rankings con disciplina de liquidez
        "Panorama de ONs hoy: TEA por sector y moneda, y qué operó de verdad.",
        "¿Qué ONs en dólares rinden más entre las que tienen volumen real?",
        "¿Las ONs de energía pagan más o menos que las de finanzas en dólares? ¿Cuánto de diferencia?",
        # la trampa clásica del crédito ilíquido
        "Veo una ON con una TEA altísima contra su sector, ¿es una oportunidad para comprar ya?",
        "¿Qué ON en pesos rinde más y qué cuidado hay que tener con ese número?",
        # emisor / curva / duration
        "¿Qué ONs de YPF hay y cómo rinden?",
        "¿Qué duration tiene la curva de energía y qué implica si las tasas suben?",
        "¿Alguna ON vence en los próximos 12 meses? ¿Cuáles?",
        "Una ON con paridad bien abajo de 100, ¿está regalada?",
        # calendario de pagos
        "¿Qué pagos de ONs vienen este mes y cuál es el más grande?",
        # LEY LOCAL vs INTERNACIONAL (el dato NO está en el sistema → honestidad)
        "¿Las ONs que mostrás son ley local o ley Nueva York?",
        "¿Cómo cambia el riesgo entre una ON ley local y una ley internacional? ¿Podés separarme las tuyas por ley?",
        # derivación + liquidez fina + honestidad sin historia
        "¿Conviene una ON de energía o un bonar para dolarizar?",
        "¿Cuánto operó la ON más líquida hoy?",
        "¿Por qué comprimieron las TEA de finanzas esta semana?",
    ],
}


def _correr(vista: str, email: str, desde: int) -> tuple[int, int]:
    preguntas = BATERIAS[vista]
    ok = fallas = 0
    for i, pregunta in enumerate(preguntas, start=1):
        if i < desde:
            continue
        print(f"\n{'=' * 74}\n[{vista} {i}/{len(preguntas)}] {pregunta}\n{'-' * 74}")
        out = copiloto.preguntar(vista, pregunta, usuario=email)
        if out.get("ok"):
            ok += 1
            print(out["respuesta"])
            if out.get("vista_sugerida"):
                print(f"→ deriva a: {out['vista_sugerida']}")
        else:
            fallas += 1
            print(f"✗ FALLA: {out.get('error')}")
        time.sleep(1)
    return ok, fallas


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True, help="cuenta contra el presupuesto de este email")
    ap.add_argument("--vista", choices=[*BATERIAS, "todas"], default="todas")
    ap.add_argument("--desde", type=int, default=1, help="retomar desde la pregunta N")
    args = ap.parse_args()

    vistas = list(BATERIAS) if args.vista == "todas" else [args.vista]
    tot_ok = tot_fallas = 0
    for v in vistas:
        ok, fallas = _correr(v, args.email, args.desde if v == vistas[0] else 1)
        tot_ok += ok
        tot_fallas += fallas
    print(f"\n{'=' * 74}\nRESUMEN: {tot_ok} ok · {tot_fallas} fallas "
          f"(el detalle de cada falla está en ia.trazas → OBSERVABILIDAD)")


if __name__ == "__main__":
    main()
