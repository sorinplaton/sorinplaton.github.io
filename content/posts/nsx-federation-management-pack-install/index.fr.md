---
title: "Superviser NSX Federation dans VCF Operations, partie 2 : construction, installation et données collectées par le pack"
date: 2026-11-18
description: "Partie 2 du management pack NSX Federation pour VCF Operations 9.1.1 : ce que le pack supervise sur une fédération de lab à deux emplacements (76 objets, 586 métriques, 87 définitions d'alerte, quatre groupes personnalisés, cinq tableaux de bord, treize vues et deux rapports), la construction de l'adaptateur avec l'Integration SDK, l'installation pas à pas avec captures d'écran (registre en HTTPS, approbation du certificat sur le proxy cloud, pack non signé, compte) et l'envoi des alertes par courriel."
summary: "L'adaptateur collecte les données d'une fédération NSX 9.1 à deux emplacements en 25 à 45 secondes. Pour qu'il fonctionne dans VCF Operations 9.1.1, il faut un registre d'images servi en HTTPS, un proxy cloud qui approuve le certificat du registre et le réglage qui autorise un pack non signé. Cet article liste ce que supervise le pack (objets, métriques, alertes, groupes, tableaux de bord, vues), détaille chaque étape de l'installation avec des captures d'écran, puis montre comment envoyer les alertes par courriel et comment le pack a été testé dans le lab."
tags: ["VCF 9.1.1", "NSX Federation", "VCF Operations", "Management pack", "Integration SDK", "Lab"]
categories: ["Notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-federation-mp/ops-dashboard-stretched.webp"]
params:
  seoDescription: "Construire et installer un management pack NSX Federation sur mesure dans VCF Operations 9.1.1 : registre HTTPS, approbation du certificat, pack non signé, puis objets, métriques, alertes, groupes, tableaux de bord et vues qu'il fournit."
---

**En bref**

- Dans le lab, le pack présente dans VCF Operations 9.1.1 une fédération NSX à deux emplacements sous forme de 76 objets, 586 métriques, 87 définitions d'alerte et cinq tableaux de bord.
- L'installation exige un registre d'images servi en HTTPS, un proxy cloud qui approuve le certificat du registre et le réglage qui autorise un pack non signé.
- Un cycle de collecte de la fédération du lab prend 25 à 45 secondes.
- Les étapes se trouvent dans [Installer le pack](#installer-le-pack).

La [partie 1](/fr/posts/nsx-federation-management-pack-design/) s'achevait sur une conception, sans une ligne de code. Le pack tourne désormais dans VCF Operations 9.1.1, et l'écriture de l'adaptateur a pris moins de temps que son installation.

Le pack décrit ici est la version 1.13.0. Son code source est sur GitHub : [sorinplaton/vcf-ops-nsx-federation-mp](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp). Trois fichiers du dépôt constituent la référence complète :

| Fichier | Contenu |
| --- | --- |
| [docs/FEATURES.fr.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.fr.md) | Chaque type d'objet, métrique, propriété et alerte, avec la condition, le seuil et la recommandation de chaque alerte |
| [docs/INSTALL.fr.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/INSTALL.fr.md) | L'installation sous forme de liste de contrôle, avec chaque commande et la machine où elle s'exécute |
| [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/OPERATIONS.md) (en anglais) | Réglages du compte, approbation des certificats, dimensionnement, mise à niveau, dépannage et tests |

## Le lab

Le lab est la fédération décrite dans la [partie 1](/fr/posts/nsx-federation-management-pack-design/) : deux emplacements, `site-a-wld01` et `site-a-wld03`, et VCF Operations 9.1.1 avec un seul proxy cloud, `opscollector-01a`. Pour cette partie, il dispose aussi d'un Standby Global Manager : `nsx-gm-01a` (`gm-site-a`) est actif et `nsx-gm-01b` (`gm-site-b`) est son standby. La construction se fait sur un jumphost Linux équipé de Docker, qui héberge aussi le registre d'images sur le port 5000.

![Le lab de la partie 2 : le Global Manager actif nsx-gm-01a et le standby nsx-gm-01b, les emplacements site-a-wld01 et site-a-wld03 avec leurs Local Managers et leurs clusters Edge à deux nœuds reliés par RTEP, VCF Operations avec le proxy cloud opscollector-01a, et le jumphost qui construit le pack et sert son image à travers le routeur du lab](/images/nsx-federation-mp/lab-topology.fr.svg?v=7)

L'adaptateur NSX Federation du schéma est le collecteur du pack : un conteneur qui tourne sur le proxy cloud, lit les Global Managers et les Local Managers par l'API NSX et envoie le résultat à VCF Operations.

Le Standby Global Manager a été déployé à partir de la même appliance 9.1.1 que le Global Manager actif, en taille Small, sur le cluster de `site-a-wld03`, avec une adresse et un enregistrement DNS dans le réseau de gestion.

Il a été enregistré sur le Global Manager actif par l'API, sous le nom `gm-site-b` en mode STANDBY, avec son adresse, les identifiants du compte admin et l'empreinte de son certificat (`PUT /global-manager/api/v1/global-infra/global-managers/gm-site-b`). La première synchronisation complète s'est terminée en quelques minutes.

## Ce que le pack supervise

Le pack fournit :

| Contenu | Nombre |
| --- | --- |
| Types d'objets | 16 (76 objets de 15 types dans le lab) |
| Métriques par cycle dans le lab | 586, en 25 groupes |
| Définitions d'alerte | 87, avec 153 symptômes et 53 recommandations |
| Groupes personnalisés | 4 |
| Tableaux de bord | 5 |
| Vues de type liste | 13 |
| Rapports | 2 |

La liste complète des objets, des métriques et des alertes se trouve dans [FEATURES.fr.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.fr.md).

### Objets

Dans le lab, le pack crée 76 objets de 15 types, dont 8 tunnels RTEP, 16 voisins BGP du Tier-0 et 14 réalisations d'objets étendus sur un emplacement. Le seizième type, la règle de pare-feu globale, s'active stratégie par stratégie et est resté désactivé pour cette installation. Les nœuds Edge, les tunnels, les canaux de synchronisation et les service routers du Tier-0 sont identifiés par des ID NSX : un nœud Edge renommé ou redéployé conserve donc ses objets et leur historique. Le pack fournit aussi une arborescence d'inventaire, sous « Inventory > Detailed View », dernier onglet, « NSX Federation ». Les données collectées pour chaque type d'objet figurent dans [les types d'objet de FEATURES.fr.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.fr.md#types-dobjet).

### Groupes personnalisés

Le pack fournit quatre groupes personnalisés, sous « Inventory > Custom Groups > Environment » : tous les objets du pack (76 membres dans le lab), managers et emplacements (9), connectivité intersite (43) et objets étendus (24). L'appartenance dépend du type d'objet : un nouvel emplacement ou un nouveau tunnel rejoint donc son groupe sans modification. Le script `install/federation_groups.py` ajoute un groupe par fédération, « NSX Federation - <fédération> », qui s'appuie sur l'ID de fédération que porte chaque objet du pack. Les groupes sont listés dans [la section des groupes de FEATURES.fr.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.fr.md#tableaux-de-bord-vues-rapports-et-groupes).

### Métriques

Les 586 métriques du lab sont rangées en 25 groupes, comme dans les intégrations natives, et la clé d'une métrique contient son groupe, par exemple `sync|standby_in_sync`. Vingt-six d'entre elles sont des indicateurs clés, affichés en premier dans le résumé d'un objet. L'adaptateur convertit en taux les totaux cumulés que renvoie NSX, comme les rejets par minute sur un tunnel RTEP, et calcule la part des paquets rejetés par un tunnel dès qu'au moins 50 paquets sont passés depuis le cycle précédent. Chaque emplacement expose aussi ce que la page « Location Manager » de NSX affiche pour lui : flux de réplication, latence et files de synchronisation, la latence étant comparée au RTT maximal défini dans NSX (250 ms par défaut). Chaque métrique et chaque propriété figurent dans [les métriques de FEATURES.fr.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.fr.md#métriques-et-propriétés).

L'objet fédération porte aussi des valeurs qui couvrent toute la fédération. Le 22 novembre, elles indiquent :

| Valeur | Dans le lab |
| --- | --- |
| Emplacements en bonne santé | 2 sur 2 (100 %) |
| Tunnels RTEP, BGP tombé, rejets | 8, aucun tombé, aucun rejet |
| Jours avant la première expiration de certificat | 328,5 (certificat d'un Local Manager) |
| Alarmes critiques ouvertes | 1 |
| Plus grand écart d'horloge | 0,07 seconde |
| Limite de configuration la plus utilisée | 97,6 % |
| Objets surchargés | Aucun |
| Jours avant la première expiration de mot de passe | 35 |
| Règles de pare-feu à revoir | Aucune |

### Alertes

Les 87 définitions d'alerte couvrent chaque type d'objet, d'une fédération sans Global Manager actif à une session BGP instable sur le Tier-0. Un symptôme est une condition portant sur une métrique, sur une propriété ou sur un événement, ici sur une propriété :

*Se trouve dans : le fichier d'alertes du pack, dans `content/alertdefs`.*

```xml
<SymptomDefinition adapterKind="NSXFederation" id="SymptomDefinition-NSXFederation-gm_backup_off"
                   name="Backup is not configured on the Global Manager" resourceKind="global_manager">
    <State severity="immediate">
        <Condition key="backup_enabled" operator="=" thresholdType="static" type="property"
                   value="False" valueType="string"/>
    </State>
</SymptomDefinition>
```

Vingt et une alertes font baisser le badge « Risk » au lieu du badge « Health », entre autres pour les sauvegardes, l'expiration des certificats et les limites de configuration. Les symptômes susceptibles d'osciller exigent deux cycles de collecte (10 minutes) pour déclencher une alerte et deux pour l'effacer, et trois alertes utilisent un seuil dynamique qui demande plusieurs jours d'historique. Une politique VCF Operations peut remplacer un seuil fixe sous « Override Symptoms » sans reconstruire le pack. Dans le lab, quatre alertes se déclenchent, issues de trois définitions : l'absence de sauvegarde sur les deux Global Managers, une mise à niveau NSX en pause sur le Local Manager de site-a-wld01 et une limite de configuration à 97,6 % sur site-a-wld03. Les seuils fixes figurent dans le [tableau des seuils de FEATURES.fr.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.fr.md#seuils), et chaque définition, avec sa condition et sa recommandation, dans [les alertes de FEATURES.fr.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.fr.md#alertes).

### Les alarmes NSX et l'intégration NSX native

L'adaptateur relaie les alarmes de fédération du Global Manager et de chaque Local Manager sous forme d'événements, au même format que l'intégration NSX native, et donne aux 15 types d'alarme de fédération de NSX 9.1.1 une alerte et une recommandation propres. L'intégration native ne couvre que les managers qui y sont ajoutés comme comptes NSX : les alarmes du Global Manager n'arrivent donc dans VCF Operations que par ce pack. Pour un Local Manager que l'intégration native supervise déjà, l'adaptateur lui laisse les alarmes, les alertes de nœud Edge et l'alerte de limite de configuration, et relie les objets Edge aux siens. Dans le lab, c'est le cas de site-a-wld01, et le pack déclenche ces alertes pour site-a-wld03. Une alarme de fédération sans définition, ainsi que chaque alarme critique du Global Manager, arrive sous la forme d'une alerte « Notification event », sans recommandation. Les règles sont décrites dans [le résumé de la collecte de FEATURES.fr.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.fr.md#ce-que-fait-la-collecte).

### Tableaux de bord

Cinq tableaux de bord sont regroupés dans un dossier nommé « NSX Federation » : Federation Overview, Location Matrix, Switchover Readiness, Inter-site Connectivity et Stretched Objects. Dans chaque tableau de bord, une liste alimente un widget « Scoreboard », et dans tous sauf Stretched Objects, un clic sur l'une de ses valeurs en affiche la courbe de tendance. La liste des tableaux de bord figure dans [FEATURES.fr.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.fr.md#tableaux-de-bord-vues-rapports-et-groupes).

Federation Overview avec gm-site-a sélectionné. Les alertes ouvertes sont celles de la sauvegarde et de la mise à niveau en pause :

![Le tableau de bord Federation Overview : deux définitions d'alerte ouvertes (sauvegarde, mise à niveau en pause), la liste avec gm-site-a et gm-site-b, et le widget « Scoreboard » de gm-site-a avec Standby in sync à 1, les nœuds hors ligne à 0 et l'expiration des certificats](/images/nsx-federation-mp/ops-dashboard-standby.webp)

Avec la fédération sélectionnée, le widget « Scoreboard » se termine par les agrégats de fédération. La capture date du 19 novembre : ses valeurs de certificat et d'alarmes diffèrent donc du tableau du 22 novembre ci-dessus :

![Le tableau de bord Federation Overview avec la fédération sélectionnée : le widget « Scoreboard » affiche les erreurs d'API, le quota d'appels, les emplacements en bonne santé à 100, RTEP BGP tombé à 0, les paquets RTEP rejetés par minute à 0, l'expiration des certificats à 330,9 jours et les alarmes critiques à 0](/images/nsx-federation-mp/ops-dashboard-rollups.webp)

Inter-site Connectivity montre les taux d'un tunnel RTEP :

![Le tableau de bord Inter-site Connectivity : canaux de synchronisation et nœuds Edge, tunnels RTEP, voisins BGP du Tier-0 et les taux du tunnel RTEP sélectionné](/images/nsx-federation-mp/ops-dashboard-connectivity.webp)

Location Matrix, avec site-a-wld03 sélectionné. Les deux emplacements sont en bonne santé et la fédération a un standby prêt à prendre le relais :

![Le tableau de bord Location Matrix : les deux emplacements avec Healthy 1 et GM connection 1, les chiffres de la fédération (2 emplacements, 2 en bonne santé, 100 %, RTEP BGP down 0, Ready to take over 1), ainsi que les valeurs clés et les propriétés de site-a-wld03](/images/nsx-federation-mp/ops-dashboard-matrix.webp)

Switchover Readiness, avec le standby gm-site-b sélectionné. Il est prêt à prendre le relais, synchronisé et dans la même version que le Global Manager actif :

![Le tableau de bord Switchover Readiness : gm-site-a actif et gm-site-b en standby, la fédération avec Ready to take over 1, ainsi que les valeurs clés et les propriétés de gm-site-b avec l'état de préparation au switchover « ready »](/images/nsx-federation-mp/ops-dashboard-switchover.webp)

Stretched Objects, avec le segment fed-stretched sélectionné :

![Le tableau de bord Stretched Objects : les passerelles, segments et stratégies étendus, la réalisation par emplacement, ainsi que les valeurs clés et les propriétés du segment fed-stretched](/images/nsx-federation-mp/ops-dashboard-stretched.webp)

### Vues et rapports

Neuf vues de type liste portent sur les emplacements, les canaux de synchronisation, les tunnels RTEP, les voisins BGP du Tier-0, la capacité des nœuds Edge, les service routers du Tier-0, la revue des règles de pare-feu, les changements récents et la réalisation des objets étendus, et quatre autres montrent la disponibilité sur les 30 derniers jours. Deux rapports les réunissent en PDF : « NSX Federation - Health report », avec les neuf premières vues, et « NSX Federation - Availability report », avec les quatre autres, à planifier chaque mois sur l'objet fédération. Les noms des vues figurent dans [FEATURES.fr.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.fr.md#tableaux-de-bord-vues-rapports-et-groupes).

## Installer le pack

L'installation se fait en six étapes, depuis un jumphost qui construit l'adaptateur et sert son image.

![Construction et déploiement du pack : le dépôt GitHub est cloné sur le jumphost, le conteneur d'outillage construit l'image et le .pak, l'image part vers le registre HTTPS du jumphost, le .pak est installé dans VCF Operations avec l'option PAK non signé activée, le compte déploie l'adaptateur sur le proxy cloud, qui tire l'image par son digest à travers le routeur du lab, lit les Global Managers et les Local Managers par leur API REST et envoie les données au nœud d'analyse](/images/nsx-federation-mp/build-topology.fr.svg?v=13)

Le pack se compose de deux éléments. Le fichier `.pak` contient le contenu du pack (modèle d'objets, alertes, tableaux de bord) et l'adresse de l'image de l'adaptateur. L'adaptateur lui-même est une image de conteneur, que le proxy cloud récupère avec Docker au démarrage du compte. Le registre que désigne le pack doit donc être joignable par le proxy cloud, en HTTPS, avec un certificat que le proxy cloud approuve. Si la récupération échoue, le pack s'installe quand même, mais le compte ne collecte jamais.

Le [guide d'installation](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/main/docs/INSTALL.fr.md) décrit quatre modes d'installation, qui ne diffèrent que par l'origine de cette image :

- [Télécharger le pack publié](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/main/docs/INSTALL.fr.md#option-a--télécharger-le-pack-publié). C'est la voie la plus rapide, pour un site dont le proxy cloud joint `ghcr.io`. L'image y est publique, et il n'y a rien à construire.
- [Télécharger le pack publié et copier l'image dans un registre interne](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/main/docs/INSTALL.fr.md#variante--copier-limage-dans-un-registre-interne). Ce mode convient à un site dont le proxy cloud n'a pas accès à Internet, ou dont les images doivent être analysées et servies par un registre interne. Il n'y a toujours rien à construire.
- Construire le pack à partir des sources. Ce mode s'adresse à qui modifie le pack, veut le construire à partir des sources ou exploite un lab avec son propre registre sur l'hôte de construction. C'est le mode du lab, et celui que suit cet article.
- [Construire le pack avec un Harbor existant](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/main/docs/INSTALL.fr.md#variante--un-registre-harbor-existant). Le build est le même, et l'image est poussée vers Harbor à la place du petit registre de l'étape 1.

Les quatre modes installent le même pack, et leurs étapes sont communes à partir de l'étape 4. Le pack publié et son image sont signés avec cosign, et le guide montre comment [vérifier les deux signatures](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/main/docs/INSTALL.fr.md#vérifier-les-signatures-recommandé) avant l'installation.

Le lab utilise le build à partir des sources. Pour le pack publié, le guide indique que le téléchargement et les commandes de vérification des signatures ont été exécutés sur une version publiée, et que la récupération de l'image sur `ghcr.io` par le proxy cloud n'a pas encore été testée dans le lab. La copie vers un registre interne a été testée avec un `registry:2` local, mais pas avec Harbor. La variante Harbor n'a pas été testée.

### Prérequis du jumphost

Le jumphost construit le pack, exécute les tests et sert l'image. Avant la construction, il lui faut :

- **Linux avec Docker Engine**. Les outils du SDK, les tests et le registre tournent dans des conteneurs. Les commandes de cette section sont écrites pour un Ubuntu fraîchement installé.
- **`git`**, pour cloner le dépôt.
- **`unzip`**, pour lire dans le pack l'adresse de l'image après le build (étape 2).
- **Python 3**, dans une version récente quelconque, pour les scripts qui tournent hors de l'image d'outillage : `tools/gen_*.py` (alertes, tableaux de bord, vues, groupes) et `tools/repack.py`.
- **`openssl`**, pour créer le certificat du registre (étape 1).
- **Un accès root par `sudo`**, pour l'installation des paquets et pour les commandes `docker`. Les commandes sont préfixées par `sudo` là où il le faut : l'utilisateur n'a donc pas besoin d'être membre du groupe `docker`.
- **Un accès sortant** vers GitHub, Docker Hub et PyPI, pour le dépôt, les images `python:3.11-slim` et `registry:2` et le paquet du SDK.
- **TCP 443 vers chaque manager NSX**, Global Managers et Local Managers, pour l'exécution locale des tests (`tools/localrun.py`).
- **Le port du registre ouvert au proxy cloud**, 5000 dans le lab. Ici, le proxy cloud l'atteint à travers le routeur du lab.

Sur un Ubuntu fraîchement installé, les outils s'installent comme suit. Docker vient du dépôt Docker, pour que `docker build` utilise BuildKit.

Le bloc installe les paquets de base, ajoute la clé de signature et la source de paquets de Docker, puis installe Docker depuis cette source. La dernière commande récupère une image de test sur Docker Hub et l'exécute. Elle affiche « Hello from Docker! », ce qui montre que Docker fonctionne et accède à Internet.

*À exécuter sur : le jumphost.*

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git unzip openssl python3 python3-requests
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
sudo docker run --rm hello-world
```

Les adresses, les noms et les mots de passe dont les commandes ont besoin sont regroupés dans un seul fichier de paramètres, `~/fedmp.env`. Les commandes des étapes qui suivent ne contiennent donc ni adresse ni mot de passe. Le dépôt fournit un exemple à copier et à remplir :

*À exécuter sur : le jumphost.*

```bash
cd ~
git clone https://github.com/sorinplaton/vcf-ops-nsx-federation-mp.git
cp vcf-ops-nsx-federation-mp/install/fedmp.env.example ~/fedmp.env && chmod 600 ~/fedmp.env
nano ~/fedmp.env
```

Le fichier définit entre autres les valeurs suivantes. `IP` est l'adresse que le proxy cloud utilise pour joindre le registre :

*Se trouve dans : `~/fedmp.env` sur le jumphost.*

```bash
IP=10.x.x.x                        # adresse du jumphost
VERSION=1.13.0                     # version du pack, comme dans pack/manifest.txt
GOVC_URL=vcenter.example.com       # vCenter qui héberge la VM du proxy cloud
GOVC_USERNAME='administrator@vsphere.local'
GOVC_PASSWORD='<mot de passe>'
GOVC_INSECURE=1
PROXY_VM=nom-de-la-vm-du-proxy-cloud
PROXY_ROOT_PW='<mot de passe root du proxy cloud>'
```

Chaque bloc de commandes qui utilise l'une de ces valeurs commence par la ligne qui charge le fichier et les exporte :

```bash
set -a; source ~/fedmp.env; set +a
```

`set -a` demande au shell d'exporter toute variable définie à partir de ce point, `source` lit le fichier et `set +a` arrête l'export. Sans l'export, les valeurs restent dans le shell, et les scripts et les conteneurs lancés par les commandes ne les voient pas. Les valeurs durent autant que le shell : un nouveau terminal demande de relancer la ligne, et un bloc collé sans elle s'exécute avec des adresses vides.

Le fichier contient des mots de passe. Seul son propriétaire peut le lire, et `fedmp.env` figure dans le `.gitignore` du dépôt. Une fois l'installation terminée, il est à supprimer ou à déplacer dans un coffre.

Deux autres outils ne servent qu'à certaines étapes :

- `govc`, le client en ligne de commande de vCenter, avec un accès au vCenter qui héberge la VM du proxy cloud. L'étape 3 s'en sert pour copier le certificat du registre dans la VM du proxy cloud.
- Google Chrome, Node.js 22 ou ultérieur et le module Python `requests`, pour l'installation scriptée du pack non signé (facultative, décrite dans [docs/INSTALL.fr.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/main/docs/INSTALL.fr.md#par-script)), avec un accès HTTPS à VCF Operations.

`govc` s'installe depuis ses versions publiées sur GitHub. La dernière ligne affiche la version :

*À exécuter sur : le jumphost.*

```bash
curl -fsSL "https://github.com/vmware/govmomi/releases/latest/download/govc_$(uname -s)_$(uname -m).tar.gz" \
  | sudo tar -C /usr/local/bin -xzf - govc
govc version
```

### Construire l'adaptateur

Le pack s'appuie sur le VCF Operations Integration SDK 1.3.1, présenté dans la [partie 1](/fr/posts/nsx-federation-management-pack-design/). Ses outils exigent une version de Python absente du jumphost ; ils s'exécutent donc depuis une petite image d'outillage :

*Se trouve dans : `tooling/Dockerfile` du dépôt, construit sur le jumphost.*

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends docker.io git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir vmware-aria-operations-integration-sdk pytest
WORKDIR /work
```

Le code de l'adaptateur tient en deux fichiers Python :

| Fichier | Rôle |
| --- | --- |
| `nsxclient.py` | Un client REST NSX en lecture seule, avec pagination par curseur, quota d'appels par manager, nouvelle tentative après attente sur les codes HTTP 429 et 503, contrôle du certificat par empreinte SHA-256 ou par chaîne de certification et nom d'hôte, et petit cache sur disque pour les données qui changent peu |
| `adapter.py` | Le modèle d'objets, la logique de collecte et les événements |

`mp-build`, l'un des outils du SDK, construit l'image de l'adaptateur, la pousse vers le registre et écrit le `.pak`. Les commandes de construction forment l'étape 2 de l'installation, parce qu'elles poussent l'image vers le registre de l'étape 1.

Les six étapes ci-dessous suivent l'ordre qui fonctionne. Chaque échec rencontré dans le lab est décrit sous l'étape qui l'évite.

### Étape 1 : servir le registre en HTTPS

L'image de l'adaptateur n'est pas dans le `.pak` : il faut donc au proxy cloud un registre où la récupérer, et il ne la récupère qu'en HTTPS. Cette étape démarre un petit registre sur le jumphost, avec un certificat créé pour lui.

En production, l'image va dans le registre déjà en place (Harbor, par exemple), comme le décrit la [variante Harbor](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/main/docs/INSTALL.fr.md#variante--un-registre-harbor-existant) du guide. Si son certificat est émis par une autorité que le proxy cloud approuve déjà, l'étape 3 est inutile. Le lab n'a pas de tel registre : il utilise donc un conteneur `registry:2` sur le jumphost, avec un certificat autosigné.

La commande `openssl` crée la clé et un certificat valable 825 jours, qui porte l'adresse de `IP`. La commande `docker run` démarre le registre avec ce certificat sur le port 5000, garde les images dans le volume Docker `registry-data` et relance le conteneur après un redémarrage de l'hôte :

*À exécuter sur : le jumphost.*

```bash
set -a; source ~/fedmp.env; set +a
mkdir -p ~/fedmp-registry/certs && cd ~/fedmp-registry
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout certs/registry.key -out certs/registry.crt -subj "/CN=fedmp-registry" \
  -addext "subjectAltName=IP:$IP,IP:127.0.0.1,DNS:localhost"

sudo docker run -d --name fedmp-registry --restart unless-stopped \
  -p $IP:5000:5000 -p 127.0.0.1:5000:5000 \
  -v registry-data:/var/lib/registry -v $PWD/certs:/certs:ro \
  -e REGISTRY_HTTP_TLS_CERTIFICATE=/certs/registry.crt \
  -e REGISTRY_HTTP_TLS_KEY=/certs/registry.key registry:2
```

Le build pousse l'image vers `localhost:5000`. Le certificat contient donc aussi `127.0.0.1` et `localhost`, et le registre écoute également sur l'adresse locale.

La vérification appelle le registre sur l'adresse que le proxy cloud utilisera et n'accepte que le certificat qui vient d'être créé. Une réponse prouve donc à la fois l'écoute et le certificat :

```bash
set -a; source ~/fedmp.env; set +a
curl --cacert ~/fedmp-registry/certs/registry.crt https://$IP:5000/v2/_catalog
```

Un registre neuf répond `{"repositories":[]}`. Il reste vide jusqu'à ce que le build y pousse l'image de l'adaptateur.

L'adresse inscrite dans le certificat est celle que le proxy cloud utilise pour joindre le registre. La même adresse donne son nom au dossier du certificat de l'étape 3, et `tools/repack.py` l'inscrit dans le pack. Un registre déplacé sur un autre hôte demande donc un nouveau certificat, un nouveau dossier sur le proxy cloud et un pack réécrit.

Le premier registre du lab servait du HTTP en clair. Avec lui, l'instance d'adaptateur est passée de `STARTING` à `FAILED` avec un message d'état vide, et le journal du registre ne contenait aucune requête.

Une capture de paquets sur le port du registre en a donné la raison : le proxy cloud ouvrait une connexion et envoyait un ClientHello TLS. Il ne se rabat pas sur HTTP, et le registre a donc été recréé avec le certificat ci-dessus.

### Étape 2 : construire le pack et pousser l'image

Cette étape produit les deux éléments du pack : l'image de l'adaptateur dans le registre de l'étape 1, et le fichier `.pak` qui la désigne.

Le premier `docker build` crée `fedmp-tooling`, l'image qui contient les outils du SDK. Le `docker run` lance ensuite `mp-build` depuis cette image. `mp-build` construit l'image de l'adaptateur avec le Docker du jumphost, par le socket monté, la pousse vers le registre et écrit le `.pak`. Le conteneur tourne avec l'ID de l'utilisateur courant et l'ID du groupe `docker` : il peut ainsi utiliser le socket, et les fichiers qu'il écrit dans le clone appartiennent à l'utilisateur qui lance le build. Le build télécharge les images de base depuis Docker Hub et les paquets Python depuis PyPI.

*À exécuter sur : le jumphost.*

```bash
set -a; source ~/fedmp.env; set +a
cd ~/vcf-ops-nsx-federation-mp
sudo docker build -t fedmp-tooling tooling

sudo docker run --rm --network host -u $(id -u):$(getent group docker | cut -d: -f3) -e HOME=$PWD/.sdkhome \
  -v /var/run/docker.sock:/var/run/docker.sock -v $(which docker):/usr/bin/docker -v $PWD:$PWD -w $PWD/pack \
  fedmp-tooling mp-build --no-ttl -r localhost:5000/nsxfederation \
  --registry-username lab --registry-password lab
```

Le résultat est `pack/build/NSXFederation_$VERSION.pak`.

`mp-build` démarre l'adaptateur pour un test sur `127.0.0.1:8080`. Si ce port est pris sur le jumphost, le build échoue avec `Bind for 127.0.0.1:8080 failed: port is already allocated`. L'option `-P 18080`, ou tout autre port libre, ajoutée aux options de `mp-build`, déplace le port de test. Le pack utilise toujours le port 443 sur le proxy cloud.

L'image est poussée vers `localhost:5000`. Docker accepte le certificat autosigné d'un registre joint par l'adresse locale : le jumphost n'a donc pas besoin de son propre dossier de certificat. Le registre de l'étape 1 ne demande aucune authentification. `mp-build` demande quand même un utilisateur et un mot de passe, et n'importe quelle valeur convient.

Lorsque l'adresse du registre est donnée sous la forme `host:port`, sans domaine, `mp-build` inscrit Docker Hub dans le pack. Le proxy cloud chercherait alors l'image sur Docker Hub, et le compte échouerait. Un court script, `tools/repack.py`, ouvre le pack et réécrit deux lignes, `REGISTRY` et `REPOSITORY`, dans le fichier `.conf` que contient `adapter.zip`. Rien d'autre ne change. Il y inscrit l'adresse que le proxy cloud utilise, `$IP:5000` :

```bash
set -a; source ~/fedmp.env; set +a
cd ~/vcf-ops-nsx-federation-mp
python3 tools/repack.py pack/build/NSXFederation_$VERSION.pak $IP:5000 nsxfederation
```

Les deux lignes suivantes extraient du pack la configuration de l'adaptateur et affichent l'adresse où le proxy cloud ira chercher l'image :

```bash
set -a; source ~/fedmp.env; set +a
cd ~/vcf-ops-nsx-federation-mp
unzip -p pack/build/NSXFederation_$VERSION.pak adapter.zip > /tmp/adapter.zip
unzip -p /tmp/adapter.zip '*.conf' | grep -E '^(REGISTRY|REPOSITORY)='
```

Le résultat attendu est `REGISTRY=<IP>:5000`, avec la valeur de `IP`, et `REPOSITORY=/nsxfederation`. Le fichier contient aussi le digest de l'image, que le proxy cloud utilise pour la récupérer :

*Se trouve dans : le fichier `.conf` de `adapter.zip`, dans le `.pak`, réécrit par `tools/repack.py` sur le jumphost.*

```
REGISTRY=10.x.x.x:5000
REPOSITORY=/nsxfederation
DIGEST=sha256:dd80...b4cb
```

La dernière vérification interroge le registre. La réponse donne le dépôt `nsxfederation` et ses tags :

```bash
set -a; source ~/fedmp.env; set +a
curl --cacert ~/fedmp-registry/certs/registry.crt https://$IP:5000/v2/nsxfederation/tags/list
```

### Étape 3 : faire approuver le certificat du registre par le proxy cloud

Cette étape concerne un registre dont le proxy cloud n'approuve pas encore le certificat, comme le certificat autosigné du lab. Le proxy cloud refuse un tel certificat : la récupération de l'image échoue et le compte reste `FAILED`. Le journal du registre affiche alors l'erreur suivante (10.0.6.33 est le routeur du lab, qui masque le proxy cloud par NAT) :

*Sortie : le journal du conteneur du registre sur le jumphost.*

```
http: TLS handshake error from 10.0.6.33:56544: remote error: tls: bad certificate
```

Le proxy cloud exécute Docker, et Docker approuve un registre privé dès lors que son certificat se trouve dans un dossier portant le nom du registre, port compris :

*Se trouve sur : le proxy cloud `opscollector-01a`.*

```
/etc/docker/certs.d/10.x.x.x:5000/ca.crt
```

SSH est désactivé sur le proxy cloud et l'est resté. Le fichier a été écrit par les opérations invité de vCenter, avec `govc` et le compte root de l'appliance. Le lab a utilisé `govc guest.run`. Les deux commandes ci-dessous donnent le même résultat : `guest.mkdir` crée le dossier dans la VM, et `guest.upload` y dépose le certificat sous le nom `ca.crt`. Elles s'exécutent sur le jumphost, qui détient le fichier de paramètres et le certificat. `govc` s'y installe comme indiqué dans les prérequis.

*À exécuter sur : le jumphost, avec `govc` et un accès au vCenter qui héberge la VM du proxy cloud.*

```bash
set -a; source ~/fedmp.env; set +a
govc guest.mkdir -vm "$PROXY_VM" -l "root:$PROXY_ROOT_PW" -p /etc/docker/certs.d/$IP:5000
govc guest.upload -vm "$PROXY_VM" -l "root:$PROXY_ROOT_PW" -f ~/fedmp-registry/certs/registry.crt \
  /etc/docker/certs.d/$IP:5000/ca.crt
```

La preuve que le proxy cloud approuve le registre arrive à l'étape 5, quand le compte passe à l'état « Collecting ».

Une mise à niveau ou un redéploiement du proxy cloud peut faire disparaître ce fichier. Il faut alors refaire cette étape.

### Étape 4 : installer le pack non signé

Deux signatures différentes entrent en jeu. VCF Operations cherche une signature Broadcom dans un `.pak`, et ce pack n'en a pas : VCF Operations le traite donc comme non signé. Le pack publié sur la page des versions du projet est signé par le projet avec cosign. Cette signature permet à la personne qui installe de vérifier l'origine du fichier, et VCF Operations ne la lit pas. L'option décrite ci-dessous reste donc nécessaire dans tous les modes d'installation, même pour un pack dont la signature cosign a été vérifiée.

Sans cette option, l'envoi par l'API de l'appliance échoue :

*Sortie : la réponse de l'API de l'appliance VCF Operations à un envoi depuis le jumphost.*

```
pak_manager.pakfile.no_signature
The PAK file ... is not signed. It is missing required signature files signature.mf and signature.cert
```

Par défaut, VCF Operations 9 bloque les packs non signés. La case « Ignore the PAK file signature checking » reste masquée dans la boîte de dialogue d'envoi tant que ce blocage n'est pas levé.

L'option qui le lève, « Allow unsigned PAK installation », se trouve dans la console d'administration (`https://<vcf-operations>/admin`), sous « Administrator Settings > Security Settings ». Broadcom la décrit dans la KB 415279. Avant son activation, la page se présente ainsi :

![La console d'administration de VCF Operations, Administrator Settings > Security Settings : l'option « Allow unsigned PAK installation » est désactivée, avec le bouton qui permet de l'activer](/images/nsx-federation-mp/ops-admin-unsigned-pak.webp)

L'option s'applique à toute l'instance VCF Operations : tant qu'elle est activée, n'importe quel pack non signé peut être installé. Elle ne reste donc activée que le temps de l'installation :

1. Dans la console d'administration, cliquer sur « Activate unsigned PAK installation ».
2. Dans l'interface principale de VCF Operations, ouvrir « Administration > Integrations > Repository », puis choisir d'ajouter un pack.
3. Sélectionner le fichier `.pak` et cocher les deux cases : celle qui force l'installation même si le pack est déjà installé, et « Ignore the PAK file signature checking ».
4. Lancer l'envoi, accepter l'avertissement relatif au fichier non signé et attendre la fin de l'installation.
5. Revenir dans la console d'administration et désactiver l'installation de fichiers PAK non signés. La console refuse tant que l'installation est en cours.

Le dépôt contient aussi une version scriptée de ces étapes : `install/upi.sh enableUPI` active l'option, et `install/install_unsigned.sh` envoie le pack, attend la fin de l'installation et désactive l'option. [docs/INSTALL.fr.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/main/docs/INSTALL.fr.md#par-script) la décrit.

Après l'installation, le type d'adaptateur `NSXFederation` et la solution `iSDK_NSXFederation` apparaissent dans VCF Operations. Le pack figure, avec sa version, sous « Administration > Integrations > Repository » :

![Administration > Integrations > Repository : le pack NSX Federation, en version 1.13.0, avec un compte](/images/nsx-federation-mp/ops-repository-pack.webp?v=9)

### Étape 5 : créer le compte

Le pack installé ne collecte rien tout seul. Un compte indique à VCF Operations quelle fédération lire, avec quels identifiants, et quel proxy cloud exécute l'adaptateur.

L'interface de VCF Operations appelle l'instance d'adaptateur un compte. Le compte se crée sous « Administration > Integrations > Accounts », avec le type NSX Federation et le proxy cloud comme collecteur.

Il reçoit une ou deux adresses de Global Manager et un jeu d'identifiants, plus un second, facultatif, pour les Local Managers. Avec les deux adresses, le pack suit le rôle actif lors d'un switchover des Global Managers. Un compte supervise une seule fédération.

Les autres réglages sont facultatifs : les stratégies de sécurité dont les règles deviennent des objets, la façon dont le pack fonctionne avec l'intégration NSX native, et la suppression des objets qui ont quitté NSX (désactivée par défaut). [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/OPERATIONS.md) (en anglais) décrit chaque réglage.

Lors de l'enregistrement du compte, il faut valider la connexion et accepter les certificats des Global Managers. L'adaptateur découvre les Local Managers à partir du Global Manager et compare le certificat de chacun d'eux à l'empreinte que le Global Manager a déjà enregistrée pour lui. Une fois un certificat vérifié, chaque connexion du cycle est liée à son empreinte : les identifiants ne sont donc pas envoyés à un serveur qui présente un autre certificat.

Le contrôle des certificats a trois modes :

| Mode | Comportement |
| --- | --- |
| enforce (par défaut) | Arrête la collecte d'un manager dont le certificat ne correspond pas ou pour lequel aucun certificat approuvé n'est enregistré |
| ca | Fonctionne comme « enforce » et approuve en plus un certificat rattaché à une autorité de certification de confiance et valide pour l'adresse qu'utilise le compte |
| report | Poursuit la collecte et déclenche une alerte |

Avec le mode par défaut, aucun manager n'est collecté tant qu'un certificat n'a pas été accepté.

En mode « ca », l'adresse du compte doit être un nom ou une adresse IP inscrits dans le certificat. Les autorités de confiance sont celles du réglage du compte Trusted CA certificates (PEM), qui accepte un texte PEM collé sur une seule ligne, tout certificat d'autorité accepté sur le compte et le magasin de certificats de l'image de l'adaptateur. Une autorité importée dans le proxy cloud ne fait pas partie de ce magasin.

Le pack ne fait que lire dans NSX : un compte en lecture seule suffit donc. Dans le lab, le compte se connecte avec l'utilisateur intégré `audit` de NSX sur les deux Global Managers et les deux Local Managers, et collecte les mêmes objets qu'avec `admin`, sans aucun appel en échec.

Une fois le registre approuvé, le proxy cloud récupère l'image au démarrage de l'instance, qui passe à l'état `STARTED`, puis `DATA_RECEIVING`. L'onglet « Accounts » affiche l'instance avec l'état « Collecting » :

![Administration > Integrations > Accounts : le compte « NSX Federation - nsx-gm-01a » à l'état Collecting, sur le proxy cloud opscollector-01a](/images/nsx-federation-mp/ops-account-collecting.webp)

Cet état prouve que les étapes 1 et 3 ont fonctionné : le proxy cloud a récupéré l'image, démarré l'adaptateur et lit NSX. Un compte qui reste `FAILED` avec un message vide signifie que le proxy cloud n'a pas pu récupérer l'image. Il faut alors vérifier le registre de l'étape 1 et le certificat de l'étape 3, et chercher `tls: bad certificate` dans le journal du registre.

### Étape 6 : attendre trois cycles de collecte

Les données n'arrivent pas toutes en même temps. Avec un intervalle de 5 minutes :

| Cycle | Ce que VCF Operations enregistre |
| --- | --- |
| 1 | Les objets et leurs relations |
| 2 | Les propriétés et les événements ; les clés de métrique sont déclarées |
| 3 | Les métriques |

Un contrôle effectué 2 minutes après le premier cycle montre des objets sans métriques, ce qui fait penser à un pack défectueux. Les métriques apparaissent environ 10 minutes après le démarrage. Un taux se calcule sur deux cycles et apparaît donc un cycle après les autres métriques.

Une installation réussie se termine avec le compte à l'état « Collecting », des métriques sur les objets et aucun appel en échec sur l'objet fédération.

### Mettre le pack à niveau

Une mise à niveau est une nouvelle installation du pack par-dessus l'ancienne, suivie d'un arrêt et d'un redémarrage du compte :

1. Mettre à jour le clone avec `cd ~/vcf-ops-nsx-federation-mp && git pull`, inscrire la nouvelle version dans `VERSION` de `~/fedmp.env`, comme dans `pack/manifest.txt`, puis construire le pack (étape 2). L'installer de la même façon, avec l'option des packs non signés (étape 4).
2. Arrêter puis redémarrer le compte, pour que l'instance d'adaptateur exécute la nouvelle image.
3. Vérifier que le compte atteint l'état « Collecting », et le démarrer de nouveau s'il reste arrêté.

Les métriques reviennent après trois cycles, et les taux après quatre.

Une installation conserve les définitions d'alertes, de symptômes et de recommandations que la nouvelle version ne fournit plus. `install_unsigned.sh` lance `install/cleanup_content.py` après l'installation pour supprimer ces définitions orphelines. Après une installation dans l'interface, il faut lancer le script à la main avec `--delete`. Le script ne supprime que des ID générés par le pack, et il garde une définition orpheline qu'utilise encore une définition d'alerte extérieure au pack.

Une modification de la façon dont l'adaptateur identifie un objet crée un nouvel objet. L'ancien reste dans l'inventaire, sans données, jusqu'à sa suppression, qui efface aussi son historique. `cleanup_content.py --legacy-objects` liste les nœuds Edge, les tunnels RTEP et les canaux de synchronisation encore identifiés par un nom, et `--legacy-objects --delete` les supprime.

## Envoyer les alertes

Le pack déclenche les alertes, VCF Operations les envoie. Deux réglages sont nécessaires, tous deux sous « Administration > Configurations », dans le groupe « Alerts » :

- une instance sortante, qui connaît le serveur de messagerie ;
- une règle de notification, qui sélectionne les alertes.

Le lab n'a pas de serveur de messagerie : les captures du courriel montrent donc les formulaires avant enregistrement. La règle a été testée avec un webhook à la place.

### L'instance sortante

1. Ouvrir « Administration > Configurations > Outbound Settings » et cliquer sur « Add ».
2. Choisir le type de plug-in « Standard Email Plugin » et donner un nom à l'instance.
3. Renseigner « SMTP Host » et « SMTP Port », ainsi que l'adresse et le nom de l'expéditeur. Cocher « Use Secure Connection » et « Requires Authentication » si le serveur de messagerie l'exige. Le formulaire comporte aussi un champ d'identifiants et une adresse de destinataire.
4. Cliquer sur « Test », puis sur « Save ».

Le formulaire, tel qu'il a été ouvert dans le lab :

![Create New Outbound Instance avec le Standard Email Plugin : hôte et port SMTP, connexion sécurisée, adresse et nom de l'expéditeur, identifiants et adresse du destinataire](/images/nsx-federation-mp/ops-email-plugin.webp)

La même liste propose des plug-ins Webhook, Slack, ServiceNow et SNMP trap. La règle de notification décrite ci-dessous fonctionne avec chacun d'eux.

### La règle de notification

1. Ouvrir « Administration > Configurations > Notifications » et cliquer sur « Add ». L'assistant compte cinq étapes, qui correspondent aux points 2 à 6 ci-dessous.
2. **Notification :** saisir un nom, par exemple « NSX Federation alerts by email ».
3. **Define Criteria :** sous « Object Scope », choisir « Object Type » et sélectionner les types d'objets de l'adaptateur NSX Federation. Sous « Alert Scope », laisser « Category » sur toutes les alertes ou le restreindre à « Alert Definition » pour choisir des définitions précises. « Criticality » limite la règle, par exemple à Immediate et Critical. « Notify On » détermine les changements d'état d'une alerte qui déclenchent un message.
4. **Set Outbound Method :** choisir le plug-in de courriel et l'instance sortante créée plus haut.
5. **Select Payload Template :** choisir le modèle qui met en forme le message. Les modèles se gèrent sous « Payload Templates », sur la même page « Configurations ».
6. **Test Notification :** lancer le test, puis cliquer sur « Create ».

L'étape « Define Criteria » se présente ainsi :

![L'étape Define Criteria d'une règle de notification : périmètre des objets, périmètre des alertes avec catégorie, criticité et état de contrôle, et changements d'état à notifier](/images/nsx-federation-mp/ops-notification-rule.webp)

Une règle limitée à Immediate et Critical n'envoie par courriel que les alertes de ces deux niveaux. Les alertes de niveau Information et Warning restent visibles dans l'interface. Une seconde règle, sans limite de criticité et adressée à la boîte aux lettres d'une équipe, permet de les recevoir aussi.

### La même règle avec un script

`install/notification_rules.py` crée la même règle pour une instance sortante qui existe déjà, avec le modèle par défaut de son type de plug-in.

La règle prend les alertes de tous les types d'objets du pack, Immediate et Critical par défaut (`--min-criticality warning` ajoute les Warning). Elle les envoie lorsqu'elles sont nouvelles, mises à jour ou annulées : un ticket ou un fil de discussion apprend ainsi que l'alerte a disparu. `--group` la limite aux membres d'un groupe, par exemple celui d'une fédération.

Dans le lab, l'instance sortante était un webhook vers un petit serveur HTTP sur le jumphost, et la règle était limitée au groupe de la fédération.

Chaque message contient l'objet, son type, le nom de l'alerte, la criticité, les dates de début et d'annulation, et la recommandation. L'instance sortante et la règle de test ont été supprimées ensuite.

## Comportement de la collecte

Les comportements ci-dessous changent la façon de lire une alerte. [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/OPERATIONS.md) (en anglais) les décrit en détail.

### Un appel en échec laisse la valeur inconnue

La collecte se compose surtout d'appels secondaires : état du cluster, alarmes, sauvegardes, certificats, état de synchronisation. Lorsque l'un d'eux échoue, l'adaptateur n'envoie rien pour les valeurs qui en dépendent.

Un appel d'état en échec n'est pas interprété comme « emplacement non connecté », et un appel de sauvegarde en échec n'est pas interprété comme « sauvegarde non configurée ». VCF Operations conserve la dernière valeur connue, et aucune alerte ne se déclenche sur des données qui n'ont jamais été reçues.

L'objet fédération compte ces appels dans la métrique « NSX API calls without an answer » et les liste dans une propriété, avec le manager, l'appel et le code HTTP. Quatre alertes surveillent la collecte elle-même : des données manquantes, une collecte lente ou bridée, une collecte sans aucun Global Manager, et un manager qui a épuisé tout son quota d'appels.

Un appel qui dépasse le délai est retenté une fois avant d'être compté comme en échec. Un manager qui dépasse le délai trois fois de suite, ou qui répond systématiquement par HTTP 429 ou 503, est ignoré jusqu'à la fin du cycle.

### Une seule alerte pour la panne d'un emplacement

Un emplacement qui perd sa connexion au Global Manager entraîne de nombreux objets avec lui : tunnels, sessions BGP, canaux de synchronisation et tous les objets étendus qui le couvrent. Chacun d'eux a une alerte.

Tant que le Global Manager actif signale un emplacement comme non connecté, l'adaptateur écrit le nom de cet emplacement dans une propriété, « Locations in outage », sur les objets qui en dépendent. Vingt-deux définitions d'alerte exigent que cette propriété vaille « none », de même que les alertes d'alarme, à l'exception des alarmes entre Global Managers. Elles restent muettes pendant la panne, et « Location lost its connection to the active Global Manager » est l'alerte à traiter.

Dans le XML des alertes, cette condition est un second symptôme avec `negateCondition="true"` :

*Se trouve dans : le fichier d'alertes du pack, dans `content/alertdefs`.*

```xml
<SymptomSet applyOn="self" operator="and">
    <Symptom ref="SymptomDefinition-NSXFederation-seg_error"/>
    <Symptom ref="SymptomDefinition-NSXFederation-outage_segment" negateCondition="true"/>
</SymptomSet>
```

Le symptôme de panne s'efface trois cycles après le retour de l'emplacement : la fin d'une panne ne déclenche donc pas d'alerte non plus. Une panne sur un autre emplacement continue de déclencher son alerte.

### Le Standby Global Manager

Le standby expose son état de synchronisation : synchronisation en bon état (1 lorsqu'il est synchronisé), entrées restant à envoyer, minutes sans synchronisation et heures écoulées depuis la dernière synchronisation complète. NSX 9.1.1 indique ONGOING comme état normal d'un standby : le pack vérifie donc aussi qu'une synchronisation différentielle tourne après une synchronisation complète terminée. Dans le lab, la métrique de synchronisation vaut 1 et aucune entrée ne reste à envoyer.

Chaque standby indique aussi s'il peut devenir le Global Manager actif tout de suite. La métrique vaut 1 (prêt), ou 0 (pas prêt) avec les raisons dans une propriété : non synchronisé, cluster instable, nœuds hors ligne, version différente, certificat expiré ou mise à niveau en cours. Quand une valeur n'a pas pu être lue, la métrique vaut aussi 0 et la propriété indique ce qui est inconnu. Dans le lab, gm-site-b est prêt.

Exemple avec un switchover planifié : la question est de savoir si l'autre site peut prendre le relais ce soir. À la main, il faut se connecter aux quatre managers NSX de ce lab et comparer les écrans. Le pack y répond à chaque cycle de collecte avec deux valeurs sur l'objet fédération : « A standby Global Manager is ready to take over » et « RTEP tunnels with inter-site BGP down ». La chaîne qui les produit :

| Étape | Hôte appelé | Ce qui est lu | Ce que cela donne |
| --- | --- | --- | --- |
| 1 | Les deux Global Managers | `/api/v1/sites/self`, `/api/v1/cluster/status`, certificats, résumé de mise à niveau | Lequel est actif, ainsi que la version, l'état du cluster, l'expiration des certificats et l'état de mise à niveau de chacun |
| 2 | Le Global Manager actif | `/api/v1/sites`, `/api/v1/sites/status` | L'état de synchronisation du standby, ainsi que l'adresse et l'empreinte du certificat de chaque Local Manager |
| 3 | Chaque Local Manager, à l'adresse lue à l'étape 2 | `/api/v1/transport-nodes` | L'ID de chaque nœud Edge de la fédération |
| 4 | Chaque Local Manager | `/api/v1/transport-nodes/{id}/inter-site/statistics` et `/inter-site/bgp/summary` | L'état et les compteurs de paquets de chaque tunnel RTEP |
| 5 | Aucun, l'adaptateur calcule | Les réponses des étapes 1 à 4 | Prêt ou pas prêt avec les raisons, à partir des étapes 1 et 2. Le nombre de tunnels dont le BGP est tombé, à partir des étapes 3 et 4 |

L'étape 1 appelle deux hôtes, l'étape 3 appelle des hôtes dont l'adresse vient de l'étape 2, et l'étape 5 compare les réponses de managers différents. Cette chaîne explique pourquoi le pack est construit avec l'Integration SDK ([partie 1](/fr/posts/nsx-federation-management-pack-design/#étape-6-le-mode-de-construction)).

### Versions et mises à niveau

NSX Federation se met à niveau dans un ordre fixe : le Standby Global Manager, le Global Manager actif, puis les Local Managers. Trois conditions rompent cet ordre ou bloquent des opérations, et chacune a son alerte :

- les Global Managers exécutent des versions différentes, auquel cas NSX refuse le switchover ;
- un emplacement exécute une release plus récente que celle du Global Manager ;
- une mise à niveau est en cours, en pause ou en échec sur un manager.

L'alerte de release compare les trois premiers nombres de la version NSX. Les managers du lab exécutent la 9.1.1 à deux niveaux de correctif, ce qui ne déclenche aucune alerte.

### Nœuds Edge, service routers du Tier-0 et changements

Un appel par nœud Edge de la fédération fournit l'utilisation de son CPU, de sa mémoire et de son système de fichiers, ainsi que l'état de chaque service router de Tier-0 étendu qu'il porte. Dans le lab, les cœurs datapath sont presque inactifs (moins de 1 %).

L'état considéré comme sain pour un Tier-0 dépend de son mode HA. Une passerelle actif-actif a besoin d'un service router actif sur chaque emplacement de sa portée. Une passerelle actif-standby a besoin d'un service router actif dans la fédération. Lorsqu'un service router change d'état HA entre deux cycles, lors d'un basculement par exemple, la passerelle déclenche un avertissement pendant deux cycles. Dans le lab, fed-t0 est en actif-actif avec quatre service routers, tous actifs avec le routage établi.

L'adaptateur compare chaque cycle aux précédents et enregistre les changements : le Global Manager actif, les versions, les emplacements, les nœuds Edge, l'état HA des service routers, et les objets étendus créés, modifiés ou supprimés, avec l'utilisateur enregistré par NSX. Chaque changement est un événement d'information sur l'objet concerné. La fédération liste les dix derniers, du plus récent au plus ancien : l'historique de ses propriétés sert ainsi d'historique des changements.

Un cycle au cours duquel un appel a échoué ne signale aucun retrait : un trou dans les données n'apparaît donc pas comme un changement. Les objets étendus sont lus toutes les 30 minutes : une modification peut donc mettre jusqu'à 30 minutes à apparaître.

### Horloges, limites de configuration et surcharges

À chaque cycle, l'horloge de chaque manager est comparée à celle du Global Manager actif, à partir de l'heure système que renvoie NSX. L'alerte se déclenche au-delà de 5 secondes, ou lorsque le service NTP du manager ne tourne pas. Dans le lab, les trois autres managers sont à moins d'un dixième de seconde du Global Manager actif.

Chaque Local Manager indique la part de chaque limite de configuration NSX qu'il utilise, comme sur sa page « Capacity », classée selon les seuils que NSX applique. Les objets globaux comptent sur chaque emplacement de leur portée : les emplacements reflètent donc aussi la fédération. Une limite à 100 % ou plus est traitée comme critique, quel que soit le classement que lui donne NSX.

Une recherche sur chaque Local Manager liste les objets globaux modifiés localement. Chaque surcharge est comptée sur l'emplacement et constitue un changement de l'historique. Le lab n'en a aucune.

### Expiration des mots de passe, revue des règles de pare-feu et disponibilité

Une fois par heure, chaque manager est interrogé sur ses utilisateurs locaux, que le compte `audit` a le droit de lire. Le pack calcule le nombre de jours restants avant l'expiration du mot de passe de son compte de collecte. Une fois ce mot de passe expiré, NSX refuse la connexion et la collecte de ce manager s'arrête : l'alerte arrive donc avant, en avertissement sous 30 jours, en critique sous 7 jours. Dans le lab, le mot de passe `audit` expire dans 35 jours sur site-a-wld01 et dans 85 à 87 jours sur les trois autres managers.

Chaque stratégie de sécurité globale est examinée à la recherche de trois sortes de règles :

| Règle | Signification | Alerte |
| --- | --- | --- |
| Masquée | Se trouve après une règle active de la même stratégie qui couvre tout son trafic : NSX ne l'atteint donc jamais | Information |
| Inutilisée | Son nombre de correspondances, additionné sur les emplacements de sa portée, n'a pas augmenté depuis 30 jours (un réglage du compte) | Information |
| Autorise tout | ALLOW de n'importe quelle source vers n'importe quelle destination sur n'importe quel service, appliquée partout | Warning |

Ces alertes pèsent sur le badge « Risk ». La vérification ne développe pas l'appartenance aux groupes : elle ne signale donc que des règles qui ne peuvent jamais être atteintes. Les stratégies par défaut de NSX sont ignorées.

Chaque cycle envoie aussi une valeur de disponibilité, 100 ou 0, pour la fédération, chaque Global Manager, chaque emplacement et chaque Tier-0 étendu. En moyenne sur une période, elle donne la part de cette période pendant laquelle l'objet était disponible. Le rapport de disponibilité s'appuie sur elle.

### Grandes fédérations

La réalisation est la seule partie de la collecte qui coûte un appel par objet. Jusqu'à 200 objets étendus, chacun est lu à chaque cycle. Au-delà, l'adaptateur relit un objet lorsqu'il est nouveau, modifié, incomplètement réalisé, ou lorsque vient son tour dans un balayage qui couvre tous les objets en six cycles.

Sur le simulateur NSX, une fédération de 610 objets étendus coûte 610 appels de réalisation au premier cycle et environ 100 à chacun des suivants. Les chiffres de dimensionnement des fédérations plus grandes figurent dans [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/OPERATIONS.md) (en anglais).

### Diagnostic

`install/diagnostics.py` rassemble dans un fichier JSON ce dont une demande de support a besoin, sans rien modifier. Il couvre les versions, chaque compte avec ses réglages et son état, les objets par type, la santé de la collecte et les alertes ouvertes, puis liste les problèmes trouvés, chacun avec ce qu'il faut vérifier. Il ne lit aucun identifiant.

Dans le lab, il a signalé un seul problème : 21 objets du pack qui ne reçoivent plus de données. Ce sont des nœuds Edge, des tunnels RTEP et des canaux de synchronisation qui ne correspondent plus à aucun objet dans NSX. `docs/OPERATIONS.md` contient un tableau de dépannage pour les cas courants.

La bibliothèque du SDK fixe `cryptography` en version 44.0.0, qui a plusieurs vulnérabilités publiées, et l'image de l'adaptateur la remplace par la 50.0.2. La CI compare les paquets Python de l'image aux vulnérabilités publiées à chaque push et chaque semaine, et produit un SBOM. `SECURITY.md` décrit ce que le pack peut faire sur NSX et sur VCF Operations.

## Tests dans le lab

Le pack ne fait que lire dans NSX, et aucun test n'y fait donc passer de trafic. Les tests remplacent NSX par un simulateur, lisent les managers réels, font échouer certains appels à l'intérieur de l'adaptateur, ou écrivent des valeurs dans VCF Operations comme si l'adaptateur les avait envoyées.

| Test | Ce qu'il fait | Ce qu'il modifie | Lancé avec |
| --- | --- | --- | --- |
| Suite de tests | Exécute l'adaptateur face à un simulateur NSX qui répond à partir d'un enregistrement anonymisé de la fédération du lab, et face à une API VCF Operations simulée | Rien | `pytest` dans le conteneur d'outillage ; GitHub Actions à chaque push et à chaque pull request |
| Exécution locale | Exécute un cycle de collecte sur les managers réels, avec les mêmes appels GET en lecture seule que l'adaptateur dans VCF Operations | Rien | `tools/localrun.py` dans le conteneur d'outillage |
| Injection de pannes | L'exécution locale, avec certains appels NSX forcés en échec HTTP 500 à l'intérieur du processus de l'adaptateur | Rien côté NSX | `tools/test_gaps.py` dans le conteneur d'outillage |
| Tests d'alertes | Écrit des métriques, des propriétés ou des événements dans VCF Operations par son API Suite, sous la forme qu'envoie l'adaptateur, puis rétablit les valeurs | Des données de VCF Operations, pendant quelques cycles | Appels à l'API Suite |
| Historique des changements | Définit la description d'un segment étendu, puis la retire | Un segment, rétabli ensuite | API NSX du Global Manager actif |

### Suite de tests et exécution locale

La suite de tests se passe du lab. Ses 162 tests couvrent la collecte, le contenu des alertes et les scripts ; la liste figure dans la section Tests de [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/OPERATIONS.md) (en anglais).

Sur le simulateur, une grande fédération (1 000 segments de plus, 200 passerelles Tier-1 de plus et 100 stratégies de plus, de 50 règles chacune) demande 1 440 appels sur le Global Manager actif au premier cycle après un démarrage, dans la limite du quota de 2 000 appels par manager, puis 233 aux cycles suivants.

*À exécuter sur : le jumphost, à la racine d'un clone du dépôt.*

```bash
docker build -t fedmp-tooling tooling
docker run --rm -u $(id -u):$(id -g) -v $PWD:/work -w /work fedmp-tooling \
  python -m pytest -q tests -p no:cacheprovider
```

L'exécution locale demande un accès réseau aux managers et un compte NSX en lecture seule. Elle affiche les objets, les métriques et les propriétés d'un cycle. `LM_USER` et `LM_PASSWORD` définissent un compte distinct pour les Local Managers.

*À exécuter sur : le jumphost, à la racine d'un clone du dépôt.*

```bash
docker run --rm --network host -v $PWD:/work -e GM_HOSTS=<gm1>,<gm2> \
  -e NSX_USER=audit -e NSX_PASSWORD='<password>' fedmp-tooling python tools/localrun.py
```

Résultats sur le lab :

| Test | Résultat |
| --- | --- |
| Exécution normale, les deux adresses de Global Manager | 76 objets, environ 60 appels d'API (environ 100 lorsque l'inventaire en cache est rechargé), 25 à 45 secondes |
| Seconde adresse de Global Manager injoignable | La collecte se poursuit. « Global Managers not reachable » vaut 1 sur l'objet fédération |
| Aucun Global Manager joignable | Les deux Local Managers restent collectés à partir de la liste d'emplacements en cache, et l'alerte « NSX Federation collection runs without a Global Manager » se déclenche. Lorsqu'un redémarrage vide le cache, la liste est relue dans VCF Operations |

### Injection de pannes

`tools/test_gaps.py` reprend l'exécution locale et force certains appels à renvoyer un code HTTP 500 à l'intérieur du processus de l'adaptateur ; NSX ne voit aucune erreur. Par défaut, les appels d'état des emplacements, de synthèse des sauvegardes, d'alarmes et d'état du cluster échouent sur chaque manager. La variable `FAIL` remplace cette liste par d'autres fragments de chemin, séparés par des virgules.

*À exécuter sur : le jumphost, à la racine d'un clone du dépôt.*

```bash
docker run --rm --network host -v $PWD:/work -e GM_HOSTS=<gm1>,<gm2> \
  -e NSX_USER=audit -e NSX_PASSWORD='<password>' fedmp-tooling python tools/test_gaps.py
```

Sur le lab, avec les valeurs par défaut, l'exécution a signalé 15 appels sans réponse et n'a envoyé aucune des valeurs qui en dépendent.

### Panne d'un emplacement

La règle de panne a été testée sans provoquer de panne. Le compte étant arrêté, l'API Suite a écrit « un emplacement en erreur » sur le segment fed-stretched (`POST /api/resources/{id}/stats`), et l'alerte « Stretched segment is in error at a location » s'est déclenchée.

L'écriture du nom d'un emplacement dans la propriété « Locations in outage » (`POST /api/resources/{id}/properties`) a annulé l'alerte alors que le symptôme d'erreur du segment restait actif. Les valeurs ont ensuite été rétablies et le compte redémarré.

### Latence, perte de paquets et instabilité BGP

La première vérification a relevé les compteurs de paquets de chaque tunnel RTEP sur les deux nœuds Edge presque au même instant, quatre fois, à deux minutes d'intervalle. Sur les deux tunnels qui avaient rejeté un paquet, le total reçu correspondait exactement au total envoyé par le pair. NSX compte donc un paquet rejeté dans le total, et le pack calcule la part des rejets sur cette base.

La seconde a testé les alertes. Le compte étant arrêté, l'API Suite a écrit des valeurs hors seuil chaque minute pendant 14 minutes, puis le compte a été redémarré :

| Valeur écrite | Alerte déclenchée |
| --- | --- |
| 6 % et 20 rejets par minute sur un tunnel | « RTEP tunnel is dropping packets », en Immediate |
| 30 paquets bloqués par minute sur un tunnel | « RTEP tunnel is blocking packets » |
| Une coupure sur une liaison montante établie | L'alerte d'instabilité du voisin BGP du Tier-0 |
| Une coupure sur la session intersite d'un tunnel | « RTEP tunnel inter-site BGP session is flapping » |
| 160 ms vers le Global Manager | L'alerte de latence, en Warning, par la condition à 150 ms (160 ms ne représentent que 64 % du réglage de 250 ms) |

Pendant le test, le tableau de bord « Inter-site Connectivity » affichait les valeurs écrites sur le tunnel sélectionné. Les couleurs du widget « Scoreboard » suivent les seuils des alertes :

![La liste des tunnels RTEP du tableau de bord Inter-site Connectivity avec un tunnel sélectionné, et ses valeurs clés pendant le test d'alerte : Rx drops par minute à 20 en orange, Rx dropped à 6 % en rouge, Tx blocked par minute à 30 et BGP drops par heure à 12 en orange, les autres valeurs à seuil en vert](/images/nsx-federation-mp/ops-dashboard-loss.webp)

L'alerte liste chaque symptôme avec la valeur observée et son seuil :

![Détail de l'alerte « RTEP tunnel is dropping packets » pendant le test : description, recommandation et symptômes avec leurs valeurs, 6 % des paquets reçus rejetés pour des seuils de 1 % (Warning) et 5 % (Immediate), et 20 rejets par minute pour un seuil de 10](/images/nsx-federation-mp/ops-alert-tunnel-drops.webp)

En fonctionnement normal, le lab mesure 1 ms vers le Global Manager, des files de synchronisation vides et aucun rejet.

### Test de l'historique des changements

Le 22 novembre à 01:48 UTC, la description du segment étendu fed-stretched a été définie sur le Global Manager actif par un PATCH. La lecture suivante de l'inventaire, à 02:13 UTC, a enregistré « Stretched Segment fed-stretched changed by admin (revision 0 -> 1) ».

Le changement est apparu sur le segment comme une notification, effacée au cycle suivant, et il est resté dans les dix derniers changements de la fédération. Le rétablissement du segment a demandé un PUT du segment complet sans la description, en révision 2, car un PATCH avec une description vide avait laissé la description en place.

### Événements d'alarme NSX

Le lab n'avait aucune alarme de fédération ouverte. Pour tester les définitions, deux événements ont été envoyés par l'API Suite (`POST /api/events`) sous la forme qu'utilise l'adaptateur : `gm_to_lm_synchronization_warning` sur le Global Manager et `rtep_bgp_down` sur l'emplacement site-a-wld01. Chacun a déclenché son alerte nommée en un cycle, et aucune alerte « Notification event » n'est apparue en parallèle.

L'alerte issue de l'alarme affiche le symptôme, le texte de l'événement et la recommandation :

![Détail de l'alerte « NSX alarm: RTEP BGP neighbor down » sur site-a-wld01 : la description, la recommandation et le symptôme « Local Manager raised NSX alarm rtep_bgp_down » avec le texte de l'événement de test](/images/nsx-federation-mp/ops-alarm-alert.webp?v=2)

Les mêmes événements, envoyés de nouveau avec une heure d'annulation, ont effacé les deux alertes au cycle suivant.

## Ce que le lab a montré

Dans le lab, un cycle de collecte a couvert 76 objets en 25 à 45 secondes. La collecte des Local Managers a continué avec la seconde adresse de Global Manager injoignable, puis sans aucun Global Manager joignable. Avec 15 appels mis en échec, le pack n'a envoyé aucune des valeurs qui en dépendaient. Pendant la panne d'emplacement simulée, l'alerte du segment étendu s'est annulée et le symptôme de la panne est resté actif. Les rejets, blocages, instabilités et latences écrits ont déclenché leurs alertes, et les deux événements d'alarme NSX du test leurs alertes nommées en un cycle.

## Sources

- Lab : VCF 9.1.1, NSX 9.1.1 (Local Managers et Global Managers), VCF Operations 9.1.1. Construction, installation et tests du 17 au 22 novembre 2026.
- Code source du pack : [github.com/sorinplaton/vcf-ops-nsx-federation-mp](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp), tag [v1.13.0](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/tree/v1.13.0)
- Guide d'installation avec liste de contrôle : [docs/INSTALL.fr.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/INSTALL.fr.md)
- Objets, métriques et alertes de la version 1.13.0 : [docs/FEATURES.fr.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.fr.md) ; réglages du compte et dimensionnement : [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/OPERATIONS.md) (en anglais)
- [VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction)
- KB Broadcom [415279](https://knowledge.broadcom.com/external/article/415279/error-no-signature-found-on-pak-file-wh.html) : « No signature found on PAK file » lors de l'installation d'un management pack
- [Docker : verify repository client with certificates](https://docs.docker.com/engine/security/certificates/)
- KB Broadcom [324244](https://knowledge.broadcom.com/external/article/324244/split-brain-condition-after-a-global-man.html) : split-brain après le basculement d'un Global Manager
- KB Broadcom [378126](https://knowledge.broadcom.com/external/article/378126/nsx-apis-throws-error-message-client-ex.html) : limites de débit appliquées aux clients de l'API NSX
- KB Broadcom [345822](https://knowledge.broadcom.com/external/article/345822/gm-to-lm-network-latency-threshold-excee.html) : alarme de latence entre Global Manager et Local Manager
