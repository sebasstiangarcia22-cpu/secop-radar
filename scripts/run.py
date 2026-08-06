#!/usr/bin/env python3
"""Punto de entrada: un barrido de SECOP contra todos los perfiles activos.

    python scripts/run.py                      # barrido completo
    python scripts/run.py --dry-run            # sin enviar correo
    python scripts/run.py --perfil educacion   # sólo un perfil
    python scripts/run.py --no-dashboard       # sin generar HTML

Los datos se descargan UNA vez y se evalúan contra todos los perfiles: agregar
clientes no multiplica las consultas a SECOP ni acerca al límite de tasa.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import config, notify, report  # noqa: E402
from radar.fields import get, resolve_schema  # noqa: E402
from radar.scoring import filter_and_score  # noqa: E402
from radar.socrata import SocrataClient, build_where, order_by_newest  # noqa: E402
from radar.store import Store  # noqa: E402
from radar.supabase_writer import SupabaseWriter  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("radar")

ROOT = Path(__file__).resolve().parent.parent


def descargar(dataset: str, client: SocrataClient, perfiles: list):
    """Descarga un dataset una sola vez, con el filtro geográfico de la unión."""
    sample = client.sample(dataset, limit=1)
    if not sample:
        log.error("El dataset '%s' no devolvio registros; se omite.", dataset)
        return [], {}

    schema = resolve_schema(sample[0])
    log.info("Campos resueltos: %s", ", ".join(sorted(schema)) or "ninguno")
    faltantes = {"objeto", "entidad", "id"} - set(schema)
    if faltantes:
        log.warning(
            "Sin alias para %s. Corre scripts/discover_schema.py y agrega los "
            "nombres reales en radar/fields.py.", ", ".join(sorted(faltantes))
        )

    where = build_where(
        schema,
        config.union_departamentos(perfiles),
        config.union_dias_atras(perfiles),
    )
    log.info("Filtro: %s", where or "(ninguno — se descarga todo)")
    if not schema.get("fecha_publicacion"):
        log.warning(
            "Sin columna de fecha de publicacion: el barrido no puede acotarse "
            "en el tiempo y va a traer historico completo."
        )

    tope = config.max_registros(perfiles)
    records = client.fetch_all(
        dataset, where=where, order=order_by_newest(schema), max_records=tope
    )
    log.info("Descargados %s registros de '%s'", len(records), dataset)
    if len(records) >= tope:
        log.warning(
            "Se alcanzo el tope de %s registros: la cobertura esta truncada. "
            "Reduce 'dias_atras' o sube 'max_registros' en los perfiles.", tope
        )
    return records, schema


def evaluar(perfil: dict, records: list, schema: dict, dataset: str, store: Store):
    """Evalúa los registros ya descargados contra un perfil."""
    nombre = perfil["nombre"]
    matches = filter_and_score(records, schema, perfil)

    # Dos cifras distintas y ambas importan: lo archivado alimenta el dashboard
    # y el histórico, pero lo que define si el radar es útil o es spam es
    # cuántas superan el umbral de alerta, que es lo que llega al correo.
    alerta = perfil["umbral_alerta"]
    sobre_alerta = sum(1 for m in matches if m.score >= alerta)
    log.info("[%s] %s archivadas (>=%s) · %s alertables (>=%s)",
             nombre, len(matches), perfil["umbral_score"], sobre_alerta, alerta)

    fresh = store.upsert_matches(matches, schema, dataset, nombre)
    if fresh:
        log.info("[%s] %s coincidencias nuevas", nombre, len(fresh))

    vistos = {str(get(m.record, schema, "id") or "") for m in matches}
    vanished = store.mark_disappeared(vistos, dataset, nombre)
    if vanished:
        log.warning("[%s] %s proceso(s) desaparecieron del portal", nombre, len(vanished))

    store.log_sweep(nombre, dataset, len(records), len(matches), len(fresh))
    return fresh, vanished, sobre_alerta


def notificar(perfil: dict, fresh: list, vanished: list, schema: dict,
              store: Store, dry_run: bool):
    nombre = perfil["nombre"]
    umbral = perfil["umbral_alerta"]
    alertables = [(i, m) for i, m in fresh if m.score >= umbral]

    if not (alertables or vanished):
        log.info("[%s] Nada nuevo que reportar.", nombre)
        return

    stats = store.stats(nombre)
    html = notify.build_email_html(alertables, vanished, stats, schema, nombre)
    asunto = f"Radar SECOP [{nombre}] — {len(alertables)} oportunidad(es) nueva(s)"
    if vanished:
        asunto += f" · {len(vanished)} retirada(s)"

    if dry_run:
        salida = ROOT / "data" / f"correo_{_slug(nombre)}.html"
        salida.parent.mkdir(parents=True, exist_ok=True)
        salida.write_text(html, encoding="utf-8")
        log.info("[%s] [dry-run] Correo escrito en %s", nombre, salida)
    elif notify.send_email(asunto, html, to=perfil.get("alertar_a") or None):
        store.mark_alerted([i for i, _ in alertables], nombre)


def _slug(texto: str) -> str:
    from radar.normalize import normalize
    return normalize(texto).replace(" ", "_") or "perfil"


def main():
    parser = argparse.ArgumentParser(description="Radar SECOP multi-perfil")
    parser.add_argument("--dry-run", action="store_true", help="No enviar correo")
    parser.add_argument("--no-dashboard", action="store_true", help="No generar HTML")
    parser.add_argument("--perfil", default=None, help="Correr sólo este perfil (por nombre de archivo)")
    parser.add_argument("--perfiles-dir", default=None, help="Directorio de perfiles")
    parser.add_argument("--db", default=str(ROOT / "data" / "radar.db"),
                        help="SQLite de trabajo (efímero, no se versiona)")
    parser.add_argument("--archivo", default=str(ROOT / "data" / "archivo.jsonl"),
                        help="Archivo durable en texto (este SÍ se versiona)")
    args = parser.parse_args()

    if args.perfil:
        directorio = Path(args.perfiles_dir) if args.perfiles_dir else config.PERFILES_DIR
        perfiles = [config.load_profile(directorio / f"{args.perfil}.yml")]
    else:
        perfiles = config.load_profiles(args.perfiles_dir)

    log.info("Perfiles activos: %s", ", ".join(p["nombre"] for p in perfiles))

    client = SocrataClient()
    store = Store(args.db)
    nube = SupabaseWriter()
    ultimo_schema = {}

    # Decirlo en voz alta: un barrido que termina "bien" sin haber escrito en
    # Supabase es indistinguible de uno que sí escribió, y esa ambigüedad
    # cuesta mucho más que una línea de log.
    if nube.activo:
        log.info("Supabase configurado: %s", nube.url)
    else:
        faltan = [v for v in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY")
                  if not os.environ.get(v)]
        log.warning("Supabase NO configurado (falta %s). El barrido archiva "
                    "igual, pero el dashboard no se actualiza.",
                    " y ".join(faltan) or "credenciales validas")

    # El runner es efímero: el estado durable vive en el JSONL versionado.
    archivo = Path(args.archivo)
    restaurados = store.import_jsonl(archivo)
    if restaurados:
        log.info("Archivo restaurado desde %s (%s registros)", archivo, restaurados)

    try:
        for dataset in config.union_datasets(perfiles):
            log.info("=== Descargando '%s' ===", dataset)
            try:
                records, schema = descargar(dataset, client, perfiles)
            except Exception as exc:  # noqa: BLE001 - un dataset malo no frena al resto
                log.error("La descarga de '%s' fallo: %s", dataset, exc)
                continue

            if not records:
                continue
            ultimo_schema = schema or ultimo_schema

            for perfil in perfiles:
                if dataset not in perfil.get("datasets", []):
                    continue
                try:
                    fresh, vanished, alertables = evaluar(
                        perfil, records, schema, dataset, store)
                    notificar(perfil, fresh, vanished, schema, store, args.dry_run)
                    nube.log_barrido(perfil["nombre"], dataset, len(records),
                                     len(store.top_matches(100000, perfil["nombre"])),
                                     alertables, len(fresh))
                except Exception as exc:  # noqa: BLE001 - un perfil malo no frena al resto
                    log.error("[%s] El perfil fallo: %s", perfil["nombre"], exc)

        if not args.no_dashboard:
            dash = report.write_dashboard(
                ROOT / "dashboard" / "index.html",
                store.top_matches(300),
                store.recent_disappearances(50),
                store.stats(),
                perfiles,
                store.stats_por_perfil(),
            )
            report.write_json_feed(ROOT / "data" / "oportunidades.json", store.top_matches(300))
            log.info("Dashboard generado en %s", dash)

        guardados = store.export_jsonl(archivo)
        log.info("Archivo persistido en %s (%s registros)", archivo, guardados)

        # El JSONL es la bitacora; Supabase es la capa de consulta del
        # dashboard. Si no hay credenciales, el barrido ya termino bien.
        if nube.activo:
            filas = store.todas_las_filas()
            escritas = nube.upsert_procesos(filas)
            if escritas < len(filas):
                log.error("Solo se sincronizaron %s de %s filas hacia Supabase",
                          escritas, len(filas))
        log.info("Resumen por perfil: %s", store.stats_por_perfil())
    finally:
        store.close()


if __name__ == "__main__":
    main()
