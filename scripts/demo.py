#!/usr/bin/env python3
"""Genera dashboard y correos de muestra sin tocar la red.

Sirve para ver cómo se ve el sistema antes de tener credenciales, y para
enseñárselo a alguien sin esperar a que ocurra un barrido real.

    python scripts/demo.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import config, notify, report  # noqa: E402
from radar.fields import resolve_schema  # noqa: E402
from radar.normalize import normalize  # noqa: E402
from radar.scoring import filter_and_score  # noqa: E402
from radar.store import Store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Registros con la forma real de SECOP II, escritos a propósito con tildes
# y mayúsculas inconsistentes — igual que los publican las entidades.
MUESTRA = [
    {
        "id_del_proceso": "CO1.REQ.4821001",
        "entidad": "ALCALDÍA DE SANTIAGO DE CALI",
        "descripci_n_del_procedimiento": "Prestación de servicios de CAPACITACIÓN en competencias laborales para población vulnerable del Distrito",
        "departamento_entidad": "Valle del Cauca",
        "ciudad_entidad": "Cali",
        "precio_base": "420000000",
        "modalidad_de_contratacion": "Concurso de méritos abierto",
        "estado_del_procedimiento": "Publicado",
        "fecha_de_publicacion_del": "2026-08-04T08:00:00.000",
        "fecha_de_recepcion_de": "2026-08-09T17:00:00.000",
        "codigo_principal_de_categoria": "V1.86101700",
        "urlproceso": {"url": "https://community.secop.gov.co/Public/Tendering/OpportunityDetail/Index?noticeUID=CO1.NTC.4821001"},
    },
    {
        "id_del_proceso": "CO1.REQ.4833127",
        "entidad": "GOBERNACIÓN DEL VALLE DEL CAUCA",
        "descripci_n_del_procedimiento": "Formación para el trabajo y desarrollo humano en competencias digitales — modalidad virtual",
        "departamento_entidad": "Valle del Cauca",
        "ciudad_entidad": "Cali",
        "precio_base": "185000000",
        "modalidad_de_contratacion": "Selección abreviada",
        "estado_del_procedimiento": "Publicado",
        "fecha_de_publicacion_del": "2026-08-05T10:30:00.000",
        "fecha_de_recepcion_de": "2026-08-20T17:00:00.000",
        "codigo_principal_de_categoria": "V1.86111600",
        "urlproceso": {"url": "https://community.secop.gov.co/Public/Tendering/OpportunityDetail/Index?noticeUID=CO1.NTC.4833127"},
    },
    {
        "id_del_proceso": "CO1.REQ.4840556",
        "entidad": "UNIVERSIDAD DEL VALLE",
        "descripci_n_del_procedimiento": "Diplomado en bilingüismo e inglés técnico con certificación internacional",
        "departamento_entidad": "Valle del Cauca",
        "ciudad_entidad": "Palmira",
        "precio_base": "96000000",
        "modalidad_de_contratacion": "Mínima cuantía",
        "estado_del_procedimiento": "Publicado",
        "fecha_de_recepcion_de": "2026-09-15T17:00:00.000",
        "codigo_principal_de_categoria": "V1.86132000",
        "urlproceso": {"url": "https://community.secop.gov.co/Public/Tendering/OpportunityDetail/Index?noticeUID=CO1.NTC.4840556"},
    },
    {
        "id_del_proceso": "CO1.REQ.4844310",
        "entidad": "SECRETARÍA DE CULTURA Y TURISMO",
        "descripci_n_del_procedimiento": "Diseño y ejecución de campaña publicitaria institucional con producción audiovisual y pauta en redes sociales",
        "departamento_entidad": "Valle del Cauca",
        "ciudad_entidad": "Cali",
        "precio_base": "310000000",
        "modalidad_de_contratacion": "Concurso de méritos",
        "estado_del_procedimiento": "Publicado",
        "fecha_de_recepcion_de": "2026-08-11T17:00:00.000",
        "codigo_principal_de_categoria": "V1.82101500",
        "urlproceso": {"url": "https://community.secop.gov.co/Public/Tendering/OpportunityDetail/Index?noticeUID=CO1.NTC.4844310"},
    },
    {
        "id_del_proceso": "CO1.REQ.4845200",
        "entidad": "EMPRESA MUNICIPAL DE RENOVACIÓN URBANA",
        "descripci_n_del_procedimiento": "Estrategia de comunicación y piezas gráficas para socialización de proyectos",
        "departamento_entidad": "Valle del Cauca",
        "precio_base": "74000000",
        "estado_del_procedimiento": "Publicado",
        "fecha_de_recepcion_de": "2026-08-28T17:00:00.000",
        "codigo_principal_de_categoria": "V1.82141500",
        "urlproceso": {"url": "https://community.secop.gov.co/w"},
    },
    {
        # Distractor: menciona "capacitación" y "diseño" pero es obra civil.
        "id_del_proceso": "CO1.REQ.4839900",
        "entidad": "SECRETARÍA DE INFRAESTRUCTURA",
        "descripci_n_del_procedimiento": "Pavimentación de vías rurales incluida capacitación al personal de obra civil y diseño estructural",
        "departamento_entidad": "Valle del Cauca",
        "precio_base": "8900000000",
        "estado_del_procedimiento": "Publicado",
        "fecha_de_recepcion_de": "2026-08-25T17:00:00.000",
        "codigo_principal_de_categoria": "V1.72141000",
        "urlproceso": {"url": "https://community.secop.gov.co/x"},
    },
    {
        # Distractor: otro departamento, debe caer por geografía.
        "id_del_proceso": "CO1.REQ.4846001",
        "entidad": "ALCALDÍA DE MEDELLÍN",
        "descripci_n_del_procedimiento": "Capacitación en competencias laborales y formación técnica",
        "departamento_entidad": "Antioquia",
        "precio_base": "260000000",
        "estado_del_procedimiento": "Publicado",
        "codigo_principal_de_categoria": "V1.86101700",
        "urlproceso": {"url": "https://community.secop.gov.co/y"},
    },
]

# Este proceso aparece en el primer barrido y desaparece en el segundo:
# es la simulación exacta del comportamiento que motivó el proyecto.
EFIMERO = {
    "id_del_proceso": "CO1.REQ.4842777",
    "entidad": "INSTITUTO MUNICIPAL DE EDUCACIÓN",
    "descripci_n_del_procedimiento": "Curso de formación en competencias laborales y habilidades blandas",
    "departamento_entidad": "Valle del Cauca",
    "precio_base": "140000000",
    "estado_del_procedimiento": "Publicado",
    "fecha_de_recepcion_de": "2026-08-08T17:00:00.000",
    "codigo_principal_de_categoria": "V1.86101800",
    "urlproceso": {"url": "https://community.secop.gov.co/z"},
}


def main():
    perfiles = config.load_profiles()
    schema = resolve_schema(MUESTRA[0])

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(str(Path(tmp) / "demo.db"))
        nuevos_por_perfil, retirados_por_perfil = {}, {}

        print(f"\nPerfiles cargados: {', '.join(p['nombre'] for p in perfiles)}")
        print(f"Registros descargados una sola vez: {len(MUESTRA) + 1}\n")

        print("--- Barrido 1: el proceso efímero está publicado ---")
        for perfil in perfiles:
            nombre = perfil["nombre"]
            matches = filter_and_score(MUESTRA + [EFIMERO], schema, perfil)
            print(f"\n  [{nombre}] {len(matches)} coincidencia(s):")
            for match in matches:
                objeto = match.record["descripci_n_del_procedimiento"][:58]
                print(f"    {match.score:>3}  {objeto}")
            nuevos_por_perfil[nombre] = store.upsert_matches(
                matches, schema, "procesos", nombre)
            store.log_sweep(nombre, "procesos", len(MUESTRA) + 1, len(matches),
                            len(nuevos_por_perfil[nombre]))

        print("\n--- Barrido 2: el proceso efímero ya no aparece ---")
        for perfil in perfiles:
            nombre = perfil["nombre"]
            matches = filter_and_score(MUESTRA, schema, perfil)
            store.upsert_matches(matches, schema, "procesos", nombre)
            vistos = {m.record["id_del_proceso"] for m in matches}
            retirados = store.mark_disappeared(vistos, "procesos", nombre)
            retirados_por_perfil[nombre] = retirados
            store.log_sweep(nombre, "procesos", len(MUESTRA), len(matches), 0)
            for item in retirados:
                print(f"  [{nombre}] RETIRADO: {item['objeto'][:50]}")
                print(f"            visible {item['first_seen']} -> {item['last_seen']}")

        dash = report.write_dashboard(
            ROOT / "dashboard" / "index.html",
            store.top_matches(100),
            store.recent_disappearances(20),
            store.stats(),
            perfiles,
            store.stats_por_perfil(),
        )
        print(f"\n  Dashboard: {dash}")

        for perfil in perfiles:
            nombre = perfil["nombre"]
            html = notify.build_email_html(
                nuevos_por_perfil[nombre], retirados_por_perfil.get(nombre, []),
                store.stats(nombre), schema, nombre)
            slug = normalize(nombre).replace(" ", "_")
            salida = ROOT / "data" / f"correo_{slug}.html"
            salida.parent.mkdir(parents=True, exist_ok=True)
            salida.write_text(html, encoding="utf-8")
            print(f"  Correo:    {salida}")

        print(f"\n  Resumen por perfil: {store.stats_por_perfil()}\n")
        store.close()


if __name__ == "__main__":
    main()
