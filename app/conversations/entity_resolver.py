import re
from collections.abc import Callable, Sequence
from typing import Any

# Type variables for generic resolution

class AmbiguousEntityError(Exception):
    """Lanzada cuando la coincidencia parcial devuelve múltiples resultados."""
    def __init__(self, matches: list[Any], message: str = "Ambigüedad detectada"):
        self.matches = matches
        super().__init__(message)

class EntityNotFoundError(Exception):
    """Lanzada cuando no se encuentra ninguna coincidencia."""
    pass

def normalize_string(s: str) -> str:
    """Normaliza un string a minúsculas y elimina tildes/acentos."""
    if not s:
        return ""
    s = s.lower()
    # Mapeo simple de tildes
    replacements = (
        ("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"),
        ("ä", "a"), ("ë", "e"), ("ï", "i"), ("ö", "o"), ("ü", "u"),
        ("ñ", "n")
    )
    for a, b in replacements:
        s = s.replace(a, b)
    # Remover caracteres especiales y múltiples espacios
    s = re.sub(r'[^a-z0-9\s]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def resolve_entity[T](
    query: str | None,
    options: Sequence[T],
    name_extractor: Callable[[T], str],
    allow_missing: bool = False
) -> T | None:
    """
    Resuelve una entidad textual contra una lista de opciones.
    1. Si query es None/vacio y allow_missing=True -> retorna None
    2. Exact match normalizado
    3. Coincidencia parcial única
    4. Múltiples -> AmbiguousEntityError
    5. Cero -> EntityNotFoundError (o None si allow_missing=True pero la query sí existía, 
       no obstante, el requerimiento dice "faltante -> pedir aclaración", entonces si
       query existe y no hace match, lanzamos EntityNotFoundError).
       Si query es None y allow_missing=False -> EntityNotFoundError.
    """
    if not query:
        if allow_missing:
            return None
        raise EntityNotFoundError("Entidad requerida pero no provista.")

    norm_query = normalize_string(query)
    
    # 1. Exact match normalizado
    exact_matches = []
    for opt in options:
        if normalize_string(name_extractor(opt)) == norm_query:
            exact_matches.append(opt)
    
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise AmbiguousEntityError(exact_matches, "Múltiples opciones exactas coinciden.")

    # 2. Coincidencia parcial
    partial_matches = []
    # Tokenizamos la query
    query_tokens = set(norm_query.split())
    for opt in options:
        opt_name = normalize_string(name_extractor(opt))
        # Si la query parcial está contenida en el nombre, o todos los tokens de la query están en el nombre
        if norm_query in opt_name:
            partial_matches.append(opt)
        else:
            opt_tokens = set(opt_name.split())
            if query_tokens.issubset(opt_tokens):
                partial_matches.append(opt)

    # Eliminar duplicados (por si ambas lógicas aplican)
    unique_partial_matches = []
    for p in partial_matches:
        if p not in unique_partial_matches:
            unique_partial_matches.append(p)

    if len(unique_partial_matches) == 1:
        return unique_partial_matches[0]
    if len(unique_partial_matches) > 1:
        raise AmbiguousEntityError(unique_partial_matches, "Múltiples opciones coinciden parcialmente.")
    
    raise EntityNotFoundError(f"No se encontró ninguna coincidencia para '{query}'.")

