"""Lecteur de fichiers Parquet (format columnar Big Data).

Utilise pyarrow pour lire les fichiers Parquet et les convertir
en lignes list[list[str]] compatibles avec le pipeline CSV.

Dépendance optionnelle : pip install pyarrow
"""

import logging
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)


def _value_to_str(value) -> str:
    """Convertit une valeur Parquet en string."""
    if value is None:
        return ''
    return str(value)


def read_parquet(
    filepath: Path,
    columns: Optional[list[str]] = None,
) -> tuple[list[str], list[list[str]], int]:
    """Lit un fichier Parquet et retourne header + lignes.

    Args:
        filepath: Chemin du fichier Parquet.
        columns: Liste de colonnes à lire (None = toutes).

    Returns:
        Tuple (header, rows, total_lines).
    """
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise ImportError(
            "pyarrow est requis pour lire les fichiers Parquet. "
            "Installez-le avec : pip install pyarrow"
        )

    table = pq.read_table(str(filepath), columns=columns)
    header = table.column_names

    logger.info(f"Lecture Parquet : {filepath.name} ({table.num_rows} lignes, {len(header)} colonnes)")

    rows = []
    # Convertir colonne par colonne pour la performance
    col_data = [table.column(name).to_pylist() for name in header]
    num_rows = table.num_rows

    for i in range(num_rows):
        row = [_value_to_str(col_data[j][i]) for j in range(len(header))]
        rows.append(row)

    total_lines = num_rows + 1
    logger.info(f"Parquet lu : {total_lines} lignes, {len(header)} colonnes")
    return header, rows, total_lines


def read_parquet_chunks(
    filepath: Path,
    chunk_size: int = 10_000,
    columns: Optional[list[str]] = None,
    skip_header: bool = False,
) -> Generator[list[list[str]], None, None]:
    """Lit un fichier Parquet par chunks en streaming.

    Utilise le batch reader de pyarrow pour un vrai streaming mémoire.

    Args:
        filepath: Chemin du fichier.
        chunk_size: Nombre de lignes par chunk.
        columns: Colonnes à lire.
        skip_header: Non utilisé (Parquet n'a pas de header dans les données).

    Yields:
        Chunks de lignes.
    """
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise ImportError("pyarrow requis : pip install pyarrow")

    pf = pq.ParquetFile(str(filepath))

    for batch in pf.iter_batches(batch_size=chunk_size, columns=columns):
        col_names = batch.schema.names
        col_data = [batch.column(name).to_pylist() for name in col_names]
        chunk = []
        for i in range(batch.num_rows):
            row = [_value_to_str(col_data[j][i]) for j in range(len(col_names))]
            chunk.append(row)
        if chunk:
            yield chunk


def get_parquet_metadata(filepath: Path) -> dict:
    """Retourne les métadonnées d'un fichier Parquet."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise ImportError("pyarrow requis : pip install pyarrow")

    pf = pq.ParquetFile(str(filepath))
    meta = pf.metadata

    schema_info = []
    for i in range(pf.schema_arrow.num_fields):
        field = pf.schema_arrow.field(i)
        schema_info.append({
            'name': field.name,
            'type': str(field.type),
            'nullable': field.nullable,
        })

    return {
        'num_rows': meta.num_rows,
        'num_columns': meta.num_columns,
        'num_row_groups': meta.num_row_groups,
        'format_version': meta.format_version,
        'schema': schema_info,
    }
