"""Regex patterns et fonctions de détection de types pour les cellules CSV.

Gère les formats FR et US pour les dates et nombres décimaux.
Détecte les valeurs masquées (N/A, NULL, #REF!, etc.)
"""

import re
from enum import Enum, auto
from typing import Optional


class CellType(Enum):
    """Types de cellules détectés."""
    DATE = auto()
    INTEGER = auto()
    DECIMAL = auto()
    TEXT_SHORT = auto()
    TEXT_LONG = auto()
    EMPTY = auto()
    DATE_INVALID = auto()
    MASKED_EMPTY = auto()
    BOOLEAN = auto()


# ── Patterns de dates ──
DATE_DMY = re.compile(
    r'^(0[1-9]|[12]\d|3[01])[./\-](0[1-9]|1[0-2])[./\-](\d{4})$'
)
DATE_YMD = re.compile(
    r'^(\d{4})[./\-](0[1-9]|1[0-2])[./\-](0[1-9]|[12]\d|3[01])$'
)
DATE_INVALID = re.compile(r'^0{1,2}[./\-]0{1,2}[./\-]0{4}$')
DATETIME_DMY = re.compile(
    r'^(0[1-9]|[12]\d|3[01])[./\-](0[1-9]|1[0-2])[./\-](\d{4})\s+(\d{2}):(\d{2})(:\d{2})?$'
)
DATETIME_YMD = re.compile(
    r'^(\d{4})[./\-](0[1-9]|1[0-2])[./\-](0[1-9]|[12]\d|3[01])\s+(\d{2}):(\d{2})(:\d{2})?$'
)

# ── Patterns de nombres ──
INTEGER_SIMPLE = re.compile(r'^[+-]?\d+$')
DECIMAL_FR = re.compile(r'^[+-]?(\d{1,3}(\.\d{3})*),(\d+)$')
DECIMAL_FR_SIMPLE = re.compile(r'^[+-]?\d+,(\d+)$')
DECIMAL_US = re.compile(r'^[+-]?(\d{1,3}(,\d{3})*)\.(\d+)$')
DECIMAL_US_SIMPLE = re.compile(r'^[+-]?\d+\.(\d+)$')
DECIMAL_PARENS = re.compile(r'^\((.+)\)$')

# ── Valeurs masquées ──
MASKED_EMPTY_VALUES = frozenset({
    'n/a', 'na', 'n.a.', 'n.a', '#n/a', '#na',
    'null', 'nil', 'none', 'nan',
    '-', '--', '---', '.',
    '#ref!', '#val!', '#div/0!', '#name?', '#num!', '#null!',
    'undefined', 'missing', 'vide', 'inconnu', 'unknown',
    '?', '??', 'tbd', 'tba',
})

# ── Booléens ──
BOOLEAN_TRUE = frozenset({'true', 'vrai', 'oui', 'yes', 'y', 'o'})
BOOLEAN_FALSE = frozenset({'false', 'faux', 'non', 'no', 'n'})
BOOLEAN_ALL = BOOLEAN_TRUE | BOOLEAN_FALSE


def detect_cell_type(value: str) -> CellType:
    """Détecte le type d'une cellule CSV."""
    stripped = value.strip()
    if not stripped:
        return CellType.EMPTY

    lower = stripped.lower()
    if lower in MASKED_EMPTY_VALUES:
        return CellType.MASKED_EMPTY
    if DATE_INVALID.match(stripped):
        return CellType.DATE_INVALID
    if DATE_DMY.match(stripped) or DATE_YMD.match(stripped):
        return CellType.DATE
    if DATETIME_DMY.match(stripped) or DATETIME_YMD.match(stripped):
        return CellType.DATE
    if lower in BOOLEAN_ALL:
        return CellType.BOOLEAN
    if INTEGER_SIMPLE.match(stripped):
        return CellType.INTEGER

    paren_match = DECIMAL_PARENS.match(stripped)
    if paren_match:
        inner = paren_match.group(1)
        if (DECIMAL_FR.match(inner) or DECIMAL_FR_SIMPLE.match(inner) or
                DECIMAL_US.match(inner) or DECIMAL_US_SIMPLE.match(inner) or
                INTEGER_SIMPLE.match(inner)):
            return CellType.DECIMAL

    if DECIMAL_FR.match(stripped) or DECIMAL_FR_SIMPLE.match(stripped):
        return CellType.DECIMAL
    if DECIMAL_US.match(stripped) or DECIMAL_US_SIMPLE.match(stripped):
        return CellType.DECIMAL

    if len(stripped) >= 50:
        return CellType.TEXT_LONG
    return CellType.TEXT_SHORT


def detect_decimal_format(value: str) -> Optional[str]:
    """Détecte si un nombre décimal est au format FR ou US."""
    stripped = value.strip()
    paren_match = DECIMAL_PARENS.match(stripped)
    if paren_match:
        stripped = paren_match.group(1)
    if DECIMAL_FR.match(stripped) or DECIMAL_FR_SIMPLE.match(stripped):
        return 'FR'
    if DECIMAL_US.match(stripped) or DECIMAL_US_SIMPLE.match(stripped):
        return 'US'
    return None


def normalize_decimal(value: str) -> Optional[float]:
    """Convertit une valeur décimale (FR ou US) en float Python.

    Gère les parenthèses comptables (négatif).
    """
    stripped = value.strip()
    is_negative = False
    paren_match = DECIMAL_PARENS.match(stripped)
    if paren_match:
        stripped = paren_match.group(1)
        is_negative = True

    fmt = detect_decimal_format(stripped)
    result = None
    if fmt == 'FR':
        cleaned = stripped.replace('.', '').replace(',', '.')
        try:
            result = float(cleaned)
        except ValueError:
            pass
    elif fmt == 'US':
        cleaned = stripped.replace(',', '')
        try:
            result = float(cleaned)
        except ValueError:
            pass
    else:
        try:
            result = float(stripped)
        except ValueError:
            pass

    if result is not None and is_negative:
        result = -abs(result)
    return result


def normalize_integer(value: str) -> Optional[int]:
    """Convertit une valeur entière string en int Python."""
    stripped = value.strip()
    if INTEGER_SIMPLE.match(stripped):
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def parse_date(value: str) -> Optional[str]:
    """Parse une date et retourne au format ISO YYYY-MM-DD."""
    stripped = value.strip()
    if DATE_INVALID.match(stripped):
        return None

    for pattern, groups_order in [
        (DATETIME_DMY, 'dmy'), (DATETIME_YMD, 'ymd'),
        (DATE_DMY, 'dmy'), (DATE_YMD, 'ymd'),
    ]:
        m = pattern.match(stripped)
        if m:
            if groups_order == 'dmy':
                day, month, year = m.group(1), m.group(2), m.group(3)
            else:
                year, month, day = m.group(1), m.group(2), m.group(3)
            return f"{year}-{month}-{day}"
    return None


def normalize_whitespace(value: str) -> str:
    """Normalise les espaces : multiples, insécables, NBSP → espace simple."""
    if not value:
        return value
    return re.sub(r'[\s\xa0\u200b\u00a0]+', ' ', value).strip()


def get_type_category(cell_type: CellType) -> str:
    """Regroupe les types en catégories pour la comparaison de profils."""
    if cell_type in (CellType.DATE, CellType.DATE_INVALID):
        return 'date'
    if cell_type in (CellType.INTEGER, CellType.DECIMAL):
        return 'numeric'
    if cell_type in (CellType.TEXT_SHORT, CellType.TEXT_LONG):
        return 'text'
    if cell_type == CellType.BOOLEAN:
        return 'boolean'
    return 'empty'
