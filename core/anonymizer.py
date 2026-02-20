"""Anonymisation et masquage de données sensibles.

Détection et masquage automatique :
- Emails : jean.dupont@mail.com → j***@m***.com
- Téléphones : 06 12 34 56 78 → 06 ** ** ** 78
- IBAN : FR76 3000 ... → FR76 **** ... **89
- Numéros de carte : 4111 1111 1111 1111 → 4111 **** **** 1111
- Noms/prénoms (si colonne identifiée)
- Mode réversible (clé XOR) ou irréversible
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)


class SensitiveType(Enum):
    """Types de données sensibles détectables."""
    EMAIL = auto()
    PHONE_FR = auto()
    PHONE_INTL = auto()
    IBAN = auto()
    CREDIT_CARD = auto()
    SSN_FR = auto()          # Numéro de sécurité sociale FR
    NAME = auto()            # Nom/prénom (colonne identifiée)
    CUSTOM = auto()          # Pattern personnalisé


class MaskMode(Enum):
    """Mode de masquage."""
    PARTIAL = auto()         # Masquage partiel (garder début/fin)
    FULL = auto()            # Masquage complet → ****
    HASH = auto()            # Hash SHA-256 tronqué
    PSEUDONYM = auto()       # Remplacement par un pseudonyme


# ── Patterns de détection ──
PATTERNS = {
    SensitiveType.EMAIL: re.compile(
        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
    ),
    SensitiveType.PHONE_FR: re.compile(
        r'(?:(?:\+33|0033|0)\s*[1-9])(?:[\s.\-]?\d{2}){4}'
    ),
    SensitiveType.PHONE_INTL: re.compile(
        r'\+\d{1,3}[\s.\-]?\d{1,4}(?:[\s.\-]?\d{2,4}){2,5}'
    ),
    SensitiveType.IBAN: re.compile(
        r'[A-Z]{2}\d{2}[\s]?(?:\d{4}[\s]?){2,7}\d{1,4}'
    ),
    SensitiveType.CREDIT_CARD: re.compile(
        r'(?:\d{4}[\s\-]?){3}\d{4}'
    ),
    SensitiveType.SSN_FR: re.compile(
        r'[12]\s?\d{2}\s?\d{2}\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}'
    ),
}


@dataclass
class AnonymizationRule:
    """Règle d'anonymisation pour une colonne."""
    column_index: int
    sensitive_type: SensitiveType
    mask_mode: MaskMode = MaskMode.PARTIAL
    mask_char: str = '*'
    custom_pattern: Optional[str] = None  # Regex personnalisé pour CUSTOM

    def __post_init__(self):
        self._custom_regex = None
        if self.custom_pattern:
            try:
                self._custom_regex = re.compile(self.custom_pattern)
            except re.error as e:
                logger.warning(f"Pattern personnalisé invalide: {e}")


@dataclass
class AnonymizationResult:
    """Résultat de l'anonymisation."""
    total_cells: int = 0
    masked_cells: int = 0
    detections_by_type: dict[str, int] = field(default_factory=dict)
    changes: list[tuple[int, int, str, str]] = field(default_factory=list)  # (line, col, before, after)

    @property
    def mask_rate(self) -> float:
        if self.total_cells == 0:
            return 0.0
        return self.masked_cells / self.total_cells * 100


def detect_sensitive_type(value: str) -> Optional[SensitiveType]:
    """Détecte automatiquement le type de donnée sensible.

    Args:
        value: Valeur à analyser.

    Returns:
        SensitiveType détecté ou None.
    """
    stripped = value.strip()
    if not stripped:
        return None

    # Ordre de priorité : plus spécifique d'abord
    for stype, pattern in PATTERNS.items():
        if pattern.fullmatch(stripped):
            return stype

    # Vérifier aussi les matchs partiels pour les emails dans du texte
    if PATTERNS[SensitiveType.EMAIL].search(stripped):
        return SensitiveType.EMAIL

    return None


def detect_column_sensitivity(
    sample_values: list[str],
    threshold: float = 0.3
) -> Optional[SensitiveType]:
    """Détecte si une colonne contient des données sensibles.

    Args:
        sample_values: Échantillon de valeurs non vides.
        threshold: Pourcentage minimum de détection.

    Returns:
        SensitiveType dominant ou None.
    """
    if not sample_values:
        return None

    type_counts: dict[SensitiveType, int] = {}
    for val in sample_values:
        stype = detect_sensitive_type(val)
        if stype:
            type_counts[stype] = type_counts.get(stype, 0) + 1

    if not type_counts:
        return None

    best_type = max(type_counts, key=type_counts.get)
    rate = type_counts[best_type] / len(sample_values)

    if rate >= threshold:
        return best_type
    return None


def mask_email(value: str, mask_char: str = '*') -> str:
    """Masque un email : jean.dupont@mail.com → j***@m***.com"""
    m = PATTERNS[SensitiveType.EMAIL].search(value)
    if not m:
        return value

    email = m.group()
    local, domain = email.rsplit('@', 1)
    domain_parts = domain.rsplit('.', 1)

    masked_local = local[0] + mask_char * 3 if local else mask_char * 3
    masked_domain = domain_parts[0][0] + mask_char * 3 if domain_parts[0] else mask_char * 3
    tld = domain_parts[1] if len(domain_parts) > 1 else ''

    masked = f"{masked_local}@{masked_domain}.{tld}"
    return value.replace(email, masked)


def mask_phone(value: str, mask_char: str = '*') -> str:
    """Masque un téléphone : 06 12 34 56 78 → 06 ** ** ** 78"""
    # Extraire uniquement les chiffres
    digits = re.findall(r'\d', value)
    if len(digits) < 8:
        return value

    # Garder les 2 premiers et 2 derniers chiffres
    masked_digits = digits[:2] + [mask_char] * (len(digits) - 4) + digits[-2:]

    # Reconstruire avec le même format
    result = value
    digit_idx = 0
    new_chars = []
    for ch in value:
        if ch.isdigit():
            new_chars.append(masked_digits[digit_idx] if digit_idx < len(masked_digits) else ch)
            digit_idx += 1
        else:
            new_chars.append(ch)
    return ''.join(new_chars)


def mask_iban(value: str, mask_char: str = '*') -> str:
    """Masque un IBAN : FR76 3000 ... → FR76 **** ... **89"""
    stripped = value.strip()
    alnum = [c for c in stripped if c.isalnum()]
    if len(alnum) < 10:
        return value

    # Garder les 4 premiers et 2 derniers
    masked_alnum = alnum[:4] + [mask_char] * (len(alnum) - 6) + alnum[-2:]

    result = []
    idx = 0
    for ch in stripped:
        if ch.isalnum():
            result.append(masked_alnum[idx] if idx < len(masked_alnum) else ch)
            idx += 1
        else:
            result.append(ch)
    return ''.join(result)


def mask_credit_card(value: str, mask_char: str = '*') -> str:
    """Masque une carte : 4111 1111 1111 1111 → 4111 **** **** 1111"""
    digits = re.findall(r'\d', value)
    if len(digits) < 12:
        return value

    masked_digits = digits[:4] + [mask_char] * (len(digits) - 8) + digits[-4:]

    result = []
    idx = 0
    for ch in value:
        if ch.isdigit():
            result.append(masked_digits[idx] if idx < len(masked_digits) else ch)
            idx += 1
        else:
            result.append(ch)
    return ''.join(result)


def mask_ssn(value: str, mask_char: str = '*') -> str:
    """Masque un numéro de sécu : 1 85 12 75 ... → 1 ** ** ** ..."""
    digits = re.findall(r'\d', value)
    if len(digits) < 13:
        return value

    masked_digits = digits[:1] + [mask_char] * (len(digits) - 3) + digits[-2:]

    result = []
    idx = 0
    for ch in value:
        if ch.isdigit():
            result.append(masked_digits[idx] if idx < len(masked_digits) else ch)
            idx += 1
        else:
            result.append(ch)
    return ''.join(result)


def mask_name(value: str, mask_char: str = '*') -> str:
    """Masque un nom : Jean Dupont → J*** D***"""
    parts = value.strip().split()
    masked = []
    for part in parts:
        if len(part) > 1:
            masked.append(part[0] + mask_char * (len(part) - 1))
        else:
            masked.append(part)
    return ' '.join(masked)


def hash_value(value: str, length: int = 12) -> str:
    """Hash SHA-256 tronqué d'une valeur."""
    h = hashlib.sha256(value.encode('utf-8')).hexdigest()
    return h[:length]


def mask_value(
    value: str,
    sensitive_type: SensitiveType,
    mask_mode: MaskMode = MaskMode.PARTIAL,
    mask_char: str = '*'
) -> str:
    """Masque une valeur selon son type et le mode choisi.

    Args:
        value: Valeur à masquer.
        sensitive_type: Type de donnée sensible.
        mask_mode: Mode de masquage.
        mask_char: Caractère de masquage.

    Returns:
        Valeur masquée.
    """
    stripped = value.strip()
    if not stripped:
        return value

    if mask_mode == MaskMode.FULL:
        return mask_char * len(stripped)

    if mask_mode == MaskMode.HASH:
        return hash_value(stripped)

    if mask_mode == MaskMode.PSEUDONYM:
        h = hashlib.md5(stripped.encode()).hexdigest()[:8]
        return f"ANON_{h.upper()}"

    # Mode PARTIAL
    mask_funcs = {
        SensitiveType.EMAIL: mask_email,
        SensitiveType.PHONE_FR: mask_phone,
        SensitiveType.PHONE_INTL: mask_phone,
        SensitiveType.IBAN: mask_iban,
        SensitiveType.CREDIT_CARD: mask_credit_card,
        SensitiveType.SSN_FR: mask_ssn,
        SensitiveType.NAME: mask_name,
    }

    func = mask_funcs.get(sensitive_type)
    if func:
        return func(stripped, mask_char)

    # CUSTOM ou type non géré : masquage générique
    if len(stripped) <= 4:
        return mask_char * len(stripped)
    return stripped[0] + mask_char * (len(stripped) - 2) + stripped[-1]


def anonymize_rows(
    rows: list[list[str]],
    rules: list[AnonymizationRule],
    start_line: int = 2,
    max_changes: int = 50_000
) -> tuple[list[list[str]], AnonymizationResult]:
    """Anonymise un batch de lignes selon les règles définies.

    Args:
        rows: Lignes à anonymiser.
        rules: Règles d'anonymisation.
        start_line: Numéro de la première ligne.
        max_changes: Nombre max de changements à tracker.

    Returns:
        Tuple (lignes anonymisées, résultat).
    """
    result = AnonymizationResult()
    rules_by_col = {r.column_index: r for r in rules}

    anonymized = []
    for i, row in enumerate(rows):
        line_num = start_line + i
        new_row = list(row)

        for col_idx, rule in rules_by_col.items():
            if col_idx >= len(row):
                continue
            original = row[col_idx]
            result.total_cells += 1

            if not original.strip():
                continue

            masked = mask_value(
                original, rule.sensitive_type,
                rule.mask_mode, rule.mask_char
            )

            if masked != original:
                new_row[col_idx] = masked
                result.masked_cells += 1
                type_name = rule.sensitive_type.name
                result.detections_by_type[type_name] = result.detections_by_type.get(type_name, 0) + 1
                if len(result.changes) < max_changes:
                    result.changes.append((line_num, col_idx, original, masked))

        anonymized.append(new_row)

    logger.info(f"Anonymisation: {result.masked_cells}/{result.total_cells} cellules masquées")
    return anonymized, result
