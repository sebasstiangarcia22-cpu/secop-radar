"""Socrata (datos.gov.co) client.

Deliberately thin: paginate, retry, return raw dicts. All filtering happens
locally in scoring.py — see the README for why we never push keyword search
down to the API.
"""

import logging
import os
import time

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://www.datos.gov.co/resource/{dataset}.json"

DATASETS = {
    "procesos": "p6dx-8zbt",       # SECOP II - Procesos de Contratación
    "contratos": "jbjy-vk9h",      # SECOP II - Contratos Electrónicos
    "secop1": "xvdr-vrge",         # SECOP I
    "paa": "b6m4-qgqv",            # Plan Anual de Adquisiciones
}

PAGE_SIZE = 1000
MAX_RETRIES = 4


class SocrataError(RuntimeError):
    pass


class SocrataClient:
    def __init__(self, app_token: str | None = None, timeout: int = 60):
        self.app_token = app_token or os.environ.get("SOCRATA_APP_TOKEN") or ""
        self.timeout = timeout
        self.session = requests.Session()
        if self.app_token:
            self.session.headers["X-App-Token"] = self.app_token
        else:
            log.warning(
                "Sin SOCRATA_APP_TOKEN: la API aplica un limite de tasa mucho "
                "mas bajo. Registra un token gratuito en datos.gov.co."
            )

    def _request(self, url: str, params: dict) -> list:
        delay = 2
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 429:
                    raise SocrataError("rate limited (429)")
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, SocrataError, ValueError) as exc:
                last_error = exc
                if attempt == MAX_RETRIES - 1:
                    break
                log.warning("Intento %s fallo (%s); reintento en %ss", attempt + 1, exc, delay)
                time.sleep(delay)
                delay *= 2
        raise SocrataError(f"La consulta fallo tras {MAX_RETRIES} intentos: {last_error}")

    def sample(self, dataset: str, limit: int = 1) -> list:
        """One page of records, used for schema discovery."""
        url = BASE_URL.format(dataset=DATASETS.get(dataset, dataset))
        return self._request(url, {"$limit": limit})

    def fetch_all(self, dataset: str, where: str | None = None,
                  order: str | None = None, max_records: int = 50_000) -> list:
        """Page through a dataset, applying only coarse server-side filters.

        `where` should narrow by geography or date only — never by keyword.
        SECOP's own text matching is exactly what we are routing around.
        """
        url = BASE_URL.format(dataset=DATASETS.get(dataset, dataset))
        records, offset = [], 0

        while offset < max_records:
            params = {"$limit": PAGE_SIZE, "$offset": offset}
            if where:
                params["$where"] = where
            if order:
                params["$order"] = order

            page = self._request(url, params)
            if not page:
                break

            records.extend(page)
            log.info("Descargados %s registros de '%s'", len(records), dataset)

            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

        return records


def build_geo_where(schema: dict, departamentos: list) -> str | None:
    """Coarse server-side geography filter.

    Accent-insensitive by construction: we compare on `upper(...)` and match a
    prefix that avoids the accented characters entirely, so 'Valle del Cauca'
    is caught however the entity typed it. Anything this lets through is
    filtered precisely on our side.
    """
    column = schema.get("departamento")
    if not column or not departamentos:
        return None

    clauses = []
    for dep in departamentos:
        # Use the longest accent-free prefix so server-side collation quirks
        # cannot drop a row; exact matching happens locally afterwards.
        safe_prefix = _accent_free_prefix(dep)
        if safe_prefix:
            clauses.append(f"upper({column}) like upper('%{safe_prefix}%')")
    return " OR ".join(clauses) if clauses else None


def _accent_free_prefix(text: str) -> str:
    """Longest leading run of characters that carry no accent."""
    out = []
    for char in text:
        if char.isascii():
            out.append(char)
        else:
            break
    return "".join(out).strip()
