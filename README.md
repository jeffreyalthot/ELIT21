# AutoEmploye IA Local (Terminal)

Un programme terminal (100% local) pour automatiser une partie de la prospection:

1. **Identifier des niches potentiellement rentables** à partir de recherches web.
2. **Trouver des emplacements publicitaires potentiels** (pages de contact/sponsor/partner, etc.) via analyse de liens sortants.
3. **Associer une publicité depuis une bibliothèque locale** pour préparer une prise de contact manuelle.

> ⚠️ Utilise uniquement des sources publiques. Respectez les CGU des sites, le `robots.txt`, et la législation locale (RGPD, ePrivacy, etc.).
> ⚠️ Le mode `full-auto` peut publier automatiquement des payloads vers un endpoint HTTP de déploiement publicitaire. Vérifiez vos autorisations légales/contractuelles avant activation.

## Prérequis

- Python 3.10+
- Aucune dépendance externe

## Utilisation rapide

### 0) Mode full auto par défaut (sans aucune option)

```bash
python3 auto_employe.py
```

Sans argument, le programme passe automatiquement en mode `auto-run` **infini** avec:
- découverte d'URLs activée,
- tentative IA locale (Ollama) activée avec fallback heuristique,
- payload d'insertion (`--auto-embed`) activé, avec insertion automatique sur **chaque** proposition en mode full-auto (fallback sélecteur `body` si aucun slot n'est détecté),
- publication distante activée (`--publish-live`) vers `http://localhost:8787/publish` par défaut,
- profil `--full-auto` appliqué (logs `DEBUG`, `--max-links 80`, `--discover-limit 40`, `--min-authorization-score 0`, `--interval 120`),
- URLs de départ par défaut: **29 URLs** (les 5 historiques + 24 nouvelles URLs marketing/tech pour une base plus large),
- résilience HTTP renforcée: en cas de `403 Forbidden`, l'URL est ignorée et l'automatisation continue sans interruption.

Si la bibliothèque pub est vide, une publicité de démarrage est créée automatiquement dans `data/ad_library.json`.

### 1) Menu interactif (options numérotées)

```bash
python3 auto_employe.py menu
```

Le menu permet de:
- rechercher des niches,
- analyser des emplacements pub,
- ajouter des publicités,
- lister les publicités,
- lancer l'automatisation en boucle.

### 2) Ajouter une publicité dans la bibliothèque

```bash
python3 auto_employe.py ads-add \
  --name "Zodiac Casino 125x125" \
  --niche "casino" \
  --embed-code '<!-- Embed Code Start --> ... <!-- Embed Code End -->'
```

Publicités stockées dans `data/ad_library.json`.

### 3) Lancer l'automatisation

Un cycle unique (avec logs détaillés):

```bash
python3 auto_employe.py auto-run https://example.com https://another-site.com --log-level DEBUG
```

Mode boucle infinie:

```bash
python3 auto_employe.py auto-run https://example.com --forever --interval 300
```

Si aucune URL n'est fournie à `auto-run`, les URLs de départ par défaut sont utilisées automatiquement.

Profil full auto explicite (active toutes les options d'automatisation):

```bash
python3 auto_employe.py auto-run --full-auto

# Optionnel: endpoint explicite de publication
python3 auto_employe.py auto-run --full-auto --publish-endpoint http://localhost:8787/publish

# Variante explicite: placement automatique en injection automatique
python3 auto_employe.py auto-run --auto-injection
```

Mode IA locale (si [Ollama](https://ollama.com) est installé localement):

```bash
python3 auto_employe.py auto-run https://example.com --use-local-ai --local-ai-model llama3.2
```

Sorties générées dans `outputs/`:
- `auto-placements-<timestamp>.json`
- `auto-placements-<timestamp>.csv`

## Commandes disponibles

- `niches` : recherche des niches rentables.
- `adspots` : repère des emplacements potentiels depuis des URLs.
- `ads-add` : ajoute une pub à la bibliothèque locale.
- `ads-list` : liste les publicités disponibles.
- `auto-run` : propose automatiquement des placements (un cycle ou boucle infinie), avec logs détaillés et moteur IA locale optionnel.
- `--auto-injection` / `--injection-automatique` : alias de `--auto-embed` pour générer les placements en mode injection automatique.
- `--publish-live` : pousse automatiquement chaque payload `auto-embed` vers un endpoint HTTP (POST JSON).
- `menu` : interface CLI interactive à choix numérotés.

## Notes

- Les scores sont heuristiques (mots-clés pondérés), donc **aide à la décision**, pas vérité absolue.
- Le mode infini sert à **surveiller** en continu et peut publier automatiquement les payloads quand `--publish-live` est actif.
- `auto-run` parallélise l'analyse de plusieurs URLs pour améliorer les performances.
- L'option `--use-local-ai` tente un matching via IA locale (Ollama), avec fallback heuristique si indisponible.
- Pour un usage pro, ajoutez:
  - anti-duplication des opportunités,
  - enrichissement CRM,
  - vérification légale automatisée,
  - workflow de validation humaine avant publication.

## Dépannage SSL (certificate verify failed)

Si vous voyez une erreur du type `certificate verify failed: unable to get local issuer certificate`, vous pouvez :

1. Fournir explicitement un bundle CA:

```bash
python3 auto_employe.py --cert-file /chemin/vers/cacert.pem auto-run https://example.com
```

2. En dernier recours uniquement (non recommandé en production), désactiver la vérification SSL:

```bash
python3 auto_employe.py --insecure-ssl auto-run https://example.com
```
