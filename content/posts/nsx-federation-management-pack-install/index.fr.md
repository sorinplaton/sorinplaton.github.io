---
title: "Superviser NSX Federation dans VCF Operations, partie 2 : construction, installation et données collectées par le pack"
date: 2026-11-18
description: "Partie 2 du management pack NSX Federation pour VCF Operations 9.1.1 : l'adaptateur construit avec l'Integration SDK, l'installation pas à pas avec captures d'écran (registre en HTTPS, approbation du certificat sur le proxy cloud, pack non signé, compte), et ce que la version 1.4.0 supervise sur une fédération de lab à deux emplacements : 70 objets, 53 définitions d'alerte, 3 tableaux de bord, 5 vues et un rapport. L'article explique aussi comment envoyer les alertes par e-mail."
summary: "L'adaptateur collecte les données d'une fédération NSX 9.1 à deux emplacements en 12 secondes et 60 appels d'API. Pour qu'il fonctionne dans VCF Operations 9.1.1, il faut un registre d'images servi en HTTPS, un proxy cloud qui approuve le certificat du registre et le réglage qui autorise un pack non signé. Cet article détaille chaque étape de l'installation avec des captures d'écran, liste ce que supervise la version 1.4.0 du pack (objets, métriques, alertes, tableaux de bord, vues) et montre comment envoyer les alertes par e-mail."
tags: ["VCF 9.1.1", "NSX Federation", "VCF Operations", "Management pack", "Integration SDK", "Holodeck"]
categories: ["Notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-federation-mp/object-model.svg"]
params:
  seoDescription: "Construire et installer un management pack NSX Federation sur mesure dans VCF Operations 9.1.1 : registre HTTPS, approbation du certificat, pack non signé, puis objets, métriques, alertes, tableaux de bord et vues de la version 1.4.0."
---

La [partie 1](/fr/posts/nsx-federation-management-pack-design/) s'achevait sur une conception, sans une ligne de code. Cet article décrit la construction du pack, son installation dans VCF Operations 9.1.1 avec des captures d'écran, ce que le pack supervise en version 1.4.0 (objets, métriques, alertes, tableaux de bord, vues) et l'envoi de ses alertes par e-mail. L'écriture de l'adaptateur a demandé moins de temps que son installation : trois contrôles distincts de VCF Operations séparaient le fichier `.pak` d'une collecte opérationnelle. Le pack a connu huit versions en trois jours, et les notes propres à chaque version figurent vers la fin de l'article.

## Le lab

Le lab est un Holodeck VCF 9.1.1 imbriqué. Le Global Manager actif, `nsx-gm-01a`, fédère deux emplacements (les « Locations » de NSX Federation), `site-a-wld01` et `site-a-wld03`, dotés chacun d'un cluster Edge à deux nœuds. VCF Operations est en version 9.1.1.0, build 25795063, avec un seul proxy cloud, `opscollector-01a`. La construction se fait sur une machine de rebond Linux équipée de Docker, 10.0.6.27. Cette même machine héberge le registre d'images, sur le port 5000. La fédération n'a eu aucun Global Manager de secours jusqu'à la nuit du 18 au 19 novembre. Un Global Manager de secours, `nsx-gm-01b`, a alors été ajouté pour tester la version 1.4.0 (voir les notes de version). Chaque Global Manager compte un seul nœud.

## Construire l'adaptateur

Le pack s'appuie sur le VCF Operations Integration SDK 1.3.1. Les outils du SDK (`mp-init`, `mp-test`, `mp-build`) exigent une version de Python absente de la machine de rebond ; ils s'exécutent donc depuis une petite image d'outillage :

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y docker.io git && rm -rf /var/lib/apt/lists/*
RUN pip install vmware-aria-operations-integration-sdk
```

L'adaptateur tient en deux fichiers Python :

- `nsxclient.py` : un client REST NSX en lecture seule, avec pagination par curseur, quota d'appels par manager, nouvelle tentative après attente sur les codes HTTP 429 et 503, épinglage du certificat par empreinte SHA-256 et petit cache sur disque pour les données qui changent peu.
- `adapter.py` : le modèle d'objets, la logique de collecte et les événements.

L'instance d'adaptateur reçoit une ou deux adresses de Global Manager et un seul jeu d'identifiants. Elle découvre les Local Managers à partir du Global Manager et approuve le certificat de chacun d'eux grâce à l'empreinte que le Global Manager a déjà enregistrée.

Avant toute installation, la collecte a été lancée depuis le conteneur d'outillage, directement sur les managers du lab :

| Test | Résultat |
| --- | --- |
| Exécution normale | 65 objets, 60 appels d'API, environ 12 secondes |
| Seconde adresse de Global Manager injoignable | La collecte se poursuit, avec un événement supplémentaire pour l'adresse injoignable |
| Aucun Global Manager joignable | Les données des deux Local Managers sont tout de même collectées grâce à la liste d'emplacements en cache : 37 objets, marqués comme dégradés |

Un pack SDK n'est pas autonome. Le fichier `.pak` contient les définitions d'objets et une référence à une image de conteneur. Le proxy cloud récupère cette image dans un registre au démarrage de l'instance d'adaptateur.

`mp-build` construit le pack. Avec une adresse de registre de la forme `host:port`, il a inscrit Docker Hub dans le pack. Un court script corrige cette erreur en réécrivant deux valeurs, `REGISTRY` et `REPOSITORY`, dans le fichier `.conf` que contient `adapter.zip` :

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
5. Revenir dans la console d'administration et désactiver de nouveau l'installation de fichiers PAK non signés.

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

L'option ne peut pas être désactivée pendant l'installation. Lors de la mise à niveau vers la version 1.2.0, la console d'administration a répondu `Cluster is in PAK installing state` jusqu'à la fin de l'installation. Un script doit donc répéter le dernier appel.

Ces appels sont internes à l'interface : ils ne sont pas pris en charge et peuvent changer à chaque version. Quant à l'option des packs non signés, elle vaut pour toute l'instance ; dans le lab, elle n'est restée activée que le temps de l'envoi.

La première version installée était la 1.0.0. Après l'installation, le type d'adaptateur `NSXFederation` et la solution `iSDK_NSXFederation` apparaissent dans VCF Operations. Le pack figure, avec sa version, sous « Administration > Integrations > Repository ». La capture ci-dessous a été prise avec la version 1.1.1 :

![Administration > Integrations > Repository avec le filtre « Federation » : le pack NSX Federation, en version 1.1.1, avec un compte](/images/nsx-federation-mp/ops-repository-pack.webp)

### Étape 4 : créer le compte

L'instance d'adaptateur, appelée compte dans l'interface, se crée sur le proxy cloud. Elle reçoit les paramètres indiqués dans la section sur la construction : une ou deux adresses de Global Manager et un seul jeu d'identifiants.

Le registre étant approuvé, le proxy cloud a récupéré l'image au démarrage suivant de l'instance, qui est passée à l'état `STARTED`, puis `DATA_RECEIVING`. L'onglet « Accounts » affiche l'instance avec l'état « Collecting ». La capture ci-dessous a été prise avec la version 1.1.1 ; la page était identique en 1.0.0 :

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

Une nouvelle version s'installe comme la première, et chaque version impose d'activer l'option des packs non signés. Dans le lab, trois mises à niveau ont demandé davantage :

- Après la mise à niveau vers la version 1.0.1, l'instance d'adaptateur est restée à l'état `FAILED` jusqu'à son arrêt puis son redémarrage, alors que le proxy cloud avait déjà récupéré la nouvelle image.
- Après la mise à niveau vers la version 1.4.0, l'instance d'adaptateur est restée arrêtée, bien que l'appel qui la redémarrait ait renvoyé un succès. Un second démarrage a été nécessaire.
- La version 1.2.0 a changé l'identité des nœuds Edge et des tunnels RTEP, et la nouvelle identité a créé de nouveaux objets. VCF Operations a recréé les 4 nœuds Edge et les 8 tunnels. Les 12 anciens objets sont restés dans l'inventaire, sans données, jusqu'à leur suppression (voir Objets ci-dessous).

## Ce que le pack supervise (version 1.4.0)

La version 1.4.0 est installée dans le lab. Le contenu d'alerte de la version 1.3.0 a d'abord été importé par l'API Suite et testé sur place ; l'installation du pack l'a ensuite remplacé, sans doublon. Les captures prises avec une version antérieure le précisent.

### Objets

Le pack crée 70 objets de 14 types dans le lab, 65 avant l'ajout du Global Manager de secours. La partie 1 prévoyait 12 types. La relecture à l'échelle d'une grande entreprise y a ajouté la fédération elle-même, le canal de synchronisation et la réalisation d'un objet sur un emplacement ; les règles, elles, sont devenues optionnelles.

| Type d'objet | Nombre dans le lab | Exemples de données collectées |
| --- | --- | --- |
| Fédération | 1 | Global Managers actifs, emplacements interrogés par rapport au total, erreurs d'API, managers ayant dépassé leur quota d'appels |
| Global Manager | 2 | Global Manager de secours synchronisé ou non, entrées restant à lui envoyer, état de la sauvegarde, alarmes ouvertes, jours restants avant la première expiration de certificat |
| Nœud de Global Manager | 2 | Nœud en ligne ou non |
| Emplacement | 2 | Connexion au Global Manager, joignable ou non depuis le proxy cloud, nœuds en ligne, échecs de synchronisation complète, sessions BGP intersites, alarmes, version |
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

Les objets règle de pare-feu globale s'activent stratégie par stratégie ; ils sont restés désactivés pour cette installation.

Le pack rend compte de son propre fonctionnement sur l'objet fédération : appels effectués, appels bridés, erreurs, et épuisement éventuel du quota d'appels d'un manager.

Un nœud Edge et ses tunnels sont identifiés par l'emplacement et le nom du nœud : un nœud Edge redéployé conserve ainsi son objet et son historique. Les nœuds Edge du lab ont été remplacés le 18 novembre, avant la version 1.2.0. Ce remplacement et le changement d'identité de la version 1.2.0 ont laissé 37 anciens objets sans données. Ils ont été supprimés par l'API Suite (`DELETE /api/resources/{id}`), ce qui efface aussi leur historique : chaque nœud Edge et chaque tunnel n'a plus qu'un seul objet.

Dans l'inventaire, les types d'objets forment une arborescence et chaque objet dispose d'une page de résumé. La capture ci-dessous montre un emplacement et date de la version 1.0.x :

![Inventaire de VCF Operations : les types d'objets NSX Federation dans l'arborescence, et le résumé de l'emplacement site-a-wld01 avec ses objets associés, ses compteurs d'alarmes, l'ancienneté de la sauvegarde et l'alerte d'écart de version](/images/nsx-federation-mp/ops-inventory-location.webp)

Depuis la version 1.2.1, le pack dispose aussi d'une arborescence d'inventaire fonctionnelle. Pour l'ouvrir, aller dans « Inventory », cliquer sur « Detailed View », choisir le dernier onglet (l'icône en forme de pièce de puzzle), puis sélectionner « NSX Federation » dans la liste déroulante. L'arborescence part de la fédération. En dessous se trouvent les segments, les passerelles Tier-1 et Tier-0, la stratégie de sécurité globale, le Global Manager et les deux emplacements. Chaque emplacement contient son cluster Edge et ses canaux de synchronisation :

![L'arborescence d'inventaire NSX Federation dans VCF Operations, version 1.2.1 : la fédération, ses segments, passerelles et stratégie, et l'emplacement site-a-wld01 déplié sur son cluster Edge et ses deux canaux de synchronisation](/images/nsx-federation-mp/ops-inventory-tree.webp)

La version 1.4.0 ajoute quatre groupes personnalisés (custom groups) : tous les objets du pack (70 dans le lab), managers et emplacements (7), connectivité intersite (39) et objets étendus (24). Un groupe réunit les alertes et les badges de ses membres, et une politique ou une règle de notification peut le prendre pour périmètre. Les groupes se trouvent sous « Inventory > Custom Groups > Environment ». L'appartenance dépend du type d'objet : un nouvel emplacement ou un nouveau tunnel rejoint donc son groupe sans modification. Un groupe par emplacement exigerait le nom de chaque emplacement, si bien que le pack n'en fournit pas.

![Groupe personnalisé « NSX Federation - Managers and locations » : les deux Global Managers, la fédération, les deux emplacements et les deux nœuds de Global Manager, sans alerte ouverte](/images/nsx-federation-mp/ops-custom-groups.webp)

### Métriques

La version 1.4.0 envoie 419 métriques par cycle dans le lab, une fois les taux disponibles, contre 398 pour la version 1.2.1, avant l'ajout du Global Manager de secours.

Depuis la version 1.4.0, les métriques sont rangées en groupes, comme dans les intégrations natives : Sync, Connectivity, Certificates, Alarms, Rates, etc., soit 17 groupes au total. L'arborescence des métriques d'un objet devient ainsi assez courte pour se lire d'un coup d'œil. La clé d'une métrique contient son groupe, par exemple `sync|standby_in_sync`. L'historique d'une métrique issue d'une version antérieure reste rattaché à son ancienne clé, sans groupe.

![Onglet « Metrics » du Global Manager gm-site-a avec la version 1.4.0 : les groupes Alarms, Certificates, Cluster, Connectivity et Sync, et la courbe de « Standby Global Managers in sync » à 1](/images/nsx-federation-mp/ops-metric-groups.webp)

Dix-huit métriques sont marquées comme indicateurs clés, de une à quatre par type d'objet : les Global Managers actifs et la part d'emplacements en bonne santé sur la fédération, les nœuds hors ligne et la synchronisation du Global Manager de secours sur un Global Manager, la connexion et la synchronisation complète d'un emplacement, BGP et les paquets rejetés sur un tunnel, etc. VCF Operations les affiche en premier dans le résumé de l'objet :

![Résumé de gm-site-a avec la version 1.4.0 : l'alerte de sauvegarde ouverte et, à droite, les indicateurs clés « Nodes offline 0 » et « Standby Global Managers in sync 1 »](/images/nsx-federation-mp/ops-key-attributes.webp)

NSX renvoie les paquets, les octets, les rejets et les correspondances de règles sous forme de totaux cumulés. L'adaptateur conserve les compteurs du cycle précédent et ajoute des taux : paquets, rejets et paquets bloqués par minute et octets par seconde sur les tunnels RTEP, correspondances de règles par minute sur les stratégies, les réalisations et les règles. Les unités sont déclarées : heures, jours, secondes, octets, paquets et taux par minute.

Trois métriques sont dérivées par l'adaptateur (Global Managers injoignables, sessions BGP intersites non établies, emplacements non réalisés), car un symptôme compare une métrique à une valeur fixe et ne sait pas soustraire deux métriques.

La version 1.3.0 ajoute sur l'objet fédération sept valeurs qui portent sur toute la fédération : emplacements en bonne santé, en nombre et en pourcentage ; tunnels RTEP ; tunnels dont la session BGP est tombée ; paquets RTEP rejetés par minute sur l'ensemble des tunnels ; jours restants avant la première expiration de certificat, tous managers confondus ; alarmes critiques ouvertes sur l'ensemble des managers. Une super métrique pourrait en calculer une partie, mais elle reste inactive tant qu'une politique ne l'active pas : l'adaptateur calcule donc les sept. Elles apparaissent dans le widget « Scoreboard » de Federation Overview. Dans le lab, la version 1.3.0 affiche deux emplacements sains sur deux (100 %), 8 tunnels, aucun tombé, aucun paquet rejeté, 331,6 jours avant la première expiration de certificat (celui d'un Local Manager ; le premier certificat du Global Manager expire dans 823 jours) et aucune alarme critique.

Le tableau ci-dessous donne la couverture de chaque type d'objet. La colonne Métriques compte les clés de métrique définies pour le type. Tous les objets n'envoient pas toutes les clés (seules les réalisations de stratégie portent des compteurs, par exemple) : la colonne multipliée par le nombre d'objets ne donne donc pas 398. Les nombres sont ceux de la version 1.4.0.

| Type d'objet | Métriques | Définitions d'alerte | Tableau de bord |
| --- | --- | --- | --- |
| Fédération | 21 | 5 | Federation Overview |
| Global Manager | 12 | 17 | Federation Overview |
| Nœud de Global Manager | 1 | Aucune, le nœud est couvert par l'alerte de cluster du Global Manager | Objets associés uniquement |
| Emplacement | 14 | 18 | Federation Overview |
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

La version 1.4.0 compte 53 définitions d'alerte, 67 symptômes et 27 recommandations. Ces définitions tiennent dans un seul fichier XML, placé dans `content/alertdefs`. Un script le génère à partir d'une table des symptômes, des alertes et des recommandations. Un symptôme est une condition portant sur une métrique, sur une propriété ou sur un événement. Deux symptômes utilisent un seuil dynamique. Une condition sur une propriété se présente ainsi :

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

Les 53 définitions se répartissent ainsi par type d'objet :

| Type d'objet | Définitions | Alertes |
| --- | --- | --- |
| Fédération | 5 | Aucun Global Manager actif ; split-brain (deux Global Managers actifs) ; Global Manager injoignable depuis le proxy cloud ; aucun Global Manager de secours configuré ; collecte partielle (quota d'appels d'API épuisé) |
| Global Manager | 17 | Cluster dégradé ; sauvegarde absente, trop ancienne ou en échec ; certificat proche de l'expiration ; Global Manager de secours non synchronisé ; 13 alarmes de fédération NSX (voir ci-dessous) |
| Emplacement | 18 | Connexion au Global Manager actif perdue ; Local Manager injoignable depuis le proxy cloud ; certificat non approuvé ; synchronisation complète non terminée ; sessions BGP intersites tombées ; version différente de celle du Global Manager ; sauvegarde absente ou trop ancienne ; certificat proche de l'expiration ; cluster du Local Manager dégradé ; 9 alarmes de fédération NSX |
| Canal de synchronisation | 1 | Canal interrompu |
| Cluster Edge | 1 | État intersite différent de UP |
| Nœud Edge | 1 | Toutes les sessions BGP intersites tombées |
| Tunnel RTEP | 3 | Session BGP intersite tombée sur le tunnel ; plus de 60 paquets rejetés par minute dans un sens ; trafic hors de sa plage habituelle (seuil dynamique) |
| Voisin BGP du Tier-0 | 2 | Session tombée ; session de liaison montante établie sans aucun préfixe reçu |
| Tier-0, Tier-1, segment, stratégie de sécurité globale | 1 chacun | Emplacements en erreur |
| Objet étendu sur un emplacement | 1 | Non réalisé |

L'alerte du nœud Edge ne se déclenche que lorsque le nœud a perdu toutes ses sessions intersites, car la perte d'une seule session déclenche l'alerte du tunnel. « Stretched object is not realized at a location » est de niveau Warning, car les alertes d'erreur sur l'objet lui-même sont de niveau Immediate.

La version 1.3.0 règle aussi ce que compte chaque alerte et la vitesse à laquelle elle réagit :

- **Badge.** Huit alertes font baisser le badge « Risk » au lieu du badge « Health » : les deux alertes de sauvegarde, les deux alertes d'expiration de certificat, le certificat non approuvé, l'absence de Global Manager de secours, l'écart de version et le quota d'appels. Ces conditions posent problème plus tard, et « Health » reste réservé à ce qui est en panne maintenant. L'intégration vCenter native place 55 de ses 185 définitions sur « Risk ».
- **Cycles d'attente et d'annulation.** Neuf symptômes susceptibles d'osciller exigent deux cycles de collecte, soit 10 minutes, pour déclencher une alerte, et deux pour l'effacer : canal de synchronisation interrompu ; sessions BGP intersite tombées, ou aucune établie, sur l'emplacement ; aucune session intersite sur le nœud Edge ; BGP intersite tombé sur le tunnel ; paquets rejetés sur le tunnel, en réception et en émission ; voisin BGP du Tier-0 tombé ; aucun préfixe reçu. Le seuil dynamique attend trois cycles.
- **Sous-type Compliance.** Quatre alertes portent le sous-type Compliance : l'absence de Global Manager de secours, les deux alertes de sauvegarde et l'écart de version. Dans VCF Operations 9.1, la page « Compliance » affiche un bandeau qui annonce l'abandon de l'ancienne fonction Compliance dans VCF 9.1 et renvoie vers Security Posture Management. Le sous-type ne sert donc qu'à classer les alertes, et aucun référentiel personnalisé n'a été construit dessus.
- **Seuils.** L'ancienneté maximale de la sauvegarde (24 heures) et le délai d'avertissement avant expiration d'un certificat (30 jours) sont fixés dans les symptômes. Jusqu'à la version 1.3.0, le compte proposait deux réglages pour ces valeurs, mais ils ne modifiaient que des événements que le pack n'envoyait plus depuis l'arrivée des définitions d'alerte. La version 1.4.0 supprime ces deux réglages. Une politique VCF Operations peut remplacer le seuil d'un symptôme par une autre valeur.
- **Seuil dynamique.** L'alerte de trafic du tunnel compare les débits en réception et en émission à la plage que VCF Operations apprend pour chaque tunnel. Elle a besoin de plusieurs jours d'historique avant de pouvoir se déclencher.

Avant l'ajout du Global Manager de secours, 4 alertes se déclenchaient dans le lab, issues de 3 définitions : absence de Global Manager de secours, sauvegarde du Global Manager et écart de version sur les deux emplacements. Ces 4 alertes décrivent correctement l'état du lab et, depuis l'import du contenu d'alerte 1.3.0, elles pèsent sur « Risk ». Une fois le Global Manager de secours en place, l'alerte d'absence de Global Manager de secours s'est effacée et l'alerte de sauvegarde s'est déclenchée aussi sur le nouveau Global Manager : il reste donc 4 alertes, issues de 2 définitions. En dehors des deux alertes d'alarme testées plus bas, aucune autre définition ne s'est déclenchée, faute de panne correspondante dans le lab. La liste des alertes affiche les 4 premières sous le nom de leur définition (capture prise avec la version 1.1.1) :

![La liste des alertes filtrée sur « Global Manager » avec la version 1.1.1 : les 4 alertes portent désormais le nom de leur définition](/images/nsx-federation-mp/ops-alerts-defined.webp)

Une alerte issue d'une définition affiche aussi sa recommandation et le symptôme qui l'a déclenchée :

![Détail de l'alerte « Global Manager backup is missing, old or failed » sur gm-site-a : la description, la recommandation et le symptôme « Backup is not configured on the Global Manager »](/images/nsx-federation-mp/ops-alert-recommendation.webp)

L'objet fédération, dont la seule alerte était l'absence de Global Manager de secours, affichait « Health » à 100 % et « Risk » à 25 %. Après l'ajout du Global Manager de secours, son « Risk » est revenu à 0 % :

![Résumé de l'objet fédération après l'import du contenu d'alerte 1.3.0 : « Badge Health » à 100 %, « Badge Risk » à 25 %, et l'alerte « NSX Federation has no standby Global Manager »](/images/nsx-federation-mp/ops-federation-risk.webp)

#### Le Global Manager de secours

Jusqu'à la version 1.3.0, l'état de synchronisation d'un Global Manager de secours arrivait sous forme d'événement chaque fois que NSX ne le déclarait pas synchronisé. Le test de synchronisation avait été écrit avant que le lab ne dispose d'un Global Manager de secours. Lors de son ajout, la version 1.3.0 a levé une alerte « Notification event » de niveau Immediate : `Standby Global Manager gm-site-b sync status is ONGOING`. Or NSX 9.1 indique ONGOING comme état normal d'un Global Manager de secours, avec une synchronisation delta, une synchronisation complète terminée et aucune entrée restant à envoyer.

La version 1.4.0 remplace l'événement par deux métriques sur le Global Manager actif : Global Managers de secours synchronisés (1 lorsque tous le sont) et entrées restant à envoyer. Un Global Manager de secours compte comme synchronisé lorsque sa synchronisation complète est terminée et que la synchronisation delta fonctionne, et comme non synchronisé en cas d'échec ou d'erreur. L'alerte « Standby Global Manager is not in sync » se déclenche après trois cycles et s'efface après deux. Dans le lab, la métrique vaut 1 et aucune entrée ne reste à envoyer. Comme le pack n'envoie plus l'événement, l'ancienne alerte « Notification event » est restée ouverte après la mise à niveau, jusqu'à son annulation par l'API Suite (`POST /api/alerts?action=cancel`).

#### Les alarmes NSX comme alertes nommées

L'adaptateur relaie les alarmes de fédération du Global Manager, et celles de chaque Local Manager, sous forme d'événements dont le texte est `NSX alarm <event_type>: <summary>`. L'intégration NSX native utilise la même forme et s'en sert pour donner à 381 types d'alarme une alerte propre. Jusqu'à la version 1.2.1, le pack n'avait aucun symptôme pour ces événements : ils apparaissaient comme des alertes « Notification event » anonymes, sans recommandation.

La version 1.3.0 donne aux 13 types d'alarme de fédération de NSX 9.1 un symptôme, une alerte et une recommandation propres : split-brain entre Global Managers ; erreurs et avertissements de synchronisation entre les Global Managers, entre le Global Manager et un emplacement, et entre emplacements ; les deux avertissements de latence ; la file de synchronisation au-dessus de son seuil ; un Local Manager restauré pendant l'import de sa configuration ; RTEP BGP tombé ; connectivité RTEP perdue. Le Global Manager reçoit les 13. Un emplacement en reçoit 9, car les quatre alarmes entre Global Managers ne le concernent pas. Les alarmes de latence couvrent en partie le point ouvert sur la latence entre managers : NSX en lève une lorsque la latence reste au-dessus de son seuil pendant deux minutes.

L'intégration NSX native dispose des mêmes alarmes, mais uniquement pour les managers qui y sont ajoutés comme comptes NSX. Dans le lab, ce sont les Local Managers : les alarmes du Global Manager n'arrivent donc dans VCF Operations que par ce pack.

Le lab n'avait aucune alarme de fédération ouverte. Pour tester les définitions, deux événements ont été envoyés par l'API Suite (`POST /api/events`) sous la forme qu'utilise l'adaptateur : `gm_to_lm_synchronization_warning` sur le Global Manager et `rtep_bgp_down` sur l'emplacement site-a-wld01. Chacun a déclenché son alerte nommée en un cycle, et aucune alerte « Notification event » n'est apparue à côté. La liste des alertes de la fédération, de son Global Manager et de ses emplacements :

![Alertes de la fédération, de son Global Manager et de ses emplacements avec le contenu d'alerte 1.3.0 : les deux alertes d'alarme NSX issues des événements de test, à côté des 4 alertes du lab](/images/nsx-federation-mp/ops-alerts-130.webp)

L'alerte d'alarme affiche le symptôme, le texte de l'événement et la recommandation :

![Détail de l'alerte « NSX alarm: RTEP BGP neighbor down » sur site-a-wld01 : la description, la recommandation et le symptôme « Local Manager raised NSX alarm rtep_bgp_down » avec le texte de l'événement de test](/images/nsx-federation-mp/ops-alarm-alert.webp)

Les mêmes événements, envoyés de nouveau avec une heure d'annulation, ont effacé les deux alertes au cycle suivant.

Les conditions sans définition arrivent toujours sous forme d'événements : les autres alarmes NSX ouvertes de chaque emplacement et toutes les alarmes critiques du Global Manager. Un tel événement apparaît sous la forme d'une alerte « Notification event » sur l'objet concerné, sans recommandation. Une condition dotée d'une définition d'alerte portant sur une métrique ou une propriété n'émet pas d'événement. Sinon, elle apparaîtrait deux fois : une fois comme alerte, une fois comme « Notification event ».

Le remplacement des nœuds Edge du 18 novembre en a fourni un exemple. Les alarmes NSX `rtep_bgp_down` et `rtep_connectivity_lost` sont apparues sous forme d'alertes sur les deux objets emplacement. VCF Operations a annulé chacune d'elles environ 15 minutes plus tard, sans intervention d'un opérateur.

### Tableaux de bord

Trois tableaux de bord sont regroupés dans un dossier nommé « NSX Federation » :

| Tableau de bord | Contenu |
| --- | --- |
| Federation Overview | Alertes ouvertes sur les objets du pack ; liste regroupant la fédération, les Global Managers et les emplacements ; valeurs clés, propriétés et objets associés de l'élément sélectionné |
| Inter-site Connectivity | Canaux de synchronisation et nœuds Edge, tunnels RTEP, voisins BGP du Tier-0 ; valeurs clés de la ligne sélectionnée |
| Stretched Objects | Passerelles, segments et stratégies étendus, avec leur portée ; réalisation de chaque objet sur chaque emplacement |

Tous fonctionnent sur le même principe : une liste alimente un widget « Scoreboard », et un clic sur l'une des valeurs de ce widget en affiche la courbe de tendance.

Federation Overview, avec le Global Manager sélectionné (version 1.1.1) :

![Le tableau de bord Federation Overview : les 4 alertes ouvertes, regroupées sous 3 définitions, la liste des objets de la fédération avec gm-site-a sélectionné, ses valeurs clés, ses objets associés et ses propriétés](/images/nsx-federation-mp/ops-dashboard-overview.webp)

Avec la version 1.3.0 et la fédération sélectionnée, le widget « Scoreboard » se termine par les agrégats de fédération : emplacements en bonne santé, RTEP BGP tombé, paquets RTEP rejetés par minute, expiration des certificats et alarmes critiques :

![Le tableau de bord Federation Overview avec la version 1.3.0 et la fédération sélectionnée : le widget « Scoreboard » affiche les erreurs d'API, le budget d'appels, les emplacements en bonne santé à 100, RTEP BGP tombé à 0, les paquets RTEP rejetés par minute à 0, l'expiration des certificats à 331,6 jours et les alarmes critiques à 0](/images/nsx-federation-mp/ops-dashboard-rollups.webp)

Avec la version 1.4.0 et gm-site-a sélectionné, le widget « Scoreboard » d'un Global Manager commence par la synchronisation du Global Manager de secours. La liste contient désormais les deux Global Managers, et les alertes ouvertes sont celles de la sauvegarde et de l'écart de version :

![Le tableau de bord Federation Overview avec la version 1.4.0 : deux définitions d'alerte ouvertes (sauvegarde, écart de version), la liste avec gm-site-a et gm-site-b, et le widget « Scoreboard » de gm-site-a avec Standby in sync à 1, les nœuds hors ligne à 0 et l'expiration des certificats à 822,9 jours](/images/nsx-federation-mp/ops-dashboard-standby.webp)

Inter-site Connectivity, capturé avec la version 1.2.0, montre les taux d'un tunnel RTEP :

![Le tableau de bord Inter-site Connectivity : canaux de synchronisation et nœuds Edge, tunnels RTEP, voisins BGP du Tier-0 et, avec la version 1.2.0, les taux du tunnel RTEP sélectionné](/images/nsx-federation-mp/ops-dashboard-connectivity.webp)

Stretched Objects, avec le segment fed-stretched sélectionné :

![Le tableau de bord Stretched Objects : les passerelles, segments et stratégies étendus, la réalisation par emplacement, ainsi que les valeurs clés, les propriétés et les objets associés du segment fed-stretched](/images/nsx-federation-mp/ops-dashboard-stretched.webp)

Deux comportements ont fait perdre du temps après l'import :

- Un tableau de bord importé affiche d'abord des widgets vides. Les listes d'objets montrent tous les objets de l'instance et les widgets « Scoreboard » n'affichent rien. Une tâche de fond de VCF Operations achève l'import quelques minutes plus tard, et les widgets reçoivent alors leurs paramètres.
- Les listes d'objets conservent leurs colonnes par défaut. Les colonnes de métriques ajoutées se retrouvent à droite, derrière une barre de défilement. La disposition des colonnes enregistrée dans le fichier n'a pas été appliquée à l'import.

### Vues et rapport

Les cinq vues de type liste portent sur les emplacements, les canaux de synchronisation, les tunnels RTEP, les voisins BGP du Tier-0 et la réalisation des objets étendus par emplacement. Un rapport, « NSX Federation - Health report », les réunit dans un même PDF. Une vue est un fichier XML. Un script le génère, comme pour les alertes et les tableaux de bord, et l'API Suite peut l'importer pour un essai avant la construction du pack. La vue Locations, capturée avec la version 1.2.0 :

![La vue de type liste « NSX Federation - Locations » : les deux emplacements avec leur version, la connexion au Global Manager, l'état joignable ou non depuis le proxy cloud, les sessions BGP intersites et les échecs de synchronisation complète](/images/nsx-federation-mp/ops-view-locations.webp)

### Lien avec l'intégration NSX native

Une option de l'instance d'adaptateur, désactivée par défaut, relie chaque nœud Edge et chaque cluster Edge à l'objet correspondant de l'intégration NSX native. La partie 1 laissait ouverte la question de savoir jusqu'où le pack étend les objets NSX natifs, et cette option est la réponse apportée à ce stade.

L'option a été activée dans le lab avec la version 1.2.1. Après un cycle, le cluster Edge de site-a-wld01 et ses deux nœuds Edge avaient pour enfants les objets natifs Edge Cluster et Transport Node. Leurs alertes, comme l'alerte native « RTEP Connectivity Lost », sont alors accessibles depuis l'objet fédération. Les nœuds Edge de site-a-wld03 n'ont reçu aucun lien : le Local Manager de cet emplacement n'est pas un compte NSX dans VCF Operations, si bien que les objets natifs n'existent pas. Le pack conserve donc ses propres alertes de tunnel et de nœud Edge. Pour un emplacement dont le Local Manager n'est pas ajouté à l'intégration native, elles sont la seule couverture.

## Envoyer les alertes par e-mail

Le pack déclenche les alertes, VCF Operations les envoie. Deux réglages sont nécessaires : une instance sortante, qui connaît le serveur de messagerie, et une règle de notification, qui sélectionne les alertes. Les deux se trouvent sous « Administration > Configurations », dans le groupe « Alerts ». Le lab n'a ni instance sortante ni serveur de messagerie : les formulaires ci-dessous ont donc été ouverts dans l'interface sans être enregistrés, et aucun e-mail n'a été envoyé.

### 1. L'instance sortante

1. Ouvrir « Administration > Configurations > Outbound Settings » et cliquer sur « Add ».
2. Choisir le type de plug-in « Standard Email Plugin » et donner un nom à l'instance.
3. Renseigner « SMTP Host » et « SMTP Port », ainsi que l'adresse et le nom de l'expéditeur. Cocher « Use Secure Connection » et « Requires Authentication » si le serveur de messagerie l'exige. Le formulaire comporte aussi des identifiants et une adresse de destinataire.
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

Une règle portant sur les types d'objets du pack et limitée à Immediate et Critical envoie par e-mail les alertes de ces deux niveaux. L'écart de version et l'absence de Global Manager de secours ne sont alors visibles que dans l'interface. Une seconde règle, sans limite de criticité et adressée à la boîte aux lettres d'une équipe, permet de les recevoir aussi.

## Notes de version, de 1.0.0 à 1.4.0

### 1.0.0 et 1.0.1 : les événements comme alertes

Les versions 1.0.0 et 1.0.1 envoyaient 312 métriques par cycle et ne contenaient aucune définition d'alerte. Chaque alerte était un « Notification event » issu d'un événement de l'adaptateur.

La version 1.0.0 affichait trois alertes :

- Warning : aucun Global Manager de secours n'est configuré pour cette fédération.
- Information, deux fois : écart de version, chaque emplacement étant en 9.1.1.0 et le Global Manager en 9.1.1.0100.

Une quatrième était attendue et manquait à l'appel : la sauvegarde non configurée sur le Global Manager. L'adaptateur émettait bien cet événement lors d'une exécution locale, et l'objet Global Manager portait la propriété `backup_enabled = False`, mais aucune alerte n'apparaissait.

La cause se trouvait dans l'adaptateur. L'événement de sauvegarde portait une clé de défaut (fault key), contrairement aux trois événements visibles. VCF Operations n'affiche un événement doté d'une clé de défaut que si le pack fournit la définition de symptôme correspondante, et ce pack n'en contenait aucune. Un tel événement est écarté sans qu'aucune erreur n'apparaisse, ni dans l'adaptateur ni dans VCF Operations.

La version 1.0.1 envoie tous les événements sans clé de défaut. Dès le premier cycle après la mise à niveau, la quatrième alerte est apparue :

- Immediate : la sauvegarde n'est pas configurée sur le Global Manager `gm-site-a`.

La liste des alertes avec la version 1.0.1 :

![Alertes de VCF Operations filtrées sur « Global Manager » : l'alerte de sauvegarde sur gm-site-a, l'absence de Global Manager de secours sur l'objet fédération, et l'écart de version sur les deux emplacements](/images/nsx-federation-mp/ops-alerts.webp)

### 1.1.0 et 1.1.1 : définitions d'alerte et tableaux de bord

L'emploi des événements comme alertes présente deux limites. Ils ne portent aucune recommandation, et tous s'appellent « Notification event ». La version 1.1 a ajouté au pack des définitions d'alerte et les trois tableaux de bord. Son fichier d'alertes comptait 33 symptômes, 22 définitions d'alerte et 18 recommandations. Ces 22 définitions sont celles du tableau des alertes ci-dessus, moins les 7 ajoutées en 1.2.0.

Trois modifications de l'adaptateur ont accompagné ces définitions :

- Les trois métriques dérivées. Avec elles, le pack envoyait 331 métriques par cycle dans le lab.
- Une condition dotée d'une définition d'alerte n'émet plus d'événement.
- Les conditions sans définition restent des événements : les alarmes NSX relayées et l'état de synchronisation d'un Global Manager de secours.

Les symptômes et les définitions d'alerte ont été testés avant toute construction du pack. L'API Suite importe du contenu d'alerte à partir d'un fichier zip (`POST /suite-api/api/content/operations/import`). Cet import ne demande ni installation de pack ni activation de l'option des packs non signés. Environ 5 minutes après l'import, les 4 alertes qui correspondent à l'état du lab se sont déclenchées, issues de 3 définitions. Les 19 autres ne se sont pas déclenchées.

Ce premier test s'est fait sans les recommandations, à cause d'une contrainte que le schéma du SDK ne vérifie pas : l'identifiant d'une recommandation doit commencer par `Recommendation-df-`. Sans ce préfixe, VCF Operations rejette l'intégralité du fichier d'alertes. La version 1.1.0 s'est installée avec l'état completed, ses tableaux de bord ont été importés, et son fichier d'alertes a été rejeté pour cette raison. La version 1.1.1 corrige le préfixe.

Un tableau de bord est un fichier JSON, et la documentation du SDK décrit une façon de l'obtenir : créer le tableau de bord dans l'interface, puis l'exporter. Pour ce pack, un script écrit le JSON à partir du même modèle d'objets que l'adaptateur. Le format a été déduit des tableaux de bord que VCF Operations exporte par l'API Suite (`/api/content/operations/export`). Dans le fichier, un type d'objet est représenté par un marqueur tel que `resourceKind:id:3_::_`, et une table placée en tête de fichier associe chaque marqueur à un type d'adaptateur et à un type d'objet.

### 1.2.0 : les lacunes face à l'intégration NSX native

Chaque type d'objet du pack a été comparé à l'intégration NSX native (50 types d'objets, 423 définitions d'alerte). L'examen de la version 1.1.1 a relevé 7 lacunes, que la version 1.2.0 comble :

| Domaine | Lacune de la version 1.1.1 | Apport de la version 1.2.0 |
| --- | --- | --- |
| Les compteurs sont des cumuls | Paquets, octets, rejets et correspondances de règles étaient les totaux cumulés que renvoie NSX. Sans taux, aucun seuil ne pouvait porter sur les rejets par minute | Des taux, détaillés dans la section Métriques |
| Tunnel RTEP | Un tunnel dont la session BGP est tombée déclenchait l'alerte de son nœud Edge. Le tunnel lui-même n'avait pas d'alerte, pas plus que les paquets rejetés ou bloqués | Deux alertes : session BGP intersite tombée sur le tunnel, et plus de 60 paquets rejetés par minute dans un sens |
| Cluster Edge | L'état intersite du cluster était une propriété sans alerte | Une alerte lorsque l'état intersite n'est pas UP |
| Objets étendus en erreur | La métrique « emplacements en erreur » n'avait pas d'alerte | Une alerte pour chacun des types Tier-0, Tier-1, segment et stratégie de sécurité globale |
| Alarmes du Global Manager | Le pack comptait les alarmes ouvertes du Global Manager et ne relayait sous forme d'événements que celles des Local Managers | Les alarmes de fédération et toutes les alarmes critiques du Global Manager arrivent sous forme d'événements |
| Unités | Aucune métrique ne déclarait d'unité | Heures, jours, secondes, octets, paquets et taux par minute sont déclarés |
| Vues, rapports et arborescence d'inventaire | Le pack n'en contenait aucun | Cinq vues de type liste, un rapport et une spécification de parcours (traversal specification) pour l'arborescence |

Deux autres changements sont arrivés avec la version 1.2.0. Les nœuds Edge et les tunnels sont désormais identifiés par l'emplacement et le nom du nœud, pour le cas des objets remplacés. L'option qui crée les liens vers l'intégration NSX a été ajoutée.

Deux alertes existantes ont changé dans la même version. L'alerte du nœud Edge a été restreinte à la perte de toutes les sessions intersites. « Stretched object is not realized at a location » est passée d'Immediate à Warning.

Après l'installation, l'adaptateur a collecté les mêmes 65 objets et les 4 mêmes alertes se sont déclenchées. Aucune des 7 nouvelles alertes ne s'est déclenchée. C'est le comportement attendu dans un lab sain, mais ces alertes restent de ce fait non testées.

L'arborescence d'inventaire ne s'est pas chargée. Le journal d'installation indique `Traversal specs root tag should be "AdapterKind"`, alors que la spécification de parcours est valide au regard du schéma du SDK et porte bien cette balise racine.

### 1.2.1 : l'arborescence d'inventaire

Le modèle de projet du SDK place ses fichiers de schéma à côté du contenu : `traversalSpecsSchema.xsd` dans `content/traversalspecs` et `alertDefinitionSchema.xsd` dans `content/alertdefs`. Le programme d'installation lit chaque fichier de `content/traversalspecs` comme une spécification de parcours. Le fichier de schéma n'a pas de balise racine `AdapterKind`, si bien que l'import de tout le dossier a échoué. Le journal du nœud Operations montre la même erreur pour les versions précédentes, qui contenaient le fichier de schéma sans aucune spécification.

La version 1.2.1 est la version 1.2.0 sans les deux fichiers de schéma, retirés du pack. L'import des spécifications de parcours s'est alors déroulé sans erreur, et l'arborescence est apparue dans l'interface (capture dans la partie Objets).

### 1.3.0 : ce que font les intégrations natives

Les 1 219 définitions d'alerte et 1 991 définitions de symptôme présentes dans le VCF Operations du lab ont été comparées au pack :

| | NSX natif | vCenter natif | vSAN natif | Pack 1.2.1 |
| --- | --- | --- | --- | --- |
| Définitions d'alerte | 423 | 185 | 225 | 29 |
| Déclencheur principal | Alarmes NSX sous forme d'événements (381), propriétés | Propriétés (702), défauts vCenter (107) | Défauts (504) | Seuils de métrique (32), propriétés (10) |
| Badge | 405 Health, 18 Risk | 129 Health, 55 Risk, 1 Efficiency | 206 Health, 15 Risk, 4 Efficiency | 29 Health |
| Cycles d'attente ou d'annulation supérieurs à 1 | Aucun | 31 | Aucun | Aucun |
| Alertes Compliance | 9 | 2 | Aucune | Aucune |

Aucune super métrique n'était définie sur le système. La version 1.3.0 retient de cette comparaison les alertes d'alarme nommées, le badge « Risk », les cycles d'attente et d'annulation, le sous-type Compliance, les agrégats de fédération et un seuil dynamique, tous décrits plus haut dans les sections Alertes et Métriques.

Les agrégats se sont aussi heurtés à un bug du SDK. `CollectResult.get_objects_by_type` compare le type d'adaptateur de chaque objet au type d'objet demandé et renvoie donc toujours une liste vide. L'adaptateur filtre lui-même les objets.

### 1.4.0 : groupes de métriques et Global Manager de secours

La version 1.4.0 rapproche le pack des intégrations natives dans sa façon de présenter les données : métriques réparties en 17 groupes, 18 indicateurs clés et quatre groupes personnalisés, tous décrits plus haut. Elle supprime aussi les deux réglages du compte pour l'ancienneté de la sauvegarde et l'avertissement de certificat, qui n'avaient plus aucun effet.

Un Global Manager de secours a été ajouté au lab pour cette version. `nsx-gm-01b` a été déployé à partir de la même appliance 9.1.1.0100 que le Global Manager actif, en taille Small, sur le cluster de `site-a-wld03`, avec une adresse et un enregistrement DNS dans le réseau de management. Il a ensuite été enregistré sur le Global Manager actif par l'API de celui-ci, sous le nom `gm-site-b` en mode STANDBY, avec son adresse, les identifiants admin et l'empreinte de son certificat (`PUT /global-manager/api/v1/global-infra/global-managers/gm-site-b`). La première synchronisation complète s'est terminée en quelques minutes. Le compte du pack a reçu la seconde adresse, et le cycle suivant a montré deux Global Managers, tous deux joignables, dont un actif.

Le Global Manager de secours a révélé la fausse alerte de synchronisation décrite dans la section Alertes. La correction, les métriques de synchronisation du Global Manager de secours et leur alerte font partie de la version 1.4.0.

## Ce qui manque encore

Dans le pack :

- Un pack signé. Chaque nouvelle version impose d'activer l'option des packs non signés.

Points restés ouverts depuis la partie 1 :

- La latence entre managers sous forme de métrique. NSX la mesure et l'affiche dans son interface ; l'API qui la fournit n'a pas été trouvée. Depuis la version 1.3.0, les alarmes de latence NSX déclenchent une alerte.
- L'état du pare-feu sur les hôtes, second point resté ouvert dans la partie 1.
- Le simulateur pour les cas que le lab ne peut pas reproduire. Il n'a pas été construit.

Tests :

- La plupart des définitions d'alerte, qui exigent des pannes que le lab ne présente pas. Le Global Manager de secours est en place depuis la version 1.4.0, mais un basculement, un Global Manager de secours non synchronisé et un split-brain n'ont pas été testés. Les alertes d'alarme ont été testées avec des événements envoyés, et non avec de vraies alarmes NSX.
- Le seuil dynamique, qui exige plusieurs jours d'historique.
- Le rapport.
- La notification par e-mail, qui exige un serveur de messagerie absent du lab.

## Sources

- Lab : Holodeck VCF 9.1.1 imbriqué, NSX 9.1.1.0 (Local Managers) et 9.1.1.0100 (Global Manager), VCF Operations 9.1.1.0 build 25795063. Construction et installation les 17 et 18 novembre 2026 ; versions 1.0.1, 1.1.0, 1.1.1, 1.2.0 et 1.2.1 le 18 novembre ; versions 1.3.0 et 1.4.0 le 19 novembre ; Global Manager de secours ajouté les 18 et 19 novembre.
- [VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction)
- KB Broadcom [415279](https://knowledge.broadcom.com/external/article/415279/error-no-signature-found-on-pak-file-wh.html) : « No signature found on PAK file » lors de l'installation d'un management pack
- [Docker : verify repository client with certificates](https://docs.docker.com/engine/security/certificates/)
