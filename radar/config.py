"""Carga de perfiles de búsqueda.

Cada archivo en `perfiles/` es un radar independiente: sus palabras clave, su
umbral y sus destinatarios. El motor no sabe nada del sector — todo lo define
el YAML, así que agregar un cliente nuevo es agregar un archivo.
"""

from pathlib import Path

import yaml

PERFILES_DIR = Path(__file__).resolve().parent.parent / "perfiles"

DEFAULTS = {
    "umbral_score": 35,
    "umbral_alerta": 60,
    "datasets": ["procesos"],
    "max_registros": 50_000,
    "activo": True,
    "dias_atras": 45,
}


def _apply_defaults(criteria: dict, nombre: str) -> dict:
    criteria.setdefault("nombre", nombre)
    criteria.setdefault("geografia", {}).setdefault("departamentos", [])
    criteria.setdefault("keywords", {})
    criteria.setdefault("unspsc", {}).setdefault("familias", [])
    criteria.setdefault("valor", {})
    criteria.setdefault("alertar_a", [])

    for key, value in DEFAULTS.items():
        criteria.setdefault(key, value)

    for bucket in ("criticas", "deseables", "excluyentes"):
        criteria["keywords"].setdefault(bucket, [])

    return criteria


def load_profile(path) -> dict:
    """Carga un perfil individual. El nombre sale del archivo si no se declara."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No se encontro el perfil: {path}")

    with open(path, encoding="utf-8") as handle:
        criteria = yaml.safe_load(handle) or {}

    return _apply_defaults(criteria, path.stem)


def load_profiles(directory=None) -> list:
    """Carga todos los perfiles activos de un directorio, en orden alfabético."""
    directory = Path(directory) if directory else PERFILES_DIR
    if not directory.is_dir():
        raise FileNotFoundError(f"No existe el directorio de perfiles: {directory}")

    perfiles = []
    for path in sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml")):
        perfil = load_profile(path)
        if perfil.get("activo", True):
            perfiles.append(perfil)

    if not perfiles:
        raise ValueError(f"No hay perfiles activos en {directory}")

    return perfiles


def union_departamentos(perfiles: list) -> list:
    """Unión de la geografía de todos los perfiles.

    La descarga es una sola y compartida, así que el filtro del servidor tiene
    que cubrir a todos; después cada perfil recorta lo suyo localmente.
    Si algún perfil no declara geografía, se descarga sin filtro.
    """
    departamentos = []
    for perfil in perfiles:
        deps = perfil.get("geografia", {}).get("departamentos") or []
        if not deps:
            return []
        for dep in deps:
            if dep not in departamentos:
                departamentos.append(dep)
    return departamentos


def union_datasets(perfiles: list) -> list:
    datasets = []
    for perfil in perfiles:
        for dataset in perfil.get("datasets", []):
            if dataset not in datasets:
                datasets.append(dataset)
    return datasets


def max_registros(perfiles: list) -> int:
    return max((p.get("max_registros", 50_000) for p in perfiles), default=50_000)


def union_dias_atras(perfiles: list) -> int | None:
    """Ventana de fechas más amplia entre los perfiles.

    La descarga es compartida, así que tiene que cubrir al perfil que mire más
    atrás; cada uno recorta lo suyo después. Si alguno no declara ventana, se
    descarga sin filtro de fecha.
    """
    ventanas = []
    for perfil in perfiles:
        dias = perfil.get("dias_atras")
        if not dias:
            return None
        ventanas.append(int(dias))
    return max(ventanas) if ventanas else None
