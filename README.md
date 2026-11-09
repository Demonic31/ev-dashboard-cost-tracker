# ev-dashboard-cost-tracker : suivi des recharges d'un véhicule électrique et coût EDF Tempo

Enregistre automatiquement chaque recharge et chaque trajet d'un **véhicule électrique**, puis les pousse toutes les heures dans un **Google Sheet** qui calcule le coût réel en **EDF Tempo** et le coût aux 100 km. Un **dashboard web** en affiche le détail.

| Service | Rôle |
| :--- | :--- |
| Services de la marque | Relevé des charges et trajets, et synchro du Sheet : [`brands/<marque>/`](brands/), choisi par `BRAND`. |
| `ev-dashboard` | Front web en lecture seule ([`web/`](web/)) sur le port 8080 (réseau local uniquement) : copie fidèle des valeurs du Sheet, état de la voiture, filtres par période, saison Tempo, comparatif des offres. |

## 🚗 Marques prises en charge

| `BRAND` | Marques | Outil | Détails |
| :--- | :--- | :--- | :--- |
| `stellantis` (défaut) | Peugeot, Citroën, DS, Opel, Vauxhall | [psa_car_controller](https://github.com/flobz/psa_car_controller) (psacc) | [`brands/stellantis/`](brands/stellantis/README.md) |

Testé avec une Peugeot e-208. **Ajouter une marque** : créer `brands/<marque>/` avec
* `docker-compose.yml` : ses services (relevé, synchro qui écrit le Sheet au format commun), inclus par le compose racine ; les chemins relatifs partent de ce dossier ;
* `source.py` : `create(env)` renvoie une `VehicleSource` (contrat dans [`web/vehicle_source.py`](web/vehicle_source.py) : nom, état de la voiture, SOH, courbes de charge, source citée dans « Sources et crédits » ; une donnée non fournie est masquée) ;
* `README.md` : prérequis, installation, variables.

## 📸 Aperçu

Captures du front web avec des **données fictives**. Pour les régénérer après une évolution du front : `tools/screenshots/run.sh http://<hôte>:8080/` (Docker et python3 requis).

| | Clair | Sombre |
| :--- | :---: | :---: |
| **PC** | <img src="docs/screenshots/pc-light.png" alt="Dashboard sur PC, thème clair" width="420"> | <img src="docs/screenshots/pc-dark.png" alt="Dashboard sur PC, thème sombre" width="420"> |
| **Mobile** | <img src="docs/screenshots/mobile-light.png" alt="Dashboard sur mobile, thème clair" width="240"> | <img src="docs/screenshots/mobile-dark.png" alt="Dashboard sur mobile, thème sombre" width="240"> |

---

## 📋 Prérequis

* Docker et le plugin `docker compose` ; l'utilisateur dans le groupe `docker` (`sudo usermod -aG docker $USER`, puis se reconnecter).
* Un véhicule d'une [marque prise en charge](#-marques-prises-en-charge) et ses prérequis (Stellantis : compte MyPeugeot…, voir [son README](brands/stellantis/README.md#-prérequis)).
* Un **Google Sheet** au format attendu (onglets `Dashboard`, `Recharges`, `Trajets`, `Couleurs Tempo`, `Paramètres`), créé à partir du [modèle fourni](#-google-sheet-à-partir-du-modèle), et un **compte de service Google Cloud** (gratuit, sans facturation).

## 📊 Google Sheet à partir du modèle

Le modèle [`sheet/modele-suivi-recharges.xlsx`](sheet/modele-suivi-recharges.xlsx) contient la structure complète, sans aucune donnée : formules, tableau de bord et ses deux graphiques, couleurs Tempo, grille tarifaire EDF Tempo et réglages.

1. Dans [Google Drive](https://drive.google.com) : **Nouveau → Importer un fichier**, choisir `modele-suivi-recharges.xlsx`.
2. Ouvrir le fichier importé avec **Google Sheets**, puis **Fichier → Enregistrer au format Google Sheets** (le `.xlsx` d'origine peut ensuite être supprimé du Drive). Le renommer à volonté.
3. Onglet **`Paramètres`** : vérifier la grille Tempo (une ligne par date d'effet, à compléter à chaque évolution des prix), les heures creuses, le coefficient de pertes de la prise et l'abonnement.
4. Noter l'**ID** du Sheet, la partie de l'adresse entre `/d/` et `/edit`, pour `SPREADSHEET_ID` dans `.env`.

* La synchro remplit `Recharges` (colonnes `A:N`), `Trajets` (`A:G`) et `Couleurs Tempo` ; tout le reste est calculé par les formules : ne pas modifier la ligne 1 ni les colonnes `O:W` de `Recharges`.
* Capacité du modèle : 1 500 recharges et 2 999 trajets (insérer des lignes en fin d'onglet au-delà).
* Le fichier vient de l'export Excel de Google Sheets : les formules propres à Google (`QUERY`, `LET`, `ARRAYFORMULA`) sont restaurées à l'import dans Google Sheets, mais **le modèle ne fonctionne pas dans Excel ou LibreOffice**.

## 🔑 Compte de service Google

Dans **Cloud Shell** (icône `>_` de https://console.cloud.google.com) :

```bash
export PROJECT=ev-dashboard-$(date +%s | tail -c 6) SA=ev-sheets-sync
gcloud projects create $PROJECT --name="EV dashboard" && gcloud config set project $PROJECT
gcloud services enable sheets.googleapis.com iam.googleapis.com
gcloud iam service-accounts create $SA --display-name="EV sheets sync"
gcloud iam service-accounts keys create ~/google-sa.json --iam-account=$SA@$PROJECT.iam.gserviceaccount.com
cloudshell download ~/google-sa.json && rm ~/google-sa.json
```

> ℹ️ Si `keys create` répond `Unknown service account`, l'API IAM n'était pas encore active : relancer `service-accounts create` puis `keys create`.

Copier ensuite la clé sur le serveur (Windows : `"$env:USERPROFILE\Downloads\google-sa.json"`) :

```bash
scp google-sa.json <user>@<serveur>:<chemin>/secrets/google-sa.json
chmod 600 secrets/google-sa.json
```

Puis **partager le Sheet** avec l'adresse `client_email` du JSON (`ev-sheets-sync@<projet>.iam.gserviceaccount.com`) en **Éditeur**, sans notification.

## 🚀 Installation

```bash
git clone git@github.com:Demonic31/ev-dashboard-cost-tracker.git && cd ev-dashboard-cost-tracker
cp .env.example .env          # BRAND, LAN_IP, SPREADSHEET_ID, prix du comparatif…
mkdir -p secrets && chmod 700 secrets
```

Puis suivre l'installation de la marque (Stellantis : [connexion psacc et premier passage de la synchro](brands/stellantis/README.md#-installation)), et lancer le dashboard (voir [Front web](#️-front-web)).

## 🔍 Synchro du Sheet

Chaque marque a sa synchro, qui remplit le Sheet au format commun (voir celle de [Stellantis](brands/stellantis/README.md#-fonctionnement-de-la-synchro)). Règles communes :

* **Recharges** (`A:N`) : une ligne par charge terminée, identifiant `C-<début UTC>`, kWh répartis en Bleu/Blanc/Rouge × HC/HP (jour Tempo = `date(t − 6h)`, HC de 22h à 6h). **Trajets** (`A:G`) : identifiant `T-<début UTC>`. **Couleurs Tempo** (`A:B`) : [api-couleur-tempo.fr](https://www.api-couleur-tempo.fr).
* **Saisies à la main** : une charge dont l'ID ne commence pas par `C-` (source p. ex. `manuel (estimation)`), ou un trajet dont l'ID ne commence pas par `T-` (p. ex. `M-<début UTC>`, import d'un export CSV de l'application de la marque), sont gardés tels quels ; la synchro n'écrit pas un trajet qui chevauche un trajet saisi à la main. Sans conso connue, laisser `E:F` vides : le kWh/100 km (voiture) du `Dashboard` ne compte que les km des trajets dont les kWh sont renseignés.
* **Km de la première charge** (`Recharges!U2`) : faute de charge précédente, la référence est le dernier compteur des `Trajets` terminés avant le jour de la charge, à défaut le compteur au départ du premier trajet du jour de la charge.
* **Écriture** : jamais la ligne 1 ni les colonnes de formules (`O:W` des recharges, `H` des trajets), aucune insertion de ligne. Upsert par identifiant, lignes gardées triées par date de début. Relancer ne crée aucun doublon.

| Variable (`.env`) | Défaut | Rôle |
| :--- | :--- | :--- |
| `BRAND` | `stellantis` | Marque : dossier de [`brands/`](brands/) (services et source de données du dashboard) |
| `LAN_IP` | — | IP locale sur laquelle les services écoutent (jamais `0.0.0.0`) |
| `SPREADSHEET_ID` | — | ID du Google Sheet |
| `DASHBOARD_PORT` | `8080` | Port du front web (publié sur `LAN_IP` uniquement) |
| `DASHBOARD_CACHE_SECONDS` | `300` | Durée du cache des données du Sheet côté front |
| `BASE_PRICE_KWH` | vide | Prix TTC du kWh en option Base |
| `HCHP_HC_PRICE_KWH` / `HCHP_HP_PRICE_KWH` | vide | Prix HC / HP de l'option Heures creuses (mêmes prix été comme hiver) |
| `ZEN_LOW_PRICE_KWH` / `ZEN_HIGH_PRICE_KWH` | vide | Prix bas (week-end, fériés, HC semaine) / haut (HP semaine) de Zen Week-End + HC |
| `ZEN_FLEX_ECO_HC_PRICE_KWH` / `ZEN_FLEX_ECO_HP_PRICE_KWH` | vide | Prix HC / HP des jours Éco de Zen Flex |
| `ZEN_FLEX_SOB_HC_PRICE_KWH` / `ZEN_FLEX_SOB_HP_PRICE_KWH` | vide | Prix HC / HP des jours Sobriété de Zen Flex ; avec les 4 prix renseignés, le calendrier des jours Éco / Sobriété est lu dans l'API du site EDF (non documentée) et gardé dans le volume `/data` |
| `BATTERY_KWH` | vide | Capacité de la batterie (kWh) : affiche dans le tableau des recharges les kWh batterie en % de la capacité (vide = pas de %) |
| `PETROL_FUELS` | vide | Voiture(s) thermique(s) de référence : `carburant:consommation` en L/100 km, séparés par des virgules, ex. `sp98:7,gazole:5.5` (décimale avec un point). Carburants : `sp98`, `sp95`, `e10`, `e85`, `gazole` (affiché « GO »), `gplc` |
| `PETROL_DEPARTEMENT` | vide | Code département pour le prix moyen ; vide = France entière |
| `PETROL_FUEL` / `PETROL_L_100KM` | `sp98` / vide | Anciens réglages, encore lus : carburant unique si `PETROL_FUELS` est vide, et consommation par défaut d'un carburant écrit sans `:` |
| `VEHICLE_NAME` | vide | Titre de l'en-tête « Marque Modèle » ; vide = lu sur la source de la marque (ex. « Peugeot e-208 ») |
| `DASHBOARD_LOGO` | vide | Logo personnel de l'en-tête : nom d'un fichier `.svg` ou `.png` (512 Ko max.) déposé dans `local/` ; vide = icône de l'application |
| `DASHBOARD_LOGO_INVERT` | vide | `1` : inverse le logo personnel en thème sombre (logo noir monochrome) |

Variables propres à la marque (VIN, intervalle de synchro, réveils…) : voir son README.

## 🖥️ Front web

```bash
docker compose up -d --build ev-dashboard
```

Puis ouvrir `http://<LAN_IP>:8080`. La page affiche ce que le Sheet a calculé, sans rien recalculer de son côté :

* **En-tête** : la marque et le modèle du véhicule (lus sur la source de la marque, ou `VEHICLE_NAME`) avec l'icône de l'application. Pour afficher un autre logo, par exemple celui de la marque, déposer le fichier dans `local/` (dossier non versionné, monté en lecture seule) et renseigner `DASHBOARD_LOGO` : voir [`local/README.md`](local/README.md). Les logos de marque ne sont pas fournis dans le dépôt, car ce sont des marques déposées.
* **Indicateurs, coût par mois, énergie par tranche** : en période « Tout », ce sont les valeurs du `Dashboard` ; pour une autre période (saison Tempo, 12 derniers mois, mois, dates libres), les mêmes formules sont appliquées aux lignes de `Recharges` et `Trajets` de la période. Par défaut, la page s'ouvre sur le **mois en cours** ; la période choisie ensuite est mémorisée dans le navigateur.
* **Graphique des coûts par jour, semaine, mois ou année** : le pas s'adapte à la durée de la période (jour jusqu'à 31 jours, semaine jusqu'à 3 mois, mois jusqu'à 3 ans, année au-delà) et peut être forcé par un petit menu à côté du titre ; un pas plus large que la période élargit le graphique (12 semaines, 12 mois ou toutes les années, jusqu'à la fin de la période). Par jour ou par semaine, chaque barre est empilée par tranche Tempo (Bleu / Blanc / Rouge, HC en aplat, HP en hachures), avec en info-bulle le coût et les kWh de chaque tranche, et une courbe du coût cumulé sur la période ; une recharge compte pour le jour où elle commence. Par mois ou par année, la courbe montre le €/100 km.
* **Santé batterie (SOH)**, en haut de page : dernier indicateur fourni par la source de la marque, avec son origine (Stellantis : champ « resistance » relevé par psacc), relu toutes les heures. Masqué si la marque ne le fournit pas.
* **Voiture**, sous le SOH : niveau de batterie (couleur du rouge au vert selon la charge), autonomie estimée, état de charge (en charge, terminée, arrêtée, échec, débranchée) et compteur. Valeurs du dernier envoi de la voiture, sans la réveiller, relues toutes les 5 min.
* **Dernière recharge**, sous la Voiture : une ligne de résumé (date, durée, % de début → fin, kWh voiture, coût), dépliée par défaut sur grand écran ; le détail ajoute l'heure de fin, la couleur Tempo, les kWh facturés, le prix au kWh, l'équivalent carburant et la répartition heures creuses / pleines ; repliée comme dépliée, la barre heures creuses / pleines indique « dont X % en HP » quand la recharge a débordé en heures pleines (kWh et coût de cette part en info-bulle). C'est toujours la dernière recharge connue, quelle que soit la période choisie.
  * **Charge en cours estimée** : la voiture n'envoie son état qu'au début de la charge, environ 45 min après, à la fin et aux réveils de `WAKEUP_AT`. Entre deux, le niveau, l'autonomie (au prorata du niveau) et l'heure de fin sont **extrapolés** depuis le dernier relevé et recalculés chaque minute dans la page, avec un encart « Estimation » qui rappelle l'heure de la dernière synchro ; la part estimée de la jauge est hachurée. Vitesse de charge (% par heure, sa source est indiquée dans l'encart) : mesurée dans la charge en cours dès que deux relevés sont espacés d'au moins 1 h, sinon médiane des 10 dernières recharges, sinon déduite du temps restant annoncé par la voiture (souvent optimiste ; son heure de fin reste affichée pour comparer). Chaque nouveau relevé sert de nouveau point de départ. Une fois l'heure de fin estimée passée, la charge est affichée « probablement terminée » ; au-delà de 14 h sans relevé, plus d'estimation (une charge interrompue ne se voit qu'à la synchro suivante).
* **Trajets par mois**, dans le tableau des trajets : une ligne par mois (du plus récent au plus ancien) avec le nombre de trajets, les km, les kWh, le kWh/100 km et le dernier compteur ; un clic déplie les trajets du mois (replié par défaut).
* **Recharges par mois**, dans le tableau des recharges : une ligne par mois avec le nombre de recharges, les kWh batterie et compteur, la répartition HC/HP, le coût, le €/kWh, les km et le €/100 km ; un clic déplie les recharges du mois (replié par défaut).
* **Équivalent carburant de chaque recharge**, sous son coût (et en total sur la ligne du mois) : ce qu'aurait coûté le même trajet en voiture thermique, une ligne par carburant de `PETROL_FUELS` (essence en orangé, gazole « GO » en violet). Km de la ligne (colonne « Km », compteur) quand ils sont connus, sinon km permis par la recharge = kWh batterie ÷ consommation moyenne des `Trajets` (kWh/100 km) ; × consommation du carburant (L/100 km) × son prix moyen **le jour de la recharge** (même moyenne que ci-dessous, appliquée aux [archives journalières officielles](https://www.prix-carburants.gouv.fr/rubrique/opendata/)). Ces prix passés sont téléchargés en tâche de fond (un fichier d'environ 1 Mo par jour de recharge, une seule fois, qui sert à tous les carburants) et gardés dans le volume Docker `dashboard-data` ; en attendant, et pour les recharges du jour, c'est le prix actuel qui sert. Le détail du calcul s'affiche au survol.
* **Courbe de charge**, dans le tableau des recharges : un clic sur une recharge déplie le graphique du niveau de batterie selon l'heure, heures creuses en fond (replié par défaut). Les points viennent de la source de la marque (relus toutes les 5 min) ; la voiture n'en envoie souvent que quelques-uns par charge, donc la courbe est interpolée entre eux. Une recharge saisie à la main est tracée en droite du début à la fin. Le survol donne la puissance moyenne entre deux points (si `BATTERY_KWH` est renseigné).
* **Saison Tempo** (1er septembre → 31 août) : jours blancs, rouges et bleus restants, couleur du lendemain et calendrier de la saison (replié par défaut, un clic le déplie), depuis [api-couleur-tempo.fr](https://www.api-couleur-tempo.fr) (cache d'une heure).
* **Comparatif des offres**, pour la même énergie (kWh facturés de la période) et hors abonnements, en deux groupes classés du moins cher au plus cher : ⚡ abonnements EDF, Tempo en tête comme référence, puis ⛽ équivalents thermiques. Badges « offre actuelle » et « la moins chère » (offre d'électricité). Pour chaque offre : coût et €/100 km, avec l'écart avec Tempo en dessous (les offres d'électricité au même kilométrage que Tempo). Le détail du tarif s'affiche au survol de la ligne (ou d'un appui sur téléphone), la méthode de calcul dans un bloc repliable. Une offre dont le prix n'est pas renseigné est signalée « à renseigner », en fin de son groupe :
  * **Tempo** : ton coût réel et ton €/100 km, calculés par le Sheet ; tarif de la grille de l'onglet `Paramètres` en vigueur à la fin de la période, en info-bulle, avec la répartition de la période (part en heures creuses et part de chaque couleur de jour) ;
  * **Base** : kWh × `BASE_PRICE_KWH` ;
  * **Heures creuses** : nouvelles heures creuses saisonnalisées (déploiement Enedis fin 2025 – 2027, 8 h par jour), au prorata du temps de charge. Plages retenues : hiver (novembre → mars) 23h–7h ; été (avril → octobre) 1h–6h et 13h–16h. Les plages réelles sont fixées par Enedis pour chaque compteur Linky : le résultat reste une estimation. Recharge sans heure de fin : répartition HC/HP Tempo des colonnes `H:M` ;
  * **Zen Week-End + HC** : prix bas le week-end, les jours fériés et en heures creuses (celles de Tempo) en semaine, au prorata du temps de charge ; répartition de la période en info-bulle ;
  * **Zen Flex** : 17 h creuses par jour (HP 8h–13h et 18h–20h) et 20 jours Sobriété par an, au prorata du temps de charge. Le calendrier des jours Éco / Sobriété / Bonus est lu dans l'API du site EDF (non documentée, susceptible de changer) : J et J+1 toutes les 10 min, plus un rattrapage de l'historique depuis novembre 2023 (au plus 30 jours par passe, seulement les jours où un jour Sobriété est possible), gardé dans le volume `dashboard-data`. Un jour pas encore lu est approché par la couleur Tempo (Rouge = Sobriété) ;
  * **Essence / Diesel** : une ligne par carburant de `PETROL_FUELS` (ou `PETROL_FUEL`) ; km parcourus (le plus grand entre les trajets enregistrés et le compteur entre recharges) × consommation du carburant × son prix moyen (€/100 km = consommation × prix moyen). Ce prix est la moyenne des stations mises à jour ces 3 derniers jours, en France ou dans un département, d'après les [données officielles prix-carburants](https://data.economie.gouv.fr/explore/dataset/prix-des-carburants-en-france-flux-instantane-v2/), relues une fois par jour (et au redémarrage du conteneur ; le bouton 🔄 « Actualiser » ne les relit pas).
* **Pied de page** : version installée (avec le libellé PREPROD en PREPROD) ; un clic ouvre le panneau **Nouveautés**, résumé de chaque version tiré de [`CHANGELOG.md`](CHANGELOG.md), avec un lien vers chaque note de version GitHub. Puis auteur, licence MIT et lien vers le code source, puis un lien **Sources et crédits**. Ce panneau liste les sources des données, chacune en lien avec ce qu'elle fournit et la date de sa dernière lecture : celle de la marque (psa_car_controller pour Stellantis), le Google Sheet, [api-couleur-tempo.fr](https://www.api-couleur-tempo.fr), le calendrier Zen Flex d'EDF et [prix-carburants.gouv.fr](https://www.prix-carburants.gouv.fr/rubrique/opendata/) ; puis les bibliothèques (Chart.js) et le projet. Les panneaux se ferment avec ✕, Échap ou le bouton retour du téléphone. La version vient de [`web/VERSION`](web/VERSION) ; à chaque release, mettre à jour ce fichier et ajouter la version en tête de `CHANGELOG.md`.

Le serveur relit le Sheet au plus toutes les `DASHBOARD_CACHE_SECONDS`. Le bouton 🔄 (Actualiser) force la relecture. Le compte de service n'y a qu'un accès en lecture.

Tout est servi par le conteneur (Chart.js dans `web/static/vendor/`, logo personnel éventuel depuis `local/`), avec une politique de sécurité stricte (`Content-Security-Policy` sans script externe ni inline, `X-Frame-Options: DENY`, `nosniff`).

### 🔐 Mire de connexion et accès distant

Le dashboard peut demander un **mot de passe unique**, défini au niveau du conteneur, pour être publié sur Internet derrière un reverse proxy qui gère le 443 et le certificat HTTPS : le **proxy intégré** (Caddy, voir plus bas), un Synology, ou tout autre.

| Variable | Défaut | Rôle |
| :--- | :--- | :--- |
| `DASHBOARD_PASSWORD_HASH` | vide | Empreinte **scrypt** du mot de passe (le mot de passe en clair n'est stocké nulle part). Vide = pas de mire. La changer déconnecte toutes les sessions. |
| `DASHBOARD_TOTP_SECRET` | vide | Clé TOTP : un code à 6 chiffres d'application d'authentification (Aegis, 2FAS, Google Authenticator…) est demandé après le mot de passe. Vide = mot de passe seul. La changer déconnecte toutes les sessions. |
| `SESSION_SECRET` | vide | Clé de signature des sessions (`openssl rand -hex 32`) ; vide = clé aléatoire, sessions perdues à chaque redémarrage |
| `TRUSTED_PROXY` | vide | Reverse proxy : son IP LAN (Synology…), ou `caddy` avec le proxy intégré (nom résolu par Docker). Seul son en-tête `X-Real-IP` (IP réelle du visiteur) est cru. |
| `LOGIN_SCOPE` | `proxy` | `proxy` : mot de passe pour les accès via le proxy, accès LAN direct libre ; `all` : partout. Tant que `TRUSTED_PROXY` est vide, le mot de passe est demandé partout. |

Garde-fous : une requête qui porte des en-têtes de reverse proxy (`X-Forwarded-For`, `X-Real-IP`…) sans venir de `TRUSTED_PROXY` est traitée comme venant d'Internet (mot de passe exigé, en-têtes ignorés) ; et **sans mot de passe configuré, toute requête venant d'Internet est refusée** (403) : un proxy mal réglé n'ouvre jamais le dashboard.

Générer l'empreinte (le mot de passe est demandé sans être affiché, 12 caractères minimum) :

```bash
docker compose run --rm ev-dashboard --hash-password
```

Enrôler l'application d'authentification : la commande affiche un QR code (et la clé à saisir à la main), demande un code pour vérifier, puis met la clé dans `.env` :

```bash
L=$(docker compose run --rm -T ev-dashboard --setup-totp | grep '^DASHBOARD_TOTP_SECRET=') && sed -i "s|^DASHBOARD_TOTP_SECRET=.*|$L|" .env && docker compose up -d ev-dashboard
```

* **Code TOTP** (RFC 6238, 30 s) : accepté pour la tranche en cours, la précédente ou la suivante (décalage d'horloge) ; un code déjà utilisé est refusé ; il n'est vérifié qu'avec le bon mot de passe. La clé ne peut pas être hachée (le serveur en a besoin pour calculer les codes) : elle reste dans `.env`, jamais dans git. Téléphone perdu : relancer `--setup-totp` depuis le serveur.
* **Page de connexion** : logo « voiture électrique » (aussi favicon et icône d'application), titre « Dashboard VE · Login », champs mot de passe et code, case « Se souvenir de moi » et, sous le formulaire, un bouton de thème clair / sombre (choix partagé avec le dashboard).
* **Session** : cookie signé (HMAC), `HttpOnly`, `SameSite=Lax`, `Secure` derrière le Synology ; 12 h, ou 30 jours avec « Se souvenir de moi », prolongés de 30 jours à chaque utilisation après un jour (on ne se reconnecte que si le dashboard reste plus d'un mois sans être ouvert). Lien « Déconnexion » dans l'en-tête.
* **Application Android (PWA)** : depuis Chrome sur l'adresse HTTPS publique, menu ⋮ → « Installer l'application » (l'adresse locale en HTTP ne peut pas être installée). En mode application, « Se souvenir de moi » est coché d'office. Le service worker ne garde en cache qu'une page « Hors connexion », jamais les données. Installer avec Chrome (Brave ne pose qu'un raccourci) ; si Chrome demande un nom ou si l'app garde une barre de titre, un VPN / bloqueur de pubs qui filtre le HTTPS a retiré le lien du manifeste : l'installer en le coupant, puis exclure l'adresse du filtrage.
* **Anti-brute-force** (mot de passe ou code) : après 5 échecs en 15 min, l'IP du visiteur est bloquée 15 min ; comparaison en temps constant et 1 s d'attente par échec. Les tentatives sont journalisées (`docker logs`).
* **Synology**, si tu préfères son reverse proxy au proxy intégré (DSM → Portail de connexion → Avancé → Proxy inversé → Créer) : source HTTPS `<nom>` port 443 avec HSTS, destination HTTP `<LAN_IP>` port du dashboard ; onglet En-tête personnalisé : `X-Real-IP` = `$remote_addr` et `X-Forwarded-Proto` = `$scheme`. Certificat Let's Encrypt pour `<nom>` dans Sécurité → Certificat → Ajouter, puis **Paramètres** pour l'associer à la règle (sinon le Synology présente son certificat auto-signé). Facultatif : pour qu'une adresse tapée sans `https://` aboutisse, une seconde règle avec la source **HTTP** port 80 et la même destination ; le dashboard renvoie alors vers HTTPS (redirection 301), sans jamais servir la page en clair. À n'ajouter qu'après l'obtention du certificat, et à vérifier au premier renouvellement Let's Encrypt (qui passe aussi par le port 80). Sans elle, HSTS fait passer le navigateur en HTTPS dès la première visite.

### 🌐 Accès depuis Internet avec le proxy intégré (Caddy)

Solution clé en main, sans Synology : un conteneur [Caddy](https://caddyserver.com/) (profil compose `proxy`, facultatif) reçoit les connexions sur les ports 80 et 443, obtient et renouvelle seul le certificat **Let's Encrypt**, redirige HTTP vers HTTPS (avec HSTS) et transmet au dashboard l'IP réelle du visiteur. Seul le dashboard est publié : les services de la marque (psacc, port 5000) restent sur le LAN.

1. **Nom de domaine** : un enregistrement DNS `A` (et `AAAA` en IPv6) de ton domaine, p. ex. `ve.example.fr`, vers l'IP publique de ta box. IP publique qui change : un DNS dynamique (DynDNS, DuckDNS, celui de ta box…).
2. **Box** : rediriger les ports **TCP 80 et 443** (et UDP 443, facultatif, pour HTTP/3) vers l'IP LAN du serveur. Jamais 5000 ni 8080. Le port 80 sert à Let's Encrypt (validation et renouvellement) et à la redirection vers HTTPS.
3. **`.env`** : la mire (mot de passe, de préférence code TOTP, `SESSION_SECRET` : voir ci-dessus), puis :
   ```bash
   DASHBOARD_DOMAIN=ve.example.fr
   TRUSTED_PROXY=caddy
   ```
4. **Démarrer** :
   ```bash
   docker compose --profile proxy up -d
   docker logs -f ev-caddy   # attendre « certificate obtained successfully »
   ```
5. Ouvrir `https://ve.example.fr` : la mire de connexion s'affiche.

* Certificats et clés dans les volumes `caddy-data` / `caddy-config` (à ne pas supprimer : Let's Encrypt limite le nombre de demandes).
* Configuration dans [`proxy/Caddyfile`](proxy/Caddyfile) ; version de Caddy figée par `CADDY_VERSION` (défaut `2.8.4-alpine`).
* Échec du certificat : vérifier le DNS (`nslookup ve.example.fr`), la redirection du port 80 depuis l'extérieur (pas depuis le LAN : beaucoup de box ne gèrent pas le « hairpin ») et les journaux de Caddy.
* Couper l'accès depuis Internet : `docker compose stop caddy` (le dashboard reste accessible sur le LAN).
* Sans mot de passe configuré, Caddy obtient le certificat mais le dashboard répond 403 aux requêtes venant d'Internet.

### 🧪 PREPROD

[`preprod/docker-compose.yml`](preprod/docker-compose.yml) lance un second dashboard (projet `ev-preprod`, port `8081` sur l'IP LAN) avec le code de la branche en cours, pour tester sans toucher à la PROD. Il lit le même Sheet et les données de la marque (base et API psacc) de la pile principale, en lecture seule, sans synchro ni réveil. Son app installable s'appelle « VE PREPROD », avec une icône et un logo de mire marqués « TEST » (`APP_LABEL`), pour la distinguer de celle de la PROD.

```bash
cp preprod/.env.example preprod/.env && chmod 600 preprod/.env   # chemins de la pile principale, mot de passe, Synology
preprod/preprod.sh on    # (re)lance le dashboard avec le code du worktree
preprod/preprod.sh off   # l'arrête et affiche à sa place la page « PREPROD en pause »
```

Arrêtée, la PREPROD n'est pas coupée du Synology : un petit serveur (`web/pause_server.py`, même image, profil `pause`) répond sur le même port avec une page « PREPROD en pause » aux couleurs du dashboard (code 503), au lieu de la page d'erreur du Synology. `PAUSE_LINK` (dans `preprod/.env`) y ajoute un bouton vers la PROD.

## 🔄 Mise à jour

```bash
git pull
docker compose up -d --build --remove-orphans
# proxy intégré (si utilisé) : changer CADDY_VERSION dans .env
docker compose --profile proxy pull caddy && docker compose --profile proxy up -d caddy
```

La version installée s'affiche en bas du dashboard. `--remove-orphans` retire les conteneurs d'un service renommé (p. ex. `e208-dashboard`, devenu `ev-dashboard` en 0.9.0). Mise à jour des outils de la marque : voir son README (psacc : [mise à jour](brands/stellantis/README.md#-mise-à-jour-de-psacc)).

## 💾 Sauvegarde

À garder : `.env`, `secrets/`, `local/` et les données de la marque (Stellantis : `psacc/config/`, voir [sa sauvegarde](brands/stellantis/README.md#-sauvegarde)). Le Google Sheet est sauvegardé par Google Drive.

## 🔒 Sécurité

* `.env`, `secrets/`, `local/` et les données des marques (`psacc/config/` : jetons Stellantis, VIN, positions GPS) sont exclus de git, et du contexte de build Docker (liste blanche dans [`.dockerignore`](.dockerignore)).
* La clé Google est montée en *secret* Docker en lecture seule ; la synchro et le dashboard tournent en utilisateur non root et lisent les données de la marque en lecture seule.
* Le front (port 8080) n'est publié que sur l'IP locale ; pour un accès depuis Internet, il passe par un reverse proxy (Caddy intégré ou Synology) et la mire de connexion. Sans mot de passe configuré, les requêtes venant d'Internet sont refusées.
* Les services des marques (p. ex. psacc, port 5000, qui permet de piloter la voiture) ne sont publiés que sur l'IP locale : ne pas les exposer sur Internet.

## 🙏 Solutions utilisées

| Solution | Rôle dans le projet |
| :--- | :--- |
| [psa_car_controller](https://github.com/flobz/psa_car_controller) (flobz) | Connexion au compte MyPeugeot, relevé des charges, trajets et courbes de charge |
| [Caddy](https://github.com/caddyserver/caddy) | Reverse proxy intégré, HTTPS Let's Encrypt automatique |
| [Chart.js](https://github.com/chartjs/Chart.js) | Graphiques du dashboard (v4.4.1, licence MIT : [`web/static/vendor/LICENSE-chartjs.md`](web/static/vendor/LICENSE-chartjs.md)) |
| [segno](https://github.com/heuer/segno) | QR code d'enrôlement TOTP |
| [requests](https://github.com/psf/requests) · [google-auth](https://github.com/googleapis/google-auth-library-python) | Appels HTTP et accès à l'API Google Sheets |
| [api-couleur-tempo.fr](https://www.api-couleur-tempo.fr/) | Couleurs des jours Tempo |
| [Prix des carburants](https://www.prix-carburants.gouv.fr/rubrique/opendata/) (données officielles) | Prix du jour et archives pour l'équivalent carburant |

## 📄 Licence

[MIT](LICENSE) © 2026 Demonic31. Les composants tiers gardent leur propre licence (voir « Solutions utilisées » ; Chart.js : [`web/static/vendor/LICENSE-chartjs.md`](web/static/vendor/LICENSE-chartjs.md)).

Projet indépendant, non affilié à Stellantis. Peugeot est une marque de Stellantis. Les noms de marques et de modèles ne servent qu'à désigner les véhicules compatibles.
