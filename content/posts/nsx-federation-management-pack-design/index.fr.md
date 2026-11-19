---
title: "Superviser NSX Federation dans VCF Operations, partie 1 : concevoir le management pack"
date: 2026-11-17
description: "VCF Operations 9.1.1 dispose d'une instance d'adaptateur pour chaque Local Manager NSX, mais ne voit rien d'une fédération NSX : ni Global Manager, ni synchronisation entre emplacements, ni tunnels RTEP, ni objets étendus. Cet article décrit la conception d'un management pack sur mesure dans un lab imbriqué : ce que couvre l'intégration native, les appels d'API NSX qui donnent l'état de la fédération, le modèle d'objets, les alertes, et ce qu'a changé la relecture de la conception à l'échelle d'une grande entreprise."
summary: "Une fédération NSX 9.1 à deux emplacements tourne dans le lab, et VCF Operations n'en montre rien. Cette première partie précède toute ligne de code : elle vérifie l'intégration NSX native, explore les API du Global Manager et des Local Managers, puis fixe un modèle à 12 types d'objets, la liste des métriques et des alertes, et les 8 changements imposés par une relecture à l'échelle d'une grande entreprise."
tags: ["VCF 9.1.1", "NSX Federation", "VCF Operations", "Management pack", "Integration SDK", "Nested lab"]
categories: ["Notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-federation-mp/object-model.svg"]
params:
  seoDescription: "Concevoir un management pack VCF Operations pour NSX 9.1 Federation : appels d'API, modèle d'objets, alertes, et les changements imposés par une relecture à l'échelle d'une grande entreprise."
---

VCF Operations 9.1.1 dispose d'une instance d'adaptateur pour chaque Local Manager NSX du lab, mais ne montre rien de la fédération qui relie deux d'entre eux. Le Global Manager, l'état de synchronisation entre les emplacements (les « Locations » de NSX Federation), les tunnels RTEP et les passerelles étendues n'y ont ni objet, ni métrique, ni alerte. Cet article décrit la conception d'un management pack qui les ajoute. La construction et l'installation sont décrites dans la [partie 2](/fr/posts/nsx-federation-management-pack-install/).

## Le lab

La fédération tourne dans un lab VCF 9.1.1 imbriqué. Elle comprend un seul Global Manager, `nsx-gm-01a` (10.1.1.37, NSX 9.1.1), actif, à un seul nœud et sans Global Manager de secours. Deux emplacements en NSX 9.1.1 y sont rattachés : `site-a-wld01` (Local Manager `nsx-wld01-a`, adresse IP virtuelle du cluster 10.1.1.24) et `site-a-wld03` (Local Manager `nsx-shared-a`, adresse IP virtuelle du cluster 10.1.1.80). Chacun dispose d'un cluster Edge à deux nœuds et de son propre VLAN RTEP. Sur le Global Manager sont définis une passerelle Tier-0 en mode actif-actif (`fed-t0`, quatre sessions BGP vers le routeur du lab), trois passerelles Tier-1, cinq segments (dont le segment étendu `fed-stretched`) et une stratégie de pare-feu (`fed-demo`).

Les deux emplacements appartiennent à la même instance VCF : c'est un raccourci de lab, à ne pas reproduire dans une architecture de production. Le lab compte quatre Local Managers NSX en tout, dont deux font partie de la fédération.

La page « Location Manager » du Global Manager montre la fédération :

![La page « Location Manager » du Global Manager : un Global Manager actif, aucun Global Manager de secours, deux emplacements avec leur latence et leur état de synchronisation, et un bandeau signalant que les sauvegardes ne sont pas configurées](/images/nsx-federation-mp/gm-location-manager.webp)

## Étape 1 : vérifier ce que VCF Operations collecte déjà

Un pack qui referait le travail de l'intégration NSX native doublerait le nombre d'objets et déclencherait deux alertes pour une même panne. La première étape consiste donc à inventorier, par l'API de VCF Operations, ce qui est déjà collecté.

```bash
OPS=ops-a.site-a.vcf.lab
TOKEN=$(curl -sk https://$OPS/suite-api/api/auth/token/acquire \
  -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d '{"username":"admin","password":"'"$OPS_PW"'","authSource":"local"}' | jq -r .token)

# version, collectors, NSX adapter instances
curl -sk -H "Authorization: OpsToken $TOKEN" -H 'Accept: application/json' \
  https://$OPS/suite-api/api/versions/current
curl -sk -H "Authorization: OpsToken $TOKEN" -H 'Accept: application/json' \
  https://$OPS/suite-api/api/collectors
curl -sk -H "Authorization: OpsToken $TOKEN" -H 'Accept: application/json' \
  "https://$OPS/suite-api/api/adapters?adapterKindKey=NSXTAdapter"
```

Résultat :

- Version : VCF Operations 9.1.1.
- Collecteur (proxy cloud) : `opscollector-01a`, de type `UNIFIED_CLOUD_PROXY`, à l'état `UP`. Ce point compte pour la construction, car un adaptateur écrit avec l'Integration SDK s'exécute dans un conteneur sur un proxy cloud, jamais sur le nœud d'analyse.
- Intégration NSX : le type d'adaptateur `NSXTAdapter` compte 50 types d'objets et 4 instances, une par Local Manager NSX du lab, y compris les deux qui ne font pas partie de la fédération. Aucun de ces 50 types ne correspond à un Global Manager, à un emplacement ou à un tunnel RTEP, et aucune instance ne pointe vers le Global Manager.
- Objets de la fédération : l'instance de `nsx-wld01-a` liste le cluster Edge `fed-ec-a` et le pool d'adresses IP RTEP, car ce sont des objets locaux. La recherche des objets globaux (`fed-t0`, `fed-stretched`) dans son inventaire n'a rien donné.
- Constat annexe : l'instance de `nsx-shared-a`, le Local Manager de l'emplacement `site-a-wld03`, existe, mais ne contient qu'un seul objet, elle-même. Elle ne collecte pas encore de données, et la vue native de cet emplacement est donc vide. Le pack n'en est pas affecté, car il interroge lui-même les managers.

L'intégration native couvre les composants propres à chaque emplacement et s'arrête à la frontière de la fédération. Au pack de prendre en charge la couche fédération et de la relier au reste.

## Étape 2 : trouver les appels d'API qui donnent l'état de la fédération

Tous les appels ci-dessous sont des GET, exécutés directement sur les managers du lab. Le Global Manager publie son arborescence Policy sous `/global-manager/api/v1/global-infra` ; un Local Manager expose les objets qu'il a reçus sous `/policy/api/v1/global-infra`.

**La fédération est-elle connectée ?** C'est le Global Manager qu'il faut interroger :

```bash
curl -sk -u "$NSX_USER:$NSX_PW" https://nsx-gm-01a.site-a.vcf.lab/api/v1/sites/status
```

```json
{"remote_connections": [
  {"site_name": "site-a-wld01", "stubs": [{"address": "10.1.1.25", "connection_up": true}]},
  {"site_name": "site-a-wld03", "stubs": [{"address": "10.1.1.81", "connection_up": true}]}],
 "active_standby_sync_statuses": []}
```

Les adresses renvoyées, 10.1.1.25 et 10.1.1.81, sont les adresses de nœud des deux Local Managers. Le même appel sur un Local Manager renvoie ce que celui-ci voit du Global Manager et de l'autre emplacement. Lancé sur chaque manager, il permet de distinguer « le Global Manager a perdu un emplacement » de « deux emplacements ne se voient plus ».

**Un objet étendu est-il réalisé partout ?** Un appel par objet, sur le Global Manager :

```bash
curl -sk -u "$NSX_USER:$NSX_PW" \
 "https://nsx-gm-01a.site-a.vcf.lab/global-manager/api/v1/global-infra/realized-state/status?intent_path=/global-infra/segments/fed-stretched"
```

La réponse contient un état consolidé et un état par emplacement (`SUCCESS` pour les deux ici). L'appel complémentaire `/global-infra/span?intent_path=...` liste les emplacements que l'objet est censé atteindre, c'est-à-dire sa portée.

**Les tunnels entre emplacements sont-ils actifs ?** L'information se trouve sur les Local Managers, par cluster Edge et par nœud Edge :

```bash
curl -sk -u "$NSX_USER:$NSX_PW" \
 https://nsx-wld01-a.site-a.vcf.lab/api/v1/edge-clusters/<edge-cluster-id>/inter-site/status
curl -sk -u "$NSX_USER:$NSX_PW" \
 https://nsx-wld01-a.site-a.vcf.lab/api/v1/transport-nodes/<edge-node-id>/inter-site/statistics
```

Le premier appel renvoie les sessions BGP intersites de chaque nœud Edge (2 au total, 2 établies sur `edge-wld01-01a`), avec les adresses RTEP source et voisine. Le second renvoie, pour chaque tunnel, les octets, les paquets, les paquets rejetés et les paquets bloqués.

Le tableau suivant récapitule les appels vérifiés :

| Question | Manager | Chemin d'API |
| --- | --- | --- |
| Rôle et cluster du Global Manager | Global | `/api/v1/cluster/status`, `/global-infra/global-managers` |
| Liste des emplacements, versions, adresses RTEP | Global | `/api/v1/sites`, `/global-infra/sites`, `/global-infra/federation-config` |
| Connexion entre managers | Les deux | `/api/v1/sites/status` |
| État de la synchronisation complète | Local | `/policy/api/v1/infra/full-sync-states` |
| Portée et réalisation d'un objet étendu | Global | `/global-infra/span`, `/global-infra/realized-state/status` |
| BGP intersite et statistiques des tunnels RTEP | Local | `/api/v1/edge-clusters/{id}/inter-site/status`, `/api/v1/transport-nodes/{id}/inter-site/bgp/summary`, `.../inter-site/statistics` |
| Voisins BGP des liaisons montantes du Tier-0 | Local | `/policy/api/v1/global-infra/tier-0s/{id}/locale-services/{location}/bgp/neighbors/status` |
| Statistiques des règles d'une stratégie de sécurité globale | Local | `/policy/api/v1/global-infra/domains/default/security-policies/{id}/statistics` |
| Sauvegarde, certificats, alarmes | Les deux | `/api/v1/cluster/backups/overview`, `/api/v1/trust-management/certificates`, `/api/v1/alarms` |

Deux valeurs sont restées introuvables :

- **La latence entre managers.** La page « Location Manager » l'affiche (3 ms et 2 ms sur la capture ci-dessus) et NSX déclenche une alarme sur cette valeur (KB 345822) : NSX la mesure donc. Aucun des 14 chemins essayés ne l'a renvoyée. La prochaine tentative consistera à capturer l'appel qu'émet la page elle-même.
- **Le pare-feu appliqué sur l'hôte.** Le lab en offre un cas concret : la règle de `fed-demo` est signalée comme réalisée sur les deux Local Managers, alors que les hôtes répondent `firewall: disabled` à la commande `nsxcli -c get firewall status` et qu'aucun port de VM ne porte de filtre. Aucune des API de manager explorées jusqu'ici n'expose cet état de l'hôte.

Deux particularités du lab donneront chacune lieu à une alerte : aucune sauvegarde n'est configurée sur le Global Manager, et celui-ci fonctionne seul, sans Global Manager de secours.

## Étape 3 : le modèle d'objets

Un management pack repose d'abord sur un modèle d'objets : quels éléments existent, comment ils sont identifiés et lequel est le parent de l'autre. La première ébauche compte 12 types répartis en 4 chaînes, comme le montre le schéma ci-dessous. Elle modélise encore elle-même les nœuds Edge, les passerelles et les segments ; l'étape 5 revient sur ce point.

![Modèle d'objets : du Global Manager au nœud de manager ; de l'emplacement au cluster Edge, au nœud Edge puis au tunnel RTEP ; de la passerelle Tier-0 à la passerelle Tier-1 puis au segment, avec le voisin BGP sous le Tier-0 ; de la stratégie globale à la règle](/images/nsx-federation-mp/object-model.svg)

Les identifiants proviennent de NSX, jamais d'une adresse de manager : identifiant de fédération et identifiant de site pour les managers et les emplacements, emplacement et nom du nœud pour les nœuds Edge, adresses RTEP et adresses des pairs pour les tunnels et les sessions BGP, chemin de stratégie globale pour les passerelles, les segments et les stratégies. Un objet identifié par une adresse IP ou par un FQDN devient un second objet, sans historique, dès qu'une adresse IP virtuelle change ou qu'un Global Manager bascule.

## Étape 4 : métriques et alertes

Chaque type d'objet reçoit une courte liste de métriques et de propriétés. Les métriques sont des valeurs numériques conservées dans le temps ; les propriétés décrivent l'état et la configuration.

| Type d'objet | Métriques | Propriétés |
| --- | --- | --- |
| Global Manager | nœuds en ligne et hors ligne, alarmes ouvertes, heures écoulées depuis la dernière sauvegarde, jours restants avant l'expiration du certificat | mode (actif ou de secours), état du cluster, version |
| Emplacement | état de la connexion vu par chaque pair, écart de version avec le Global Manager | adresse du Local Manager, adresses RTEP, phase de la synchronisation complète |
| Cluster Edge, nœud Edge | sessions BGP intersites, au total et établies | état intersite, adresse et VLAN RTEP |
| Tunnel RTEP | octets et paquets entrants et sortants, paquets rejetés, paquets bloqués | RTEP local et distant, emplacement distant |
| Tier-0, Tier-1, segment | emplacements dans la portée, réalisés, en erreur | état par emplacement, mode HA, sous-réseaux |
| Voisin BGP | préfixes reçus et annoncés, durée d'établissement en secondes, coupures de connexion | état, AS distant |
| Stratégie de sécurité globale, règle | nombre de règles ; correspondances, paquets et octets par emplacement | action, activation, état |

La première version s'en tient à une courte liste d'alertes :

- perte du Global Manager par un emplacement
- perte de visibilité entre deux emplacements
- chute d'une session BGP intersite ou d'un tunnel RTEP
- objet étendu non réalisé sur l'un des emplacements de sa portée
- échec de la synchronisation
- chute d'un voisin BGP de liaison montante
- sauvegarde du Global Manager absente ou trop ancienne
- absence de Global Manager de secours
- version d'un emplacement différente de celle du Global Manager

Deux d'entre elles se déclencheraient dans le lab dès le premier jour : la sauvegarde absente et l'absence de Global Manager de secours. Le Global Manager et les emplacements exécutent la même release, 9.1.1, ce qui ne déclenche aucune alerte.

## Étape 5 : éprouver la conception à l'échelle d'une grande entreprise

Le lab compte deux emplacements et un Global Manager à un seul nœud. L'objectif est un pack exploitable dans une grande entreprise, c'est-à-dire avec des clusters Global Manager à trois nœuds, l'un actif et l'autre de secours sur des sites distincts, de nombreux emplacements, des milliers d'objets étendus et plusieurs équipes. La conception a été réexaminée sous cet angle, et huit points ont modifié le plan.

1. **Une seule adresse de Global Manager est un défaut de conception.** Après un basculement, l'adaptateur pointerait vers un manager rétrogradé ou hors service, au moment précis où la supervision est nécessaire. Il lui faut l'adresse du Global Manager actif et celle du Global Manager de secours ; il doit repérer l'actif à chaque cycle et continuer à collecter sur les Local Managers quand aucun Global Manager ne répond. Deux managers qui se déclarent actifs en même temps déclenchent une alerte critique.
2. **L'état du Global Manager de secours entre dans la première version.** Pour une équipe dotée d'une procédure de reprise d'activité, la première question est la suivante : le secours est-il synchronisé, et un basculement peut-il être lancé ? La première ébauche du plan reléguait ce point en dernière position.
3. **Le pack ne doit plus faire doublon avec l'intégration native.** Les nœuds Edge, les passerelles et les segments existent déjà pour chaque Local Manager. Le pack doit, autant que possible, rattacher ses métriques et ses relations à ces objets, et ne créer que les types propres à la fédération. Tel qu'il est construit, le pack relie les nœuds Edge et les clusters Edge aux objets natifs et conserve ses propres types ([partie 2](/fr/posts/nsx-federation-management-pack-install/)).
4. **Un objet par règle ne passe pas à l'échelle.** Des milliers de règles, à quatre compteurs chacune, sur vingt emplacements : la plupart des valeurs resteraient à zéro. Par défaut, le pack remonte des totaux par stratégie et ne crée d'objets règle que sur demande.
5. **Il faut un quota d'appels d'API.** Un appel de réalisation par objet étendu ne tient pas dans un cycle de 5 minutes à grande échelle, et NSX limite chaque client (100 requêtes par seconde et 40 requêtes simultanées par défaut). La collecte doit interroger les Local Managers en parallèle, avec des cycles plus lents pour l'inventaire que pour les états.
6. **Les avalanches d'alertes doivent être évitées dès la conception.** La perte d'un seul emplacement déclencherait l'alerte de l'emplacement, puis celles de chaque tunnel, de chaque session BGP et de chaque objet étendu. Les alertes doivent être regroupées sous leur cause première et tenir compte des fenêtres de maintenance.
7. **Le nombre de secondes écoulées depuis la dernière synchronisation complète n'est pas le bon indicateur.** La première ébauche prévoyait cette métrique. Une fédération saine fonctionne en synchronisation différentielle, et la synchronisation complète reste l'exception. Les indicateurs utiles sont l'état de la synchronisation, le remplissage des files d'attente et les échecs de synchronisation complète. Tel qu'il est construit, le pack conserve l'âge de la dernière synchronisation complète comme métrique, sans alerte, et déclenche des alertes sur l'état de la synchronisation et sur les échecs de synchronisation complète.
8. **Le pack doit se superviser lui-même.** Il expose sa durée de collecte, son nombre d'appels d'API et la dernière collecte réussie par emplacement, pour qu'une absence de données ne soit jamais interprétée comme un état sain.

Une réserve : vingt emplacements, c'est peut-être plus que ce que NSX 9.1 Federation prend en charge. Ce point est à vérifier dans les valeurs maximales de configuration avant de fixer les objectifs d'échelle.

## Étape 6 : le mode de construction

VCF Operations offre deux manières de construire un pack : le Management Pack Builder, sans code, et l'Integration SDK. Le SDK a été retenu parce qu'une même instance d'adaptateur doit interroger plusieurs managers, découvrir les Local Managers à partir du Global Manager et suivre le basculement d'un Global Manager. Les valeurs dérivées, comme un écart de version ou un état d'emplacement qui ne provoque pas d'avalanche d'alertes, exigent elles aussi du code.

Ce choix alourdit la construction. Un adaptateur SDK est une image de conteneur : il faut donc un outillage Python et Docker pour le construire, et le proxy cloud récupère l'image dans un registre de conteneurs qu'il doit pouvoir joindre.

## Points en suspens à la fin de la partie 1

- Installer l'outillage du SDK et choisir le registre.
- Décider jusqu'où le pack étend les objets NSX natifs.
- Construire le cœur de la collecte sur le lab, avec un simulateur pour les cas que le lab ne peut pas reproduire : un Global Manager de secours, un basculement, vingt emplacements.
- Trouver l'API de latence et un moyen de lire l'état du pare-feu sur les hôtes.

La [partie 2](/fr/posts/nsx-federation-management-pack-install/) traite de la construction, de l'installation et de ce que le pack supervise.

## Sources

- Lab : VCF 9.1.1 imbriqué, NSX 9.1.1 (Local Managers et Global Manager), VCF Operations 9.1.1. Tous les appels d'API de cet article ont été exécutés le 17 novembre 2026.
- [VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction)
- KB Broadcom [324244](https://knowledge.broadcom.com/external/article/324244/split-brain-condition-after-a-global-man.html) : split-brain après le basculement d'un Global Manager
- KB Broadcom [378126](https://knowledge.broadcom.com/external/article/378126/nsx-apis-throws-error-message-client-ex.html) : limites de débit appliquées aux clients de l'API NSX
- KB Broadcom [345822](https://knowledge.broadcom.com/external/article/345822/gm-to-lm-network-latency-threshold-excee.html) : alarme de latence entre Global Manager et Local Manager
