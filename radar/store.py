"""Archivo local de todo lo que el radar ha visto.

Esta es la parte que responde al problema real de SECOP: procesos que se
publican, duran unos minutos y desaparecen. Una vez que un registro entra acá
ya no sale, así que un proceso retirado del portal sigue documentado de este
lado — y como comparamos `last_seen` contra cada barrido, el retiro mismo se
reporta como hallazgo.

La clave primaria es (id, perfil): un mismo proceso puede interesarle a varios
perfiles con puntajes distintos, y cada uno lleva su propio seguimiento.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS procesos (
    id              TEXT NOT NULL,
    perfil          TEXT NOT NULL,
    dataset         TEXT NOT NULL,
    entidad         TEXT,
    objeto          TEXT,
    departamento    TEXT,
    valor           REAL,
    modalidad       TEXT,
    estado          TEXT,
    fecha_cierre    TEXT,
    url             TEXT,
    score           INTEGER,
    reasons         TEXT,
    raw             TEXT NOT NULL,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    sweeps_seen     INTEGER NOT NULL DEFAULT 1,
    alerted_at      TEXT,
    disappeared_at  TEXT,
    PRIMARY KEY (id, perfil)
);

CREATE INDEX IF NOT EXISTS idx_score      ON procesos(score DESC);
CREATE INDEX IF NOT EXISTS idx_first_seen ON procesos(first_seen DESC);
CREATE INDEX IF NOT EXISTS idx_perfil     ON procesos(perfil);

CREATE TABLE IF NOT EXISTS sweeps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    perfil      TEXT NOT NULL,
    dataset     TEXT NOT NULL,
    fetched     INTEGER NOT NULL,
    matched     INTEGER NOT NULL,
    new_matches INTEGER NOT NULL,
    notes       TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SchemaDesactualizada(RuntimeError):
    pass


class Store:
    def __init__(self, path: str = "secop-radar/data/radar.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._check_legacy_schema(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _check_legacy_schema(self, path):
        """Detecta bases creadas antes del soporte multi-perfil.

        La clave primaria cambió de (id) a (id, perfil), y eso no se puede
        corregir con ALTER TABLE. Es preferible fallar con un mensaje claro
        que corromper el archivo en silencio.
        """
        existe = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='procesos'"
        ).fetchone()
        if not existe:
            return
        columnas = {r["name"] for r in self.conn.execute("PRAGMA table_info(procesos)")}
        if "perfil" not in columnas:
            raise SchemaDesactualizada(
                f"La base '{path}' es de la version anterior (sin perfiles). "
                "Borrala y volve a correr el barrido: se reconstruye sola."
            )

    def close(self):
        self.conn.close()

    def upsert_matches(self, matches: list, schema: dict, dataset: str, perfil: str) -> list:
        """Inserta o refresca coincidencias. Devuelve sólo las nunca vistas.

        Un registro ya conocido conserva su `first_seen` original — esa marca
        es la evidencia de cuándo apareció el proceso de verdad, sin importar
        lo que el portal muestre después.
        """
        from .fields import get

        timestamp = now()
        fresh = []

        for match in matches:
            record = match.record
            identifier = str(
                get(record, schema, "id")
                or record.get("id_del_proceso")
                or hash(json.dumps(record, sort_keys=True))
            )

            existing = self.conn.execute(
                "SELECT id FROM procesos WHERE id = ? AND perfil = ?", (identifier, perfil)
            ).fetchone()

            payload = (
                dataset,
                get(record, schema, "entidad"),
                get(record, schema, "objeto"),
                get(record, schema, "departamento"),
                _num(get(record, schema, "valor")),
                get(record, schema, "modalidad"),
                get(record, schema, "estado"),
                str(get(record, schema, "fecha_cierre") or ""),
                get(record, schema, "url"),
                match.score,
                " | ".join(match.reasons),
                json.dumps(record, ensure_ascii=False),
            )

            if existing:
                self.conn.execute(
                    """UPDATE procesos SET dataset=?, entidad=?, objeto=?,
                       departamento=?, valor=?, modalidad=?, estado=?,
                       fecha_cierre=?, url=?, score=?, reasons=?, raw=?,
                       last_seen=?, sweeps_seen = sweeps_seen + 1,
                       disappeared_at = NULL
                       WHERE id=? AND perfil=?""",
                    payload + (timestamp, identifier, perfil),
                )
            else:
                self.conn.execute(
                    """INSERT INTO procesos (id, perfil, dataset, entidad, objeto,
                       departamento, valor, modalidad, estado, fecha_cierre,
                       url, score, reasons, raw, first_seen, last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (identifier, perfil) + payload + (timestamp, timestamp),
                )
                fresh.append((identifier, match))

        self.conn.commit()
        return fresh

    def mark_disappeared(self, seen_ids: set, dataset: str, perfil: str) -> list:
        """Marca procesos de este perfil ausentes del barrido actual."""
        rows = self.conn.execute(
            """SELECT id, perfil, entidad, objeto, first_seen, last_seen, url, score
               FROM procesos
               WHERE dataset = ? AND perfil = ? AND disappeared_at IS NULL""",
            (dataset, perfil),
        ).fetchall()

        vanished = []
        timestamp = now()
        for row in rows:
            if row["id"] in seen_ids:
                continue
            self.conn.execute(
                "UPDATE procesos SET disappeared_at = ? WHERE id = ? AND perfil = ?",
                (timestamp, row["id"], perfil),
            )
            vanished.append(dict(row))

        self.conn.commit()
        return vanished

    def mark_alerted(self, ids: list, perfil: str):
        timestamp = now()
        self.conn.executemany(
            """UPDATE procesos SET alerted_at = ?
               WHERE id = ? AND perfil = ? AND alerted_at IS NULL""",
            [(timestamp, i, perfil) for i in ids],
        )
        self.conn.commit()

    def log_sweep(self, perfil: str, dataset: str, fetched: int, matched: int,
                  new_matches: int, notes: str = ""):
        self.conn.execute(
            """INSERT INTO sweeps (started_at, perfil, dataset, fetched, matched,
               new_matches, notes) VALUES (?,?,?,?,?,?,?)""",
            (now(), perfil, dataset, fetched, matched, new_matches, notes),
        )
        self.conn.commit()

    def top_matches(self, limit: int = 200, perfil: str | None = None) -> list:
        query = """SELECT * FROM procesos WHERE disappeared_at IS NULL"""
        params = []
        if perfil:
            query += " AND perfil = ?"
            params.append(perfil)
        query += " ORDER BY score DESC, first_seen DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def recent_disappearances(self, limit: int = 50, perfil: str | None = None) -> list:
        query = "SELECT * FROM procesos WHERE disappeared_at IS NOT NULL"
        params = []
        if perfil:
            query += " AND perfil = ?"
            params.append(perfil)
        query += " ORDER BY disappeared_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def stats(self, perfil: str | None = None) -> dict:
        where, params = ("WHERE perfil = ?", [perfil]) if perfil else ("", [])
        c = self.conn.execute
        total = c(f"SELECT COUNT(*) n FROM procesos {where}", params).fetchone()["n"]
        activos = c(
            f"SELECT COUNT(*) n FROM procesos {where or 'WHERE 1=1'}"
            " AND disappeared_at IS NULL", params).fetchone()["n"]
        desaparecidos = c(
            f"SELECT COUNT(*) n FROM procesos {where or 'WHERE 1=1'}"
            " AND disappeared_at IS NOT NULL", params).fetchone()["n"]
        sweeps = c(
            f"SELECT COUNT(*) n FROM sweeps {where}", params).fetchone()["n"]
        return {
            "total": total,
            "activos": activos,
            "desaparecidos": desaparecidos,
            "sweeps": sweeps,
        }

    # --- Persistencia en texto -------------------------------------------
    # El runner de GitHub Actions es efímero: si el archivo no se versiona, cada
    # barrido arranca en blanco y la detección de retiros nunca funciona. Pero
    # versionar el .sqlite es peor — git no delta-comprime binarios y el repo se
    # infla decenas de MB por día. Por eso el formato durable es JSONL: una línea
    # por registro, orden estable, y git guarda sólo las líneas que cambiaron.

    COLUMNAS = [
        "id", "perfil", "dataset", "entidad", "objeto", "departamento", "valor",
        "modalidad", "estado", "fecha_cierre", "url", "score", "reasons", "raw",
        "first_seen", "last_seen", "sweeps_seen", "alerted_at", "disappeared_at",
    ]

    def export_jsonl(self, path) -> int:
        """Vuelca el archivo completo a JSONL, ordenado para que el diff sea limpio."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.conn.execute(
            f"SELECT {', '.join(self.COLUMNAS)} FROM procesos ORDER BY perfil, id"
        ).fetchall()
        with open(path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        return len(rows)

    def import_jsonl(self, path) -> int:
        """Reconstruye el archivo desde JSONL. Ignora líneas corruptas."""
        path = Path(path)
        if not path.exists():
            return 0

        placeholders = ",".join("?" * len(self.COLUMNAS))
        cargados = 0
        with open(path, encoding="utf-8") as handle:
            for linea in handle:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    registro = json.loads(linea)
                except json.JSONDecodeError:
                    continue
                self.conn.execute(
                    f"INSERT OR REPLACE INTO procesos ({', '.join(self.COLUMNAS)}) "
                    f"VALUES ({placeholders})",
                    [registro.get(c) for c in self.COLUMNAS],
                )
                cargados += 1
        self.conn.commit()
        return cargados

    def stats_por_perfil(self) -> dict:
        """Resumen por perfil, para el encabezado del dashboard."""
        rows = self.conn.execute(
            """SELECT perfil,
                      COUNT(*) total,
                      SUM(CASE WHEN disappeared_at IS NULL THEN 1 ELSE 0 END) activos,
                      SUM(CASE WHEN disappeared_at IS NOT NULL THEN 1 ELSE 0 END) desaparecidos
               FROM procesos GROUP BY perfil ORDER BY perfil"""
        ).fetchall()
        return {r["perfil"]: dict(r) for r in rows}


def _num(value):
    try:
        return float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None
