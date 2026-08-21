"""api/routers/ia.py — endpoints del módulo IA (QuantAI, docs/QUANTAI.md).

Solo HTTP plumbing (la lógica vive en api/services/). El gate es
estructural: se monta en api/main.py con `_IA` (bearer + require_module("ia")),
y el prefijo /api/ia ya mapea al módulo en ENDPOINT_MODULE_PREFIXES.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_user_email, require_admin
from api.services import briefing, ia_obs

router = APIRouter(prefix="/api/ia", tags=["ia"])


@router.get("/observabilidad", dependencies=[Depends(require_admin)])
def observabilidad(dias: int = 14, limit: int = 60, offset: int = 0,
                   tarea: str | None = None, usuario: str | None = None,
                   solo_error: bool = False, q: str | None = None):
    """Trazas del gateway de IA para OBSERVABILIDAD → IA: resumen de hoy
    (+% presupuesto), serie por día, agregados por tarea y por PROVEEDOR, y
    el historial de llamadas paginado y filtrable (tarea/usuario/errores/texto).

    ADMIN-ONLY (require_admin, no delegable desde la matriz). Devuelve
    `detalle`/`respuesta`/`razonamiento` de ia.trazas — o sea la PREGUNTA y la
    RESPUESTA literal de las conversaciones de TODOS los usuarios, más su email,
    y acepta filtros `usuario`/`q` para buscar dentro de ellas. Con el gate del
    módulo `ia` solamente quedaba alcanzable por cualquier rol con `ia` tildado
    y —peor— por el portal INVITADO (`ia` ∈ INVITADO_MODULES, core/roles.py),
    que habría leído conversaciones del NEGOCIO de la mesa (REGLA #8).
    require_admin además rechaza al guest de forma dura."""
    return ia_obs.observabilidad(dias=dias, limit=limit, offset=offset, tarea=tarea,
                                 usuario=usuario, solo_error=solo_error, q=q)


class PresupuestosBody(BaseModel):
    global_dia: int | None = None   # tokens/día de TODO el sistema (techo duro)
    usuario_dia: int | None = None  # tokens/día por usuario (≤ global)


@router.get("/presupuesto", dependencies=[Depends(require_admin)])
def presupuesto_get():
    """Límites de tokens vigentes del gateway (tabla > env > default).

    ADMIN-ONLY: es el par de lectura de `POST /presupuesto` (ya admin-only).
    Config interna de costos — no la ve un invitado ni un rol con `ia`."""
    return ia_obs.get_presupuestos()


@router.get("/saldo", dependencies=[Depends(require_admin)])
def saldo_proveedor():
    """Estado de los proveedores LLM configurados: modelos por tier, si se
    comprometen a no entrenar, y saldo real del que lo expone.

    ADMIN-ONLY: expone el saldo real de la cuenta del proveedor (dato
    financiero de la empresa) — nunca para el portal invitado."""
    return ia_obs.saldo()


class PresupuestoUsuarioBody(BaseModel):
    email: str
    valor: int | None = None  # None = borrar la excepción (vuelve al tope general)


@router.post("/presupuesto/usuario", dependencies=[Depends(require_admin)])
def presupuesto_usuario_set(body: PresupuestoUsuarioBody, email: str = Depends(get_user_email)):
    """Excepción PERSONAL de tope diario para un usuario (SOLO admin) — pisa
    el tope general solo para ese email. valor null la borra."""
    try:
        return ia_obs.set_presupuesto_usuario(email=body.email, valor=body.valor, actor=email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/presupuesto", dependencies=[Depends(require_admin)])
def presupuesto_set(body: PresupuestosBody, email: str = Depends(get_user_email)):
    """Edita los topes diarios (SOLO admin). El global es techo duro del día;
    el tope por usuario no puede superarlo. Queda auditado quién y cuándo."""
    try:
        return ia_obs.set_presupuestos(
            global_dia=body.global_dia, usuario_dia=body.usuario_dia, actor=email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/briefing")
def briefing_apertura():
    """Briefing de apertura (modal de HOME, 10:00 ART). v1 determinista —
    futuros US, oficial (MAE live + A3500) y cierres MEP/CCL con variaciones."""
    return briefing.briefing_hoy()


# ── AV AGENT (docs/AV_AGENT.md) ──────────────────────────────────────────────
#
# Vive bajo /api/ia a propósito: hereda el gate `ia` de forma ESTRUCTURAL en vez
# de estrenar un prefijo que habría que acordarse de sumar a
# ENDPOINT_MODULE_PREFIXES. Un endpoint de IA que nace fuera de ese prefijo nace
# sin gate, y eso no se ve hasta que alguien lo prueba sin permisos.
#
# **TODO admin-only** (decisión del user 2026-08-16, apretado desde el módulo
# `ia`): el agente expone el estado interno de la valuación — qué bonos están mal
# cargados, cuáles no entran al AuM, qué le falta al catálogo. Eso no es
# información de mercado: es cómo está hecho el sistema por dentro, y con la
# matriz de roles dándole `ia` a la mesa, gatearlo solo por módulo se lo
# mostraría a un comercial.


class RespuestaAvAgent(BaseModel):
    id: int = Field(..., ge=1)
    respuesta: str = Field(..., min_length=1, max_length=32)
    nota: str = Field("", max_length=500)


@router.get("/av-agent/vista", dependencies=[Depends(require_admin)])
def av_agent_vista():
    """Toda la pantalla en UN request: hallazgos de la última corrida, preguntas
    abiertas, lo ya decidido y los ignorados."""
    from api.services import av_agent_vista as vista_svc
    return vista_svc.vista()


@router.post("/av-agent/responder", dependencies=[Depends(require_admin)])
def av_agent_responder(body: RespuestaAvAgent, email: str = Depends(get_user_email)):
    """Contesta una pregunta del agente y **aplica su efecto**."""
    from api.services import av_agent_preguntas as preg
    try:
        r = preg.responder(body.id, body.respuesta, por=email or "", nota=body.nota)
    except preg.RespuestaInvalida as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "id": body.id, "respuesta": r.get("respuesta"),
            "aplicada": r.get("aplicada", False)}


class IgnorarAvAgent(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=40)
    motivo: str = ""


@router.post("/av-agent/ignorar", dependencies=[Depends(require_admin)])
def av_agent_ignorar(body: IgnorarAvAgent, email: str = Depends(get_user_email)):
    """«No me interesa»: el ticker deja de aparecer en TODOS los tipos de hallazgo.

    Antes ignorar solo se podía hacer contestando una pregunta del agente, y solo
    valía para los faltantes: en el resto de la lista no había forma de decir que
    algo no interesa. Surte efecto **en la lectura siguiente**, no en la próxima
    corrida — apretar el botón y que la fila siga ahí es lo que hace desconfiar de
    toda la lista.

    Reversible desde la tab DECIDIDO (`designorar`)."""
    from api.services import av_agent_preguntas as preg
    try:
        return preg.ignorar(body.ticker, motivo=body.motivo, por=email or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class DesignorarAvAgent(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=32)


@router.post("/av-agent/designorar", dependencies=[Depends(require_admin)])
def av_agent_designorar(body: DesignorarAvAgent, email: str = Depends(get_user_email)):
    """Deshace un «no me interesa»: el ticker vuelve a proponerse.

    Existe porque **la reversibilidad es lo que hace barata la decisión**. Sin
    este botón, marcar `ignorar` es irreversible desde la app y la única salida es
    tocar SQL a mano — con lo cual la respuesta segura pasa a ser no contestar
    nada, y el mecanismo entero deja de usarse.

    ⚠️ Es POST y no DELETE **a propósito**: el proxy de Next para /api/ia es un
    catch-all que hoy expone solo GET y POST. Agregarle DELETE habilitaría el
    verbo para TODOS los endpoints de /api/ia —presentes y futuros— a cambio de
    la elegancia REST de uno solo. La superficie mínima gana."""
    from api.services import av_agent_preguntas as preg
    try:
        return preg.designorar(body.ticker, por=email or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class ResolverAviso(BaseModel):
    id: int = Field(..., ge=1)
    deshacer: bool = False


@router.post("/av-agent/aviso", dependencies=[Depends(require_admin)])
def av_agent_aviso(body: ResolverAviso, email: str = Depends(get_user_email)):
    """Marca un aviso como HECHO, o lo reabre (`deshacer`).

    Los avisos son la lista de trabajo manual que dejó un alta: el agente hace el
    95% y anota el 5% que ninguna fuente publica. **Los cierra una persona** —
    derivarlos del estado del master los haría desaparecer sin dejar ver qué
    había pendiente ni qué se hizo.

    Reversible por la misma razón que `designorar`: sin poder deshacer, marcar
    algo se vuelve una decisión cara y el mecanismo se deja de usar."""
    from api.services import av_agent_vista as vista
    r = vista.resolver_aviso(body.id, por=email or "", deshacer=body.deshacer)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "no se pudo"))
    return r


class CompletarAviso(BaseModel):
    id: int = Field(..., ge=1)
    valor: str = Field(..., min_length=1, max_length=200)


@router.post("/av-agent/aviso/completar", dependencies=[Depends(require_admin)])
def av_agent_aviso_completar(body: CompletarAviso,
                             email: str = Depends(get_user_email)):
    """Carga el dato que faltaba **desde la misma lista de avisos** y cierra el
    aviso en el mismo acto (pedido del user, 2026-08-17).

    Antes el aviso decía «falta el CER de emisión» y te mandaba a Manager →
    Títulos a buscar el bono: el agente hacía el 95% y te dejaba el 5% en otra
    pantalla. Ahora el valor se escribe donde se lee el aviso.

    Escribe SOLO los campos del catálogo `_CAMPO_AVISO` y **verifica** contra el
    master antes de cerrar: si el dato no quedó cargado, el aviso sigue abierto.
    """
    from api.services import av_agent_vista as vista
    r = vista.completar_aviso(body.id, body.valor, por=email or "")
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "no se pudo"))
    return r


class DiagnosticoSalud(BaseModel):
    chequeo_id: str = Field(..., min_length=2, max_length=120)


class VotoEval(BaseModel):
    """El voto humano sobre un diagnóstico. **Es el insumo del eval set.**"""
    caso: str = Field(..., min_length=2, max_length=200)
    # `sistema` se sumó el 2026-08-19: el agente ya mira tablas, motores,
    # permisos y datos partidos, y esos hallazgos no tenían dónde votarse.
    dominio: str = Field("bono", pattern="^(bono|salud|sistema)$")
    causa: str = Field(..., min_length=2, max_length=60)
    acierta: bool
    nota: str = Field("", max_length=1000)
    causa_correcta: str = Field("", max_length=60)
    # EL TIPO del hallazgo, para que el BACKEND decida qué clase de voto es. El
    # front NO manda el `origen`: si lo mandara, una pantalla vieja o un `curl`
    # podrían anotar un «¿te sirve?» como si fuera un juicio y eso mueve la
    # compuerta de autonomía. Quién puede abrir esa compuerta se decide de este
    # lado.
    tipo: str = Field("", max_length=40)


class FlujosAvAgent(BaseModel):
    ticker: str = Field(..., min_length=2, max_length=32)
    # El CER de emisión tipeado en la cadena: sin ese número la rama `cer` del
    # motor no devuelve TEA ni paridad, y el cotejo contra 1816 no se puede hacer.
    cer_emision: float | None = Field(None, gt=0)


@router.post("/av-agent/simular-flujos", dependencies=[Depends(require_admin)])
def av_agent_simular_flujos(body: FlujosAvAgent):
    """E3 — el hallazgo `flujos_vacios` se vuelve accionable. **No escribe nada.**

    Baja el cronograma de 1816 para un bono que YA está en `mercado.curvas`, lo
    convierte con la rama que ese bono ya tiene, simula la TEA y la coteja contra
    1816 (paridad + duration). Es la pregunta del user: *«lo que hay que chequear
    es si con el flujo que agregaríamos y nuestro modelo nos da una TEA y esos
    datos como a 1816»*."""
    from api.services import av_agent_alta
    return av_agent_alta.simular_flujos(body.ticker, cer_emision=body.cer_emision)


@router.post("/av-agent/aplicar-flujos", dependencies=[Depends(require_admin)])
def av_agent_aplicar_flujos(body: FlujosAvAgent, email: str = Depends(get_user_email)):
    """E3 — escribe el cronograma, **y nada más**.

    Es un UPDATE puntual sobre el blob: los ejes, el emisor y el símbolo ni
    siquiera están en el payload, así que no se pueden pisar por accidente.

    La ÚNICA excepción es `cer_emision`, y solo si el user lo tipeó en la cadena
    **y** el bono no lo tenía: sin ese número la rama `cer` del motor no devuelve
    tasa y el bono quedaría con cronograma y sin valuar."""
    from api.services import av_agent_alta
    return av_agent_alta.aplicar_flujos(body.ticker, actor=email or "",
                                        cer_emision=body.cer_emision)


@router.post("/av-agent/simular-arreglo", dependencies=[Depends(require_admin)])
def av_agent_simular_arreglo(body: FlujosAvAgent):
    """E3.j — qué INSUMO está mal en un bono con tasa sospechosa. **No escribe.**

    Corre el motor dos veces (el bono como está HOY y con la propuesta de 1816) y
    coteja las dos contra ellos. El ANTES es la mitad del diagnóstico: sin esa
    columna no hay forma de saber si el arreglo mejora algo."""
    from api.services import av_agent_alta
    return av_agent_alta.simular_arreglo(body.ticker, cer_emision=body.cer_emision)


@router.post("/av-agent/aplicar-arreglo", dependencies=[Depends(require_admin)])
def av_agent_aplicar_arreglo(body: FlujosAvAgent, email: str = Depends(get_user_email)):
    """E3.j — **la única puerta que PISA un dato existente.**

    Por eso su cadena exige las DOS mitades: que la propuesta coincida con 1816 y
    que lo de hoy NO. Sin la segunda se pisaría un bono que ya estaba bien, que es
    estrictamente peor que no hacer nada."""
    from api.services import av_agent_alta
    return av_agent_alta.aplicar_arreglo(body.ticker, actor=email or "",
                                         cer_emision=body.cer_emision)


class SimularAvAgent(BaseModel):
    ticker: str = Field(..., min_length=2, max_length=32)
    curva_1816: str = Field(..., min_length=2, max_length=80)
    # El dato que ninguna fuente publica, tipeado en la misma pantalla. Opcional:
    # sin él la simulación es la de siempre.
    cer_emision: float | None = Field(None, gt=0)


@router.post("/av-agent/salud", dependencies=[Depends(require_admin)])
def av_agent_salud(body: DiagnosticoSalud):
    """**SALUD, razonada por el agente** — la ÚNICA puerta a SALUD desde el
    2026-08-19: el panel de Manager se dio de baja y el agente es la entrada.

    Todas las lentes son deterministas y **no gastan un token**. No escribe nada:
    SALUD sigue siendo el dueño de su estado."""
    from api.services import av_agent_salud as svc
    return svc.diagnosticar(body.chequeo_id)


# ── LO QUE EL AGENTE SABE HACER (2026-08-19) ────────────────────────────────
#
# El ciclo PROPONER → OK → APLICAR → VERIFICAR de `av_agent_hacer`, expuesto en
# tres endpoints y no en uno por acción: la acción es un PARÁMETRO, así que
# sumar una acción nueva no toca ni el router ni el front.
#
# **Los tres son admin-only y de escritura.** Aplicar escribe en el catálogo de
# producción (`portafolio.assets`, `clientes.contrapartes`), o sea que el gate
# es el mismo que el de Manager y no puede ser más flojo.


class ProponerHacer(BaseModel):
    accion: str = Field(..., min_length=3, max_length=60)
    # Apagar el modelo deja la parte determinista disponible siempre — sin key,
    # con presupuesto agotado, o cuando simplemente no hace falta pagar.
    con_ia: bool = True


class AplicarHacer(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=200)
    # El humano puede CORREGIR la sugerencia antes de aplicarla ({id: valor}) en
    # vez de tener que elegir entre aceptarla tal cual o descartarla. Es también
    # el único camino para las propuestas que nacen sin valor (a quién avisarle).
    valores: dict[str, str] = Field(default_factory=dict)


class RechazarHacer(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=200)


@router.get("/av-agent/hacer", dependencies=[Depends(require_admin)])
def av_agent_hacer_listar(accion: str = ""):
    """Qué sabe hacer el agente, qué espera tu OK y cómo viene acertando."""
    from api.services import av_agent_hacer as svc
    return {"catalogo": svc.catalogo(), "pendientes": svc.pendientes(accion),
            "historial": svc.historial(accion, limite=60),
            "resumen": svc.resumen(accion)}


@router.post("/av-agent/hacer/proponer", dependencies=[Depends(require_admin)])
def av_agent_hacer_proponer(body: ProponerHacer):
    """**Sugiere, sin tocar nada.** Vuelve a correr el control para trabajar
    sobre lo que sigue mal AHORA (no sobre la foto del cron) y persiste una
    propuesta por caso. La regla determinista resuelve lo que puede; el modelo
    entra solo para lo que quedó afuera."""
    from api.services import av_agent_hacer as svc
    return svc.proponer(body.accion, con_ia=body.con_ia)


@router.post("/av-agent/hacer/aplicar", dependencies=[Depends(require_admin)])
def av_agent_hacer_aplicar(body: AplicarHacer, email: str = Depends(get_user_email)):
    """**El OK.** Escribe por la misma puerta que usa la pantalla y **relee para
    confirmar, en el mismo request**. Si la verificación no confirma, la
    propuesta queda `fallida` y no `aplicada`: marcar hecho algo que no se puede
    comprobar es cómo un tablero termina en verde con el dato roto."""
    from api.services import av_agent_hacer as svc
    return svc.aplicar(body.ids, por=email or "", valores=body.valores)


@router.post("/av-agent/hacer/rechazar", dependencies=[Depends(require_admin)])
def av_agent_hacer_rechazar(body: RechazarHacer, email: str = Depends(get_user_email)):
    """Descartar también se registra: es lo que dice que el agente sugirió algo
    que un humano no compró, y sin eso parecería tener 100% de acierto."""
    from api.services import av_agent_hacer as svc
    return svc.rechazar(body.ids, por=email or "")


@router.get("/av-agent/agenda", dependencies=[Depends(require_admin)])
def av_agent_agenda():
    """**QUÉ ESTÁ HACIENDO EL AGENTE HOY** — la tab CONTROL (§0.bv).

    SKILLS contesta *qué sé hacer*; esto contesta *¿lo estoy haciendo?*, que
    solo se responde cruzando el catálogo con las corridas reales. Una
    capacidad que nadie ejecuta se ve idéntica a una que corre cada 5 minutos.
    """
    from api.services import av_agent_agenda
    return av_agent_agenda.vista()


@router.get("/av-agent/skills", dependencies=[Depends(require_admin)])
def av_agent_skills():
    """**TODO lo que el agente sabe hacer**, en un solo lugar.

    Regla del user (2026-08-19): *«por ley y regla, todo lo nuevo que se agregue
    de funcionalidad o habilidad tiene que quedar en esta tab, para que se vaya
    mapeando todo lo que va consolidando; y dejar asentado si esa skill usa IA o
    no»*.

    **El catálogo se DERIVA de los registros reales**, no se escribe a mano: una
    skill nueva aparece por existir, y no hay forma de agregar una capacidad y
    olvidarse de mapearla. Cada una declara si usa el modelo —`no` / `opcional`
    / `si`— porque eso cambia cuánto hay que desconfiar de lo que devuelve."""
    from api.services import av_agent_skills as svc
    return svc.vista()


# ── LO QUE EL AGENTE SABE EXPLICAR (2026-08-19) ─────────────────────────────
#
# Las ocho pantallas de Manager → VALIDACIONES **nunca fueron debug**: son las
# preguntas que se hace alguien de finanzas todos los días, con nombre de
# programador. El user: *«che, saber por qué esta TEA rinde tanto, por qué la TNA
# de futuros es tanto… quiero ir migrando funciones útiles al agent para que el
# día de mañana le hable y se lo pida»*.
#
# **No se reimplementa ningún cálculo**: cada explicador envuelve la MISMA
# función que ya usa la pantalla. Manager → VALIDACIONES queda donde está.


class Explicar(BaseModel):
    explicador: str = Field(..., min_length=2, max_length=40)
    sujeto: str = Field("", max_length=80)
    # La frase del modelo es lo ÚLTIMO y lo más chico. Apagarla deja la
    # explicación entera disponible: los pasos son deterministas.
    con_ia: bool = True


@router.get("/av-agent/explicar", dependencies=[Depends(require_admin)])
def av_agent_explicar_catalogo(explicador: str = ""):
    """Qué sabe contestar el agente. **Este catálogo es, además, el menú del día
    que se le pueda hablar**: lo que está acá es lo que va a entender."""
    from api.services import av_agent_explicar as svc
    return {"catalogo": svc.catalogo(),
            "sugerencias": svc.sugerencias(explicador) if explicador else []}


@router.post("/av-agent/explicar", dependencies=[Depends(require_admin)])
def av_agent_explicar(body: Explicar):
    """La respuesta: los pasos del cálculo + UNA frase.

    **El cálculo determinista produce los números; el modelo produce la frase** —
    nunca al revés. Y si al reproducir el cálculo el número no coincide con el
    que muestra la app, eso viaja aparte en `discrepancia`: deja de ser una
    explicación y pasa a ser un aviso."""
    from api.services import av_agent_explicar as svc
    return svc.explicar(body.explicador, body.sujeto, con_ia=body.con_ia)


# ── SALUD, SOLO por el agente (2026-08-19) ──────────────────────────────────
#
# El panel de Manager → OBSERVABILIDAD → SALUD, el botón de la barra y el modal
# de alertas se dieron de baja: *«eliminar SALUD del front de observabilidad…
# toda la salud, y esto pasa 100% por el agent»* (user). El motor
# (`api/services/salud.py`) NO se tocó — sigue siendo el dueño de la evaluación
# y el agente lo lee. Lo que se movió acá son las dos cosas que el panel hacía y
# que no se podían perder: **interrumpir** cuando algo se rompe, y **silenciar**
# lo que no querés que te interrumpa.


class VistosSalud(BaseModel):
    # `None` = todos los pendientes de este admin.
    ids: list[int] | None = None


class SilenciarChequeo(BaseModel):
    chequeo_id: str = Field(..., min_length=2, max_length=120)
    alertar: bool
    nota: str = Field("", max_length=300)


@router.get("/av-agent/salud/pendientes", dependencies=[Depends(require_admin)])
def av_agent_salud_pendientes(email: str = Depends(get_user_email)):
    """Lo que se rompió y este admin todavía no vio. **Es lo que hace que la
    señal te busque** en vez de esperarte en una pantalla que hay que ir a abrir
    — la razón por la que SALUD existe (el backfill que falló dos días y nadie se
    enteró). Ahora el que interrumpe es el agente."""
    from api.services import salud
    return {"pendientes": salud.pendientes(email or "")}


@router.post("/av-agent/salud/vistos", dependencies=[Depends(require_admin)])
def av_agent_salud_vistos(body: VistosSalud, email: str = Depends(get_user_email)):
    """El «entendido»: deja de interrumpir con eso. Por admin, no global."""
    from api.services import salud
    return salud.marcar_vistos(email or "", body.ids)


@router.post("/av-agent/salud/silenciar", dependencies=[Depends(require_admin)])
def av_agent_salud_silenciar(body: SilenciarChequeo,
                             email: str = Depends(get_user_email)):
    """Silenciar o reactivar un chequeo. **Silenciar no lo esconde**: sigue en la
    lista del agente con su estado real, solo deja de abrir el modal. Un chequeo
    que desaparece al silenciarlo es un problema que se te olvida."""
    from api.services import salud
    try:
        return salud.set_alerta(body.chequeo_id, body.alertar, email or "", body.nota)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/av-agent/eval", dependencies=[Depends(require_admin)])
def av_agent_eval(body: VotoEval, email: str = Depends(get_user_email)):
    """**El voto humano sobre un diagnóstico**: ¿la causa que dijo el agente es la
    correcta?

    Es el insumo del EVAL SET, y sin él no hay forma de saber si el agente acierta
    — o sea que no hay forma de darle más autonomía sin fe. Un ✔/✖ por
    diagnóstico, con el motivo cuando falla.

    ⚠️ **Dos clases de voto, y el backend elige cuál es.** A una OBSERVACIÓN (un
    ERROR copiado del log del motor) no se le puede preguntar «¿acertó?»: la
    respuesta es siempre que sí, y esos «siempre sí» llegan a 10/10 y marcan la
    causa como candidata a automatizarse con evidencia que no mide nada. Esas
    votan como `utilidad` («¿te sirve verla?»), que la compuerta ya deja afuera.
    El `origen` NO viaja en el body a propósito — ver el comentario del modelo."""
    from api.services import av_agent, av_agent_evals
    observacion = av_agent.pregunta_de(body.tipo) == av_agent.OBSERVACION
    return av_agent_evals.votar(
        caso=body.caso, dominio=body.dominio, causa=body.causa,
        acierta=body.acierta, nota=body.nota,
        origen="utilidad" if observacion else "humano",
        causa_correcta=body.causa_correcta, por=email or "")


@router.get("/av-agent/eval", dependencies=[Depends(require_admin)])
def av_agent_eval_resumen():
    """La PRECISIÓN medida por causa: cuántos votos, cuántos aciertos, y dónde se
    equivoca. Es el tablero que decide qué arreglo se puede automatizar."""
    from api.services import av_agent_evals
    return av_agent_evals.resumen()


@router.post("/av-agent/simular", dependencies=[Depends(require_admin)])
def av_agent_simular(body: SimularAvAgent):
    """E2 — calcula la TEA que TENDRÍA el bono, **sin escribir nada**.

    `cer_emision` permite RE-simular con el número tipeado a mano: nuestra serie
    CER no llega a la fecha de emisión de un bono nuevo, así que sin él la rama
    CER no devuelve tasa. Con él, el user ve la TEA antes de aplicar."""
    from api.services import av_agent_alta
    return av_agent_alta.simular(body.ticker, curva_1816=body.curva_1816,
                                 cer_emision=body.cer_emision)


@router.post("/av-agent/aplicar-alta", dependencies=[Depends(require_admin)])
def av_agent_aplicar_alta(body: SimularAvAgent, email: str = Depends(get_user_email)):
    """E2 — simula y, si la rama lo permite, da de alta el bono en
    `mercado.curvas` por la MISMA puerta que usa la mesa (`upsert_bono`).

    Si viene `cer_emision`, el bono nace CON el dato: no se crea pelado para
    después completarlo, y por lo tanto tampoco genera el aviso."""
    from api.services import av_agent_alta
    return av_agent_alta.aplicar(body.ticker, curva_1816=body.curva_1816,
                                 actor=email or "", cer_emision=body.cer_emision)


# ── EL TABLERO DE CONTROL (2026-08-18) ───────────────────────────────────────
#
# Los 15 endpoints de arriba actúan sobre UN hallazgo. Estos dos son los primeros
# que actúan sobre **EL AGENTE**: pedido del user, *«quiero control total del
# agente desde el modal por las dudas»*.


class ParadaAvAgent(BaseModel):
    activa: bool
    # Obligatorio para FRENAR (lo valida el service, no acá: la regla es de
    # negocio y tiene que valer también para el que llame al service directo).
    # El que se encuentra al agente frenado tiene que poder decidir si lo reanuda
    # sin ir a preguntarle a nadie.
    motivo: str = Field("", max_length=400)


@router.get("/av-agent/control", dependencies=[Depends(require_admin)])
def av_agent_control_estado():
    """**El tablero**: la parada, las fuentes de las que lee el agente y en qué
    estado están.

    Ninguna lectura pega a la red — un tablero que gasta un crédito de 1816 cada
    vez que se mira consume justo el recurso que vino a cuidar."""
    from api.services import av_agent_control
    return av_agent_control.estado()


@router.post("/av-agent/control/parada", dependencies=[Depends(require_admin)])
def av_agent_control_parada(body: ParadaAvAgent, email: str = Depends(get_user_email)):
    """**Frena o reanuda al agente.** Corta las ESCRITURAS de datos de mercado
    (alta, flujos, arreglo, crear curva) y deja intacta la lectura: se puede
    seguir diagnosticando con la mano frenada, que es lo que uno quiere mientras
    investiga. Queda en el libro de acciones."""
    from api.services import av_agent_control
    return av_agent_control.set_parada(activa=body.activa, motivo=body.motivo,
                                       por=email or "")


# ── EL DIAGNÓSTICO MASIVO (2026-08-18) ───────────────────────────────────────


class MasivoAvAgent(BaseModel):
    # Los casos los manda el FRONT porque son exactamente los que el usuario está
    # viendo con su filtro puesto — «diagnosticá estos 30» y no «diagnosticá lo
    # que vos creas». Que el backend re-derivara el filtro sería una segunda
    # implementación del mismo criterio, y la pantalla podría mostrar una cosa y
    # la corrida hacer otra.
    casos: list[dict] = Field(default_factory=list)
    filtro: dict = Field(default_factory=dict)
    # `sin_red` deja el análisis determinista disponible sin gastar un crédito ni
    # pagar la cola de 1,2s por caso. El default es CON red: el user lo pidió
    # explícito («los créditos están para gastarlos»), y lo que de verdad se cuida
    # es el reloj, no el saldo.
    sin_red: bool = False
    tope_creditos: int | None = Field(None, ge=100, le=50_000)


@router.post("/av-agent/masivo", dependencies=[Depends(require_admin)])
def av_agent_masivo(body: MasivoAvAgent, email: str = Depends(get_user_email)):
    """**Arranca el diagnóstico masivo** y devuelve el id al instante.

    Corre en background porque el plan de 1816 permite 1 petición por segundo:
    68 bonos son 2-3 minutos y ningún request HTTP sobrevive a eso."""
    from api.services import av_agent_masivo as svc
    return svc.arrancar(body.casos, por=email or "", filtro=body.filtro,
                        sin_red=body.sin_red,
                        tope_creditos=body.tope_creditos
                        if body.tope_creditos is not None
                        else svc.TOPE_CREDITOS_DEFAULT)


@router.get("/av-agent/masivo", dependencies=[Depends(require_admin)])
def av_agent_masivo_estado(run_id: int | None = None):
    """El progreso + el informe PARCIAL (se puede mirar mientras corre) + el
    bloque de texto para copiar. Sin `run_id`, el último."""
    from api.services import av_agent_masivo as svc
    return svc.estado(run_id)


@router.post("/av-agent/masivo/frenar", dependencies=[Depends(require_admin)])
def av_agent_masivo_frenar(run_id: int):
    """Corta la corrida en el próximo caso. Lo ya diagnosticado queda."""
    from api.services import av_agent_masivo as svc
    return svc.frenar(run_id)


@router.get("/av-agent/masivo/historial", dependencies=[Depends(require_admin)])
def av_agent_masivo_historial(limite: int = 15):
    """Las corridas anteriores — «esto mejoró, esto empeoró» es la pregunta que
    un informe suelto no contesta."""
    from api.services import av_agent_masivo as svc
    return {"runs": svc.historial(limite)}


@router.post("/av-agent/masivo/analizar", dependencies=[Depends(require_admin)])
def av_agent_masivo_analizar(run_id: int | None = None,
                             email: str = Depends(get_user_email)):
    """**El informe, leído por la IA.** Le pide el PATRÓN — qué causas dominan,
    qué huele a bug del agente, en qué orden atacar.

    Los diagnósticos ya están hechos y son deterministas: acá la IA no los toca
    ni inventa números. Si no está disponible, el informe determinista sigue
    entero. El análisis queda pegado a su corrida."""
    from api.services import av_agent_analista, av_agent_masivo
    run = av_agent_masivo.estado(run_id)
    if not run.get("ok"):
        return run
    return av_agent_analista.analizar(run, usuario=email or "")


# ── EL CENTINELA (2026-08-18) ────────────────────────────────────────────────


class VistoCentinela(BaseModel):
    claves: list[str] = Field(default_factory=list, max_length=500)


@router.get("/av-agent/centinela", dependencies=[Depends(require_admin)])
def av_agent_centinela(limite: int = 200):
    """**El tablero del centinela en UN request**: el latido (¿está prendido?),
    lo que está abierto —lo nuevo y sin ver primero— y lo que se arregló solo en
    las últimas 8 horas.

    `vivo` es una afirmación sobre AHORA: sale de la edad del último latido, no
    de que alguna vez haya corrido."""
    from api.services import av_agent_centinela as svc
    return svc.estado(limite)


@router.post("/av-agent/centinela/visto", dependencies=[Depends(require_admin)])
def av_agent_centinela_visto(body: VistoCentinela,
                             email: str = Depends(get_user_email)):
    """«Ya lo miré». **No lo resuelve ni lo esconde** — lo saca de «nuevo».
    Mezclar las dos cosas haría que nadie toque el botón por miedo a perder de
    vista el hallazgo."""
    from api.services import av_agent_centinela as svc
    return svc.marcar_visto(body.claves, por=email or "")


class DiagnosticoSinPrecio(BaseModel):
    ticker: str = Field(..., min_length=2, max_length=40)


@router.post("/av-agent/sin-precio", dependencies=[Depends(require_admin)])
def av_agent_sin_precio(body: DiagnosticoSinPrecio):
    """**Por qué este bono no tiene precio.** Cinco causas que se arreglan
    distinto: sin símbolo en el master · fuera del catálogo de Primary · pata
    equivocada · nunca operó (iliquidez, no un bug) · sin actividad hoy.

    Cero red y cero créditos. No escribe nada."""
    from api.services import av_agent_sin_precio as svc
    return svc.diagnosticar(body.ticker)


class PataDolar(BaseModel):
    ticker: str = Field(..., min_length=2, max_length=40)


@router.post("/av-agent/pata", dependencies=[Depends(require_admin)])
def av_agent_pata(body: PataDolar):
    """**Dónde está la pata en dólares de este bono** — SOLO LECTURA.

    Pregunta las tres cosas en orden: si la tenemos sembrada
    (`mercado.especies`), si existe en el catálogo de Primary —la fuente que no
    depende de ninguna decisión nuestra— y si la estamos pidiendo. Recién con las
    tres contestadas «no hay pata en dólares» se puede afirmar.

    Cero red y cero créditos. No escribe nada."""
    from api.services import av_agent_pata as svc
    return svc.explicar(body.ticker)


@router.post("/av-agent/pata/pedir", dependencies=[Depends(require_admin)])
def av_agent_pata_pedir(body: PataDolar, email: str = Depends(get_user_email)):
    """Siembra la especie si falta y **pide la pata**. Se ve en 5 segundos.

    Escribe en `mercado.especies` y `mercado.adhoc_subscriptions`, por las puertas
    que ya existen. **No toca `mercado.curvas`**: cambiar el símbolo del master
    exige reiniciar el motor y sigue siendo una decisión manual — se toma después,
    con el precio a la vista."""
    from api.services import av_agent_pata as svc
    return svc.pedir(body.ticker, por=email or "")


class ApuntarPata(BaseModel):
    ticker: str = Field(..., min_length=2, max_length=40)
    # Sin esto NO escribe: devuelve qué haría. Es el paso SIMULAR de la fila.
    aplicar: bool = False


@router.post("/av-agent/pata/apuntar", dependencies=[Depends(require_admin)])
def av_agent_pata_apuntar(body: ApuntarPata, email: str = Depends(get_user_email)):
    """**Apunta el master a la pata correcta** — el arreglo de `pata_equivocada`.

    Distinto de `/pata/pedir`, y esa diferencia es la que costó 17 votos: pedir
    trae una pata que **ya cotizaba** y deja `mercado.curvas` apuntando a la de
    pesos, así que el hallazgo volvía todas las ruedas. Esto corrige el campo
    (columna + blob, en un solo UPDATE) **y** pide la pata, así el precio entra
    por el `adhoc_watcher` en 5 s sin reiniciar el motor.

    Pasa por `av_agent_hacer.uno`, o sea por la MISMA acción y la MISMA
    verificación que la tab de propuestas — no es un segundo camino a la
    escritura."""
    from api.services import av_agent_hacer as svc
    return svc.uno("mercado.apuntar_pata", body.ticker,
                   aplicar_ya=body.aplicar, por=email or "")


@router.post("/av-agent/relevar", dependencies=[Depends(require_admin)])
def av_agent_relevar(alcance: str = "soberanos",
                     email: str = Depends(get_user_email)):
    """**Volver a mirar AHORA**, desde la pantalla.

    Censa 1816 (~29 créditos) y reescribe los hallazgos. Corre en background: son
    ~29 llamadas a 1 por segundo, y ningún request HTTP debería esperar eso.
    Un lock evita que dos clicks gasten el doble para escribir lo mismo."""
    from api.services import av_agent_relevar as svc
    return svc.arrancar(alcance=alcance, por=email or "")


@router.get("/av-agent/relevar", dependencies=[Depends(require_admin)])
def av_agent_relevar_estado():
    """¿Hay una relevada en curso? El modal lo usa para no ofrecer el botón dos
    veces y para poder decir «ya está corriendo» en vez de no hacer nada."""
    from api.services import av_agent_relevar as svc
    return svc.corriendo()


@router.post("/av-agent/salud/recontrolar", dependencies=[Depends(require_admin)])
def av_agent_recontrolar(control_id: str):
    """**Volver a mirar ESTE control, ahora.** Corre solo ese invariante y
    devuelve cuántos siguen, cuántos se resolvieron y cuántos aparecieron.

    Usa la MISMA persistencia que el cron, así un re-chequeo a mano y la corrida
    automática no pueden dejar estados distintos."""
    from api.services import av_agent_salud as svc
    return svc.recontrolar(control_id)
