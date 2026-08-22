"""Siembra las LECCIONES que ya aprendimos, para que el agente las muestre.

**Por qué existe** (user, 2026-08-17): *«no puede quedar nada desperdiciado: tiene
que quedar todo, cómo se va construyendo la solución, los errores que detecto,
cómo se fue modificando… porque ahora es bonos, pero después está SALUD y van a
venir más cosas.»*

Hasta hoy cada lección vivía en un chat: se detectaba un error, se arreglaba, y el
aprendizaje se evaporaba. Las de abajo son las de la sesión del 2026-08-17 —
**todas salieron de errores reales**, y varias las cazó el user mirando la
pantalla, no un test.

Cada una se cuenta en cuatro partes, que es el ciclo completo:

    SÍNTOMA      lo que se veía
    CAUSA RAÍZ   qué pasaba de verdad
    CAMBIO       qué se modificó
    COMMIT       dónde quedó

**Idempotente**: `slug` es la identidad, así que re-correrlo actualiza en vez de
duplicar. Una lección se refina.

    python -m scripts.sembrar_lecciones
    python -m scripts.sembrar_lecciones --listar
"""

from __future__ import annotations

import sys

from api.services.av_agent_memoria import guardar_leccion, lecciones_de

LECCIONES = [
    {
        "slug": "proporcion-como-senal-de-falso-positivo",
        "causa": "cotiza_en_pesos", "dominio": "bono",
        "titulo": "46 de 230 no es un hallazgo, es un detector mal calibrado",
        "sintoma": "el monitor de rueda, en su primera corrida real, marcó 46 "
                   "bonos de 230 con severidad ALTA por «el precio llega en la "
                   "moneda equivocada».",
        "causa_raiz": "el detector comparaba el precio crudo contra la banda de "
                      "paridad y concluía que estaba mal, sin preguntarse qué hace "
                      "el motor con ese precio. `engines/curvas.py::"
                      "precio_soberano_a_usd` YA divide por el MEP cuando el "
                      "símbolo no termina en D/C, así que en los 46 la TEA y la "
                      "paridad estaban BIEN — el propio user lo había dicho: «por "
                      "más que la tasa y eso esté bien». Lo real era otra cosa y "
                      "más chica: la GRILLA muestra el precio crudo, así que "
                      "102.700 (pesos) y 74,19 (dólares) conviven en la misma "
                      "columna sin que nada lo diga.",
        "cambio": "la regla se partió en dos con severidades distintas: "
                  "`cotiza_en_pesos` (BAJA, es contexto — y lo accionable es "
                  "nombrar la pata D que sí mostraría dólares) y "
                  "`precio_fuera_de_escala` (ALTA, solo cuando el símbolo TERMINA "
                  "en D/C y aun así se va de rango: ahí no hay conversión que lo "
                  "explique). **La proporción es la señal**: cuando un detector "
                  "marca un tercio del universo en rojo, la hipótesis más probable "
                  "no es que un tercio esté roto — es que el detector no sabe qué "
                  "hace el código que está juzgando. Y un detector que grita en 46 "
                  "casos sanos no es estricto: enseña a ignorar la lista.",
        "detectado_por": "user",
    },
    {
        "slug": "moneda-flujo-vs-ejes",
        "causa": "moneda_flujo_contradice", "dominio": "bono",
        "titulo": "Dos vocabularios para el mismo hecho",
        "sintoma": "paridad de 156.570% en un bono cuyo cuadro estaba perfecto, y "
                   "el XIRR sin converger.",
        "causa_raiz": "`rama_calculo` se migró a los EJES el 2026-08-16 pero la "
                      "rama ON del motor sigue despachando por `moneda_flujo`, un "
                      "campo aparte cargado a mano. Cuando divergen, el motor "
                      "calcula con uno y la vista clasifica con el otro, **sin dar "
                      "ningún error**. Cuatro bonos tenían `HD` —el vocabulario de "
                      "la CARTERA— que el `else` del motor convierte en ARS en "
                      "silencio. Censo: 30 de 140 bonos de la rama ON.",
        "cambio": "regla `moneda_flujo_contradice` que mira el DEFECTO (dos campos "
                  "del mismo doc) en vez del síntoma, así no necesita precio ni "
                  "1816; `moneda_flujo_esperada()` vive pegada al `if` que la "
                  "consume; y el arreglo se verifica localmente antes de escribir.",
        "detectado_por": "agente",
    },
    {
        "slug": "primero-moneda-despues-pata",
        "causa": "moneda_flujo_contradice", "dominio": "bono",
        "titulo": "El orden de las causas ES el contrato",
        "sintoma": "el diag proponía «cambiá la pata» en 14 hallazgos.",
        "causa_raiz": "con `moneda_flujo` mal, TODO lo que se mida después está "
                      "medido en la unidad equivocada. La pata de LOC6O estaba "
                      "perfecta: el símbolo termina en «O» y `precio_soberano_a_usd` "
                      "lo habría dividido por MEP sin problema. Aplicar ese arreglo "
                      "habría pisado un dato sano para tapar el síntoma de otro.",
        "cambio": "la causa la fija la lente que falla **más aguas arriba**, y ese "
                  "orden quedó congelado por test.",
        "detectado_por": "agente",
    },
    {
        "slug": "fosiles-del-snapshot",
        "causa": "falta_cer", "dominio": "bono",
        "titulo": "El snapshot conserva métricas que el motor ya no calcula",
        "sintoma": "CO3D7 con paridad 417%, PMA28 con 200%, TMF27 con 4.789%.",
        "causa_raiz": "`mercado.market_snapshot` es un upsert PARCIAL: cuando el "
                      "motor sale por una puerta de emergencia (CER sin índice → "
                      "`curvas.py:439`, rama `otros` → `:733`, XIRR fuera de rango) "
                      "escribe **solo `duration`**, y la TEA y la paridad viejas se "
                      "quedan ahí **sin fecha propia**. El hallazgo lo disparaba un "
                      "número de otra época, no el bono de hoy.",
        "cambio": "`falta_cer` pasó a evaluarse ANTES que la forma del cuadro, y el "
                  "diagnóstico avisa que la métrica guardada es un fósil.",
        "detectado_por": "agente",
    },
    {
        "slug": "el-agente-se-contradice",
        "causa": "", "dominio": "",
        "titulo": "Dos lentes de la misma pantalla diciendo cosas opuestas",
        "sintoma": "en OLC3O, la lente del precio decía «ninguna fuente local tiene "
                   "un precio mayor que 0» y tres pasos más abajo la MISMA pantalla "
                   "mostraba «precio 137.280 (snapshot)».",
        "causa_raiz": "a las lentes se les pasaba `antes`, un dict con solo "
                      "tea/paridad/duration — **sin la clave `precio`**. La lente "
                      "miraba algo que nunca llegaba. No era un bono mal cargado: "
                      "era el agente contradiciéndose, que es peor, porque destruye "
                      "la confianza en todo lo demás que dice.",
        "cambio": "las lentes reciben el MISMO precio que usa la cadena, y además "
                  "`av_agent_memoria.contradicciones()` busca estas incoherencias "
                  "sola en cada diagnóstico — el bug pasó tests, lint y una lectura "
                  "humana; lo único que lo caza es comparar dos frases separadas "
                  "por seis renglones, y eso lo hace mejor una máquina.",
        "detectado_por": "user",
    },
    {
        "slug": "1816-no-es-el-insumo",
        "causa": "escala_del_cuadro", "dominio": "bono",
        "titulo": "Agotar lo local antes de salir a la red",
        "sintoma": "con 1816 devolviendo 429, la pantalla no decía NADA: ni "
                   "siquiera lo que sale de una división.",
        "causa_raiz": "el agente sabía hacer UN solo arreglo —traer el cronograma "
                      "de 1816 y pisar los flujos— así que las cinco reglas de tasa "
                      "pasaban por la misma cadena y sus dos puertas de 1816 la "
                      "bloqueaban entera. Medido: **38 de 38 hallazgos se resuelven "
                      "con datos que ya están en la base**. 1816 no era el insumo, "
                      "era una costumbre.",
        "cambio": "el diagnóstico local corre PRIMERO y, si la causa tiene arreglo "
                  "local, no se hace una sola llamada. La verificación también es "
                  "local: la métrica tiene que volver al rango o no se escribe.",
        "detectado_por": "user",
    },
    {
        "slug": "backoff-contra-una-cuota",
        "causa": "", "dominio": "",
        "titulo": "El backoff es la respuesta correcta a un rate limit y la peor a una cuota",
        "sintoma": "«auth HTTP 429» con backoff 5/10/20/40s que nunca se recuperaba.",
        "causa_raiz": "el plan de 1816 da **50 tokens por día** y el token dura 24h, "
                      "pero vivía en un dict de módulo: cada proceso (15 corridas de "
                      "`tamar_1816`, cada restart de la API, cada job) quemaba uno. "
                      "Y el backoff lo aceleraba — reintentar contra una cuota "
                      "consume justo el recurso que se acabó.",
        "cambio": "token COMPARTIDO en `manager.tokens_externos` (de ~16-30 logins "
                  "diarios a 1-2), 2 reintentos en vez de 5, tope propio antes de "
                  "gastar, y el límite de 1 petición/segundo pasó a ser GLOBAL.",
        "detectado_por": "user",
    },
    {
        "slug": "paridad-cer-la-calcula-mal-el-motor",
        "causa": "paridad_del_motor", "dominio": "bono",
        "titulo": "A veces el dato está bien y lo que está mal es la cuenta",
        "sintoma": "TX26 con paridad 20,07% — fuera del rango sano.",
        "causa_raiz": "`curvas.py:457` arma el valor técnico del CER con "
                      "`valor_nominal` (estático, 100) en vez del residual VIVO, así "
                      "que un bono que ya amortizó el 80% muestra la paridad 5 veces "
                      "más chica. Con el residual real (20) da 100,4%: normal.",
        "cambio": "causa propia `paridad_del_motor`, marcada como **no arreglable "
                  "con datos**. El agente lo dice en vez de proponer que se toque un "
                  "bono que está bien.",
        "detectado_por": "agente",
    },
]


def main(argv: list[str]) -> int:
    if "--listar" in argv:
        for causa in sorted({l["causa"] for l in LECCIONES}):
            for lec in lecciones_de(causa):
                print(f"  [{causa or 'TODAS'}] {lec['slug']}: {lec['titulo']}")
        return 0
    ok = 0
    for lec in LECCIONES:
        r = guardar_leccion(**lec)
        estado = "✔" if r.get("ok") else f"✖ {r.get('error')}"
        print(f"  {estado}  {lec['slug']}  —  {lec['titulo']}")
        ok += bool(r.get("ok"))
    print(f"\n{ok}/{len(LECCIONES)} lecciones en agente.av_agent_lecciones.")
    print("El agente las muestra dentro del diagnóstico cuando aplican a la causa.\n")
    return 0 if ok == len(LECCIONES) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
