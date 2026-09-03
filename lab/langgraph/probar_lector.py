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
"""
from __future__ import annotations

import sys

from lab.langgraph.base import ESCRITOR, LECTOR, _conectar, _uri

OK, MAL = "✅", "❌"
TABLAS_QUE_LEE = ("mercado.curvas", "mercado.market_snapshot",
                  "agente.hallazgos", "agente.reincidencias",
                  "agente.acciones", "manager.job_runs")


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

        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM clientes.comitentes")
            vio = True
        except Exception:
            conn.rollback()
            vio = False
        todo &= _fila(not vio, "¿ve datos del negocio?",
                      "SÍ VE clientes/ — revisá el alcance" if vio
                      else "NO — clientes/ le es invisible")
    return todo


def _probar_escritor() -> bool:
    print(f"\n── {ESCRITOR} ──  escribe el diario y nada más\n")
    try:
        _uri(ESCRITOR)
        conn = _conectar(ESCRITOR)
    except Exception as e:
        return _fila(False, "¿conecta?", _primera_linea(e))
    todo = _fila(True, "¿conecta?", "entró")

    fila = ("PRUEBA", "PRUEBA", "no_se", "prueba", "prueba", "prueba", "prueba")
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
