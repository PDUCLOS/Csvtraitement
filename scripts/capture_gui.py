"""Captures RÉELLES de l'interface Tkinter, pilotée sur l'export synthétique de démo.

Lance le vrai DashboardApp, charge samples/erp_demo_1k.csv, déclenche les mêmes
actions que les boutons (Analyser, Stats, Qualité, Doublons, Corriger, Preview) et
photographie la fenêtre après chaque étape (PIL.ImageGrab, Windows).

Usage :  python scripts/capture_gui.py      -> docs/img/gui-*.png
"""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from pathlib import Path

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "img"
DEMO = ROOT / "samples" / "erp_demo_1k.csv"

try:  # coordonnées Tk = pixels physiques (sinon décalage avec la mise à l'échelle Windows)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

from ui.dashboard import DashboardApp  # noqa: E402


def grab(win: tk.Misc, name: str) -> None:
    win.update_idletasks()
    win.update()
    x, y = win.winfo_rootx(), win.winfo_rooty()
    w, h = win.winfo_width(), win.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(OUT / f"{name}.png")
    print(f"  {name}.png  {w}x{h}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    root.geometry("1440x900+40+40")
    root.attributes("-topmost", True)
    app = DashboardApp(root, initial_file=str(DEMO))
    app.root.geometry("1440x900+40+40")

    def wait_for(attr: str, then, tries: int = 100):
        if getattr(app, attr) is not None:
            root.after(600, then)
        elif tries:
            root.after(200, lambda: wait_for(attr, then, tries - 1))
        else:
            raise SystemExit(f"timeout en attendant {attr}")

    preview: dict = {}

    def s1():
        app.notebook.select(app.tab_diag); grab(root, "gui-diagnostic")
        app.notebook.select(app.tab_columns); grab(root, "gui-colonnes")
        app._on_analyze(); wait_for("analysis", s2)

    def s2():
        grab(root, "gui-anomalies")
        app._on_quality(); wait_for("quality_report", s3)

    def s3():
        grab(root, "gui-qualite")
        app._on_dedup(); wait_for("dedup_result", s4)

    def s4():
        grab(root, "gui-doublons")
        app._on_fix(); wait_for("fix_result", s5)

    def s5():
        grab(root, "gui-corrections")
        from ui.dashboard import DARK
        from ui.preview import PreviewWindow
        pw = PreviewWindow(root, app.detection, app.fix_result, dark_theme=DARK)
        preview["pw"] = pw
        pw.window.attributes("-topmost", True)
        pw.window.geometry("1440x860+40+40")
        root.after(1200, s6)

    def s6():
        pw = preview["pw"]
        grab(pw.window, "gui-preview")
        pw.filter_var.set("issues")  # filtre « Non conformes » : les lignes décalées
        pw._load_page(0)
        root.after(800, s7)

    def s7():
        pw = preview["pw"]
        grab(pw.window, "gui-preview-avant")
        pw.notebook.select(1)
        root.after(800, s8)

    def s8():
        grab(preview["pw"].window, "gui-preview-apres")
        root.after(200, root.destroy)

    wait_for("detection", s1)
    root.mainloop()


if __name__ == "__main__":
    main()
