# FILTREmail - Classificateur d'emails Yandex

## Description

Classificateur automatique d'emails pour TWOWIN (СтройБразерс), distributeur de materiaux de construction a Ekaterinbourg.
Le script surveille plusieurs boites mail Yandex via IMAP, utilise DeepSeek AI pour classifier les emails entrants, et deplace automatiquement les commandes clients vers le dossier "Заявки".

## Stack technique

- **Langage** : Python 3.11
- **IA** : DeepSeek API (via SDK OpenAI)
- **Base de donnees** : MongoDB Atlas (pymongo)
- **Email** : IMAP (imap.yandex.ru) avec encodage UTF-7
- **Config** : python-dotenv
- **Deploiement** : Render (Background Worker)

## Structure du projet

```
email_classifier.py          # Script principal (v6 - version active, code non commente)
email_classifier copy.py     # Ancienne version v2 (commentee, a ignorer)
email_classifier copy 2.py   # Ancienne version (commentee, a ignorer)
requirements.txt             # Dependances Python
runtime.txt                  # Version Python pour Render
processed_emails.json        # Ancien stockage local (remplace par MongoDB)
.env                         # Variables d'environnement (NE PAS COMMITER)
```

**Note** : Le fichier `email_classifier.py` contient du code commente (lignes 1-660, anciennes versions v2-v5) suivi du code actif v6 (lignes 662-1352).

## Commandes

```bash
# Installation
pip install -r requirements.txt

# Lancement
python email_classifier.py

# Arret propre
Ctrl+C ou SIGTERM
```

## Variables d'environnement (.env)

| Variable | Description | Defaut |
|---|---|---|
| `MAILBOX_N_LOGIN` | Email de la boite N (1, 2, 3...) | - |
| `MAILBOX_N_PASSWORD` | Mot de passe app Yandex boite N | - |
| `DEEPSEEK_API_KEY` | Cle API DeepSeek | - |
| `DEEPSEEK_BASE_URL` | URL base DeepSeek | `https://api.deepseek.com` |
| `MONGO_URI` | URI de connexion MongoDB Atlas | - |
| `MONGO_DB` | Nom de la base MongoDB | `email_classifier` |
| `TEST_MODE` | Mode test (filtre par TEST_EMAIL) | `false` |
| `TEST_EMAIL` | Email de test | - |
| `DRY_RUN` | Simulation sans deplacement | `false` |
| `CHECK_INTERVAL` | Intervalle entre cycles (secondes) | `120` |

## Architecture du code (v6)

### Flux principal
1. **Boucle infinie** (`__main__`) : execute `run_cycle()` toutes les `CHECK_INTERVAL` secondes
2. **run_cycle()** : itere sur chaque boite mail configuree
3. **process_mailbox()** : pour chaque boite :
   - Connexion IMAP avec retry exponentiel (3 tentatives)
   - Connexion MongoDB fraiche (evite les timeouts)
   - Recherche des emails non lus du jour (`UNSEEN SINCE today`)
   - Filtrage des emails deja traites (MongoDB)
   - Classification par DeepSeek AI
   - Deplacement vers "Заявки" si commande client (confidence >= 0.7)

### Fonctions cles
- `classify_email()` : appel DeepSeek avec prompt de classification detaille
- `connect_imap()` : connexion avec retry exponentiel
- `get_processed_collection()` : connexion MongoDB fraiche a chaque cycle
- `mark_processed()` / `is_processed()` : tracking MongoDB avec TTL 30 jours
- `encode_imap_utf7()` : encodage des noms de dossiers cyrilliques pour IMAP

### Robustesse (v6)
- Reconnexion MongoDB a chaque cycle (pas de connexion globale)
- Retry IMAP exponentiel (3 tentatives, delai croissant)
- Timeout explicite IMAP (30s) et MongoDB (10s)
- Heartbeat log a chaque cycle
- Gestion SIGINT/SIGTERM pour arret propre
- Try/catch a tous les niveaux

## Conventions

- Logs en francais (v6) avec emojis pour lisibilite
- Prompts de classification en russe (langue metier)
- Collection MongoDB `processed_emails` avec index unique sur `uid` et TTL 30 jours
- UID unique = `mailbox_login:imap_uid` pour eviter les collisions entre boites
