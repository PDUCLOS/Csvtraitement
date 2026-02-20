"""Règles de validation custom par colonne + validation croisée inter-colonnes.

Validation par colonne :
- Plage de valeurs (min/max)
- Regex pattern
- Liste de valeurs autorisées
- Non-null obligatoire
- Longueur min/max pour les textes

Validation croisée inter-colonnes :
- Date début < Date fin
- Vérification arithmétique (HT * TVA = TTC)
- Champ requis conditionnellement
- Exclusion mutuelle
- Cohérence entre colonnes
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


# ════════════════════════════════════════════════════
# Validation croisée inter-colonnes
# ════════════════════════════════════════════════════


class CrossRuleType:
    """Types de règles de validation croisée."""
    DATE_ORDER = 'date_order'              # Date A < Date B
    ARITHMETIC = 'arithmetic'              # A * B ≈ C (avec tolérance)
    CONDITIONAL_REQUIRED = 'conditional'   # Si A rempli → B requis
    MUTUAL_EXCLUSIVE = 'mutual_exclusive'  # A ou B, pas les deux
    CONSISTENCY = 'consistency'            # A implique une valeur de B


@dataclass
class CrossColumnRule:
    """Règle de validation croisée entre colonnes."""
    name: str
    rule_type: str                        # Valeur de CrossRuleType
    columns: list[int]                    # Indices des colonnes impliquées
    column_names: list[str] = field(default_factory=list)

    # Date order: columns[0] < columns[1]
    date_allow_equal: bool = True

    # Arithmetic: columns[0] op columns[1] = columns[2]
    arithmetic_expression: str = ""       # Ex: "{0} * {1}" résultat attendu en columns[-1]
    arithmetic_tolerance: float = 0.01    # Tolérance relative

    # Conditional required
    condition_column: int = -1            # Si cette colonne est non-vide...
    required_columns: list[int] = field(default_factory=list)  # ...celles-ci doivent l'être

    # Consistency: si columns[0] = clé → columns[1] doit valoir la valeur associée
    consistency_map: dict[str, str] = field(default_factory=dict)

    custom_message: str = ""

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'rule_type': self.rule_type,
            'columns': self.columns,
            'column_names': self.column_names,
            'date_allow_equal': self.date_allow_equal,
            'arithmetic_expression': self.arithmetic_expression,
            'arithmetic_tolerance': self.arithmetic_tolerance,
            'condition_column': self.condition_column,
            'required_columns': self.required_columns,
            'consistency_map': self.consistency_map,
            'custom_message': self.custom_message,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'CrossColumnRule':
        return cls(
            name=data['name'],
            rule_type=data['rule_type'],
            columns=data['columns'],
            column_names=data.get('column_names', []),
            date_allow_equal=data.get('date_allow_equal', True),
            arithmetic_expression=data.get('arithmetic_expression', ''),
            arithmetic_tolerance=data.get('arithmetic_tolerance', 0.01),
            condition_column=data.get('condition_column', -1),
            required_columns=data.get('required_columns', []),
            consistency_map=data.get('consistency_map', {}),
            custom_message=data.get('custom_message', ''),
        )


@dataclass
class CrossValidationResult:
    """Résultat de la validation croisée."""
    total_checked: int = 0
    total_errors: int = 0
    errors: list[ValidationError] = field(default_factory=list)
    errors_by_rule: dict[str, int] = field(default_factory=dict)


def _parse_date_for_compare(value: str) -> Optional[str]:
    """Parse une date en format comparable YYYY-MM-DD."""
    from utils.patterns import parse_date
    stripped = value.strip()
    if not stripped:
        return None
    return parse_date(stripped)


def _parse_numeric(value: str) -> Optional[float]:
    """Parse une valeur numérique."""
    stripped = value.strip()
    if not stripped:
        return None
    num = normalize_decimal(stripped)
    if num is None:
        num_int = normalize_integer(stripped)
        if num_int is not None:
            return float(num_int)
    return num


def validate_cross_column(
    row: list[str],
    rule: CrossColumnRule,
    line_number: int
) -> Optional[ValidationError]:
    """Valide une règle croisée sur une ligne."""
    def _get(col: int) -> str:
        return row[col].strip() if col < len(row) else ''

    if rule.rule_type == CrossRuleType.DATE_ORDER:
        if len(rule.columns) >= 2:
            date_a = _parse_date_for_compare(_get(rule.columns[0]))
            date_b = _parse_date_for_compare(_get(rule.columns[1]))
            if date_a and date_b:
                violated = (date_a > date_b) if rule.date_allow_equal else (date_a >= date_b)
                if violated:
                    col_names = rule.column_names or [str(c) for c in rule.columns]
                    return ValidationError(
                        line_number=line_number,
                        column_index=rule.columns[0],
                        column_name=col_names[0],
                        value=f"{_get(rule.columns[0])} > {_get(rule.columns[1])}",
                        rule_violated=f'cross:{rule.name}',
                        message=rule.custom_message or
                                f"{col_names[0]} ({date_a}) postérieure à {col_names[1]} ({date_b})"
                    )

    elif rule.rule_type == CrossRuleType.ARITHMETIC:
        if len(rule.columns) >= 3 and rule.arithmetic_expression:
            values = [_parse_numeric(_get(c)) for c in rule.columns]
            if all(v is not None for v in values):
                try:
                    expr = rule.arithmetic_expression
                    for i, v in enumerate(values[:-1]):
                        expr = expr.replace(f'{{{i}}}', str(v))
                    expected = eval(expr)  # noqa: S307
                    actual = values[-1]
                    tol = abs(expected * rule.arithmetic_tolerance) if expected != 0 else rule.arithmetic_tolerance
                    if abs(expected - actual) > tol:
                        col_names = rule.column_names or [str(c) for c in rule.columns]
                        return ValidationError(
                            line_number=line_number,
                            column_index=rule.columns[-1],
                            column_name=col_names[-1] if col_names else str(rule.columns[-1]),
                            value=f"attendu={expected:.2f}, trouvé={actual:.2f}",
                            rule_violated=f'cross:{rule.name}',
                            message=rule.custom_message or
                                    f"Écart: attendu {expected:.2f}, trouvé {actual:.2f}"
                        )
                except Exception:
                    pass

    elif rule.rule_type == CrossRuleType.CONDITIONAL_REQUIRED:
        cond_val = _get(rule.condition_column)
        if cond_val:
            for req_col in rule.required_columns:
                if not _get(req_col):
                    col_names = rule.column_names or []
                    cond_name = col_names[0] if col_names else str(rule.condition_column)
                    return ValidationError(
                        line_number=line_number,
                        column_index=req_col,
                        column_name=str(req_col),
                        value='(vide)',
                        rule_violated=f'cross:{rule.name}',
                        message=rule.custom_message or
                                f"Requis quand {cond_name} est rempli"
                    )

    elif rule.rule_type == CrossRuleType.MUTUAL_EXCLUSIVE:
        if len(rule.columns) >= 2:
            filled = [c for c in rule.columns if _get(c)]
            if len(filled) > 1:
                col_names = rule.column_names or [str(c) for c in rule.columns]
                return ValidationError(
                    line_number=line_number,
                    column_index=rule.columns[0],
                    column_name=col_names[0] if col_names else str(rule.columns[0]),
                    value=f"{len(filled)} colonnes remplies",
                    rule_violated=f'cross:{rule.name}',
                    message=rule.custom_message or
                            f"Mutuellement exclusives : {', '.join(col_names)}"
                )

    elif rule.rule_type == CrossRuleType.CONSISTENCY:
        if len(rule.columns) >= 2 and rule.consistency_map:
            key_val = _get(rule.columns[0]).lower()
            expected_val = rule.consistency_map.get(key_val)
            if expected_val is not None:
                actual_val = _get(rule.columns[1])
                if actual_val.lower() != expected_val.lower():
                    col_names = rule.column_names or [str(c) for c in rule.columns]
                    return ValidationError(
                        line_number=line_number,
                        column_index=rule.columns[1],
                        column_name=col_names[1] if len(col_names) > 1 else str(rule.columns[1]),
                        value=actual_val,
                        rule_violated=f'cross:{rule.name}',
                        message=rule.custom_message or
                                f"Incohérence: attendu '{expected_val}', trouvé '{actual_val}'"
                    )

    return None


def validate_cross_columns(
    rows: list[list[str]],
    rules: list[CrossColumnRule],
    start_line: int = 2,
    max_errors: int = 10_000
) -> CrossValidationResult:
    """Valide des règles croisées sur un ensemble de lignes.

    Args:
        rows: Lignes à valider.
        rules: Règles croisées.
        start_line: Numéro de la première ligne.
        max_errors: Nombre max d'erreurs.

    Returns:
        CrossValidationResult.
    """
    result = CrossValidationResult()

    for i, row in enumerate(rows):
        line_num = start_line + i
        result.total_checked += 1

        for rule in rules:
            error = validate_cross_column(row, rule, line_num)
            if error and result.total_errors < max_errors:
                result.errors.append(error)
                result.total_errors += 1
                result.errors_by_rule[rule.name] = result.errors_by_rule.get(rule.name, 0) + 1

    return result
