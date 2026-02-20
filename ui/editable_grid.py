"""Grille de données éditable pour CSV Traitement.

Permet la modification directe des cellules comme dans Excel :
- Double-clic pour éditer une cellule
- Navigation clavier (Tab, Entrée, Échap)
- Ajout/suppression de lignes
- Suivi des modifications (cellules modifiées en surbrillance)
"""

import logging
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

logger = logging.getLogger(__name__)

# Palette sombre (reprise du dashboard)
DARK = {
    'bg': '#1e1e2e',
    'bg_alt': '#252536',
    'bg_card': '#2a2a3d',
    'fg': '#cdd6f4',
    'fg_dim': '#6c7086',
    'accent': '#89b4fa',
    'accent2': '#a6e3a1',
    'warning': '#fab387',
    'error': '#f38ba8',
    'success': '#a6e3a1',
    'border': '#45475a',
    'select': '#313244',
    'header_bg': '#363650',
    'corrected_bg': '#3d3800',
    'edited_bg': '#2d3d2d',
}


class EditableGrid(tk.Toplevel):
    """Fenêtre d'édition de données avec grille modifiable."""

    def __init__(self, parent, header: list[str], rows: list[list[str]],
                 title: str = "Éditeur de données", on_save=None):
        """
        Args:
            parent: Widget parent.
            header: Liste des en-têtes de colonnes.
            rows: Liste de lignes (listes de valeurs).
            title: Titre de la fenêtre.
            on_save: Callback appelé avec (header, rows, changes) quand l'utilisateur sauvegarde.
        """
        super().__init__(parent)
        self.title(title)
        self.configure(bg=DARK['bg'])
        self.geometry("1000x650")
        self.minsize(700, 400)
        self.transient(parent)

        self._header = list(header)
        self._rows = [list(row) for row in rows]
        self._on_save = on_save
        self._changes: dict[tuple[int, int], tuple[str, str]] = {}  # (row, col) -> (old, new)
        self._deleted_rows: set[int] = set()
        self._added_rows: list[int] = []

        self._current_edit: Optional[tk.Entry] = None
        self._current_cell: Optional[tuple[str, int]] = None

        self._build_toolbar()
        self._build_grid()
        self._build_status()
        self._populate()

    def _build_toolbar(self):
        """Barre d'outils de l'éditeur."""
        toolbar = ttk.Frame(self, padding=(10, 6))
        toolbar.pack(fill='x')

        ttk.Button(toolbar, text="➕ Ajouter ligne",
                   command=self._add_row).pack(side='left', padx=3)
        ttk.Button(toolbar, text="➖ Supprimer ligne",
                   command=self._delete_row).pack(side='left', padx=3)

        ttk.Separator(toolbar, orient='vertical').pack(side='left', padx=8, fill='y')

        ttk.Button(toolbar, text="↩ Annuler modifs",
                   command=self._revert_changes).pack(side='left', padx=3)

        ttk.Separator(toolbar, orient='vertical').pack(side='left', padx=8, fill='y')

        ttk.Button(toolbar, text="✅ Appliquer",
                   command=self._on_apply, style='Accent.TButton').pack(side='right', padx=3)
        ttk.Button(toolbar, text="Annuler",
                   command=self.destroy).pack(side='right', padx=3)

        # Info
        self._info_label = ttk.Label(toolbar, text="Double-clic pour éditer une cellule",
                                     style='Dim.TLabel')
        self._info_label.pack(side='left', padx=15)

    def _build_grid(self):
        """Grille de données avec scrollbars."""
        grid_frame = ttk.Frame(self)
        grid_frame.pack(fill='both', expand=True, padx=10, pady=(0, 5))

        # Colonnes : numéro de ligne + en-têtes
        cols = ['#'] + self._header
        self._tree = ttk.Treeview(grid_frame, columns=cols, show='headings',
                                  selectmode='browse')

        for i, col in enumerate(cols):
            self._tree.heading(col, text=col)
            width = 40 if i == 0 else max(80, min(200, len(col) * 10 + 20))
            self._tree.column(col, width=width, minwidth=40)

        # Scrollbars
        vsb = ttk.Scrollbar(grid_frame, orient='vertical', command=self._tree.yview)
        hsb = ttk.Scrollbar(grid_frame, orient='horizontal', command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        grid_frame.rowconfigure(0, weight=1)
        grid_frame.columnconfigure(0, weight=1)

        # Bind double-click for editing
        self._tree.bind('<Double-1>', self._on_double_click)
        self._tree.bind('<Return>', self._on_enter)
        self._tree.bind('<Escape>', self._cancel_edit)

        # Tags for visual feedback
        self._tree.tag_configure('edited', background=DARK['edited_bg'])
        self._tree.tag_configure('added', background='#1d3d2d')
        self._tree.tag_configure('deleted', background='#3d1d1d')

    def _build_status(self):
        """Barre de statut."""
        status_frame = ttk.Frame(self, padding=(10, 4))
        status_frame.pack(fill='x', side='bottom')
        self._status_label = ttk.Label(status_frame,
                                       text=f"{len(self._rows)} lignes, {len(self._header)} colonnes",
                                       style='Dim.TLabel')
        self._status_label.pack(side='left')
        self._changes_label = ttk.Label(status_frame, text="Aucune modification",
                                        style='Dim.TLabel')
        self._changes_label.pack(side='right')

    def _populate(self):
        """Remplit la grille avec les données."""
        self._tree.delete(*self._tree.get_children())
        for i, row in enumerate(self._rows):
            if i in self._deleted_rows:
                continue
            values = [str(i + 1)] + row
            tags = ()
            if i in self._added_rows:
                tags = ('added',)
            elif any((i, c) in self._changes for c in range(len(row))):
                tags = ('edited',)
            self._tree.insert('', 'end', iid=str(i), values=values, tags=tags)

    def _on_double_click(self, event):
        """Démarre l'édition d'une cellule au double-clic."""
        self._cancel_edit()

        region = self._tree.identify_region(event.x, event.y)
        if region != 'cell':
            return

        item = self._tree.identify_row(event.y)
        column = self._tree.identify_column(event.x)

        if not item or not column:
            return

        col_idx = int(column.replace('#', '')) - 1  # 0-indexed
        if col_idx == 0:  # Skip row number column
            return

        data_col = col_idx - 1  # Index into actual data
        row_idx = int(item)

        # Get cell bounding box
        bbox = self._tree.bbox(item, column)
        if not bbox:
            return

        x, y, w, h = bbox
        current_value = self._rows[row_idx][data_col] if data_col < len(self._rows[row_idx]) else ''

        # Create entry widget
        entry = tk.Entry(self._tree, bg=DARK['bg_alt'], fg=DARK['fg'],
                        insertbackground=DARK['fg'], font=('JetBrains Mono', 9),
                        relief='solid', borderwidth=1,
                        highlightcolor=DARK['accent'], highlightthickness=1)
        entry.insert(0, current_value)
        entry.select_range(0, 'end')
        entry.place(x=x, y=y, width=w, height=h)
        entry.focus_set()

        self._current_edit = entry
        self._current_cell = (item, data_col)

        # Bind validation
        entry.bind('<Return>', lambda e: self._commit_edit())
        entry.bind('<Tab>', lambda e: self._commit_and_next())
        entry.bind('<Escape>', self._cancel_edit)
        entry.bind('<FocusOut>', lambda e: self._commit_edit())

    def _commit_edit(self):
        """Valide l'édition en cours."""
        if not self._current_edit or not self._current_cell:
            return

        item, data_col = self._current_cell
        row_idx = int(item)
        new_value = self._current_edit.get()

        # Ensure row has enough columns
        while len(self._rows[row_idx]) <= data_col:
            self._rows[row_idx].append('')

        old_value = self._rows[row_idx][data_col]
        if new_value != old_value:
            self._changes[(row_idx, data_col)] = (old_value, new_value)
            self._rows[row_idx][data_col] = new_value

        self._current_edit.destroy()
        self._current_edit = None
        self._current_cell = None

        self._populate()
        self._update_status()

        # Re-select the edited row
        if self._tree.exists(item):
            self._tree.selection_set(item)
            self._tree.focus(item)

    def _commit_and_next(self):
        """Valide et passe à la cellule suivante."""
        if not self._current_cell:
            return

        item, data_col = self._current_cell
        self._commit_edit()

        # Move to next column, or next row
        next_col = data_col + 1
        row_idx = int(item)
        if next_col >= len(self._header):
            next_col = 0
            row_idx += 1
            while row_idx in self._deleted_rows:
                row_idx += 1

        if row_idx < len(self._rows):
            next_item = str(row_idx)
            if self._tree.exists(next_item):
                self._tree.selection_set(next_item)
                self._tree.focus(next_item)
                # Simulate double-click on next cell
                column_id = f'#{next_col + 2}'  # +2 for row number + 1-indexed
                bbox = self._tree.bbox(next_item, column_id)
                if bbox:
                    x, y, w, h = bbox
                    self._start_edit(next_item, next_col, x, y, w, h)

        return 'break'

    def _start_edit(self, item: str, data_col: int, x: int, y: int, w: int, h: int):
        """Démarre l'édition d'une cellule spécifique."""
        self._cancel_edit()

        row_idx = int(item)
        current_value = self._rows[row_idx][data_col] if data_col < len(self._rows[row_idx]) else ''

        entry = tk.Entry(self._tree, bg=DARK['bg_alt'], fg=DARK['fg'],
                        insertbackground=DARK['fg'], font=('JetBrains Mono', 9),
                        relief='solid', borderwidth=1,
                        highlightcolor=DARK['accent'], highlightthickness=1)
        entry.insert(0, current_value)
        entry.select_range(0, 'end')
        entry.place(x=x, y=y, width=w, height=h)
        entry.focus_set()

        self._current_edit = entry
        self._current_cell = (item, data_col)

        entry.bind('<Return>', lambda e: self._commit_edit())
        entry.bind('<Tab>', lambda e: self._commit_and_next())
        entry.bind('<Escape>', self._cancel_edit)
        entry.bind('<FocusOut>', lambda e: self._commit_edit())

    def _cancel_edit(self, event=None):
        """Annule l'édition en cours."""
        if self._current_edit:
            self._current_edit.destroy()
            self._current_edit = None
            self._current_cell = None

    def _on_enter(self, event):
        """Commence l'édition sur Entrée."""
        sel = self._tree.selection()
        if sel:
            item = sel[0]
            # Edit first data column
            column_id = '#2'
            bbox = self._tree.bbox(item, column_id)
            if bbox:
                x, y, w, h = bbox
                self._start_edit(item, 0, x, y, w, h)

    def _add_row(self):
        """Ajoute une ligne vide à la fin."""
        self._cancel_edit()
        new_row = [''] * len(self._header)
        idx = len(self._rows)
        self._rows.append(new_row)
        self._added_rows.append(idx)
        self._populate()
        self._update_status()
        # Scroll to new row
        item = str(idx)
        if self._tree.exists(item):
            self._tree.see(item)
            self._tree.selection_set(item)

    def _delete_row(self):
        """Supprime la ligne sélectionnée."""
        self._cancel_edit()
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("Supprimer", "Sélectionnez une ligne à supprimer.", parent=self)
            return

        row_idx = int(sel[0])
        self._deleted_rows.add(row_idx)
        self._populate()
        self._update_status()

    def _revert_changes(self):
        """Annule toutes les modifications."""
        self._cancel_edit()

        # Revert cell changes
        for (row_idx, col_idx), (old_value, _) in self._changes.items():
            if row_idx < len(self._rows) and col_idx < len(self._rows[row_idx]):
                self._rows[row_idx][col_idx] = old_value

        # Remove added rows
        for idx in sorted(self._added_rows, reverse=True):
            if idx < len(self._rows):
                self._rows.pop(idx)

        self._changes.clear()
        self._deleted_rows.clear()
        self._added_rows.clear()
        self._populate()
        self._update_status()

    def _update_status(self):
        """Met à jour les compteurs."""
        active_rows = len(self._rows) - len(self._deleted_rows)
        self._status_label.config(text=f"{active_rows} lignes, {len(self._header)} colonnes")

        n_changes = len(self._changes) + len(self._deleted_rows) + len(self._added_rows)
        if n_changes == 0:
            self._changes_label.config(text="Aucune modification")
        else:
            parts = []
            if self._changes:
                parts.append(f"{len(self._changes)} cellule(s) modifiée(s)")
            if self._added_rows:
                parts.append(f"{len(self._added_rows)} ligne(s) ajoutée(s)")
            if self._deleted_rows:
                parts.append(f"{len(self._deleted_rows)} ligne(s) supprimée(s)")
            self._changes_label.config(text=" | ".join(parts))

    def _on_apply(self):
        """Applique les modifications et ferme."""
        self._cancel_edit()

        # Build final rows (excluding deleted)
        final_rows = []
        for i, row in enumerate(self._rows):
            if i not in self._deleted_rows:
                final_rows.append(row)

        n_changes = len(self._changes) + len(self._deleted_rows) + len(self._added_rows)

        if n_changes == 0:
            self.destroy()
            return

        if self._on_save:
            self._on_save(self._header, final_rows, self._changes)

        self.destroy()
