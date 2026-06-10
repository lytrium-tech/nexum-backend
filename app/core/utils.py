import unicodedata


def normalize_name(text: str | None) -> str | None:
    if not text:
        return text
    # Remove leading/trailing spaces and multiple spaces
    text = " ".join(text.split())
    # Lowercase
    text = text.lower()
    # Remove accents
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return text


def clean_presentation_name(text: str | None) -> str | None:
    if not text:
        return text
    return " ".join(text.split())
