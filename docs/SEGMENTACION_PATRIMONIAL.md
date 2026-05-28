# Segmentación Patrimonial de Clientes

> Documento vivo del feature. El **LOG DE AVANCES** (al final) es append-only
> con fecha. Cada vez que toquemos algo de esta feature, agregar una entrada.
>
> Sub-feature de **[TABLERO_COMERCIAL.md](TABLERO_COMERCIAL.md)** — esto es
> "Segmentación de clientes" (capa [3] en la cadena de dependencias del tablero),
> pero con un criterio **patrimonial objetivo** (límite de fondeo) en vez de
> categórico-manual.

## Objetivo

Clasificar cada cuenta comitente en uno de **6 segmentos patrimoniales** (3 para
Personas Humanas, 3 para Personas Jurídicas) a partir del **límite de fondeo
disponible** que reporta el custodio. El segmento es el insumo central del CRM
para métricas comerciales, campañas y priorización de la mesa.

**Por qué este criterio y no el `nivel_3` manual de hoy**: `nivel_3` se carga a
mano por la mesa y se revirtió la derivación automática previa
(commit `7d71bc4`, derivaba de `tipo_cliente` y daba mal). El límite de fondeo
es un dato duro, comparable entre cuentas, y refleja el patrimonio real que el
custodio le reconoce al cliente — es el proxy correcto para "cuán grande" es
un cliente desde lo comercial.

**Decisión clave**: `segmento_patrimonial` (derivado, automático) **convive**
con `nivel_3` (manual, lo que ya hay) — son **dos campos separados**. No se
pisa el manual. Esto evita repetir el bug del revert y deja a la mesa la
opción de overrides puntuales si el algoritmo no les cierra para una cuenta.

## Reglas de clasificación (6 segmentos)

El criterio depende de si la cuenta es **Persona Humana (PH)** o **Persona
Jurídica (PJ)** y del **límite disponible para fondear** convertido a la
moneda/unidad correspondiente.

### Personas Humanas — umbral en **USD**

| Segmento              | Límite disponible (USD) |
|-----------------------|-------------------------|
| `PH_RETAIL`           | < 50.000                |
| `PH_MEDIO_RETAIL`     | ≥ 50.000 y ≤ 100.000    |
| `PH_ALTO_PATRIMONIO`  | > 100.000               |

### Personas Jurídicas — umbral en **UVAs**

| Segmento       | Límite disponible (UVAs) |
|----------------|--------------------------|
| `PJ_PEQUENA`   | ≤ 350.000                |
| `PJ_MEDIANA`   | > 350.000 y ≤ 700.000    |
| `PJ_GRANDE`    | > 700.000                |

> **Convención de bordes** (para que no haya ambigüedad en el código):
> los valores exactos del límite superior caen en el segmento inferior
> (intervalos `(−∞, A]`, `(A, B]`, `(B, +∞)` para PJ; `[0, 50k)`, `[50k, 100k]`,
> `(100k, +∞)` para PH — leído tal cual del texto del usuario).

### Distinguir PH vs PJ

Orden de precedencia:
1. Campo `tipo_cliente` de `Comitentes` si está poblado y es claro
   ("Persona Humana" / "Persona Jurídica" o equivalente).
2. Fallback: derivar del prefijo del CUIT — `20/23/24/27` ⇒ PH,
   `30/33/34` ⇒ PJ.

## Conversiones de moneda/unidad

El **input siempre viene en ARS** (es lo que reporta el custodio en el Excel).
Para clasificar PH se convierte a USD; para clasificar PJ se convierte a UVAs.

### ARS → USD (para PH)

**Decisión recomendada (a confirmar)**: usar **MEP** del momento del cálculo
(no del momento de la carga). Justificación: el "patrimonio del cliente" en
términos relevantes para la mesa es lo que puede dolarizarse en el mercado,
no el oficial. Fuente live: `get_ultimo_mep` (TTL 5s), igual que el resto de
la API.

Alternativas si se descarta MEP: dólar oficial mayorista (BCRA A3500) desde
`Trading.DOLAR` (fixing diario) o desde `Valuaciones.DolarOficialLive` (feed
MAE intradiario).

### ARS → UVAs (para PJ)

**Pendiente de implementar**: en el repo no hay serie UVA todavía. Hay que
sumarla a `jobs/bcra.py` (es una serie estadística pública igual que CER /
Riesgo País). Decisión abierta sobre dónde persistirla — probablemente nueva
colección `Trading.UVA` (timeseries diaria, mismo shape que `Trading.DOLAR`).

Una vez ingestada: el motor toma el último valor UVA publicado al momento
del cálculo y hace `limite_uvas = limite_ars / uva`.

## Modelo de datos

Todo vive en **`Clientes.Comitentes`** (la colección ya existente) como
**subdocumentos atómicos** — no se crea colección nueva en esta fase.

```jsonc
{
  // ... campos existentes (id_cuenta, denominacion, operador, nivel_1..5, etc.) ...

  // ── Input de la carga masiva (Excel) ────────────────────────────────
  "limite_fondeo": {
    "disponible_ars": 12345678.90,
    "utilizado_ars":   2345678.90,
    "utilizacion_pct": 19.01,             // derivado, persistido para indexar
    "cargado_en":  "2026-05-28T13:00:00Z",
    "fuente":      "excel:carga_2026-05"  // nombre del archivo o etiqueta
  },

  // ── Output del motor de segmentación ────────────────────────────────
  "segmento_patrimonial": "PH_RETAIL",    // uno de los 6, o null si falta input
  "segmento_patrimonial_calc": {           // auditabilidad
    "tipo": "PH",                          // "PH" | "PJ"
    "limite_convertido": 38420.15,         // USD para PH, UVAs para PJ
    "unidad": "USD",                       // "USD" | "UVA"
    "tc": {                                // sólo PH
      "valor": 1320.50,
      "fuente": "MEP",                     // o "oficial_a3500", etc.
      "fecha":  "2026-05-28"
    },
    "uva": {                               // sólo PJ
      "valor": 1450.32,
      "fecha": "2026-05-28"
    },
    "calculado_en": "2026-05-28T13:05:00Z"
  }
}
```

**Contrato con `sync_comitentes`**: estos campos NO los maneja Aunesa, los
escribe nuestra carga masiva / nuestro motor. Por lo tanto van a `MANUAL_FIELDS`
(o equivalente) en `jobs/sync_comitentes.py` para que el sync diario NO los
pise. Mismo patrón que `nivel_1..5` y `operador_email/operador_nombre` hoy.

**Histórico (FASE 2, fuera de scope inicial)**: una colección
`Clientes.LimitesFondeoHistorico` append-only (un doc por carga) habilita
métricas temporales (evolución del segmento del cliente mes a mes) sin migrar
el modelo actual. No se construye ahora; se deja la puerta abierta.

## Componentes a construir

### 1. Carga masiva — `scripts/cargar_limites_fondeo.py`

- Lee el Excel/CSV (formato a confirmar; ver "Decisiones abiertas").
- Por cada fila: upsert del subdoc `limite_fondeo` en `Comitentes` matcheando
  por `id_cuenta`.
- **No clasifica** acá — sólo persiste el input. El motor corre después.
- Resumen al final: cuántas cuentas matchearon, cuáles del Excel no existen
  en `Comitentes`, cuántos límites cambiaron vs. la carga anterior, dry-run
  por default.
- Encadena al final una llamada al motor (`python -m jobs.segmentar_patrimonial`).
- Per [REGLA #0](../CLAUDE.md): el script vive en el repo, el usuario lo
  corre en el Droplet con `python -m scripts.cargar_limites_fondeo`.

### 2. Motor de segmentación — `jobs/segmentar_patrimonial.py`

- Toma todas las `Comitentes` con `limite_fondeo.disponible_ars` poblado.
- Para cada cuenta: detecta PH/PJ, obtiene TC (MEP) o UVA según corresponda,
  convierte el límite, aplica las reglas → escribe `segmento_patrimonial` +
  `segmento_patrimonial_calc`.
- Idempotente. Corre standalone o encadenado.
- Cron: a definir (probablemente mensual, post-carga). Si UVA o MEP cambian
  mucho podría correr semanalmente — TBD.

### 3. Vista en `/comercial`

- Servicio: `api/services/comercial.py` → agregaciones por `segmento_patrimonial`
  (counts + AuM agregado por banda + % utilización promedio), filtrable por
  operador.
- Endpoint: `api/routers/manager/comercial.py` (manager-only, sigue el patrón
  existente — ver TABLERO_COMERCIAL).
- Frontend: card/tab nueva dentro del tablero comercial en `acaquant-web/`
  (cross-repo, ver feedback `acaquant_web_companion`).

### 4. Diagnóstico — `scripts/diag_segmentacion_patrimonial.py`

Read-only. Imprime la distribución actual por segmento, qué cuentas tienen
`limite_fondeo` pero no `segmento_patrimonial` (debería estar vacío post-motor),
qué cuentas con AuM > 0 no tienen límite cargado (gap del Excel), etc.

## Plan de implementación (orden sugerido)

1. **Fase 1 — Ingesta UVA al repo** (bloqueante para PJ).
   - Sumar serie UVA a `jobs/bcra.py` o crear `jobs/uva.py`.
   - Persistir en `Trading.UVA` (TBD shape exacto).
2. **Fase 2 — Carga masiva**.
   - `scripts/cargar_limites_fondeo.py` + tests unit del parser y el upsert.
   - Agregar `limite_fondeo` y `segmento_patrimonial*` a `MANUAL_FIELDS` en
     `sync_comitentes.py`.
3. **Fase 3 — Motor**.
   - `jobs/segmentar_patrimonial.py`.
   - Cron en `deploy/crontab.txt` + regenerar `deploy/SISTEMA.md`
     (`python -m scripts.gen_sistema`).
4. **Fase 4 — API + vista**.
   - Service + endpoint manager-only.
   - Frontend en `acaquant-web/`.
5. **Fase 5 (opcional)** — Histórico (`LimitesFondeoHistorico`) si la mesa
   quiere ver evolución mes a mes.

## Decisiones tomadas

- **Subdoc en `Clientes.Comitentes`** (no colección nueva en esta fase).
- **`segmento_patrimonial` convive con `nivel_3` manual** (no lo pisa).
- **`PH_RETAIL` / `PH_MEDIO_RETAIL` / `PH_ALTO_PATRIMONIO` / `PJ_PEQUENA` / `PJ_MEDIANA` / `PJ_GRANDE`** como enum de string (snake_case mayúscula, sin acentos para evitar bugs en queries).
- **Inputs en ARS, segmentación en USD (PH) o UVAs (PJ)**.
- **Carga masiva por script, no por endpoint de upload** — patrón TradingAV
  (REGLA #0). Endpoint de upload puede venir después si la frecuencia justifica.
- **Sin histórico en el MVP**; modelo deja la puerta abierta para fase 2.

## Decisiones abiertas

- [ ] **Formato exacto del Excel** de carga masiva. Headers, encoding, si
      viene como `.xlsx`, `.csv`, o ambos. (Bloquea Fase 2.)
- [ ] **TC a usar para PH**: MEP (recomendado) vs oficial A3500 vs mayorista
      MAE. (Bloquea Fase 3.)
- [ ] **Dónde persistir UVA**: nueva `Trading.UVA` (recomendado) vs sumarla
      a otra colección existente. Cron de ingesta (probablemente 12 UTC L-V,
      como `argentina_datos`). (Bloquea Fase 3 para PJ.)
- [ ] **Periodicidad del motor**: post-carga + cron semanal vs mensual.
- [ ] **`tipo_cliente` en `Comitentes`** — confirmar nombre exacto del campo
      y valores que toma para poder detectar PH/PJ con confiabilidad antes
      del fallback a CUIT.
- [ ] **Estado inicial post-carga**: ¿qué hacemos con cuentas Activas que
      el Excel del custodio NO incluye (cliente sin límite cargado)? Opciones:
      `segmento_patrimonial = null`, o `"SIN_DATOS"` explícito.

## Estado / TODO

- [x] Diseño documentado.
- [ ] Confirmar decisiones abiertas con la mesa.
- [ ] Fase 1 — Ingesta UVA.
- [ ] Fase 2 — Carga masiva.
- [ ] Fase 3 — Motor de segmentación.
- [ ] Fase 4 — API + vista.
- [ ] Fase 5 — Histórico (opcional).

---

## LOG DE AVANCES

- **2026-05-28** — **Doc inicial.** Se definió el modelo (subdoc en
  `Clientes.Comitentes`, separado de `nivel_3` manual), los 6 segmentos con
  umbrales (3 PH en USD: 50k/100k; 3 PJ en UVAs: 350k/700k), las conversiones
  (input ARS → USD/UVAs), y el plan en 5 fases. Bloqueantes para arrancar:
  formato del Excel de carga, TC a usar para PH, y agregar serie UVA al repo
  (no está). Ver "Decisiones abiertas".
