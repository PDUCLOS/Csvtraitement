"""Fenêtre de preview avec recherche, filtre, navigation et pagination.

Affiche avant/après avec coloration et permet de naviguer
dans les données avec recherche par contenu ou numéro de ligne.
"""

import tkinter as tk
from tkinter import ttk
from typing import Optional

from core.detector import DetectionResult
from core.fixer import FixResult
from utils.chunked_reader import read_lines_raw, parse_csv_line


LINES_PER_PAGE = 100
MAX_CELL_WIDTH = 35


class PreviewWindow:
    """Fenêtre de preview améliorée."""

    def __init__(
        self,
        parent: tk.Tk,
        detection: DetectionResult,
        fix_result: Optional[FixResult] = None,
        dark_theme: Optional[dict] = None
    ):
        self.detection = detection
        self.fix_result = fix_result
        self.colors = dark_theme or {}

        self.current_page = 0
        self.total_pages = max(1, (detection.total_lines - 1) // LINES_PER_PAGE)
        self.search_results: list[int] = []
        self.search_idx = 0

        self.window = tk.Toplevel(parent)
        self.window.title("Preview — Données")
        self.window.geometry("1300x700")
        self.window.transient(parent)

        if self.colors:
            self.window.configure(bg=self.colors.get('bg', '#1e1e2e'))

        self._build_ui()
        self._load_page(0)

    def _build_ui(self):
        """Construit l'interface."""
        bg = self.colors.get('bg', '#1e1e2e')
        bg_alt = self.colors.get('bg_alt', '#252536')
        fg = self.colors.get('fg', '#cdd6f4')

        # ── Barre de contrôle ──
        ctrl = ttk.Frame(self.window, padding=8)
        ctrl.pack(fill='x')

        # Navigation
        ttk.Button(ctrl, text="⏮", command=lambda: self._load_page(0), width=3).pack(side='left', padx=2)
        ttk.Button(ctrl, text="◀", command=self._prev_page, width=3).pack(side='left', padx=2)
        self.page_label = ttk.Label(ctrl, text="Page 1/1")
        self.page_label.pack(side='left', padx=8)
        ttk.Button(ctrl, text="▶", command=self._next_page, width=3).pack(side='left', padx=2)
        ttk.Button(ctrl, text="⏭", command=lambda: self._load_page(self.total_pages - 1), width=3).pack(side='left', padx=2)

        ttk.Separator(ctrl, orient='vertical').pack(side='left', padx=10, fill='y')

        # Aller à une ligne
        ttk.Label(ctrl, text="Ligne:").pack(side='left', padx=(0, 3))
        self.goto_var = tk.StringVar()
        goto_entry = ttk.Entry(ctrl, textvariable=self.goto_var, width=8)
        goto_entry.pack(side='left')
        goto_entry.bind('<Return>', lambda e: self._goto_line())
        ttk.Button(ctrl, text="Go", command=self._goto_line, width=3).pack(side='left', padx=3)

        ttk.Separator(ctrl, orient='vertical').pack(side='left', padx=10, fill='y')

        # Recherche
        ttk.Label(ctrl, text="Chercher:").pack(side='left', padx=(0, 3))
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(ctrl, textvariable=self.search_var, width=20)
        search_entry.pack(side='left')
        search_entry.bind('<Return>', lambda e: self._search())
        ttk.Button(ctrl, text="🔍", command=self._search, width=3).pack(side='left', padx=2)
        ttk.Button(ctrl, text="▼", command=self._search_next, width=3).pack(side='left', padx=2)
        self.search_lbl = ttk.Label(ctrl, text="")
        self.search_lbl.pack(side='left', padx=5)

        # Filtre
        ttk.Separator(ctrl, orient='vertical').pack(side='left', padx=10, fill='y')
        self.filter_var = tk.StringVar(value='all')
        ttk.Radiobutton(ctrl, text="Toutes", variable=self.filter_var, value='all',
                        command=lambda: self._load_page(self.current_page)).pack(side='left')
        ttk.Radiobutton(ctrl, text="Non conformes", variable=self.filter_var, value='issues',
                        command=lambda: self._load_page(self.current_page)).pack(side='left')
        ttk.Radiobutton(ctrl, text="Corrigées", variable=self.filter_var, value='corrected',
                        command=lambda: self._load_page(self.current_page)).pack(side='left')

        # ── Notebook avant/après ──
        self.notebook = ttk.Notebook(self.window)
        self.notebook.pack(fill='both', expand=True, padx=8, pady=(0, 8))

        frame_before = ttk.Frame(self.notebook)
        self.notebook.add(frame_before, text='  Données originales  ')
        self.tree_before = self._create_tree(frame_before)

        if self.fix_result and self.fix_result.corrected_lines:
            frame_after = ttk.Frame(self.notebook)
            self.notebook.add(frame_after, text='  Après correction  ')
            self.tree_after = self._create_tree(frame_after)
        else:
            self.tree_after = None

        # Légende
        legend = ttk.Frame(self.window, padding=4)
        legend.pack(fill='x', padx=10)
        for color, text in [('#a6e3a1', 'Conforme'), ('#fab387', 'Corrigé'), ('#f38ba8', 'Non conforme')]:
            lbl = tk.Label(legend, text=f" ● {text} ", bg=bg, fg=color, font=('Segoe UI', 9))
            lbl.pack(side='left', padx=5)

    def _create_tree(self, parent: ttk.Frame) -> ttk.Treeview:
        n_cols = self.detection.expected_columns
        headers = self.detection.header[:n_cols]
        columns = ['#Ligne'] + [f"c{i}" for i in range(n_cols)]
        tree = ttk.Treeview(parent, columns=columns, show='headings', height=28)

        tree.heading('#Ligne', text='#')
        tree.column('#Ligne', width=60, anchor='center')
        for i in range(n_cols):
            header_text = headers[i].strip() if i < len(headers) else f'Col {i}'
            tree.heading(f"c{i}", text=header_text)
            tree.column(f"c{i}", width=max(80, min(MAX_CELL_WIDTH * 7, 180)))

        v_sb = ttk.Scrollbar(parent, orient='vertical', command=tree.yview)
        h_sb = ttk.Scrollbar(parent, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        tree.grid(row=0, column=0, sticky='nsew')
        v_sb.grid(row=0, column=1, sticky='ns')
        h_sb.grid(row=1, column=0, sticky='ew')
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

        tree.tag_configure('conforming', background='#1a3320')
        tree.tag_configure('non_conforming', background='#3d1f28')
        tree.tag_configure('corrected', background='#3d3800')
        tree.tag_configure('search_hit', background='#2a2a60')

        return tree

    def _load_page(self, page: int):
        """Charge une page de données."""
        self.current_page = max(0, min(page, self.total_pages - 1))
        self.page_label.config(text=f"Page {self.current_page + 1}/{self.total_pages}")

        start_line = self.current_page * LINES_PER_PAGE + 2  # +2 pour header + 1-indexed
        expected = self.detection.expected_columns
        sep = self.detection.separator
        enc = self.detection.encoding
        corrected_lines = self.fix_result.corrected_lines if self.fix_result else {}
        filter_mode = self.filter_var.get()

        self.tree_before.delete(*self.tree_before.get_children())
        if self.tree_after:
            self.tree_after.delete(*self.tree_after.get_children())

        line_num = 1
        displayed = 0
        skip_target = self.current_page * LINES_PER_PAGE

        for line in read_lines_raw(self.detection.filepath, enc, skip_header=True):
            line_num += 1
            if line_num < start_line:
                continue
            if displayed >= LINES_PER_PAGE:
                break

            cells = parse_csv_line(line, sep)
            is_conforming = len(cells) == expected
            is_corrected = line_num in corrected_lines

            # Filtre
            if filter_mode == 'issues' and is_conforming and not is_corrected:
                continue
            if filter_mode == 'corrected' and not is_corrected:
                continue

            tag = 'corrected' if is_corrected else ('non_conforming' if not is_conforming else 'conforming')

            display = self._truncate_cells(cells, expected)
            self.tree_before.insert('', 'end', values=[str(line_num)] + display, tags=(tag,))

            if self.tree_after:
                if is_corrected:
                    after_cells = corrected_lines[line_num]
                    after_display = self._truncate_cells(after_cells, expected)
                    self.tree_after.insert('', 'end', values=[str(line_num)] + after_display, tags=('corrected',))
                else:
                    self.tree_after.insert('', 'end', values=[str(line_num)] + display, tags=(tag,))

            displayed += 1

    def _truncate_cells(self, cells: list[str], expected: int) -> list[str]:
        display = []
        for c in cells[:expected]:
            val = c.strip() if c else ''
            if len(val) > MAX_CELL_WIDTH:
                val = val[:MAX_CELL_WIDTH - 2] + '…'
            display.append(val)
        while len(display) < expected:
            display.append('')
        return display

    def _prev_page(self):
        if self.current_page > 0:
            self._load_page(self.current_page - 1)

    def _next_page(self):
        if self.current_page < self.total_pages - 1:
            self._load_page(self.current_page + 1)

    def _goto_line(self):
        try:
            line = int(self.goto_var.get())
            page = max(0, (line - 2) // LINES_PER_PAGE)
            self._load_page(page)
        except ValueError:
            pass

    def _search(self):
        """Recherche dans les données."""
        query = self.search_var.get().lower()
        if not query:
            return

        self.search_results = []
        self.search_idx = 0
        sep = self.detection.separator
        enc = self.detection.encoding

        line_num = 1
        for line in read_lines_raw(self.detection.filepath, enc, skip_header=True):
            line_num += 1
            if query in line.lower():
                self.search_results.append(line_num)
            if len(self.search_results) >= 500:
                break

        if self.search_results:
            self.search_lbl.config(text=f"{len(self.search_results)} résultats")
            self._goto_search_result(0)
        else:
            self.search_lbl.config(text="Aucun résultat")

    def _search_next(self):
        if self.search_results:
            self.search_idx = (self.search_idx + 1) % len(self.search_results)
            self._goto_search_result(self.search_idx)

    def _goto_search_result(self, idx: int):
        line = self.search_results[idx]
        page = max(0, (line - 2) // LINES_PER_PAGE)
        self._load_page(page)
        self.search_lbl.config(text=f"{idx + 1}/{len(self.search_results)}")
