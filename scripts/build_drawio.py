"""Génère docs/architecture.drawio (4 pages) puis l'exporte en PNG via draw.io CLI.

Les schémas sont décrits en Python (boîtes sur une grille, flèches orthogonales)
pour rester lisibles et reproductibles. Usage :  python scripts/build_drawio.py
"""

from __future__ import annotations

import subprocess
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "architecture.drawio"
IMG = ROOT / "docs" / "img"
DRAWIO = r"C:\Users\Patrice DUCLOS\AppData\Local\Programs\draw.io\draw.io.exe"

# Palette (fond blanc, lisible imprimé) : une couleur par rôle
C = {
    "source": ("#eef2f7", "#5b6b82"),
    "ingest": ("#e4eafb", "#2f55c9"),
    "process": ("#fbefd9", "#9a6200"),
    "fix": ("#e1f2e8", "#1f7a4d"),
    "ui": ("#f3e8fb", "#7b3fb0"),
    "serve": ("#e6f4f7", "#1b6f80"),
    "warn": ("#fbe3e1", "#b3261e"),
    "note": ("#ffffff", "#9aa3b5"),
    "lane": ("#f7f8fa", "#c9cfd9"),
}
FONT = "fontFamily=Helvetica;fontSize=13;"


class Page:
    def __init__(self, name: str, w: int, h: int):
        self.name, self.w, self.h = name, w, h
        self.cells: list[str] = []
        self.n = 0

    def _id(self) -> str:
        self.n += 1
        return f"{self.name[:3].lower()}{self.n}"

    def box(self, x, y, w, h, text, role="process", bold_first=True, align="center", size=13, dashed=False) -> str:
        fill, stroke = C[role]
        cid = self._id()
        lines = text.split("\n")
        if bold_first and len(lines) > 1:
            html = f"<b>{escape(lines[0])}</b><br>" + "<br>".join(
                f'<font style="font-size:{size - 2}px" color="#3a4354">{escape(t)}</font>' for t in lines[1:])
        elif bold_first:
            html = f"<b>{escape(lines[0])}</b>"
        else:
            html = "<br>".join(escape(t) for t in lines)
        style = (f"rounded=1;whiteSpace=wrap;html=1;arcSize=8;fillColor={fill};strokeColor={stroke};"
                 f"strokeWidth=1.5;align={align};verticalAlign=middle;spacing=8;{FONT}fontSize={size};"
                 + ("dashed=1;" if dashed else ""))
        self.cells.append(
            f'<mxCell id="{cid}" value="{escape(html)}" style="{style}" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return cid

    def lane(self, x, y, w, h, title) -> None:
        fill, stroke = C["lane"]
        cid = self._id()
        style = (f"rounded=1;whiteSpace=wrap;html=1;arcSize=3;fillColor={fill};strokeColor={stroke};dashed=1;"
                 f"verticalAlign=top;align=left;spacingLeft=12;spacingTop=6;{FONT}fontSize=12;fontColor=#566074;")
        self.cells.append(
            f'<mxCell id="{cid}" value="{escape(title)}" style="{style}" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')

    def text(self, x, y, w, h, text, size=13, color="#141c2b", bold=False, align="left") -> None:
        cid = self._id()
        val = f"<b>{escape(text)}</b>" if bold else escape(text).replace("\n", "<br>")
        style = (f"text;html=1;whiteSpace=wrap;align={align};verticalAlign=middle;{FONT}"
                 f"fontSize={size};fontColor={color};")
        self.cells.append(
            f'<mxCell id="{cid}" value="{escape(val)}" style="{style}" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')

    def arrow(self, src, dst, label="", exit=None, entry=None, dashed=False, color="#566074", points=()) -> None:
        cid = self._id()
        style = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;strokeWidth=1.6;"
                 f"strokeColor={color};{FONT}fontSize=11;fontColor=#566074;labelBackgroundColor=#ffffff;")
        if dashed:
            style += "dashed=1;"
        if exit:
            style += f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;"
        if entry:
            style += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
        pts = ""
        if points:
            pts = "<Array as=\"points\">" + "".join(f'<mxPoint x="{px}" y="{py}"/>' for px, py in points) + "</Array>"
        self.cells.append(
            f'<mxCell id="{cid}" value="{escape(label)}" style="{style}" edge="1" parent="1" source="{src}" target="{dst}">'
            f'<mxGeometry relative="1" as="geometry">{pts}</mxGeometry></mxCell>')

    def xml(self) -> str:
        body = "".join(self.cells)
        return (f'<diagram name="{escape(self.name)}" id="{self.name[:3].lower()}">'
                f'<mxGraphModel dx="{self.w}" dy="{self.h}" grid="1" gridSize="10" guides="1" tooltips="1" '
                f'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{self.w}" pageHeight="{self.h}" '
                f'background="#ffffff" math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
                f'{body}</root></mxGraphModel></diagram>')


def page_pipeline() -> Page:
    p = Page("1 - Pipeline", 1500, 570)
    p.text(30, 16, 1400, 34, "Csvtraitement — du fichier ERP « sale » au fichier corrigé, en 6 étapes", 20, bold=True)
    p.text(30, 48, 1400, 24, "Chaque étape relit le fichier par blocs de 10 000 lignes (csv.reader) ; aucune correction n'est écrite sans validation.", 13, "#566074")
    W, H, Y = 205, 92, 120
    xs = [30, 270, 510, 750, 990, 1230]
    titles = ["1 · Entrée", "2 · Détection", "3 · Analyse", "4 · Correction", "5 · Validation", "6 · Export"]
    for x, t in zip(xs, titles):
        p.text(x, Y - 34, W, 24, t, 14, "#2f55c9", bold=True, align="center")
    a1 = p.box(xs[0], Y, W, H, "CSV · TXT · TSV\nlecture native (csv.reader)", "source")
    a2 = p.box(xs[0], Y + 130, W, H, "XLSX · XLS · ODS\nJSON · Parquet\nconvertis en CSV temporaire", "source")
    b1 = p.box(xs[1], Y, W, H, "Encodage + séparateur\nBOM / chardet · score\nmoyenne / (1 + écart-type)", "ingest")
    b2 = p.box(xs[1], Y + 130, W, H, "Profilage des colonnes\n9 types · échantillon\n1 000 premières + 1 000 aléatoires", "ingest")
    c1 = p.box(xs[2], Y, W, H, "Décalages de colonnes\nligne + colonne fautive\nextra / missing / type", "process")
    c2 = p.box(xs[2], Y + 130, W, H, "Score qualité 0-100 (A→F)\nremplissage · types · formats\noutliers IQR · diversité", "process")
    c3 = p.box(xs[2], Y + 260, W, H, "Doublons exacts + flous\nhash · SequenceMatcher ≥ 85 %\nblocage multi-clés", "process")
    d1 = p.box(xs[3], Y, W, H, "Réalignement\nrecolle un texte coupé par « ; »\nou insère la cellule manquante", "fix")
    d2 = p.box(xs[3], Y + 130, W, H, "Normalisation\ndates → ISO · 00.00.0000 → vide\nFR/US → float · (1,5) → -1.5", "fix")
    d3 = p.box(xs[3], Y + 260, W, H, "Valeurs masquées\n#REF! · N/A · NULL → vide\nmasquage PII optionnel", "fix")
    e1 = p.box(xs[4], Y + 65, W, H + 40, "Revue humaine\nGUI Tkinter 8 onglets\npreview avant / après\nundo / redo", "ui")
    e2 = p.box(xs[4], Y + 260, W, H, "ou CLI / batch\nrapport JSON\n(automatisation)", "ui")
    f1 = p.box(xs[5], Y, W, H, "XLSX annoté\ncellules corrigées en couleur\n+ onglet rapport", "serve")
    f2 = p.box(xs[5], Y + 130, W, H, "CSV · JSON · ODS · Parquet\nséparateur configurable", "serve")
    f3 = p.box(xs[5], Y + 260, W, H, "Écriture via fichier\ntemporaire local\n(lecteurs réseau / cloud)", "serve")
    p.arrow(a1, b1); p.arrow(a2, b1, exit=(1, 0.5), entry=(0, 0.75))
    p.arrow(b1, b2, exit=(0.5, 1), entry=(0.5, 0))
    p.arrow(b2, c1, exit=(1, 0.5), entry=(0, 0.5), points=[(xs[1] + W + 18, Y + 130 + H / 2), (xs[1] + W + 18, Y + H / 2)])
    p.arrow(b2, c2); p.arrow(b2, c3, exit=(1, 0.5), entry=(0, 0.5), points=[(xs[1] + W + 18, Y + 130 + H / 2), (xs[1] + W + 18, Y + 260 + H / 2)])
    p.arrow(c1, d1); p.arrow(c1, d2, exit=(1, 0.75), entry=(0, 0.5), points=[(xs[2] + W + 18, Y + 69), (xs[2] + W + 18, Y + 130 + H / 2)])
    p.arrow(c1, d3, exit=(1, 0.75), entry=(0, 0.5), points=[(xs[2] + W + 18, Y + 69), (xs[2] + W + 18, Y + 260 + H / 2)])
    p.arrow(d1, e1, exit=(1, 0.5), entry=(0, 0.25)); p.arrow(d2, e1, exit=(1, 0.5), entry=(0, 0.6))
    p.arrow(d3, e2)
    p.arrow(e1, f1, exit=(1, 0.3), entry=(0, 0.5)); p.arrow(e1, f2, exit=(1, 0.7), entry=(0, 0.5))
    p.arrow(e2, f3)
    p.text(30, 492, 1440, 60,
           "Mesuré sur un export ERP synthétique de 100 000 lignes (11 colonnes, 7 types d'anomalies injectées) : 100 % des décalages trouvés, 0 faux positif ; "
           "100 % des lignes décalées retrouvent leurs valeurs d'origine ; 100 % des dates invalides, valeurs masquées, montants US et parenthèses comptables traités ; "
           "résultats identiques à 1 000 000 de lignes. Chiffres et limites : docs/PORTFOLIO.md.", 12, "#566074")
    return p


def page_memoire() -> Page:
    p = Page("2 - Streaming et memoire", 1400, 620)
    p.text(30, 16, 1340, 34, "Ce qui est lu en flux, et ce qui reste en mémoire", 20, bold=True)
    p.text(30, 48, 1340, 24, "Lecture : toujours par blocs de 10 000 lignes. Mais certains résultats sont conservés pour la revue humaine (preview, undo) : c'est le vrai coût mémoire.", 13, "#566074")
    p.lane(30, 90, 640, 400, "Flux (mémoire constante)")
    p.lane(700, 90, 670, 400, "Conservé en mémoire (croît avec le fichier)")
    src = p.box(60, 140, 180, 80, "Fichier source\n142 Mo · 1 M lignes", "source")
    chunk = p.box(300, 140, 160, 80, "Bloc de\n10 000 lignes", "ingest")
    det = p.box(500, 120, 150, 60, "Détection\néchantillon 2 000", "ingest", size=12)
    ana = p.box(500, 200, 150, 60, "Analyse\ncompteurs", "process", size=12)
    q = p.box(500, 280, 150, 60, "Qualité / stats\nagrégats", "process", size=12)
    exp = p.box(500, 380, 150, 80, "Export CSV / XLSX\nconstant_memory", "serve", size=12)
    p.arrow(src, chunk); p.arrow(chunk, det, exit=(1, 0.3), entry=(0, 0.5)); p.arrow(chunk, ana, exit=(1, 0.7), entry=(0, 0.5))
    p.arrow(chunk, q, exit=(0.5, 1), entry=(0, 0.5), points=[(380, 310)])
    p.arrow(chunk, exp, exit=(0.3, 1), entry=(0, 0.5), points=[(348, 420)])
    fix = p.box(730, 140, 300, 90, "corrected_lines + corrections\nchaque ligne modifiée et chaque cellule corrigée\n(nécessaire pour preview, undo, surlignage)", "warn", size=12)
    dd = p.box(730, 270, 300, 90, "Index de déduplication\nclé normalisée + cellules de chaque ligne\n(exacts puis candidats flous)", "warn", size=12)
    sh = p.box(730, 400, 300, 70, "Détails des anomalies\nplafonnés à 50 000", "process", size=12)
    p.arrow(ana, sh, exit=(1, 0.5), entry=(0, 0.5), points=[(690, 230), (690, 435)])
    p.text(730, 236, 300, 30, "alimenté à chaque bloc par la passe de correction", 11, "#566074")
    p.text(730, 366, 300, 30, "alimenté à chaque bloc par la recherche de doublons", 11, "#566074")
    p.box(1060, 140, 280, 90, "Pic mesuré (dédup + XLSX)\n10 K : 0,11 Go · 100 K : 0,52 Go\n500 K : 2,2 Go", "note", size=12)
    p.box(1060, 270, 280, 90, "1 M lignes (sans dédup)\n1,5 Go après corrections\npic 2,4 Go avec export XLSX", "note", size=12)
    p.box(1060, 400, 280, 70, "Piste\nmode batch « sans revue » :\nne garder que les compteurs", "note", size=12)
    p.text(30, 510, 1340, 80,
           "Les chiffres de pic mémoire sont ceux de scripts/benchmark.py (peak working set Windows, Python 3.12) sur les fichiers synthétiques ; "
           "voir le tableau complet dans docs/PORTFOLIO.md.", 12, "#566074")
    return p


def page_dedup() -> Page:
    p = Page("3 - Doublons flous", 1400, 600)
    p.text(30, 16, 1340, 34, "Doublons flous : 240 s → 2,0 s sur 10 000 lignes", 20, bold=True)
    p.text(30, 48, 1340, 24, "Comparer toutes les paires coûte n²/2. Le « blocage » ne compare que des lignes qui ont une chance d'être des doublons.", 13, "#566074")
    p.lane(30, 90, 650, 420, "Avant : bloc = 3 premiers caractères de la clé")
    p.lane(710, 90, 660, 420, "Après : blocage multi-clés + préfiltre")
    k = p.box(60, 140, 280, 80, "Clé de la ligne (toutes colonnes)\n« cmd4500000177|c40298|industries… »", "source", size=12)
    b = p.box(60, 260, 280, 80, "Bloc « cmd »\ntoutes les lignes y tombent", "warn", size=12)
    n = p.box(380, 260, 270, 80, "10 000 lignes : 49,6 M paires\n(non exécuté) · 1 000 lignes :\n494 515 paires, 281 s", "warn", size=12)
    r = p.box(380, 390, 270, 80, "Clé sans n° de commande : 240 s\n552 508 paires · 14 / 15 trouvés\n(version d'origine, 10 000 lignes)", "warn", size=12)
    p.arrow(k, b); p.arrow(b, n); p.arrow(n, r)
    k2 = p.box(740, 140, 290, 80, "Colonnes discriminantes\ncardinalité ≥ 30 % : n° commande,\ne-mail, téléphone…", "ingest", size=12)
    b2 = p.box(740, 260, 290, 80, "Un bloc par valeur exacte\nun quasi-doublon partage forcément\nles cellules non modifiées", "fix", size=12)
    f2 = p.box(1060, 260, 280, 80, "Préfiltre difflib\nreal_quick_ratio, quick_ratio :\nbornes sup., même décision", "fix", size=12)
    r2 = p.box(1060, 390, 280, 80, "2,0 s pour 10 000 lignes\n9 089 comparaisons · 15 / 15\n0 faux positif", "fix", size=12)
    p.arrow(k2, b2); p.arrow(b2, f2); p.arrow(f2, r2)
    p.text(740, 400, 290, 70, "Repli automatique sur l'ancien préfixe\nsi aucune colonne n'est discriminante.", 12, "#566074")
    p.text(30, 525, 1340, 50, "Équivalence du préfiltre vérifiée par tests/test_pipeline.py (3 000 paires aléatoires × 3 seuils : décision identique à ratio()).", 12, "#566074")
    return p


def page_deploy() -> Page:
    p = Page("4 - Deploiement batch", 1400, 560)
    p.text(30, 16, 1340, 34, "Déploiement : un traitement par lots, pas un serveur", 20, bold=True)
    p.text(30, 48, 1340, 24, "Sur un poste : l'interface Tkinter. Sur un serveur : la même CLI, planifiée chaque nuit par un CronJob Kubernetes.", 13, "#566074")
    erp = p.box(40, 150, 200, 90, "ERP (SAP…)\nexport planifié\nCSV / XLSX", "source")
    sftp = p.box(290, 150, 200, 90, "Dépôt SFTP\nou partage réseau", "source")
    pvc = p.box(560, 150, 260, 90, "PersistentVolumeClaim\n/data/in → /data/out\nRWX (Azure Files) ou RWO local", "ingest", size=12)
    cj = p.box(560, 300, 260, 110, "CronJob nettoyage-exports\n02:00 Europe/Paris · Forbid\nnon-root · rootfs lecture seule\nlimite mémoire 2 Gi", "process", size=12)
    img = p.box(900, 300, 220, 110, "Image csvtraitement\npython:3.12-slim\nCLI seule (sans Tk)\nENTRYPOINT main.py cli", "serve", size=12)
    out = p.box(900, 150, 220, 90, "Fichiers corrigés\n+ rapport.json", "fix")
    bi = p.box(1170, 150, 190, 90, "Power BI / Excel\nentrepôt de données", "ui")
    poste = p.box(40, 330, 450, 80, "Poste analyste : python main.py (GUI)\nrevue humaine, undo / redo, export XLSX annoté", "ui", size=12)
    p.arrow(erp, sftp); p.arrow(sftp, pvc); p.arrow(pvc, cj, label="monte /data", exit=(0.5, 1), entry=(0.5, 0))
    p.arrow(img, cj, label="image", exit=(0, 0.5), entry=(1, 0.5))
    p.arrow(pvc, out, label="/data/out", exit=(1, 0.5), entry=(0, 0.5))
    p.arrow(out, bi)
    p.text(560, 430, 800, 80,
           "Overlays kustomize : local (Docker Desktop, toutes les 15 min) et cloud (registre privé, Azure Files 50 Gi). "
           "Rendu vérifié avec « kubectl kustomize » ; non appliqué sur un cluster, image non construite dans cette session (voir PORTFOLIO.md).", 12, "#566074")
    return p


def main() -> None:
    pages = [page_pipeline(), page_memoire(), page_dedup(), page_deploy()]
    xml = ('<mxfile host="Electron" agent="scripts/build_drawio.py" version="24.0.0">'
           + "".join(pg.xml() for pg in pages) + "</mxfile>")
    OUT.write_text(xml, encoding="utf-8")
    IMG.mkdir(parents=True, exist_ok=True)
    for i in range(len(pages)):
        png = IMG / f"architecture-{i + 1}.png"
        subprocess.run([DRAWIO, "-x", "-f", "png", "--scale", "2", "--border", "20", "-p", str(i + 1),
                        "-o", str(png), str(OUT)], check=True, capture_output=True)
        print(png.name)


if __name__ == "__main__":
    main()
