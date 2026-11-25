---
title: "NSX dans VCF 9.1.2 : le NSX autonome est de retour, et quatre changements plus modestes"
date: 2026-11-25
description: "VCF 9.1.2 est une version de maintenance, mais NSX 9.1.2 contient un changement qui compte pour le design : on peut de nouveau installer, mettre à niveau et exploiter NSX seul, sans SDDC Manager ni domaine VCF. Je commence par le NSX autonome, puis je passe aux quatre changements plus modestes : vDefend dans vSphere Foundation, des adresses TEP statiques dans un transport node profile, un sous-réseau plus grand pour les Service Engines d'Avi, et des runbooks ODS qu'on lance depuis l'interface. La fin du billet couvre ce qui a bougé autour de NSX dans VCF et l'ordre de mise à niveau."
summary: "NSX 9.1.2 ramène le cycle de vie autonome que NSX 9.0 avait retiré : installation ou mise à niveau depuis NSX 4.2.x sans SDDC Manager, avec VCF Operations gardé seulement pour les licences. Il donne aussi à vSphere Foundation un NSX Manager limité à la sécurité pour vDefend, l'adressage statique des TEP dans les transport node profiles, un sous-réseau plus grand pour les Service Engines d'Avi sur les Supervisors en tier-1, et des runbooks ODS qu'on lance depuis l'interface de NSX Manager. Sept schémas, les changements d'intégration dans SDDC Manager, HCX et VCF Operations, et l'ordre de mise à niveau et de correctif."
tags: ["VCF 9.1", "NSX", "NSX autonome", "vDefend", "vSphere Foundation", "Mise à niveau", "Avi", "Notes de terrain"]
categories: ["Notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-912/fig-01-standalone-vs-full-stack.fr.svg"]
params:
  seoDescription: "Ce qu'apporte NSX 9.1.2 : NSX autonome, NSX limité à la sécurité pour vSphere Foundation, TEP statiques, Avi, runbooks ODS et ordre de mise à niveau."
---

VCF 9.1.2 est sorti le 24 novembre 2026. C'est une version de maintenance, surtout du travail de supportabilité, mais il y a un changement NSX à connaître avant votre prochain design. Depuis NSX 9.0, [l'installation ou la mise à niveau autonome de NSX n'était plus prise en charge](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/release-notes/vmware-cloud-foundation-90-release-notes/platform-product-support-notes/product-support-notes-nsx.html) : NSX venait avec VCF, ou pas du tout. Avec NSX 9.1.2, le cycle de vie autonome revient, et on peut de nouveau installer, mettre à niveau et exploiter NSX tout seul.

J'ai lu les notes de version 9.1.2 et le guide NSX de la documentation VCF 9.1.x avec trois questions en tête : qu'est-ce qui est nouveau dans NSX, qu'est-ce qui a changé dans la façon dont NSX s'intègre à VCF, et dans quel ordre faire la mise à niveau. Là où la documentation est mince, je le dis. Deux des schémas sont mes propres croquis de choses que la doc décrit seulement en mots, et c'est indiqué sur chacun.

## Ce que livre NSX 9.1.2

NSX 9.1.2.0, c'est le build 25929667 dans la BOM de VCF 9.1.2, avec cinq nouveautés.

| Nouveauté | Ce qu'elle fait | Pour qui |
|---|---|---|
| Cycle de vie autonome de NSX | Installer, mettre à niveau et exploiter NSX depuis NSX Manager, sans SDDC Manager ni domaine VCF | Les parcs NSX 4.2.x hors VCF, et les clients VVF qui achètent vDefend |
| Droit d'usage de NSX dans VVF | vSphere Foundation peut faire tourner un NSX Manager limité à la sécurité pour vDefend, sans réseau overlay | Les clients vSphere Foundation qui ajoutent vDefend |
| IP et MAC statiques des TEP d'hôte | Un transport node profile peut attribuer des adresses TEP statiques sans conflit ARP ni fausse détection d'override | Les clusters qui veulent des adresses TEP fixes et documentées |
| CIDR du réseau de données des SE Avi | Sur un Supervisor en NSX tier-1, les Service Engines d'Avi ont leur propre sous-réseau et peuvent dépasser l'ancien /28 | Les Supervisors en tier-1 qui ont besoin de plus de Service Engines |
| Runbooks ODS dans l'interface | Lancer des runbooks de diagnostic depuis NSX Manager, les suivre, télécharger ce qu'ils collectent | Quiconque dépanne NSX sans outillage API |

Les deux premières changent l'endroit où NSX peut tourner. Les trois autres sont plus modestes et simplifient le quotidien ; je les prends dans cet ordre.

## 1. Le NSX autonome

Dans un VCF complet, NSX n'est qu'un composant du fleet : VCF Operations pilote son cycle de vie, SDDC Manager gère les domaines, et NSX arrive par la BOM de VCF. En autonome, SDDC Manager et les domaines disparaissent. Il vous faut toujours vCenter et des hôtes ESX, et aussi VCF Operations et un serveur de licences, mais seulement pour le droit d'usage (j'y reviens plus bas, dans la partie sur les licences).

![Deux zones en pointillé côte à côte. À gauche, VCF 9.1.2 complet : VCF Operations, SDDC Manager, vCenter, le cluster NSX Manager et les nœuds de transport ESX empilés de haut en bas, NSX étant livré par la BOM de VCF. À droite, NSX 9.1.2 autonome : un serveur de licences et VCF Operations reliés à vCenter par une ligne pointillée de droit d'usage, puis vCenter enregistré comme compute manager, NSX Manager qui gère les mises à niveau, la sauvegarde, les certificats, les mots de passe et l'authentification, et des nœuds de transport ESX en 9.1.2 pour une nouvelle installation ou en 8.0 U3 ou plus récent pour une mise à niveau. Un pied de schéma liste les limites.](/images/nsx-912/fig-01-standalone-vs-full-stack.fr.svg)

Tout le reste du cycle de vie se fait depuis l'interface de NSX Manager ou l'API NSX : mises à niveau, correctifs, sauvegarde et restauration, inventaire, certificats, mots de passe, authentification, supervision et journaux. Local Manager et Global Manager sont tous les deux pris en charge, donc NSX Federation fonctionne, et les hôtes stateless aussi.

### Une nouvelle installation

Avant de commencer, il vous faut un vSphere Distributed Switch, des enregistrements DNS pour les managers et un MTU d'au moins 1700 sur le VDS (9000 recommandé). Ensuite :

1. Installez le premier nœud NSX Manager et connectez-vous.
2. Activez les droits d'usage de vCenter et de NSX.
3. Enregistrez vCenter comme compute manager.
4. Au besoin, déployez deux autres nœuds pour former le cluster à trois nœuds.
5. Créez les transport zones, et un cluster NSX Edge s'il vous en faut un.
6. Préparez les hôtes ESX comme nœuds de transport avec un transport node profile.
7. Ajoutez une passerelle tier-0 si vous voulez du routage.

Le nouveau NSX Manager lui-même se déploie sur ESX 9.1.2 ou plus récent.

### Une mise à niveau depuis NSX 4.2.x

Si vous exploitez déjà NSX hors VCF, c'est par ici. La source doit être en NSX 4.2.0 ou plus récent, avec vCenter et ESX en 8.0 Update 3 ou plus récent. L'Upgrade Coordinator de NSX fait l'essentiel du travail, dans cet ordre : les pré-vérifications avec le bundle `.pub`, le bundle de mise à niveau `.mub`, le plan de gestion, vCenter (mis à niveau avec vSphere), les hôtes, les edges, la finalisation, et en dernier l'activation des licences.

Pour les hôtes, vous avez deux options :

| Option | Ce qui se passe | Ce qui reste |
|---|---|---|
| VIB NSX seulement | L'Upgrade Coordinator met à niveau les VIB NSX sur les hôtes | vCenter et ESX restent en 8.x |
| Mise à niveau complète d'ESX | Vous passez vCenter et ESX en 9.1.2, et les VIB NSX viennent avec la mise à niveau d'ESX | Rien ne reste en 8.x |

La première option vous amène à NSX 9.1.2 en gardant les hôtes vSphere 8 tels quels, ce qui vous laisse le temps de planifier la suite.

### Les licences

Depuis VCF 9, plus de clé de licence NSX à saisir. Le serveur de licences détient les droits, VCF Operations attribue une licence principale à vCenter, et NSX tire son droit d'usage de ce vCenter. Le NSX autonome fonctionne pareil, et c'est pour ça que VCF Operations et le serveur de licences restent dans la liste plus haut.

Une nouvelle installation ou une mise à niveau démarre une évaluation de 90 jours, et une fois l'évaluation terminée, l'application des licences restreint l'accès aux fonctions de NSX. Mettez l'activation des droits d'usage dans votre plan de déploiement.

### Les limites

- Le mode Multi NSX Manager avec un seul vCenter n'est pas pris en charge en autonome, et d'après le guide, c'est la seule fonction qu'on perd.
- La mise à niveau autonome ne vaut pas pour un NSX qui fait déjà partie d'un VCF complet ; celui-là se met à niveau avec le reste de VCF.
- Vous pouvez plus tard faire converger un NSX autonome vers un VCF complet, mais la convergence crée un domaine de gestion VCF et il n'y a pas de retour en arrière.

## 2. Un NSX limité à la sécurité pour vSphere Foundation

La fonction s'appelle « NSX Entitlement in VVF » : on peut déployer NSX et vDefend directement dans un environnement vSphere Foundation, et donner ainsi aux charges de travail VVF la sécurité réseau et la protection contre les menaces.

Connaissez les limites avant de promettre quoi que ce soit. À partir de 9.1.2, VVF fait tourner un NSX Manager allégé, restreint à une configuration de sécurité seulement, et le réseau overlay n'est pas disponible dans ce modèle de déploiement. Autrement dit, c'est vDefend pour des charges de travail sur des réseaux VLAN, sans VPC ni segments overlay.

![Une zone en pointillé intitulée vSphere Foundation. À l'intérieur, vCenter comme compute manager et un NSX Manager allégé, limité à la sécurité, au-dessus d'une barre épaisse intitulée réseaux de charges de travail adossés à des VLAN, d'où pendent cinq VM protégées par vDefend. Une ligne pointillée de NSX Manager vers la barre porte la mention politique vDefend. À droite, un encadré grisé indique que le réseau overlay ne fait pas partie de ce modèle, et un second indique que le VCF Installer n'installe pas NSX pour un déploiement VVF et que le NSX autonome prend en charge la plateforme NSX pour le virtual patching de vDefend. Un pied de schéma précise que le dessin est un croquis, car le guide ne donne pas de topologie.](/images/nsx-912/fig-02-vvf-security-only.fr.svg)

Ce qu'on ne trouve nulle part, c'est un endroit qui explique comment NSX arrive dans un environnement VVF. D'un côté, le guide de déploiement précise que le VCF Installer n'exige pas et n'installe pas NSX pour un déploiement de type VVF ; de l'autre, les notes de version indiquent que le NSX autonome prend en charge la plateforme NSX pour le virtual patching de vDefend, une fonction pour les clients qui achètent VVF et vDefend. En mettant les deux ensemble, ma lecture est que NSX pour VVF passe par l'installation autonome décrite plus haut.

Ne cherchez pas non plus la procédure d'activation pas à pas dans la documentation 9.1.x : le guide NSX se contente de renvoyer à une rubrique intitulée *Activating VMware vDefend with VMware vSphere Foundation (VVF) Deployment*. Et un dernier point avant de dimensionner quoi que ce soit : selon le guide de design VCF, License Hub est un composant obligatoire dès qu'on utilise le module vDefend.

## 3. IP et MAC statiques des TEP dans un transport node profile

D'abord deux définitions. Un TEP (tunnel endpoint), c'est l'adaptateur VMkernel qui transporte le trafic overlay Geneve sur chaque hôte, et un transport node profile (TNP), c'est le gabarit que NSX applique à tous les hôtes d'un cluster, adressage des TEP compris. Si vous voulez la carte complète des pools de TEP et des autres pools d'adresses de VCF 9.1, je l'ai dessinée dans [un billet précédent](/fr/posts/vcf9-ip-pools-explained/).

![À gauche, un symbole de profil de nœud de transport relié à une barre épaisse intitulée VLAN des TEP d'hôte, avec trois hôtes ESX en dessous, chacun avec un vmk TEP à IP et MAC fixes. À droite, quatre lignes montrent comment un TEP d'hôte obtient son adresse : DHCP et pool d'IP, utilisables dans un TNP ou sur un hôte seul ; liste d'IP statiques, hôte seul uniquement selon la KB 431877 ; et, surligné en vert, IP et MAC statiques dans le TNP, nouveau en 9.1.2, sans conflit ARP ni fausse détection de dérogation dans vSphere Configuration Profiles.](/images/nsx-912/fig-03-static-tep.fr.svg)

Le DHCP et les pools d'IP fonctionnent dans un TNP comme sur un hôte seul. La liste d'IP statiques, elle, était jusqu'ici une option par hôte : d'après la [KB 431877](https://knowledge.broadcom.com/external/article/431877/migration-of-host-transport-node-vtep-in.html), elle n'est offerte que quand on configure un hôte seul. NSX 9.1.2 ajoute une quatrième voie : on peut maintenant attribuer des adresses IP et MAC statiques aux TEP d'hôte dans un transport node profile, sans provoquer de conflit d'IP (ARP) ni de fausse détection d'override dans vSphere Configuration Profiles.

Les notes de version ne vont pas plus loin. Vous n'y trouverez ni les champs de l'interface ni les propriétés d'API, alors vérifiez la référence d'API de NSX 9.1.2 avant d'automatiser.

### Le rôle de vSphere Configuration Profiles

Comme vSphere Configuration Profiles fait partie de cette phrase, ça vaut la peine de voir comment VCP et NSX se partagent un cluster. vSphere Configuration Profiles (VCP) garde tous les hôtes d'un cluster sur une même configuration cible, en quatre étapes : validation, vérification de conformité, impact, remédiation. Depuis VCF 9.1, VCP gère aussi le vmk TEP de l'overlay depuis vCenter, avec un type de service appelé vNetworking.

Avant de préparer un cluster VCP pour NSX, lancez ces deux commandes sur chacun de ses hôtes :

```bash
esxcli network firewall ruleset set -e true -r DVFilter
esxcli system syslog config set --remote-host-max-msg-len 4096
```

Ensuite, la démarche est la suivante :

1. Créez un commutateur d'hôte VDS.
2. Activez VCP sur le cluster.
3. Sous *Desired State › Configuration › Draft*, créez ou importez un brouillon.
4. Ajoutez les clés `esx/network/vmknics`, `esx/network/net_stacks`, `esx/network_vds/switches` et `nsx/fabric/transport_node_config` ou `transport_node_profile`.
5. Lancez la pré-vérification, appliquez, puis vérifiez la conformité.

À partir de là, NSX Manager affiche une capsule VCP sur le cluster sous *System › Fabric › Hosts › Clusters* et y bloque la création, la modification et la désinstallation. Pour attacher un nouveau TNP, on le crée dans NSX, puis on met à jour le brouillon du cluster dans vCenter.

VCP compare chaque hôte au document du cluster, donc une adresse TEP statique qui change d'un hôte à l'autre pouvait passer pour une dérive. Je pense que c'est cette fausse détection d'override que 9.1.2 supprime, mais le mécanisme n'est expliqué nulle part : prenez ça comme ma lecture.

## 4. Un sous-réseau dédié aux Service Engines d'Avi

Celui-ci concerne la répartition de charge pour Kubernetes. Sur un Supervisor en réseau NSX tier-1, NCP (NSX Container Plugin) construit les segments et les passerelles, et quand c'est Avi qui fait la répartition de charge, ses Service Engines ont besoin d'un réseau de données. Ce réseau était plafonné à un /28, donc 16 adresses, une limite que Broadcom juge lui-même trop restrictive.

![Deux zones en pointillé. À gauche, avant 9.1.2 : une passerelle NSX tier-1 au-dessus d'une courte barre intitulée réseau de données des SE Avi, /28, avec trois Service Engines et la légende 16 adresses dans un /28. À droite, NSX 9.1.2 : une CRD NCPSetting qui porte le CIDR du sous-réseau de données des SE pointe vers la passerelle tier-1, au-dessus d'une longue barre intitulée réseau de données des SE Avi, votre CIDR, avec six Service Engines et la légende de la place pour dépasser la limite du /28. Un pied de schéma indique que le sous-réseau correspond au sous-réseau Services du profil de service VPC après un passage à NSX VPC, et que le dessin est un croquis.](/images/nsx-912/fig-04-avi-se-subnet.fr.svg)

En 9.1.2, c'est vous qui choisissez le CIDR du sous-réseau des Service Engines. La valeur va dans la ressource personnalisée NCPSetting, la même CRD que vous utilisez déjà pour les réglages de NCP. Si le Supervisor migre plus tard de NSX tier-1 vers le réseau NSX VPC, le sous-réseau correspond au sous-réseau Services existant du profil de service VPC.

Gardez en tête que depuis 9.1, le vSphere Client ne déploie plus de Supervisor en réseau classique de segments NSX ; seule l'API le fait encore (voir les [notes de version du Supervisor pour 9.1](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-service-administration-and-development/9-1/release-notes/vmware-vsphere-supervisor-release-notes.html)). En pratique, vous réglerez donc surtout ce paramètre sur des Supervisors déjà en place. Il n'y a aucun exemple de NCPSetting dans les notes de version, alors prenez le nom du champ dans la documentation de NCP.

## 5. Les runbooks ODS dans l'interface de NSX Manager

Si vous avez déjà dépanné NSX à la main, vous savez pourquoi l'Online Diagnostic System (ODS) existe. C'est surtout du travail manuel (analyse de journaux et commandes lancées avec les privilèges root), ça coûte beaucoup d'allers-retours avec le support, on refait la même démarche pour chaque problème du même type, et ça demande des connaissances de spécialiste.

ODS emballe ces démarches dans des runbooks. Un runbook s'exécute dans le nœud NSX, fait les vérifications d'état, la collecte de données et l'analyse de cause, et vous rend un rapport, parfois avec des artefacts comme des captures de paquets. Les runbooks prédéfinis sont livrés avec NSX ; les runbooks dynamiques sont des paquets que Broadcom a testés et certifiés pour des problèmes ciblés.

Jusqu'ici, on ne pouvait lancer les runbooks que par l'API. Depuis 9.1.2, on peut aussi les lancer depuis l'interface.

![Cinq boîtes en ligne reliées par des flèches. Un, une alarme se déclenche, une alarme associée à un runbook. Deux, NSX recommande l'article de la base de connaissances et le runbook à lancer. Trois, vous le lancez sous Plan & Troubleshoot, ODS Runbooks. Quatre, il s'exécute dans le nœud : NSX Edge, hôte ESX ou NSX Manager. Cinq, vous obtenez un rapport par View Details, plus des artefacts à télécharger. Un pied de schéma liste les runbooks prédéfinis offerts dans l'interface et les deux runbooks dynamiques.](/images/nsx-912/fig-05-ods-runbook-flow.fr.svg)

Voici le chemin dans l'interface, depuis la liste des runbooks :

1. Connectez-vous à NSX Manager avec un compte administrateur local, à `https://<nsx-manager>/login.jsp?local=true`.
2. Allez dans *Plan & Troubleshoot › ODS Runbooks*.
3. Ouvrez le menu à trois points du runbook et cliquez sur *Invoke Runbook*.
4. Renseignez les paramètres ; l'icône d'information explique chacun d'eux.
5. Cliquez sur *Invoke Runbook*, puis sur *View Details* une fois l'exécution terminée.

Les exécutions passées restent dans l'onglet *Runbook Invocation*. Pour ajouter un runbook dynamique, passez par *Import Runbook*, choisissez le fichier, indiquez les nœuds cibles et importez.

Sept des runbooks prédéfinis qu'on trouve dans l'interface, et ce qu'ils diagnostiquent :

| Runbook | Ce qu'il diagnostique |
|---|---|
| EdgeHealth | Un edge en panne pendant un déploiement ou une mise à niveau ; propose des contournements |
| BgpNeighborState | Pourquoi un voisin BGP est tombé, avec capture BGP et BFD en option |
| EdgeDpBfd | Les problèmes BFD des edges ; se déclenche aussi quand une session BFD tombe |
| OverlayTunnel | Un tunnel overlay manquant ou tombé entre deux VTEP |
| IPSecVPNSession | Les échecs de session VPN : propositions, authentification, sélecteurs de trafic |
| AdfCollect | Les données de performance du datapath ESX |
| CorfuServer | La stabilité du layout Corfu, les E/S disque, l'état du compactor |

LBScale et CcpTNConnection sont aussi dans l'interface, et on peut lancer CorfuServer et CcpTNConnection directement depuis une alarme active avec le lien *Invoke Runbook*. Par contre, tous les runbooks prédéfinis ne sont pas dans l'interface ; les autres passent encore par l'API.

## Les changements d'API

Si vous automatisez NSX, il y a 23 nouvelles opérations NSX : 13 dans la Policy API, 5 dans la Manager API et 5 dans l'Autonomous Edge API. Aucune opération n'est dépréciée ni supprimée.

- **ODS (Policy API) :** lister les runbooks, chercher des exécutions, filtrer les arguments, interroger les paquets de runbooks dynamiques.
- **Résolveur DNS du pare-feu de passerelle tier-0 (Policy API) :** `dns-resolver-config`, `dns-resolver-status` et `dns-resolved-ips` sous `/infra/tier-0s/{id}/firewall/`.
- **Service fqdn-resolver (Manager API et Autonomous Edge API) :** état, démarrage, arrêt et redémarrage sous `/node/services/fqdn-resolver`.
- **Données des signatures IDS :** scores CVSS v3 et versions requises sur les signatures, identifiants de tactique et de technique MITRE.

Un renommage à vérifier dans vos scripts : une propriété de structure est passée de `datapth_tx_pkts` à `datapath_tx_pkts` dans `HostScrxDebugStats`.

## Ce qui a changé autour de NSX dans VCF

En dehors de NSX lui-même, SDDC Manager, HCX et VCF Operations ont chacun changé quelque chose qui touche NSX. Les voici à côté des deux changements déjà vus.

| Sujet | Avant 9.1.2 | En 9.1.2 |
|---|---|---|
| Cycle de vie de NSX | Uniquement par la BOM de VCF | NSX autonome rétabli, avec une convergence à sens unique vers un VCF complet |
| vSphere Foundation | Pas de droit d'usage de NSX | NSX Manager limité à la sécurité pour vDefend, sans overlay |
| Convergence et import | Hôtes avec plusieurs vmk de gestion ou de vMotion, ou vCenter sur un autre VLAN : non pris en charge | Les deux sont pris en charge |
| NSX partagé entre domaines | VCF Operations affichait ses certificats sous un seul domaine | Corrigé : affichés sous chaque domaine qui le partage |
| Extension réseau HCX | L'extension DVPG vers DVPG exigeait des nœuds de transport NSX à destination | Plus nécessaire, avec un profil réseau adossé à un DVPG |
| VCF Operations for networks | Pas d'instances de plateforme autonomes | Des instances supplémentaires avec leur propre cycle de vie Day 0 et Day 2 |

Les deux premières lignes, vous les connaissez déjà, et la ligne des certificats n'est qu'une correction de la façon dont VCF Operations affiche les certificats d'un NSX partagé. Les lignes convergence, HCX et VCF Operations for networks méritent un peu plus d'explications :

- Convergence avec plusieurs adaptateurs vmk : les hôtes peuvent avoir plusieurs adaptateurs VMkernel de gestion et de vMotion, en IPv4 et en IPv6, à condition que chaque hôte en ait au moins un de chaque.
- Convergence d'un vCenter sur un autre VLAN : un vCenter existant peut se trouver sur un VLAN de gestion autre que celui de VCF, sur un port group distribué associé à ce VLAN.
- HCX sans nœuds de transport NSX : créez un profil réseau adossé à un DVPG et assignez-le au réseau HCX Intrasite Control quand vous créez le compute profile du site de destination.
- VCF Operations for networks : les instances de plateforme supplémentaires se gèrent dans leur propre interface et leur propre API, hors du cycle de vie de VCF.

## L'ordre de mise à niveau

Une instance VCF 9.0.x qui passe en 9.1.x suit un ordre strict, et le schéma le reprend ; une instance 5.2.x a son propre tableau, très proche. N'improvisez pas avec la séquence : si vous la ratez, vous aurez des erreurs, par exemple des licences impossibles à attribuer à vCenter.

![Sept boîtes en ligne reliées par des flèches. Un, le fleet : VCF Operations d'abord, puis SDDC Manager, les services de gestion, Automation, HCX. Deux, les Global Managers, avec Federation seulement : cluster en attente puis cluster actif. Trois, NSX Manager, mis à niveau avec vCenter en une seule étape du plan de mise à niveau du domaine. Quatre, VKS 3.6.0 ou plus récent, puis le Supervisor. Cinq, ESX, images vLCM uniquement, où les VIB NSX sont mis à niveau. Six, les clusters NSX Edge, puis la finalisation de NSX. Sept, le nettoyage : version du VDS, Tools, compatibilité des VM, format de disque vSAN. Les boîtes deux, trois et six sont encadrées comme étapes du plan de gestion et de contrôle de NSX, et la boîte cinq comme plan de données de NSX sur les hôtes.](/images/nsx-912/fig-06-upgrade-order.fr.svg)

Dans cet ordre, NSX intervient à trois moments, quatre si vous avez Federation :

- **Les Global Managers, avant tout le reste côté NSX.** VCF ne gère pas leur cycle de vie, donc vous téléchargez vous-même les bundles `.pub` et `.mub`, puis vous mettez à niveau l'Upgrade Coordinator, le cluster en attente, puis le cluster actif.
- **NSX Manager, avec vCenter.** Les deux se mettent à niveau en une seule étape (section suivante).
- **ESX.** Depuis VCF 9, les VIB NSX se mettent à niveau dans l'étape ESX, donc les hôtes n'entrent plus en mode maintenance pendant la mise à niveau de NSX Manager. ESX ne passe qu'après NSX Manager et vCenter.
- **Les clusters NSX Edge, puis la finalisation.** Tant que la finalisation n'est pas faite, vous ne pouvez pas configurer les nouvelles fonctions de NSX. Laissez environ 15 minutes à l'auto-réconciliation avant de planifier la mise à niveau suivante.

Avant de commencer, sauvegardez SDDC Manager, chaque vCenter et chaque NSX Manager, vérifiez qu'aucun mot de passe ni certificat n'est expiré, et lancez les pré-vérifications dans VCF Operations. Et la règle du sens unique s'applique : la version cible doit être sortie après celle où vous êtes.

### vCenter et NSX Manager forment une seule étape

Depuis VCF 9.1, le plan de mise à niveau du domaine traite vCenter et NSX Manager comme une seule étape. Vous construisez le plan dans VCF Operations (*Build › Lifecycle › VCF Instances*, votre domaine, *Upgrades*, *Plan Domain Upgrade*) et vous choisissez un scénario.

![Deux rangées de boîtes numérotées. La rangée fenêtre de maintenance optimisée compte cinq étapes : préparer vCenter sans interruption, mettre à niveau NSX Manager, basculer vCenter, mettre à niveau les hôtes ESX, NSX Edge et finalisation, avec une barre verte au-dessus des étapes deux et trois intitulée une seule fenêtre de maintenance. La rangée mise à niveau séquentielle compte quatre étapes, chacune dans sa propre fenêtre : mettre à niveau NSX Manager, mettre à niveau vCenter sur place ou par préparation et bascule, mettre à niveau les hôtes ESX, NSX Edge et finalisation. Un pied de schéma liste les contraintes.](/images/nsx-912/fig-07-vcenter-nsx-step.fr.svg)

Le scénario optimisé prépare d'abord le nouveau vCenter, sans interruption, puis met à niveau NSX Manager et bascule vCenter dans une même fenêtre de maintenance. La condition : la version cible de vCenter doit être la même que la version actuelle de SDDC Manager. Le scénario séquentiel donne à chaque composant sa propre fenêtre. Si vous partez de 5.2.x, la seule option pour vCenter est la mise à niveau à interruption réduite.

### Passer de 9.1.x à 9.1.2

Si vous êtes déjà en 9.1.0.x ou 9.1.1.x, ne touchez pas à *Upgrade All* dans VCF Operations tant que les dépendances de correctif de 9.1.2 ne sont pas respectées, et appliquez plutôt les correctifs composant par composant, dans cet ordre :

1. Commencez par le composant fleet lifecycle des VCF management services.
2. Passez VCF Operations seul, sans aucune autre opération de cycle de vie en cours.
3. Depuis 9.1.0.x seulement : corrigez le runtime des management services avant l'identity broker ou le Salt RaaS qu'il héberge, et VCF Automation avant le migration service engine.
4. Corrigez ensuite les autres composants, NSX compris.

Pendant le correctif d'un software depot, les autres correctifs attendent qu'il soit fini ; prévoyez le coup.

Parmi les problèmes NSX résolus, voici ceux que je lirais en premier : la création de VPC sur la transit gateway par défaut qui échouait sur une incohérence de transport zone, une transit gateway centralisée qui perdait son sous-réseau de transit après une mise à niveau, la mise à niveau du Manager bloquée tôt sur le mode maintenance des contrôleurs, et un blocage du routage des edges avec des voisins BGP dynamiques.

Et les problèmes connus à prévoir :

- Les groupes DFW basés sur des VM peuvent être périmés quelques minutes pendant une mise à niveau progressive du Manager. Ils se resynchronisent tout seuls.
- Très grands ensembles d'IP DFW : restez à 1,5 million d'IP réalisées ou moins par hôte et à 800 000 par filtre de VM (KB 441679).
- Une condition de course sur la sauvegarde locale peut mettre en pause la mise à niveau du Manager avec « Failed to create Local backup ». Relancez, tout simplement.

## Ce que j'en ferais

- **Vous exploitez NSX 4.2.x hors VCF ?** Vous avez maintenant un chemin vers 9.1.2 qui ne commence pas par SDDC Manager. Prévoyez quand même VCF Operations et un serveur de licences, et gardez un œil sur les 90 jours d'évaluation.
- **Vous exploitez vSphere Foundation et vous voulez vDefend ?** C'est possible maintenant, avec un NSX Manager limité à la sécurité. N'en attendez simplement pas de réseau overlay.
- **Vous êtes déjà en VCF 9.1 complet ?** Pour vous, 9.1.2 est un correctif. Suivez l'ordre des correctifs, laissez *Upgrade All* tranquille tant que les dépendances ne sont pas satisfaites, et lisez les problèmes connus de NSX avant la fenêtre de maintenance.
- **Vous êtes encore en 5.2 ou en 9.0 ?** Vous pouvez passer directement à 9.1.2, à condition de garder l'ordre : fleet, Global Managers, NSX Manager avec vCenter, ESX, puis edges et finalisation.

Broadcom met à jour ses notes de version après la sortie, alors relisez-les avant de planifier.

## Sources

- [VMware Cloud Foundation 9.1.2.0 Release Notes](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/release-notes/vmware-cloud-foundation-9-1-2-0-release-notes.html) : Getting to 9.1.2, et les sections NSX 9.1.2.0, SDDC Manager 9.1.2.0 et changelog d'API
- [VCF Operations 9.1.2.0 Release Notes](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/release-notes/vmware-cloud-foundation-9-1-2-0-release-notes/vcf-operations-9-1-2-0-release-notes.html) : HCX, VCF Operations for networks, certificats d'un NSX partagé
- [Documentation VMware Cloud Foundation 9.1.x](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1.html) : guide NSX (Standalone NSX, profils de nœud de transport, runbooks ODS, vDefend), guide Deployment, Convergence and Upgrade
- [Notes de support produit NSX, VCF 9.0](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/release-notes/vmware-cloud-foundation-90-release-notes/platform-product-support-notes/product-support-notes-nsx.html) : installation ou mise à niveau autonome non prise en charge à partir de NSX 9.0
- [VMware vSphere Supervisor Release Notes, 9.1](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-service-administration-and-development/9-1/release-notes/vmware-vsphere-supervisor-release-notes.html) : Supervisor en réseau classique de segments NSX, par l'API seulement
- [KB 431877](https://knowledge.broadcom.com/external/article/431877/migration-of-host-transport-node-vtep-in.html) : options d'attribution d'IP des TEP d'hôte, liste d'IP statiques sur un hôte seul
