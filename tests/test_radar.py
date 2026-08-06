"""Offline test suite.

Everything here runs without touching the network, using synthetic records
shaped like SECOP rows. The accent cases are the ones that matter most: they
encode the exact failure mode this project exists to defeat.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import config  # noqa: E402
from radar.fields import get, resolve_schema  # noqa: E402
from radar.normalize import contains_phrase, flatten_record, normalize, strip_accents  # noqa: E402
from radar.report import build_dashboard  # noqa: E402
from radar.scoring import filter_and_score, matches_geography, score_record  # noqa: E402
from radar.store import Store  # noqa: E402

CRITERIA = {
    "keywords": {
        "criticas": ["capacitacion", "formacion", "competencias laborales"],
        "deseables": ["ingles", "virtual", "certificacion"],
        "excluyentes": ["obra civil", "pavimentacion"],
    },
    "unspsc": {"familias": ["86"]},
    "valor": {"minimo": 10_000_000, "maximo": None},
    "umbral_score": 35,
    "umbral_alerta": 60,
}

SAMPLE = {
    "id_del_proceso": "VA-2026-001",
    "entidad": "ALCALDIA DE SANTIAGO DE CALI",
    "descripci_n_del_procedimiento": "Prestación de servicios de capacitación en competencias laborales",
    "departamento_entidad": "Valle del Cauca",
    "precio_base": "250000000",
    "modalidad_de_contratacion": "Concurso de méritos",
    "estado_del_procedimiento": "Publicado",
    "fecha_de_recepcion_de": "2030-01-01T00:00:00.000",
    "codigo_principal_de_categoria": "V1.86101700",
    "urlproceso": {"url": "https://community.secop.gov.co/x"},
}


def check(label, condition):
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    return bool(condition)


results = []

# --------------------------------------------------------------------------
print("\n[1] Normalización de texto — el problema de las tildes")

results.append(check("strip_accents('formación') == 'formacion'",
                     strip_accents("formación") == "formacion"))
results.append(check("'CAPACITACIÓN' y 'capacitacion' colapsan al mismo token",
                     normalize("CAPACITACIÓN") == normalize("capacitacion")))
results.append(check("'ñ' se pliega a 'n' ('enseñanza' -> 'ensenanza')",
                     normalize("enseñanza") == "ensenanza"))
results.append(check("puntuación y espacios múltiples se colapsan",
                     normalize("  Educación,   NO   formal! ") == "educacion no formal"))
results.append(check("None no revienta",
                     normalize(None) == ""))
results.append(check("frase acentuada encontrada en texto sin acentos",
                     contains_phrase("servicios de CAPACITACION laboral", "capacitación")))
results.append(check("'ingles' NO matchea dentro de 'inglesa'",
                     not contains_phrase("bandera inglesa", "ingles")))
results.append(check("frase multi-palabra matchea",
                     contains_phrase("curso de COMPETENCIAS  LABORALES", "competencias laborales")))

# --------------------------------------------------------------------------
print("\n[2] Resolución de esquema")

schema = resolve_schema(SAMPLE)
results.append(check("resuelve 'objeto'", schema.get("objeto") == "descripci_n_del_procedimiento"))
results.append(check("resuelve 'entidad'", schema.get("entidad") == "entidad"))
results.append(check("resuelve 'valor'", schema.get("valor") == "precio_base"))
results.append(check("extrae url desde objeto anidado",
                     get(SAMPLE, schema, "url") == "https://community.secop.gov.co/x"))
results.append(check("campo ausente devuelve el default",
                     get(SAMPLE, {}, "inexistente", "def") == "def"))

# --------------------------------------------------------------------------
print("\n[3] Scoring")

match = score_record(SAMPLE, schema, CRITERIA)
results.append(check(f"proceso relevante puntúa alto (score={match.score})", match.score >= 60))
results.append(check("la alerta explica sus razones", len(match.reasons) >= 2))

irrelevant = dict(SAMPLE, descripci_n_del_procedimiento="Suministro de tóner para impresoras",
                  codigo_principal_de_categoria="V1.44103100")
results.append(check("proceso irrelevante puntúa 0",
                     score_record(irrelevant, schema, CRITERIA).score == 0))

excluded = dict(SAMPLE,
                descripci_n_del_procedimiento="Capacitación al personal de obra civil y pavimentacion")
excluded_match = score_record(excluded, schema, CRITERIA)
results.append(check("palabra excluyente descarta pese a tener keyword crítica",
                     not excluded_match.is_match and excluded_match.excluded_by is not None))

accented = dict(SAMPLE,
                id_del_proceso="VA-2026-002",
                descripci_n_del_procedimiento="FORMACIÓN Y CAPACITACIÓN EN COMPETENCIAS LABORALES",
                codigo_principal_de_categoria="")
results.append(check(f"texto con tildes y mayúsculas matchea igual (score={score_record(accented, schema, CRITERIA).score})",
                     score_record(accented, schema, CRITERIA).score >= 35))

no_keyword_but_unspsc = dict(SAMPLE,
                             descripci_n_del_procedimiento="Servicios profesionales varios",
                             codigo_principal_de_categoria="V1.86111600")
results.append(check("matchea sólo por código UNSPSC, sin palabra clave",
                     score_record(no_keyword_but_unspsc, schema, CRITERIA).score > 0))

urgent = dict(SAMPLE, fecha_de_recepcion_de="2020-01-01T00:00:00.000")
results.append(check("proceso ya cerrado se marca como tal",
                     any("cerrado" in r.lower() for r in score_record(urgent, schema, CRITERIA).reasons)))

batch = filter_and_score([SAMPLE, irrelevant, excluded, accented], schema, CRITERIA)
results.append(check(f"filter_and_score devuelve sólo los relevantes ({len(batch)})", len(batch) == 2))
results.append(check("ordenado por score descendente",
                     all(batch[i].score >= batch[i + 1].score for i in range(len(batch) - 1))))

# --------------------------------------------------------------------------
print("\n[4] Archivo y detección de desapariciones")

with tempfile.TemporaryDirectory() as tmp:
    store = Store(str(Path(tmp) / "test.db"))

    fresh = store.upsert_matches(batch, schema, "procesos", "Educación")
    results.append(check(f"primer barrido: {len(fresh)} coincidencias nuevas", len(fresh) == 2))

    again = store.upsert_matches(batch, schema, "procesos", "Educación")
    results.append(check("segundo barrido no duplica", len(again) == 0))

    row = store.conn.execute(
        "SELECT sweeps_seen, first_seen, last_seen FROM procesos WHERE id = ? AND perfil = ?",
        ("VA-2026-001", "Educación")).fetchone()
    results.append(check("sweeps_seen se incrementa", row["sweeps_seen"] == 2))
    results.append(check("first_seen se preserva entre barridos",
                         row["first_seen"] <= row["last_seen"]))

    # El proceso desaparece del portal: barrido sin él.
    vanished = store.mark_disappeared(set(), "procesos", "Educación")
    results.append(check(f"detecta {len(vanished)} proceso(s) retirado(s)", len(vanished) == 2))
    results.append(check("el retirado conserva su ventana real de publicación",
                         all(v["first_seen"] and v["last_seen"] for v in vanished)))

    stats = store.stats("Educación")
    results.append(check("stats cuenta los desaparecidos", stats["desaparecidos"] == 2))
    results.append(check("el registro sobrevive al retiro del portal", stats["total"] == 2))

    # Reaparece: se limpia la marca.
    store.upsert_matches(batch, schema, "procesos", "Educación")
    results.append(check("reaparecer limpia la marca de desaparición",
                         store.stats("Educación")["desaparecidos"] == 0))

    store.mark_alerted(["VA-2026-001"], "Educación")
    alerted = store.conn.execute(
        "SELECT alerted_at FROM procesos WHERE id = ? AND perfil = ?",
        ("VA-2026-001", "Educación")).fetchone()
    results.append(check("mark_alerted deja sello temporal", alerted["alerted_at"] is not None))

    # --- El mismo proceso, seguido por un segundo perfil ------------------
    otro = store.upsert_matches(batch, schema, "procesos", "Marketing")
    results.append(check("el mismo proceso entra de nuevo bajo otro perfil", len(otro) == 2))
    results.append(check("los perfiles se cuentan por separado",
                         store.stats("Marketing")["total"] == 2 and
                         store.stats()["total"] == 4))

    # Desaparece para Marketing pero sigue activo para Educación.
    solo_marketing = store.mark_disappeared(set(), "procesos", "Marketing")
    results.append(check("el retiro de un perfil no afecta al otro",
                         len(solo_marketing) == 2 and
                         store.stats("Educación")["desaparecidos"] == 0))

    por_perfil = store.stats_por_perfil()
    results.append(check("stats_por_perfil separa los dos perfiles",
                         set(por_perfil) == {"Educación", "Marketing"}))
    results.append(check("filtrar top_matches por perfil",
                         len(store.top_matches(50, "Educación")) == 2 and
                         len(store.top_matches(50, "Marketing")) == 0))

    store.close()

# --------------------------------------------------------------------------
print("\n[4b] Carga de perfiles")

perfiles = config.load_profiles()
nombres = [p["nombre"] for p in perfiles]
results.append(check(f"carga los perfiles de disco ({', '.join(nombres)})", len(perfiles) >= 2))
results.append(check("cada perfil tiene nombre y umbrales",
                     all(p.get("nombre") and p.get("umbral_alerta") for p in perfiles)))
results.append(check("la unión de departamentos no duplica",
                     len(config.union_departamentos(perfiles)) ==
                     len(set(config.union_departamentos(perfiles)))))
results.append(check("la unión de datasets no duplica",
                     len(config.union_datasets(perfiles)) ==
                     len(set(config.union_datasets(perfiles)))))

edu = next(p for p in perfiles if "ITGEM" in p["nombre"])
mkt = next(p for p in perfiles if "arketing" in p["nombre"])
publicidad = dict(SAMPLE, id_del_proceso="VA-2026-003",
                  descripci_n_del_procedimiento="Campaña publicitaria y produccion audiovisual institucional",
                  codigo_principal_de_categoria="V1.82101500")
results.append(check("un proceso de publicidad puntúa en Marketing",
                     score_record(publicidad, schema, mkt).score >= 60))
results.append(check("ese mismo proceso NO puntúa en Educación",
                     score_record(publicidad, schema, edu).score == 0))

# --------------------------------------------------------------------------
print("\n[4c] Filtro geográfico local")

fuera = dict(SAMPLE, id_del_proceso="VA-2026-004", departamento_entidad="Antioquia")
results.append(check("descarta un proceso de otro departamento",
                     not matches_geography(fuera, schema, edu)))
results.append(check("acepta el departamento del perfil",
                     matches_geography(SAMPLE, schema, edu)))
sin_dep = {k: v for k, v in SAMPLE.items() if k != "departamento_entidad"}
results.append(check("sin departamento publicado, no descarta",
                     matches_geography(sin_dep, schema, edu)))

# --------------------------------------------------------------------------
print("\n[5] Dashboard")

html = build_dashboard(
    [{"score": 88, "perfil": "Educación", "objeto": "Capacitación <script>alert(1)</script>",
      "entidad": "Alcaldía", "valor": 250000000.0, "fecha_cierre": "2030-01-01",
      "first_seen": "2026-08-06T10:00:00", "url": "https://x.co",
      "reasons": "Palabras clave: capacitacion"},
     {"score": 71, "perfil": "Marketing", "objeto": "Campaña publicitaria",
      "entidad": "Gobernación", "valor": 60000000.0, "fecha_cierre": "2030-02-01",
      "first_seen": "2026-08-06T11:00:00", "url": "", "reasons": "Palabras clave: publicidad"}],
    [{"perfil": "Educación", "objeto": "Proceso retirado", "first_seen": "2026-08-01T09:00:00",
      "last_seen": "2026-08-01T09:18:00", "disappeared_at": "2026-08-01T09:30:00"}],
    {"activos": 2, "desaparecidos": 1, "total": 3, "sweeps": 5},
    [{"nombre": "Educación", "geografia": {"departamentos": ["Valle del Cauca"]}},
     {"nombre": "Marketing", "geografia": {"departamentos": ["Valle del Cauca"]}}],
    {"Educación": {"activos": 1}, "Marketing": {"activos": 1}},
)
results.append(check("genera HTML", html.startswith("<!doctype html>")))
results.append(check("escapa HTML inyectado en los datos", "<script>alert(1)</script>" not in html))
results.append(check("muestra la cobertura geográfica", "Valle del Cauca" in html))
results.append(check("incluye la sección de retirados", "retirados" in html.lower()))
results.append(check("muestra ambos perfiles", "Educación" in html and "Marketing" in html))
results.append(check("incluye los filtros por perfil", 'data-filtro=' in html))
results.append(check("flatten_record aplana estructuras anidadas",
                     "community" in flatten_record(SAMPLE)))

# --------------------------------------------------------------------------
print("\n[5b] Precisión: el objeto pesa más que el resto del registro")

# El caso que inflaba los resultados en producción: una entidad cuyo NOMBRE
# contiene la palabra clave, pero cuyo contrato no tiene nada que ver.
toner = {
    "id_del_proceso": "VA-2026-900",
    "entidad": "SECRETARÍA DE EDUCACIÓN DEL VALLE",
    "descripci_n_del_procedimiento": "Suministro de tóner para impresoras de oficina",
    "departamento_entidad": "Valle del Cauca",
    "precio_base": "80000000",
    "codigo_principal_de_categoria": "V1.44103100",
}
# Sin código UNSPSC de educación a propósito: una alerta no puede depender de
# que la entidad haya clasificado bien el contrato.
real = dict(toner, id_del_proceso="VA-2026-901",
            descripci_n_del_procedimiento="Capacitación en competencias laborales para docentes")

score_toner = score_record(toner, schema, edu).score
score_real = score_record(real, schema, edu).score
results.append(check(f"contrato de tóner de la Secretaría de Educación puntúa bajo ({score_toner})",
                     score_toner < edu["umbral_alerta"]))
results.append(check(f"contrato real de capacitación puntúa alto ({score_real})",
                     score_real >= edu["umbral_alerta"]))
results.append(check("el objeto real supera claramente al falso positivo",
                     score_real - score_toner >= 25))
# El mecanismo objeto-vs-contexto se verifica con un criterio sintético: el
# perfil real de ITGEM ya no lleva términos sueltos como 'educacion', así que
# el nombre de la entidad por sí solo no dispara nada — que es justamente el
# efecto buscado.
solo_nombre = dict(toner, entidad="INSTITUTO DE CAPACITACION DEL VALLE",
                   descripci_n_del_procedimiento="Suministro de tóner para impresoras")
razones = score_record(solo_nombre, schema, CRITERIA).reasons
results.append(check("la razón distingue objeto de contexto",
                     any("contexto" in r.lower() for r in razones)))
results.append(check("una keyword sólo en el nombre de la entidad no alcanza para alertar",
                     score_record(solo_nombre, schema, CRITERIA).score < CRITERIA["umbral_alerta"]))

# --------------------------------------------------------------------------
print("\n[5c] Filtros del servidor")

from radar.socrata import build_date_where, build_where, order_by_newest  # noqa: E402

# Esquema con fecha de publicación, que es la columna sobre la que se acota.
con_fecha = resolve_schema(dict(SAMPLE, fecha_de_publicacion_del="2026-08-01T00:00:00.000"))
results.append(check("resuelve la fecha de publicación",
                     con_fecha.get("fecha_publicacion") == "fecha_de_publicacion_del"))

clausula = build_date_where(con_fecha, 45)
results.append(check(f"la ventana de fechas genera cláusula ({clausula})",
                     clausula and "fecha_de_publicacion_del >=" in clausula))
results.append(check("sin días no hay cláusula de fecha",
                     build_date_where(con_fecha, None) is None))
results.append(check("sin columna de fecha tampoco",
                     build_date_where({}, 45) is None))

combinado = build_where(con_fecha, ["Valle del Cauca"], 45)
results.append(check("combina geografía y fecha con AND",
                     combinado and " AND " in combinado and "Valle" in combinado))
results.append(check("sólo geografía no lleva AND",
                     " AND " not in (build_where(con_fecha, ["Valle del Cauca"], None) or "")))
results.append(check("ordena por fecha de publicación descendente",
                     (order_by_newest(con_fecha) or "").endswith("DESC")))
results.append(check("sin filtros devuelve None",
                     build_where({}, [], None) is None))

# --------------------------------------------------------------------------
print("\n[5d] Perfil ITGEM contra casos reales del sector")

# Casos tomados del lenguaje real de la contratación pública colombiana. Los
# de RUIDO son los que hacían inservible el primer barrido: el PAE y el
# transporte escolar mueven cifras enormes y su texto está lleno de
# 'educación', pero no son formación laboral.
base_itgem = {
    "id_del_proceso": "VA-ITGEM", "entidad": "ALCALDÍA DE CALI",
    "departamento_entidad": "Valle del Cauca", "precio_base": "200000000",
    "codigo_principal_de_categoria": "V1.86101700",
    "fecha_de_recepcion_de": "2030-01-01T00:00:00.000",
}
esquema_itgem = resolve_schema(dict(base_itgem, descripci_n_del_procedimiento="x"))

deben_alertar = [
    "Formación para el trabajo y desarrollo humano en competencias laborales",
    "Capacitación técnica laboral por competencias para población vulnerable",
    "Diplomado en gestión empresarial y emprendimiento",
    "Certificación de competencias laborales modalidad virtual",
]
deben_descartarse = [
    "Prestación del servicio de alimentación escolar PAE en instituciones educativas",
    "Transporte escolar para estudiantes de zona rural",
    "Construcción de aulas e infraestructura educativa",
    "Dotación escolar y mobiliario para sedes educativas",
    "Suministro de tóner para la Secretaría de Educación",
]

itgem = next(p for p in perfiles if "ITGEM" in p["nombre"])
for objeto in deben_alertar:
    s = score_record(dict(base_itgem, descripci_n_del_procedimiento=objeto),
                     esquema_itgem, itgem).score
    results.append(check(f"alerta ({s}): {objeto[:48]}", s >= itgem["umbral_alerta"]))

for objeto in deben_descartarse:
    s = score_record(dict(base_itgem, descripci_n_del_procedimiento=objeto),
                     esquema_itgem, itgem).score
    results.append(check(f"descarta ({s}): {objeto[:48]}", s < itgem["umbral_score"]))

results.append(check("cubre Cauca además de Valle (sedes de Puerto Tejada y Corinto)",
                     "Cauca" in itgem["geografia"]["departamentos"]))

# --------------------------------------------------------------------------
print("\n[6] Persistencia en texto (JSONL)")

with tempfile.TemporaryDirectory() as tmp:
    ruta = Path(tmp)
    archivo = ruta / "archivo.jsonl"

    origen = Store(str(ruta / "origen.db"))
    origen.upsert_matches(batch, schema, "procesos", "Educación")
    origen.upsert_matches(batch, schema, "procesos", "Marketing")
    origen.mark_disappeared(set(), "procesos", "Marketing")
    exportados = origen.export_jsonl(archivo)
    origen.close()
    results.append(check(f"exporta todos los registros ({exportados})", exportados == 4))

    # Un runner efímero: base nueva, restaurada desde el texto versionado.
    destino = Store(str(ruta / "destino.db"))
    restaurados = destino.import_jsonl(archivo)
    results.append(check("restaura el archivo en una base vacía", restaurados == 4))
    results.append(check("los perfiles sobreviven al round-trip",
                         set(destino.stats_por_perfil()) == {"Educación", "Marketing"}))
    results.append(check("el estado de retiro sobrevive",
                         destino.stats("Marketing")["desaparecidos"] == 2 and
                         destino.stats("Educación")["desaparecidos"] == 0))

    fila = destino.conn.execute(
        "SELECT first_seen, raw FROM procesos WHERE perfil = 'Educación' LIMIT 1").fetchone()
    results.append(check("first_seen se preserva", bool(fila["first_seen"])))
    results.append(check("el registro crudo de SECOP se preserva",
                         "id_del_proceso" in json.loads(fila["raw"])))

    destino.import_jsonl(archivo)
    results.append(check("reimportar es idempotente", destino.stats()["total"] == 4))

    lineas = archivo.read_text(encoding="utf-8").strip().split("\n")
    results.append(check("una línea por registro", len(lineas) == 4))

    # Lo que hace limpio el diff de git no es un orden alfabético de las líneas,
    # sino que dos exportaciones del mismo estado sean byte a byte idénticas.
    segunda = ruta / "segunda.jsonl"
    destino.export_jsonl(segunda)
    results.append(check("exportar dos veces da un archivo idéntico",
                         archivo.read_text(encoding="utf-8") ==
                         segunda.read_text(encoding="utf-8")))
    results.append(check("las claves van ordenadas dentro de cada línea",
                         list(json.loads(lineas[0])) == sorted(json.loads(lineas[0]))))
    results.append(check("archivo inexistente devuelve 0 sin reventar",
                         destino.import_jsonl(ruta / "no-existe.jsonl") == 0))
    destino.close()

# --------------------------------------------------------------------------
passed, total = sum(results), len(results)
print(f"\n{'=' * 60}")
print(f"  {passed}/{total} pruebas pasaron")
print(f"{'=' * 60}\n")
sys.exit(0 if passed == total else 1)
