---
name: add-habilidad
description: Agregar una habilidad al AV AGENT (detector, con o sin arreglo). Cubre las SIETE piezas que hacen que quede conectada al modelo — la fila del catálogo con sus cinco declaraciones, el detector, la fuente, el test, el diario, la cita del diario en el código y el conteo del front — y cómo decidir qué es aviso y qué es trabajo. REGLA #10 del CLAUDE.md.
---

# Agregar una habilidad al AV AGENT

Se aplica cuando el usuario pide «que el agente mire X», «avisame cuando Y»,
«agregá un detector de Z» o cualquier cosa que el agente tenga que vigilar o
arreglar. Doc que manda: `docs/AGENT.md` parte A (§1 a §8). El diario (§0.x)
cuenta por qué cada regla quedó como quedó; se cita, no se copia.

> ⚠️ El 2026-09-02 se sumaron cuatro habilidades a mano y **dos veces faltó una
> pieza, y las dos veces lo dijo un test después de pushear a `main`**. Esta
> skill existe para que la lista no viva en la memoria de nadie. Y el hook
> `.claude/hooks/check_agente.py` corre los tests del agente antes de cada push
> que toque `agente/`: si falta una pieza, el push no sale.

## 0. Antes de escribir: tres preguntas que deciden la forma

1. **¿Qué mira, en castellano, en una frase?** Va en `que_mira`. Si no se puede
   decir en una frase, son dos habilidades.
2. **¿Tiene arreglo?** Un arreglo **ESCRIBE** (invariante #10: un botón que
   vuelve a mirar no es un arreglo). Si lo tiene → cada regla que emite se
   mapea a un id de `agente/arreglos.ARREGLOS` y el hallazgo entra a ENCONTRÓ.
   Si no lo tiene → es un **aviso**: vive en AHORA, y `arreglos={}` se deja
   vacío **a propósito y con un comentario que lo diga**.
3. **¿De dónde saca el dato?** Si ya lo lee otra habilidad, va por
   `agente/fuentes.py` (una pasada, una foto). Si no existe la lectura, se
   agrega ahí, nunca adentro del detector. **`None` = no pude leer**; vacío =
   no hay nada. No se confunden.

## 1. La fuente — `agente/fuentes.py` (solo si hace falta un dato nuevo)

```python
def mi_dato() -> dict | None:
    """Qué es, de qué tabla, y qué significa `None` (no pude leer)."""
    def _leer():
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT …")
            return {...}
    return _una_vez("mi_dato", _leer)
```

Si el dato lo escribe un **cron** y el resto del sistema lo lee como verdad,
es una FOTO: además del detector, vigilar que no quede vieja con una fila más
sobre `sistema._foto(...)` (mirar `foto_primary` / `foto_1816`, §0.cy y §0.df).

## 2. El detector — `agente/detectores/<dominio>.py`

`mercado.py` · `sistema.py` · `datos.py` · `catalogo.py`. Contrato (§2.4 y §3):

```python
# ═══ mi_habilidad ══════════════════════════════════════════════════════════
def mi_habilidad(u: dict) -> list[Hallazgo]:
    """Qué mira y por qué importa. Cita el diario: (§0.xx)."""
    from agente import fuentes
    dato = fuentes.mi_dato()
    if dato is None:
        raise SinDatos("no pude leer <tabla>: no sé si …")   # NUNCA devolver []
    out = []
    for sujeto, valor in dato.items():
        if <la regla>:
            out.append(Hallazgo(
                sujeto=sujeto, regla="mi_regla", severidad="alta|media|baja",
                problema=f"qué pasa, con el número y la hora · {reloj.hhmm()}",
                detalle="el dato crudo (el error tal cual, la lista, el pid)",
                que_hacer="qué hacer, distinto por regla — obligatorio",
                evidencia={"lo que usó el detector": ..., "fecha_de_la_foto": ...}))
    return out
```

Reglas fijas del contrato, todas congeladas por test:

- **Levanta `SinDatos`, no decide.** «¿Qué hago si no puedo mirar?» lo contesta
  el motor una vez (invariante #6). Devolver `[]` cuando no se pudo leer cierra
  por ausencia todo lo abierto: es la mentira que el invariante #1 prohíbe.
- **No escribe nada.** Solo `agente/registro.py` escribe hallazgos.
- **`que_hacer` es obligatorio** y distinto por regla; «revisar» no es un qué hacer.
- **Fecha y hora en todo lo que se muestra** (`reloj.hhmm()` en `problema`).
- **Umbrales por `u`** (`u.get("umbral", default)`), declarados en el catálogo,
  nunca constantes sueltas en el detector.
- **Sujeto = la identidad del problema.** Si es un grupo (un campo, un job)
  y no un ítem, poner la lista de ítems en `evidencia["items"]` para que la
  reincidencia se decida por ítem (§0.cz).
- **El horario sale del crontab**, evaluado por `salud.ultima_ejecucion_esperada`
  (§0.db). No se copia una hora en el detector.

## 3. La fila del catálogo — `agente/catalogo.py`

```python
    # Por qué existe, en dos líneas, y por qué tiene o no tiene arreglo. §0.xx
    Habilidad(
        nombre="mi_habilidad", tipo="detector", dominio="MERCADO|SISTEMA|DATOS|SEGURIDAD",
        que_mira="una frase en castellano",
        cada_segundos=N * _M, ventana="rueda|cierre|habil|siempre",
        correr=<modulo>.mi_habilidad,
        umbrales={"umbral": default},          # solo si el detector lee `u`
        arreglos={"mi_regla": "id_del_arreglo"}),   # o {} con el comentario
```

Las cinco declaraciones de la REGLA #10 están en esa fila: qué mira, cuándo,
qué arreglo, dónde escribe (por `registro`, siempre) y qué hacer (en cada
hallazgo). Sumar la fila **es** conectarla: no hay reloj, lista ni mapa aparte.

## 4. Si tiene arreglo — `agente/arreglos.py`

Una clase `Arreglo` con `id`, `titulo`, `donde` (tabla), `campo`, `preview`
(calcula, no muta, y devuelve `puede_aplicar`) y `aplicar` (escribe por la
puerta única del dominio y anota en el libro **una línea por cosa escrita**).
`inmediato=False` si el efecto lo confirma el detector después. Registrarla
en `ARREGLOS`. Si pide que una persona elija un valor: `pide_datos = True`.

## 5. El test — `tests/unit/test_agente.py`

Dos, al final del archivo, con el estilo del resto (el docstring cuenta el
caso real que motivó la regla):

```python
def test_mi_habilidad_<lo_que_congela>(monkeypatch):
    """Por qué existe, con el caso real y la fecha."""
    from agente import fuentes, reloj
    monkeypatch.setattr(fuentes, "mi_dato", lambda: {...})
    monkeypatch.setattr(reloj, "ahora_utc", lambda a=None: datetime(2026, 9, 2, 15, 0, tzinfo=UTC))
    por = {h.sujeto: h for h in <modulo>.mi_habilidad({"umbral": 1})}
    assert por["X"].regla == "mi_regla"
    monkeypatch.setattr(fuentes, "mi_dato", lambda: None)
    with pytest.raises(tipos.SinDatos):
        <modulo>.mi_habilidad({})
```

El segundo congela la estructura: que la habilidad esté en el catálogo, con
el dominio y los arreglos que corresponden, y que el detector no escriba.

## 6. El diario — `docs/AGENT.md`

Una entrada `### 0.<siguiente> TÍTULO (fecha)` al final: qué se vio, qué era,
qué queda. **Y una cita `§0.<letras>` en el código** (el detector o la fila del
catálogo): `tests/unit/test_doc_agente.py` exige que toda entrada esté citada
y que toda cita exista. Sin la cita, la entrada «sobra»; sin la entrada, la
cita «cuelga». Las dos fallan el test.

## 7. Lo que se toca fuera del agente, según el caso

- **Cron nuevo** → `deploy/crontab.txt` por `run_job.sh`, una `Pieza` en
  `api/services/diagnostico_registry.py` (`tests/test_diagnostico_registry.py`
  exige que todo cron esté en el árbol), y `python -m scripts.gen_sistema`.
- **Tabla nueva** → `sql/schema.sql` (idempotente) y, si la escribe un job,
  `jobs/CLAUDE.md` manda `JobRunLogger`.
- **Conteo del front** → `acaquant-frontend/CLAUDE.md` dice cuántas
  habilidades hay; el número manda desde el catálogo, pero el doc lo repite.
- **Endpoint nuevo** → `python -m scripts.gen_mapa_app` (CI lo exige).

## 8. Verificar antes de pushear

```bash
ruff check . && python -m pytest -q tests/unit/test_agente.py tests/unit/test_doc_agente.py tests/test_diagnostico_registry.py tests/unit/test_scripts.py
python -c "import api.main"
```

El hook `check_agente.py` corre esto solo en el `git push` si el diff toca
`agente/`, `docs/AGENT.md`, el registro de diagnóstico o el crontab.
