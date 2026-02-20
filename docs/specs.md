# Spécifications - CSV Traitement

## Vue d'ensemble

CSV Traitement est un outil de diagnostic et correction de fichiers CSV issus d'exports ERP (SAP, etc.) présentant des anomalies de structure et de formatage.

## Cas d'usage principal

1. L'utilisateur charge un fichier CSV
2. L'outil détecte automatiquement : encodage, séparateur, nombre de colonnes, types de données
3. L'outil analyse les décalages et anomalies
4. Un dashboard affiche le diagnostic complet
5. L'utilisateur valide les corrections proposées
6. L'export XLSX typé est généré

## Algorithmes clés

### Détection du séparateur

Score = moyenne_occurrences / (1 + écart_type)

- Plus le séparateur est fréquent ET régulier, plus son score est élevé
- Testé sur les 200 premières lignes

### Profiling de colonnes

Pour chaque colonne, sur les lignes conformes :
- Compter les occurrences de chaque type (DATE, INTEGER, DECIMAL, TEXT_SHORT, TEXT_LONG, EMPTY)
- Le type dominant = celui avec le plus d'occurrences (hors EMPTY)
- La confiance = occurrences_dominant / total

### Détection de décalage

1. Lignes avec nb_colonnes ≠ attendu → décalage structurel
2. Pour localiser : parcourir cellule par cellule en comparant le type détecté avec le profil attendu
3. Quand le type diverge → colonne de début du décalage

### Réalignement

- Colonne en trop (extra_column) : supprimer la cellule excédentaire
- Colonne manquante (missing_column) : insérer une cellule vide à l'emplacement détecté
- Erreur de type (type_mismatch) : correction du type uniquement

## Formats supportés

### Dates
| Format | Exemple |
|--------|---------|
| DD.MM.YYYY | 15.03.1985 |
| DD/MM/YYYY | 15/03/1985 |
| DD-MM-YYYY | 15-03-1985 |
| YYYY-MM-DD | 1985-03-15 |
| Invalide | 00.00.0000 → vide |

### Nombres décimaux
| Format | Exemple | Résultat |
|--------|---------|----------|
| FR (milliers=.) | 1.987.239,99 | 1987239.99 |
| FR simple | 987,50 | 987.50 |
| US (milliers=,) | 1,987,239.99 | 1987239.99 |
| US simple | 987.50 | 987.50 |

## Contraintes techniques

- Fichiers jusqu'à 500K+ lignes
- Traitement par chunks de 10 000 lignes
- Profiling sur échantillon (1000 premières + 1000 aléatoires)
- Export XLSX jusqu'à 1M lignes, CSV au-delà
- Interface Tkinter (pas de dépendance web)

## Workflow utilisateur

```
[Charger] → [Diagnostic auto] → [Analyser décalages] → [Preview] → [Valider options] → [Corriger] → [Exporter]
```

L'utilisateur valide à chaque étape. Aucune correction n'est appliquée sans validation explicite.
