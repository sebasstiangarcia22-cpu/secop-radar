"""Logical field names mapped onto whatever the API actually returns.

The SECOP open-data columns get renamed between dataset refreshes, and the
published documentation lags behind the live schema. Rather than hardcode column
names and break silently, every logical field lists candidate API names and we
resolve against the keys really present in a sample record.

Run `scripts/discover_schema.py` to print the live schema and see which logical
fields resolved; add any missing alias here.
"""

# Ordered by preference: the first candidate present in the record wins.
FIELD_ALIASES = {
    "id": [
        "id_del_proceso",
        "referencia_del_proceso",
        "id_del_portafolio",
        "numero_del_contrato",
        "id_contrato",
    ],
    "entidad": [
        "entidad",
        "nombre_entidad",
        "nombre_de_la_entidad",
        "razon_social_entidad",
    ],
    "objeto": [
        "descripci_n_del_procedimiento",
        "descripcion_del_procedimiento",
        "nombre_del_procedimiento",
        "objeto_del_contrato",
        "descripcion_del_proceso",
        "objeto_a_contratar",
        "detalle_del_objeto_a_contratar",
    ],
    "departamento": [
        "departamento_entidad",
        "departamento",
        "dpto_y_muni_contrato",
        "departamento_de_la_entidad",
    ],
    "ciudad": [
        "ciudad_entidad",
        "ciudad",
        "municipio_entidad",
    ],
    "valor": [
        "precio_base",
        "valor_total_adjudicacion",
        "valor_del_contrato",
        "cuantia_proceso",
        "valor_estimado",
    ],
    "modalidad": [
        "modalidad_de_contratacion",
        "modalidad_de_contrataci_n",
        "tipo_de_proceso",
    ],
    "estado": [
        "estado_del_procedimiento",
        "estado_de_apertura_del_proceso",
        "estado_contrato",
        "fase",
    ],
    "fecha_publicacion": [
        "fecha_de_publicacion_del",
        "fecha_de_publicacion",
        "fecha_de_publicacion_del_proceso",
        "fecha_de_firma",
    ],
    "fecha_cierre": [
        "fecha_de_recepcion_de",
        "fecha_de_cierre",
        "fecha_limite_de_presentacion",
        "fecha_de_fin_del_contrato",
    ],
    "unspsc": [
        "codigo_principal_de_categoria",
        "codigo_de_categoria_principal",
        "codigo_unspsc",
    ],
    "url": [
        "urlproceso",
        "url_del_proceso",
        "urlproceso_url",
        "enlace_proceso",
    ],
}


def resolve_schema(sample: dict) -> dict:
    """Map logical names to the concrete keys present in `sample`.

    Missing logical fields are simply absent from the result; callers degrade
    gracefully rather than raising, because a record with no closing date is
    still a record worth alerting on.
    """
    present = set(sample.keys())
    resolved = {}
    for logical, candidates in FIELD_ALIASES.items():
        for candidate in candidates:
            if candidate in present:
                resolved[logical] = candidate
                break
    return resolved


def get(record: dict, schema: dict, logical: str, default=None):
    """Read a logical field out of a raw record using a resolved schema."""
    key = schema.get(logical)
    if key is None:
        return default
    value = record.get(key, default)
    # Socrata wraps some columns as {'url': ...} objects.
    if isinstance(value, dict):
        return value.get("url") or value.get("description") or default
    return value
