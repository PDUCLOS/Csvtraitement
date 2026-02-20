"""Export multi-format avec highlighting des cellules corrigées.

Formats supportés en sortie :
- XLSX : xlsxwriter avec typage et highlighting (défaut)
- CSV  : texte délimité avec séparateur configurable
- JSON : array d'objets ou array d'arrays
- ODS  : LibreOffice / OpenOffice (via odfpy)
- Parquet : format columnar Apache (via pyarrow)

Écriture via fichier temporaire local pour éviter les erreurs d'E/S
sur les lecteurs réseau/cloud (pCloud, Google Drive, etc.).
"""

import json
import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.detector import DetectionResult
from core.analyzer import AnalysisResult
from core.fixer import FixResult, CellCorrection
from utils.patterns import CellType, detect_cell_type
from utils.chunked_reader import read_chunks

logger = logging.getLogger(__name__)

XLSX_MAX_ROWS = 1_048_576  # Limite Excel

# Séparateurs CSV disponibles pour l'export
CSV_SEPARATORS = {
    '§': '§ (section — recommandé, jamais dans les données)',
    '|': '| (pipe)',
    '\\t': 'Tabulation (TSV)',
    ';': '; (point-virgule — classique FR)',
    ',': ', (virgule — classique US)',
}

# Formats d'export disponibles
EXPORT_FORMATS = {
    'xlsx': 'XLSX (Excel — typé, colorisé)',
    'csv': 'CSV (texte délimité — léger, configurable)',
    'json': 'JSON (array d\'objets — web, API)',
    'ods': 'ODS (LibreOffice / OpenOffice)',
    'parquet': 'Parquet (columnar — Big Data, performant)',
}


def _safe_write(tmp_path: Path, final_path: Path) -> Path:
    """Copie un fichier temporaire vers la destination finale puis supprime le temporaire.

    Args:
        tmp_path: Chemin du fichier temporaire local.
        final_path: Chemin de destination (peut être réseau/cloud).

    Returns:
        Chemin du fichier final créé.

    Raises:
        OSError: Si la copie vers la destination échoue.
    """
    try:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(tmp_path), str(final_path))
        logger.info(f"Fichier copié vers : {final_path}")
        return final_path
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
            logger.debug(f"Fichier temporaire supprimé : {tmp_path}")
        except OSError as e:
            logger.warning(f"Impossible de supprimer le temporaire {tmp_path}: {e}")


def export_xlsx(
    detection: DetectionResult,
    analysis: AnalysisResult,
    fix_result: FixResult,
    output_path: Path,
    progress_callback=None
) -> Path:
    """Exporte les données corrigées en XLSX avec typage et highlighting.

    Args:
        detection: Résultat de détection.
        analysis: Résultat d'analyse.
        fix_result: Résultat des corrections.
        output_path: Chemin du fichier XLSX de sortie.
        progress_callback: Fonction de suivi.

    Returns:
        Chemin du fichier créé.
    """
    import xlsxwriter

    output_path = Path(output_path)
    total_lines = detection.total_lines

    # Vérifier la limite XLSX
    if total_lines > XLSX_MAX_ROWS:
        logger.warning(
            f"Fichier trop grand ({total_lines} lignes) pour XLSX. "
            f"Export CSV à la place."
        )
        csv_path = output_path.with_suffix('.csv')
        return export_csv_clean(detection, fix_result, csv_path, progress_callback, separator='§')

    # Écrire dans un fichier temporaire local pour éviter les erreurs d'E/S
    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix='.xlsx', prefix='csvtraitement_')
    import os
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)
    logger.info(f"Écriture temporaire : {tmp_path}")

    wb = xlsxwriter.Workbook(str(tmp_path), {
        'strings_to_numbers': False,
        'constant_memory': True,  # Mode streaming pour gros fichiers
    })

    # ── Formats ──
    fmt_header = wb.add_format({
        'bold': True,
        'bg_color': '#4472C4',
        'font_color': 'white',
        'border': 1,
    })
    fmt_corrected = wb.add_format({
        'bg_color': '#FFF2CC',  # Jaune pâle
        'border': 1,
    })
    fmt_date = wb.add_format({
        'num_format': 'yyyy-mm-dd',
    })
    fmt_date_corrected = wb.add_format({
        'num_format': 'yyyy-mm-dd',
        'bg_color': '#FFF2CC',
        'border': 1,
    })
    fmt_number = wb.add_format({
        'num_format': '#,##0.00',
    })
    fmt_number_corrected = wb.add_format({
        'num_format': '#,##0.00',
        'bg_color': '#FFF2CC',
        'border': 1,
    })
    fmt_integer = wb.add_format({
        'num_format': '#,##0',
    })

    # ── Onglet Données ──
    ws_data = wb.add_worksheet('Données')

    # Indexer les corrections par (line, col)
    corrections_index: set[tuple[int, int]] = set()
    for corr in fix_result.corrections:
        corrections_index.add((corr.line_number, corr.column_index))

    # Écrire le header
    profiles = detection.column_profiles
    for col_idx, header_val in enumerate(detection.header):
        ws_data.write(0, col_idx, header_val.strip(), fmt_header)

    # Écrire les données
    row = 1
    line_num = 1
    sep = detection.separator
    enc = detection.encoding
    expected = detection.expected_columns

    for chunk in read_chunks(detection.filepath, sep, enc, chunk_size=10_000, skip_header=True):
        for cells in chunk:
            line_num += 1

            # Utiliser les cellules corrigées si disponibles
            if line_num in fix_result.corrected_lines:
                out_cells = fix_result.corrected_lines[line_num]
            else:
                out_cells = list(cells)
                while len(out_cells) < expected:
                    out_cells.append('')
                if len(out_cells) > expected:
                    out_cells = out_cells[:expected]

            for col_idx, value in enumerate(out_cells):
                is_corrected = (line_num, col_idx) in corrections_index

                if col_idx < len(profiles):
                    profile = profiles[col_idx]
                    _write_typed_cell(
                        ws_data, row, col_idx, value, profile,
                        is_corrected, fmt_corrected, fmt_date,
                        fmt_date_corrected, fmt_number,
                        fmt_number_corrected, fmt_integer
                    )
                else:
                    if is_corrected:
                        ws_data.write(row, col_idx, value, fmt_corrected)
                    else:
                        ws_data.write(row, col_idx, value)

            row += 1

        if progress_callback:
            progress_callback(
                min(row, total_lines),
                total_lines,
                f"Export XLSX: {row}/{total_lines} lignes"
            )

    # Ajuster largeur colonnes
    for i in range(expected):
        ws_data.set_column(i, i, 15)

    # ── Onglet Rapport ──
    ws_report = wb.add_worksheet('Rapport')
    _write_report_sheet(ws_report, detection, analysis, fix_result, fmt_header)

    wb.close()

    # Copier vers la destination finale et supprimer le temporaire
    _safe_write(tmp_path, output_path)
    logger.info(f"Export XLSX terminé: {output_path} ({row - 1} lignes de données)")
    return output_path


def _write_typed_cell(
    ws, row: int, col: int, value: str,
    profile, is_corrected: bool,
    fmt_corrected, fmt_date, fmt_date_corrected,
    fmt_number, fmt_number_corrected, fmt_integer
):
    """Écrit une cellule avec le type approprié."""
    stripped = value.strip() if value else ''

    if not stripped:
        if is_corrected:
            ws.write_blank(row, col, '', fmt_corrected)
        else:
            ws.write_blank(row, col, '')
        return

    if profile.dominant_type in (CellType.DATE, CellType.DATE_INVALID):
        # Tenter de parser comme date
        try:
            if '-' in stripped and len(stripped) == 10:
                dt = datetime.strptime(stripped, '%Y-%m-%d')
                fmt = fmt_date_corrected if is_corrected else fmt_date
                ws.write_datetime(row, col, dt, fmt)
                return
        except ValueError:
            pass
        fmt = fmt_corrected if is_corrected else None
        ws.write(row, col, stripped, fmt)

    elif profile.dominant_type == CellType.DECIMAL:
        try:
            num = float(stripped)
            fmt = fmt_number_corrected if is_corrected else fmt_number
            ws.write_number(row, col, num, fmt)
            return
        except ValueError:
            pass
        fmt = fmt_corrected if is_corrected else None
        ws.write(row, col, stripped, fmt)

    elif profile.dominant_type == CellType.INTEGER:
        try:
            num = int(stripped)
            fmt = fmt_corrected if is_corrected else fmt_integer
            ws.write_number(row, col, num, fmt)
            return
        except ValueError:
            pass
        fmt = fmt_corrected if is_corrected else None
        ws.write(row, col, stripped, fmt)

    else:
        fmt = fmt_corrected if is_corrected else None
        ws.write(row, col, stripped, fmt)


def _write_report_sheet(ws, detection, analysis, fix_result, fmt_header):
    """Écrit l'onglet rapport avec les statistiques."""
    ws.set_column(0, 0, 30)
    ws.set_column(1, 1, 50)

    row = 0
    ws.write(row, 0, 'Rapport de traitement CSV', fmt_header)
    ws.write(row, 1, '', fmt_header)
    row += 2

    info = [
        ('Fichier source', str(detection.filepath.name)),
        ('Encodage', detection.encoding),
        ('Séparateur', repr(detection.separator)),
        ('Colonnes attendues', str(detection.expected_columns)),
        ('Total lignes', str(detection.total_lines)),
        ('', ''),
        ('Lignes conformes', str(analysis.conforming_lines)),
        ('Lignes non conformes', str(analysis.non_conforming_lines)),
        ('% conformité', f'{analysis.conformity_percentage:.1f}%'),
        ('', ''),
        ('Corrections totales', str(fix_result.total_corrections)),
        ('Dates corrigées', str(fix_result.date_fixes)),
        ('Décimales normalisées', str(fix_result.decimal_fixes)),
        ('Réalignements', str(fix_result.realignment_fixes)),
    ]

    for label, value in info:
        ws.write(row, 0, label)
        ws.write(row, 1, value)
        row += 1

    # Profils de colonnes
    row += 2
    ws.write(row, 0, 'Profil des colonnes', fmt_header)
    ws.write(row, 1, '', fmt_header)
    row += 1

    for profile in detection.column_profiles:
        header_name = detection.header[profile.index] if profile.index < len(detection.header) else f'Col {profile.index}'
        ws.write(row, 0, f'{header_name} (col {profile.index})')
        ws.write(row, 1, f'{profile.dominant_type.name} ({profile.confidence:.0%} confiance)')
        row += 1


def export_csv_clean(
    detection: DetectionResult,
    fix_result: FixResult,
    output_path: Path,
    progress_callback=None,
    separator: str = '§'
) -> Path:
    """Export CSV nettoyé avec séparateur configurable.

    Args:
        detection: Résultat de détection.
        fix_result: Résultat des corrections.
        output_path: Chemin du fichier de sortie.
        progress_callback: Fonction de suivi.
        separator: Séparateur CSV de sortie (défaut: § pour éviter les conflits).

    Returns:
        Chemin du fichier créé.
    """
    output_path = Path(output_path)
    sep = '\t' if separator == '\\t' else separator
    enc = detection.encoding
    expected = detection.expected_columns

    # Écrire dans un fichier temporaire local
    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix='.csv', prefix='csvtraitement_')
    import os
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)
    logger.info(f"Écriture temporaire CSV : {tmp_path} (séparateur: {repr(sep)})")

    with open(tmp_path, 'w', encoding='utf-8', newline='') as f:
        # Header
        f.write(sep.join(detection.header[:expected]) + '\n')

        line_num = 1
        for chunk in read_chunks(
            detection.filepath, detection.separator, enc,
            chunk_size=10_000, skip_header=True
        ):
            for cells in chunk:
                line_num += 1

                if line_num in fix_result.corrected_lines:
                    out_cells = fix_result.corrected_lines[line_num]
                else:
                    out_cells = list(cells)
                    while len(out_cells) < expected:
                        out_cells.append('')
                    if len(out_cells) > expected:
                        out_cells = out_cells[:expected]

                f.write(sep.join(out_cells) + '\n')

            if progress_callback:
                progress_callback(line_num, detection.total_lines, f"Export CSV: {line_num} lignes")

    # Copier vers la destination finale et supprimer le temporaire
    _safe_write(tmp_path, output_path)
    logger.info(f"Export CSV terminé: {output_path}")
    return output_path


def _collect_rows(detection: DetectionResult, fix_result: FixResult) -> list[list[str]]:
    """Collecte toutes les lignes corrigées pour les exports non-streaming.

    Args:
        detection: Résultat de détection.
        fix_result: Résultat des corrections.

    Returns:
        Liste de lignes (sans header).
    """
    expected = detection.expected_columns
    rows = []
    line_num = 1

    for chunk in read_chunks(
        detection.filepath, detection.separator, detection.encoding,
        chunk_size=10_000, skip_header=True
    ):
        for cells in chunk:
            line_num += 1
            if line_num in fix_result.corrected_lines:
                out_cells = fix_result.corrected_lines[line_num]
            else:
                out_cells = list(cells)
                while len(out_cells) < expected:
                    out_cells.append('')
                if len(out_cells) > expected:
                    out_cells = out_cells[:expected]
            rows.append(out_cells)

    return rows


def export_json(
    detection: DetectionResult,
    fix_result: FixResult,
    output_path: Path,
    progress_callback=None,
    as_array: bool = False,
) -> Path:
    """Export JSON (array d'objets ou array d'arrays).

    Args:
        detection: Résultat de détection.
        fix_result: Résultat des corrections.
        output_path: Chemin du fichier de sortie.
        progress_callback: Fonction de suivi.
        as_array: Si True, exporte en array d'arrays au lieu d'objets.

    Returns:
        Chemin du fichier créé.
    """
    output_path = Path(output_path)
    header = detection.header[:detection.expected_columns]

    if progress_callback:
        progress_callback(0, detection.total_lines, "Collecte des données...")

    rows = _collect_rows(detection, fix_result)

    if progress_callback:
        progress_callback(len(rows) // 2, detection.total_lines, "Écriture JSON...")

    # Écrire dans un temporaire
    import os
    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix='.json', prefix='csvtraitement_')
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)

    with open(tmp_path, 'w', encoding='utf-8') as f:
        if as_array:
            data = [header] + rows
        else:
            data = []
            for row in rows:
                obj = {}
                for i, h in enumerate(header):
                    obj[h.strip()] = row[i] if i < len(row) else ''
                data.append(obj)

        json.dump(data, f, ensure_ascii=False, indent=2)

    _safe_write(tmp_path, output_path)

    if progress_callback:
        progress_callback(detection.total_lines, detection.total_lines, "Export JSON terminé")

    logger.info(f"Export JSON terminé: {output_path} ({len(rows)} lignes)")
    return output_path


def export_ods(
    detection: DetectionResult,
    fix_result: FixResult,
    output_path: Path,
    progress_callback=None,
) -> Path:
    """Export ODS (LibreOffice / OpenOffice).

    Args:
        detection: Résultat de détection.
        fix_result: Résultat des corrections.
        output_path: Chemin du fichier de sortie.
        progress_callback: Fonction de suivi.

    Returns:
        Chemin du fichier créé.
    """
    try:
        from odf.opendocument import OpenDocumentSpreadsheet
        from odf import table as odf_table
        from odf import text as odf_text
        from odf.style import Style, TableColumnProperties, TextProperties
    except ImportError:
        raise ImportError(
            "odfpy est requis pour l'export ODS. "
            "Installez-le avec : pip install odfpy"
        )

    output_path = Path(output_path)
    header = detection.header[:detection.expected_columns]

    if progress_callback:
        progress_callback(0, detection.total_lines, "Collecte des données...")

    rows = _collect_rows(detection, fix_result)

    if progress_callback:
        progress_callback(len(rows) // 2, detection.total_lines, "Écriture ODS...")

    doc = OpenDocumentSpreadsheet()

    # Style pour le header
    header_style = Style(name="HeaderStyle", family="table-cell")
    header_style.addElement(TextProperties(fontweight="bold"))
    doc.automaticstyles.addElement(header_style)

    # Créer la feuille
    ws = odf_table.Table(name="Données")

    # Header
    header_row = odf_table.TableRow()
    for h in header:
        cell = odf_table.TableCell(stylename=header_style, valuetype="string")
        cell.addElement(odf_text.P(text=h.strip()))
        header_row.addElement(cell)
    ws.addElement(header_row)

    # Données
    for row in rows:
        tr = odf_table.TableRow()
        for val in row:
            cell = odf_table.TableCell(valuetype="string")
            cell.addElement(odf_text.P(text=val))
            tr.addElement(cell)
        ws.addElement(tr)

    doc.spreadsheet.addElement(ws)

    # Écrire dans un temporaire
    import os
    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix='.ods', prefix='csvtraitement_')
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)

    doc.save(str(tmp_path))
    _safe_write(tmp_path, output_path)

    if progress_callback:
        progress_callback(detection.total_lines, detection.total_lines, "Export ODS terminé")

    logger.info(f"Export ODS terminé: {output_path} ({len(rows)} lignes)")
    return output_path


def export_parquet(
    detection: DetectionResult,
    fix_result: FixResult,
    output_path: Path,
    progress_callback=None,
) -> Path:
    """Export Parquet (format columnar Apache).

    Args:
        detection: Résultat de détection.
        fix_result: Résultat des corrections.
        output_path: Chemin du fichier de sortie.
        progress_callback: Fonction de suivi.

    Returns:
        Chemin du fichier créé.
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        raise ImportError(
            "pyarrow est requis pour l'export Parquet. "
            "Installez-le avec : pip install pyarrow"
        )

    output_path = Path(output_path)
    header = [h.strip() for h in detection.header[:detection.expected_columns]]

    if progress_callback:
        progress_callback(0, detection.total_lines, "Collecte des données...")

    rows = _collect_rows(detection, fix_result)

    if progress_callback:
        progress_callback(len(rows) // 2, detection.total_lines, "Écriture Parquet...")

    # Construire les colonnes
    columns = {h: [] for h in header}
    for row in rows:
        for i, h in enumerate(header):
            columns[h].append(row[i] if i < len(row) else '')

    table = pa.table(columns)

    # Écrire dans un temporaire
    import os
    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix='.parquet', prefix='csvtraitement_')
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)

    pq.write_table(table, str(tmp_path))
    _safe_write(tmp_path, output_path)

    if progress_callback:
        progress_callback(detection.total_lines, detection.total_lines, "Export Parquet terminé")

    logger.info(f"Export Parquet terminé: {output_path} ({len(rows)} lignes)")
    return output_path


def export_any(
    detection: DetectionResult,
    analysis: AnalysisResult,
    fix_result: FixResult,
    output_path: Path,
    fmt: str = 'xlsx',
    csv_separator: str = '§',
    progress_callback=None,
) -> Path:
    """Export unifié vers n'importe quel format supporté.

    Args:
        detection: Résultat de détection.
        analysis: Résultat d'analyse.
        fix_result: Résultat des corrections.
        output_path: Chemin du fichier de sortie.
        fmt: Format ('xlsx', 'csv', 'json', 'ods', 'parquet').
        csv_separator: Séparateur pour l'export CSV.
        progress_callback: Fonction de suivi.

    Returns:
        Chemin du fichier créé.
    """
    if fmt == 'xlsx':
        return export_xlsx(detection, analysis, fix_result, output_path, progress_callback)
    elif fmt == 'csv':
        return export_csv_clean(detection, fix_result, output_path, progress_callback, csv_separator)
    elif fmt == 'json':
        return export_json(detection, fix_result, output_path, progress_callback)
    elif fmt == 'ods':
        return export_ods(detection, fix_result, output_path, progress_callback)
    elif fmt == 'parquet':
        return export_parquet(detection, fix_result, output_path, progress_callback)
    else:
        raise ValueError(f"Format d'export non supporté : {fmt}. Utilisez : {list(EXPORT_FORMATS.keys())}")
