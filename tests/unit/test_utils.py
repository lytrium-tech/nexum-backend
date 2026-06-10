from app.core.utils import clean_presentation_name, normalize_name


def test_normalize_name():
    assert normalize_name("  HólA  MuNdó  ") == "hola mundo"
    assert normalize_name(None) is None
    assert normalize_name("café") == "cafe"


def test_clean_presentation_name():
    assert clean_presentation_name("  HólA  MuNdó  ") == "HólA MuNdó"
    assert clean_presentation_name(None) is None
