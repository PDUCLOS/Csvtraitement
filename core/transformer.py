"""Règles de transformation personnalisées pour les données CSV.

Permet de définir des transformations par colonne :
- Regex find/replace
- Split colonne (1 → 2+)
- Merge colonnes (2+ → 1)
- Mapping de valeurs (table de correspondance)
- Chaîne de transformations
- Sérialisable en JSON pour les templates
"""

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class TransformType(Enum):
    """Types de transformation."""
    REGEX_REPLACE = auto()   # Regex find/replace
    SPLIT = auto()           # Split une colonne en plusieurs
    MERGE = auto()           # Merge plusieurs colonnes en une
    VALUE_MAP = auto()       # Table de correspondance
    STRIP = auto()           # Supprimer des caractères
    PAD = auto()             # Padding (remplissage)
    FORMULA = auto()         # Expression simple (col1 + col2, etc.)


@dataclass
class TransformRule:
    """Règle de transformation pour une ou plusieurs colonnes."""
    name: str
    transform_type: TransformType
    source_columns: list[int]           # Colonnes source (indices)
    target_column: int = -1             # Colonne cible (-1 = nouvelle colonne)

    # Regex replace
    regex_pattern: str = ""
    regex_replacement: str = ""
    regex_flags: int = 0                # re flags

    # Split
    split_separator: str = " "
    split_max: int = 2                  # Nombre max de parties
    split_new_headers: list[str] = field(default_factory=list)

    # Merge
    merge_separator: str = " "
    merge_template: str = ""            # Ex: "{0} - {1}" pour formatter

    # Value map
    value_map: dict[str, str] = field(default_factory=dict)
    value_map_default: Optional[str] = None  # Valeur par défaut si pas dans le map
    value_map_case_insensitive: bool = True

    # Strip
    strip_chars: str = ""

    # Pad
    pad_width: int = 0
    pad_char: str = "0"
    pad_side: str = "left"              # "left" ou "right"

    # Formula
    formula: str = ""                   # Expression Python simple

    def __post_init__(self):
        self._compiled_regex = None
        if self.regex_pattern:
            try:
                self._compiled_regex = re.compile(self.regex_pattern, self.regex_flags)
            except re.error as e:
                logger.warning(f"Regex invalide pour règle '{self.name}': {e}")

    def to_dict(self) -> dict:
        """Sérialise la règle en dict JSON-compatible."""
        return {
            'name': self.name,
            'transform_type': self.transform_type.name,
            'source_columns': self.source_columns,
            'target_column': self.target_column,
            'regex_pattern': self.regex_pattern,
            'regex_replacement': self.regex_replacement,
            'split_separator': self.split_separator,
            'split_max': self.split_max,
            'split_new_headers': self.split_new_headers,
            'merge_separator': self.merge_separator,
            'merge_template': self.merge_template,
            'value_map': self.value_map,
            'value_map_default': self.value_map_default,
            'value_map_case_insensitive': self.value_map_case_insensitive,
            'strip_chars': self.strip_chars,
            'pad_width': self.pad_width,
            'pad_char': self.pad_char,
            'pad_side': self.pad_side,
            'formula': self.formula,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'TransformRule':
        """Reconstruit une règle depuis un dict."""
        return cls(
            name=data['name'],
            transform_type=TransformType[data['transform_type']],
            source_columns=data['source_columns'],
            target_column=data.get('target_column', -1),
            regex_pattern=data.get('regex_pattern', ''),
            regex_replacement=data.get('regex_replacement', ''),
            split_separator=data.get('split_separator', ' '),
            split_max=data.get('split_max', 2),
            split_new_headers=data.get('split_new_headers', []),
            merge_separator=data.get('merge_separator', ' '),
            merge_template=data.get('merge_template', ''),
            value_map=data.get('value_map', {}),
            value_map_default=data.get('value_map_default'),
            value_map_case_insensitive=data.get('value_map_case_insensitive', True),
            strip_chars=data.get('strip_chars', ''),
            pad_width=data.get('pad_width', 0),
            pad_char=data.get('pad_char', '0'),
            pad_side=data.get('pad_side', 'left'),
            formula=data.get('formula', ''),
        )


@dataclass
class TransformResult:
    """Résultat de l'application des transformations."""
    total_rows: int = 0
    modified_rows: int = 0
    new_columns_added: int = 0
    changes: list[tuple[int, str, str, str]] = field(default_factory=list)  # (line, rule, before, after)
    errors: list[tuple[int, str, str]] = field(default_factory=list)         # (line, rule, error)
    new_headers: list[str] = field(default_factory=list)


def apply_transform_rule(
    cells: list[str],
    rule: TransformRule,
    headers: list[str]
) -> tuple[list[str], list[str], bool]:
    """Applique une règle de transformation à une ligne.

    Args:
        cells: Cellules de la ligne.
        rule: Règle à appliquer.
        headers: En-têtes actuels.

    Returns:
        Tuple (cellules modifiées, headers modifiés, True si modifié).
    """
    modified = False
    new_cells = list(cells)
    new_headers = list(headers)

    if rule.transform_type == TransformType.REGEX_REPLACE:
        for col_idx in rule.source_columns:
            if col_idx < len(new_cells):
                original = new_cells[col_idx]
                if rule._compiled_regex:
                    replaced = rule._compiled_regex.sub(rule.regex_replacement, original)
                    if replaced != original:
                        new_cells[col_idx] = replaced
                        modified = True

    elif rule.transform_type == TransformType.SPLIT:
        if rule.source_columns and rule.source_columns[0] < len(new_cells):
            col_idx = rule.source_columns[0]
            value = new_cells[col_idx]
            parts = value.split(rule.split_separator, rule.split_max - 1)

            # Remplir avec des vides si pas assez de parties
            while len(parts) < rule.split_max:
                parts.append('')

            # Remplacer la colonne d'origine et insérer les nouvelles
            new_cells[col_idx] = parts[0]
            for j, part in enumerate(parts[1:], 1):
                insert_pos = col_idx + j
                new_cells.insert(insert_pos, part)
                if rule.split_new_headers and j - 1 < len(rule.split_new_headers):
                    new_headers.insert(insert_pos, rule.split_new_headers[j - 1])
                else:
                    new_headers.insert(insert_pos, f"{new_headers[col_idx]}_{j+1}")
            modified = True

    elif rule.transform_type == TransformType.MERGE:
        if len(rule.source_columns) >= 2:
            values = []
            for col_idx in rule.source_columns:
                if col_idx < len(new_cells):
                    values.append(new_cells[col_idx].strip())
                else:
                    values.append('')

            if rule.merge_template:
                merged = rule.merge_template.format(*values)
            else:
                merged = rule.merge_separator.join(v for v in values if v)

            # Écrire dans la première colonne source
            target = rule.target_column if rule.target_column >= 0 else rule.source_columns[0]
            if target < len(new_cells):
                new_cells[target] = merged
            modified = True

    elif rule.transform_type == TransformType.VALUE_MAP:
        for col_idx in rule.source_columns:
            if col_idx < len(new_cells):
                original = new_cells[col_idx].strip()
                lookup = original.lower() if rule.value_map_case_insensitive else original

                # Construire lookup map
                if rule.value_map_case_insensitive:
                    map_lookup = {k.lower(): v for k, v in rule.value_map.items()}
                else:
                    map_lookup = rule.value_map

                if lookup in map_lookup:
                    new_cells[col_idx] = map_lookup[lookup]
                    modified = True
                elif rule.value_map_default is not None:
                    new_cells[col_idx] = rule.value_map_default
                    modified = True

    elif rule.transform_type == TransformType.STRIP:
        for col_idx in rule.source_columns:
            if col_idx < len(new_cells):
                original = new_cells[col_idx]
                stripped = original.strip(rule.strip_chars) if rule.strip_chars else original.strip()
                if stripped != original:
                    new_cells[col_idx] = stripped
                    modified = True

    elif rule.transform_type == TransformType.PAD:
        for col_idx in rule.source_columns:
            if col_idx < len(new_cells):
                original = new_cells[col_idx].strip()
                if original and len(original) < rule.pad_width:
                    if rule.pad_side == 'left':
                        padded = original.rjust(rule.pad_width, rule.pad_char)
                    else:
                        padded = original.ljust(rule.pad_width, rule.pad_char)
                    if padded != new_cells[col_idx]:
                        new_cells[col_idx] = padded
                        modified = True

    return new_cells, new_headers, modified


def apply_transforms(
    rows: list[list[str]],
    headers: list[str],
    rules: list[TransformRule],
    start_line: int = 2,
    max_changes: int = 50_000
) -> tuple[list[list[str]], list[str], TransformResult]:
    """Applique une chaîne de transformations à des lignes.

    Args:
        rows: Lignes à transformer.
        headers: En-têtes.
        rules: Règles de transformation à appliquer dans l'ordre.
        start_line: Numéro de la première ligne.
        max_changes: Nombre max de changements à tracker.

    Returns:
        Tuple (lignes transformées, headers mis à jour, résultat).
    """
    result = TransformResult()
    current_headers = list(headers)
    transformed = []

    for i, row in enumerate(rows):
        line_num = start_line + i
        result.total_rows += 1
        current_cells = list(row)
        row_modified = False

        for rule in rules:
            try:
                new_cells, new_headers, modified = apply_transform_rule(
                    current_cells, rule, current_headers
                )
                if modified:
                    if len(result.changes) < max_changes:
                        before = '|'.join(current_cells[c] for c in rule.source_columns if c < len(current_cells))
                        after = '|'.join(new_cells[c] for c in rule.source_columns if c < len(new_cells))
                        result.changes.append((line_num, rule.name, before, after))
                    current_cells = new_cells
                    current_headers = new_headers
                    row_modified = True
            except Exception as e:
                result.errors.append((line_num, rule.name, str(e)))

        if row_modified:
            result.modified_rows += 1
        transformed.append(current_cells)

    result.new_headers = current_headers
    result.new_columns_added = len(current_headers) - len(headers)

    logger.info(
        f"Transformations: {result.modified_rows}/{result.total_rows} lignes modifiées, "
        f"{result.new_columns_added} nouvelles colonnes"
    )
    return transformed, current_headers, result


def save_transform_rules(rules: list[TransformRule], filepath: Path):
    """Sauvegarde les règles de transformation en JSON."""
    data = [r.to_dict() for r in rules]
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info(f"Règles sauvegardées : {filepath}")


def load_transform_rules(filepath: Path) -> list[TransformRule]:
    """Charge les règles de transformation depuis un JSON."""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    rules = [TransformRule.from_dict(d) for d in data]
    logger.info(f"Règles chargées : {len(rules)} depuis {filepath}")
    return rules
