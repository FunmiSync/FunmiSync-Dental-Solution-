"""Blind prefix index: SQL can match names without storing their plaintext."""
from app.security.encryption import hash_lookup


def normalize_name(value):
    return " ".join((value or "").casefold().split())


def name_index(value):
    name = normalize_name(value)[:255]
    terms = {name[:length] for length in range(2, len(name) + 1)}
    for word in name.split():
        terms.update(word[:length] for length in range(2, len(word) + 1))
    return "|" + "|".join(sorted(hash_lookup(term) for term in terms)) + "|" if terms else None


def name_search_token(value):
    term = normalize_name(value)
    return "|" + hash_lookup(term) + "|" if 2 <= len(term) <= 255 else None
