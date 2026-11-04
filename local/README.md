# Fichiers personnels (non versionnés)

Ce dossier est monté en lecture seule dans le dashboard (`/local`). Tout ce que vous y déposez reste hors de git, sauf ce README.

**Logo de l'en-tête.** Déposez un fichier `.svg` ou `.png` de 512 Ko au plus, par exemple le logo de la marque de votre véhicule, puis indiquez son nom dans `.env` :

```bash
DASHBOARD_LOGO=logo.svg
# Logo monochrome noir : l'inverser en thème sombre pour qu'il reste lisible
DASHBOARD_LOGO_INVERT=1
```

Sans `DASHBOARD_LOGO`, l'en-tête affiche l'icône de l'application.

Les logos de marque sont des marques déposées, protégées par le droit d'auteur. Gardez-les ici, ne les publiez pas dans le dépôt.
