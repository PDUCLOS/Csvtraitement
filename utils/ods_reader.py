"""Lecteur de fichiers ODS (LibreOffice / OpenOffice).

Utilise odfpy pour parser les fichiers ODS et les convertir
en lignes list[list[str]] compatibles avec le pipeline CSV.

Dépendance : pip install odfpy
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _get_cell_text(cell) -> str:
    """Extrait le texte d'une cellule ODS."""
    from odf import text as odf_text
    result = []
    for node in cell.childNodes:
        if node.qname[1] == 'p':
            # Élément <text:p>
            for child in node.childNodes:
                if hasattr(child, 'data'):
                    result.append(child.data)
                elif hasattr(child, '__str__'):
                    result.append(str(child))
    return ''.join(result).strip()


def read_ods(
    filepath: Path,
    sheet_name: Optional[str] = None,
    sheet_index: int = 0,
) -> tuple[list[str], list[list[str]], int]:
    """Lit un fichier ODS et retourne header + lignes.

    Args:
        filepath: Chemin du fichier ODS.
        sheet_name: Nom de la feuille (prioritaire sur sheet_index).
        sheet_index: Index de la feuille (0-based).

    Returns:
        Tuple (header, rows, total_lines).
    """
    try:
        from odf.opendocument import load as odf_load
        from odf import table as odf_table
    except ImportError:
        raise ImportError(
            "odfpy est requis pour lire les fichiers ODS. "
            "Installez-le avec : pip install odfpy"
        )

    doc = odf_load(str(filepath))
    sheets = doc.spreadsheet.getElementsByType(odf_table.Table)

    if not sheets:
        raise ValueError(f"Aucune feuille trouvée dans {filepath.name}")

    # Sélectionner la feuille
    ws = None
    if sheet_name:
        for s in sheets:
            if s.getAttribute('name') == sheet_name:
                ws = s
                break
        if ws is None:
            available = [s.getAttribute('name') for s in sheets]
            raise ValueError(
                f"Feuille '{sheet_name}' introuvable. "
                f"Feuilles disponibles : {available}"
            )
    else:
        ws = sheets[min(sheet_index, len(sheets) - 1)]

    sheet_title = ws.getAttribute('name')
    logger.info(f"Lecture ODS : {filepath.name} → feuille '{sheet_title}'")

    # Lire les lignes
    all_rows = []
    for row in ws.getElementsByType(odf_table.TableRow):
        cells = []
        for cell in row.getElementsByType(odf_table.TableCell):
            # Gérer les cellules répétées
            repeat = cell.getAttribute('numbercolumnsrepeated')
            text = _get_cell_text(cell)
            if repeat:
                try:
                    n = int(repeat)
                    # Limiter les répétitions vides (ODS peut avoir 1024+ colonnes vides)
                    if not text and n > 20:
                        n = 1
                    cells.extend([text] * n)
                except ValueError:
                    cells.append(text)
            else:
                cells.append(text)
        all_rows.append(cells)

    # Supprimer les lignes vides en fin
    while all_rows and all(not c for c in all_rows[-1]):
        all_rows.pop()

    if not all_rows:
        return [], [], 0

    # Normaliser le nombre de colonnes
    max_cols = max(len(r) for r in all_rows) if all_rows else 0
    for row in all_rows:
        while len(row) < max_cols:
            row.append('')
        if len(row) > max_cols:
            row[:] = row[:max_cols]

    header = all_rows[0]
    rows = all_rows[1:]
    total_lines = len(all_rows)

    logger.info(f"ODS lu : {total_lines} lignes, {len(header)} colonnes")
    return header, rows, total_lines


def list_sheets_ods(filepath: Path) -> list[str]:
    """Liste les noms de feuilles d'un fichier ODS."""
    try:
        from odf.opendocument import load as odf_load
        from odf import table as odf_table
    except ImportError:
        raise ImportError("odfpy requis : pip install odfpy")

    doc = odf_load(str(filepath))
    sheets = doc.spreadsheet.getElementsByType(odf_table.Table)
    return [s.getAttribute('name') for s in sheets]
