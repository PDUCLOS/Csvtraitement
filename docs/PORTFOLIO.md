# Csvtraitement — fiche portfolio (faits vérifiés)

> Fiche destinée à l'intégration dans pduclos.github.io (slug `csvtraitement`).
> Tous les chiffres ci-dessous ont été **mesurés le 27/10/2026** sur la machine de démonstration
> (AMD Ryzen 9 3900X, 32 Go, Windows 11, Python 3.12), branche `feat/vitrine-csvtraitement`.
> Les mesures tournaient sur **un cœur**, pendant que d'autres traitements occupaient la machine :
> les temps sont des ordres de grandeur, pas des records.
> **Aucune donnée d'entreprise** : tous les fichiers sont synthétiques (`scripts/generate_sample.py`, graine 42).

## En une phrase

Application Python (interface Tkinter + CLI) qui détecte et corrige les défauts des exports ERP —
colonnes décalées, décimales FR/US, dates invalides, valeurs masquées, doublons, données personnelles —
avec revue humaine avant export ; testée sur 1 000 000 de lignes avec vérité terrain.

## Chiffres clés (pour les KPI du portfolio)

| KPI proposé | Valeur | Source |
|---|---|---|
| Lignes traitées | **1 000 000** en **63,5 s** (détection → analyse → corrections → export CSV) | `bench_out/results.jsonl`, run `final` 1 M |
| Décalages de colonnes | **100 %** trouvés, **0** faux positif (15 046 / 15 046 à 1 M) | idem |
| Lignes décalées restituées à l'identique | **100 %** (15 046 / 15 046, aux espaces près) — **32 %** avant correctif | runs `final` et `legacy-realign` |
| Doublons flous | **240 s → 2,0 s** sur 10 000 lignes (×120), 15/15 trouvés (14/15 avant) | `bench_out/dedup_legacy.jsonl` |
| Tests | **84** pytest verts (59 au clonage, tous verts aussi) | `pytest tests -q` |

Proposition de remplacement des KPI actuels de `projects.py` :
`("1 M", "lignes traitées en 63 s")`, `("100 %", "décalages trouvés, 0 faux positif")`,
`("×120", "doublons flous plus rapides")`, `("84", "tests")`.

## Vérification des affirmations de la fiche actuelle (`build/projects.py`)

| Affirmation | Verdict | Détail |
|---|---|---|
| « 500 K+ lignes traitées » | ✅ reproduit | 500 000 et 1 000 000 lignes traitées de bout en bout |
| « 59 tests » | ✅ reproduit | 59 / 59 verts au clonage ; 84 aujourd'hui |
| « 6 formats lus / écrits » | ⚠️ **écart** | **6 lus** (CSV, XLSX, XLS, ODS, JSON, Parquet), **5 écrits** (pas de XLS). Écrire « 6 formats lus, 5 écrits ». Lecture XLS **non testée** (aucun fichier .xls disponible) ; les 5 autres formats : export puis relecture testés |
| « Score qualité A→F » | ✅ | mais voir limite : 92,7/100 « A » sur un fichier dont 6,5 % des lignes sont défectueuses |
| « Streaming par blocs de 10 000 lignes ; pas de saturation mémoire » | ⚠️ **à nuancer** | la *lecture* est en flux, mais les corrections sont gardées en mémoire (preview / undo) : pic 0,52 Go à 100 K, 2,2 Go à 500 K, 2,4 Go à 1 M |
| « Parquet lu en iter_batches » | ✅ | `utils/parquet_reader.py:89` |
| « Séparateur : score moyenne / (1 + écart-type) » | ✅ | `core/detector.py` |
| « Doublons approchés SequenceMatcher > 85 % avec blocage par préfixe » | ⚠️ corrigé | le blocage par préfixe rendait la recherche quadratique ; remplacé par un blocage multi-clés (voir ci-dessous) |
| « Masquage e-mail, téléphone, IBAN, NIR » | ✅ | détection testée (`tests/test_pipeline.py`) ; colonnes Email/Téléphone de l'export détectées automatiquement |
| « Interface Tkinter 8 onglets, undo / redo » | ✅ | captures réelles `docs/img/gui-*.png` |
| « 27 modules journalisés » | ✅ | 27 fichiers `.py` sur 37 utilisent `logging.getLogger` (commit d'origine) |
| « Humain dans la boucle : aucune correction sans validation » | ✅ GUI / ⚠️ CLI | vrai dans l'interface ; en CLI `--fix`, les corrections sont appliquées directement (rapport JSON) |
| README « Licence MIT » | ❌ corrigé | le fichier `LICENSE` est « tous droits réservés » ; README aligné |

## Tableau de mesures

### Qualité des contrôles (vérité terrain)

Export synthétique : 11 colonnes façon SAP (n° commande, client, raison sociale, 2 dates, montant FR,
quantité, statut, e-mail, téléphone, ville), séparateur `;`, décimales FR, 8 types d'anomalies injectées.

| Anomalie injectée (fichier 100 000 lignes) | Injectées | Résultat | Faux positifs |
|---|---:|---|---:|
| Colonne en trop (`;` non échappé dans la raison sociale) | 990 | 990 détectées, type correct, morceaux recollés | 0 |
| Colonne manquante (quantité perdue) | 513 | 513 détectées, cellule vide insérée au bon endroit | 0 |
| Ligne décalée restituée à l'identique | 1 503 | 1 503 (100 %) | — |
| Date invalide `00.00.0000` | 974 | 974 vidées | 0 |
| Valeur masquée (`#REF!`, `N/A`, `NULL`, `-`, `??`) | 994 | 994 vidées | 0 |
| Montant au format US | 1 963 | 1 963 au bon format en sortie (4 l'étaient déjà, ex. `912.79`, vérifié à la main) | 0 |
| Parenthèses comptables `(1.234,56)` | 520 | 520 → négatif | 0 |
| Doublon exact | 464 | 464 | 0 |
| Quasi-doublon (2 lettres inversées) | 197 | 197 | 0 |

Taux identiques à 10 000, 500 000 et 1 000 000 lignes (doublons non mesurés à 1 M).
Colonnes PII détectées automatiquement : `Email_Contact` (EMAIL), `Telephone` (PHONE_FR), aucune autre.

### Temps et mémoire (secondes ; un processus par taille)

| Lignes | Fichier | Détection | Analyse | Corrections | Export CSV | **Cœur** | Score qualité | Export XLSX | Doublons | Pic mémoire |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 000 | 1,4 Mo | 0,23 | 0,40 | 0,26 | 0,07 | **0,97** | 0,61 | 1,77 | 3,54 | 110 Mo |
| 100 000 | 14 Mo | 0,46 | 2,98 | 3,18 | 0,66 | **7,28** | 6,12 | 13,8 | 24,0 | 516 Mo |
| 500 000 | 71 Mo | 1,41 | 15,2 | 16,1 | 2,58 | **35,3** | 31,3 | 69,2 | 39,8 | 2,22 Go |
| 1 000 000 | 142 Mo | 2,57 | 29,3 | 25,2 | 6,51 | **63,5** | 52,3 | 133,8 | non mesuré | 2,42 Go |

Débit cœur : 10 300 à 15 700 lignes/s. Mémoire après corrections (avant XLSX) : 73 Mo (10 K),
203 Mo (100 K), 767 Mo (500 K), 1,47 Go (1 M).

### Doublons flous : version d'origine vs corrigée (mêmes fichiers)

| Cas | Paires comparées (origine) | Origine | Corrigée | Quasi-doublons trouvés (origine → corrigée) |
|---|---:|---:|---:|---|
| 1 000 lignes, clé = toutes colonnes (défaut CLI) | 494 515 | 281 s | 0,59 s | 1/1 → 1/1 |
| 10 000 lignes, clé = colonnes 1-10 | 552 508 | 240 s | 1,98 s | 14/15 → 15/15 |
| 10 000 lignes, clé = toutes colonnes | 49 595 820 | **non exécuté** (≈ 100× le cas 1 000) | 3,0 s | — → 15/15 |

Note : le message du commit `731f8c6` cite « 264 s → 3,7 s » : c'était une mesure exploratoire
(générateur antérieur, autre clé). Les chiffres de référence sont ceux de ce tableau.

## Problèmes trouvés et corrigés (commits de la branche)

1. **CLI inutilisable** — `run_single` lisait `sheet_name` avant affectation : toute analyse non silencieuse levait `UnboundLocalError` (`790985e`).
2. **Doublons flous quadratiques** — blocage sur 3 caractères de clé = un seul bloc dès que la 1re colonne a un préfixe constant (`731f8c6`).
3. **Perte de texte au réalignement** — « Martinez;SARL » perdait « SARL » : 32 % → 100 % de lignes restituées (`974fc20`).
4. **Comparaison faussée par les clés non uniques** — k-ième occurrence toujours comparée à la 1re ; occurrences surnuméraires jamais « supprimées » (`924df2c`).
5. **`--compare` / `--reconcile` incomplets** — clé primaire et colonnes de montant non exposées : 0/2 écarts trouvés → 2/2 (`790985e`).
6. **Valeurs masquées jamais nettoyées** — cases GUI reliées à rien, `#REF!` exporté tel quel : 100 % vidées aujourd'hui (`9c269b7`).
7. **README** — licence MIT annoncée, fichier LICENSE « tous droits réservés » (`f7be713`).

## Extraits de code exacts

Préfiltre de similarité (`core/dedup.py`) — mêmes décisions que `ratio()`, testé sur 3 000 paires × 3 seuils :

```python
    sm = SequenceMatcher(None, s1, s2)
    if threshold > 0.0:
        bound = sm.real_quick_ratio()
        if bound < threshold:
            return bound
        bound = sm.quick_ratio()
        if bound < threshold:
            return bound
    return sm.ratio()
```

Recollage d'un champ texte coupé par un séparateur non échappé (`core/fixer.py`, `realign_row`) :

```python
        if (merge_text and 0 < col < len(cells)
                and profiles[col - 1].dominant_category == 'text'):
            # Cas typique ERP : séparateur non échappé dans un champ texte
            # ("Dupont;Fils") -> on recolle les deux morceaux au lieu d'en perdre un.
            merged = cells[col - 1] + separator + cells[col]
            new_cells = cells[:col - 1] + [merged] + cells[col + 1:]
```

## Déploiement

L'outil n'est **pas un service** (pas d'API) : application de bureau + CLI. Livré :
`Dockerfile` (python:3.12-slim, CLI seule, non root, `ENTRYPOINT main.py cli`) et `deploy/k8s`
(kustomize : base `Namespace` + `PersistentVolumeClaim` + **`CronJob`** nocturne 02:00 Europe/Paris,
`concurrencyPolicy: Forbid`, rootfs en lecture seule, limite 2 Gi ; overlays `local` et `cloud`/Azure Files).

- ✅ `kubectl kustomize deploy/k8s/overlays/{local,cloud}` : rendu OK, 3 objets chacun.
- ✅ La commande exacte de l'image (`--batch /data/in --fix -o /data/out --format csv --report …`) a tourné en local : fichiers corrigés + `rapport.json`.
- ❌ **Non vérifié** : `docker build` / `docker run` (VM Docker Desktop limitée à 6 Go et saturée par un autre projet) ; aucun cluster Kubernetes disponible (`kubectl apply` non exécuté). La limite 2 Gi est déduite des pics mesurés, pas d'un Pod réel.

## Limites (à afficher honnêtement)

- **Mémoire** : la lecture est en flux, mais `corrected_lines` + `corrections` gardent chaque ligne modifiée ; la normalisation des dates touchant quasiment toutes les lignes, la mémoire croît linéairement (1,47 Go après corrections à 1 M). Piste : mode batch « compteurs seuls ».
- **Ambiguïté** : une cellule manquante voisine d'une valeur masquée ne peut pas être placée avec certitude (4 cas sur 1 503 observés lors d’un run exploratoire à 100 K) ; le résultat final est juste (deux cellules vides) mais la position du trou est arbitraire.
- **Espaces** : le lecteur supprime l'espace qui suit un séparateur (`skipinitialspace=True`) ; « Martinez; SARL » devient « Martinez;SARL ».
- **Score qualité indulgent** : 92,7/100 (A) sur un fichier avec 6,5 % de lignes défectueuses ; suivre plutôt le taux de lignes non conformes.
- **Doublons** : l'index garde toutes les lignes en mémoire ; non mesuré à 1 M. Les colonnes discriminantes (cardinalité ≥ 30 %) dépendent du fichier.
- **Données** : uniquement synthétiques ; pas testé sur de vrais exports (encodages mixtes, en-têtes multiples, sous-totaux).
- **XLS (ancien Excel)** : lecture non testée.

## Médias produits

| Fichier | Contenu |
|---|---|
| `docs/index.html` | vitrine GitHub Pages (problème, démo, fonctionnement, architecture, mesures, démarche, déploiement, limites, glossaire) |
| `docs/video/csvtraitement-promo.mp4` | vidéo 1080p, **113,7 s, 11,9 Mo**, narration Hortense, fond sonore synthétisé (`scripts/promo_video.py`) |
| `docs/video/poster.png` | affiche de la vidéo |
| `docs/architecture.drawio` | 4 pages : pipeline, mémoire, doublons flous, déploiement (`scripts/build_drawio.py`) |
| `docs/img/architecture-1..4.png` | exports PNG draw.io (vérifiés visuellement) |
| `docs/img/gui-*.png` | 8 captures **réelles** de l'interface Tkinter sur l'export de démo (`scripts/capture_gui.py`) |
| `docs/img/avant-apres.png` | ligne 30 avant / après correction (recadrage des captures réelles) |

## Reproduire

```bash
py -3.12 -m venv .venv && .venv/Scripts/pip install -r requirements.txt pyarrow odfpy psutil pillow
.venv/Scripts/python -m pytest tests -q                       # 84 tests
bash scripts/run_bench.sh                                     # ~25 min : 10 K -> 1 M + dédup origine/corrigée
python main.py --file samples/erp_demo_1k.csv                 # interface sur la démo
```
