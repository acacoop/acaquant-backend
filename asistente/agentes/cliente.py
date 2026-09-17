"""Agente CLIENTE: QUIÉN es el titular de una cuenta y cómo está comercialmente.
Contacto, documento, operador, segmento, estado, grupo. Nada de plata ni de
tenencias. Sus herramientas y su agente, en un solo archivo. Doc: docs/AvAgentAI.md.

Dato PERSONAL: la tarea lleva `traza_sin_texto` (la traza guarda tokens y
latencia, no el pedido ni la respuesta). El documento se muestra recortado.
"""
from __future__ import annotations

from asistente import estado as EST
from asistente import permitido
from asistente.agente import COMUN, Agente
from core.postgres import get_pool

# Cuántos dígitos del documento se muestran. Alcanza para reconocerlo, no para copiarlo.
DOC_VISIBLES = 3


def _recortar_doc(nro) -> str | None:
    s = str(nro or "").strip()
    if not s:
        return None
    return "…" + s[-DOC_VISIBLES:] if len(s) > DOC_VISIBLES else s


def ficha_cliente(cuenta: str) -> dict:
    """QUIÉN es el titular de una cuenta y cómo está comercialmente.

    Contesta «¿quién es la 805?», «¿cómo lo contacto?», «¿quién lo atiende?»,
    «¿en qué segmento está?», «¿está activo?», «¿de qué grupo es?».

    NO es lo que tiene ni lo que cobra: para eso están las herramientas de
    cartera. Acá no hay plata ni nominales: hay personas y estados.

    `cuenta` es OBLIGATORIA: el `id_cuenta` sale de la lista de cuentas
    habilitadas de las instrucciones.

    QUÉ DEVUELVE:
      · `titular` — denominación, tipo de titular y de cliente, documento
        (recortado a propósito: no lo completes), email, teléfono, provincia.
      · `comercial` — operador que lo atiende, segmentación por niveles,
        estado legal y estado comercial, fecha de alta, perfil de inversión,
        riesgo, quién lo refirió, observaciones.
      · `grupos` — los grupos de la plataforma en los que está la cuenta.
      · `sin_legajo` — true si la cuenta existe pero no tiene ficha cargada.

    Args:
        cuenta: el `id_cuenta` a mirar.
    """
    try:
        params = permitido.parametros()
    except permitido.SinPermiso:
        return permitido.como_error()
    pedida = str(cuenta or "").strip()
    if pedida not in permitido.cuentas():
        return {"error": f"la cuenta {pedida!r} no está habilitada para el asistente",
                "cuentas_habilitadas": permitido.cuentas(),
                "que_hacer": "Preguntale al usuario cuál de las cuentas habilitadas quiere."}
    params["cuenta"] = pedida
    sql_ficha = f"""
        SELECT cu.denominacion, t.tipo_titular, t.tipo_cliente, t.tipo_doc, t.nro_doc,
               t.email, t.telefono, t.provincia, t.sucursal,
               o.nombre AS operador, t.operador_email,
               t.nivel_1, t.nivel_2, t.nivel_3, t.segmento_patrimonial,
               t.estado, t.estado_comercial, t.fecha_alta_legajo, t.perfil_inversion,
               t.riesgo_la_ft, t.referido, t.observaciones
          FROM clientes.cuentas cu
          LEFT JOIN clientes.comitentes t ON t.id_cuenta = cu.id_cuenta
          LEFT JOIN clientes.operadores o ON o.email = t.operador_email
         WHERE cu.id_cuenta = %(cuenta)s AND cu.id_cuenta = ANY(%(cuentas_permitidas)s)
           AND ({permitido.FILTRO_SQL} OR t.id_cuenta IS NULL)
    """
    sql_grupos = "SELECT nombre FROM manager.grupos WHERE %(cuenta)s = ANY(id_cuentas) ORDER BY nombre"
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql_ficha, params)
            fila = cur.fetchone()
            cur.execute(sql_grupos, params)
            grupos = [r[0] for r in cur.fetchall()]
    except Exception as e:
        return {"error": f"no pude leer la ficha: {type(e).__name__}: {e}"}
    if fila is None:
        return {"error": f"la cuenta {pedida} no existe en el padrón de cuentas"}
    (denominacion, tipo_titular, tipo_cliente, tipo_doc, nro_doc, email, telefono, provincia,
     sucursal, operador, operador_email, n1, n2, n3, seg_pat, estado, estado_com, alta,
     perfil, riesgo, referido, observaciones) = fila
    # La cuenta existe en el padrón pero no hay fila en comitentes: todo lo del
    # LEFT JOIN viene NULL. No es una heurística sobre algunos campos.
    sin_legajo = all(x is None for x in fila[1:])
    return {
        "cuenta": pedida,
        "titular": {
            "denominacion": denominacion,
            "tipo_titular": tipo_titular,
            "tipo_cliente": tipo_cliente,
            "documento": f"{tipo_doc} {_recortar_doc(nro_doc)}" if tipo_doc or nro_doc else None,
            "email": email,
            "telefono": telefono,
            "provincia": provincia,
            "sucursal": sucursal,
        },
        "comercial": {
            "operador": operador or operador_email,
            "segmento": [x for x in (n1, n2, n3) if x],
            "segmento_patrimonial": seg_pat,
            "estado": estado,
            "estado_comercial": estado_com,
            "alta": alta.isoformat() if alta else None,
            "perfil_inversion": perfil,
            "riesgo": riesgo,
            "referido": referido,
            "observaciones": observaciones,
        },
        "grupos": grupos,
        "sin_legajo": sin_legajo,
    }


# ── el agente ───────────────────────────────────────────────────────────────

_INSTRUCCION = """
Hablás de QUIÉN es el cliente y cómo está: contacto, operador, segmento,
estado. Un dato personal se dice solo si lo pidieron, y tal cual vino: el
documento llega recortado a propósito, no lo completes ni lo adivines. Si el
usuario ya nombró una cuenta —en esta pregunta o antes: la que está en
foco—, usala. Si no hay ninguna y hay más de una habilitada, preguntá cuál.
"""


def _instruccion(foco: dict) -> str:
    cuentas = permitido.cuentas()
    partes = [COMUN, _INSTRUCCION]
    if cuentas:
        partes.append("Cuentas habilitadas (las ÚNICAS que podés consultar; el número es el "
                      f"`cuenta` de la herramienta): {', '.join(cuentas)}\n")
    partes.append(EST.como_texto(foco))
    return "".join(partes)


AGENTE = Agente(
    nombre="cliente",
    tarea="asistente_cliente",
    describe="QUIÉN es el titular de una cuenta y cómo está comercialmente: nombre, contacto, "
             "documento, operador que lo atiende, segmento, estado, grupo. Nada de plata, "
             "tenencias ni operaciones.",
    instruccion=_instruccion,
    herramientas=(ficha_cliente,),
    senales=("cliente", "clientes", "titular", "quien es", "contacto", "mail", "email",
             "telefono", "documento", "cuit", "dni", "operador", "atiende", "segmento", "grupo",
             "activo", "activa", "dormido", "dormida", "legajo", "perfil"),
    foco=("cuenta",),
)
