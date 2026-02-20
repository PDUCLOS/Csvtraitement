"""Système d'historique et d'undo pour les corrections.

Enregistre chaque opération pour permettre l'annulation individuelle
ou globale des corrections.
"""

import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class HistoryEntry:
    """Entrée dans l'historique des corrections."""
    timestamp: str
    operation: str          # 'fix_dates', 'fix_decimals', 'realign', 'validate', 'manual'
    description: str
    lines_affected: int
    corrections_count: int
    # Snapshot des données modifiées pour undo
    # Format: {line_number: (before_cells, after_cells)}
    changes: dict[int, tuple[list[str], list[str]]] = field(default_factory=dict)


class CorrectionHistory:
    """Gère l'historique des corrections avec undo/redo."""

    def __init__(self, max_history: int = 50):
        self._history: list[HistoryEntry] = []
        self._redo_stack: list[HistoryEntry] = []
        self._max_history = max_history

    @property
    def entries(self) -> list[HistoryEntry]:
        """Liste des entrées d'historique (plus récent en premier)."""
        return list(reversed(self._history))

    @property
    def can_undo(self) -> bool:
        return len(self._history) > 0

    @property
    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0

    @property
    def total_operations(self) -> int:
        return len(self._history)

    @property
    def total_corrections(self) -> int:
        return sum(e.corrections_count for e in self._history)

    def record(
        self,
        operation: str,
        description: str,
        changes: dict[int, tuple[list[str], list[str]]]
    ) -> HistoryEntry:
        """Enregistre une opération dans l'historique.

        Args:
            operation: Type d'opération.
            description: Description lisible.
            changes: {line_number: (before_cells, after_cells)}.

        Returns:
            L'entrée créée.
        """
        entry = HistoryEntry(
            timestamp=datetime.now().isoformat(),
            operation=operation,
            description=description,
            lines_affected=len(changes),
            corrections_count=len(changes),
            changes=changes,
        )

        self._history.append(entry)
        self._redo_stack.clear()  # Redo invalide après une nouvelle opération

        # Limiter la taille de l'historique
        if len(self._history) > self._max_history:
            self._history.pop(0)

        logger.info(f"Historique: {operation} - {len(changes)} lignes")
        return entry

    def undo(self) -> Optional[HistoryEntry]:
        """Annule la dernière opération.

        Returns:
            L'entrée annulée, ou None si rien à annuler.
            Les changes contiennent les données à restaurer.
        """
        if not self._history:
            return None

        entry = self._history.pop()
        self._redo_stack.append(entry)

        logger.info(f"Undo: {entry.operation} - {entry.lines_affected} lignes")
        return entry

    def redo(self) -> Optional[HistoryEntry]:
        """Rétablit la dernière opération annulée.

        Returns:
            L'entrée rétablie, ou None si rien à rétablir.
        """
        if not self._redo_stack:
            return None

        entry = self._redo_stack.pop()
        self._history.append(entry)

        logger.info(f"Redo: {entry.operation} - {entry.lines_affected} lignes")
        return entry

    def clear(self):
        """Efface tout l'historique."""
        self._history.clear()
        self._redo_stack.clear()
        logger.info("Historique effacé")

    def get_summary(self) -> dict:
        """Retourne un résumé de l'historique."""
        ops_count: dict[str, int] = {}
        for entry in self._history:
            ops_count[entry.operation] = ops_count.get(entry.operation, 0) + entry.corrections_count

        return {
            'total_operations': self.total_operations,
            'total_corrections': self.total_corrections,
            'can_undo': self.can_undo,
            'can_redo': self.can_redo,
            'operations_breakdown': ops_count,
        }

    def get_affected_lines(self) -> set[int]:
        """Retourne l'ensemble des lignes modifiées dans l'historique courant."""
        lines = set()
        for entry in self._history:
            lines.update(entry.changes.keys())
        return lines

    def get_current_state(self, line_number: int) -> Optional[list[str]]:
        """Retourne l'état actuel d'une ligne (après toutes les corrections).

        Parcourt l'historique dans l'ordre pour appliquer les modifications.

        Args:
            line_number: Numéro de la ligne.

        Returns:
            Cellules de la ligne après corrections, ou None si non modifiée.
        """
        current = None
        for entry in self._history:
            if line_number in entry.changes:
                _, after = entry.changes[line_number]
                current = after
        return current
