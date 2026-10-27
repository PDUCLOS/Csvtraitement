"""Générateur d'exports ERP SYNTHÉTIQUES avec anomalies injectées et vérité terrain.

Aucune donnée réelle : noms, sociétés, e-mails, téléphones et IBAN sont fabriqués
aléatoirement (graine fixe => fichiers reproductibles). Les IBAN ne passent pas
la clé de contrôle.

Le script écrit deux fichiers :
  - <out>.csv          : l'export « sale », façon SAP (séparateur ';', décimales FR, cp1252 optionnel)
  - <out>.truth.json   : la vérité terrain (numéros de ligne de chaque anomalie injectée
                         + lignes propres attendues pour les lignes décalées)

Usage :
    python scripts/generate_sample.py --rows 100000 --out samples/erp_100k
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

HEADER = [
    "Num_Commande", "Code_Client", "Raison_Sociale", "Date_Commande",
    "Date_Livraison", "Montant_HT", "Quantite", "Statut", "Email_Contact",
    "Telephone", "Ville",
]
VILLES = ["Lyon", "Paris", "Marseille", "Toulouse", "Lille", "Nantes", "Bordeaux",
          "Grenoble", "Strasbourg", "Rennes", "Saint-Étienne", "Villeurbanne"]
PREFIXES = ["Atelier", "Groupe", "Société", "Ets", "Comptoir", "Industries", "Transports"]
NOMS = ["Morel", "Garnier", "Faure", "Rousseau", "Blanc", "Guerin", "Boyer", "Chevalier",
        "Lambert", "Bonnet", "Francois", "Martinez", "Legrand", "Gauthier", "Perrin"]
SUFFIXES = ["SARL", "SAS", "SA", "& Cie", "Frères", "Distribution", "Services"]
STATUTS = ["LIVRE", "EN_COURS", "FACTURE", "ANNULE"]
MASKED = ["#REF!", "N/A", "NULL", "-", "??"]

# Taux d'injection (par ligne)
RATES = {
    "extra_column": 0.010,    # ';' non échappé dans la raison sociale
    "missing_column": 0.005,  # une cellule perdue (Quantite)
    "invalid_date": 0.010,    # 00.00.0000
    "masked_value": 0.010,    # #REF!, N/A... dans Montant_HT
    "us_decimal": 0.020,      # 1,234.56 au lieu de 1.234,56
    "accounting_neg": 0.005,  # (1.234,56)
    "exact_duplicate": 0.005, # ligne recopiée à l'identique
    "near_duplicate": 0.002,  # ligne recopiée avec une faute de frappe (lettres inversées)
}


def fr_amount(x: float) -> str:
    entier, dec = f"{x:.2f}".split(".")
    groups = []
    while len(entier) > 3:
        groups.insert(0, entier[-3:])
        entier = entier[:-3]
    groups.insert(0, entier)
    return ".".join(groups) + "," + dec


def us_amount(x: float) -> str:
    return f"{x:,.2f}"


def fake_date(rng: random.Random) -> str:
    return f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.{rng.randint(2019, 2025)}"


def make_row(rng: random.Random, i: int) -> list[str]:
    nom = rng.choice(NOMS)
    societe = f"{rng.choice(PREFIXES)} {nom} {rng.choice(SUFFIXES)}"
    contact = f"{rng.choice(['j', 'm', 'a', 'p', 's'])}.{nom.lower()}{rng.randint(1, 99)}"
    return [
        f"CMD{4500000000 + i}",
        f"C{rng.randint(10000, 99999)}",
        societe,
        fake_date(rng),
        fake_date(rng),
        fr_amount(rng.uniform(10, 250_000)),
        str(rng.randint(1, 500)),
        rng.choice(STATUTS),
        f"{contact}@exemple-fictif.fr",
        f"0{rng.randint(1, 9)} {rng.randint(10, 99)} {rng.randint(10, 99)} {rng.randint(10, 99)} {rng.randint(10, 99)}",
        rng.choice(VILLES),
    ]


def generate(rows: int, out: Path, seed: int = 42, encoding: str = "utf-8") -> dict:
    rng = random.Random(seed)
    truth: dict = {k: [] for k in RATES}
    truth["shift_expected"] = {}  # ligne -> cellules propres attendues (échantillon)
    out.parent.mkdir(parents=True, exist_ok=True)
    csv_path = out.with_suffix(".csv")
    line_no = 1  # l'en-tête est la ligne 1
    prev_row: list[str] | None = None
    prev_tags: list[str] = []  # anomalies portées par la ligne précédente
    with open(csv_path, "w", encoding=encoding, newline="") as f:
        f.write(";".join(HEADER) + "\r\n")
        for i in range(rows):
            line_no += 1
            r = rng.random()
            if prev_row is not None and r < RATES["exact_duplicate"] + RATES["near_duplicate"]:
                row = list(prev_row)
                if r < RATES["exact_duplicate"]:
                    truth["exact_duplicate"].append(line_no)
                else:
                    # faute de frappe : deux lettres voisines inversées dans la raison sociale
                    name = row[2]
                    k = rng.randint(1, len(name) - 3)
                    while name[k] == name[k + 1]:  # inverser « ss » ne changerait rien
                        k = rng.randint(1, len(name) - 3)
                    row[2] = name[:k] + name[k + 1] + name[k] + name[k + 2:]
                    truth["near_duplicate"].append(line_no)
                for tag in prev_tags:  # le doublon hérite des anomalies de sa source
                    truth[tag].append(line_no)
                f.write(";".join(row) + "\r\n")
                continue

            row = make_row(rng, i)
            clean = list(row)
            r = rng.random()
            amount = rng.uniform(10, 250_000)
            if r < RATES["us_decimal"]:
                row[5] = us_amount(amount)
                truth["us_decimal"].append(line_no)
            elif r < RATES["us_decimal"] + RATES["accounting_neg"]:
                row[5] = f"({fr_amount(amount)})"
                truth["accounting_neg"].append(line_no)
            elif r < RATES["us_decimal"] + RATES["accounting_neg"] + RATES["masked_value"]:
                row[5] = rng.choice(MASKED)
                truth["masked_value"].append(line_no)
            clean[5] = row[5]

            if rng.random() < RATES["invalid_date"]:
                row[4] = "00.00.0000"
                clean[4] = row[4]
                truth["invalid_date"].append(line_no)

            r = rng.random()
            if r < RATES["extra_column"]:
                # La raison sociale contient un ';' (« Martinez; SARL ») que l'ERP n'a pas
                # échappé : à la lecture, le champ est coupé en deux cellules.
                parts = row[2].rsplit(" ", 1)
                row[2] = parts[0] + "; " + parts[1]
                clean[2] = row[2]
                written = ";".join(row)
                truth["extra_column"].append(line_no)
            elif r < RATES["extra_column"] + RATES["missing_column"]:
                written = ";".join(row[:6] + row[7:])  # Quantite perdue
                truth["missing_column"].append(line_no)
            else:
                written = ";".join(row)
            if line_no in truth["extra_column"][-1:] or line_no in truth["missing_column"][-1:]:
                if len(truth["shift_expected"]) < 20_000:
                    truth["shift_expected"][line_no] = clean
            f.write(written + "\r\n")
            if line_no in truth["extra_column"][-1:] or line_no in truth["missing_column"][-1:]:
                prev_row = None  # on ne duplique pas une ligne décalée
            else:
                prev_row = row
                prev_tags = [k for k in ("invalid_date", "masked_value", "us_decimal", "accounting_neg")
                             if truth[k][-1:] == [line_no]]

    truth["rows"] = rows
    truth["seed"] = seed
    truth["file"] = csv_path.name
    with open(out.with_suffix(".truth.json"), "w", encoding="utf-8") as f:
        json.dump(truth, f)
    return truth


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", type=int, default=10_000)
    ap.add_argument("--out", type=Path, default=Path("samples/erp_10k"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--encoding", default="utf-8")
    a = ap.parse_args()
    t = generate(a.rows, a.out, a.seed, a.encoding)
    counts = {k: len(v) for k, v in t.items() if isinstance(v, list)}
    print(f"{a.out.with_suffix('.csv')} : {a.rows:,} lignes de données | anomalies injectées : {counts}")


if __name__ == "__main__":
    main()
