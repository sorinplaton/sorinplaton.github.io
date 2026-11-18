---
title: "Superviser NSX Federation dans VCF Operations, partie 2 : construction, installation et données collectées par le pack"
date: 2026-11-18
description: "Partie 2 du management pack NSX Federation pour VCF Operations 9.1.1 : l'adaptateur construit avec l'Integration SDK, les trois obstacles qui séparent un fichier .pak d'un collecteur opérationnel (pack non signé, registre accessible uniquement en HTTPS, certificat non approuvé), les 65 objets, 312 métriques et quatre alertes que le pack produit sur une fédération de lab à deux emplacements, les 22 définitions d'alerte et les trois tableaux de bord ajoutés en version 1.1, et la marche à suivre pour envoyer les alertes par e-mail."
summary: "L'adaptateur collecte une NSX Federation 9.1 à deux emplacements en 12 secondes et 60 appels d'API. Pour le faire fonctionner dans VCF Operations 9.1.1, il a fallu trois corrections : autoriser un pack non signé, servir le registre d'images en HTTPS et faire approuver le certificat du registre par le proxy cloud. Cet article détaille chaque étape et liste ce que le pack collecte. Il présente aussi les définitions d'alerte et les tableaux de bord de la version 1.1, ainsi que l'envoi des alertes par e-mail."
tags: ["VCF 9.1.1", "NSX Federation", "VCF Operations", "Management pack", "Integration SDK", "Holodeck"]
categories: ["Notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-federation-mp/object-model.svg"]
params:
  seoDescription: "Construire et installer un management pack NSX Federation sur mesure dans VCF Operations 9.1.1 : pack non signé, registre privé, approbation du certificat, objets et métriques collectés, définitions d'alerte et tableaux de bord."
---

La [partie 1](/fr/posts/nsx-federation-management-pack-design/) s'achevait sur une conception, sans une ligne de code. Cet article couvre la construction du pack, son installation dans VCF Operations 9.1.1 et ce qu'il affiche une fois la collecte lancée. L'écriture de l'adaptateur a demandé moins de temps que son installation : trois contrôles distincts de VCF Operations séparaient le fichier `.pak` d'un collecteur opérationnel.

## Le lab

Un Holodeck VCF 9.1.1 imbriqué. Un Global Manager (`nsx-gm-01a`, un seul nœud actif, pas de Global Manager de secours) fédère deux emplacements (les « Locations » de NSX Federation), `site-a-wld01` et `site-a-wld03`, dotés chacun d'un cluster Edge à deux nœuds. VCF Operations est en version 9.1.1.0, build 25795063, avec un seul proxy cloud, `opscollector-01a`. La construction se fait sur une machine de rebond Linux équipée de Docker.

## Étape 1 : construire l'adaptateur

Le pack s'appuie sur le VCF Operations Integration SDK 1.3.1. Les outils du SDK (`mp-init`, `mp-test`, `mp-build`) exigent une version de Python absente de la machine de rebond ; ils s'exécutent donc depuis une petite image d'outillage :

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y docker.io git && rm -rf /var/lib/apt/lists/*
RUN pip install vmware-aria-operations-integration-sdk
```

L'adaptateur tient en deux fichiers Python :

- `nsxclient.py` : un client REST NSX en lecture seule, avec pagination par curseur, budget d'appels par manager, temporisation avant nouvel essai sur les codes HTTP 429 et 503, épinglage du certificat par empreinte SHA-256 et petit cache sur disque pour les données qui changent peu.
- `adapter.py` : le modèle d'objets, la logique de collecte et les événements.

L'instance d'adaptateur reçoit une ou deux adresses de Global Manager et un seul jeu d'identifiants. Elle découvre les Local Managers à partir du Global Manager et approuve chacun d'eux grâce à l'empreinte de certificat que le Global Manager détient déjà.

Avant toute installation, la collecte a été lancée depuis le conteneur d'outillage sur les managers du lab en fonctionnement :

| Test | Résultat |
| --- | --- |
| Exécution normale | 65 objets, 60 appels d'API, environ 12 secondes |
| Seconde adresse de Global Manager injoignable | La collecte se poursuit, avec un événement supplémentaire pour l'adresse injoignable |
| Aucun Global Manager joignable | Les deux Local Managers sont tout de même collectés grâce à la liste d'emplacements en cache : 37 objets, marqués comme dégradés |

## Étape 2 : un registre pour l'image de l'adaptateur

Un pack SDK ne se suffit pas à lui-même. Le fichier `.pak` contient les définitions d'objets et une référence à une image de conteneur, que le proxy cloud récupère dans un registre au démarrage de l'instance d'adaptateur. Le lab utilise un conteneur `registry:2` sur la machine de rebond.

Avec une adresse de la forme `host:port`, `mp-build` a inscrit le mauvais registre dans le pack (il s'est replié sur Docker Hub). Un court script réécrit deux valeurs, `REGISTRY` et `REPOSITORY`, dans le fichier `.conf` que contient `adapter.zip` :

```
REGISTRY=10.0.6.27:5000
REPOSITORY=/nsxfederation
DIGEST=sha256:4085...86e9
```

## Étape 3 : VCF Operations refuse un pack non signé

Le pack n'est pas signé par Broadcom. L'envoi par l'API de l'appliance échoue :

```
pak_manager.pakfile.no_signature
The PAK file ... is not signed. It is missing required signature files signature.mf and signature.cert
```

Par défaut, VCF Operations 9 bloque les packs non signés, et la case « Ignore the PAK file signature checking » (ignorer la vérification de signature du fichier PAK) reste masquée dans la boîte de dialogue d'envoi tant que ce blocage n'est pas levé. L'option se trouve dans la console d'administration (`https://<vcf-operations>/admin`), sous « Administrator Settings, Security Settings » : « Allow unsigned PAK installation ». Broadcom la décrit dans la KB 415279.

![La console d'administration de VCF Operations, Administrator Settings, Security Settings : l'option « Allow unsigned PAK installation » est désactivée, avec le bouton qui permet de l'activer](/images/nsx-federation-mp/ops-admin-unsigned-pak.webp)

Une fois l'option activée, le pack peut s'installer de deux façons.

### Méthode 1 : à la main, dans l'interface

1. Dans la console d'administration, cliquer sur « Activate unsigned PAK installation ».
2. Dans VCF Operations, ouvrir « Administration, Integrations, Repository », puis choisir d'ajouter un pack.
3. Sélectionner le fichier `.pak` et cocher les deux cases : celle qui force l'installation même si le pack est déjà installé, et « Ignore the PAK file signature checking ».
4. Lancer l'envoi, accepter l'avertissement relatif au fichier non signé et attendre la fin de l'installation.
5. Revenir dans la console d'administration et désactiver de nouveau l'installation de fichiers PAK non signés.

### Méthode 2 : par script

L'API d'envoi de l'appliance (`/casa/upgrade/cluster/pak/reserved/operation/upload`) n'offre aucun paramètre pour ignorer le contrôle de signature, même lorsque l'option est activée. Un script doit donc passer par les mêmes appels que l'interface, et celle-ci les protège par un jeton anti-CSRF (`secureToken`) qu'un simple client HTTP ne reçoit jamais. Dans VCF Operations 9.1.1, ce jeton figure dans la page chargée sous `/vcf-operations/plug/ops/`, à l'intérieur de l'interface principale.

Le lab a utilisé un Chrome sans interface graphique (headless) sur la machine de rebond, piloté par le Chrome DevTools Protocol :

1. Chrome ouvre une session sur la console d'administration et appelle `clusterManagement.action` avec `mainAction=enableUPI`. La même page propose aussi `getUPIStatus` et `disableUPI`.
2. Chrome ouvre une session sur l'interface du produit. Un court script lit dans la page les cookies de session et la valeur de `secureToken`.
3. Un script Python réutilise cette session pour trois appels :

```
POST admin/services/solution/upload?uploadId=<ms>&ignoreSignatureChecking=true&secureToken=<token>
     multipart field "solution" = the .pak file, forceUpload=true, forceContent=true
POST solution.action   mainAction=install
POST solution.action   mainAction=getInstallStatus      (until "completed")
```

4. Chrome appelle `disableUPI`.

L'étape 2 doit suivre l'étape 1, car l'ouverture de session sur la console d'administration ferme la session de l'interface du produit. Un jeton de session lu auparavant n'est plus valide.

Ces appels sont internes à l'interface : ils ne sont pas pris en charge et peuvent changer à chaque version. L'option vaut pour toute l'instance. Dans le lab, elle n'est restée activée que le temps de l'envoi.

La première version installée était la 1.0.0. Après l'installation, le type d'adaptateur `NSXFederation` et la solution `iSDK_NSXFederation` apparaissent dans VCF Operations. Le pack figure, avec sa version, sous Administration, Integrations, Repository. La capture est postérieure à la dernière mise à niveau mentionnée dans cet article, vers la version 1.1.1.

![Administration, Integrations, Repository avec le filtre « Federation » : le pack NSX Federation, en version 1.1.1, avec un compte](/images/nsx-federation-mp/ops-repository-pack.webp)

## Étape 4 : le proxy cloud ne s'adresse à un registre qu'en HTTPS

L'instance d'adaptateur, créée sur le proxy cloud, est passée de `STARTING` à `FAILED` avec un message d'état vide. Le journal du registre ne contenait aucune requête.

Une capture de paquets sur le port du registre en a donné la raison : le proxy cloud ouvrait une connexion et envoyait un Hello TLS à un registre qui ne servait que du HTTP en clair. Or le proxy cloud ne se replie pas sur HTTP. Le registre a donc été recréé avec un certificat :

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

## Étape 5 : le proxy cloud n'approuve pas le certificat

Une fois le HTTPS en place, le journal du registre a changé (10.0.6.33 est le routeur du lab, qui masque le proxy cloud derrière du NAT) :

```
http: TLS handshake error from 10.0.6.33:56544: remote error: tls: bad certificate
```

Le proxy cloud exécute Docker, et Docker approuve un registre privé lorsque le certificat de celui-ci se trouve dans un dossier qui porte le nom du registre :

```
/etc/docker/certs.d/10.0.6.27:5000/ca.crt
```

SSH est désactivé sur le proxy cloud et l'est resté. Le fichier a été écrit au moyen des opérations invité de vCenter (`govc guest.run` avec le compte root de l'appliance). Au démarrage suivant, le proxy cloud a récupéré l'image et l'instance est passée à l'état `STARTED`, puis `DATA_RECEIVING`. L'onglet « Accounts » affiche l'instance avec l'état « Collecting ». Cette capture date elle aussi de la version 1.1.1 ; la page était identique en 1.0.0.

![Administration, Integrations, Accounts : le compte « NSX Federation - nsx-gm-01a » à l'état Collecting, sur le proxy cloud opscollector-01a](/images/nsx-federation-mp/ops-account-collecting.webp)

Une mise à niveau ou un redéploiement du proxy cloud peut faire disparaître ce fichier. Un registre dont le certificat est émis par une autorité que le proxy cloud approuve déjà dispense de cette étape.

## Étape 6 : les trois premiers cycles de collecte

Les données n'arrivent pas toutes en même temps. Avec un intervalle de cinq minutes :

| Cycle | Ce que VCF Operations enregistre |
| --- | --- |
| 1 | Les 65 objets et leurs relations |
| 2 | Les propriétés et les événements ; les clés de métrique sont déclarées |
| 3 | Les métriques |

Deux minutes après le premier cycle, un contrôle montre des objets sans métriques, ce qui fait penser à un pack défectueux. Les métriques apparaissent environ dix minutes après le démarrage.

## Ce que le pack collecte

Avec les versions 1.0.0 et 1.0.1, le pack collecte 65 objets de 14 types et 312 métriques par cycle. La partie 1 prévoyait 12 types. Le réexamen à l'échelle d'une grande entreprise y a ajouté la fédération elle-même, le canal de synchronisation et la réalisation d'un objet sur un emplacement ; les règles, elles, sont devenues optionnelles.

| Type d'objet | Nombre dans le lab | Exemples de données collectées |
| --- | --- | --- |
| Fédération | 1 | Global Managers actifs, emplacements collectés sur le total, erreurs d'API, managers ayant dépassé leur budget d'appels |
| Global Manager | 1 | État de synchronisation du Global Manager de secours, état de la sauvegarde, alarmes ouvertes, jours restants avant la première expiration de certificat |
| Nœud de manager | 1 | État en ligne |
| Emplacement | 2 | Connexion au Global Manager, accessibilité depuis le collecteur, nœuds en ligne, échecs de synchronisation complète, sessions BGP intersites, alarmes, version |
| Canal de synchronisation | 6 | État de la connexion entre deux managers |
| Cluster Edge | 2 | État intersite, membres, adresses RTEP |
| Nœud Edge | 4 | Tunnels RTEP, sessions BGP intersites |
| Tunnel RTEP | 8 | État BGP, paquets, paquets rejetés et bloqués dans chaque sens |
| Voisin BGP | 16 | État, préfixes reçus et annoncés, messages, durée d'établissement ; 4 sessions de liaison montante et 12 sessions internes entre nœuds Edge |
| Passerelle Tier-0 | 1 | Étendue, emplacements réalisés, emplacements en erreur |
| Passerelle Tier-1 | 3 | Comme pour la passerelle Tier-0 |
| Segment | 5 | Idem, avec en plus les sous-réseaux et la passerelle connectée |
| Stratégie de sécurité globale | 1 | Étendue, réalisation, nombre total de règles et de règles désactivées |
| Réalisation (objet sur un emplacement) | 14 | Réalisé ou non, libellé de l'état |

Les objets règle s'activent stratégie par stratégie ; ils sont restés désactivés pour cette installation.

Le pack rend compte de son propre fonctionnement sur l'objet fédération : appels effectués, appels bridés, erreurs, et épuisement éventuel du budget d'appels d'un manager.

![Inventaire de VCF Operations : les types d'objets NSX Federation dans l'arborescence, et le résumé de l'emplacement site-a-wld01 avec ses objets associés, ses compteurs d'alarmes, l'ancienneté de la sauvegarde et l'alerte d'écart de version](/images/nsx-federation-mp/ops-inventory-location.webp)

### Les alertes du premier jour

Les versions 1.0.0 et 1.0.1 ne contiennent aucune définition d'alerte. Un événement émis par l'adaptateur apparaît dans VCF Operations sous la forme d'une alerte « Notification event » sur l'objet concerné.

La version 1.0.0 affichait trois alertes :

- **Warning :** aucun Global Manager de secours n'est configuré pour cette fédération.
- **Information, deux fois :** écart de version, chaque emplacement étant en 9.1.1.0 et le Global Manager en 9.1.1.0100.

Une quatrième était attendue et manquait à l'appel : la sauvegarde non configurée sur le Global Manager. L'adaptateur émettait bien cet événement lors d'une exécution locale, et l'objet Global Manager portait la propriété `backup_enabled = False`, mais aucune alerte n'apparaissait.

La cause se trouvait dans l'adaptateur. L'événement de sauvegarde portait une clé de défaut (fault key), contrairement aux trois événements visibles. VCF Operations n'affiche un événement doté d'une clé de défaut que si le pack fournit la définition de symptôme correspondante, et ce pack n'en contient aucune. Un tel événement est écarté sans qu'aucune erreur n'apparaisse, ni d'un côté ni de l'autre.

La version 1.0.1 envoie tous les événements sans clé de défaut. Après la mise à niveau, l'instance d'adaptateur est restée à l'état `FAILED` jusqu'à ce qu'elle soit arrêtée puis redémarrée. Le proxy cloud avait déjà récupéré la nouvelle image. Dès le premier cycle, la quatrième alerte est apparue :

- **Immediate :** la sauvegarde n'est pas configurée sur le Global Manager `gm-site-a`.

![Alertes de VCF Operations filtrées sur « Global Manager » : l'alerte de sauvegarde sur gm-site-a, l'absence de Global Manager de secours sur l'objet fédération, et l'écart de version sur les deux emplacements](/images/nsx-federation-mp/ops-alerts.webp)

Toutes les quatre décrivent fidèlement le lab.

Les alarmes NSX ouvertes de chaque emplacement arrivent par le même chemin. Le 18 novembre, pendant le remplacement de nœuds Edge dans le lab, les alarmes `rtep_bgp_down` et `rtep_connectivity_lost` sont apparues sous forme d'alertes sur les deux objets emplacement. VCF Operations a annulé chacune d'elles environ 15 minutes plus tard, sans aucune intervention de ce côté.

## Version 1.1 : définitions d'alerte et tableaux de bord

Utiliser les événements comme alertes a deux limites : ils ne portent aucune recommandation, et tous s'appellent « Notification event ». La version 1.1 ajoute au pack de véritables définitions d'alerte et trois tableaux de bord.

### Définitions d'alerte

Le contenu tient dans un seul fichier XML, placé dans `content/alertdefs` : 33 symptômes, 22 définitions d'alerte et 18 recommandations. Un script le génère à partir d'une table des symptômes, des alertes et des recommandations. Chaque symptôme est une condition portant sur une métrique ou sur une propriété :

```xml
<SymptomDefinition adapterKind="NSXFederation" id="SymptomDefinition-NSXFederation-gm_backup_off"
                   name="Backup is not configured on the Global Manager" resourceKind="global_manager">
    <State severity="immediate">
        <Condition key="backup_enabled" operator="=" thresholdType="static" type="property"
                   value="False" valueType="string"/>
    </State>
</SymptomDefinition>
```

| Type d'objet | Alertes |
| --- | --- |
| Fédération | Aucun Global Manager actif ; split-brain (deux Global Managers actifs) ; Global Manager injoignable depuis le collecteur ; aucun Global Manager de secours configuré ; collecte partielle (budget d'appels d'API épuisé) |
| Global Manager | Cluster dégradé ; sauvegarde absente, trop ancienne ou en échec ; certificat proche de l'expiration |
| Emplacement | Connexion au Global Manager actif perdue ; Local Manager injoignable depuis le collecteur ; certificat non approuvé ; synchronisation complète non terminée ; sessions BGP intersites tombées ; version différente de celle du Global Manager ; sauvegarde absente ou trop ancienne ; certificat proche de l'expiration ; cluster du Local Manager dégradé |
| Canal de synchronisation | Canal interrompu |
| Nœud Edge | Sessions BGP intersites tombées |
| Voisin BGP du Tier-0 | Session tombée ; session de liaison montante établie sans aucun préfixe reçu |
| Objet étendu sur un emplacement | Non réalisé |

Trois modifications de l'adaptateur accompagnent ces définitions :

- Trois métriques dérivées, car un symptôme compare une métrique à une valeur fixe et ne sait pas soustraire deux métriques : Global Managers injoignables, sessions BGP intersites non établies, emplacements non réalisés. Avec elles, le pack envoie 331 métriques par cycle dans le lab.
- Une condition couverte par une définition d'alerte n'émet plus d'événement. Sinon, elle apparaîtrait deux fois : une fois comme alerte, une fois comme « Notification event ».
- Les conditions sans définition restent des événements : les alarmes NSX relayées et l'état de synchronisation d'un Global Manager de secours, que le lab ne permet pas de tester.

Les symptômes et les définitions d'alerte ont été testés avant toute construction du pack, dans un premier temps sans les recommandations. L'API Suite importe du contenu d'alerte à partir d'un fichier zip (`POST /suite-api/api/content/operations/import`), sans installation de pack et sans l'option des packs non signés. Environ cinq minutes après l'import, les quatre alertes qui correspondent à l'état du lab se sont déclenchées : absence de Global Manager de secours, sauvegarde du Global Manager, et écart de version sur les deux emplacements. Les 18 autres ne se sont pas déclenchées, faute de panne correspondante dans le lab.

Une règle échappe au schéma du SDK : l'identifiant d'une recommandation doit commencer par `Recommendation-df-`. Sans ce préfixe, VCF Operations rejette l'intégralité du fichier d'alertes. La version 1.1.1 n'existe que pour corriger ce préfixe. Installées avec le pack, les définitions déclenchent les quatre mêmes alertes, cette fois sous leur propre nom :

![La liste des alertes filtrée sur « Global Manager » avec la version 1.1.1 : les quatre alertes portent désormais le nom de leur définition](/images/nsx-federation-mp/ops-alerts-defined.webp)

Une alerte issue d'une définition affiche aussi sa recommandation et le symptôme qui l'a déclenchée :

![Détail de l'alerte « Global Manager backup is missing, old or failed » sur gm-site-a : la description, la recommandation et le symptôme « Backup is not configured on the Global Manager »](/images/nsx-federation-mp/ops-alert-recommendation.webp)

### Tableaux de bord

Trois tableaux de bord sont regroupés dans un dossier nommé « NSX Federation » :

| Tableau de bord | Contenu |
| --- | --- |
| Federation Overview | Alertes ouvertes sur les objets du pack ; liste regroupant la fédération, les Global Managers et les emplacements ; valeurs clés, propriétés et objets associés de l'élément sélectionné |
| Inter-site Connectivity | Canaux de synchronisation et nœuds Edge, tunnels RTEP, voisins BGP du Tier-0 ; valeurs clés de la ligne sélectionnée |
| Stretched Objects | Passerelles, segments et stratégies étendus, avec leur étendue ; réalisation de chaque objet sur chaque emplacement |

Tous fonctionnent sur le même principe : une liste alimente un widget « Scoreboard » (un tableau de valeurs clés), et un clic sur l'une de ces valeurs en trace la tendance.

![Le tableau de bord Federation Overview : les quatre alertes ouvertes, regroupées sous trois définitions, la liste des objets de la fédération avec gm-site-a sélectionné, ses valeurs clés, ses objets associés et ses propriétés](/images/nsx-federation-mp/ops-dashboard-overview.webp)

![Le tableau de bord Inter-site Connectivity : canaux de synchronisation et nœuds Edge, tunnels RTEP, voisins BGP du Tier-0 et valeurs clés du nœud Edge sélectionné](/images/nsx-federation-mp/ops-dashboard-connectivity.webp)

![Le tableau de bord Stretched Objects : les passerelles, segments et stratégies étendus, la réalisation par emplacement, ainsi que les valeurs clés, les propriétés et les objets associés du segment fed-stretched](/images/nsx-federation-mp/ops-dashboard-stretched.webp)

Un tableau de bord est un fichier JSON, et la documentation du SDK décrit une façon de l'obtenir : créer le tableau de bord dans l'interface, puis l'exporter. Pour ce pack, un script écrit le JSON à partir du même modèle d'objets que l'adaptateur. Le format a été déduit des tableaux de bord que VCF Operations exporte par l'API Suite (`/api/content/operations/export`). Dans le fichier, un type d'objet est représenté par un marqueur tel que `resourceKind:id:3_::_`, et une table placée en tête de fichier associe chaque marqueur à un type d'adaptateur et à un type d'objet.

Deux comportements ont fait perdre du temps :

- Un tableau de bord importé affiche d'abord des widgets vides. Les listes d'objets montrent tous les objets de l'instance et les widgets Scoreboard n'affichent rien. Une tâche de fond de VCF Operations achève l'import quelques minutes plus tard, et les widgets reçoivent alors leurs paramètres.
- Les listes d'objets conservent leurs colonnes par défaut. Les colonnes de métriques ajoutées se retrouvent à droite, derrière une barre de défilement. La disposition des colonnes enregistrée dans le fichier n'a pas été appliquée à l'import.

### Couverture par type d'objet

Chaque type d'objet du pack a été comparé à l'intégration NSX native (50 types d'objets, 423 définitions d'alerte). Le résultat, type par type :

| Type d'objet | Métriques | Définitions d'alerte | Tableau de bord |
| --- | --- | --- | --- |
| Fédération | 14 | 5 | Federation Overview |
| Global Manager | 10 | 3 | Federation Overview |
| Nœud de Global Manager | 1 | Aucune, le nœud est couvert par l'alerte de cluster du Global Manager | Objets associés uniquement |
| Emplacement | 14 | 9 | Federation Overview |
| Canal de synchronisation | 1 | 1 | Inter-site Connectivity |
| Cluster Edge | 4 | Aucune | Inter-site Connectivity |
| Nœud Edge | 4 | 1 | Inter-site Connectivity |
| Tunnel RTEP | 9 | Aucune | Inter-site Connectivity |
| Voisin BGP du Tier-0 | 7 | 2 | Inter-site Connectivity |
| Tier-0, Tier-1 et segment étendus | 4 chacun | Aucune, ces objets sont couverts par l'alerte de réalisation | Stretched Objects |
| Stratégie de sécurité globale | 7 | Aucune, la stratégie est couverte par l'alerte de réalisation | Stretched Objects |
| Objet étendu sur un emplacement | 5 | 1 | Stretched Objects |
| Règle de pare-feu globale (optionnelle) | 4 | Aucune | Aucun |

Cette comparaison a fait apparaître les lacunes suivantes :

- **Tunnel RTEP.** Un tunnel dont la session BGP est tombée déclenche l'alerte de son nœud Edge. Le tunnel lui-même n'a pas d'alerte, pas plus que les paquets rejetés ou bloqués.
- **Les compteurs sont des cumuls.** Paquets, octets, rejets et correspondances de règles sont les cumuls que renvoie NSX. Le pack ne calcule aucun taux : un seuil sur les rejets par minute n'est donc pas encore possible.
- **Cluster Edge.** L'état intersite du cluster est une propriété sans alerte.
- **Objets étendus en erreur.** La métrique « emplacements en erreur » n'a pas d'alerte. Un objet en échec sur un emplacement ne déclenche que l'alerte de la réalisation concernée.
- **Alarmes du Global Manager.** Le pack compte les alarmes ouvertes du Global Manager, et ne relaie sous forme d'événements que celles des Local Managers.
- **Unités.** Aucune métrique ne déclare d'unité : heures, jours, octets et paquets s'affichent comme de simples nombres.
- **Vues, rapports et super métriques.** Le pack n'en contient aucun.

## Envoyer les alertes par e-mail

Le pack déclenche les alertes, VCF Operations les envoie. Deux réglages sont nécessaires : une instance sortante (« Outbound Settings »), qui connaît le serveur de messagerie, et une règle de notification, qui sélectionne les alertes. Les deux se trouvent sous « Administration, Configurations », dans le groupe « Alerts ». Le lab n'a ni instance sortante ni serveur de messagerie : les formulaires ci-dessous ont donc été ouverts dans l'interface sans être enregistrés, et aucun e-mail n'a été envoyé.

### 1. L'instance sortante

1. Ouvrir Administration, Configurations, Outbound Settings et cliquer sur « Add ».
2. Choisir le type de plug-in « Standard Email Plugin » et donner un nom à l'instance.
3. Renseigner « SMTP Host » et « SMTP Port », ainsi que l'adresse et le nom de l'expéditeur. Cocher « Use Secure Connection » et « Requires Authentication » si le serveur de messagerie l'exige.
4. Cliquer sur « Test », puis sur « Save ».

![Create New Outbound Instance avec le Standard Email Plugin : hôte et port SMTP, connexion sécurisée, adresse et nom de l'expéditeur, identifiants et adresse du destinataire](/images/nsx-federation-mp/ops-email-plugin.webp)

La même liste propose des plug-ins Webhook, Slack, ServiceNow et SNMP trap. La règle de l'étape suivante fonctionne avec chacun d'eux.

### 2. La règle de notification

1. Ouvrir Administration, Configurations, Notifications et cliquer sur « Add ». L'assistant compte cinq étapes.
2. **Notification :** un nom, par exemple « NSX Federation alerts by email ».
3. **Define Criteria :** sous « Object Scope », choisir « Object Type » et sélectionner les types d'objets de l'adaptateur NSX Federation. Sous « Alert Scope », le champ « Category » peut rester sur toutes les alertes ou être restreint à « Alert Definition » pour choisir des définitions précises. « Criticality » limite la règle, par exemple à Immediate et Critical. « Notify On » détermine les changements d'état d'une alerte qui déclenchent un message.
4. **Set Outbound Method :** le plug-in e-mail et l'instance créée à la section 1.
5. **Select Payload Template :** le modèle qui met en forme le message. Les modèles se gèrent sous « Payload Templates », sur la même page Configurations.
6. **Test Notification**, puis « Create ».

Les étapes 4 à 6 reprennent les intitulés de l'assistant. Faute d'instance sortante, le lab n'a pas permis de les parcourir.

![L'étape Define Criteria d'une règle de notification : périmètre des objets, périmètre des alertes avec catégorie, criticité et état de contrôle, et changements d'état à notifier](/images/nsx-federation-mp/ops-notification-rule.webp)

Une règle portant sur les types d'objets du pack et limitée à Immediate et Critical envoie les pannes par e-mail. L'écart de version et l'absence de Global Manager de secours restent dans l'interface. Une seconde règle, sans limite de criticité et adressée à la boîte aux lettres d'une équipe, les couvre.

## Ce qui manque encore

- Une arborescence d'inventaire (traversal specification).
- Les lacunes relevées dans la couverture : des alertes pour les tunnels RTEP, les clusters Edge, les objets étendus en erreur et les alarmes du Global Manager, des taux pour les compteurs, et des unités sur les métriques.
- Un test de la notification par e-mail, qui exige un serveur de messagerie absent du lab.
- Les tests de 18 des 22 définitions d'alerte, qui exigent des pannes que le lab ne présente pas.
- Le nettoyage des objets remplacés. Un nœud Edge redéployé reçoit un nouvel identifiant dans NSX et devient un nouvel objet. Après le remplacement des nœuds Edge, 26 anciens objets (nœuds Edge et tunnels) sont restés dans l'inventaire, sans données, à côté des 65 objets actifs.
- La latence entre managers. NSX la mesure et l'affiche dans son interface ; l'API qui la fournit n'a pas été trouvée.
- L'état du pare-feu sur les hôtes, second point resté ouvert dans la partie 1.
- Les liens vers les objets de l'intégration NSX native.
- Les tests d'un Global Manager de secours, d'un basculement et d'un split-brain. Le lab n'a qu'un seul Global Manager.
- Un pack signé. Chaque nouvelle version impose d'activer l'option des packs non signés.

## Sources

- Lab : Holodeck VCF 9.1.1 imbriqué, NSX 9.1.1.0 (Local Managers) et 9.1.1.0100 (Global Manager), VCF Operations 9.1.1.0 build 25795063. Construction et installation les 17 et 18 novembre 2026 ; versions 1.0.1, 1.1.0 et 1.1.1 le 18 novembre.
- [VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction)
- KB Broadcom [415279](https://knowledge.broadcom.com/external/article/415279/error-no-signature-found-on-pak-file-wh.html) : « No signature found on PAK file » lors de l'installation d'un management pack
- [Docker : verify repository client with certificates](https://docs.docker.com/engine/security/certificates/)
