---
title: "Superviser NSX Federation dans VCF Operations, partie 1 : concevoir le management pack"
date: 2026-11-17
description: "VCF Operations 9.1.1 dispose d'une instance d'adaptateur pour chaque Local Manager NSX, mais ne voit rien d'une fédération NSX : ni Global Manager, ni synchronisation entre emplacements, ni tunnels RTEP, ni objets étendus. Cet article décrit la conception d'un management pack sur mesure dans un lab : ce que couvre l'intégration native, les appels d'API NSX qui donnent l'état de la fédération, le modèle d'objets, les métriques, et ce qu'a changé la relecture de la conception à l'échelle d'une grande entreprise."
summary: "Une fédération NSX 9.1 à deux emplacements tourne dans le lab, et VCF Operations n'en montre rien. Cette première partie précède toute ligne de code : elle vérifie l'intégration NSX native, explore les API du Global Manager et des Local Managers, puis fixe un premier modèle à 12 types d'objets, la liste des métriques et des propriétés, et les changements imposés par une relecture à l'échelle d'une grande entreprise."
tags: ["VCF 9.1.1", "NSX Federation", "VCF Operations", "Management pack", "Integration SDK", "Lab"]
categories: ["Notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-federation-mp/gm-location-manager.webp"]
params:
  seoDescription: "Concevoir un management pack VCF Operations pour NSX 9.1 Federation : appels d'API, modèle d'objets, métriques, et les changements imposés par une relecture à l'échelle d'une grande entreprise."
---

VCF Operations 9.1.1 dispose d'une instance d'adaptateur pour chaque Local Manager NSX du lab, mais ne montre rien de la fédération qui relie deux d'entre eux. Le Global Manager, l'état de synchronisation entre les emplacements (les « Locations » de NSX Federation), les tunnels RTEP et les passerelles étendues n'y ont ni objet, ni métrique, ni alerte. Cet article décrit la conception d'un management pack qui les ajoute. La construction et l'installation sont décrites dans la [partie 2](/fr/posts/nsx-federation-management-pack-install/).

## Le lab

La fédération tourne dans un lab VCF 9.1.1. Elle comprend un seul Global Manager, `nsx-gm-01a` (10.1.1.37, NSX 9.1.1), actif, à un seul nœud et sans Standby Global Manager. Deux emplacements en NSX 9.1.1 y sont rattachés : `site-a-wld01` (Local Manager `nsx-wld01-a`, adresse IP virtuelle du cluster 10.1.1.24) et `site-a-wld03` (Local Manager `nsx-wld03-a`, adresse IP virtuelle du cluster 10.1.1.80). Chacun dispose d'un cluster Edge à deux nœuds et de son propre VLAN RTEP. Sur le Global Manager sont définis une passerelle Tier-0 en mode actif-actif (`fed-t0`, quatre sessions BGP vers le routeur du lab), trois passerelles Tier-1, cinq segments (dont le segment étendu `fed-stretched`) et une stratégie de pare-feu (`fed-demo`). Un Standby Global Manager, `nsx-gm-01b`, a été ajouté plus tard, avant l'installation, et le pack est testé avec les deux Global Managers ([partie 2](/fr/posts/nsx-federation-management-pack-install/)).

Les deux emplacements appartiennent à la même instance VCF : c'est un raccourci de lab, à ne pas reproduire dans une architecture de production. Le lab compte quatre Local Managers NSX en tout, dont deux font partie de la fédération.

La page « Location Manager » du Global Manager montre la fédération :

![La page « Location Manager » du Global Manager : un Global Manager actif, aucun Standby Global Manager, deux emplacements avec leur latence et leur état de synchronisation, et un bandeau signalant que les sauvegardes ne sont pas configurées](/images/nsx-federation-mp/gm-location-manager.webp)

## Étape 1 : vérifier ce que VCF Operations collecte déjà

Un pack qui referait le travail de l'intégration NSX native doublerait le nombre d'objets et déclencherait deux alertes pour une même panne. La première étape consiste donc à inventorier, par l'API de VCF Operations, ce qui est déjà collecté.

*À exécuter sur : le jumphost, vers l'API de VCF Operations.*

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
- Constat annexe : l'instance de `nsx-wld03-a`, le Local Manager de l'emplacement `site-a-wld03`, existe, mais ne contient qu'un seul objet, elle-même. Elle ne collecte pas encore de données, et la vue native de cet emplacement est donc vide. Le pack n'en est pas affecté, car il interroge lui-même les managers.

L'intégration native couvre les composants propres à chaque emplacement et s'arrête à la frontière de la fédération. Au pack de prendre en charge la couche fédération et de la relier au reste.

## Étape 2 : trouver les appels d'API qui donnent l'état de la fédération

Tous les appels ci-dessous sont des GET, envoyés aux managers du lab. Le Global Manager publie son arborescence Policy sous `/global-manager/api/v1/global-infra` ; un Local Manager expose les objets qu'il a reçus sous `/policy/api/v1/global-infra`.

**La fédération est-elle connectée ?** C'est le Global Manager qu'il faut interroger :

*À exécuter sur : le jumphost, vers l'API du Global Manager actif.*

```bash
curl -sk -u "$NSX_USER:$NSX_PW" https://nsx-gm-01a.site-a.vcf.lab/api/v1/sites/status
```

*Sortie : la réponse du Global Manager à l'appel ci-dessus.*

```json
{"remote_connections": [
  {"site_name": "site-a-wld01", "stubs": [{"address": "10.1.1.25", "connection_up": true}]},
  {"site_name": "site-a-wld03", "stubs": [{"address": "10.1.1.81", "connection_up": true}]}],
 "active_standby_sync_statuses": []}
```

Les adresses renvoyées, 10.1.1.25 et 10.1.1.81, sont les adresses de nœud des deux Local Managers. Le même appel sur un Local Manager renvoie ce que celui-ci voit du Global Manager et de l'autre emplacement. Lancé sur chaque manager, il permet de distinguer « le Global Manager a perdu un emplacement » de « deux emplacements ne se voient plus ».

**Un objet étendu est-il réalisé partout ?** Un appel par objet, sur le Global Manager :

*À exécuter sur : le jumphost, vers l'API du Global Manager actif.*

```bash
curl -sk -u "$NSX_USER:$NSX_PW" \
 "https://nsx-gm-01a.site-a.vcf.lab/global-manager/api/v1/global-infra/realized-state/status?intent_path=/global-infra/segments/fed-stretched"
```

La réponse contient un état consolidé et un état par emplacement (`SUCCESS` pour les deux ici). L'appel complémentaire `/global-infra/span?intent_path=...` liste les emplacements que l'objet est censé atteindre, c'est-à-dire sa portée.

**Les tunnels entre emplacements sont-ils actifs ?** L'information se trouve sur les Local Managers, par cluster Edge et par nœud Edge :

*À exécuter sur : le jumphost, vers l'API du Local Manager `nsx-wld01-a`.*

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

## Étape 3 : le modèle d'objets

Un management pack repose d'abord sur un modèle d'objets : quels éléments existent, comment ils sont identifiés et lequel est le parent de l'autre. La première ébauche compte 12 types répartis en 4 chaînes, comme le montre le schéma ci-dessous. Elle modélise encore elle-même les nœuds Edge, les passerelles et les segments ; l'étape 5 revient sur ce point. Tel qu'il est construit, le pack compte 15 types ([partie 2](/fr/posts/nsx-federation-management-pack-install/)).

![Modèle d'objets : du Global Manager au nœud de manager ; de l'emplacement au cluster Edge, au nœud Edge puis au tunnel RTEP ; de la passerelle Tier-0 à la passerelle Tier-1 puis au segment, avec le voisin BGP sous le Tier-0 ; de la stratégie globale à la règle](/images/nsx-federation-mp/object-model.fr.svg?v=2)

Les identifiants proviennent de NSX, jamais d'une adresse de manager : identifiant de fédération et identifiant de site pour les managers et les emplacements, emplacement et nom du nœud pour les nœuds Edge, adresses RTEP et adresses des pairs pour les tunnels et les sessions BGP, chemin de stratégie globale pour les passerelles, les segments et les stratégies. Un objet identifié par une adresse IP ou par un FQDN devient un second objet, sans historique, dès qu'une adresse IP virtuelle change ou qu'un Global Manager bascule. Tel qu'il est construit, le pack identifie un canal de synchronisation par les noms que NSX donne à ses deux extrémités, un emplacement ou un Global Manager, comme dans `site-a-wld01 -> gm-site-a`.

## Étape 4 : métriques et propriétés

Chaque type d'objet reçoit une courte liste de métriques et de propriétés. Les métriques sont des valeurs numériques conservées dans le temps ; les propriétés décrivent l'état et la configuration.

| Type d'objet | Métriques | Propriétés |
| --- | --- | --- |
| Global Manager | nœuds en ligne et hors ligne, alarmes ouvertes, heures écoulées depuis la dernière sauvegarde, jours restants avant l'expiration du certificat | mode (actif ou standby), état du cluster, version |
| Emplacement | état de la connexion vu par chaque pair, écart de version avec le Global Manager | adresse du Local Manager, adresses RTEP, phase de la synchronisation complète |
| Cluster Edge, nœud Edge | sessions BGP intersites, au total et établies | état intersite, adresse et VLAN RTEP |
| Tunnel RTEP | octets et paquets entrants et sortants, paquets rejetés, paquets bloqués | RTEP local et distant, emplacement distant |
| Tier-0, Tier-1, segment | emplacements dans la portée, réalisés, en erreur | état par emplacement, mode HA, sous-réseaux |
| Voisin BGP | préfixes reçus et annoncés, durée d'établissement en secondes, coupures de connexion | état, AS distant |
| Stratégie de sécurité globale, règle | nombre de règles ; correspondances, paquets et octets par emplacement | action, activation, état |

## Étape 5 : éprouver la conception à l'échelle d'une grande entreprise

La conception a ensuite été relue à l'échelle d'une grande entreprise : clusters Global Manager à trois nœuds sur un site actif et un site de secours, de nombreux emplacements et des milliers d'objets étendus. Cette relecture a changé la collecte. L'adaptateur suit le Global Manager actif et rend compte de l'état du standby dès la première version, les règles sont comptées par stratégie, chaque Local Manager a son quota d'appels d'API, et la santé de la synchronisation se lit dans son état et dans les échecs de synchronisation complète, au lieu de l'âge de la dernière. Elle a aussi changé les alertes : la perte d'un emplacement déclenche une seule alerte au lieu d'une avalanche, et le pack rend compte de sa propre collecte, pour qu'une absence de données ne soit jamais lue comme un état sain (la [partie 2](/fr/posts/nsx-federation-management-pack-install/) montre comment chaque point a été construit).

La relecture demandait aussi de réutiliser les objets Edge natifs. Tel qu'il est construit, le pack conserve ses propres objets cluster Edge, nœud Edge, passerelle et segment, et relie ses objets Edge aux objets natifs, car l'intégration native ne collecte ni les objets globaux ni un Local Manager qu'elle ne supervise pas.

## Étape 6 : le mode de construction

VCF Operations offre deux manières de construire un pack : le Management Pack Builder, sans code, et l'Integration SDK. Le SDK a été retenu parce qu'une même instance d'adaptateur doit interroger plusieurs managers, découvrir les Local Managers à partir du Global Manager et suivre le basculement d'un Global Manager. Les valeurs dérivées, comme un écart de version ou un état d'emplacement qui ne provoque pas d'avalanche d'alertes, exigent elles aussi du code.

Ce choix alourdit la construction. Un adaptateur SDK est une image de conteneur : il faut donc un outillage Python et Docker pour le construire, et le proxy cloud récupère l'image dans un registre de conteneurs qu'il doit pouvoir joindre.

## L'Integration SDK en bref

L'[Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction) est un ensemble d'outils et une bibliothèque Python pour écrire un management pack en code. La bibliothèque modélise les objets que le pack envoie à VCF Operations, avec leurs métriques, propriétés, événements et relations. Chaque adaptateur est une image de conteneur dotée d'un serveur HTTP qui appelle le code du pack, et il s'exécute sur un proxy cloud. Trois commandes couvrent le travail : `mp-init` crée le projet, `mp-test` exécute l'adaptateur en local face à un VCF Operations simulé, et `mp-build` assemble l'adaptateur, ses métadonnées et son contenu dans un fichier `.pak`.

## Les limites du Management Pack Builder

Le Management Pack Builder est la voie sans code : un pack se conçoit dans des écrans de VCF Operations, « without writing a single line of code » ([blog VMware](https://blogs.vmware.com/cloud-foundation/2026/02/04/extending-visibility-with-vmware-cloud-foundations-management-pack-builder/)). Pour ce pack, trois de ses limites pèsent plus lourd que le coût de construction du SDK.

- **Pas de code.** Détecter le Global Manager actif à chaque cycle, vérifier les empreintes des certificats, calculer un écart de version ou un état d'emplacement qui ne provoque pas d'avalanche d'alertes : tout cela demande une logique que les écrans de conception ne contiennent pas.
- **La rupture de version.** Après une mise à niveau de VCF Operations en 9.1.1, les packs personnalisés construits avec une version antérieure du builder cessent de collecter, et leurs objets affichent des métriques, des propriétés et des alertes périmées ou absentes. La correction se fait pack par pack : ouvrir chaque conception dans le builder mis à jour, y apporter une petite modification pour que le builder enregistre un changement, lancer la mise à niveau à l'étape d'installation et vérifier que les instances d'adaptateur reviennent à l'état « Data Receiving » (KB Broadcom [454434](https://knowledge.broadcom.com/external/article/454434)). Certains packs de Broadcom ont eux aussi eu besoin de leur version 9.1.1 après la même mise à niveau (KB [454432](https://knowledge.broadcom.com/external/article/454432)). Un pack du builder se répare dans le builder, une conception après l'autre ; un pack SDK se reconstruit à partir de son propre code source.
- **Événements et alertes.** Le builder livré avec VCF 9.0 a abandonné l'association directe entre événements collectés et alertes. Un employé de Broadcom a répondu sur le [forum de la communauté](https://community.broadcom.com/vmware-cloud-foundation/discussion/vcf-ops-v9-built-in-pack-builder-is-inferior) que VCF Operations dispose d'autres moyens de déclencher des alertes à partir d'événements et qu'aucun retour de cette association n'est prévu, tout en indiquant qu'il en parlerait à l'équipe.

## Points en suspens à la fin de la partie 1

- Installer l'outillage du SDK et choisir le registre.
- Décider jusqu'où le pack étend les objets NSX natifs.
- Construire le cœur de la collecte sur le lab, avec un simulateur pour les cas que le lab ne peut pas reproduire : un Standby Global Manager, un basculement, vingt emplacements.
- Trouver l'appel d'API derrière la latence qu'affiche la page « Location Manager ».

La [partie 2](/fr/posts/nsx-federation-management-pack-install/) traite de la construction, de l'installation et de ce que le pack supervise.

## Sources

- Lab : VCF 9.1.1, NSX 9.1.1 (Local Managers et Global Manager), VCF Operations 9.1.1. Tous les appels d'API de cet article ont été exécutés le 17 novembre 2026.
- [VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction)
- KB Broadcom [454434](https://knowledge.broadcom.com/external/article/454434) : les packs construits avec le Management Pack Builder ne collectent plus après la mise à niveau vers VCF Operations 9.1.1
- KB Broadcom [454432](https://knowledge.broadcom.com/external/article/454432) : des management packs ne collectent plus après la mise à niveau vers VCF Operations 9.1.1
- [Blog VMware : étendre la visibilité avec le Management Pack Builder](https://blogs.vmware.com/cloud-foundation/2026/02/04/extending-visibility-with-vmware-cloud-foundations-management-pack-builder/)
- [Communauté Broadcom : le pack builder intégré à VCF Operations 9](https://community.broadcom.com/vmware-cloud-foundation/discussion/vcf-ops-v9-built-in-pack-builder-is-inferior)
- KB Broadcom [324244](https://knowledge.broadcom.com/external/article/324244/split-brain-condition-after-a-global-man.html) : split-brain après le basculement d'un Global Manager
- KB Broadcom [378126](https://knowledge.broadcom.com/external/article/378126/nsx-apis-throws-error-message-client-ex.html) : limites de débit appliquées aux clients de l'API NSX
- KB Broadcom [345822](https://knowledge.broadcom.com/external/article/345822/gm-to-lm-network-latency-threshold-excee.html) : alarme de latence entre Global Manager et Local Manager
