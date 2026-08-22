"""api/services/av_agent_analista.py — EL ANALISTA del diagnóstico masivo.

Doc madre: **`docs/AV_AGENT.md`** §0.j.

Pedido del user (2026-08-18): *«no tener que copiar y venirme acá… si yo ya
tengo IA. La idea sería entrenarlo lo máximo posible para que sepa resolver
esto, y que quede general para que si mañana paso a otra IA lo entienda»*.

**Qué hace.** Toma el informe del diagnóstico masivo y le pide al LLM lo único
que la IA aporta acá: **el patrón**. Los diagnósticos de cada caso ya están
hechos, son deterministas y NO se le piden ni se le dejan tocar.

**LA REGLA DE ORO SIGUE EN PIE: la IA nunca es la fuente de un número.** Todo lo
que este módulo le manda al modelo ya fue calculado por las lentes; lo que se le
pide es leer decenas de casos juntos y decir qué causas dominan, cuáles se
contradicen y cuáles huelen a bug del agente. Si el LLM no está disponible, el
informe determinista sigue entero — esto es una capa ARRIBA, nunca un
reemplazo.

**Por qué el "entrenamiento" es un CONTEXTO y no un fine-tuning** (y por qué eso
es lo que lo hace portable). Con decenas de ejemplos, ajustar pesos no tiene
sentido y ataría el resultado a un proveedor. Lo que sí sirve es darle al modelo
lo que un analista nuevo necesitaría para entender el dominio: las reglas del
negocio, el catálogo de causas conocidas y las lecciones ya aprendidas. Eso vive
en la base (`av_agent_lecciones`, `CAUSAS`) y se INYECTA en cada pedido, así que:

  · el conocimiento crece solo — cada lección nueva mejora el análisis siguiente
    sin re-entrenar nada;
  · **funciona con cualquier proveedor**. Cambiar de DeepSeek a otro es tocar
    `core/llm.py`; este módulo no sabe con quién habla.
"""
from __future__ import annotations

import json
import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)

_TAREA = "av_agent_informe"

# ── EL CONTEXTO DEL DOMINIO ─────────────────────────────────────────────────
#
# Lo que un analista nuevo necesitaría saber para leer el informe sin que nadie
# se lo explique. Es CONOCIMIENTO ESTABLE del negocio —no cambia entre corridas—
# y por eso vive acá y no en la base: si mañana cambia una regla del dominio,
# cambia con el código que la implementa, en el mismo commit.
#
# Está escrito para CUALQUIER modelo, no para uno: sin jerga del proveedor, sin
# formato propietario, todo explicado desde cero.
_DOMINIO = """\
CONTEXTO — qué es este sistema y qué significan los números.

Sos analista de datos de una mesa de renta fija argentina. El sistema valúa
bonos: toma un PRECIO de mercado y un CRONOGRAMA DE FLUJOS (cuándo paga cuánto)
y calcula TEA (tasa efectiva anual) y PARIDAD.

Las reglas que hacen falta para leer el informe:

1. PARIDAD = precio / valor técnico × 100. Un bono cotiza "por 100 de valor
   nominal", así que un cronograma sano suma alrededor de 100 en amortizaciones
   futuras, y una paridad sana cae entre 40% y 160%. Una paridad de 156.570% o
   de 0,039% NO es un bono raro: es una división entre dos números que están en
   unidades distintas.

2. CUÁL DE LOS DOS LADOS ESTÁ MAL SE SABE MIRANDO EL RESIDUAL:
   - residual ≈ 100 y paridad enorme  → el que está fuera de escala es el PRECIO.
   - residual ≈ 100.000 y paridad ≈ 0 → el que está fuera de escala es el CUADRO
     (está cargado en nominales de la emisión en vez de base 100).

3. `moneda_flujo` le dice al motor en qué unidad entra el precio. Los valores que
   el motor entiende son USD, DL (dólar-linked) y ARS. Cualquier otra cosa cae en
   un `else` silencioso y el precio entra como peso nativo SIN dar error. "HD" y
   "DL" son vocabulario de la CARTERA (cómo la mesa agrupa), no del motor:
   cuando aparece "HD" ahí, el bono está mal marcado.

4. Hay tres ramas de cálculo y cada una lee campos distintos del cronograma:
   - "cer": lee `amortizacion_pct`, y además EXIGE el dato `cer_emision`. Sin él
     el motor sale temprano y no escribe ni TEA ni paridad: lo que quede guardado
     es viejo.
   - "soberanos": lee `amortizacion_pct` (montos por 100).
   - "tasa_fija": lee `amortizacion` (montos absolutos).
   Un cronograma cargado con el campo de la OTRA rama deja al motor viendo cero
   amortizaciones futuras.

5. Si el XIRR no converge, la causa NO está en la tasa: está en que el precio y
   los flujos están en escalas distintas. Es un SÍNTOMA, nunca la causa.
"""

_INSTRUCCIONES = """\
QUÉ TE PIDO, Y QUÉ NO.

NO te pido que diagnostiques los casos: eso ya está hecho por reglas
deterministas y no se discute. NO inventes números: todos los que necesitás
están en el informe, y si un dato no está, decí que no está.

Te pido lo que NINGUNA fila individual puede decir, mirando los casos JUNTOS:

1. EL PATRÓN. ¿Cuántas causas REALES hay detrás de estos casos? Si N casos son
   la misma causa raíz, decilo: el trabajo real es mucho más chico de lo que
   parece la lista.

2. LOS QUE HUELEN A BUG DEL AGENTE, no a dato mal cargado. Señales: varios casos
   que fallan con la misma excepción; un caso marcado "listo para aplicar" cuyo
   diagnóstico dice que el problema NO se arregla tocando el dato; una lente que
   se contradice con otra dentro del mismo caso.

3. EL ORDEN DE ATAQUE, con el motivo. Qué conviene arreglar primero y por qué —
   normalmente lo que está más aguas arriba, o lo que destraba más casos.

4. LO QUE NO CIERRA. Cualquier cosa del informe que te resulte sospechosa,
   incluso si el agente la dio por buena. Preferí decir "esto me hace ruido" a
   quedarte callado.

FORMATO: texto plano, en castellano rioplatense, directo. Sin markdown, sin
viñetas decorativas. Empezá por la conclusión. Si algo no se puede afirmar con
lo que hay, decí exactamente qué dato falta para poder afirmarlo.
"""


def _lecciones() -> str:
    """Lo YA aprendido, inyectado como contexto. **Acá está el "entrenamiento"**:
    cada lección que se guarda mejora el análisis de la próxima corrida sin
    re-entrenar nada y sin atarse a un proveedor."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT sintoma, causa_raiz, cambio FROM "
                        "agente.av_agent_lecciones WHERE activa ORDER BY creado_at")
            rows = cur.fetchall()
    except Exception as e:
        logger.warning("av_agent_analista: sin lecciones (%s)", e)
        return ""
    if not rows:
        return ""
    L = ["LO QUE YA APRENDIMOS EN ESTE SISTEMA (casos reales, ya resueltos).",
         "Si un caso del informe se parece a alguno de estos, decilo:"]
    for sintoma, causa, cambio in rows:
        L.append(f"- Síntoma: {sintoma}\n  Causa raíz: {causa}\n  Se resolvió: {cambio}")
    return "\n".join(L)


def _causas() -> str:
    """El catálogo de causas conocidas, con el dato que más importa para decidir:
    **cuáles sabe arreglar el agente solo y cuáles no.**"""
    from api.services.av_agent_alta import CAUSAS
    L = ["CAUSAS QUE EL SISTEMA YA SABE NOMBRAR:"]
    for clave, c in CAUSAS.items():
        quien = "el agente lo arregla solo" if c.get("agente") else "necesita una persona"
        L.append(f"- {clave}: {c['titulo']} → {c['arreglo']} ({quien})")
    return "\n".join(L)


def analizar(run: dict, *, usuario: str = "") -> dict:
    """Le pide al LLM el ANÁLISIS del informe. Nunca levanta: si la IA no está,
    se dice y el informe determinista sigue sirviendo igual."""
    # La versión COMPACTA: el informe para leer repite cada conclusión en tres
    # lentes distintas (para una persona eso ayuda; para el modelo es ruido que
    # compite por la ventana y esconde el patrón). Es el MISMO informe, no un
    # segundo formato — se arma con la misma función.
    from api.services.av_agent_masivo import informe_texto
    from core import ai
    texto = informe_texto(run, compacto=True) if run.get("informe") else (run.get("texto") or "")
    if not texto.strip():
        return {"ok": False, "error": "el informe está vacío"}
    if not ai.disponible(_TAREA):
        return {"ok": False,
                "error": "la IA no está disponible (sin credenciales o presupuesto "
                         "agotado). El informe determinista sigue completo."}

    partes = [_DOMINIO, _causas()]
    if (lec := _lecciones()):
        partes.append(lec)
    partes.append(_INSTRUCCIONES)
    system = "\n\n".join(partes)

    resumen = run.get("resumen") or {}
    user = (f"INFORME A ANALIZAR (corrida #{run.get('id')}, "
            f"{run.get('hechos')} de {run.get('total')} casos):\n\n{texto}\n\n"
            f"Agregados ya calculados (no los recalcules): "
            f"{json.dumps(resumen, ensure_ascii=False)}")

    txt, traza_id = ai.completar_con_traza(
        _TAREA, system=system, user=user, usuario=usuario or None,
        detalle=f"informe masivo #{run.get('id')} — {run.get('hechos')} casos")
    if not txt:
        return {"ok": False,
                "error": "la IA no devolvió nada (timeout o error del proveedor). "
                         "El informe determinista sigue completo."}
    _guardar(run.get("id"), txt)
    return {"ok": True, "analisis": txt, "traza_id": traza_id}


def _guardar(run_id: int | None, analisis: str) -> None:
    """El análisis queda pegado a SU corrida. Sin esto habría que volver a pagarlo
    cada vez que se reabre el informe — y peor, dos lecturas del mismo informe
    podrían decir cosas distintas."""
    if not run_id:
        return
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE agente.av_agent_runs SET analisis = %s, "
                        "analisis_at = now() WHERE id = %s", (analisis, run_id))
            conn.commit()
    except Exception as e:
        logger.warning("av_agent_analista: no se pudo guardar el análisis: %s", e)
