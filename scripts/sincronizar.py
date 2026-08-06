#!/usr/bin/env python3
"""Carga el archivo JSONL existente hacia Supabase, sin consultar SECOP.

Sirve para poblar la base la primera vez, o para reconstruirla desde la
bitácora versionada si algo se pierde del lado de Supabase. El JSONL en git es
la fuente de verdad; Supabase es una proyección consultable de esa fuente.

    export SUPABASE_URL=https://xxxx.supabase.co
    export SUPABASE_SERVICE_KEY=...
    python scripts/sincronizar.py
"""

import argparse
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.store import Store  # noqa: E402
from radar.supabase_writer import SupabaseWriter  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("sincronizar")

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description="Carga el archivo hacia Supabase")
    parser.add_argument("--archivo", default=str(ROOT / "data" / "archivo.jsonl"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Sólo contar, sin escribir")
    args = parser.parse_args()

    archivo = Path(args.archivo)
    if not archivo.exists():
        log.error("No existe %s. Corré un barrido primero.", archivo)
        return 1

    nube = SupabaseWriter()
    if not nube.activo and not args.dry_run:
        log.error("Faltan SUPABASE_URL y/o SUPABASE_SERVICE_KEY en el entorno.")
        return 1

    # Base temporal: sólo se usa para releer el JSONL con los tipos correctos.
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(str(Path(tmp) / "sync.db"))
        cargados = store.import_jsonl(archivo)
        log.info("Leídos %s registros de %s", cargados, archivo)

        filas = store.todas_las_filas()
        por_perfil = {}
        for f in filas:
            por_perfil[f["perfil"]] = por_perfil.get(f["perfil"], 0) + 1
        for perfil, n in sorted(por_perfil.items()):
            log.info("  %s: %s", perfil, n)

        if args.dry_run:
            log.info("[dry-run] No se escribió nada.")
        else:
            escritas = nube.upsert_procesos(filas)
            log.info("Sincronizadas %s de %s filas hacia Supabase", escritas, len(filas))

        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
