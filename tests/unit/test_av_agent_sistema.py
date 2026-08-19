"""EL AGENTE ENTIENDE LA BASE Y LA LATENCIA — `av_agent_db` + `av_agent_latencia`.

Lo que se congela son las decisiones de diseño que hacen que estos dos avisos se
puedan creer. El user fue explícito: *«tiene que ser algo fiable y verdadero, no
tirar por tirar»* — y de un detector de latencia que grita seguido no se
desconfía: se lo ignora, que es peor.
"""
from __future__ import annotations

from api.services import av_agent_db as db
from api.services import av_agent_latencia as lat

# ── LA BASE: la información es el DELTA, no el tamaño ──────────────────────

def _cmp(nuevas=(), crecieron=(), desaparecidas=(), total=10**10, corte=10**7):
    return {"ok": True, "primera": False, "fecha": "2026-08-19",
            "fecha_previa": "2026-08-18", "bytes_total": total,
            "delta_total": 0, "n_tablas": 200, "corte_bytes": corte,
            "nuevas": list(nuevas), "crecieron": list(crecieron),
            "desaparecidas": list(desaparecidas)}


def test_la_primera_foto_NO_inventa_200_tablas_nuevas(monkeypatch):
    """Sin foto previa, TODO parecería nuevo. Arrancar con 200 falsos positivos
    es la forma más rápida de que nadie vuelva a mirar esto."""
    monkeypatch.setattr(db, "comparar", lambda: {"ok": True, "primera": True})
    assert db.detectar_db() == []


def test_sin_ninguna_foto_no_dice_nada(monkeypatch):
    monkeypatch.setattr(db, "comparar", lambda: {"ok": False, "motivo": "x"})
    assert db.detectar_db() == []


def test_no_reporta_TAMAÑOS_sino_CAMBIOS(monkeypatch):
    """Que una tabla pese 4 GB no es un hallazgo: puede ser exactamente lo que
    tiene que pesar. Llenar la pantalla con los 200 tamaños del día es el ruido
    que hace que nadie mire."""
    monkeypatch.setattr(db, "comparar", lambda: _cmp())
    assert db.detectar_db() == []


def test_una_tabla_nueva_siempre_se_canta(monkeypatch):
    """No lleva umbral a propósito: que aparezca una tabla es un HECHO, no una
    magnitud. Y es lo que uno se entera último."""
    monkeypatch.setattr(db, "comparar", lambda: _cmp(
        nuevas=[{"tabla": "mercado.rara", "bytes": 4096, "filas": 3}]))
    h = db.detectar_db()
    assert len(h) == 1 and h[0]["regla"] == "tabla_nueva"


def test_una_tabla_que_desaparecio_es_ALTA(monkeypatch):
    """Alguien dropeó algo. Es lo más grave de los tres."""
    monkeypatch.setattr(db, "comparar", lambda: _cmp(
        desaparecidas=[{"tabla": "mercado.x", "bytes": 10**9}]))
    assert db.detectar_db()[0]["severidad"] == "alta"


def test_el_hallazgo_lleva_la_evidencia_como_DICT(monkeypatch):
    """La columna es `jsonb` y el resto del agente la lee como objeto. Un string
    ahí no explota —`json.dumps("x")` es JSON válido— pero la pantalla no
    encuentra ninguna clave y el hallazgo queda mudo."""
    monkeypatch.setattr(db, "comparar", lambda: _cmp(
        crecieron=[{"tabla": "t", "bytes": 10**9, "delta": 10**8, "pct": 40.0,
                    "filas": 10, "filas_delta": 5}]))
    ev = db.detectar_db()[0]["evidencia"]
    assert isinstance(ev, dict) and ev["texto"] and ev["delta"] == 10**8


def test_el_hallazgo_tiene_LA_MISMA_forma_que_los_del_mercado(monkeypatch):
    monkeypatch.setattr(db, "comparar", lambda: _cmp(
        nuevas=[{"tabla": "t", "bytes": 1, "filas": 0}]))
    h = db.detectar_db()[0]
    assert set(h) == {"tipo", "ticker", "regla", "severidad", "motivo", "evidencia"}


def test_el_corte_se_AUTO_CALIBRA_sobre_el_tamano_de_la_base():
    """No tengo acceso a prod, así que cualquier umbral en MB que ponga a mano es
    una adivinanza (REGLA #2). Al calcularlo como fracción de la base, el aviso
    significa lo mismo con 500 MB que con 50 GB y nadie recalibra nada."""
    assert 0 < db.CRECIO_FRACCION_BASE < 0.05
    assert db.CRECIO_PISO_BYTES > 0
    chica = max(db.CRECIO_PISO_BYTES, int(10**8 * db.CRECIO_FRACCION_BASE))
    grande = max(db.CRECIO_PISO_BYTES, int(10**12 * db.CRECIO_FRACCION_BASE))
    assert grande > chica, "el corte tiene que crecer con la base"


def test_solo_se_guardan_DOS_fechas():
    """*«Que persista para tener contexto, pero a su vez no crecer todo el
    tiempo: con que tenga registro de hoy y ayer alcanza»* (user)."""
    assert db.FECHAS_QUE_SE_GUARDAN == 2


def test_la_purga_va_en_la_MISMA_transaccion_que_la_escritura():
    """Depender de que alguien se acuerde de correr una limpieza es cómo una
    tabla que vigila el tamaño de la base termina siendo el problema."""
    import inspect
    src = inspect.getsource(db.sacar_foto)
    assert "DELETE FROM manager.db_tamano" in src
    assert src.index("INSERT INTO manager.db_tamano") < src.index("DELETE FROM")


def test_los_bytes_se_leen(): 
    assert db.mb(0) == "0,0 B"
    assert db.mb(52_000_000).endswith("MB")
    assert db.mb(None) == "—"


# ── LA LATENCIA: lo ANORMAL, no lo lento ───────────────────────────────────

def _serie(endpoint, horas_previas, avg_previo, horas_rec, avg_rec, n=50, err=0):
    """Serie sintética: N horas previas a `avg_previo` ms y las recientes a
    `avg_rec`."""
    from datetime import UTC, datetime, timedelta
    ahora = datetime.now(UTC)
    filas = []
    for i in range(horas_previas):
        filas.append({"endpoint": endpoint,
                      "hora": ahora - timedelta(hours=lat.VENTANA_H + 1 + i),
                      "n": n, "total_ms": n * avg_previo, "max_ms": avg_previo,
                      "lentas": 0, "errores": 0})
    for i in range(horas_rec):
        filas.append({"endpoint": endpoint, "hora": ahora - timedelta(minutes=10 * i),
                      "n": n, "total_ms": n * avg_rec, "max_ms": avg_rec,
                      "lentas": 0, "errores": err})
    return {endpoint: filas}


def test_lo_LENTO_no_es_un_hallazgo(monkeypatch):
    """**El error de diseño que este módulo corrige.** `/salud/diagnostico` a
    11.724 ms encabeza el ranking y está BIEN: es una llamada al LLM. Un
    endpoint constantemente lento no se reporta nunca."""
    monkeypatch.setattr(lat, "_series", lambda: _serie("/lento", 20, 11_000, 2, 11_000))
    assert lat.detectar_latencia() == []


def test_lo_ANORMAL_si(monkeypatch):
    monkeypatch.setattr(lat, "_series", lambda: _serie("/x", 20, 200, 2, 2_000))
    h = lat.detectar_latencia()
    assert len(h) == 1 and h[0]["regla"] == "mas_lento"
    assert h[0]["evidencia"]["base_ms"] == 200


def test_guarda_1_poco_volumen_no_es_una_medicion(monkeypatch):
    """La media de 3 requests es ruido."""
    monkeypatch.setattr(lat, "_series",
                        lambda: _serie("/x", 20, 200, 1, 5_000, n=2))
    assert lat.detectar_latencia() == []


def test_guarda_2_sin_historia_no_hay_normal(monkeypatch):
    """Comparar contra dos puntos es adivinar."""
    monkeypatch.setattr(lat, "_series", lambda: _serie("/x", 2, 200, 2, 5_000))
    assert lat.detectar_latencia() == []


def test_guarda_3_triplicarse_de_10ms_a_30ms_no_le_importa_a_nadie(monkeypatch):
    """Pasa el filtro RELATIVO y no el absoluto. Tienen que pasar los dos."""
    monkeypatch.setattr(lat, "_series", lambda: _serie("/x", 20, 10, 2, 40))
    assert lat.detectar_latencia() == []


def test_guarda_3_empeorar_mucho_en_ms_pero_poco_en_proporcion_tampoco(monkeypatch):
    """De 5.000 a 5.400 ms son 400 ms más, pero es su normalidad."""
    monkeypatch.setattr(lat, "_series", lambda: _serie("/x", 20, 5_000, 2, 5_400))
    assert lat.detectar_latencia() == []


def test_guarda_4_un_pico_previo_no_puede_TAPAR_el_problema(monkeypatch):
    """Con PROMEDIO, un pico de ayer sube la vara y esconde justo lo que se
    repite hoy. Por eso la línea base es la MEDIANA."""
    from datetime import UTC, datetime, timedelta
    ahora = datetime.now(UTC)
    filas = [{"endpoint": "/x", "hora": ahora - timedelta(hours=lat.VENTANA_H + 1 + i),
              "n": 50, "total_ms": 50 * (60_000 if i == 0 else 200),
              "max_ms": 0, "lentas": 0, "errores": 0} for i in range(20)]
    filas += [{"endpoint": "/x", "hora": ahora, "n": 50, "total_ms": 50 * 2_000,
               "max_ms": 2_000, "lentas": 0, "errores": 0}]
    monkeypatch.setattr(lat, "_series", lambda: {"/x": filas})
    h = lat.detectar_latencia()
    assert h, "con mediana el pico previo no debería tapar la degradación"
    assert h[0]["evidencia"]["base_ms"] == 200


def test_los_5xx_NO_pasan_por_la_linea_base(monkeypatch):
    """Un endpoint que rompe está roto tarde lo que tarde."""
    monkeypatch.setattr(lat, "_series",
                        lambda: _serie("/x", 20, 200, 2, 200, n=50, err=25))
    h = lat.detectar_latencia()
    assert [x["regla"] for x in h] == ["errores"]


def test_cada_endpoint_se_compara_contra_SI_MISMO(monkeypatch):
    """Que `/tesoreria/dia` sea más lento que `/me` no es noticia."""
    s = {**_serie("/rapido", 20, 50, 2, 50), **_serie("/lento", 20, 5_000, 2, 5_000)}
    monkeypatch.setattr(lat, "_series", lambda: s)
    assert lat.detectar_latencia() == []


def test_la_evidencia_de_latencia_tambien_es_un_dict(monkeypatch):
    monkeypatch.setattr(lat, "_series", lambda: _serie("/x", 20, 200, 2, 2_000))
    ev = lat.detectar_latencia()[0]["evidencia"]
    assert isinstance(ev, dict) and ev["texto"] and ev["veces"]


def test_una_query_sola(monkeypatch):
    """Corre cada pocos minutos con los demás detectores live: el peaje a
    Supabase es fijo por VIAJE (~8,5 ms de distancia), así que lo que importa es
    la cantidad de queries, no su plan."""
    import inspect
    assert inspect.getsource(lat._series).count("_q(") == 1


# ── Las dos entran al registro de SKILLS por la LEY ────────────────────────

def test_las_dos_capacidades_nuevas_quedaron_mapeadas():
    from api.services.av_agent_skills import catalogo
    ids = {s.id for s in catalogo()}
    assert "detectar.db_cambio" in ids and "detectar.latencia" in ids


def test_y_ninguna_de_las_dos_usa_el_modelo():
    from api.services.av_agent_skills import SIN_IA, catalogo
    for s in catalogo():
        if s.id in ("detectar.db_cambio", "detectar.latencia"):
            assert s.usa_ia == SIN_IA


# ── Un hallazgo de rueda VENCE (2026-08-19) ────────────────────────────────

def test_una_OBSERVACION_DE_MERCADO_vence():
    """*«En AVISOS tenía un montón de avisos de precios sin precio, pero eso era
    porque el MERCADO ESTABA CERRADO»* (user).

    El monitor corre 10:30-17 ART y reemplaza lo suyo en cada pasada — pero al
    cerrar deja de correr, y su última foto se quedaba en la pantalla toda la
    noche. El detector estaba bien; la foto estaba vieja, que para el que mira es
    lo mismo."""
    from api.services import av_agent
    assert av_agent.vence("sin_punta")
    assert av_agent.vence("precio_viejo")
    # Más que el intervalo del monitor (5') para no parpadear entre pasadas.
    assert av_agent.VENCE_OBSERVACION_S >= 10 * 60


def test_un_PROBLEMA_DE_CONFIGURACION_no_vence():
    """*«Eso tiene que ser independiente del mercado. Si ya detectó que cotiza la
    pata en pesos es lo mismo que el mercado esté abierto o no: mañana va a
    volver a abrir y va a pasar lo mismo… si ya detectó el error tiene que saber
    que va a volver a pasar si no se hizo nada.»* (user, 2026-08-19)

    El vencimiento era por ALCANCE, así que se llevaba puesto TODO el monitor —
    incluidos los hechos sobre nuestros propios datos. El costo no se veía: el
    problema desaparecía a la noche y volvía a la mañana como nuevo, así que
    **nunca acumulaba antigüedad**, y uno de hace tres semanas se veía igual que
    uno de recién."""
    from api.services import av_agent
    for r in ("no_suscripto", "sin_simbolo", "pata_equivocada",
              "cotiza_en_pesos", "precio_fuera_de_escala"):
        assert not av_agent.vence(r), f"{r} es config nuestra: no puede vencer"


def test_una_regla_NUEVA_no_vence_por_default():
    """Igual que `DE_QUIEN`: el default es el lado que NO esconde. Una regla que
    nadie clasificó desapareciendo sola de la pantalla es el peor modo de falla —
    no da ningún error."""
    from api.services import av_agent
    assert not av_agent.vence("una_regla_que_todavia_no_existe")


def test_el_alcance_SISTEMA_no_vence():
    """Corre una vez por noche: hacerlo vencer como el monitor de rueda dejaría
    la pantalla vacía el 99% del día."""
    from api.services import av_agent
    assert "sistema" in av_agent.ALCANCES_VIVOS
    # Sus reglas no son observaciones de mercado, así que ninguna vence.
    for r in ("tabla_quieta", "db_cambio", "permiso_flojo"):
        assert not av_agent.vence(r)


def test_el_vencimiento_se_aplica_en_SQL_no_en_el_front():
    """Si el filtro viviera en la pantalla, cualquier otra vía de consulta
    seguiría leyendo la foto vencida."""
    import inspect

    from api.services import av_agent_vista
    src = inspect.getsource(av_agent_vista._hallazgos_ultima_corrida)
    assert "OBSERVACIONES_DE_MERCADO" in src and "make_interval" in src
    # Y el corte por REGLA tiene que estar en el WHERE, no filtrado después.
    assert "regla = ANY" in src and "regla <> ALL" in src


# ── (2026-08-19) EL DOMINIO DEL VOTO ────────────────────────────────────────

def test_todo_tipo_que_emite_un_detector_tiene_DOMINIO_de_voto():
    """El eval set nació con dos dominios (`bono` | `salud`) porque eran los dos
    detectores que había. Desde entonces el agente aprendió a mirar el SISTEMA, y
    esos hallazgos **no tenían dónde votarse**: el payload los rechazaba por el
    patrón y nadie se enteraba, porque el botón todavía no existía.

    Si mañana entra un tipo nuevo sin dominio, cae en `bono` y su precisión se
    mezcla con la de los bonos — un error que no da ningún síntoma."""
    from api.services import av_agent
    tipos = set(av_agent.ACCION_POR_TIPO)
    faltan = tipos - set(av_agent.DOMINIO_EVAL)
    assert not faltan, f"tipos sin dominio de voto declarado: {sorted(faltan)}"


def test_los_dominios_declarados_son_los_que_el_endpoint_ACEPTA():
    """Si `DOMINIO_EVAL` dijera un dominio que el payload rechaza, el voto se
    perdería con un 422 y el botón parecería roto sin motivo."""
    from api.services import av_agent
    assert set(av_agent.DOMINIO_EVAL.values()) <= set(av_agent.DOMINIOS_EVAL)
    import inspect

    from api.routers import ia
    src = inspect.getsource(ia.VotoEval)
    for d in av_agent.DOMINIOS_EVAL:
        assert d in src, f"el payload no acepta el dominio «{d}»"


def test_un_tipo_desconocido_cae_en_bono_y_no_levanta():
    from api.services import av_agent
    assert av_agent.dominio_eval("no_existe") == "bono"
    assert av_agent.dominio_eval("") == "bono"
    assert av_agent.dominio_eval("dato_partido") == "sistema"
