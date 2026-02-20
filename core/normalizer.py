"""Normalisation avancée du texte pour les cellules CSV.

Fonctionnalités :
- Casse uniforme (UPPER, lower, Title Case) par colonne
- Suppression des accents (unicodedata)
- Nettoyage des caractères invisibles (zero-width, soft hyphen, BOM)
- Trim des guillemets parasites
- Uniformisation des apostrophes et guillemets typographiques
"""

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)


class CaseMode(Enum):
    """Modes de transformation de casse."""
    NONE = auto()
    UPPER = auto()
    LOWER = auto()
    TITLE = auto()
    SENTENCE = auto()  # Première lettre majuscule seulement


@dataclass
class NormalizationConfig:
    """Configuration de normalisation par colonne."""
    column_index: int
    case_mode: CaseMode = CaseMode.NONE
    strip_accents: bool = False
    clean_invisible: bool = True
    normalize_quotes: bool = True
    trim_quotes: bool = True
    collapse_whitespace: bool = True

    def has_transforms(self) -> bool:
        """True si au moins une transformation est active."""
        return (
            self.case_mode != CaseMode.NONE
            or self.strip_accents
            or self.clean_invisible
            or self.normalize_quotes
            or self.trim_quotes
            or self.collapse_whitespace
        )


@dataclass
class NormalizationResult:
    """Résultat de la normalisation."""
    total_cells: int = 0
    modified_cells: int = 0
    changes: list[tuple[int, int, str, str]] = field(default_factory=list)  # (line, col, before, after)

    @property
    def modification_rate(self) -> float:
        if self.total_cells == 0:
            return 0.0
        return self.modified_cells / self.total_cells * 100


# ── Caractères invisibles à nettoyer ──
INVISIBLE_CHARS = re.compile(
    '[\u200b\u200c\u200d\u200e\u200f'   # Zero-width
    '\u00ad'                              # Soft hyphen
    '\ufeff'                              # BOM / zero-width no-break space
    '\u2060\u2061\u2062\u2063\u2064'      # Word joiner etc.
    '\u180e'                              # Mongolian vowel separator
    '\u2028\u2029'                        # Line/paragraph separator
    '\ufff9\ufffa\ufffb]'                 # Interlinear annotation
)

# ── Guillemets et apostrophes à normaliser ──
FANCY_QUOTES = {
    '\u2018': "'",   # Left single quotation mark '
    '\u2019': "'",   # Right single quotation mark '
    '\u201a': "'",   # Single low-9 quotation mark ‚
    '\u201b': "'",   # Single high-reversed-9 quotation mark ‛
    '\u2032': "'",   # Prime ′
    '\u2035': "'",   # Reversed prime ‵
    '\u201c': '"',   # Left double quotation mark "
    '\u201d': '"',   # Right double quotation mark "
    '\u201e': '"',   # Double low-9 quotation mark „
    '\u201f': '"',   # Double high-reversed-9 quotation mark ‟
    '\u2033': '"',   # Double prime ″
    '\u2036': '"',   # Reversed double prime ‶
    '\u00ab': '"',   # Left-pointing double angle «
    '\u00bb': '"',   # Right-pointing double angle »
    '\u2039': "'",   # Single left-pointing angle ‹
    '\u203a': "'",   # Single right-pointing angle ›
}

# Non-breaking spaces et espaces exotiques
EXOTIC_SPACES = re.compile(
    '[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]'
)

# Guillemets en début/fin à trimmer
SURROUNDING_QUOTES = re.compile(r'^["\'](.+)["\']$', re.DOTALL)


def strip_accents(text: str) -> str:
    """Supprime les accents d'un texte en utilisant unicodedata.

    Args:
        text: Texte source.

    Returns:
        Texte sans accents (é→e, ü→u, ç→c, etc.)
    """
    nfkd = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in nfkd if unicodedata.category(c) != 'Mn')


def clean_invisible_chars(text: str) -> str:
    """Supprime les caractères invisibles (zero-width, soft hyphen, BOM)."""
    return INVISIBLE_CHARS.sub('', text)


def normalize_quotes(text: str) -> str:
    """Remplace les guillemets typographiques par des guillemets droits."""
    for fancy, simple in FANCY_QUOTES.items():
        if fancy in text:
            text = text.replace(fancy, simple)
    return text


def trim_surrounding_quotes(text: str) -> str:
    """Supprime les guillemets englobants parasites."""
    m = SURROUNDING_QUOTES.match(text)
    if m:
        return m.group(1)
    return text


def collapse_whitespace(text: str) -> str:
    """Normalise les espaces : multiples et exotiques → espace simple."""
    text = EXOTIC_SPACES.sub(' ', text)
    return re.sub(r' {2,}', ' ', text).strip()


def apply_case(text: str, mode: CaseMode) -> str:
    """Applique une transformation de casse."""
    if mode == CaseMode.UPPER:
        return text.upper()
    elif mode == CaseMode.LOWER:
        return text.lower()
    elif mode == CaseMode.TITLE:
        return text.title()
    elif mode == CaseMode.SENTENCE:
        return text[0].upper() + text[1:].lower() if text else text
    return text


def normalize_cell(value: str, config: NormalizationConfig) -> str:
    """Applique toutes les normalisations configurées à une cellule.

    Args:
        value: Valeur brute de la cellule.
        config: Configuration de normalisation.

    Returns:
        Valeur normalisée.
    """
    if not value or not value.strip():
        return value

    result = value

    # Ordre : invisible → quotes → whitespace → accents → casse
    if config.clean_invisible:
        result = clean_invisible_chars(result)

    if config.normalize_quotes:
        result = normalize_quotes(result)

    if config.trim_quotes:
        result = trim_surrounding_quotes(result)

    if config.collapse_whitespace:
        result = collapse_whitespace(result)

    if config.strip_accents:
        result = strip_accents(result)

    if config.case_mode != CaseMode.NONE:
        result = apply_case(result, config.case_mode)

    return result


def normalize_rows(
    rows: list[list[str]],
    configs: list[NormalizationConfig],
    start_line: int = 2,
    max_changes: int = 50_000
) -> tuple[list[list[str]], NormalizationResult]:
    """Normalise un batch de lignes selon les configurations par colonne.

    Args:
        rows: Lignes à normaliser.
        configs: Configurations par colonne.
        start_line: Numéro de la première ligne.
        max_changes: Nombre max de changements à tracker.

    Returns:
        Tuple (lignes normalisées, résultat).
    """
    result = NormalizationResult()
    configs_by_col = {c.column_index: c for c in configs if c.has_transforms()}

    if not configs_by_col:
        return rows, result

    normalized = []
    for i, row in enumerate(rows):
        line_num = start_line + i
        new_row = list(row)
        for col_idx, config in configs_by_col.items():
            if col_idx >= len(row):
                continue
            original = row[col_idx]
            normed = normalize_cell(original, config)
            result.total_cells += 1
            if normed != original:
                new_row[col_idx] = normed
                result.modified_cells += 1
                if len(result.changes) < max_changes:
                    result.changes.append((line_num, col_idx, original, normed))
        normalized.append(new_row)

    return normalized, result
