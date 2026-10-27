"""Corrections des données CSV : dates, décimales, réalignement.

Applique les corrections validées par l'utilisateur :
- Remplacement des dates invalides (00.00.0000) par vide
- Normalisation des décimales
- Réalignement des colonnes décalées
- Nettoyage des valeurs masquées (N/A, NULL, #REF!...) et des espaces
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from core.detector import DetectionResult, ColumnProfile
from core.analyzer import AnalysisResult, ShiftInfo
from utils.patterns import (
    CellType, detect_cell_type, normalize_decimal,
    normalize_integer, parse_date, get_type_category,
    DATE_INVALID, MASKED_EMPTY_VALUES
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CellCorrection:
    """Correction appliquée à une cellule (slots : ~3 objets par ligne sur 1 M de lignes)."""
    line_number: int
    column_index: int
    original_value: str
    corrected_value: str
    correction_type: str  # 'date_invalid', 'decimal_normalize', 'realign', 'type_cast'


@dataclass
class FixResult:
    """Résultat des corrections."""
    corrected_lines: dict[int, list[str]]  # line_number -> corrected cells
    corrections: list[CellCorrection]
    total_corrections: int = 0
    date_fixes: int = 0
    decimal_fixes: int = 0
    realignment_fixes: int = 0
    masked_fixes: int = 0
    whitespace_fixes: int = 0

    @property
    def summary(self) -> dict[str, int]:
        return {
            'total': self.total_corrections,
            'dates': self.date_fixes,
            'decimals': self.decimal_fixes,
            'realignments': self.realignment_fixes,
            'masked': self.masked_fixes,
            'whitespace': self.whitespace_fixes,
        }


def fix_date_cell(value: str) -> tuple[str, bool]:
    """Corrige une cellule de type date.

    - 00.00.0000 → '' (vide)
    - Dates valides → format ISO YYYY-MM-DD

    Args:
        value: Valeur brute de la cellule.

    Returns:
        Tuple (valeur corrigée, True si modifiée).
    """
    stripped = value.strip()
    if not stripped:
        return '', False

    if DATE_INVALID.match(stripped):
        return '', True

    parsed = parse_date(stripped)
    if parsed and parsed != stripped:
        return parsed, True

    return stripped, False


def fix_decimal_cell(value: str, target_format: str = 'FLOAT') -> tuple[str, bool]:
    """Normalise une cellule décimale.

    Convertit 1.987.239,99 ou 1,987,239.99 en float.

    Args:
        value: Valeur brute.
        target_format: 'FLOAT' pour Python float string, 'FR' pour format FR.

    Returns:
        Tuple (valeur normalisée, True si modifiée).
    """
    stripped = value.strip()
    if not stripped:
        return '', False

    result = normalize_decimal(stripped)
    if result is not None:
        if target_format == 'FR':
            # Format FR pour CSV : virgule décimale, pas de séparateur milliers
            int_part = int(result)
            dec_part = result - int_part
            if dec_part == 0:
                formatted = str(int_part)
            else:
                formatted = f"{result:.10f}".rstrip('0').rstrip('.').replace('.', ',')
            if formatted != stripped:
                return formatted, True
            return stripped, False
        else:
            # Float standard
            formatted = f"{result:.10f}".rstrip('0').rstrip('.')
            if formatted != stripped:
                return formatted, True
            return stripped, False

    return stripped, False


def fix_integer_cell(value: str) -> tuple[str, bool]:
    """Normalise une cellule entière.

    Args:
        value: Valeur brute.

    Returns:
        Tuple (valeur normalisée, True si modifiée).
    """
    stripped = value.strip()
    result = normalize_integer(stripped)
    if result is not None:
        formatted = str(result)
        if formatted != stripped:
            return formatted, True
    return stripped, False


def realign_row(
    cells: list[str],
    shift_info: ShiftInfo,
    profiles: list[ColumnProfile],
    expected_columns: int,
    separator: str = ';',
    merge_text: bool = True,
) -> tuple[list[str], list[CellCorrection]]:
    """Réaligne une ligne décalée.

    Stratégies selon le type de décalage :
    - extra_column : si la cellule précédente est une colonne texte, recoller les deux
      morceaux avec le séparateur d'origine (``merge_text``) ; sinon supprimer la cellule en trop
    - missing_column : insérer une cellule vide
    - type_mismatch : garder tel quel (correction de type seulement)

    Args:
        cells: Cellules de la ligne.
        shift_info: Information sur le décalage.
        profiles: Profils de référence.
        expected_columns: Nombre de colonnes attendu.
        separator: Séparateur d'origine, utilisé pour recoller un champ texte coupé.
        merge_text: Recoller plutôt que supprimer quand la colonne précédente est du texte.

    Returns:
        Tuple (cellules réalignées, liste des corrections).
    """
    corrections: list[CellCorrection] = []

    if shift_info.shift_type == 'extra_column':
        col = shift_info.shift_start_column
        if (merge_text and 0 < col < len(cells)
                and profiles[col - 1].dominant_category == 'text'):
            # Cas typique ERP : séparateur non échappé dans un champ texte
            # ("Dupont;Fils") -> on recolle les deux morceaux au lieu d'en perdre un.
            merged = cells[col - 1] + separator + cells[col]
            new_cells = cells[:col - 1] + [merged] + cells[col + 1:]
            if len(new_cells) > expected_columns:
                new_cells = new_cells[:expected_columns]
            while len(new_cells) < expected_columns:
                new_cells.append('')
            corrections.append(CellCorrection(
                line_number=shift_info.line_number,
                column_index=col - 1,
                original_value=cells[col - 1],
                corrected_value=merged,
                correction_type='realign'
            ))
            return new_cells, corrections

        if 0 <= col < len(cells):
            removed = cells[col]
            new_cells = cells[:col] + cells[col + 1:]

            # Tronquer ou padder si nécessaire
            if len(new_cells) > expected_columns:
                new_cells = new_cells[:expected_columns]
            while len(new_cells) < expected_columns:
                new_cells.append('')

            corrections.append(CellCorrection(
                line_number=shift_info.line_number,
                column_index=col,
                original_value=removed,
                corrected_value='[SUPPRIMÉ]',
                correction_type='realign'
            ))
            return new_cells, corrections

    elif shift_info.shift_type == 'missing_column':
        col = shift_info.shift_start_column
        new_cells = cells[:col] + [''] + cells[col:]

        if len(new_cells) > expected_columns:
            new_cells = new_cells[:expected_columns]
        while len(new_cells) < expected_columns:
            new_cells.append('')

        corrections.append(CellCorrection(
            line_number=shift_info.line_number,
            column_index=col,
            original_value='[MANQUANT]',
            corrected_value='',
            correction_type='realign'
        ))
        return new_cells, corrections

    # type_mismatch ou inconnu : retourner tel quel
    result = list(cells)
    while len(result) < expected_columns:
        result.append('')
    if len(result) > expected_columns:
        result = result[:expected_columns]
    return result, corrections


def fix_row_types(
    cells: list[str],
    profiles: list[ColumnProfile],
    line_number: int
) -> tuple[list[str], list[CellCorrection]]:
    """Corrige les types des cellules d'une ligne selon les profils.

    Args:
        cells: Cellules de la ligne.
        profiles: Profils de colonnes.
        line_number: Numéro de ligne pour le logging.

    Returns:
        Tuple (cellules corrigées, liste des corrections).
    """
    corrections: list[CellCorrection] = []
    fixed = list(cells)

    for i, (cell, profile) in enumerate(zip(fixed, profiles)):
        original = cell

        if profile.dominant_type in (CellType.DATE, CellType.DATE_INVALID):
            new_val, changed = fix_date_cell(cell)
            if changed:
                fixed[i] = new_val
                corrections.append(CellCorrection(
                    line_number=line_number,
                    column_index=i,
                    original_value=original,
                    corrected_value=new_val,
                    correction_type='date_invalid' if not new_val else 'date_format'
                ))

        elif profile.dominant_type == CellType.DECIMAL:
            new_val, changed = fix_decimal_cell(cell, target_format='FLOAT')
            if changed:
                fixed[i] = new_val
                corrections.append(CellCorrection(
                    line_number=line_number,
                    column_index=i,
                    original_value=original,
                    corrected_value=new_val,
                    correction_type='decimal_normalize'
                ))

        elif profile.dominant_type == CellType.INTEGER:
            new_val, changed = fix_integer_cell(cell)
            if changed:
                fixed[i] = new_val
                corrections.append(CellCorrection(
                    line_number=line_number,
                    column_index=i,
                    original_value=original,
                    corrected_value=new_val,
                    correction_type='type_cast'
                ))

    return fixed, corrections


def apply_fixes(
    detection: DetectionResult,
    analysis: AnalysisResult,
    fix_dates: bool = True,
    fix_decimals: bool = True,
    fix_shifts: bool = True,
    fix_masked: bool = True,
    fix_whitespace: bool = False,
    progress_callback=None
) -> FixResult:
    """Applique toutes les corrections au fichier.

    Args:
        detection: Résultat de détection.
        analysis: Résultat d'analyse.
        fix_dates: Corriger les dates invalides.
        fix_decimals: Normaliser les décimales.
        fix_shifts: Réaligner les lignes décalées.
        fix_masked: Vider les valeurs masquées (N/A, NULL, #REF!, ?? ...).
        fix_whitespace: Supprimer les espaces de début/fin et réduire les espaces multiples.
        progress_callback: Fonction de suivi de progression.

    Returns:
        FixResult avec toutes les corrections.
    """
    result = FixResult(corrected_lines={}, corrections=[])

    # Indexer les problèmes par numéro de ligne
    issues_by_line: dict[int, ShiftInfo] = {}
    if fix_shifts:
        for shift in analysis.shifts:
            issues_by_line[shift.line_number] = shift

    profiles = detection.column_profiles
    expected = detection.expected_columns
    sep = detection.separator
    enc = detection.encoding
    fp = detection.filepath

    line_num = 1  # Commence après le header
    chunk_count = 0

    from utils.chunked_reader import read_chunks

    for chunk in read_chunks(fp, sep, enc, chunk_size=10_000, skip_header=True):
        chunk_count += 1

        for cells in chunk:
            line_num += 1
            current_cells = list(cells)
            line_corrections: list[CellCorrection] = []

            # 1. Réalignement si nécessaire
            if line_num in issues_by_line and fix_shifts:
                shift_info = issues_by_line[line_num]
                current_cells, realign_corr = realign_row(
                    current_cells, shift_info, profiles, expected, separator=sep
                )
                line_corrections.extend(realign_corr)
                result.realignment_fixes += len(realign_corr)

            # Padder si nécessaire
            while len(current_cells) < expected:
                current_cells.append('')
            if len(current_cells) > expected:
                current_cells = current_cells[:expected]

            # 2. Valeurs masquées et espaces
            if fix_masked or fix_whitespace:
                for i, cell in enumerate(current_cells):
                    new_val, ctype = cell, None
                    if fix_masked and cell.strip().lower() in MASKED_EMPTY_VALUES:
                        new_val, ctype = '', 'masked_clean'
                    elif fix_whitespace:
                        squeezed = ' '.join(cell.split())
                        if squeezed != cell:
                            new_val, ctype = squeezed, 'whitespace'
                    if ctype:
                        line_corrections.append(CellCorrection(
                            line_number=line_num, column_index=i,
                            original_value=cell, corrected_value=new_val,
                            correction_type=ctype,
                        ))
                        current_cells[i] = new_val
                        if ctype == 'masked_clean':
                            result.masked_fixes += 1
                        else:
                            result.whitespace_fixes += 1

            # 3. Corrections de types (dates, décimales)
            if fix_dates or fix_decimals:
                type_fixed, type_corr = fix_row_types(
                    current_cells, profiles, line_num
                )
                for corr in type_corr:
                    if corr.correction_type.startswith('date') and fix_dates:
                        line_corrections.append(corr)
                        result.date_fixes += 1
                        current_cells = type_fixed
                    elif corr.correction_type == 'decimal_normalize' and fix_decimals:
                        line_corrections.append(corr)
                        result.decimal_fixes += 1
                        current_cells = type_fixed

            if line_corrections:
                result.corrected_lines[line_num] = current_cells
                result.corrections.extend(line_corrections)
                result.total_corrections += len(line_corrections)

        if progress_callback:
            progress_callback(
                min(chunk_count * 10_000, detection.total_lines),
                detection.total_lines,
                f"Correction: {result.total_corrections} corrections appliquées"
            )

    logger.info(
        f"Corrections terminées: {result.total_corrections} total "
        f"({result.date_fixes} dates, {result.decimal_fixes} décimales, "
        f"{result.realignment_fixes} réalignements)"
    )
    return result
