"""`lab/langgraph/probar_lector.py` — ¿los permisos del lab son REALES?

    python -m lab.langgraph.probar_lector

**No pregunta qué permisos hay: los usa y mira qué contesta la base.** Es la
misma idea de `agente/seguridad.py` — lo declarado se lee, lo efectivo se
prueba. Un permiso «en los papeles» y uno real se ven iguales hasta que alguien
empuja la puerta.

Varias de las pruebas **tienen que fallar** para que esté bien. Un ✅ al lado de
«no pudo» significa que la base lo frenó, que es lo que queremos.

⚠️ Todo lo que escribe va adentro de una transacción que se DESHACE pase lo que
pase: el script que averigua si los permisos están bien no puede ser el que
ensucie la base mientras lo averigua.

⚠️⚠️ **Y AHORA MIDE EL TECHO, NO SÓLO EL PISO.**
Durante un tiempo esto comprobó que el lector PUEDE leer sus seis tablas y que
NO puede leer `clientes.comitentes`. Las dos cosas están bien y ninguna alcanza:
**nunca comprobaba que pueda leer SÓLO esas seis.** Un `GRANT SELECT ON ALL
TABLES IN SCHEMA mercado` otorgado un martes apurado pasaba los chequeos en
verde para siempre, y la única forma de enterarse era ir a mirar el catálogo.

Por eso el paso «¿lee de MÁS?» de abajo le pregunta al catálogo de Postgres por
**todo** lo que el rol puede leer y lo compara contra la lista declarada en
`sql/lab.sql`. Un permiso de más se canta con nombre y apellido — y correr
`apply_schema` lo saca, porque ese archivo revoca antes de otorgar.
"""
from __future__ import annotations

import sys

from lab.langgraph.base import ESCRITOR, LECTOR, _conectar, _uri

OK, MAL = "✅", "❌"
# ⚠️ **ESTA LISTA ES EL ESPEJO DE `sql/lab.sql`**, y sirve para las dos mitades:
# el piso (¿puede leerlas?) y el TECHO (¿puede leer algo MÁS que esto?). Si una
# herramienta nueva necesita otra tabla, se agrega en los dos lados — el archivo
# SQL la otorga y esta lista la espera. Divergir hace fallar el chequeo, que es
# exactamente lo que queremos: dos listas que no se hablan es la REGLA #9.
TABLAS_QUE_LEE = ("mercado.curvas", "mercado.market_snapshot",
                  "agente.hallazgos", "agente.reincidencias",
                  "agente.acciones", "manager.job_runs")

# Lo que el LECTOR puede tocar además de producción: su propio esquema.
LAB_QUE_LEE = ("lab.investigaciones", "lab.pedidos")

# ⚠️ **EL NEGOCIO, QUE NO PUEDE VER NI DE CASUALIDAD.** Antes se probaba con UNA
# sola tabla (`clientes.comitentes`) — como probar que la casa está cerrada
# tocando el picaporte de adelante y no mirar las ventanas. Estas son las cuatro
# familias que la REGLA #8 llama «el negocio de la mesa»: quién es el cliente,
# cuánto tiene, qué operó y quién puede qué.
NEGOCIO_PROHIBIDO = ("clientes.comitentes", "portafolio.tenencia",
                     "operaciones.operaciones", "manager.manager_users")

# ⚠️ LAS VISTAS VAN APARTE, y no por prolijidad: en una tabla de producción el
# CERO es sospechoso (huele a RLS sin política), pero `v_ahora` vacía es una
# respuesta LEGÍTIMA — quiere decir que hoy no pasó nada. Lo que se prueba acá
# es que CONTESTEN, no cuánto traen. Meterlas en la lista de arriba habría dado
# un ❌ cualquier día tranquilo, que es la forma más rápida de que nadie mire
# más este script.
#
# Están en la lista porque el `DROP VIEW` de `sql/schema.sql` se lleva puestos
# los GRANT en cada deploy: es el único permiso del lab que se puede caer solo,
# sin que nadie toque nada.
VISTAS_QUE_LEE = ("agente.v_ahora", "agente.v_encontro")


def _fila(bien: bool, pregunta: str, detalle: str) -> bool:
    print(f"  {OK if bien else MAL}  {pregunta:38} {detalle}")
    return bien


def _primera_linea(e: Exception) -> str:
    return str(e).strip().splitlines()[0][:95]


def _probar_lector() -> bool:
    print(f"\n── {LECTOR} ──  lee producción, no puede escribir\n")
    try:
        conn = _conectar(LECTOR)
    except Exception as e:
        return _fila(False, "¿conecta?", _primera_linea(e))
    todo = _fila(True, "¿conecta?", "entró")

    with conn:
        for tabla in TABLAS_QUE_LEE:
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT count(*) FROM {tabla}")
                    n = cur.fetchone()[0]
                # ⚠️ CERO es sospechoso, no bueno: con RLS prendido y sin
                # política la base devuelve vacío SIN error, y ahí «no hay
                # nada» y «no puedo mirar» se ven iguales. Ya pasó con
                # `mercado`.
                todo &= _fila(n > 0, f"¿lee {tabla}?",
                              f"{n} filas" + ("" if n else "  ← ¿RLS sin política?"))
            except Exception as e:
                conn.rollback()
                todo &= _fila(False, f"¿lee {tabla}?", _primera_linea(e))

        for vista in VISTAS_QUE_LEE:
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT count(*) FROM {vista}")
                    n = cur.fetchone()[0]
                todo &= _fila(True, f"¿lee {vista}?", f"contesta ({n} filas)")
            except Exception as e:
                conn.rollback()
                todo &= _fila(False, f"¿lee {vista}?", _primera_linea(e)
                              + "  ← ¿corriste apply_schema?")

        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE mercado.curvas SET emisor = emisor "
                            "WHERE ticker = (SELECT ticker FROM mercado.curvas "
                            "                LIMIT 1)")
            escribio, motivo = True, "SÍ PUDO — el permiso está mal"
        except Exception as e:
            escribio, motivo = False, "NO — " + _primera_linea(e)
        finally:
            conn.rollback()
        todo &= _fila(not escribio, "¿puede ESCRIBIR en producción?", motivo)

        # ⚠️ LAS CUATRO FAMILIAS DEL NEGOCIO, no una. Cada una se prueba aparte
        # porque «no ve clientes» no dice nada sobre si ve las tenencias.
        for tabla in NEGOCIO_PROHIBIDO:
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT count(*) FROM {tabla}")
                vio = True
            except Exception:
                conn.rollback()
                vio = False
            todo &= _fila(not vio, f"¿NO ve {tabla}?",
                          "LA VE — revisá el alcance" if vio else "invisible")

        todo &= _probar_techo(conn)
    return todo


def _probar_techo(conn) -> bool:
    """¿Lee ALGO MÁS de lo declarado? El chequeo que faltaba.

    Le pregunta al catálogo por todo lo que `lector_lab` puede SELECTear y lo
    resta contra la lista declarada. Sin esto, un permiso otorgado a mano vivía
    para siempre y ningún chequeo lo veía — el script decía que todo estaba bien
    porque sólo miraba las puertas que esperaba encontrar abiertas.

    Se lee del catálogo y no probando tabla por tabla, porque probar exige saber
    de antemano qué buscar: justo lo que no se puede saber de un permiso que
    nadie escribió.
    """
    declarado = {*TABLAS_QUE_LEE, *LAB_QUE_LEE,
                 "agente.v_ahora", "agente.v_encontro", "agente.v_habilidades"}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_schema || '.' || table_name"
                "  FROM information_schema.table_privileges"
                " WHERE grantee = 'lector_lab' AND privilege_type = 'SELECT'"
                " ORDER BY 1")
            puede = {f[0] for f in cur.fetchall()}
    except Exception as e:
        conn.rollback()
        # No poder mirar el catálogo NO es un ✅: es no haber mirado. La misma
        # regla que rige adentro del agente (invariante #1).
        return _fila(False, "¿lee de MÁS que lo declarado?",
                     "NO PUDE VERIFICARLO — " + _primera_linea(e))
    de_mas = sorted(puede - declarado)
    if not puede:
        return _fila(False, "¿lee de MÁS que lo declarado?",
                     "el catálogo no devolvió NADA — ¿es este el rol?")
    return _fila(not de_mas, "¿lee de MÁS que lo declarado?",
                 f"SÍ, {len(de_mas)}: {', '.join(de_mas[:4])}"
                 f"{'…' if len(de_mas) > 4 else ''}  ← `python -m scripts.apply_schema` "
                 f"lo revoca" if de_mas else f"no — exactamente las {len(puede)} declaradas")


def _probar_escritor() -> bool:
    print(f"\n── {ESCRITOR} ──  escribe el diario y nada más\n")
    try:
        _uri(ESCRITOR)
        conn = _conectar(ESCRITOR)
    except Exception as e:
        return _fila(False, "¿conecta?", _primera_linea(e))
    todo = _fila(True, "¿conecta?", "entró")

    # ⚠️ Las cuatro del medio son `text[]`, no `text`. Mandarles un string suelto
    # hace fallar la prueba por el motivo equivocado y parece un permiso roto.
    fila = ("PRUEBA", "PRUEBA", "no_se",
            ["prueba"], ["prueba"], ["prueba"], ["prueba"])
    with conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO lab.investigaciones (tipo, caso, de_quien_es, "
                    " que_paso, por_que, que_haria, lo_que_no_se, de_donde, "
                    " piso_cubierto, corto_por_presupuesto, vueltas) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,false,1) RETURNING id",
                    (*fila, ["prueba"]))
                nid = cur.fetchone()[0]
            todo &= _fila(True, "¿escribe el diario?", f"insertó la fila {nid}")
        except Exception as e:
            conn.rollback()
            todo &= _fila(False, "¿escribe el diario?", _primera_linea(e))

        # EL CHECK: un veredicto sin fuentes NO puede entrar.
        #
        # ⚠️ **NO ALCANZA CON QUE FALLE: TIENE QUE FALLAR POR EL CHECK.** La
        # primera versión daba ✅ cuando la tabla ni existía —falló, luego está
        # bien— que es el mismo error que este laboratorio existe para cazar:
        # «no pude mirar» disfrazado de «está todo bien». Se exige que el error
        # NOMBRE la restricción.
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO lab.investigaciones (tipo, caso, de_quien_es, "
                    " que_paso, por_que, que_haria, lo_que_no_se, de_donde, "
                    " piso_cubierto, corto_por_presupuesto, vueltas) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,false,1)",
                    (*fila, []))
            entro, motivo = True, "SÍ ENTRÓ — el CHECK no está"
        except Exception as e:
            entro = False
            fue_el_check = "inv_fuentes" in str(e)
            motivo = ("NO — la rechaza el CHECK inv_fuentes" if fue_el_check
                      else "falló por OTRA cosa, no probó el CHECK: "
                           + _primera_linea(e))
        finally:
            conn.rollback()
        todo &= _fila(not entro and "inv_fuentes" in motivo,
                      "¿entra un veredicto SIN fuentes?", motivo)

        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE mercado.curvas SET emisor = emisor "
                            "WHERE ticker = (SELECT ticker FROM mercado.curvas "
                            "                LIMIT 1)")
            toco = True
        except Exception:
            toco = False
        finally:
            conn.rollback()
        todo &= _fila(not toco, "¿puede tocar producción?",
                      "SÍ PUDO — el permiso está mal" if toco
                      else "NO — sólo escribe el diario")

    # ⚠️ Y que la prueba no haya dejado basura. Todo lo de arriba va adentro de
    # transacciones que se deshacen, pero eso es una afirmación sobre el código:
    # esto la verifica contra la base, que es donde importa.
    try:
        with _conectar(ESCRITOR) as c2, c2.cursor() as cur:
            cur.execute("SELECT count(*) FROM lab.investigaciones "
                        " WHERE tipo = 'PRUEBA'")
            quedaron = cur.fetchone()[0]
        todo &= _fila(quedaron == 0, "¿la prueba dejó basura?",
                      "NO — no quedó ninguna fila" if not quedaron
                      else f"SÍ — quedaron {quedaron} fila(s) de prueba")
    except Exception as e:
        todo &= _fila(False, "¿la prueba dejó basura?", _primera_linea(e))
    return todo


def main() -> int:
    bien = _probar_lector()
    bien &= _probar_escritor()
    print("\n" + ("✅ LOS DOS ROLES ESTÁN BIEN." if bien else
                  "❌ ALGO NO CIERRA — mirá las líneas con ❌ antes de seguir.")
          + "\n")
    return 0 if bien else 1


if __name__ == "__main__":
    sys.exit(main())
