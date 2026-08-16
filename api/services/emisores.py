"""api/services/emisores.py — el EMISOR y su INDUSTRIA. Service puro (sin FastAPI).

## Por qué existe

La industria es un atributo del EMISOR, no del bono. Hasta hoy vivía por bono en
`mercado.curvas.sector`, que además no guardaba una industria sino la curva vieja
con el prefijo `on_` sacado (`on_energia` → `energia`).

Guardar el mismo dato N veces y esperar que nadie lo escriba distinto no funciona,
y está **medido**: 8 de 51 emisores tienen hoy sectores que se contradicen entre
sus propios bonos. Pampa Energía tiene tres valores repartidos en sus 4 bonos.
Ninguna fila está "mal" — cada una suma bien por separado — y por eso agrupar por
industria da distinto según de dónde se lea. Con el dato una sola vez, eso es
imposible por construcción.

## Las dos reglas que sostienen la clave de texto

La PK es el NOMBRE del emisor y no un id: es la clave con la que ya joinean
`mercado.curvas.emisor` y `portafolio.assets.EMISOR`. El precio es que `'YPF '` y
`'YPF'` serían dos emisores distintos, así que:

  · `normalizar()` es el ÚNICO lugar donde se decide la forma canónica, y
  · el índice único va sobre `upper(btrim(emisor))`, o sea que la base rechaza el
    duplicado aunque alguien escriba por fuera del service.

## Sin clasificar es un ESTADO, no un error

`industria = NULL` significa "todavía nadie lo decidió" y tiene que VERSE. No se
reparte a dedo ni se colapsa por mayoría: elegir por mayoría entre los tres
sectores contradictorios de Pampa es una heurística disfrazada de dato.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.postgres import get_pool


def normalizar(emisor: str | None) -> str:
    """La forma canónica de un nombre de emisor. PURA y ÚNICA.

    Solo colapsa espacios: NO toca mayúsculas ni acentos, porque el nombre que
    guardamos es el que 1816 estandarizó y ese se muestra tal cual. El índice
    único de la base hace el resto (compara en `upper(btrim(...))`), así que
    `'YPF '` y `'YPF'` chocan aunque se vean distinto.
    """
    return " ".join((emisor or "").split())


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def listar(solo_activos: bool = False) -> list[dict]:
    """El catálogo con cuántos bonos cuelga cada emisor.

    El conteo viene del master y NO de una columna guardada: un contador
    persistido se desactualiza el día que se da de alta un bono y nadie se entera.
    """
    where = "WHERE e.activo IS NOT false" if solo_activos else ""
    return _q(f"""
        SELECT e.emisor, e.industria, e.activo, e.obs, e.editado_por,
               e.editado_at,
               (SELECT count(*) FROM mercado.curvas c
                 WHERE upper(btrim(c.emisor)) = upper(btrim(e.emisor))) AS bonos
        FROM mercado.emisores e {where}
        ORDER BY e.industria NULLS FIRST, e.emisor
    """)


def industrias() -> list[str]:
    """El catálogo controlado, para el dropdown."""
    return [r["industria"] for r in _q(
        "SELECT industria FROM mercado.industrias WHERE activo IS NOT false "
        "ORDER BY industria")]


def crear_industria(industria: str) -> dict:
    nombre = normalizar(industria)
    if not nombre:
        raise ValueError("falta 'industria'")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO mercado.industrias (industria) VALUES (%s) "
                    "ON CONFLICT (industria) DO NOTHING", (nombre,))
        conn.commit()
    return {"industria": nombre}


def set_industria(emisor: str, industria: str | None, actor: str = "") -> dict:
    """Asigna la industria de un emisor. Crea la fila si no existía.

    La industria se valida contra el catálogo — igual que el `rubro` de los
    CEDEARs — porque escribirla libre es cómo nacen 'Energia', 'energía' y
    'ENERGIA' como tres industrias distintas que suman bien por separado.
    `None`/`''` la deja SIN CLASIFICAR, que es un estado válido y visible.
    """
    em = normalizar(emisor)
    if not em:
        raise ValueError("falta 'emisor'")
    ind = normalizar(industria) or None
    if ind and ind not in industrias():
        raise ValueError(f"industria inexistente: {ind!r} — creala primero")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO mercado.emisores (emisor, industria, editado_por, editado_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (emisor) DO UPDATE SET industria = EXCLUDED.industria,
                editado_por = EXCLUDED.editado_por, editado_at = EXCLUDED.editado_at
        """, (em, ind, actor or None, datetime.now(UTC)))
        conn.commit()
    return {"emisor": em, "industria": ind}


def sin_clasificar() -> list[dict]:
    """Emisores del master que NO están en el catálogo, o que están sin industria.

    Es el pendiente que tiene que ser VISIBLE. Un emisor que aparece en un bono y
    no en el catálogo no puede quedar en silencio: al agrupar por industria caería
    en un bucket vacío que se lee como "otros" y nadie lo distingue de un emisor
    realmente clasificado como otros.
    """
    return _q("""
        SELECT c.emisor, count(*) AS bonos,
               (e.emisor IS NULL) AS falta_en_catalogo
        FROM mercado.curvas c
        LEFT JOIN mercado.emisores e
               ON upper(btrim(e.emisor)) = upper(btrim(c.emisor))
        WHERE c.emisor IS NOT NULL AND btrim(c.emisor) <> ''
          AND (e.emisor IS NULL OR e.industria IS NULL)
        GROUP BY c.emisor, (e.emisor IS NULL)
        ORDER BY count(*) DESC, c.emisor
    """)


def contradicciones() -> list[dict]:
    """Emisores cuyos bonos tienen HOY sectores distintos entre sí.

    Es la evidencia de por qué la industria se muda al emisor, y sirve de guía
    para la carga: son los casos donde alguien TIENE que elegir, porque colapsar
    por mayoría sería inventar un criterio.
    """
    return _q("""
        SELECT emisor, count(*) AS bonos,
               string_agg(DISTINCT COALESCE(NULLIF(btrim(sector), ''), '(vacío)'),
                          ' · ' ORDER BY COALESCE(NULLIF(btrim(sector), ''), '(vacío)')
                         ) AS sectores
        FROM mercado.curvas
        WHERE emisor IS NOT NULL AND btrim(emisor) <> ''
        GROUP BY emisor
        HAVING count(DISTINCT COALESCE(NULLIF(btrim(sector), ''), '')) > 1
        ORDER BY count(*) DESC, emisor
    """)


def industria_por_emisor() -> dict[str, str]:
    """`emisor normalizado (upper) → industria`. Es el mapa que reemplaza a leer
    `curvas.sector`: se resuelve en la LECTURA para que el dato no se duplique."""
    return {r["k"]: r["industria"] for r in _q(
        "SELECT upper(btrim(emisor)) AS k, industria FROM mercado.emisores "
        "WHERE industria IS NOT NULL")}
