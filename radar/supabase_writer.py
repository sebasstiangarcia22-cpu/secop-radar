"""Escritura de resultados hacia Supabase.

El JSONL versionado sigue siendo la bitácora de auditoría: inmutable, fechada
por commit y legible sin credenciales. Supabase es la capa de consulta — lo que
le permite al dashboard buscar sobre todo el histórico en vez de mostrar la
foto del último barrido.

Si no hay credenciales configuradas, el barrido corre igual y sólo se salta
este paso. Nada de lo que ya funciona depende de que Supabase esté arriba.
"""

import json
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

LOTE = 500          # filas por request; PostgREST acepta arrays
MAX_REINTENTOS = 4

COLUMNAS = [
    "id", "perfil", "dataset", "entidad", "objeto", "departamento", "ciudad",
    "valor", "modalidad", "estado", "fecha_cierre", "fecha_publicacion", "url",
    "unspsc", "score", "reasons", "raw", "first_seen", "last_seen",
    "sweeps_seen", "alerted_at", "disappeared_at",
]


class SupabaseWriter:
    def __init__(self, url: str | None = None, service_key: str | None = None):
        self.url = (url or os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self.key = service_key or os.environ.get("SUPABASE_SERVICE_KEY") or ""
        self.session = requests.Session()
        if self.activo:
            self.session.headers.update({
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
            })

    @property
    def activo(self) -> bool:
        return bool(self.url and self.key)

    def _post(self, path: str, payload: list, prefer: str) -> bool:
        endpoint = f"{self.url}/rest/v1/{path}"
        headers = {"Prefer": prefer}
        espera = 2

        for intento in range(MAX_REINTENTOS):
            try:
                r = self.session.post(endpoint, headers=headers,
                                      data=json.dumps(payload, ensure_ascii=False,
                                                      default=str).encode("utf-8"),
                                      timeout=60)
                if r.status_code < 300:
                    return True
                # 4xx que no sea 429 es un error nuestro: reintentar no ayuda.
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    log.error("Supabase rechazo el lote (%s): %s",
                              r.status_code, r.text[:300])
                    return False
                log.warning("Supabase respondio %s; reintento en %ss",
                            r.status_code, espera)
            except requests.RequestException as exc:
                log.warning("Fallo de red hacia Supabase (%s); reintento en %ss",
                            exc, espera)

            if intento < MAX_REINTENTOS - 1:
                time.sleep(espera)
                espera *= 2

        log.error("No se pudo escribir en Supabase tras %s intentos", MAX_REINTENTOS)
        return False

    def upsert_procesos(self, filas: list) -> int:
        """Inserta o actualiza filas. Devuelve cuántas se escribieron.

        `merge-duplicates` hace el upsert contra la clave (id, perfil), así que
        un proceso ya conocido se refresca en vez de duplicarse.
        """
        if not self.activo:
            log.info("Supabase sin configurar; se omite la escritura.")
            return 0

        limpias = [self._normalizar(f) for f in filas]
        escritas = 0

        for i in range(0, len(limpias), LOTE):
            lote = limpias[i:i + LOTE]
            if self._post("procesos", lote,
                          "resolution=merge-duplicates,return=minimal"):
                escritas += len(lote)
            else:
                break

        if escritas:
            log.info("Escritas %s filas en Supabase", escritas)
        return escritas

    def log_barrido(self, perfil: str, dataset: str, descargados: int,
                    archivadas: int, alertables: int, nuevas: int) -> bool:
        if not self.activo:
            return False
        return self._post("barridos", [{
            "perfil": perfil, "dataset": dataset, "descargados": descargados,
            "archivadas": archivadas, "alertables": alertables, "nuevas": nuevas,
        }], "return=minimal")

    @staticmethod
    def _normalizar(fila: dict) -> dict:
        """Deja sólo las columnas del esquema, con los tipos que Postgres espera."""
        out = {}
        for col in COLUMNAS:
            valor = fila.get(col)
            if col == "raw" and isinstance(valor, str):
                # En SQLite se guarda serializado; jsonb lo quiere como objeto.
                try:
                    valor = json.loads(valor)
                except (json.JSONDecodeError, TypeError):
                    valor = None
            elif col in ("fecha_cierre", "fecha_publicacion") and not valor:
                # Cadena vacía no es un timestamp válido para Postgres.
                valor = None
            out[col] = valor
        return out
