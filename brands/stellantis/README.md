# Stellantis (Peugeot, Citroën, DS, Opel, Vauxhall) avec psacc

Services de la marque `stellantis` (valeur par défaut de `BRAND`), inclus par le [`docker-compose.yml`](../../docker-compose.yml) racine :

| Service | Rôle |
| :--- | :--- |
| `psacc` | Image officielle [`flobz/psa_car_controller`](https://github.com/flobz/psa_car_controller) : connexion au compte MyPeugeot (ou MyCitroën, MyDS, MyOpel), enregistrement des positions, charges et courbes de charge dans `psacc/config/info.db` (option `-r`), tableau de bord web sur le port 5000 (réseau local uniquement). |
| `psacc-sheets-sync` | Petit service Python ([`sync/psacc_sheets_sync.py`](sync/psacc_sheets_sync.py)) qui lit une copie de la base psacc, récupère les couleurs Tempo et met à jour le Sheet toutes les heures. |

Côté dashboard, [`source.py`](source.py) lit en lecture seule l'API web de psacc (réseau Docker interne, sans appel à Stellantis ni réveil de la voiture) et une copie de sa base : marque et modèle, état de la voiture, SOH, courbes de charge.

Les données restent à la racine du dépôt : `psacc/config/` (session Stellantis, `cars.json`, `config.ini`, base `info.db`) et `secrets/`.

## 📋 Prérequis

* Un compte **MyPeugeot** (ou l'application de la marque) sur lequel le véhicule est rattaché, le code PIN de l'application et un téléphone pour l'OTP par SMS.

## 🚀 Installation

Après les étapes communes du [README principal](../../README.md#-installation) (`.env`, Google Sheet, compte de service) :

```bash
mkdir -p psacc/config
docker compose up -d psacc
```

1. **Connexion MyPeugeot** : ouvrir `http://<LAN_IP>:5000/config`, choisir la marque (**Peugeot**…) et le pays, saisir e-mail et mot de passe, puis sur la page OTP demander le **SMS** et saisir le code avec le **PIN** de l'application.
2. **Réglages** (`psacc/config/config.ini`, conteneur arrêté car il appartient à root et psacc le réécrit) : section `[Electricity config]`, `day price`, `night price`, `night hour start = 22h00`, `night hour end = 6h00`. Ces prix ne servent qu'au tableau de bord psacc ; le vrai calcul Tempo est fait dans le Sheet.
3. **Batterie** : psacc la déduit du modèle (`battery_power` dans `cars.json`, p. ex. 46 kWh pour une e-208 d'avant 2023, 51 kWh après). Si d'autres véhicules sont rattachés au compte, indiquer `VIN` dans `.env` (sinon : premier véhicule électrique de `cars.json`).
4. **Synchro** : un essai à blanc, un passage réel, puis le service en continu :

```bash
docker compose run --rm psacc-sheets-sync --once --dry-run
docker compose run --rm psacc-sheets-sync --once
docker compose up -d psacc-sheets-sync
docker compose logs -f psacc-sheets-sync
```

## ⚙️ Variables (`.env`)

| Variable | Défaut | Rôle |
| :--- | :--- | :--- |
| `PSACC_VERSION` | `v3.7.10` | Tag de l'image psacc |
| `VIN` | vide | VIN du véhicule suivi (synchro et dashboard) ; vide = premier véhicule électrique de `cars.json` |
| `SYNC_INTERVAL` | `3600` | Secondes entre deux passages de la synchro |
| `DRY_RUN` | `false` | `true` : calcule et journalise sans écrire (et n'envoie aucun réveil) |
| `WAKEUP_AT` | `03:00,05:55` | Heures (Paris) de réveil de la voiture, seulement si une charge est en cours ; vide = jamais |

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

## 🖥️ Données fournies au dashboard

* **Titre** : `brand` et `label` de `/get_vehicles` (p. ex. « Peugeot e-208 »), relus toutes les heures.
* **Santé batterie (SOH)** : champ « resistance » de l'API Stellantis (qui ne mesure pas forcément la capacité restante), via `/battery/soh/<VIN>`, relu toutes les heures.
* **Voiture** : dernier envoi de la voiture, lu dans le cache de psacc (`/get_vehicleinfo/<VIN>?from_cache=1` : la voiture n'est jamais réveillée), relu toutes les 5 min. Les états de charge de l'API (`InProgress`, `Finished`…) et le temps restant (`PT2H25M`) sont traduits dans le format commun du dashboard.
* **Courbes de charge** : table `battery_curve`, lue sur une copie de `info.db` (`psacc/config` monté en lecture seule), relue toutes les 5 min. La voiture n'envoie que 2 ou 3 points par charge lente (début, ~45 min, fin).

## 🔄 Mise à jour de psacc

```bash
# changer PSACC_VERSION dans .env (voir https://github.com/flobz/psa_car_controller/releases)
docker compose pull psacc && docker compose up -d psacc
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

* `psacc/config/` (jetons Stellantis, VIN, positions GPS) est exclu de git et du contexte de build Docker.
* La synchro tourne en utilisateur non root et monte la config psacc en lecture seule.
* Le port 5000 n'est publié que sur l'IP locale : ne pas l'exposer sur Internet (l'interface permet de piloter la voiture).
