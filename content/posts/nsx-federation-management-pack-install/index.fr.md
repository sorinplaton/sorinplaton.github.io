---
title: "Superviser NSX Federation dans VCF Operations, partie 2 : construction, installation et données collectées par le pack"
date: 2026-11-18
description: "Partie 2 du management pack NSX Federation pour VCF Operations 9.1.1 : l'adaptateur construit avec l'Integration SDK, l'installation pas à pas avec captures d'écran (registre en HTTPS, approbation du certificat sur le proxy cloud, pack non signé, compte), et ce que le pack supervise sur une fédération de lab à deux emplacements : 72 objets, 447 métriques, 63 définitions d'alerte, quatre groupes personnalisés, trois tableaux de bord, cinq vues et un rapport. L'article couvre aussi un basculement de Global Manager et l'envoi des alertes par e-mail."
summary: "L'adaptateur collecte les données d'une fédération NSX 9.1 à deux emplacements en 10 à 25 secondes. Pour qu'il fonctionne dans VCF Operations 9.1.1, il faut un registre d'images servi en HTTPS, un proxy cloud qui approuve le certificat du registre et le réglage qui autorise un pack non signé. Cet article détaille chaque étape de l'installation avec des captures d'écran, liste ce que supervise le pack (objets, métriques, alertes, groupes, tableaux de bord, vues), teste un basculement de Global Manager et montre comment envoyer les alertes par e-mail."
tags: ["VCF 9.1.1", "NSX Federation", "VCF Operations", "Management pack", "Integration SDK", "Holodeck"]
categories: ["Notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-federation-mp/object-model.svg"]
params:
  seoDescription: "Construire et installer un management pack NSX Federation sur mesure dans VCF Operations 9.1.1 : registre HTTPS, approbation du certificat, pack non signé, puis objets, métriques, alertes, groupes, tableaux de bord et vues qu'il fournit."
---

La [partie 1](/fr/posts/nsx-federation-management-pack-design/) s'achevait sur une conception, sans une ligne de code. Cet article décrit la construction du pack, son installation dans VCF Operations 9.1.1 avec des captures d'écran, ce que le pack supervise (objets, métriques, alertes, groupes, tableaux de bord, vues), un basculement de Global Manager et l'envoi des alertes par e-mail. Le pack décrit ici est la version 1.7.6, et son code source est sur GitHub : [sorinplaton/vcf-ops-nsx-federation-mp](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp). [docs/FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.7.6/docs/FEATURES.md) (en anglais) liste chaque type d'objet, métrique, propriété et alerte du pack, avec la condition et la recommandation de chaque alerte. L'écriture de l'adaptateur a demandé moins de temps que son installation : trois contrôles distincts de VCF Operations séparaient le fichier `.pak` d'une collecte opérationnelle.

## Le lab

Le lab est un Holodeck VCF 9.1.1 imbriqué. Il compte deux Global Managers, chacun à un seul nœud : `nsx-gm-01a` (`gm-site-a`) est actif et `nsx-gm-01b` (`gm-site-b`) est son Global Manager de secours. Ils fédèrent deux emplacements (les « Locations » de NSX Federation), `site-a-wld01` et `site-a-wld03`, dotés chacun d'un cluster Edge à deux nœuds. VCF Operations est en version 9.1.1.0, build 25795063, avec un seul proxy cloud, `opscollector-01a`. La construction se fait sur une machine de rebond Linux équipée de Docker, 10.0.6.27. Cette même machine héberge le registre d'images, sur le port 5000.

Le Global Manager de secours a été déployé à partir de la même appliance 9.1.1.0100 que le Global Manager actif, en taille Small, sur le cluster de `site-a-wld03`, avec une adresse et un enregistrement DNS dans le réseau de gestion. Il a été enregistré sur le Global Manager actif par l'API, sous le nom `gm-site-b` en mode STANDBY, avec son adresse, les identifiants du compte admin et l'empreinte de son certificat (`PUT /global-manager/api/v1/global-infra/global-managers/gm-site-b`). La première synchronisation complète s'est terminée en quelques minutes.

## Construire l'adaptateur

Le pack s'appuie sur le VCF Operations Integration SDK 1.3.1. Les outils du SDK (`mp-init`, `mp-test`, `mp-build`) exigent une version de Python absente de la machine de rebond ; ils s'exécutent donc depuis une petite image d'outillage :

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y docker.io git && rm -rf /var/lib/apt/lists/*
RUN pip install vmware-aria-operations-integration-sdk
```

Le code de l'adaptateur tient en deux fichiers Python :

- `nsxclient.py` : un client REST NSX en lecture seule, avec pagination par curseur, quota d'appels par manager, nouvelle tentative après attente sur les codes HTTP 429 et 503, contrôle du certificat par empreinte SHA-256 et petit cache sur disque pour les données qui changent peu.
- `adapter.py` : le modèle d'objets, la logique de collecte et les événements.

L'instance d'adaptateur reçoit une ou deux adresses de Global Manager et un seul jeu d'identifiants. Elle découvre les Local Managers à partir du Global Manager et compare le certificat de chacun d'eux à l'empreinte que le Global Manager a déjà enregistrée. Un Global Manager est approuvé d'après le certificat accepté sur le compte dans VCF Operations. Une fois un certificat vérifié, chaque connexion du cycle est liée à son empreinte : les identifiants ne sont donc pas envoyés à un serveur qui présente un autre certificat.

Un compte supervise une seule fédération. Un Global Manager de la liste qui répond pour une autre fédération est signalé et écarté, et un réglage facultatif désigne l'objet fédération lorsque VCF Operations en supervise plusieurs.

Un pack SDK n'est pas autonome. Le fichier `.pak` contient les définitions d'objets et une référence à une image de conteneur. Le proxy cloud récupère cette image dans un registre au démarrage de l'instance d'adaptateur.

`mp-build` construit le pack. Lorsque l'adresse de registre fournie a la forme `host:port`, il inscrit pourtant Docker Hub dans le pack. Un court script, `tools/repack.py`, réécrit deux valeurs, `REGISTRY` et `REPOSITORY`, dans le fichier `.conf` que contient `adapter.zip` :

```
REGISTRY=10.0.6.27:5000
REPOSITORY=/nsxfederation
DIGEST=sha256:4085...86e9
```

## Installer le pack

L'installation compte cinq étapes, présentées ici dans l'ordre qui fonctionne. Les échecs rencontrés dans le lab sont conservés comme symptômes, sous l'étape qui les évite.

### Étape 1 : servir le registre en HTTPS

Le lab utilise un conteneur `registry:2` sur la machine de rebond. Le proxy cloud ne s'adresse à un registre qu'en HTTPS, et le conteneur a donc besoin d'un certificat :

```bash
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout certs/registry.key -out certs/registry.crt -subj "/CN=fedmp-registry" \
  -addext "subjectAltName=IP:10.0.6.27"

docker run -d --name fedmp-registry -p 10.0.6.27:5000:5000 \
  -v registry-data:/var/lib/registry -v $PWD/certs:/certs:ro \
  -e REGISTRY_HTTP_TLS_CERTIFICATE=/certs/registry.crt \
  -e REGISTRY_HTTP_TLS_KEY=/certs/registry.key registry:2
```

Le premier registre du lab servait du HTTP en clair. Avec lui, l'instance d'adaptateur, créée sur le proxy cloud, est passée de `STARTING` à `FAILED` avec un message d'état vide. Le journal du registre ne contenait aucune requête. Une capture de paquets sur le port du registre en a donné la raison : le proxy cloud ouvrait une connexion et envoyait un ClientHello TLS à un registre qui ne servait que du HTTP en clair. Comme le proxy cloud ne se rabat pas sur HTTP, le registre a été recréé avec le certificat ci-dessus.

### Étape 2 : faire approuver le certificat du registre par le proxy cloud

Avec un certificat autosigné, le HTTPS ne suffit pas. Tant que le proxy cloud n'approuve pas le certificat, le journal du registre affiche l'erreur suivante (10.0.6.33 est le routeur du lab, qui masque le proxy cloud par NAT) :

```
http: TLS handshake error from 10.0.6.33:56544: remote error: tls: bad certificate
```

Le proxy cloud exécute Docker, et Docker approuve un registre privé dès lors que son certificat se trouve dans un dossier portant le nom du registre :

```
/etc/docker/certs.d/10.0.6.27:5000/ca.crt
```

SSH est désactivé sur le proxy cloud et l'est resté. Le fichier a été écrit au moyen des opérations invité de vCenter (`govc guest.run` avec le compte root de l'appliance).

Une mise à niveau ou un redéploiement du proxy cloud peut faire disparaître ce fichier. Un registre dont le certificat est émis par une autorité que le proxy cloud approuve déjà dispense de cette étape.

### Étape 3 : installer le pack non signé

Le pack n'est pas signé par Broadcom. L'envoi par l'API de l'appliance échoue :

```
pak_manager.pakfile.no_signature
The PAK file ... is not signed. It is missing required signature files signature.mf and signature.cert
```

Par défaut, VCF Operations 9 bloque les packs non signés, et la case « Ignore the PAK file signature checking » reste masquée dans la boîte de dialogue d'envoi tant que ce blocage n'est pas levé. Le réglage qui le lève est une option de la console d'administration (`https://<vcf-operations>/admin`), sous « Administrator Settings > Security Settings » : « Allow unsigned PAK installation ». Broadcom la décrit dans la KB 415279. Avant l'activation de l'option, la page se présente ainsi :

![La console d'administration de VCF Operations, Administrator Settings > Security Settings : l'option « Allow unsigned PAK installation » est désactivée, avec le bouton qui permet de l'activer](/images/nsx-federation-mp/ops-admin-unsigned-pak.webp)

Le pack peut s'installer de deux façons. Toutes deux commencent par l'activation de l'option et se terminent par sa désactivation.

#### Méthode 1 : à la main, dans l'interface

1. Dans la console d'administration, cliquer sur « Activate unsigned PAK installation ».
2. Dans l'interface principale de VCF Operations, ouvrir « Administration > Integrations > Repository », puis choisir d'ajouter un pack.
3. Sélectionner le fichier `.pak` et cocher les deux cases : celle qui force l'installation même si le pack est déjà installé, et « Ignore the PAK file signature checking ».
4. Lancer l'envoi, accepter l'avertissement relatif au fichier non signé et attendre la fin de l'installation.
5. Revenir dans la console d'administration et désactiver l'installation de fichiers PAK non signés.

#### Méthode 2 : par script

L'API d'envoi de l'appliance (`/casa/upgrade/cluster/pak/reserved/operation/upload`) n'offre aucun paramètre pour ignorer le contrôle de signature, même lorsque l'option est activée. Un script doit donc passer par les mêmes appels que l'interface, et celle-ci les protège par un jeton anti-CSRF (`secureToken`) qu'un simple client HTTP ne reçoit jamais. Dans VCF Operations 9.1.1, ce jeton figure dans la page chargée sous `/vcf-operations/plug/ops/`, à l'intérieur de l'interface principale.

Dans le lab, un Chrome sans interface graphique (headless), piloté par le Chrome DevTools Protocol, s'en est chargé depuis la machine de rebond :

1. Chrome ouvre une session sur la console d'administration et appelle `clusterManagement.action` avec `mainAction=enableUPI`. La même page propose aussi `getUPIStatus` et `disableUPI`.
2. Chrome ouvre une session sur l'interface principale. Un court script lit dans la page les cookies de session et la valeur de `secureToken`.
3. Un script Python réutilise cette session pour trois appels :

```
POST admin/services/solution/upload?uploadId=<ms>&ignoreSignatureChecking=true&secureToken=<token>
     multipart field "solution" = the .pak file, forceUpload=true, forceContent=true
POST solution.action   mainAction=install
POST solution.action   mainAction=getInstallStatus      (until "completed")
```

4. Chrome appelle `disableUPI`.

L'ouverture de session sur l'interface principale (point 2) doit suivre celle sur la console d'administration (point 1), car cette dernière ferme la session de l'interface principale. Un jeton de session lu avant cette ouverture n'est plus valide.

L'option ne peut pas être désactivée pendant l'installation. Lors d'une mise à niveau, la console d'administration a répondu `Cluster is in PAK installing state` jusqu'à la fin de l'installation. Un script doit donc répéter le dernier appel.

Ces appels sont internes à l'interface : ils ne sont pas pris en charge et peuvent changer à chaque version. Quant à l'option des packs non signés, elle vaut pour toute l'instance ; dans le lab, elle n'est restée activée que le temps de l'envoi. Le dépôt contient ces étapes sous la forme des scripts `install/upi.sh` et `install/install_unsigned.sh`.

Après l'installation, le type d'adaptateur `NSXFederation` et la solution `iSDK_NSXFederation` apparaissent dans VCF Operations. Le pack figure, avec sa version, sous « Administration > Integrations > Repository » :

![Administration > Integrations > Repository : le pack NSX Federation, en version 1.7.6, avec un compte](/images/nsx-federation-mp/ops-repository-pack.webp)

### Étape 4 : créer le compte

L'instance d'adaptateur, appelée compte dans l'interface, se crée sur le proxy cloud. Elle reçoit une ou deux adresses de Global Manager et un seul jeu d'identifiants. Avec les deux adresses, le pack suit le rôle actif lorsque les Global Managers basculent. Les autres réglages sont facultatifs : les stratégies de sécurité dont les règles deviennent des objets, la façon dont le pack fonctionne avec l'intégration NSX native (liens et alertes d'alarme, automatiques par défaut), et la suppression des objets qui ont quitté NSX (désactivée par défaut). [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.7.6/docs/OPERATIONS.md) (en anglais) décrit chaque réglage.

Lors de l'enregistrement du compte, il faut valider la connexion et accepter les certificats des Global Managers. Ce contrôle a deux modes : « enforce » arrête la collecte d'un manager dont le certificat ne correspond pas ou pour lequel aucun certificat approuvé n'est enregistré ; « report » poursuit la collecte et déclenche une alerte. Le mode par défaut est « enforce » : sans certificat accepté, aucun manager n'est interrogé. Le compte du lab a été créé par l'API, n'a aucun certificat accepté et fonctionne en mode « report ». Le compte accepte aussi un jeu d'identifiants distinct pour les Local Managers. Le pack ne fait que lire dans NSX : un compte en lecture seule suffit donc. Le compte du lab utilise le compte intégré `audit` de NSX sur les quatre managers et collecte les mêmes objets qu'avec `admin`, sans aucun appel en échec.

Le registre étant approuvé, le proxy cloud a récupéré l'image au démarrage suivant de l'instance, qui est passée à l'état `STARTED`, puis `DATA_RECEIVING`. L'onglet « Accounts » affiche l'instance avec l'état « Collecting » :

![Administration > Integrations > Accounts : le compte « NSX Federation - nsx-gm-01a » à l'état Collecting, sur le proxy cloud opscollector-01a](/images/nsx-federation-mp/ops-account-collecting.webp)

### Étape 5 : attendre trois cycles de collecte

Les données n'arrivent pas toutes en même temps. Avec un intervalle de 5 minutes :

| Cycle | Ce que VCF Operations enregistre |
| --- | --- |
| 1 | Les objets et leurs relations |
| 2 | Les propriétés et les événements ; les clés de métrique sont déclarées |
| 3 | Les métriques |

Un contrôle effectué 2 minutes après le premier cycle montre des objets sans métriques, ce qui fait penser à un pack défectueux. Les métriques apparaissent environ 10 minutes après le démarrage. Un taux se calcule sur deux cycles et apparaît donc un cycle après les autres métriques.

### Mettre le pack à niveau

Une nouvelle version s'installe de la même façon, avec l'option des packs non signés. Après une mise à niveau, l'instance d'adaptateur doit être arrêtée puis redémarrée. Sans cela, elle peut rester à l'état `FAILED`, alors que le proxy cloud a déjà récupéré la nouvelle image. Il faut vérifier que le compte atteint l'état « Collecting », et le démarrer une seconde fois s'il reste arrêté. Les métriques reviennent après trois cycles, et les taux après quatre. Une définition de symptôme que la nouvelle version ne fournit plus reste inutilisée dans VCF Operations ; elle se supprime sous « Symptom Definitions ».

Une modification de la façon dont l'adaptateur identifie un objet crée un nouvel objet. L'ancien reste dans l'inventaire, sans données, jusqu'à sa suppression par l'API Suite (`DELETE /api/resources/{id}`), ce qui efface aussi son historique.

## Ce que le pack supervise

Cette section donne une vue d'ensemble. La liste complète des objets, des métriques et des alertes se trouve dans [FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.7.6/docs/FEATURES.md) (en anglais).

### Objets

Le pack définit 15 types d'objets. Dans le lab, il crée 72 objets de 14 types. Le quinzième type, la règle de pare-feu globale, s'active stratégie par stratégie et est resté désactivé pour cette installation.

| Type d'objet | Nombre dans le lab | Exemples de données collectées |
| --- | --- | --- |
| Fédération | 1 | Global Managers actifs, emplacements en bonne santé et interrogés, agrégats RTEP, erreurs d'API, managers ayant dépassé leur quota d'appels |
| Global Manager | 2 | Global Manager de secours synchronisé ou non et depuis combien de temps il ne l'est plus, entrées restant à lui envoyer, dernière synchronisation complète, version comparée à celle du Global Manager actif, état de la mise à niveau, état de la sauvegarde, alarmes ouvertes, jours restants avant la première expiration de certificat |
| Nœud de manager | 4 | État en ligne de chaque nœud de Global Manager et de Local Manager |
| Emplacement | 2 | Connexion au Global Manager, flux de réplication, dernier contact avec le Global Manager, état de l'onboarding, joignable ou non depuis le proxy cloud et temps écoulé depuis la dernière collecte, nœuds en ligne, échecs et durée de la synchronisation complète, sessions BGP intersites, alarmes, version, état de la mise à niveau |
| Canal de synchronisation | 9 | État de la connexion entre deux managers |
| Cluster Edge | 2 | État intersite, membres, adresses RTEP |
| Nœud Edge | 4 | Tunnels RTEP, sessions BGP intersites |
| Tunnel RTEP | 8 | État BGP, paquets, paquets rejetés et bloqués dans chaque sens |
| Voisin BGP du Tier-0 | 16 | État, préfixes reçus et annoncés, messages, durée d'établissement ; 4 sessions de liaison montante et 12 sessions internes entre nœuds Edge |
| Passerelle Tier-0 | 1 | Portée, emplacements réalisés, emplacements en erreur |
| Passerelle Tier-1 | 3 | Mêmes données que la passerelle Tier-0 |
| Segment | 5 | Mêmes données, plus les sous-réseaux et la passerelle connectée |
| Stratégie de sécurité globale | 1 | Portée, réalisation, nombre total de règles et de règles désactivées |
| Objet étendu sur un emplacement (réalisation) | 14 | Réalisé ou non, libellé de l'état |

Le pack rend compte de son propre fonctionnement sur l'objet fédération : appels effectués, appels bridés, erreurs, et épuisement éventuel du quota d'appels d'un manager.

Un nœud Edge est identifié par l'emplacement et le nom du nœud, et chacun de ses tunnels par ce nœud et ses deux adresses RTEP : un nœud Edge redéployé conserve ainsi ses objets et leur historique.

Dans l'inventaire, les types d'objets forment une arborescence et chaque objet dispose d'une page de résumé. La capture ci-dessous montre un emplacement :

![Inventaire de VCF Operations : les types d'objets NSX Federation dans l'arborescence, et le résumé de l'emplacement site-a-wld01 avec ses objets associés, ses indicateurs clés et l'alerte sur une mise à niveau NSX en pause](/images/nsx-federation-mp/ops-inventory-location.webp)

Le pack fournit aussi une arborescence d'inventaire. Pour l'ouvrir, aller dans « Inventory », cliquer sur « Detailed View », choisir le dernier onglet (l'icône en forme de pièce de puzzle), puis sélectionner « NSX Federation » dans la liste déroulante. L'arborescence part de la fédération. En dessous se trouvent les segments, les passerelles Tier-1 et Tier-0, la stratégie de sécurité globale, les Global Managers et les deux emplacements. Un Global Manager contient son nœud de manager et ses canaux de synchronisation. Un emplacement contient son nœud de manager, ses canaux de synchronisation et son cluster Edge, avec les nœuds Edge et leurs tunnels. Un Tier-0 contient ses voisins BGP, et chaque objet étendu contient sa réalisation par emplacement :

![L'arborescence d'inventaire NSX Federation dans VCF Operations : la fédération, ses segments, passerelles et stratégie, et l'emplacement site-a-wld01 déplié sur son nœud de manager, ses canaux de synchronisation et son cluster Edge](/images/nsx-federation-mp/ops-inventory-tree.webp)

### Groupes personnalisés

Le pack fournit quatre groupes personnalisés (custom groups) : tous les objets du pack (72 dans le lab), managers et emplacements (9), connectivité intersite (39) et objets étendus (24). Un groupe réunit les alertes et les badges de ses membres, et une politique ou une règle de notification peut le prendre pour périmètre. Les groupes se trouvent sous « Inventory > Custom Groups > Environment ». L'appartenance dépend du type d'objet : un nouvel emplacement ou un nouveau tunnel rejoint donc son groupe sans modification.

![Groupe personnalisé « NSX Federation - Managers and locations » : les deux Global Managers, la fédération, les deux emplacements et les quatre nœuds de manager, sans alerte ouverte](/images/nsx-federation-mp/ops-custom-groups.webp)

### Métriques

Le pack envoie 447 métriques par cycle dans le lab, une fois les taux disponibles.

Les métriques sont rangées en groupes, comme dans les intégrations natives : Sync, Connectivity, Certificates, Alarms, Rates, etc., soit 17 groupes au total. L'arborescence des métriques d'un objet reste ainsi assez courte pour se lire d'un coup d'œil. La clé d'une métrique contient son groupe, par exemple `sync|standby_in_sync`.

![Onglet « Metrics » du Global Manager gm-site-a : les groupes Alarms, Certificates, Cluster, Connectivity et Sync, et la courbe de « Standby sync healthy (1 = in sync) » à 1](/images/nsx-federation-mp/ops-metric-groups.webp)

Dix-huit métriques sont marquées comme indicateurs clés, trois au plus par type d'objet : les Global Managers actifs et la part d'emplacements en bonne santé sur la fédération, les nœuds hors ligne et la synchronisation du secours sur un Global Manager, la connexion et la synchronisation complète d'un emplacement, BGP et les paquets rejetés sur un tunnel, etc. VCF Operations les affiche en premier dans le résumé de l'objet :

![Résumé de gm-site-a : l'alerte de sauvegarde ouverte et, à droite, les indicateurs clés « Nodes offline 0 » et « Standby sync healthy (1 = in sync) 1 »](/images/nsx-federation-mp/ops-key-attributes.webp)

NSX renvoie les paquets, les octets, les rejets et les correspondances de règles sous forme de totaux cumulés. L'adaptateur conserve les compteurs du cycle précédent et ajoute des taux : paquets, rejets et paquets bloqués par minute et octets par seconde sur les tunnels RTEP, correspondances de règles par minute sur les stratégies, les réalisations et les règles. Les unités sont déclarées : secondes, minutes, heures, jours, millisecondes, octets, octets par seconde, paquets, appels, pourcentages et taux par minute.

Trois métriques sont dérivées par l'adaptateur (Global Managers injoignables, sessions BGP intersites non établies, emplacements non réalisés), car un symptôme compare une métrique à une valeur fixe et ne sait pas soustraire deux métriques.

Sept valeurs, portées par l'objet fédération, couvrent toute la fédération : emplacements en bonne santé, en nombre et en pourcentage ; tunnels RTEP ; tunnels dont la session BGP est tombée ; paquets RTEP rejetés par minute sur l'ensemble des tunnels ; jours restants avant la première expiration de certificat, tous managers confondus ; alarmes critiques ouvertes sur l'ensemble des managers. Une super métrique pourrait en calculer une partie, mais elle reste inactive tant qu'une politique ne l'active pas : l'adaptateur calcule donc les sept. Dans le lab, elles indiquent deux emplacements en bonne santé sur deux (100 %), 8 tunnels, aucun tombé, aucun paquet rejeté, 330,9 jours avant la première expiration de certificat (celui d'un Local Manager) et aucune alarme critique.

Chaque emplacement expose aussi ce que la page « Location Manager » de NSX affiche pour lui : l'état de ses trois flux de réplication avec le Global Manager, le moment où le Global Manager l'a vu pour la dernière fois, le temps d'aller-retour lorsque NSX en indique un, et l'état de son onboarding. La référence de l'API NSX ne mentionne pas l'appel utilisé par cette page. Il a été relevé dans les requêtes de l'interface NSX : `GET /api/v1/federation/replication/gm/sites/<site-id>/status` sur le Global Manager actif. Dans le lab, les trois flux indiquent SYNCHRONIZING et le temps d'aller-retour est vide.

Le tableau ci-dessous donne la couverture de chaque type d'objet. La colonne Métriques compte les clés de métrique définies pour le type. Tous les objets n'envoient pas toutes les clés (seules les réalisations de stratégie portent des compteurs, par exemple) : la colonne multipliée par le nombre d'objets ne donne donc pas 447.

| Type d'objet | Métriques | Définitions d'alerte | Tableau de bord |
| --- | --- | --- | --- |
| Fédération | 28 | 9 | Federation Overview |
| Global Manager | 14 | 20 | Federation Overview |
| Nœud de manager | 1 | Aucune, le nœud est couvert par l'alerte de cluster de son Global Manager ou de son emplacement | Objets associés uniquement |
| Emplacement | 21 | 21 | Federation Overview |
| Canal de synchronisation | 1 | 1 | Inter-site Connectivity |
| Cluster Edge | 4 | 1 | Inter-site Connectivity |
| Nœud Edge | 4 | 1 | Inter-site Connectivity |
| Tunnel RTEP | 17 | 3 | Inter-site Connectivity |
| Voisin BGP du Tier-0 | 7 | 2 | Inter-site Connectivity |
| Tier-0, Tier-1 et segment étendus | 4 chacun | 1 chacun | Stretched Objects |
| Stratégie de sécurité globale | 8 | 1 | Stretched Objects |
| Objet étendu sur un emplacement | 6 | 1 | Stretched Objects |
| Règle de pare-feu globale (optionnelle) | 5 | Aucune | Aucun |

### Alertes

Le pack compte 63 définitions d'alerte, 98 symptômes et 34 recommandations. Onze recommandations se terminent par un lien vers l'article de la KB Broadcom ou la page de documentation qui traite de la panne. Ce contenu tient dans un seul fichier XML, placé dans `content/alertdefs`. Un script le génère à partir d'une table des symptômes, des alertes et des recommandations. Un symptôme est une condition portant sur une métrique, sur une propriété ou sur un événement. Deux symptômes utilisent un seuil dynamique. Une condition sur une propriété se présente ainsi :

```xml
<SymptomDefinition adapterKind="NSXFederation" id="SymptomDefinition-NSXFederation-gm_backup_off"
                   name="Backup is not configured on the Global Manager" resourceKind="global_manager">
    <State severity="immediate">
        <Condition key="backup_enabled" operator="=" thresholdType="static" type="property"
                   value="False" valueType="string"/>
    </State>
</SymptomDefinition>
```

Une condition sur un événement compare le texte de l'événement à une expression régulière :

```xml
<Condition eventMsg="NSX alarm rtep_bgp_down:(?s).*" eventSubType="5" eventType="11"
           operator="regex" type="msg_event"/>
```

Les 63 définitions se répartissent ainsi par type d'objet :

| Type d'objet | Définitions | Alertes |
| --- | --- | --- |
| Fédération | 9 | Aucun Global Manager actif ; split-brain (deux Global Managers actifs) ; Global Manager injoignable depuis le proxy cloud ; aucun Global Manager de secours configuré ; collecte partielle (quota d'appels d'API épuisé) ; collecte avec des données manquantes ; collecte lente ou bridée ; collecte sans Global Manager ; Global Manager de la liste appartenant à une autre fédération |
| Global Manager | 20 | Cluster dégradé ; sauvegarde absente, trop ancienne ou en échec ; certificat proche de l'expiration ; Global Manager de secours non synchronisé ; aucun rôle dans la fédération (mode NONE) ; version différente de celle du Global Manager actif ; mise à niveau NSX en cours, en pause ou en échec ; 13 alarmes de fédération NSX (voir ci-dessous) |
| Emplacement | 21 | Connexion au Global Manager actif perdue ; Local Manager injoignable depuis le proxy cloud ; certificat non approuvé ; synchronisation complète non terminée ; sessions BGP intersites tombées ; release NSX différente de celle du Global Manager ; sauvegarde absente, trop ancienne ou en échec ; certificat proche de l'expiration ; cluster du Local Manager dégradé ; flux de réplication en erreur ; release NSX plus récente que celle du Global Manager ; mise à niveau NSX en cours, en pause ou en échec ; 9 alarmes de fédération NSX |
| Canal de synchronisation | 1 | Canal interrompu |
| Cluster Edge | 1 | État intersite différent de UP |
| Nœud Edge | 1 | Toutes les sessions BGP intersites tombées |
| Tunnel RTEP | 3 | Session BGP intersite tombée sur le tunnel ; plus de 60 paquets rejetés par minute dans un sens ; trafic au-dessus de sa plage habituelle (seuil dynamique) |
| Voisin BGP du Tier-0 | 2 | Session tombée ; session de liaison montante établie sans aucun préfixe reçu |
| Tier-0, Tier-1, segment, stratégie de sécurité globale | 1 chacun | Emplacements en erreur |
| Objet étendu sur un emplacement | 1 | Non réalisé |

L'alerte du nœud Edge ne se déclenche que lorsque le nœud a perdu toutes ses sessions intersites, car la perte d'une seule session déclenche l'alerte du tunnel. « Stretched object is not realized at a location » est de niveau Warning, car les alertes d'erreur sur l'objet lui-même sont de niveau Immediate.

Chaque alerte définit aussi le badge sur lequel elle pèse et la vitesse à laquelle elle réagit :

- **Badge.** Quatorze alertes font baisser le badge « Risk » au lieu du badge « Health » : les deux alertes de sauvegarde, les deux alertes d'expiration de certificat, le certificat non approuvé, l'absence de Global Manager de secours, l'écart de release, le quota d'appels, les données manquantes, la collecte lente ou bridée, les Global Managers de versions différentes, l'emplacement en avance sur le Global Manager et les deux alertes de mise à niveau. Ces conditions poseront problème plus tard ; « Health » reste réservé à ce qui est déjà en panne.
- **Cycles d'attente et d'annulation.** Dix symptômes susceptibles d'osciller exigent deux cycles de collecte, soit 10 minutes, pour déclencher une alerte, et deux pour l'effacer : flux de réplication en erreur ; canal de synchronisation interrompu ; sessions BGP intersites tombées, ou aucune établie, sur l'emplacement ; aucune session intersite sur le nœud Edge ; BGP intersite tombé sur le tunnel ; paquets rejetés sur le tunnel, en réception et en émission ; voisin BGP du Tier-0 tombé ; aucun préfixe reçu. Les données manquantes et la collecte lente attendent aussi deux cycles, tout comme une synchronisation complète non terminée et un objet étendu non réalisé : une synchronisation ou une modification en cours ne déclenche donc rien. Le seuil dynamique, la synchronisation du Global Manager de secours et le bridage en attendent trois. Les symptômes de panne s'effacent après trois cycles (voir « Comportement de la collecte »).
- **Sous-type Compliance.** Six alertes portent le sous-type Compliance : l'absence de Global Manager de secours, les deux alertes de sauvegarde, les Global Managers de versions différentes et les deux alertes de release d'un emplacement. Dans VCF Operations 9.1, la page « Compliance » affiche un bandeau qui indique que l'ancienne fonction Compliance est obsolète dans VCF 9.1 et qui renvoie vers Security Posture Management : le sous-type ne sert donc qu'à classer les alertes.
- **Seuils.** L'ancienneté maximale de la sauvegarde (24 heures) et le délai d'avertissement avant expiration d'un certificat (30 jours) sont fixés dans les symptômes. Une politique VCF Operations peut remplacer le seuil d'un symptôme par une autre valeur.
- **Seuil dynamique.** L'alerte de trafic du tunnel compare les débits en réception et en émission à la plage que VCF Operations apprend pour chaque tunnel. Elle a besoin de plusieurs jours d'historique avant de pouvoir se déclencher.

Dans le lab, 3 alertes se déclenchent, issues de deux définitions : l'absence de sauvegarde sur les deux Global Managers, et une mise à niveau NSX en pause sur le Local Manager de site-a-wld01, dont le coordinateur de mise à niveau conserve une version cible qu'il n'a pas appliquée. Toutes trois décrivent correctement l'état du lab et pèsent sur « Risk ». Les autres définitions restent muettes, car le lab ne présente aucune des pannes qu'elles surveillent.

Une alerte issue d'une définition affiche sa recommandation et le symptôme qui l'a déclenchée :

![Détail de l'alerte « Global Manager backup is missing, old or failed » sur gm-site-a : la description, la recommandation et le symptôme « Backup is not configured on the Global Manager »](/images/nsx-federation-mp/ops-alert-recommendation.webp)

#### Les alarmes NSX comme alertes nommées

L'adaptateur relaie les alarmes de fédération du Global Manager, et celles de chaque Local Manager, sous forme d'événements dont le texte est `NSX alarm <event_type>: <summary>`. L'intégration NSX native utilise la même forme et s'en sert pour donner à 381 types d'alarme une alerte propre.

Le pack donne aux 13 types d'alarme de fédération de NSX 9.1 un symptôme, une alerte et une recommandation propres : split-brain entre Global Managers ; erreurs et avertissements de synchronisation entre les Global Managers, entre le Global Manager et un emplacement, et entre emplacements ; les deux avertissements de latence ; la file de synchronisation au-dessus de son seuil ; un Local Manager restauré pendant l'import de sa configuration ; RTEP BGP tombé ; connectivité RTEP perdue. Le Global Manager reçoit les 13. Un emplacement en reçoit 9, car les quatre alarmes entre Global Managers ne le concernent pas. NSX déclenche une alarme de latence lorsque la latence entre deux managers reste au-dessus de son seuil pendant deux minutes.

L'intégration NSX native dispose des mêmes alarmes, mais uniquement pour les managers qui y sont ajoutés comme comptes NSX. Les alarmes du Global Manager n'arrivent dans VCF Operations que par ce pack. Pour un Local Manager, l'intégration native et le pack déclencheraient chacun une alerte pour la même alarme. L'adaptateur demande donc à VCF Operations quels clusters NSX l'intégration native supervise, et lui laisse les alarmes de fédération de ces Local Managers. Dans le lab, site-a-wld01 a dans l'intégration native un compte qui collecte, et le compte natif de site-a-wld03 existe mais ne collecte pas : une propriété de chaque emplacement vaut « the built-in NSX integration » pour le premier et « this pack » pour le second. Un réglage force l'un ou l'autre comportement.

Certaines conditions sans définition arrivent tout de même sous forme d'événements : une alarme de fédération dont le type n'a pas de définition, sur un Local Manager ou sur le Global Manager, et toutes les alarmes critiques du Global Manager, dans la limite de 20 par manager. Les autres alarmes NSX d'un emplacement sont comptées dans ses métriques et ne sont pas transmises. Un tel événement apparaît sous la forme d'une alerte « Notification event » sur l'objet concerné, sans recommandation. Une condition dotée d'une définition d'alerte portant sur une métrique ou une propriété n'émet pas d'événement. Sinon, elle apparaîtrait deux fois : une fois comme alerte, une fois comme « Notification event ».

### Tableaux de bord

Trois tableaux de bord sont regroupés dans un dossier nommé « NSX Federation » :

| Tableau de bord | Contenu |
| --- | --- |
| Federation Overview | Alertes ouvertes sur les objets du pack ; liste regroupant la fédération, les Global Managers et les emplacements ; valeurs clés, propriétés et objets associés de l'élément sélectionné |
| Inter-site Connectivity | Canaux de synchronisation et nœuds Edge, tunnels RTEP, voisins BGP du Tier-0 ; valeurs clés de la ligne sélectionnée |
| Stretched Objects | Passerelles, segments et stratégies étendus, avec leur portée ; réalisation de chaque objet sur chaque emplacement |

Dans chaque tableau de bord, une liste alimente un widget « Scoreboard ». Dans Federation Overview et Inter-site Connectivity, un clic sur l'une des valeurs de ce widget en affiche la courbe de tendance.

Federation Overview avec gm-site-a sélectionné. Le widget « Scoreboard » d'un Global Manager commence par la synchronisation du Global Manager de secours, et les alertes ouvertes sont celles de la sauvegarde et de la mise à niveau en pause :

![Le tableau de bord Federation Overview : deux définitions d'alerte ouvertes (sauvegarde, mise à niveau en pause), la liste avec gm-site-a et gm-site-b, et le widget « Scoreboard » de gm-site-a avec Standby in sync à 1, les nœuds hors ligne à 0 et l'expiration des certificats](/images/nsx-federation-mp/ops-dashboard-standby.webp)

Avec la fédération sélectionnée, le widget « Scoreboard » se termine par les agrégats de fédération : emplacements en bonne santé, RTEP BGP tombé, paquets RTEP rejetés par minute, expiration des certificats et alarmes critiques :

![Le tableau de bord Federation Overview avec la fédération sélectionnée : le widget « Scoreboard » affiche les erreurs d'API, le quota d'appels, les emplacements en bonne santé à 100, RTEP BGP tombé à 0, les paquets RTEP rejetés par minute à 0, l'expiration des certificats à 330,9 jours et les alarmes critiques à 0](/images/nsx-federation-mp/ops-dashboard-rollups.webp)

Inter-site Connectivity montre les taux d'un tunnel RTEP :

![Le tableau de bord Inter-site Connectivity : canaux de synchronisation et nœuds Edge, tunnels RTEP, voisins BGP du Tier-0 et les taux du tunnel RTEP sélectionné](/images/nsx-federation-mp/ops-dashboard-connectivity.webp)

Stretched Objects, avec le segment fed-stretched sélectionné :

![Le tableau de bord Stretched Objects : les passerelles, segments et stratégies étendus, la réalisation par emplacement, ainsi que les valeurs clés et les propriétés du segment fed-stretched](/images/nsx-federation-mp/ops-dashboard-stretched.webp)

### Vues et rapport

Les cinq vues de type liste portent sur les emplacements, les canaux de synchronisation, les tunnels RTEP, les voisins BGP du Tier-0 et la réalisation des objets étendus par emplacement. Un rapport, « NSX Federation - Health report », les réunit dans un même PDF. Une vue est un fichier XML. Un script le génère, comme pour les alertes et les tableaux de bord. La vue Locations :

![La vue de type liste « NSX Federation - Locations » : les deux emplacements avec leur version, la connexion au Global Manager, l'état joignable ou non depuis le proxy cloud, les sessions BGP intersites et les échecs de synchronisation complète](/images/nsx-federation-mp/ops-view-locations.webp)

### Lien avec l'intégration NSX native

L'adaptateur relie chaque nœud Edge et chaque cluster Edge à l'objet correspondant de l'intégration NSX native, pour chaque Local Manager que cette intégration supervise. Un réglage désactive les liens ou les impose.

Dans le lab, le cluster Edge de site-a-wld01 et ses deux nœuds Edge ont pour enfants les objets natifs Edge Cluster et Transport Node. Leurs alertes, comme l'alerte native « RTEP Connectivity Lost », sont alors accessibles depuis l'objet fédération. Les nœuds Edge de site-a-wld03 n'ont reçu aucun lien : le compte natif de ce Local Manager ne collecte pas, si bien que les objets natifs n'existent pas. Le pack conserve donc ses propres alertes de tunnel et de nœud Edge. Pour un emplacement dont le Local Manager n'est pas ajouté à l'intégration native, elles constituent la seule couverture.

## Comportement de la collecte

### Un appel en échec laisse la valeur inconnue

La collecte se compose surtout d'appels secondaires : état du cluster, alarmes, sauvegardes, certificats, état de synchronisation. Lorsque l'un d'eux échoue, sur une erreur HTTP ou un dépassement de délai, l'adaptateur n'envoie rien pour les valeurs qui en dépendent. Un appel d'état en échec n'est pas interprété comme « emplacement non connecté », et un appel de sauvegarde en échec n'est pas interprété comme « sauvegarde non configurée ». VCF Operations conserve la dernière valeur connue, et aucune alerte ne se déclenche sur des données qui n'ont jamais été reçues.

L'objet fédération compte ces appels dans la métrique « NSX API calls without an answer » et les liste dans une propriété, avec le manager, l'appel et le code HTTP. Quatre alertes surveillent la collecte elle-même : des données manquantes pendant deux cycles, une collecte qui dure plus de quatre minutes ou que NSX bride, une collecte sans aucun Global Manager, et un manager qui a épuisé tout son quota d'appels. Un appel qui dépasse le délai ou perd sa connexion est retenté une fois avant d'être compté comme en échec. Un manager qui dépasse le délai trois fois de suite, ou qui répond systématiquement par HTTP 429 ou 503, est ignoré jusqu'à la fin du cycle. Un Local Manager dont l'appel d'état du cluster échoue en même temps que la plupart de ses autres appels est signalé comme injoignable.

### Une seule alerte pour la panne d'un emplacement

Un emplacement qui perd sa connexion au Global Manager entraîne de nombreux objets avec lui : les tunnels et les sessions BGP des autres emplacements vers lui, les canaux de synchronisation et tous les objets étendus qui le couvrent. Chacun d'eux a une alerte, et toutes se déclencheraient en plus de celle de l'emplacement.

Tant que le Global Manager actif signale un emplacement comme non connecté, l'adaptateur écrit le nom de cet emplacement dans une propriété, « Locations in outage », sur les objets qui en dépendent : l'emplacement, son cluster Edge, ses nœuds Edge et ses voisins BGP, les tunnels et les canaux de synchronisation dont une extrémité s'y trouve, les objets étendus dont la portée l'inclut, avec leur réalisation sur cet emplacement, et les Global Managers. Une panne sur un autre emplacement continue de déclencher son alerte. Treize définitions d'alerte exigent que cette propriété vaille « none », de même que les alertes d'alarme, à l'exception des alarmes entre Global Managers. Elles restent muettes pendant la panne, et « Location lost its connection to the active Global Manager » est l'alerte à traiter. Dans le XML des alertes, cette condition est un second symptôme avec `negateCondition="true"` :

```xml
<SymptomSet applyOn="self" operator="and">
    <Symptom ref="SymptomDefinition-NSXFederation-seg_error"/>
    <Symptom ref="SymptomDefinition-NSXFederation-outage_segment" negateCondition="true"/>
</SymptomSet>
```

Le symptôme de panne s'efface trois cycles après le retour de l'emplacement, soit un cycle après les symptômes qu'il fait taire : la fin d'une panne ne déclenche donc pas d'alerte non plus.

Les canaux de synchronisation ont une seconde propriété du même type. Les canaux d'un Global Manager qui ne répond pas, ou de tous les Global Managers lorsqu'aucun n'est actif, restent muets derrière l'alerte de la fédération.

Une seconde panne sans rapport, sur les objets de l'emplacement touché, ne déclenche aucune alerte tant que la première dure. Ses métriques et ses propriétés restent collectées.

### Le Global Manager de secours

Quatre métriques décrivent le Global Manager de secours : synchronisation en bon état (1 lorsqu'il est synchronisé), entrées restant à envoyer, minutes sans synchronisation et heures écoulées depuis la dernière synchronisation complète. Le Global Manager actif les expose pour chacun de ses secours, et chaque secours expose son propre état de synchronisation. Le secours compte comme synchronisé lorsque NSX le signale comme tel, ou lorsqu'une synchronisation différentielle fonctionne après une synchronisation complète terminée. Tout autre état compte comme non synchronisé. NSX 9.1 indique ONGOING comme état normal du secours, avec une synchronisation différentielle, une synchronisation complète terminée et aucune entrée restant à envoyer : le libellé de l'état ne peut donc pas servir de test à lui seul. L'alerte « Standby Global Manager is not in sync » se déclenche après trois cycles et s'efface après deux. Dans le lab, la métrique vaut 1 et aucune entrée ne reste à envoyer.

### Versions et mises à niveau

NSX Federation se met à niveau dans un ordre fixe : le Global Manager de secours, le Global Manager actif, puis les Local Managers. Trois conditions rompent cet ordre ou bloquent des opérations, et chacune a son alerte : les Global Managers exécutent des versions différentes, auquel cas NSX refuse un basculement ; un emplacement exécute une release plus récente que celle du Global Manager ; une mise à niveau est en cours, en pause ou en échec sur un manager. L'état de la mise à niveau provient du coordinateur de mise à niveau de chaque manager (`/api/v1/upgrade/summary`).

L'alerte de release compare les trois premiers nombres de la version NSX. Les Local Managers du lab sont en 9.1.1.0 et les Global Managers en 9.1.1.0100 : la même release à deux niveaux de correctif, ce qui ne déclenche aucune alerte.

### Ce que l'adaptateur lit dans VCF Operations

Le proxy cloud donne à un adaptateur une connexion à VCF Operations lui-même. Le pack s'en sert pour trois choses : la liste des clusters NSX que supervise l'intégration NSX native, la liste des emplacements des cycles précédents lorsqu'aucun Global Manager ne répond et que le cache du conteneur de l'adaptateur est vide, et, lorsque le réglage est activé, la suppression des objets que NSX ne renvoie plus et qui n'ont reçu aucune donnée depuis un nombre de jours défini. Une propriété de l'objet fédération indique si la connexion fonctionne. Elle vaut « ok » dans le lab. Sans cette connexion, le pack déclenche lui-même toutes les alertes d'alarme, ne crée aucun lien et ne supprime aucun objet.

### Réalisation des objets étendus

La réalisation est la seule partie de la collecte qui coûte un appel par objet. Jusqu'à 200 objets étendus, chacun est lu à chaque cycle. Au-delà, l'adaptateur relit un objet lorsqu'il est nouveau, modifié dans NSX, incomplètement réalisé, ou lorsque vient son tour dans un balayage qui couvre tous les objets en six cycles ; sinon, il conserve le dernier état. Sur le simulateur NSX, une fédération de 610 objets étendus coûte 610 appels de réalisation au premier cycle et environ 100 à chacun des suivants.

## Tests dans le lab

Le pack dispose d'une suite de tests qui se passe de NSX : un simulateur répond aux appels de l'adaptateur à partir d'un enregistrement anonymisé de la fédération du lab. Ses 65 tests couvrent un cycle normal, des appels en échec, la panne d'un emplacement, un Global Manager d'une autre fédération, la liste des emplacements en cache et celle relue dans VCF Operations, le quota d'appels, l'approbation des certificats, les versions et les mises à niveau, la réalisation par paliers sur 600 segments de plus, la suppression des objets qui ont quitté NSX, et la cohérence du contenu des alertes avec le modèle d'objets. Un workflow GitHub Actions les exécute à chaque push.

La collecte s'exécute aussi depuis le conteneur d'outillage, directement sur les managers du lab, sans VCF Operations :

| Test | Résultat |
| --- | --- |
| Exécution normale, les deux adresses de Global Manager | 72 objets, environ 45 appels d'API (environ 75 lorsque l'inventaire en cache est rechargé), 10 à 25 secondes |
| Seconde adresse de Global Manager injoignable | La collecte se poursuit, avec un événement supplémentaire pour l'adresse injoignable |
| Aucun Global Manager joignable | Les données des deux Local Managers sont tout de même collectées grâce à la liste d'emplacements en cache : les objets des deux emplacements, marqués comme dégradés. Le cache se trouve dans le conteneur de l'adaptateur. Lorsqu'un redémarrage le vide, la liste est relue dans VCF Operations |

### Appels en échec

Ce comportement a été vérifié par injection de pannes lors d'une exécution locale : les appels d'état, de sauvegarde, d'alarmes et de cluster ont été forcés à renvoyer un code HTTP 500 sur chaque manager. L'exécution a signalé 15 appels sans réponse et n'a envoyé aucune des valeurs qui en dépendent.

### Panne d'un emplacement

La règle a été testée sur le lab sans provoquer de panne. Le compte étant arrêté, l'API Suite a écrit « un emplacement en erreur » sur le segment fed-stretched (`POST /api/resources/{id}/stats`), et l'alerte « Stretched segment is in error at a location » s'est déclenchée. L'écriture du nom d'un emplacement dans la propriété (`POST /api/resources/{id}/properties`) a annulé l'alerte alors que le symptôme de la panne restait actif. Les valeurs ont ensuite été rétablies et le compte redémarré.

### Test de basculement

Un basculement planifié a été mené le 19 novembre, pour observer ce que le pack signale lorsque le rôle actif change de Global Manager. Le basculement tient en un seul appel sur le Global Manager de secours, l'équivalent, par l'API, de « Make Active » dans « Location Manager » :

```
PATCH https://<standby-gm>/global-manager/api/v1/global-infra/global-managers/gm-site-b
{"display_name": "gm-site-b", "mode": "ACTIVE"}
```

Le même appel en PUT, avec l'objet complet, a été refusé avec `Global Manager can only be activated locally`. Le PATCH limité au nom et au mode a été accepté. La progression s'affiche en six étapes dans `GET /api/v1/sites/switchover-status` sur le nouveau Global Manager actif.

| Heure | NSX | Pack |
| --- | --- | --- |
| 07:26:31 | Basculement vers gm-site-b demandé ; vérification préalable | |
| 07:27 | | Cycle pendant la vérification préalable : gm-site-a actif, un seul Global Manager actif |
| 07:28 | gm-site-a passe en secours | |
| 07:29:10 | Basculement terminé | |
| 07:32 | | Premier cycle complet : gm-site-b actif, gm-site-a en secours, tous deux joignables, les deux emplacements connectés, secours synchronisé |
| 07:42:32 | Retour vers gm-site-a demandé | |
| 07:44:35 | Retour terminé | |
| 07:52 | | gm-site-a de nouveau actif, secours synchronisé |

Chaque basculement a duré environ deux minutes. Le pack a suivi le rôle actif dès son premier cycle après le changement, sans modification du compte, puisque celui-ci contient les deux adresses. Aucune alerte ne s'est ouverte ni fermée pendant le test. Pendant environ une minute, au cours de la vérification préalable, le nouveau Global Manager actif a présenté les deux Global Managers comme actifs. Le pack a tout de même compté un seul Global Manager actif et n'a déclenché aucune alerte de split-brain.

Après un basculement, l'ancien Global Manager actif devient le Global Manager de secours et expose sa propre valeur de synchronisation, comme le décrit la section « Le Global Manager de secours ».

Un basculement planifié ne laisse jamais la fédération sans Global Manager actif : les alertes « aucun Global Manager actif » et « Global Manager injoignable » ne se sont donc pas déclenchées, comme prévu.

### Événements d'alarme NSX

Le lab n'avait aucune alarme de fédération ouverte. Pour tester les définitions, deux événements ont été envoyés par l'API Suite (`POST /api/events`) sous la forme qu'utilise l'adaptateur : `gm_to_lm_synchronization_warning` sur le Global Manager et `rtep_bgp_down` sur l'emplacement site-a-wld01. Chacun a déclenché son alerte nommée en un cycle, et aucune alerte « Notification event » n'est apparue en parallèle.

L'alerte issue de l'alarme affiche le symptôme, le texte de l'événement et la recommandation :

![Détail de l'alerte « NSX alarm: RTEP BGP neighbor down » sur site-a-wld01 : la description, la recommandation et le symptôme « Local Manager raised NSX alarm rtep_bgp_down » avec le texte de l'événement de test](/images/nsx-federation-mp/ops-alarm-alert.webp)

Les mêmes événements, envoyés de nouveau avec une heure d'annulation, ont effacé les deux alertes au cycle suivant.

## Envoyer les alertes par e-mail

Le pack déclenche les alertes, VCF Operations les envoie. Deux réglages sont nécessaires : une instance sortante, qui connaît le serveur de messagerie, et une règle de notification, qui sélectionne les alertes. Les deux se trouvent sous « Administration > Configurations », dans le groupe « Alerts ». Le lab n'a ni instance sortante ni serveur de messagerie : les formulaires ci-dessous ont donc été ouverts dans l'interface sans être enregistrés, et aucun e-mail n'a été envoyé.

### 1. L'instance sortante

1. Ouvrir « Administration > Configurations > Outbound Settings » et cliquer sur « Add ».
2. Choisir le type de plug-in « Standard Email Plugin » et donner un nom à l'instance.
3. Renseigner « SMTP Host » et « SMTP Port », ainsi que l'adresse et le nom de l'expéditeur. Cocher « Use Secure Connection » et « Requires Authentication » si le serveur de messagerie l'exige. Le formulaire comporte aussi un champ d'identifiants et une adresse de destinataire.
4. Cliquer sur « Test », puis sur « Save ».

Le formulaire, tel qu'il a été ouvert dans le lab :

![Create New Outbound Instance avec le Standard Email Plugin : hôte et port SMTP, connexion sécurisée, adresse et nom de l'expéditeur, identifiants et adresse du destinataire](/images/nsx-federation-mp/ops-email-plugin.webp)

La même liste propose des plug-ins Webhook, Slack, ServiceNow et SNMP trap. La règle de notification décrite ci-dessous fonctionne avec chacun d'eux.

### 2. La règle de notification

1. Ouvrir « Administration > Configurations > Notifications » et cliquer sur « Add ». L'assistant compte cinq étapes, qui correspondent aux points 2 à 6 ci-dessous.
2. **Notification :** saisir un nom, par exemple « NSX Federation alerts by email ».
3. **Define Criteria :** sous « Object Scope », choisir « Object Type » et sélectionner les types d'objets de l'adaptateur NSX Federation. Sous « Alert Scope », laisser « Category » sur toutes les alertes ou le restreindre à « Alert Definition » pour choisir des définitions précises. « Criticality » limite la règle, par exemple à Immediate et Critical. « Notify On » détermine les changements d'état d'une alerte qui déclenchent un message.
4. **Set Outbound Method :** choisir le plug-in e-mail et l'instance sortante créée précédemment.
5. **Select Payload Template :** choisir le modèle qui met en forme le message. Les modèles se gèrent sous « Payload Templates », sur la même page « Configurations ».
6. **Test Notification :** lancer le test, puis cliquer sur « Create ».

Les trois dernières étapes de l'assistant (méthode d'envoi, modèle de message, test) sont décrites d'après leur seul intitulé : faute d'instance sortante, elles n'ont pas pu être déroulées dans le lab. L'étape « Define Criteria » se présente ainsi :

![L'étape Define Criteria d'une règle de notification : périmètre des objets, périmètre des alertes avec catégorie, criticité et état de contrôle, et changements d'état à notifier](/images/nsx-federation-mp/ops-notification-rule.webp)

Une règle portant sur les types d'objets du pack et limitée à Immediate et Critical envoie par e-mail les alertes de ces deux niveaux. Les alertes de niveau Information et Warning ne sont alors visibles que dans l'interface. Une seconde règle, sans limite de criticité et adressée à la boîte aux lettres d'une équipe, permet de les recevoir aussi.

## Notes pour les développeurs de packs

### Les pièges du SDK

Cinq points ont fait perdre du temps pendant la construction. Aucun ne figure dans la documentation du SDK :

- **Les événements dotés d'une clé de défaut sont écartés.** VCF Operations n'affiche un événement d'adaptateur doté d'une clé de défaut (fault key) que si le pack fournit la définition de symptôme correspondante. Sans elle, l'événement disparaît sans qu'aucune erreur n'apparaisse, ni d'un côté ni de l'autre. Le pack envoie ses événements sans clé de défaut.
- **Identifiants de recommandation.** L'identifiant d'une recommandation doit commencer par `Recommendation-df-`. Le schéma du SDK ne le vérifie pas, et sans ce préfixe VCF Operations rejette l'intégralité du fichier d'alertes alors que l'installation indique un succès.
- **Fichiers de schéma dans les dossiers de contenu.** Le modèle de projet place `traversalSpecsSchema.xsd` dans `content/traversalspecs`. Le programme d'installation lit chaque fichier de ce dossier comme une spécification de parcours (traversal specification), échoue sur le schéma avec `Traversal specs root tag should be "AdapterKind"`, et l'arborescence d'inventaire ne se charge jamais. Les fichiers de schéma doivent rester hors du pack.
- **`CollectResult.get_objects_by_type`** compare le type d'adaptateur de chaque objet au type d'objet demandé et renvoie donc toujours une liste vide. L'adaptateur filtre lui-même les objets.
- **Tableaux de bord.** La documentation du SDK recommande de créer un tableau de bord dans l'interface puis de l'exporter. Le pack, lui, génère le JSON de ses tableaux de bord par un script, à partir du même modèle d'objets que l'adaptateur. Le format a été déduit des tableaux de bord exportés par l'API Suite (`/api/content/operations/export`). Dans le fichier, un type d'objet est représenté par un marqueur tel que `resourceKind:id:3_::_`, et une table placée en tête de fichier associe chaque marqueur à un type d'adaptateur et à un type d'objet.

Les définitions d'alerte, les vues et les tableaux de bord peuvent être testés avant toute construction du pack : l'API Suite les importe à partir d'un fichier zip (`POST /suite-api/api/content/operations/import`), sans installation de pack ni activation de l'option des packs non signés.

### Import des tableaux de bord

Deux comportements ont fait perdre du temps après l'import d'un tableau de bord :

- Un tableau de bord importé affiche d'abord des widgets vides. Les listes d'objets montrent tous les objets de l'instance et les widgets « Scoreboard » n'affichent rien. Une tâche de fond de VCF Operations achève l'import quelques minutes plus tard, et les widgets reçoivent alors leurs paramètres.
- Les listes d'objets conservent leurs colonnes par défaut. Les colonnes de métriques ajoutées se retrouvent à droite, derrière une barre de défilement. La disposition des colonnes enregistrée dans le fichier n'a pas été appliquée à l'import.

### Comparaison avec les intégrations natives

Les 1 253 définitions d'alerte et 2 047 définitions de symptôme présentes dans l'instance VCF Operations du lab ont été comparées au pack, en comptant les définitions de chaque type d'adaptateur. Le choix des badges, des délais de réaction et du nommage des alarmes dans le pack découle de cette comparaison :

| | NSX natif | vCenter natif | vSAN natif | Pack NSX Federation |
| --- | --- | --- | --- | --- |
| Définitions d'alerte | 423 | 185 | 225 | 63 |
| Définitions de symptôme par condition | Alarmes NSX sous forme d'événements (381), propriétés (99), seuils de métrique (12) | Propriétés (329), défauts vCenter (76), seuils de métrique (57), événements (33), seuils dynamiques (4) | Défauts (430), propriétés (20), seuils de métrique (2) | Seuils de métrique (38), propriétés (36), alarmes NSX sous forme d'événements (22), seuils dynamiques (2) |
| Badge | 405 Health, 18 Risk | 129 Health, 55 Risk, 1 Efficiency | 206 Health, 15 Risk, 4 Efficiency | 49 Health, 14 Risk |
| Symptômes avec cycles d'attente ou d'annulation supérieurs à 1 | Aucun | 45 | 2 | 31 |
| Alertes du sous-type Compliance | 22 | 42 | 12 | 6 |

## Sources

- Lab : Holodeck VCF 9.1.1 imbriqué, NSX 9.1.1.0 (Local Managers) et 9.1.1.0100 (Global Managers), VCF Operations 9.1.1.0 build 25795063. Construction, installation et tests du 17 au 19 novembre 2026.
- Code source du pack : [github.com/sorinplaton/vcf-ops-nsx-federation-mp](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp), tag v1.7.6
- Objets, métriques et alertes de la version 1.7.6 : [docs/FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.7.6/docs/FEATURES.md) (en anglais) ; réglages du compte et dimensionnement : [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.7.6/docs/OPERATIONS.md) (en anglais)
- [VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction)
- KB Broadcom [415279](https://knowledge.broadcom.com/external/article/415279/error-no-signature-found-on-pak-file-wh.html) : « No signature found on PAK file » lors de l'installation d'un management pack
- [Docker : verify repository client with certificates](https://docs.docker.com/engine/security/certificates/)
