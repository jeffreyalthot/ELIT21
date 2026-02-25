# AutoEmploye IA Local (Terminal)

Un programme terminal (100% local) pour automatiser une partie de la prospection:

1. **Identifier des niches potentiellement rentables** à partir de recherches web.
2. **Trouver des emplacements publicitaires potentiels** (pages de contact/sponsor/partner, etc.) via analyse de liens sortants.

> ⚠️ Utilise uniquement des sources publiques. Respectez les CGU des sites, le `robots.txt`, et la législation locale (RGPD, ePrivacy, etc.).

## Prérequis

- Python 3.10+
- Aucune dépendance externe

## Utilisation rapide

### 1) Recherche de niches

```bash
python3 auto_employe.py niches "AI B2B SaaS newsletter" --limit 20
```

Sorties générées dans `outputs/`:
- `niches-<timestamp>.json`
- `niches-<timestamp>.csv`

### 2) Recherche d'opportunités publicitaires

```bash
python3 auto_employe.py adspots https://example.com https://another-site.com --max-links 50
```

Sorties générées dans `outputs/`:
- `adspots-<timestamp>.json`
- `adspots-<timestamp>.csv`

## Idée de workflow “employé automatique”

1. Lancer `niches` pour repérer des marchés prioritaires.
2. Garder les résultats avec score élevé.
3. Lancer `adspots` sur des sites ciblés de ces niches.
4. Contacter manuellement les pages classées (score élevé) avec une offre claire.

## Notes

- Les scores sont heuristiques (mots-clés pondérés), donc **aide à la décision**, pas vérité absolue.
- Pour un usage pro, ajoutez:
  - rotation de sources,
  - anti-duplication,
  - enrichissement CRM,
  - vérification légale automatisée.
