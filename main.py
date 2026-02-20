#!/usr/bin/env python3
"""CSV Traitement - Point d'entrée principal.

Usage:
    python main.py                            # GUI
    python main.py --file data.csv            # GUI avec fichier
    python main.py cli --file data.csv        # CLI analyse
    python main.py cli --file data.csv --fix --output out.xlsx
    python main.py cli --batch /dossier/ --fix --output /sortie/
    python main.py cli --file data.csv --stats --report rapport.json
"""

import argparse
import logging
import sys


def setup_logging(debug: bool = False):
    """Configure le logging."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )


def main():
    """Point d'entrée principal."""
    parser = argparse.ArgumentParser(
        description='CSV Traitement - Diagnostic & Correction de fichiers CSV',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  python main.py                                      # Interface graphique
  python main.py --file export.csv                    # GUI + charger fichier
  python main.py cli --file export.csv                # Analyser en CLI
  python main.py cli --file export.csv --fix -o out.xlsx   # Corriger + exporter
  python main.py cli --batch /exports/ --fix -o /sortie/   # Batch
  python main.py cli --file export.csv --stats             # Statistiques
        """
    )
    parser.add_argument('--debug', '-d', action='store_true', help='Mode debug')
    parser.add_argument('--file', '-f', type=str, default=None,
                       help='Fichier CSV à charger (mode GUI)')

    # Sous-commandes
    subparsers = parser.add_subparsers(dest='mode', help='Mode de fonctionnement')

    # CLI mode
    from core.cli import setup_cli_parser
    setup_cli_parser(subparsers)

    args = parser.parse_args()
    setup_logging(getattr(args, 'debug', False))
    logger = logging.getLogger(__name__)

    if args.mode == 'cli':
        # Mode CLI
        logger.info("Mode CLI")
        from core.cli import run_cli
        run_cli(args)
    else:
        # Mode GUI (par défaut)
        logger.info("Mode GUI")
        import tkinter as tk
        from ui.dashboard import DashboardApp

        root = tk.Tk()
        try:
            root.iconbitmap(default='')
        except tk.TclError:
            pass

        app = DashboardApp(root, initial_file=args.file)
        root.mainloop()


if __name__ == '__main__':
    main()
