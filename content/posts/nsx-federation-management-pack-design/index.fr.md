---
title: "Superviser NSX Federation dans VCF Operations, partie 1 : concevoir le management pack"
date: 2026-11-17
description: "VCF Operations 9.1.1 supervise chaque Local Manager NSX mais n'a aucune vue d'une NSX Federation : ni Global Manager, ni synchronisation entre locations, ni tunnels RTEP, ni objets étendus. Ce billet documente la conception d'un management pack sur mesure dans un lab Holodeck : ce que couvre l'intégration native, quels appels d'API NSX portent l'état de la fédération, le modèle d'objets, les alertes, et ce qu'une revue indépendante a changé."
summary: "Une NSX Federation 9.1 à deux locations tourne dans le lab et VCF Operations n'en montre rien. Partie 1 de la construction du management pack : vérification de l'intégration NSX native, exploration des API du Global Manager et des Local Managers, un modèle de 12 types d'objets, la liste des métriques et des alertes, et les huit changements imposés par une revue indépendante avant la première ligne de code."
tags: ["VCF 9.1.1", "NSX Federation", "VCF Operations", "Management pack", "Integration SDK", "Holodeck"]
categories: ["Notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-federation-mp/object-model.svg"]
params:
  seoDescription: "Concevoir un management pack VCF Operations pour NSX 9.1 Federation : appels d'API, modèle d'objets, alertes et changements imposés par une revue indépendante."
---

VCF Operations 9.1.1 supervise chaque Local Manager NSX du lab et ne montre rien de la fédération qui les relie. Le Global Manager, l'état de synchronisation entre les locations, les tunnels RTEP et les gateways étendues n'ont ni objet, ni métrique, ni alerte. Ce billet documente la conception d'un management pack qui les ajoute. Aucun code d'adaptateur n'existe encore. La partie 1 s'arrête là où la construction commence.

## Le lab

La fédération tourne sur un Holodeck VCF 9.1.1 imbriqué : un Global Manager, `nsx-gm-01a` (10.1.1.37, un seul nœud actif, pas de standby, NSX 9.1.1.0100), et deux locations en NSX 9.1.1.0, `site-a-wld01` (Local Manager 10.1.1.24) et `site-a-wld03` (Local Manager 10.1.1.80), chacune avec un edge cluster de deux nœuds et son propre VLAN RTEP. Le Global Manager porte un Tier-0 actif-actif (`fed-t0`, quatre sessions BGP vers le routeur du lab), trois gateways Tier-1, cinq segments dont le segment étendu `fed-stretched`, et une politique de pare-feu, `fed-demo`. Les deux locations se trouvent dans une seule instance VCF. C'est un raccourci de lab, à ne pas prendre pour un design de référence.

![La page Location Manager du Global Manager : un Global Manager actif, pas de standby, deux locations avec leur latence et leur état de synchronisation, et un bandeau indiquant que les sauvegardes ne sont pas configurées](/images/nsx-federation-mp/gm-location-manager.webp)

La page NSX ci-dessus répond déjà aux questions d'un exploitant : le Global Manager est-il disponible, les locations sont-elles synchronisées, quelle latence les sépare. Le pack doit donner les mêmes réponses, avec l'historique et les alertes, dans l'outil qui supervise le reste de la plateforme.

## Étape 1 : vérifier ce que VCF Operations collecte déjà

Un pack qui recopie l'intégration NSX native double le nombre d'objets et lève deux alertes pour une seule panne. La première étape est un inventaire de l'existant, par l'API de VCF Operations.

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

Le résultat :

- **Version** : VCF Operations 9.1.1.0, build 25795063.
- **Collecteur** : `opscollector-01a`, type `UNIFIED_CLOUD_PROXY`, état `UP`. Ce point compte pour la construction, car les adaptateurs écrits avec l'Integration SDK s'exécutent en conteneurs sur un collecteur et jamais sur le nœud d'analyse.
- **Intégration NSX** : type d'adaptateur `NSXTAdapter`, avec 50 types d'objets et quatre instances, une par Local Manager NSX du lab, fédéré ou non. Aucun des 50 types n'est un Global Manager, une location ou un tunnel RTEP, et aucune instance ne pointe vers le Global Manager.
- **Objets de la fédération** : l'instance de `nsx-wld01` liste l'edge cluster `fed-ec-a` et le pool d'IP RTEP, parce que ce sont des objets locaux. Une recherche des objets globaux (`fed-t0`, `fed-stretched`) dans son inventaire n'a rien renvoyé.
- **Un constat annexe** : l'instance de `nsx-shared-a`, le Local Manager de la location `site-a-wld03`, existe mais ne contient qu'un objet, elle-même. Elle ne collecte pas encore.

L'intégration native couvre la plomberie de chaque location et s'arrête à la frontière de la fédération. Le pack doit prendre en charge la couche fédération et se relier au reste.

## Étape 2 : trouver les appels d'API qui portent l'état de la fédération

Chaque appel ci-dessous est un GET et a été exécuté sur les managers en service. Le Global Manager expose son arbre de politiques sous `/global-manager/api/v1/global-infra` ; un Local Manager expose les objets qu'il a reçus sous `/policy/api/v1/global-infra`.

**La fédération est-elle connectée ?** Question posée au Global Manager :

```bash
curl -sk -u "$NSX_USER:$NSX_PW" https://nsx-gm-01a.site-a.vcf.lab/api/v1/sites/status
```

```json
{"remote_connections": [
  {"site_name": "site-a-wld01", "stubs": [{"address": "10.1.1.25", "connection_up": true}]},
  {"site_name": "site-a-wld03", "stubs": [{"address": "10.1.1.81", "connection_up": true}]}],
 "active_standby_sync_statuses": []}
```

Le même appel sur un Local Manager renvoie sa vue du Global Manager et de l'autre location. Collecté sur chaque manager, il permet de distinguer « le Global Manager a perdu une location » de « deux locations se sont perdues entre elles ».

**Un objet étendu est-il réalisé partout ?** Un appel par objet, sur le Global Manager :

```bash
curl -sk -u "$NSX_USER:$NSX_PW" \
 "https://nsx-gm-01a.site-a.vcf.lab/global-manager/api/v1/global-infra/realized-state/status?intent_path=/global-infra/segments/fed-stretched"
```

La réponse contient un état consolidé et un état par location (`SUCCESS` pour les deux ici). L'appel associé `/global-infra/span?intent_path=...` liste les locations que l'objet est censé atteindre.

**Les tunnels entre locations sont-ils montés ?** L'information se trouve sur les Local Managers, par edge cluster et par nœud edge :

```bash
curl -sk -u "$NSX_USER:$NSX_PW" \
 https://nsx-wld01.site-a.vcf.lab/api/v1/edge-clusters/<edge-cluster-id>/inter-site/status
curl -sk -u "$NSX_USER:$NSX_PW" \
 https://nsx-wld01.site-a.vcf.lab/api/v1/transport-nodes/<edge-node-id>/inter-site/statistics
```

Le premier renvoie les sessions BGP inter-site de chaque nœud edge (2 au total, 2 établies sur `edge-wld01-01a`) avec les adresses RTEP source et voisine. Le second renvoie les octets, les paquets, les paquets perdus et les paquets bloqués par tunnel.

La carte complète, telle que vérifiée :

| Question | Manager | Chemin d'API |
| --- | --- | --- |
| Rôle et cluster du Global Manager | Global | `/api/v1/cluster/status`, `/global-infra/global-managers` |
| Liste des locations, versions, adresses RTEP | Global | `/api/v1/sites`, `/global-infra/sites`, `/global-infra/federation-config` |
| Connexion entre managers | Les deux | `/api/v1/sites/status` |
| État du full sync | Local | `/policy/api/v1/infra/full-sync-states` |
| Span et réalisation d'un objet étendu | Global | `/global-infra/span`, `/global-infra/realized-state/status` |
| BGP inter-site et statistiques des tunnels RTEP | Local | `/api/v1/edge-clusters/{id}/inter-site/status`, `/api/v1/transport-nodes/{id}/inter-site/bgp/summary`, `.../inter-site/statistics` |
| Voisins BGP des uplinks du Tier-0 | Local | `/policy/api/v1/global-infra/tier-0s/{id}/locale-services/{location}/bgp/neighbors/status` |
| Statistiques des règles d'une politique globale | Local | `/policy/api/v1/global-infra/domains/default/security-policies/{id}/statistics` |
| Sauvegarde, certificats, alarmes | Les deux | `/api/v1/cluster/backups/overview`, `/api/v1/trust-management/certificates`, `/api/v1/alarms` |

Deux valeurs sont restées introuvables :

- **La latence entre managers.** La page Location Manager l'affiche (3 ms et 2 ms dans la capture ci-dessus), donc NSX la mesure. Aucun des quatorze chemins essayés ne l'a renvoyée. La prochaine tentative consiste à capturer l'appel que la page fait elle-même.
- **Le pare-feu appliqué sur l'hôte.** Le lab en a un cas réel : la règle de `fed-demo` est déclarée réalisée sur les deux Local Managers, alors que les hôtes répondent `firewall: disabled` à `nsxcli -c get firewall status` et qu'aucun port de VM n'a de filtre. Aucune API de manager trouvée à ce stade n'expose cet état de l'hôte.

L'exploration a aussi montré deux faits sur le lab lui-même : le Global Manager n'a pas de sauvegarde configurée, et il tourne seul, sans standby. Les deux deviennent des alertes.

## Étape 3 : le modèle d'objets

Un management pack commence par un modèle d'objets : quels éléments existent, ce qui les identifie, et quel objet est le parent de quel autre. Le premier jet compte 12 types répartis en quatre chaînes.

![Modèle d'objets : Global Manager vers nœud de manager ; Location vers edge cluster vers nœud edge vers tunnel RTEP ; gateway Tier-0 vers gateway Tier-1 vers segment, avec le voisin BGP sous le Tier-0 ; politique globale vers règle](/images/nsx-federation-mp/object-model.svg)

Les identifiants viennent de NSX, jamais d'une adresse : l'identifiant de fédération et l'identifiant de site pour les managers et les locations, l'identifiant de transport node pour les edges, le chemin de politique globale pour les gateways, les segments et les politiques. Un objet identifié par une adresse IP ou un FQDN devient un second objet, sans historique, le jour où une IP virtuelle change ou qu'un Global Manager bascule.

## Étape 4 : métriques et alertes

Chaque type d'objet reçoit une liste courte. Les métriques sont des nombres conservés dans le temps ; les propriétés décrivent l'état et la configuration.

| Type d'objet | Métriques | Propriétés |
| --- | --- | --- |
| Global Manager | nœuds en ligne et hors ligne, alarmes ouvertes, heures depuis la dernière sauvegarde, jours avant l'expiration du certificat | mode (actif ou standby), état du cluster, version |
| Location | connexion vue depuis chaque pair, écart de version avec le Global Manager | adresse du Local Manager, adresses RTEP, étape du full sync |
| Edge cluster, nœud edge | sessions BGP inter-site, total et établies | état inter-site, adresse et VLAN RTEP |
| Tunnel RTEP | octets et paquets entrants et sortants, perdus, bloqués | RTEP local et distant, location distante |
| Tier-0, Tier-1, segment | locations dans le span, réalisées, en erreur | état par location, mode HA, sous-réseaux |
| Voisin BGP | préfixes reçus et annoncés, secondes depuis l'établissement, pertes de connexion | état, AS distant |
| Politique globale, règle | nombre de règles, compteurs de hits, de paquets et d'octets par location | action, activée, état |

La première version garde une liste d'alertes courte : une location a perdu son Global Manager, deux locations se sont perdues entre elles, une session BGP inter-site ou un tunnel RTEP est tombé, un objet étendu n'est pas réalisé quelque part dans son span, la synchronisation a échoué, un voisin BGP d'uplink est tombé, la sauvegarde du Global Manager est absente ou ancienne. Trois se déclencheraient dans le lab dès le premier jour : pas de sauvegarde, pas de Global Manager standby, et un écart de build entre le Global Manager (9.1.1.0100) et les locations (9.1.1.0).

## Étape 5 : une revue indépendante

Le lab a deux locations et un seul nœud Global Manager. La cible est un pack qu'une grande entreprise pourrait exploiter : des clusters Global Manager à trois nœuds, un actif et un standby sur des sites différents, de nombreuses locations, des milliers d'objets étendus, plusieurs équipes. Un second relecteur, un agent d'IA qui n'a reçu que le plan et rien du raisonnement qui y a mené, a été chargé d'attaquer la conception sur cette base. Huit points ont modifié le plan.

1. **Une seule adresse de Global Manager est un défaut de conception.** Après un switchover, l'adaptateur pointerait vers un manager rétrogradé ou mort au moment où il est nécessaire. Il lui faut l'adresse de l'actif et celle du standby, il doit détecter l'actif à chaque cycle, et il doit continuer à collecter sur les Local Managers quand aucun Global Manager ne répond. Deux managers qui revendiquent tous deux le rôle actif deviennent une alerte critique.
2. **L'état de préparation du standby passe dans la première version.** Pour une équipe qui a un runbook de reprise, « le standby est-il synchronisé et un switchover peut-il démarrer » est la première question. Le premier jet la plaçait en dernier.
3. **Ne plus dupliquer l'intégration native.** Les nœuds edge, les gateways et les segments existent déjà pour chaque Local Manager. Le pack doit rattacher ses métriques et ses relations à ces objets quand c'est possible, et ne posséder que les types propres à la fédération.
4. **Les règles en tant qu'objets ne passent pas à l'échelle.** Des milliers de règles à quatre compteurs chacune, sur vingt locations, donnent surtout des zéros. Le pack remonte par défaut des totaux par politique et ne crée des objets règle que sur demande.
5. **Budgéter les appels d'API.** Un appel de réalisation par objet étendu ne tient pas dans un cycle de 5 minutes à grande échelle, et NSX limite chaque client (100 requêtes par seconde et 40 simultanées par défaut). La collecte doit se faire par Local Manager, en parallèle, avec des cycles plus lents pour l'inventaire que pour l'état.
6. **Concevoir contre les tempêtes d'alertes.** Une seule location perdue lèverait l'alerte de location, plus une par tunnel, par session BGP et par objet étendu. Les alertes ont besoin d'un ordre de cause racine et d'une prise en compte des fenêtres de maintenance.
7. **« Secondes depuis le dernier full sync » est le mauvais signal.** Une fédération saine fonctionne en delta sync, et un full sync est l'exception. Les signaux utiles sont l'état de synchronisation, l'occupation des files et les échecs de full sync.
8. **Le pack doit se superviser lui-même.** Durée de collecte, nombre d'appels d'API et dernier succès par location, pour que « pas de données » ne soit jamais lu comme « sain ».

La revue a aussi contredit un constat de l'étape 2, et la capture d'écran tranche : NSX mesure bien la latence entre managers. Le produit possède ce chiffre, et l'exploration n'a pas trouvé l'API qui le renvoie.

Une mise en garde est revenue avec elle : vingt locations, c'est peut-être plus que ce que NSX 9.1 Federation supporte. Ce point doit être vérifié dans les maximums de configuration avant de fixer les objectifs d'échelle.

## Étape 6 : la voie de construction

VCF Operations propose deux façons de construire un pack : le Management Pack Builder, sans code, et l'Integration SDK. Le SDK a été retenu pour trois raisons, toutes tirées des étapes ci-dessus. Une seule instance d'adaptateur doit interroger plusieurs managers et découvrir les Local Managers à partir du Global Manager. Elle doit suivre une bascule du Global Manager. Et les valeurs dérivées, comme un écart de version ou un état de location qui résiste aux tempêtes d'alertes, demandent du code.

Le coût se paie côté construction. Un adaptateur SDK est une image de conteneur, donc la construction demande un outillage Python et Docker, et le collecteur tire l'image depuis un registre de conteneurs qu'il peut joindre.

## La suite

- Installer l'outillage du SDK et choisir le registre.
- Décider jusqu'où le pack étend les objets NSX natifs.
- Construire le cœur du collecteur face au lab, avec une maquette pour les cas que le lab ne peut pas montrer : un Global Manager standby, un switchover, vingt locations.
- Trouver l'API de latence et un moyen de lire l'état du pare-feu des hôtes.

La [partie 2](/fr/posts/nsx-federation-management-pack-install/) couvre le build, l'installation et les premiers objets dans VCF Operations.

## Sources

- Lab : Holodeck VCF 9.1.1 imbriqué, NSX 9.1.1.0 (Local Managers) et 9.1.1.0100 (Global Manager), VCF Operations 9.1.1.0 build 25795063. Tous les appels d'API de ce billet ont été exécutés le 17 novembre 2026.
- [VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction)
- KB Broadcom [324244](https://knowledge.broadcom.com/external/article/324244/split-brain-condition-after-a-global-man.html) : split-brain après un switchover de Global Manager
- KB Broadcom [378126](https://knowledge.broadcom.com/external/article/378126/nsx-apis-throws-error-message-client-ex.html) : limites de débit des clients de l'API NSX
- KB Broadcom [345822](https://knowledge.broadcom.com/external/article/345822/gm-to-lm-network-latency-threshold-excee.html) : alarme de latence entre Global Manager et Local Manager
