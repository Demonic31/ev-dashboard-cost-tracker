# e208-cost-tracker : suivi des recharges d'une e-208 et coût EDF Tempo

Enregistre automatiquement chaque recharge et chaque trajet d'une **Peugeot e-208** avec [psa_car_controller](https://github.com/flobz/psa_car_controller) (psacc), puis les pousse toutes les heures dans un **Google Sheet** qui calcule le coût réel en **EDF Tempo** et le coût aux 100 km.

| Service | Rôle |
| :--- | :--- |
| `psacc` | Image officielle `flobz/psa_car_controller` : connexion au compte MyPeugeot, enregistrement des positions, charges et courbes de charge dans `psacc/config/info.db` (option `-r`), tableau de bord web sur le port 5000 (réseau local uniquement). |
| `psacc-sheets-sync` | Petit service Python ([`sync/psacc_sheets_sync.py`](sync/psacc_sheets_sync.py)) qui lit une copie de la base psacc, récupère les couleurs Tempo et met à jour le Sheet toutes les heures. |
| `e208-dashboard` | Front web en lecture seule ([`web/`](web/)) sur le port 8080 (réseau local uniquement) : copie fidèle des valeurs du Sheet, filtres par période, saison Tempo, comparatif des offres. |

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
| `DRY_RUN` | `false` | `true` : calcule et journalise sans écrire |
| `DASHBOARD_PORT` | `8080` | Port du front web (publié sur `LAN_IP` uniquement) |
| `DASHBOARD_CACHE_SECONDS` | `300` | Durée du cache des données du Sheet côté front |
| `BASE_PRICE_KWH` | vide | Prix TTC du kWh en option Base |
| `HCHP_HC_PRICE_KWH` / `HCHP_HP_PRICE_KWH` | vide | Prix HC / HP de l'option Heures creuses |
| `ZEN_LOW_PRICE_KWH` / `ZEN_HIGH_PRICE_KWH` | vide | Prix bas (week-end, fériés, HC semaine) / haut (HP semaine) de Zen Week-End + HC |
| `PETROL_L_100KM` | vide | Consommation de la voiture thermique de référence (L/100 km) |
| `BATTERY_KWH` | vide | Capacité de la batterie (kWh) : affiche dans le tableau des recharges les kWh batterie en % de la capacité (vide = pas de %) |
| `PETROL_FUEL` | `sp98` | Carburant (`sp98`, `sp95`, `e10`, `e85`, `gazole`, `gplc`) |
| `PETROL_DEPARTEMENT` | vide | Code département pour le prix moyen ; vide = France entière |

## 🖥️ Front web

```bash
docker compose up -d --build e208-dashboard
```

Puis ouvrir `http://<LAN_IP>:8080`. La page affiche ce que le Sheet a calculé, sans rien recalculer de son côté :

* **Indicateurs, coût par mois, énergie par tranche** : en période « Tout », ce sont les valeurs du `Dashboard` ; pour une autre période (saison Tempo, 12 derniers mois, mois, dates libres), les mêmes formules sont appliquées aux lignes de `Recharges` et `Trajets` de la période. La période choisie est mémorisée dans le navigateur.
* **Saison Tempo** (1er septembre → 31 août) : jours blancs, rouges et bleus restants, couleur du lendemain et calendrier de la saison, depuis [api-couleur-tempo.fr](https://www.api-couleur-tempo.fr) (cache d'une heure).
* **Comparatif des offres**, pour la même énergie (kWh facturés de la période) et hors abonnements. Une offre dont le prix n'est pas renseigné est signalée « à renseigner » :
  * **Tempo** : ton coût réel, calculé par le Sheet ;
  * **Base** : kWh × `BASE_PRICE_KWH` ;
  * **Heures creuses** : la part HC/HP de chaque charge, tirée des colonnes `H:M`, avec les mêmes heures creuses que Tempo (onglet `Paramètres`) ;
  * **Zen Week-End + HC** : prix bas le week-end, les jours fériés et en heures creuses en semaine, au prorata du temps de charge ;
  * **Essence** : km parcourus (le plus grand entre les trajets enregistrés et le compteur entre recharges) × `PETROL_L_100KM` × prix moyen du carburant. Ce prix est la moyenne des stations mises à jour ces 3 derniers jours, en France ou dans un département, d'après les [données officielles prix-carburants](https://data.economie.gouv.fr/explore/dataset/prix-des-carburants-en-france-flux-instantane-v2/), relues une fois par semaine (et au redémarrage du conteneur ; le bouton « Actualiser » ne les relit pas).

Le serveur relit le Sheet au plus toutes les `DASHBOARD_CACHE_SECONDS`. Le bouton « Actualiser » force la relecture. Le compte de service n'y a qu'un accès en lecture.

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
* Le front (port 8080) n'est publié que sur l'IP locale, comme psacc.
* Le port 5000 n'est publié que sur l'IP locale : ne pas l'exposer sur Internet (l'interface permet de piloter la voiture).
