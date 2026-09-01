"""Detectores del CATÁLOGO DE TÍTULOS. Doc: `docs/AGENT.md` §5.

Devuelven `list[Hallazgo]` o levantan `SinDatos`. **Ninguno escribe.**
"""
from __future__ import annotations

from agente import reloj
from agente.tipos import Hallazgo, SinDatos
from core.postgres import get_pool

# ── QUÉ CAMPO FALTA, Y QUÉ ROMPE CUANDO FALTA ──────────────────────────────
#
# ⚠️ **CADA REGLA DECLARA EL DAÑO, y el daño está verificado en el código, no
# supuesto.** Un aviso que dice «falta un campo» sin decir qué se rompe le pasa
# el problema entero al que lo lee, y por eso el CHECK de la base exige
# `que_hacer`. Acá se exige además el PORQUÉ: es lo que separa «completá esto
# cuando puedas» de «esto está saliendo mal en una pantalla ahora».
#
# «NO APLICA» cuenta como vacío igual que el NULL — una cartera «NO APLICA» no
# le da divisor a nadie. Es el criterio que ya tenía el control viejo.
_CARTERA_VACIA = ("(a.cartera IS NULL OR btrim(a.cartera) = '' "
                  "OR upper(btrim(a.cartera)) = 'NO APLICA')")

CAMPOS: tuple[dict, ...] = (
    {
        "campo": "cartera",
        "regla": "sin_cartera",
        "severidad": "alta",
        "falta": _CARTERA_VACIA,
        "rompe": ("la CARTERA decide el divisor de la valuación (÷100 o no), "
                  "así que sin ella la tenencia queda SIN CLASIFICAR en el AuM "
                  "y en /aca la plata cae al balde «otras»"),
    },
    {
        "campo": "clase_activo",
        "regla": "sin_clase_activo",
        "severidad": "media",
        "falta": "(a.clase_activo IS NULL OR btrim(a.clase_activo) = '')",
        "rompe": ("/aca abre por MONEDA con la regla de CLASE, que gana sobre "
                  "la de cartera: sin clase, lo que la cartera no resuelva va "
                  "a «sin_clasificar»"),
    },
    {
        "campo": "emisor",
        "regla": "sin_emisor",
        "severidad": "media",
        "falta": "(a.emisor IS NULL OR btrim(a.emisor) = '')",
        "rompe": ("cualquier cosa que AGRUPE por emisor cuenta mal — y no se "
                  "nota, porque las filas existen y suman bien por separado"),
    },
    {
        # El `ticker` solo se exige a los FCI: en el resto del catálogo puede
        # faltar sin que se rompa nada visible, y pedirlo sería ruido.
        "campo": "ticker",
        "regla": "fci_sin_ticker",
        "severidad": "alta",
        "falta": ("(upper(btrim(coalesce(a.cartera,''))) IN ('FCI','CARTERA FCI') "
                  " AND (a.ticker IS NULL OR btrim(a.ticker) = ''))"),
        "rompe": ("el fondo sale SIN NOMBRE en el detalle de /aum → FCI y, si "
                  "varios comparten el vacío, se fusionan en un renglón mudo"),
    },
)

# ⚠️⚠️ **EL ALCANCE SON LAS CARTERAS DE CLIENTES**, y no el catálogo entero
# (decisión del user 2026-08-27: *«lo ideal sería todos los que estén en
# carteras de clientes, así se logra que esas carteras no queden con los datos
# incompletos»*).
#
# Medido el 2026-08-27: el catálogo tiene 2.229 assets, 1.604 vigentes y **981
# en tenencia**. Pedirle la ficha a los 2.229 sería pedirla para papeles que
# nadie tiene: la mitad de la lista serían problemas de nadie, y una lista así
# no se vacía nunca. Con este recorte, completar lo que falta ES terminar el
# trabajo, y el contador puede llegar a cero y quedarse ahí.
#
# UNA fecha, resuelta por índice — nunca un scan de la tenencia histórica.
_ULTIMA_FECHA = "(SELECT max(fecha) FROM portafolio.tenencia)"
_EN_CARTERA_DE_CLIENTE = (
    f"EXISTS (SELECT 1 FROM portafolio.tenencia t "
    f"         WHERE t.unidad = a.unidad AND t.fecha = {_ULTIMA_FECHA})")

# Cuántas unidades viajan en la evidencia. **Es una muestra para leer el aviso,
# no la lista de trabajo**: la lista viva la recalcula el arreglo cada vez que
# se mira, así que lo que se completó ya no aparece. Guardar las 379 acá dejaría
# una foto que envejece dentro de un jsonb.
MUESTRA = 8


def _filas(sql: str, params: tuple = ()) -> list[tuple]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def faltantes(campo: dict, *, limite: int = 0) -> list[dict]:
    """Los títulos EN CARTERA DE CLIENTE a los que les falta ese campo.

    **La consulta vive acá y la usan los dos**: el detector para contar y el
    arreglo para armar el listado editable. Si cada uno tuviera la suya, el
    botón podría ofrecer un título que el aviso no cuenta —o al revés— y nadie
    lo notaría: las dos mitades seguirían siendo coherentes consigo mismas.
    """
    tope = f" LIMIT {int(limite)}" if limite else ""
    return [
        {"unidad": u, "cartera": c or "", "ticker": tk or "",
         "clase_activo": cl or "", "emisor": em or ""}
        for u, c, tk, cl, em in _filas(
            f"SELECT a.unidad, a.cartera, a.ticker, a.clase_activo, a.emisor "
            f"  FROM portafolio.assets a "
            f" WHERE {campo['falta']} AND {_EN_CARTERA_DE_CLIENTE} "
            f" ORDER BY a.cartera NULLS FIRST, a.unidad{tope}")]


def valores_usados(campo: str) -> list[str]:
    """Los valores que ese campo YA tiene en el catálogo.

    Es lo que convierte el listado en un desplegable en vez de un campo de texto
    libre. **No es cosmético**: escribir `HD ` con un espacio, o `hd` en
    minúscula, rompe los filtros que comparan exacto —el divisor del AuM, la
    Tenencia Valorizada— y no falla en el momento. Ofrecer lo que ya existe es
    la forma barata de que la próxima carga no invente una variante.
    """
    if campo not in {c["campo"] for c in CAMPOS}:      # nunca del llamador
        return []
    return [v for (v,) in _filas(
        f"SELECT DISTINCT btrim({campo}) FROM portafolio.assets "
        f" WHERE {campo} IS NOT NULL AND btrim({campo}) <> '' "
        f"   AND upper(btrim({campo})) <> 'NO APLICA' "
        f" ORDER BY 1") if v]


def ficha_incompleta(u: dict) -> list[Hallazgo]:
    """Títulos en carteras de clientes con la ficha incompleta.

    **UN hallazgo por CAMPO, no uno por título.** 379 títulos sin clase no son
    379 problemas: son UN trabajo de carga. El control viejo emitía una anomalía
    por fila y por eso su lista no se leía — y el agente la recibía aplastada en
    un único aviso genérico cuyo sujeto era el nombre del control, que es el
    defecto contrario y tan malo como el otro.

    Acá el sujeto es **el campo**, y el trío del hallazgo queda
    `ficha_incompleta + clase_activo + sin_clase_activo`: uno solo, estable, que
    sube y baja de número mientras se completa y se cierra cuando llega a cero.

    ⚠️ **La lista NO viaja en la evidencia.** Solo una muestra y el conteo: la
    lista viva la recalcula `preview` cada vez que se mira, que es la regla del
    módulo de arreglos («lo que se aplica es lo cierto AHORA»). Así lo que se
    completa desaparece del listado sin que nadie tenga que refrescar nada.
    """
    try:
        fecha = _filas(f"SELECT {_ULTIMA_FECHA}")[0][0]
    except Exception as e:
        raise SinDatos(f"no pude leer la tenencia: {e}") from e
    if fecha is None:
        raise SinDatos("no hay ninguna fecha en portafolio.tenencia")

    out = []
    for c in CAMPOS:
        try:
            filas = faltantes(c)
        except Exception as e:
            raise SinDatos(f"no pude contar «{c['campo']}»: {e}") from e
        if not filas:
            continue
        muestra = [f["unidad"] for f in filas[:MUESTRA]]
        out.append(Hallazgo(
            sujeto=c["campo"], regla=c["regla"], severidad=c["severidad"],
            nombre=c["campo"].upper(),
            problema=(f"{len(filas)} título(s) en carteras de clientes no "
                      f"tienen {c['campo'].upper()} · {reloj.hhmm()}"),
            detalle=(f"{c['rompe']} · foto de tenencia del {fecha} · "
                     + " · ".join(m[:40] for m in muestra)
                     + (f" · y {len(filas) - MUESTRA} más"
                        if len(filas) > MUESTRA else "")),
            que_hacer=("Abrir el listado y completar el campo. Se escribe desde "
                       "acá mismo y lo completado sale de la lista."),
            evidencia={"campo": c["campo"], "n": len(filas),
                       "fecha_tenencia": str(fecha),
                       # La MUESTRA, no la lista: ver §MUESTRA arriba.
                       "muestra": muestra,
                       "rompe": c["rompe"],
                       # ⚠️ **`items` NO es para la pantalla: es la IDENTIDAD
                       # (§0.cz).** El sujeto es el CAMPO, así que para el
                       # registro «CARTERA sin_cartera» de hoy y el de hace un
                       # mes son el mismo problema — y un título NUEVO sin
                       # cartera reincidía sobre un arreglo que escribió OTROS
                       # títulos. Con la lista, `registro._ver` sólo declara
                       # reincidencia si alguno de estos lo escribió la acción
                       # que cerró el anterior. Son unidades, no fichas.
                       "items": [f["unidad"] for f in filas]}))
    return out
