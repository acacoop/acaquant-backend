"""`scripts/diag_contrapartes.py` — LOS NÚMEROS QUE FALTAN PARA QUE EL AGENTE
VIGILE LAS CONTRAPARTES. Read-only: no escribe una sola fila.

Existe por la REGLA #2. Para agregarle al agente la habilidad «avisame cuando
entra una cuenta nueva que es contraparte» hay que decidir tres cosas, y las
tres dependen de datos que sólo están en prod:

    1. ¿DE QUÉ TAMAÑO ES LA LISTA de pendientes? Una lista de 600 cuentas que
       nunca llega a cero es una lista que nadie mira. Si son 12, la habilidad
       sirve desde el día uno. Este número decide la FORMA de la regla.
    2. ¿CUÁL ES EL VOCABULARIO REAL de `segmento`, y qué señal lo predice?
       `inferir_segmento` conoce tres valores (Fondos, ALYC, Bancos) y decide
       con tres `if` sobre el nombre. Las ~100 filas que la mesa ya clasificó A
       MANO son un set de validación gratis: acá se mide cuánto reproduce cada
       señal candidata, en vez de proponer reglas de memoria.
    3. ¿QUÉ COLUMNAS TIENE DE VERDAD la base? El código lee dos columnas que
       `sql/schema.sql` NO declara (`comitentes.created_at` y
       `contrapartes.denominacion`). O la base está adelante del schema —y
       entonces `apply_schema` no puede reconstruirla— o algo viene fallando en
       silencio. Es lo primero que imprime.

    python -m scripts.diag_contrapartes
    python -m scripts.diag_contrapartes --aunesa   # + qué ve el conciliador que SQL no

El DISEÑO completo de la habilidad —cómo funciona el conciliador hoy, sus tres
defectos, qué efecto tiene sobre el AuM equivocarse, y las siete piezas de la
arquitectura propuesta— está en `docs/AGENT.md` §0.eq. Acá sólo se miden los
números que esa entrada deja abiertos.

⚠️ Es un one-shot de diseño: cuando la habilidad esté hecha y calibrada, se
borra (REGLA #5).
"""
from __future__ import annotations

import argparse
import re
import sys

from core.postgres import get_pool

# ── LAS SEÑALES CANDIDATAS ────────────────────────────────────────────────
#
# ⚠️ **NO SON REGLAS: SON PREGUNTAS.** Ninguna decide nada acá — el diag mide
# contra qué segmento cae cada una en las filas que YA clasificó una persona, y
# recién con esa tabla a la vista se escribe la regla. Proponer el mapeo desde
# el código sería inventarlo: el vocabulario de `segmento` es editable a mano y
# nadie lo declaró en ningún lado.
#
# Tokens de FORMA LEGAL, con `\b` y en el orden en que se prueban (el primero
# que matchea gana, por eso los más específicos van arriba).
TOKENS: tuple[tuple[str, str], ...] = (
    ("SGFCI",        r"\bS\.?G\.?F\.?C\.?I\.?\b|SOCIEDAD GERENTE"),
    ("FCI",          r"\bFCI\b|\bF\.?C\.?I\.?\b|FONDO COMUN|FONDOS? COMUN"),
    ("BANCO",        r"\bBANCO\b|\bBANK\b|\bBCO\b"),
    ("ALYC",         r"\bALYC\b|AGENTE DE LIQUIDACION|\bSOC\.? DE BOLSA\b|SOCIEDAD DE BOLSA"),
    ("SEGUROS",      r"\bSEGUROS?\b|\bASEGURADORA\b|\bA\.?R\.?T\.?\b|\bRETIRO\b"),
    ("COOPERATIVA",  r"\bCOOPERATIVA\b|\bCOOP\b|\bMUTUAL\b"),
    ("FIDEICOMISO",  r"\bFIDEICOMISO\b|\bFID\b|\bTRUST\b"),
    ("SGR",          r"\bS\.?G\.?R\.?\b|GARANTIA RECIPROCA"),
    ("BURSATIL",     r"\bBURSATIL\b|\bBOLSA\b|\bVALORES\b|\bINVERSIONES\b"),
)
_TOKENS_RE = tuple((n, re.compile(p)) for n, p in TOKENS)

# Persona física según Aunesa — el MISMO criterio que usa el conciliador hoy
# (`api.services.segmentacion._TIPOS_PH`). No se copia el literal: se importa,
# porque dos definiciones de «persona física» darían dos listas distintas de
# candidatas sin que ninguna falle (REGLA #9).


def _filas(sql: str, params: tuple = ()) -> list[tuple]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _uno(sql: str, params: tuple = ()):
    f = _filas(sql, params)
    return f[0][0] if f else None


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


def _token(den: str | None) -> str:
    d = (den or "").upper()
    for nombre, rx in _TOKENS_RE:
        if rx.search(d):
            return nombre
    return "—"


# ── 1. LA BASE CONTRA EL SCHEMA ───────────────────────────────────────────
def _columnas() -> None:
    _titulo("1. QUÉ COLUMNAS TIENE DE VERDAD (base contra `sql/schema.sql`)")
    print("El código lee dos columnas que el schema del repo NO declara. Si acá\n"
          "aparecen, la base está ADELANTE del schema y `apply_schema` no puede\n"
          "reconstruirla; si NO aparecen, algo viene fallando callado.\n")
    esperadas = (("clientes", "comitentes", "created_at",
                  "lo escribe `jobs/sync_comitentes` en cada INSERT"),
                 ("clientes", "comitentes", "updated_at",
                  "lo escribe `jobs/sync_comitentes` en cada INSERT"),
                 ("clientes", "contrapartes", "denominacion",
                  "la lee `contrapartes_seg._DEN` en TODA consulta del panel"))
    for esquema, tabla, col, quien in esperadas:
        hay = _uno("SELECT count(*) FROM information_schema.columns "
                   " WHERE table_schema = %s AND table_name = %s AND column_name = %s",
                   (esquema, tabla, col))
        marca = "ESTÁ" if hay else "⚠ NO ESTÁ"
        print(f"  {marca:>10}  {esquema}.{tabla}.{col:<14} — {quien}")
    # ⚠️ **LA PRUEBA DECISIVA, y cuesta una query.** Si una de esas columnas no
    # existe, `sync_comitentes` no puede haber escrito nunca — y entonces las
    # últimas corridas están en `error`. Si están en `ok`, la columna está y el
    # que quedó viejo es el schema del repo.
    print("\n  Últimas 5 corridas de `sync_comitentes` (el job que las escribe):")
    # ⚠️ La clave es `errors` y es una LISTA (`core/job_runs.py`), no un string
    # `error`: leerla mal devolvía NULL siempre y el diag habría dicho «ninguna
    # corrida falló» sin haber mirado nada.
    corridas = _filas("SELECT started_at, status, "
                      "       left(coalesce(data->>'errors', ''), 110) "
                      "  FROM manager.job_runs WHERE tipo = 'sync_comitentes' "
                      " ORDER BY started_at DESC LIMIT 5")
    if not corridas:
        print("      (ninguna) — el job nunca dejó rastro: ese es el problema, no el schema.")
    for (at, st, err) in corridas:
        print(f"      {str(at)[:19]}  {st!s:<8} {err}")

    print("\n  Columnas reales de `clientes.contrapartes`:")
    for (c, t) in _filas("SELECT column_name, data_type FROM information_schema.columns "
                         " WHERE table_schema = 'clientes' AND table_name = 'contrapartes' "
                         " ORDER BY ordinal_position"):
        print(f"      {c:<20} {t}")


# ── 2. EL PADRÓN ──────────────────────────────────────────────────────────
def _padron() -> None:
    _titulo("2. EL PADRÓN — de qué tamaño es todo")
    n_cuentas = _uno("SELECT count(*) FROM clientes.cuentas")
    n_com = _uno("SELECT count(*) FROM clientes.comitentes")
    n_act = _uno("SELECT count(*) FROM clientes.comitentes WHERE estado = 'Activa'")
    n_cp = _uno("SELECT count(*) FROM clientes.contrapartes")
    print(f"  clientes.cuentas ................ {n_cuentas}")
    print(f"  clientes.comitentes ............. {n_com}  (Activa: {n_act})")
    print(f"  clientes.contrapartes ........... {n_cp}")
    print("\n  Contrapartes por `origen` (quién las cargó):")
    for (o, n) in _filas("SELECT coalesce(origen,'(sin origen)'), count(*) "
                         "  FROM clientes.contrapartes GROUP BY 1 ORDER BY 2 DESC"):
        print(f"      {o:<20} {n}")
    print("\n  ⚠️ Cuántas contrapartes NO están en `comitentes` (el sync sólo trae")
    print("     tipo=Comitente + Activa, así que el conciliador ve cosas que SQL no):")
    n_huerf = _uno("SELECT count(*) FROM clientes.contrapartes c "
                   " WHERE NOT EXISTS (SELECT 1 FROM clientes.comitentes m "
                   "                    WHERE m.id_cuenta = c.id_cuenta)")
    print(f"      {n_huerf} de {n_cp}")
    if n_huerf:
        print("      → el detector NO puede leer sólo `comitentes`: se pierde ese pedazo.")


# ── 3. LOS VACÍOS DE LO QUE YA ESTÁ ───────────────────────────────────────
def _vacios() -> None:
    _titulo("3. LOS VACÍOS — trabajo que la habilidad podría cantar desde el día uno")
    print("Es el filtro «VACÍOS EN» de la pantalla, contado del lado del servidor.\n"
          "Estas tres son las reglas más baratas de la habilidad nueva: el sujeto\n"
          "es el CAMPO, la lista baja mientras se completa y puede llegar a CERO.\n")
    vacio = "({0} IS NULL OR btrim({0}) = '')"
    for campo in ("contraparte", "segmento", "codigo_mae"):
        n = _uno(f"SELECT count(*) FROM clientes.contrapartes WHERE {vacio.format(campo)}")
        print(f"  sin {campo:<14} {n}")


# ── 4. EL VOCABULARIO REAL ────────────────────────────────────────────────
def _vocabulario() -> None:
    _titulo("4. EL VOCABULARIO REAL DE `segmento` — lo que la mesa escribió a mano")
    print("`inferir_segmento` sólo sabe devolver Fondos, ALYC y Bancos. Todo lo\n"
          "que aparezca acá y no esté en esa lista es un segmento que el\n"
          "conciliador NUNCA va a sugerir.\n")
    for (s, n) in _filas("SELECT coalesce(nullif(btrim(segmento),''),'(vacío)'), count(*) "
                         "  FROM clientes.contrapartes GROUP BY 1 ORDER BY 2 DESC"):
        conocido = "" if s in ("Fondos", "ALYC", "Bancos", "(vacío)") else "   ← no lo sugiere nadie"
        print(f"      {s:<28} {n}{conocido}")
    print("\n  Y los NOMBRES de contraparte más repetidos (cada uno es una keyword")
    print("  del conciliador — de ahí sale su único criterio de búsqueda):")
    for (c, n) in _filas("SELECT btrim(contraparte), count(*) FROM clientes.contrapartes "
                         " WHERE contraparte IS NOT NULL AND btrim(contraparte) <> '' "
                         " GROUP BY 1 ORDER BY 2 DESC LIMIT 15"):
        print(f"      {c:<34} {n} cuenta(s)")


# ── 5. QUÉ SEÑAL PREDICE EL SEGMENTO ──────────────────────────────────────
def _senales() -> None:
    from api.services.contrapartes_seg import inferir_segmento
    from api.services.segmentacion import _TIPOS_PH

    _titulo("5. QUÉ SEÑAL PREDICE EL SEGMENTO — medido contra lo clasificado a mano")
    print("⚠️ NO propone reglas: cruza cada señal contra el `segmento` que YA puso\n"
          "una persona. Si una fila de abajo dice «41 filas, 40 son Fondos», la\n"
          "regla se escribe sola. Si sale repartido, esa señal no sirve.\n")

    filas = _filas(
        "SELECT c.id_cuenta, btrim(coalesce(c.segmento,'')), "
        "       coalesce(m.tipo_cliente,''), "
        # ⚠️ La denominación sale de `clientes.cuentas` (la escribe el sync
        # desde Aunesa), que es la MISMA fuente que mira el conciliador en
        # vivo. No es un proxy: es el mismo string un rato después.
        "       upper(coalesce(u.denominacion, '')), "
        "       btrim(coalesce(c.contraparte,'')) "
        "  FROM clientes.contrapartes c "
        "  LEFT JOIN clientes.comitentes m ON m.id_cuenta = c.id_cuenta "
        "  LEFT JOIN clientes.cuentas    u ON u.id_cuenta = c.id_cuenta "
        " WHERE btrim(coalesce(c.segmento,'')) <> ''")
    if not filas:
        print("  No hay ninguna contraparte con `segmento` cargado: sin set de")
        print("  validación no se puede medir nada. Cargar unas cuantas a mano primero.")
        return
    print(f"  Set de validación: {len(filas)} contraparte(s) con segmento puesto a mano.\n")

    def _cruce(titulo: str, clave) -> None:
        print(f"  ── {titulo} " + "─" * max(0, 60 - len(titulo)))
        agrupado: dict[str, dict[str, int]] = {}
        for f in filas:
            k, seg = clave(f) or "(vacío)", f[1]
            agrupado.setdefault(k, {})[seg] = agrupado.setdefault(k, {}).get(seg, 0) + 1
        for k in sorted(agrupado, key=lambda x: -sum(agrupado[x].values())):
            tot = sum(agrupado[k].values())
            dom, n = max(agrupado[k].items(), key=lambda kv: kv[1])
            resto = " · ".join(f"{s}:{c}" for s, c in
                               sorted(agrupado[k].items(), key=lambda kv: -kv[1])[1:5])
            print(f"      {k:<22} {tot:>4} filas → {dom} en {n} ({100 * n // tot}%)"
                  + (f"   [{resto}]" if resto else ""))
        print()

    _cruce("tipo_cliente de Aunesa (hoy sólo se usa para excluir personas)",
           lambda f: f[2])
    _cruce("token de forma legal en la denominación", lambda f: _token(f[3]))

    # ── LA LÍNEA DE BASE: qué diría HOY `inferir_segmento` ─────────────────
    print("  ── LÍNEA DE BASE: qué acierta HOY `inferir_segmento` " + "─" * 22)
    acierta = contradice = calla = 0
    ejemplos: list[str] = []
    # ⚠️ Se le pasa la CONTRAPARTE de la fila, no `None`: `inferir_segmento`
    # tiene una rama que mira ese campo (`"ALYC" in cp`), y llamarla sin él
    # mediría una función que no es la que corre en producción.
    for idc, seg, _tipo, den, cp in filas:
        sug = inferir_segmento(den, cp)
        if sug is None:
            calla += 1
        elif sug == seg:
            acierta += 1
        else:
            contradice += 1
            if len(ejemplos) < 8:
                ejemplos.append(f"{idc}: dice «{sug}», la mesa puso «{seg}» — {den[:40]}")
    print(f"      acierta ....... {acierta} de {len(filas)}")
    print(f"      CONTRADICE .... {contradice}   ← lo caro: sugiere mal y alguien lo acepta")
    print(f"      no opina ...... {calla}")
    for e in ejemplos:
        print(f"        · {e}")
    print(f"\n  Personas físicas excluidas por el conciliador: tipo_cliente ∈ {sorted(_TIPOS_PH)}")


# ── 6. EL TAMAÑO DE LA LISTA DE PENDIENTES ────────────────────────────────
def _pendientes() -> None:
    from api.services.segmentacion import _TIPOS_PH

    _titulo("6. EL TAMAÑO DE LA LISTA — cuántas cuentas quedarían pendientes")
    print("⚠️ **ES EL NÚMERO QUE DECIDE SI LA HABILIDAD SIRVE.** Una lista que no\n"
          "puede llegar a cero no la mira nadie: es la lección de `ficha_incompleta`,\n"
          "que se acotó a las carteras de clientes justo por esto.\n")
    ph = list(_TIPOS_PH)
    base = ("  FROM clientes.comitentes m "
            "  JOIN clientes.cuentas u ON u.id_cuenta = m.id_cuenta "
            " WHERE m.estado = 'Activa' "
            "   AND coalesce(m.tipo_cliente,'') <> ALL(%s) "
            "   AND NOT EXISTS (SELECT 1 FROM clientes.contrapartes c "
            "                    WHERE c.id_cuenta = m.id_cuenta)")
    total = _uno("SELECT count(*) " + base, (ph,))
    print(f"  Cuentas Activas NO persona física y SIN fila en contrapartes: {total}")
    if not total:
        print("  → no hay nada pendiente: la habilidad nacería en cero. Bien.")
        return

    print("\n  Repartidas por `tipo_cliente` (la señal fuerte que hoy se tira):")
    for (t, n) in _filas("SELECT coalesce(nullif(m.tipo_cliente,''),'(sin tipo)'), count(*) "
                         + base + " GROUP BY 1 ORDER BY 2 DESC", (ph,)):
        print(f"      {t:<32} {n}")

    print("\n  Repartidas por token de forma legal en la denominación:")
    dens = _filas("SELECT upper(coalesce(u.denominacion,'')) " + base, (ph,))
    por_token: dict[str, int] = {}
    for (d,) in dens:
        k = _token(d)
        por_token[k] = por_token.get(k, 0) + 1
    for k in sorted(por_token, key=lambda x: -por_token[x]):
        nota = "   ← sin ninguna señal: el 90% de esto NO es contraparte" if k == "—" else ""
        print(f"      {k:<32} {por_token[k]}{nota}")

    print("\n  Y cuántas encuentra HOY el conciliador (keyword de contraparte conocida):")
    kws = [str(r[0]).strip().upper() for r in
           _filas("SELECT DISTINCT contraparte FROM clientes.contrapartes "
                  " WHERE contraparte IS NOT NULL AND length(btrim(contraparte)) >= 3")]
    kws = [k for k in kws if k and k not in ("NO APLICA", "N/A", "NONE", "NULL", "-")]
    rx = re.compile(r"\b(" + "|".join(re.escape(k) for k in
                                      sorted(kws, key=lambda x: (-len(x), x))) + r")\b") if kws else None
    con_kw = sum(1 for (d,) in dens if rx and rx.search(d))
    print(f"      {con_kw} de {total}   ← el conciliador SÓLO ve estas")
    print(f"      {total - con_kw} son invisibles para él: son las contrapartes NUEVAS,")
    print("      las que no comparten nombre con ninguna que ya tengamos.")


# ── 7. LO QUE SQL NO VE (opcional: pega Aunesa una vez) ───────────────────
def _aunesa() -> None:
    from api.services.segmentacion import _TIPOS_PH
    from core import aunesa

    _titulo("7. LO QUE SQL NO VE — Aunesa en vivo contra `clientes.cuentas`")
    print("El conciliador pide TODOS los tipos de cuenta; `sync_comitentes` pide\n"
          "sólo `tipoCuenta=Comitente`. Este número dice si un detector que lee\n"
          "SQL se pierde algo — y por lo tanto si hace falta una FOTO completa.\n")
    r = aunesa.get("cuentas/listadoCuentas")
    if r.status_code != 200:
        print(f"  ⚠ Aunesa devolvió {r.status_code}: no se puede comparar.")
        return
    data = r.json()
    if isinstance(data, dict):
        data = data.get("cuentas") or data.get("data") or []
    en_sql = {str(r[0]) for r in _filas("SELECT id_cuenta FROM clientes.cuentas")}
    activas = faltan = 0
    muestra: list[str] = []
    for c in data:
        if not isinstance(c, dict) or (c.get("estado") or "") != "Activa":
            continue
        if ((c.get("disposicionesGenerales") or {}).get("tipoCliente")) in _TIPOS_PH:
            continue
        activas += 1
        cid = c.get("id")
        if cid is not None and str(cid) not in en_sql:
            faltan += 1
            if len(muestra) < 10:
                muestra.append(f"{cid} · {str(c.get('denominacion') or '')[:50]}")
    print(f"  Aunesa: {activas} cuenta(s) Activas no persona física.")
    print(f"  De ésas, {faltan} NO están en `clientes.cuentas`.")
    for m in muestra:
        print(f"      · {m}")
    if faltan:
        print("\n  → hace falta una FOTO completa (patrón `foto_primary`/`foto_1816`):")
        print("     un job la baja, el detector lee la tabla y nunca pega la API.")
    else:
        print("\n  → SQL ya tiene todo: el detector puede leer sólo la base.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aunesa", action="store_true",
                    help="además, pega Aunesa UNA vez para ver qué falta en SQL")
    args = ap.parse_args()
    for paso in (_columnas, _padron, _vacios, _vocabulario, _senales, _pendientes):
        try:
            paso()
        except Exception as e:
            print(f"\n⚠ «{paso.__name__}» falló: {type(e).__name__}: {e}")
    if args.aunesa:
        try:
            _aunesa()
        except Exception as e:
            print(f"\n⚠ «_aunesa» falló: {type(e).__name__}: {e}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
