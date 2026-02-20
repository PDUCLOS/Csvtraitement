"""Lecteur streaming par chunks pour fichiers CSV volumineux.

Gère : BOM UTF-8, champs entre guillemets, retours à la ligne dans les champs,
encodages exotiques, écriture streaming.
"""

import csv
import io
import logging
from pathlib import Path
from typing import Generator, Optional

import chardet

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 10_000
ENCODING_SAMPLE_SIZE = 100_000

# BOM bytes pour différents encodages
BOM_MAP = {
    b'\xef\xbb\xbf': 'utf-8-sig',
    b'\xff\xfe': 'utf-16-le',
    b'\xfe\xff': 'utf-16-be',
}


def detect_encoding(filepath: Path) -> str:
    """Détecte l'encodage d'un fichier, y compris BOM."""
    with open(filepath, 'rb') as f:
        raw_start = f.read(4)
        # Vérifier BOM
        for bom, enc in BOM_MAP.items():
            if raw_start.startswith(bom):
                logger.info(f"BOM détecté : {enc}")
                return enc

        f.seek(0)
        raw = f.read(ENCODING_SAMPLE_SIZE)

    result = chardet.detect(raw)
    encoding = result.get('encoding', 'utf-8') or 'utf-8'
    confidence = result.get('confidence', 0)

    # Mapper les noms d'encodage courants
    encoding_map = {
        'ascii': 'utf-8',
        'iso-8859-1': 'latin-1',
        'iso-8859-15': 'latin-1',
        'windows-1252': 'cp1252',
        'macintosh': 'mac-roman',
    }
    encoding = encoding_map.get(encoding.lower(), encoding)

    logger.info(f"Encodage détecté : {encoding} (confiance: {confidence:.0%})")
    return encoding


def count_lines(filepath: Path, encoding: str = 'utf-8') -> int:
    """Compte le nombre total de lignes d'un fichier."""
    count = 0
    with open(filepath, 'r', encoding=encoding, errors='replace') as f:
        for _ in f:
            count += 1
    return count


def read_lines_raw(
    filepath: Path,
    encoding: str = 'utf-8',
    skip_header: bool = False,
    max_lines: Optional[int] = None
) -> Generator[str, None, None]:
    """Lit les lignes brutes d'un fichier en streaming."""
    with open(filepath, 'r', encoding=encoding, errors='replace') as f:
        if skip_header:
            next(f, None)
        for i, line in enumerate(f):
            if max_lines is not None and i >= max_lines:
                break
            yield line.rstrip('\n').rstrip('\r')


def parse_csv_line(line: str, separator: str) -> list[str]:
    """Parse une ligne CSV en respectant les guillemets.

    Gère les champs entre guillemets contenant le séparateur,
    les guillemets doublés (""), et les espaces.

    Args:
        line: Ligne brute.
        separator: Séparateur CSV.

    Returns:
        Liste de cellules.
    """
    try:
        reader = csv.reader(
            io.StringIO(line),
            delimiter=separator,
            quotechar='"',
            doublequote=True,
            skipinitialspace=True
        )
        return next(reader)
    except (csv.Error, StopIteration):
        # Fallback sur split simple si le parsing CSV échoue
        return line.split(separator)


def read_chunks_quoted(
    filepath: Path,
    separator: str,
    encoding: str = 'utf-8',
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    skip_header: bool = False
) -> Generator[list[list[str]], None, None]:
    """Lit un fichier CSV par chunks avec support des guillemets.

    Utilise le module csv de Python pour gérer correctement les champs
    entre guillemets, les retours à la ligne dans les champs, etc.

    Args:
        filepath: Chemin vers le fichier.
        separator: Séparateur CSV détecté.
        encoding: Encodage du fichier.
        chunk_size: Nombre de lignes par chunk.
        skip_header: Ignorer la première ligne.

    Yields:
        Liste de lignes (chaque ligne = liste de cellules).
    """
    chunk: list[list[str]] = []

    with open(filepath, 'r', encoding=encoding, errors='replace', newline='') as f:
        reader = csv.reader(
            f,
            delimiter=separator,
            quotechar='"',
            doublequote=True,
            skipinitialspace=True
        )

        if skip_header:
            next(reader, None)

        for row in reader:
            chunk.append(row)
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = []

    if chunk:
        yield chunk


def read_chunks(
    filepath: Path,
    separator: str,
    encoding: str = 'utf-8',
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    skip_header: bool = False
) -> Generator[list[list[str]], None, None]:
    """Lit un fichier CSV par chunks (avec support guillemets).

    Alias vers read_chunks_quoted pour rétrocompatibilité.
    """
    yield from read_chunks_quoted(
        filepath, separator, encoding, chunk_size, skip_header
    )


def sample_lines(
    filepath: Path,
    encoding: str = 'utf-8',
    n_head: int = 1000,
    n_random: int = 1000,
    total_lines: Optional[int] = None
) -> list[str]:
    """Échantillonne des lignes pour le profiling."""
    import random

    lines: list[str] = []

    for i, line in enumerate(read_lines_raw(filepath, encoding)):
        if i >= n_head:
            break
        lines.append(line)

    if total_lines is not None and total_lines <= n_head:
        return lines
    if total_lines is None:
        total_lines = count_lines(filepath, encoding)
    if total_lines <= n_head:
        return lines

    random_indices = sorted(random.sample(
        range(n_head, total_lines),
        min(n_random, total_lines - n_head)
    ))

    idx_set = set(random_indices)
    for i, line in enumerate(read_lines_raw(filepath, encoding)):
        if i < n_head:
            continue
        if i in idx_set:
            lines.append(line)
        if i > random_indices[-1]:
            break

    logger.info(f"Échantillon : {len(lines)} lignes")
    return lines


class StreamingWriter:
    """Écrit un fichier CSV en streaming pour éviter la surcharge mémoire.

    Utilisé par le fixer pour écrire les corrections au fil du traitement
    sans stocker toutes les lignes corrigées en RAM.
    """

    def __init__(self, filepath: Path, separator: str = ';', encoding: str = 'utf-8'):
        self.filepath = filepath
        self.separator = separator
        self.encoding = encoding
        self._file = None
        self._writer = None
        self._rows_written = 0

    def __enter__(self):
        self._file = open(self.filepath, 'w', encoding=self.encoding, newline='')
        self._writer = csv.writer(
            self._file,
            delimiter=self.separator,
            quotechar='"',
            quoting=csv.QUOTE_MINIMAL
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._file:
            self._file.close()

    def write_row(self, cells: list[str]):
        """Écrit une ligne."""
        self._writer.writerow(cells)
        self._rows_written += 1

    def write_rows(self, rows: list[list[str]]):
        """Écrit plusieurs lignes."""
        for row in rows:
            self.write_row(row)

    @property
    def rows_written(self) -> int:
        return self._rows_written
