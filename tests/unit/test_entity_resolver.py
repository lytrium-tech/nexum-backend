from dataclasses import dataclass

import pytest

from app.conversations.entity_resolver import (
    AmbiguousEntityError,
    EntityNotFoundError,
    normalize_string,
    resolve_entity,
)


@dataclass
class DummyEntity:
    id: int
    name: str


def test_normalize_string():
    assert normalize_string("Nequi") == "nequi"
    assert normalize_string("RappiCard") == "rappicard"
    assert normalize_string("Cómida rápida") == "comida rapida"
    assert normalize_string("  Dos   espacios ") == "dos espacios"
    assert normalize_string("123!!!") == "123"
    assert normalize_string("") == ""


def test_resolve_entity_exact_match():
    options = [
        DummyEntity(1, "Nequi"),
        DummyEntity(2, "Bancolombia"),
    ]
    res = resolve_entity("nequi", options, lambda x: x.name)
    assert res.id == 1


def test_resolve_entity_exact_match_accents():
    options = [
        DummyEntity(1, "Camión"),
        DummyEntity(2, "Carro"),
    ]
    res = resolve_entity("camion", options, lambda x: x.name)
    assert res.id == 1


def test_resolve_entity_partial_unique():
    options = [
        DummyEntity(1, "Cuenta de Ahorros Bancolombia"),
        DummyEntity(2, "Cuenta Nequi"),
    ]
    res = resolve_entity("ahorros", options, lambda x: x.name)
    assert res.id == 1

    res2 = resolve_entity("nequi", options, lambda x: x.name)
    assert res2.id == 2


def test_resolve_entity_ambiguous():
    options = [
        DummyEntity(1, "Tarjeta Nu"),
        DummyEntity(2, "Tarjeta Rappi"),
    ]
    with pytest.raises(AmbiguousEntityError) as exc:
        resolve_entity("tarjeta", options, lambda x: x.name)
    
    assert len(exc.value.matches) == 2


def test_resolve_entity_not_found():
    options = [
        DummyEntity(1, "Efectivo"),
    ]
    with pytest.raises(EntityNotFoundError):
        resolve_entity("tarjeta", options, lambda x: x.name)


def test_resolve_entity_missing_allowed():
    options = [DummyEntity(1, "Efectivo")]
    res = resolve_entity(None, options, lambda x: x.name, allow_missing=True)
    assert res is None


def test_resolve_entity_missing_not_allowed():
    options = [DummyEntity(1, "Efectivo")]
    with pytest.raises(EntityNotFoundError):
        resolve_entity(None, options, lambda x: x.name, allow_missing=False)
