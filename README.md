# e208-cost-tracker : suivi des recharges d'une e-208 et coût EDF Tempo

Enregistre automatiquement chaque recharge et chaque trajet d'une **Peugeot e-208** avec [psa_car_controller](https://github.com/flobz/psa_car_controller) (psacc), puis les pousse toutes les heures dans un **Google Sheet** qui calcule le coût réel en **EDF Tempo** et le coût aux 100 km.

| Service | Rôle |
| :--- | :--- |
| `psacc` | Image officielle `flobz/psa_car_controller` : connexion au compte MyPeugeot, enregistrement des positions, charges et courbes de charge dans `psacc/config/info.db` (option `-r`), tableau de bord web sur le port 5000 (réseau local uniquement). |
| `psacc-sheets-sync` | Petit service Python ([`sync/psacc_sheets_sync.py`](sync/psacc_sheets_sync.py)) qui lit une copie de la base psacc, récupère les couleurs Tempo et met à jour le Sheet toutes les heures. |
| `e208-dashboard` | Front web en lecture seule ([`web/`](web/)) sur le port 8080 (réseau local uniquement) : copie fidèle des valeurs du Sheet, filtres par période, saison Tempo, comparatif des offres. |

## 📸 Aperçu

Captures du front web avec des **données fictives**.

| | Clair | Sombre |
| :--- | :---: | :---: |
| **PC** | <img src="docs/screenshots/pc-light.png" alt="Dashboard sur PC, thème clair" width="420"> | <img src="docs/screenshots/pc-dark.png" alt="Dashboard sur PC, thème sombre" width="420"> |
| **Mobile** | <img src="docs/screenshots/mobile-light.png" alt="Dashboard sur mobile, thème clair" width="240"> | <img src="docs/screenshots/mobile-dark.png" alt="Dashboard sur mobile, thème sombre" width="240"> |

---

## 📋 Prérequis

* Docker et le plugin `docker compose` ; l'utilisateur dans le groupe `docker` (`sudo usermod -aG docker $USER`, puis se reconnecter).
* Un compte **MyPeugeot** sur lequel l'e-208 est rattachée (le code PIN de l'application et un téléphone pour l'OTP par SMS).
* Un **Google Sheet** au format attendu (onglets `Recharges`, `Trajets`, `Couleurs Tempo`, `Paramètres`) et un **compte de service Google Cloud** (gratuit, sans facturation).

## 🔑 Compte de service Google

Dans **Cloud Shell** (icône `>_` de https://console.cloud.google.com) :

```bash
export PROJECT=e208-cost-tracker-$(date +%s | tail -c 6) SA=psacc-sheets-sync
gcloud projects create $PROJECT --name="e208 cost tracker" && gcloud config set project $PROJECT
gcloud services enable sheets.googleapis.com iam.googleapis.com
gcloud iam service-accounts create $SA --display-name="psacc sheets sync"
gcloud iam service-accounts keys create ~/google-sa.json --iam-account=$SA@$PROJECT.iam.gserviceaccount.com
cloudshell download ~/google-sa.json && rm ~/google-sa.json
```

> ℹ️ Si `keys create` répond `Unknown service account`, l'API IAM n'était pas encore active : relancer `service-accounts create` puis `keys create`.

Copier ensuite la clé sur le serveur (Windows : `"$env:USERPROFILE\Downloads\google-sa.json"`) :

```bash
scp google-sa.json <user>@<serveur>:<chemin>/secrets/google-sa.json
chmod 600 secrets/google-sa.json
```

Puis **partager le Sheet** avec l'adresse `client_email` du JSON (`psacc-sheets-sync@<projet>.iam.gserviceaccount.com`) en **Éditeur**, sans notification.

## 🚀 Installation

```bash
git clone git@github.com:Demonic31/e208-cost-tracker.git && cd e208-cost-tracker
cp .env.example .env          # LAN_IP, SPREADSHEET_ID, prix du comparatif…
mkdir -p psacc/config secrets && chmod 700 secrets
docker compose up -d psacc
```

1. **Connexion MyPeugeot** : ouvrir `http://<LAN_IP>:5000/config`, choisir **Peugeot** et le pays, saisir e-mail et mot de passe MyPeugeot, puis sur la page OTP demander le **SMS** et saisir le code avec le **PIN** de l'application.
2. **Réglages** (`psacc/config/config.ini`, conteneur arrêté car il appartient à root et psacc le réécrit) : section `[Electricity config]`, `day price`, `night price`, `night hour start = 22h00`, `night hour end = 6h00`. Ces prix ne servent qu'au tableau de bord psacc ; le vrai calcul Tempo est fait dans le Sheet.
3. **Batterie** : psacc la déduit du modèle (`battery_power` dans `cars.json`, 46 kWh pour une e-208 d'avant 2023, 51 kWh après). Si d'autres véhicules sont rattachés au compte, mettre l'e-208 **en premier** dans `cars.json` (le tableau de bord affiche le premier véhicule).
4. **Synchro** : un essai à blanc, un passage réel, puis le service en continu :

```bash
docker compose run --rm psacc-sheets-sync --once --dry-run
docker compose run --rm psacc-sheets-sync --once
docker compose up -d psacc-sheets-sync
docker compose logs -f psacc-sheets-sync
```

## 🔍 Fonctionnement de la synchro

* **Lecture** : copie de `info.db` (+ WAL) dans un dossier temporaire à chaque passage ; la base psacc, montée en lecture seule, n'est jamais verrouillée. Les dates psacc sont en **UTC** et sont converties en heure de Paris (`YYYY-MM-DD HH:MM:SS`, écrites en `USER_ENTERED` : le Sheet en `fr_FR` les reconnaît comme dates).
* **Couleurs Tempo** (`A:B`) : [api-couleur-tempo.fr](https://www.api-couleur-tempo.fr) (`/api/joursTempo?periode[]=2025-2026` pour l'historique, `/api/jourTempo/today|tomorrow`), de la première charge (à défaut la première position) jusqu'à demain si publié.
* **Recharges** (`A:N` uniquement) : une ligne par charge **terminée** (`battery.stop_at` renseigné), identifiant `C-<début UTC>`. Les kWh (`battery.kw` = capacité × % gagné) sont répartis en Bleu/Blanc/Rouge × HC/HP : jour Tempo = `date(t − 6h)`, HC de 22h à 6h, au prorata du % gagné entre deux points de la **courbe de charge** (`battery_curve`), à défaut au prorata du temps. Une charge dont une couleur n'est pas encore connue est reprise au passage suivant. Compteur km : celui du début de charge, sinon la dernière position connue avant la charge.
* **Trajets** (`A:G` uniquement) : psacc ne stocke pas les trajets ; ils sont recalculés depuis la table `position` avec **les mêmes règles que psacc** (arrêt si vitesse moyenne quasi nulle, recharge détectée ou trou de plus de 2 h ; trajets aberrants écartés). Le dernier trajet n'est écrit que lorsqu'il est clos.
* **Réveil pendant la charge** : à chaque heure de `WAKEUP_AT` (par défaut 3h et 5h55), la synchro lit le dernier état connu dans le cache de psacc (`/get_vehicleinfo/<VIN>?from_cache=1`, sans réveil). Si la voiture est « en charge », elle demande un réveil à psacc (`/wakeup/<VIN>`) : la voiture renvoie son état et psacc ajoute un point à la courbe de charge (milieu de nuit, et niveau juste avant la fin des heures creuses). Aucun réveil sinon, ni en `DRY_RUN`. Test manuel : `docker compose run --rm psacc-sheets-sync --check-wakeup --dry-run`.
* **Trajets saisis à la main** (p. ex. import d'un export CSV MyPeugeot) : identifiant `M-<début UTC>` (tout ID ne commençant pas par `T-`). La synchro ne les modifie jamais et n'écrit pas un trajet psacc qui chevauche leur plage horaire. Sans conso connue, laisser `E:F` vides : le kWh/100 km (voiture) du `Dashboard` ne compte que les km des trajets dont les kWh sont renseignés.
* **Km de la première charge** (`Recharges!U2`) : faute de charge précédente, la référence est le dernier compteur des `Trajets` terminés avant le jour de la charge (compteur de fin de journée), à défaut le compteur au départ du premier trajet du jour de la charge.
* **Charges saisies à la main** (`Recharges`, ID ne commençant pas par `C-`, source p. ex. `manuel (estimation)`) : gardées telles quelles par la synchro, triées par date avec les autres.
* **Écriture** : jamais la ligne 1 ni les colonnes de formules (`O:W` des recharges, `H` des trajets), aucune insertion de ligne (la formule `U2` utilise des plages fixes). Upsert par identifiant, lignes gardées triées par date de début ; si une charge doit s'intercaler, les lignes suivantes sont décalées dans `A:N` et un avertissement invite à vérifier les notes de la colonne `O`. Une lecture et au plus deux écritures groupées par passage (quotas Sheets), avec reprise automatique sur erreur 429/5xx. Relancer ne crée aucun doublon.

| Variable (`.env`) | Défaut | Rôle |
| :--- | :--- | :--- |
| `LAN_IP` | — | IP locale sur laquelle psacc écoute (jamais `0.0.0.0`) |
| `PSACC_VERSION` | `v3.7.10` | Tag de l'image psacc |
| `SPREADSHEET_ID` | — | ID du Google Sheet |
| `VIN` | vide | VIN de l'e-208 ; vide = premier véhicule électrique de `cars.json` |
| `SYNC_INTERVAL` | `3600` | Secondes entre deux passages |
| `DRY_RUN` | `false` | `true` : calcule et journalise sans écrire (et n'envoie aucun réveil) |
| `WAKEUP_AT` | `03:00,05:55` | Heures (Paris) de réveil de la voiture, seulement si une charge est en cours ; vide = jamais |
| `DASHBOARD_PORT` | `8080` | Port du front web (publié sur `LAN_IP` uniquement) |
| `DASHBOARD_CACHE_SECONDS` | `300` | Durée du cache des données du Sheet côté front |
| `BASE_PRICE_KWH` | vide | Prix TTC du kWh en option Base |
| `HCHP_HC_PRICE_KWH` / `HCHP_HP_PRICE_KWH` | vide | Prix HC / HP de l'option Heures creuses |
| `ZEN_LOW_PRICE_KWH` / `ZEN_HIGH_PRICE_KWH` | vide | Prix bas (week-end, fériés, HC semaine) / haut (HP semaine) de Zen Week-End + HC |
| `PETROL_L_100KM` | vide | Consommation de la voiture thermique de référence (L/100 km) |
| `PSACC_URL` | `http://psacc:5000` | API web de psacc pour le SOH et l'état de la voiture (fixée dans `docker-compose.yml`) |
| `BATTERY_KWH` | vide | Capacité de la batterie (kWh) : affiche dans le tableau des recharges les kWh batterie en % de la capacité (vide = pas de %) |
| `PETROL_FUEL` | `sp98` | Carburant (`sp98`, `sp95`, `e10`, `e85`, `gazole`, `gplc`) |
| `PETROL_DEPARTEMENT` | vide | Code département pour le prix moyen ; vide = France entière |

## 🖥️ Front web

```bash
docker compose up -d --build e208-dashboard
```

Puis ouvrir `http://<LAN_IP>:8080`. La page affiche ce que le Sheet a calculé, sans rien recalculer de son côté :

* **Indicateurs, coût par mois, énergie par tranche** : en période « Tout », ce sont les valeurs du `Dashboard` ; pour une autre période (saison Tempo, 12 derniers mois, mois, dates libres), les mêmes formules sont appliquées aux lignes de `Recharges` et `Trajets` de la période. La période choisie est mémorisée dans le navigateur.
* **Santé batterie (SOH)**, en haut de page : dernier SOH relevé par psacc (champ « resistance » de l'API Stellantis, qui ne mesure pas forcément la capacité restante), lu sur l'API web de psacc par le réseau Docker interne (`/get_vehicles` et `/battery/soh/<VIN>`, lecture seule, sans appel à Stellantis), relu toutes les heures.
* **Voiture**, sous le SOH : niveau de batterie (couleur du rouge au vert selon la charge), autonomie estimée, état de charge (en charge, terminée, arrêtée, échec, débranchée) et compteur. Valeurs du dernier envoi de la voiture, lues dans le cache de psacc (`/get_vehicleinfo/<VIN>?from_cache=1` : la voiture n'est jamais réveillée), relues toutes les 5 min.
* **Trajets par mois**, dans le tableau des trajets : une ligne par mois (du plus récent au plus ancien) avec le nombre de trajets, les km, les kWh, le kWh/100 km et le dernier compteur ; un clic déplie les trajets du mois (replié par défaut).
* **Recharges par mois**, dans le tableau des recharges : une ligne par mois avec le nombre de recharges, les kWh batterie et compteur, la répartition HC/HP, le coût, le €/kWh, les km et le €/100 km ; un clic déplie les recharges du mois (replié par défaut).
* **Courbe de charge**, dans le tableau des recharges : un clic sur une recharge déplie le graphique du niveau de batterie selon l'heure, heures creuses en fond (replié par défaut). Les points viennent de la table `battery_curve` de psacc, lue sur une copie de `info.db` (`psacc/config` monté en lecture seule, relu toutes les 5 min). La voiture n'envoie que 2 ou 3 points par charge lente (début, ~45 min, fin), donc la courbe est interpolée entre eux. Une recharge saisie à la main est tracée en droite du début à la fin. Le survol donne la puissance moyenne entre deux points (si `BATTERY_KWH` est renseigné).
* **Saison Tempo** (1er septembre → 31 août) : jours blancs, rouges et bleus restants, couleur du lendemain et calendrier de la saison, depuis [api-couleur-tempo.fr](https://www.api-couleur-tempo.fr) (cache d'une heure).
* **Comparatif des offres**, pour la même énergie (kWh facturés de la période) et hors abonnements. Une offre dont le prix n'est pas renseigné est signalée « à renseigner » :
  * **Tempo** : ton coût réel, calculé par le Sheet ;
  * **Base** : kWh × `BASE_PRICE_KWH` ;
  * **Heures creuses** : la part HC/HP de chaque charge, tirée des colonnes `H:M`, avec les mêmes heures creuses que Tempo (onglet `Paramètres`) ;
  * **Zen Week-End + HC** : prix bas le week-end, les jours fériés et en heures creuses en semaine, au prorata du temps de charge ;
  * **Essence** : km parcourus (le plus grand entre les trajets enregistrés et le compteur entre recharges) × `PETROL_L_100KM` × prix moyen du carburant. Ce prix est la moyenne des stations mises à jour ces 3 derniers jours, en France ou dans un département, d'après les [données officielles prix-carburants](https://data.economie.gouv.fr/explore/dataset/prix-des-carburants-en-france-flux-instantane-v2/), relues une fois par semaine (et au redémarrage du conteneur ; le bouton 🔄 « Actualiser » ne les relit pas).

Le serveur relit le Sheet au plus toutes les `DASHBOARD_CACHE_SECONDS`. Le bouton 🔄 (Actualiser) force la relecture. Le compte de service n'y a qu'un accès en lecture.

Tout est servi par le conteneur (Chart.js et logo compris, dans `web/static/vendor/`), avec une politique de sécurité stricte (`Content-Security-Policy` sans script externe ni inline, `X-Frame-Options: DENY`, `nosniff`).

### 🔐 Mire de connexion et accès distant

Le dashboard peut demander un **mot de passe unique**, défini au niveau du conteneur, pour être publié sur Internet derrière le reverse proxy du Synology (qui gère le 443 et le certificat HTTPS).

| Variable | Défaut | Rôle |
| :--- | :--- | :--- |
| `DASHBOARD_PASSWORD_HASH` | vide | Empreinte **scrypt** du mot de passe (le mot de passe en clair n'est stocké nulle part). Vide = pas de mire. La changer déconnecte toutes les sessions. |
| `SESSION_SECRET` | vide | Clé de signature des sessions (`openssl rand -hex 32`) ; vide = clé aléatoire, sessions perdues à chaque redémarrage |
| `TRUSTED_PROXY` | vide | IP LAN du Synology : seules ses requêtes sont considérées comme venant d'Internet, et leur en-tête `X-Real-IP` (IP réelle du visiteur) est cru |
| `LOGIN_SCOPE` | `proxy` | `proxy` : mot de passe pour les accès via le Synology, accès LAN direct libre ; `all` : partout. Tant que `TRUSTED_PROXY` est vide, le mot de passe est demandé partout. |

Générer l'empreinte (le mot de passe est demandé sans être affiché, 12 caractères minimum) :

```bash
docker compose run --rm e208-dashboard --hash-password
```

* **Session** : cookie signé (HMAC), `HttpOnly`, `SameSite=Strict`, `Secure` derrière le Synology ; 12 h, ou 30 jours avec « Se souvenir de moi ». Lien « Déconnexion » dans l'en-tête.
* **Anti-brute-force** : après 5 échecs en 15 min, l'IP du visiteur est bloquée 15 min ; comparaison en temps constant et 1 s d'attente par échec. Les tentatives sont journalisées (`docker logs`).
* **Synology** (DSM → Portail de connexion → Avancé → Proxy inversé → Créer) : source HTTPS `<nom>` port 443 avec HSTS, destination HTTP `<LAN_IP>` port du dashboard ; onglet En-tête personnalisé : `X-Real-IP` = `$remote_addr` et `X-Forwarded-Proto` = `$scheme`. Certificat Let's Encrypt pour `<nom>` dans Sécurité → Certificat → Ajouter, puis **Paramètres** pour l'associer à la règle (sinon le Synology présente son certificat auto-signé). Facultatif : pour qu'une adresse tapée sans `https://` aboutisse, une seconde règle avec la source **HTTP** port 80 et la même destination ; le dashboard renvoie alors vers HTTPS (redirection 301), sans jamais servir la page en clair. À n'ajouter qu'après l'obtention du certificat, et à vérifier au premier renouvellement Let's Encrypt (qui passe aussi par le port 80). Sans elle, HSTS fait passer le navigateur en HTTPS dès la première visite.

### 🧪 PREPROD

[`preprod/docker-compose.yml`](preprod/docker-compose.yml) lance un second dashboard (projet `e208-preprod`, port `8081` sur l'IP LAN) avec le code de la branche en cours, pour tester sans toucher à la PROD. Il lit le même Sheet et la base et l'API psacc de la pile principale, en lecture seule, sans synchro ni réveil.

```bash
cp preprod/.env.example preprod/.env && chmod 600 preprod/.env   # chemins de la pile principale, mot de passe, Synology
docker compose --env-file preprod/.env -f preprod/docker-compose.yml up -d --build
```

## 🔄 Mise à jour

```bash
# psacc : changer PSACC_VERSION dans .env (voir https://github.com/flobz/psa_car_controller/releases)
docker compose pull psacc && docker compose up -d psacc
# synchro et front : après git pull ou modification du code
git pull
docker compose build psacc-sheets-sync && docker compose up -d psacc-sheets-sync
docker compose up -d --build e208-dashboard
```

## 💾 Sauvegarde

Tout l'état est dans `psacc/config/` (session Stellantis, `cars.json`, `config.ini`, base `info.db`) et `secrets/` : à sauvegarder **conteneur arrêté** pour une base cohérente.

```bash
docker compose stop psacc
tar czf ~/psacc-config-$(date +%F).tar.gz psacc/config --exclude='*.apk*'
docker compose start psacc
```

Les fichiers `mypeugeot.apk*` (~120 Mo) sont retéléchargés automatiquement et n'ont pas besoin d'être sauvegardés.

## 🔐 Relancer la connexion si la session expire

Symptômes : `Please reconnect by going to config web page` dans `docker compose logs psacc`, plus de nouvelles positions, la synchro ne voit plus de nouvelles charges.

1. Ouvrir `http://<LAN_IP>:5000/config` et refaire la connexion (marque, identifiants MyPeugeot, OTP par SMS + PIN).
2. `docker compose restart psacc`, puis vérifier les logs (`access token refreshed`, `new position recorded`).

Les données déjà enregistrées sont conservées ; rien à faire côté synchro.

## 🔒 Sécurité

* `.env`, `secrets/` et `psacc/config/` (jetons Stellantis, VIN, positions GPS) sont exclus de git.
* La clé Google est montée en *secret* Docker en lecture seule ; la synchro tourne en utilisateur non root et monte la config psacc en lecture seule.
* Le front (port 8080) n'est publié que sur l'IP locale, comme psacc ; pour un accès depuis Internet, il passe par le reverse proxy du Synology et la mire de connexion.
* Le port 5000 n'est publié que sur l'IP locale : ne pas l'exposer sur Internet (l'interface permet de piloter la voiture).
