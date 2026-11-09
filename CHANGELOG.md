# Journal des versions

Résumé de chaque version, affiché dans le dashboard (clic sur le numéro de version, en bas de page). Le détail se trouve dans les notes de version GitHub.

Format : un titre `## <version> — <AAAA-MM-JJ>` par version, puis des puces `- ` d'une ligne (`**gras**` et `` `code` `` acceptés).

## 0.17.1 — 2026-11-09
- **Info-bulle Tempo** du comparatif : répartition de la période, part en heures creuses et part de chaque couleur de jour (🔵 ⚪ 🔴).

## 0.17.0 — 2026-11-06
- **Offre Zen Flex** dans le comparatif : jours Éco 🟢 et Sobriété 🟠, calendrier des jours Sobriété lu chez EDF (jours Rouges Tempo en attendant).
- **Répartition de la période** dans l'info-bulle des deux offres Zen.

## 0.16.0 — 2026-11-06
- **Nouvelles heures creuses** pour l'option Heures creuses du comparatif : plages d'été (☀️ 1h–6h et 13h–16h) et d'hiver (❄️ 23h–7h), part de la période en heures creuses dans l'info-bulle ; les heures creuses réelles dépendent du compteur Linky.

## 0.15.0 — 2026-11-06
- **Comparatif des offres classé** du moins cher au plus cher, Tempo en tête, en deux groupes : ⚡ abonnements EDF et ⛽ équivalents thermiques ; badges « offre actuelle » et « la moins chère ».
- **Écart avec Tempo sous chaque coût**, et nouvelle colonne **€/100 km** avec son écart.
- **Tarifs en info-bulle** (ou d'un appui sur téléphone), dont la grille Tempo du Sheet par couleur ; méthode de calcul repliée.

## 0.14.1 — 2026-11-05
- **Documentation** : captures d'écran du README à jour, et outil versionné pour les régénérer avec des données fictives.

## 0.14.0 — 2026-11-05
- **Dernière recharge repliée** : « dont X % en HP » à droite de la barre quand la recharge a débordé en heures pleines, dans la couleur du jour ; détail des kWh en info-bulle.

## 0.13.1 — 2026-11-05
- **Dernière recharge repliée** : la barre heures creuses / heures pleines s'affiche sous le résumé, précédée de « HC/HP ».

## 0.13.0 — 2026-11-05
- **Tranches Tempo plus lisibles** : une teinte vive par couleur de jour, heures creuses en aplat et heures pleines en hachures, sur le camembert comme sur les barres de répartition.

## 0.12.2 — 2026-11-05
- **Dernière recharge repliée sur téléphone** : toujours sur deux lignes, la durée en tête de la 2ᵉ ligne.

## 0.12.1 — 2026-11-05
- **Pied de page** : « Made with ❤️ » au lieu de 💖.

## 0.12.0 — 2026-11-05
- **Pied de page** : version installée, auteur, licence et lien vers le code source.
- **Sources et crédits** : un panneau liste les sources des données (avec la date de leur dernière lecture), les bibliothèques et le projet.
- **Nouveautés** : ce journal des versions, ouvert depuis le numéro de version.

## 0.11.1 — 2026-11-05
- **Dernière recharge** : prix au kWh et équivalents carburant de nouveau sur une ligne, le GO aligné sous le SP98.

## 0.11.0 — 2026-11-05
- **Équivalent gazole (GO)** en plus du SP98 : sous chaque recharge, sur la ligne du mois, dans la Dernière recharge et dans le comparatif.
- Nouveau réglage `PETROL_FUELS` (ex. `sp98:7,gazole:5.5`), une consommation par carburant.

## 0.10.1 — 2026-11-05
- **Dernière recharge** : l'équivalent carburant reprend la couleur et l'info-bulle de la table des recharges.

## 0.10.0 — 2026-11-05
- **Bloc « Dernière recharge »** sous la Voiture : une ligne de résumé, le détail au clic (ouvert par défaut sur grand écran).

## 0.9.1 — 2026-11-04
- **Tuiles d'indicateurs réorganisées** par thème, en grille sans trou sur PC comme sur mobile.

## 0.9.0 — 2026-11-04
- **Dépôt organisé par marque** (`brands/stellantis/`), en prévision d'autres marques ; nom neutre `ev-dashboard`.

## 0.8.0 — 2026-11-04
- **En-tête neutre** : titre « Marque Modèle » lu sur la voiture, logo personnel facultatif.

## 0.7.0 — 2026-11-04
- **Reverse proxy Caddy intégré** (facultatif) pour publier le dashboard sur Internet avec un certificat Let's Encrypt.

## 0.6.0 — 2026-11-04
- **Charge en cours estimée** entre deux relevés de la voiture : niveau, autonomie et heure de fin recalculés chaque minute.

## 0.5.0 — 2026-11-04
- **Équivalent essence de chaque recharge**, au prix moyen du carburant le jour de la recharge.

## 0.4.0 — 2026-11-03
- **Page « PREPROD en pause »** à la place de la page d'erreur quand la PREPROD est arrêtée.

## 0.3.2 — 2026-11-03
- **Période « Mois en cours »** par défaut à la première ouverture.

## 0.3.1 — 2026-11-03
- **PREPROD reconnaissable** : application « VE PREPROD » et icônes marquées « TEST ».

## 0.3.0 — 2026-11-03
- **Application Android (PWA)** installable, et connexion conservée 30 jours.

## 0.2.1 — 2026-11-02
- **Nouvelle icône** et mire de connexion remaniée, avec choix du thème.

## 0.2.0 — 2026-11-02
- **Mire de connexion** (mot de passe et code TOTP) pour l'accès depuis Internet.

## 0.1.0 — 2026-11-02
- **Première version** : recharges et trajets synchronisés dans le Google Sheet, coût réel en EDF Tempo, dashboard web.
