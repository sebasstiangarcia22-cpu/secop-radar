#!/usr/bin/env python3
"""Print the live schema of each SECOP dataset.

Run this first, from a machine with internet access. The published docs lag the
real columns, so this is the ground truth for filling in radar/fields.py.

    python scripts/discover_schema.py
    python scripts/discover_schema.py --dataset procesos --sample 3
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.fields import FIELD_ALIASES, resolve_schema  # noqa: E402
from radar.socrata import DATASETS, SocrataClient  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=None, help="Uno de: " + ", ".join(DATASETS))
    parser.add_argument("--sample", type=int, default=1)
    parser.add_argument("--dump", action="store_true", help="Imprimir el registro completo")
    args = parser.parse_args()

    client = SocrataClient()
    targets = [args.dataset] if args.dataset else list(DATASETS)

    for dataset in targets:
        print(f"\n{'=' * 72}\n  {dataset}  ({DATASETS.get(dataset, dataset)})\n{'=' * 72}")
        try:
            records = client.sample(dataset, limit=args.sample)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: {exc}")
            continue

        if not records:
            print("  (sin registros)")
            continue

        record = records[0]
        print(f"\n  {len(record)} columnas presentes:\n")
        for key in sorted(record):
            value = str(record[key])[:70].replace("\n", " ")
            print(f"    {key:<45} {value}")

        resolved = resolve_schema(record)
        print("\n  --- Mapeo de campos logicos ---")
        for logical in FIELD_ALIASES:
            hit = resolved.get(logical)
            mark = "OK " if hit else "!! "
            print(f"    {mark}{logical:<20} -> {hit or 'NO RESUELTO'}")

        unresolved = [k for k in FIELD_ALIASES if k not in resolved]
        if unresolved:
            print(
                "\n  Agrega los nombres reales de estas columnas en "
                f"radar/fields.py: {', '.join(unresolved)}"
            )

        if args.dump:
            print("\n" + json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
