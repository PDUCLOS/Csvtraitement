"""Règles de validation custom par colonne.

Permet de définir des contraintes spécifiques :
- Plage de valeurs (min/max)
- Regex pattern
- Liste de valeurs autorisées
- Non-null obligatoire
- Longueur min/max pour les textes
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional, Any

from utils.patterns import CellType, detect_cell_type, normalize_decimal, normalize_integer

logger = logging.getLogger(__name__)


@dataclass
class ValidationRule:
    """Règle de validation pour une colonne."""
    column_index: int
    column_name: str = ""
    required: bool = False                      # Cellule non vide obligatoire
    allowed_types: list[CellType] = field(default_factory=list)  # Types acceptés
    min_value: Optional[float] = None           # Valeur numérique minimum
    max_value: Optional[float] = None           # Valeur numérique maximum
    regex_pattern: Optional[str] = None         # Regex que la valeur doit matcher
    allowed_values: Optional[set[str]] = None   # Liste blanche de valeurs
    min_length: Optional[int] = None            # Longueur minimum (texte)
    max_length: Optional[int] = None            # Longueur maximum (texte)
    custom_message: str = ""                    # Message d'erreur personnalisé

    def __post_init__(self):
        self._compiled_regex = None
        if self.regex_pattern:
            try:
                self._compiled_regex = re.compile(self.regex_pattern)
            except re.error as e:
                logger.warning(f"Regex invalide pour col {self.column_index}: {e}")


@dataclass
class ValidationError:
    """Erreur de validation sur une cellule."""
    line_number: int
    column_index: int
    column_name: str
    value: str
    rule_violated: str
    message: str


@dataclass
class ValidationResult:
    """Résultat de la validation."""
    total_checked: int = 0
    total_errors: int = 0
    errors: list[ValidationError] = field(default_factory=list)
    errors_by_column: dict[int, int] = field(default_factory=dict)
    errors_by_rule: dict[str, int] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return self.total_errors == 0

    @property
    def error_rate(self) -> float:
        if self.total_checked == 0:
            return 0.0
        return self.total_errors / self.total_checked


def validate_cell(
    value: str,
    rule: ValidationRule,
    line_number: int
) -> Optional[ValidationError]:
    """Valide une cellule selon une règle.

    Args:
        value: Valeur de la cellule.
        rule: Règle de validation.
        line_number: Numéro de ligne.

    Returns:
        ValidationError si violation, None sinon.
    """
    stripped = value.strip()
    cell_type = detect_cell_type(stripped)

    # Required check
    if rule.required and cell_type in (CellType.EMPTY, CellType.MASKED_EMPTY):
        return ValidationError(
            line_number=line_number,
            column_index=rule.column_index,
            column_name=rule.column_name,
            value=value,
            rule_violated='required',
            message=rule.custom_message or f"Valeur requise en colonne {rule.column_name}"
        )

    # Skip other checks if empty
    if cell_type in (CellType.EMPTY, CellType.MASKED_EMPTY):
        return None

    # Type check
    if rule.allowed_types and cell_type not in rule.allowed_types:
        return ValidationError(
            line_number=line_number,
            column_index=rule.column_index,
            column_name=rule.column_name,
            value=value,
            rule_violated='type',
            message=f"Type {cell_type.name} non autorisé (attendu: {[t.name for t in rule.allowed_types]})"
        )

    # Numeric range check
    if rule.min_value is not None or rule.max_value is not None:
        num = normalize_decimal(stripped)
        if num is None:
            num_int = normalize_integer(stripped)
            if num_int is not None:
                num = float(num_int)

        if num is not None:
            if rule.min_value is not None and num < rule.min_value:
                return ValidationError(
                    line_number=line_number,
                    column_index=rule.column_index,
                    column_name=rule.column_name,
                    value=value,
                    rule_violated='min_value',
                    message=f"Valeur {num} < minimum {rule.min_value}"
                )
            if rule.max_value is not None and num > rule.max_value:
                return ValidationError(
                    line_number=line_number,
                    column_index=rule.column_index,
                    column_name=rule.column_name,
                    value=value,
                    rule_violated='max_value',
                    message=f"Valeur {num} > maximum {rule.max_value}"
                )

    # Regex check
    if rule._compiled_regex and not rule._compiled_regex.match(stripped):
        return ValidationError(
            line_number=line_number,
            column_index=rule.column_index,
            column_name=rule.column_name,
            value=value,
            rule_violated='regex',
            message=f"Ne correspond pas au pattern '{rule.regex_pattern}'"
        )

    # Allowed values check
    if rule.allowed_values is not None and stripped not in rule.allowed_values:
        return ValidationError(
            line_number=line_number,
            column_index=rule.column_index,
            column_name=rule.column_name,
            value=value,
            rule_violated='allowed_values',
            message=f"Valeur '{stripped}' non autorisée"
        )

    # Length check
    if rule.min_length is not None and len(stripped) < rule.min_length:
        return ValidationError(
            line_number=line_number,
            column_index=rule.column_index,
            column_name=rule.column_name,
            value=value,
            rule_violated='min_length',
            message=f"Longueur {len(stripped)} < minimum {rule.min_length}"
        )
    if rule.max_length is not None and len(stripped) > rule.max_length:
        return ValidationError(
            line_number=line_number,
            column_index=rule.column_index,
            column_name=rule.column_name,
            value=value,
            rule_violated='max_length',
            message=f"Longueur {len(stripped)} > maximum {rule.max_length}"
        )

    return None


def validate_rows(
    rows: list[list[str]],
    rules: list[ValidationRule],
    start_line: int = 2,
    max_errors: int = 10_000
) -> ValidationResult:
    """Valide un ensemble de lignes selon les règles définies.

    Args:
        rows: Lignes à valider.
        rules: Règles de validation.
        start_line: Numéro de la première ligne.
        max_errors: Nombre maximum d'erreurs à collecter.

    Returns:
        ValidationResult avec les erreurs trouvées.
    """
    result = ValidationResult()
    rules_by_col = {r.column_index: r for r in rules}

    for i, row in enumerate(rows):
        line_num = start_line + i
        result.total_checked += 1

        for col_idx, rule in rules_by_col.items():
            value = row[col_idx] if col_idx < len(row) else ''
            error = validate_cell(value, rule, line_num)

            if error and result.total_errors < max_errors:
                result.errors.append(error)
                result.total_errors += 1
                result.errors_by_column[col_idx] = result.errors_by_column.get(col_idx, 0) + 1
                result.errors_by_rule[error.rule_violated] = (
                    result.errors_by_rule.get(error.rule_violated, 0) + 1
                )

    return result
