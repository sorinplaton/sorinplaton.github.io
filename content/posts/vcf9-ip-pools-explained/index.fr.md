---
title: "Pools, blocs et IP Spaces dans VCF 9.1 : lequel alimente quel composant réseau"
date: 2026-10-28
description: "VCF 9.1 demande un Network Pool, deux pools TEP, un External IP Block, un Private Transit Gateway IP Block, des CIDR privés de VPC, un service CIDR et — dans VCF Automation — des IP Spaces. Un seul mot, pool, pour sept objets différents. Voici ce qu'est chacun, où il se définit et quelle passerelle, quel sous-réseau ou quel hôte le consomme vraiment."
summary: "Entre SDDC Manager, NSX, l'assistant Supervisor et VCF Automation, VCF 9.1 vous demande au moins sept « pools » différents. Ce billet associe chacun au composant réseau qui le consomme — Tier-0, Transit Gateway, passerelle VPC, sous-réseaux, hôtes ESX, Supervisor — avec trois schémas, les points de NAT, les règles de chevauchement et les pièges de dimensionnement de 9.1."
tags: ["VCF 9.1", "NSX", "VPC", "Transit Gateway", "VCF Automation", "Supervisor", "Adressage IP", "Holodeck"]
categories: ["Notes"]
showToc: true
tocOpen: false
images: ["/images/ip-pools/fig-01-pool-map.svg"]
---

VCF 9.1 demande des adresses à quatre endroits différents : SDDC Manager veut un *Network Pool*, NSX veut des *IP Address Pools* et des *IP Address Blocks*, l'assistant Supervisor veut des *CIDR*, et VCF Automation veut des *IP Spaces*. Tout cela ressemble à « une plage d'adresses », tout cela s'appelle un pool à un moment ou à un autre, et tout cela fait quelque chose de différent. La confusion se comprend : le même `10.x.0.0/16` tapé dans le mauvais champ, soit ne sort jamais de la fabrique, soit se retrouve annoncé à tout le centre de données.

Ce billet est la carte que j'aurais voulu avoir. Pour chaque pool : ce que c'est, où on le définit, quel composant réseau le consomme, et s'il est un jour visible au nord du Tier-0.

*Octobre 2026 · lab Holodeck VCF 9.1.1 · Transit Gateway centralisé sauf mention contraire · les adresses d'exemple sont celles du lab*

## La carte

![Chaque pool et bloc d'un domaine VCF 9.1, associé au composant qui le consomme : External IP Block et uplinks Edge au Tier-0, Private Transit Gateway IP Block et sous-réseau de transit 100.64 au Transit Gateway, SNAT et VIP LB à la passerelle VPC, trois types de sous-réseaux, Network Pool et pools TEP aux hôtes ESX, plage de gestion et service CIDR au plan de contrôle du Supervisor.](/images/ip-pools/fig-01-pool-map.svg)

Lisez-la de haut en bas comme un paquet, du sud vers le nord. Tout ce qui est en **bleu** sort de la fabrique avec sa propre adresse. Tout ce qui est en **vert** reste à l'intérieur d'un Transit Gateway. Tout ce qui est en **violet** a besoin d'un NAT depuis un bloc bleu pour sortir. Les pastilles **moutarde** sont de simples VLAN sur lesquels roule l'overlay, et les **sombres** ne quittent jamais le Supervisor.

| Pool / bloc | Défini dans | Consommé par | Annoncé au nord ? |
|---|---|---|---|
| Network Pool | SDDC Manager | Ports vmkernel ESX : vMotion, vSAN, NFS, Host Overlay (TEP) | Non (VLAN classiques) |
| Host TEP IP Pool | NSX, créé par VCF à partir du Network Pool | Nœuds de transport hôtes (extrémités de tunnel GENEVE) | Non |
| Edge TEP IP Pool | Assistant Edge cluster (vCenter › Networks › Transit Gateways) | Nœuds de transport Edge | Non |
| **External IP Block** | NSX IP Address Blocks, visibilité *External* | Tier-0 (annoncé en BGP), sous-réseaux publics, SNAT, NAT 1:1, VIP LB | **Oui** |
| **Private Transit Gateway IP Block** | NSX IP Address Blocks, visibilité *Private* | Transit Gateway : sous-réseaux private-TGW, routés entre les VPC du projet | Non (9.1 : optionnel) |
| CIDR privés de VPC / Private IP Blocks de l'org | Assistant Supervisor, VPC vCenter, ou portail org de VCFA | Passerelle VPC : sous-réseaux privés, NATés en sortie | Non |
| Sous-réseau de transit VPC (`100.64.0.0/16`) | Transit Gateway, automatique | Liens TGW ↔ passerelle VPC | Non |
| Service CIDR | Assistant Supervisor | Services ClusterIP Kubernetes dans le Supervisor | Non |
| Plage de gestion | Assistant Supervisor | 5 IP consécutives sur le VLAN de gestion pour le plan de contrôle | VLAN routable |
| **IP Space** | Portail fournisseur de VCF Automation | Provider Gateway (Tier-0 / VRF) ; devient l'External IP Block du projet de l'org | **Oui** |

## 1. L'underlay : Network Pool et pools TEP

Ce sont les seuls « pools » qui sont d'honnêtes sous-réseaux VLAN avec une passerelle physique sur le ToR.

**Network Pool (SDDC Manager).** Créé au moment de commissionner les hôtes. Il contient une plage par type de trafic — vMotion, vSAN, NFS et *Host Overlay* — et SDDC Manager remet une adresse de chaque plage au port vmkernel de chaque hôte. Dans le lab, les hôtes de charge de travail sont sur `wld-a-np01`. Un Network Pool n'a rien à voir avec le routage NSX ; c'est de la plomberie par hôte.

**Host TEP IP Pool (NSX).** La plage Host Overlay du Network Pool réapparaît dans NSX comme un *IP Address Pool* sous Networking › IP Address Pools ; VCF le crée pour vous à la création du cluster, ou vous pouvez le pré-créer dans NSX. Chaque nœud de transport ESX y prend une adresse par uplink ; ces adresses sont la source et la destination externes de chaque paquet GENEVE. Du DHCP sur le VLAN TEP est une alternative acceptée.

**Edge TEP IP Pool.** Même idée pour les nœuds Edge, créé à la volée dans l'assistant *Setup Network Connectivity* qui déploie le cluster Edge (choisissez *IP Pool* comme mode d'attribution et l'assistant vous laisse le définir sur place). Les Edges reçoivent aussi leurs VLAN d'uplink ici : un /29 ou /30 point à point par ToR pour le peering BGP du Tier-0.

Aucun de ces pools n'est annoncé nulle part, et aucun n'est nécessaire dans le modèle VPC sans TEP, adossé à des VLAN, de 9.1.1 — c'est tout l'objet du [billet sur VKS sans TEP](/fr/posts/vks-without-teps/).

## 2. Les IP Address Blocks de NSX : les deux qui comptent

Sous NSX › Networking › IP Address Management › IP Address Blocks vivent les deux objets qui décident de ce qui sort de la fabrique. VCF crée un `Day0 External Ip Block` et un `Day0 Private Tgw Ip Block` au bring-up ; vous pouvez les utiliser ou les remplacer, mais vous devez comprendre le drapeau *visibility*.

### External IP Block (visibilité : External)

Ce sont des plages que VCF possède, elles ne doivent chevaucher rien d'autre dans le réseau physique, et le Tier-0 les annonce aux routeurs amont en BGP. Tout ce qui doit être joignable depuis l'extérieur du VPC prend une adresse ici : les sous-réseaux publics, l'adresse SNAT de chaque VPC, les adresses de NAT 1:1 et chaque VIP de répartiteur de charge — y compris la VIP de l'API du Supervisor, ce qui explique que le FQDN du serveur d'API se résolve dans ce bloc et non dans le VLAN de gestion.

Deux pièges de dimensionnement :

- Le bloc est **auto-découpé**. Un sous-réseau public y est taillé dans la taille demandée par le VPC (/26 par défaut dans l'assistant). Planifiez des sous-réseaux, pas un nombre d'IP.
- Le Supervisor est gourmand. Par défaut, il réserve *tout* le Day-0 External IP Block à l'activation alors qu'il n'en utilise qu'une tranche, et ne laisse rien aux autres. [La solution de William Lam](https://williamlam.com/2026/08/vcf-9-1-quick-tip-optimized-workflow-for-configuring-distributed-transit-gateway-dtgw-with-nsx-virtual-network-appliance-vna.html) consiste à découper le bloc Day-0 en plusieurs /26 *avant* l'activation : le Supervisor prend alors un /26 et le reste demeure libre pour VCF Automation et les autres connexions externes, sur le même VLAN.

Un profil de connectivité accepte jusqu'à cinq External IP Blocks.

### Private Transit Gateway IP Block (visibilité : Private)

Ce bloc alimente les *sous-réseaux privés de transit gateway*. Ses adresses ne sont jamais annoncées au nord et il est limité au projet qui possède le Transit Gateway. Un sous-réseau private-TGW est joignable depuis chaque VPC rattaché au même Transit Gateway, sans NAT ; c'est ce qu'utilise le VPC des Supervisor Services pour que les pods de service soient joignables depuis les autres VPC du projet. Pour quitter le projet, le trafic d'un tel sous-réseau est NATé en source au Transit Gateway vers une IP externe — sauf si vous activez l'interrupteur *Private IP Blocks* de 9.1 sur la connexion externe, qui permet d'étendre les sous-réseaux private-TGW tels quels à travers la connexion externe (voir le [billet VCF sur les options de connectivité du Transit Gateway](https://blogs.vmware.com/cloud-foundation/2026/05/12/transit-gateway-connectivity-options-in-vcf-9-1/)).

Et le piège de 9.1 : donnez un **/16** à ce bloc. Un /24 suffisait en 9.0 ; en 9.1 le déploiement du Supervisor/VKS se bloque avec un /24, comme le rapportent [vstellar](https://vstellar.com/2026/05/vcf-9-1-home-lab-series-part-6-configure-network-connectivity/) et d'autres. Broadcom ne documente aucun minimum ; les praticiens s'accordent sur le symptôme. Comme le bloc n'est jamais annoncé, un /16 ne coûte rien. Évitez simplement de réutiliser la valeur Day-0 par défaut `172.31.0.0/16` si vous parlez aussi à AWS.

### Celui que personne ne configure : le sous-réseau de transit

Entre le Transit Gateway et chaque passerelle VPC, NSX alloue des adresses de lien dans `100.64.0.0/16` (le *VPC Transit Subnet* du TGW). NSX le choisit lui-même dans la plage d'adresses partagées et il n'y a aucune raison de le changer. Vous le verrez dans les traceroutes ; vous ne devriez jamais le taper.

## 3. Dans un VPC : trois types de sous-réseaux, trois sorties

Tout ce qui précède ne compte qu'au moment où un sous-réseau est créé, parce que le **type de sous-réseau** décide de quel bloc vient l'adresse et où se fait le NAT.

![Un VPC avec un sous-réseau public, un privé et un private-TGW. Les adresses publiques viennent de l'External IP Block et atteignent le Tier-0 inchangées ; les adresses privées viennent du CIDR privé du VPC et sont NATées en source ou en 1:1 à la passerelle VPC ; les adresses private-TGW viennent du Private Transit Gateway IP Block, sont routées entre VPC par le Transit Gateway et NATées là quand elles quittent le projet.](/images/ip-pools/fig-02-subnet-exits.svg)

| Type de sous-réseau | L'adresse vient de | Annoncé au nord ? | Point de NAT | Joignable depuis |
|---|---|---|---|---|
| Public | External IP Block | oui, tel quel | aucun | partout où le bloc est routé |
| Privé | CIDR privé du VPC (vCenter) / Private IP Block de l'org (VCFA) | seulement via NAT | passerelle VPC : SNAT, ou NAT 1:1 si publié | l'intérieur du VPC |
| Private-TGW | Private Transit Gateway IP Block | non (9.1 : extension optionnelle) | Transit Gateway : SNAT | chaque VPC du même TGW |

Quelques conséquences faciles à manquer :

- **Les CIDR privés peuvent se chevaucher** entre VPC et entre organisations ; les deux autres types de blocs doivent être uniques dans le projet, et l'External block doit être unique dans le centre de données.
- L'interrupteur **SNAT par défaut** vit dans le VPC Connectivity Profile. Désactivé, un sous-réseau privé n'a aucune sortie tant que vous ne publiez pas un NAT 1:1 ; le VPC adossé à des VLAN de 9.1.1 l'exige désactivé parce qu'il n'y a pas de moteur de NAT sur un simple VLAN.
- Une **VIP de LoadBalancer** est une IP externe quel que soit le sous-réseau des backends. Sous-réseau privé + VIP LB est le motif normal de VKS : des nœuds sur le CIDR privé, un point d'accès API sur une IP externe.
- Le **ToR n'apprend jamais que des External blocks**. Si un préfixe d'un bloc privé ou private-TGW apparaît dans votre table BGP, quelqu'un a mis la mauvaise visibilité sur le bloc.

## 4. Comment VCF Automation appelle tout cela

VCF Automation (organisations All Apps) n'invente pas de nouveaux objets réseau ; il en renomme quelques-uns de NSX et les enveloppe de quotas. L'astuce est de savoir quel mot VCFA pointe vers quel objet NSX.

![Objets fournisseur de VCF Automation à gauche, associés aux objets NSX qu'ils créent ou consomment à droite : Region vers NSX Manager, IP Space vers un IP Address Block externe, Provider Gateway vers un Tier-0, réseau régional vers le projet NSX, le Transit Gateway et le VPC par défaut de l'org, blocs IP privés de l'org vers les IP blocks du VPC, VPC et namespaces de l'org vers les sous-réseaux et profils du VPC.](/images/ip-pools/fig-03-vcfa-to-nsx.svg)

**Region.** Lie un NSX Manager, les Supervisors qui sont derrière et une classe de stockage. En général un domaine de charge de travail, et plusieurs Supervisors ne peuvent partager une Region que s'ils dépendent du même NSX Manager.

**IP Space.** C'est celui qui embrouille tout le monde, parce que c'est le nom VCFA d'un External IP Block. Un IP Space a deux moitiés. Ses *IP blocks* sont les adresses au sud du Provider Gateway — celles que les tenants consomment réellement pour leurs sous-réseaux et services. Son *external reachability* est la liste des préfixes au nord de la passerelle vers lesquels les tenants sont autorisés à router. NSX auto-découpe les IP blocks pour les sous-réseaux publics des tenants, pour le NAT source de leurs sous-réseaux privés et pour leurs VIP de répartiteur — exactement les trois rôles d'un External IP Block. La consommation est mesurée par organisation avec des quotas sur les IP unitaires, les CIDR et la taille maximale de sous-réseau.

**Provider Gateway.** Un pointeur vers un Tier-0 ou un Tier-0 VRF de NSX. Vous y rattachez d'un à cinq IP Spaces, et cinq CIDR au total (cinq IP Spaces veut donc dire un CIDR chacun). Il peut être partagé entre organisations ou dédié à une seule — un VRF dédié avec ses propres IP Spaces est la façon de donner à un tenant à la fois Internet et un WAN privé.

**Réseau régional (regional networking).** L'étape qui construit vraiment les choses. Vous donnez à une organisation un Provider Gateway et un cluster Edge dans une Region, et VCF Automation crée un projet NSX au nom de l'organisation, un Default Transit Gateway à l'intérieur avec une connexion externe vers le Tier-0 derrière le Provider Gateway, et un premier VPC nommé `<region>-default-vpc` (le [deep dive de Fojta](https://fojta.wordpress.com/2025/12/05/vcf-automation-9-0-networking-deep-dive/) le détaille). À partir de ce moment, l'organisation *est* un projet NSX : un projet, un Transit Gateway, et les CIDR de l'IP Space apparaissent comme External IP Block du VPC Connectivity Profile de ce projet.

**Private IP Blocks de l'organisation.** La contrepartie côté tenant : les administrateurs d'organisation les créent et les gèrent eux-mêmes, dans l'organisation. Ce sont des CIDR privés de VPC, rien de plus : ils alimentent les sous-réseaux privés, ils peuvent chevaucher ceux des autres tenants, et ils ne touchent jamais au quota du fournisseur.

Le vocabulaire VCFA tient donc en une ligne : **IP Space = External IP Block avec un quota ; Private IP Block de l'org = CIDR privé de VPC ; Provider Gateway = Tier-0 ; réseau régional = projet + TGW + VPC par défaut.**

## 5. Où se place le Supervisor là-dedans

Le Supervisor ajoute trois entrées qui lui sont propres et consomme deux des blocs ci-dessus.

- **Plage de gestion.** Cinq IP consécutives sur le VLAN de gestion — l'adresse de départ devient l'IP flottante, les trois suivantes vont aux VM du plan de contrôle, la cinquième est gardée pour les mises à niveau progressives. Dans le lab, c'est `10.1.1.188–.192`. C'est la seule entrée du Supervisor qui soit une simple adresse de VLAN routable.
- **Service CIDR.** La plage ClusterIP de Kubernetes. Jamais routée, elle ne doit chevaucher rien de ce que voit le plan de contrôle, y compris les CIDR de pods et de services des clusters invités VKS (Antrea, non routables, par cluster).
- **CIDR privés (VPC).** Le bloc privé de VPC dans lequel le Supervisor taillera les sous-réseaux privés des nœuds VKS et des vSphere Pods.

Il prend ensuite un projet NSX et un VPC Connectivity Profile, qui amènent avec eux les blocs External et Private-TGW, et crée deux VPC dans ce projet : `kube-system_<id>` (la VIP de l'API et ses répartiteurs) et `vmware-system-supervisor-services-vpc_<id>` (les Supervisor Services sur des sous-réseaux private-TGW), plus une règle SNAT pour la sortie du plan de contrôle — le tout tiré des blocs *de ce projet*.

En pratique, le Supervisor est activé dans le **projet Default** : ses VPC système, sa VIP d'API et sa sortie utilisent l'External block du projet Default, et chaque organisation tenant reçoit son propre projet avec son propre IP Space. Les deux ne partagent jamais d'espace d'adressage ; les nœuds VKS d'un tenant ont juste besoin d'une route vers la VIP de l'API du Supervisor, et tout le reste passe par le Namespace Proxy de VCFA. Projet Default contre projet d'organisation, c'est une histoire à part — la version courte : Default = contexte administrateur, modifiable depuis vCenter, pas pour la multi-location ; projet d'org = un par organisation, son propre Transit Gateway, ses propres blocs, des quotas via les IP Spaces.

## 6. Aide-mémoire de planification

L'adressage du lab, avec la règle qui s'applique à chaque ligne. Changez les chiffres, gardez les règles.

| Entrée | Valeur du lab | Règle |
|---|---|---|
| VLAN de gestion (plan de contrôle) | `10.1.1.188–.192 /24`, GW `10.1.1.1` | 5 consécutives, sous-réseau différent de tout CIDR de charge de travail |
| Host Overlay (Network Pool) | `10.1.2.0/24` | VLAN avec passerelle ToR, MTU ≥ 1700 (9000 en pratique) |
| Pool TEP Edge | `10.1.3.0/24` | VLAN avec passerelle ToR, routé vers le VLAN TEP des hôtes |
| External IP Block | `10.60.0.0/24`, découpé en /26 | Unique dans le centre de données, annoncé en BGP, jusqu'à 5 par profil |
| Private Transit Gateway IP Block | `10.250.0.0/16` | Unique dans le projet, jamais annoncé, **/16 en 9.1** |
| CIDR privés (VPC) | `10.244.0.0/16` | Peuvent se chevaucher entre VPC ; joignables uniquement via NAT |
| Service CIDR | `172.29.0.0/16` | Aucun chevauchement avec quoi que ce soit, CIDR pods/services VKS inclus |
| Sous-réseau de transit | `100.64.0.0/16` | N'y touchez pas |
| IP Space VCFA (par org) | `10.61.0.0/24` sur `pgw-east` | Mêmes règles qu'un External block, plus les quotas |
| Private IP Block de l'org | `10.251.0.0/16` | Mêmes règles qu'un CIDR privé de VPC |

Deux vérifications avant de cliquer sur *Finish* dans n'importe lequel des assistants :

1. Ouvrez NSX › IP Address Blocks et lisez la colonne **visibility**. External pour tout ce qui doit être joignable, Private pour le bloc du Transit Gateway. Les listes déroulantes des assistants filtrent dessus, et une liste *External IP Blocks* vide est presque toujours un bloc créé avec la mauvaise visibilité — ou un assistant resté sur un état périmé ; annulez et relancez.
2. Pinguez les cinq adresses de gestion et résolvez le FQDN du serveur d'API. Le FQDN ne doit *pas* encore se résoudre vers une IP de gestion ; il recevra une VIP de l'External block pendant l'activation, donc les enregistrements A et PTR suivent en général.

## Sources

- [Broadcom TechDocs — Virtual Private Cloud in NSX (VCF 9.1)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/advanced-network-management/virtual-private-cloud-in-nsx.html)
- [Broadcom TechDocs — Create an IP Pool for Tunnel Endpoint IP Addresses](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/transport-zones-and-transport-nodes/create-an-ip-pool-for-tunnel-endpoint-ip-addresses.html)
- [Broadcom TechDocs — Managing IP Spaces in VCF Automation](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/provider-management/managing-networking-resources/managing-ip-spaces.html) · [Managing Provider Gateways](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/provider-management/managing-networking-resources/managing-provider-gateways.html)
- [Blogue VMware Cloud Foundation — Transit Gateway Connectivity Options in VCF 9.1](https://blogs.vmware.com/cloud-foundation/2026/05/12/transit-gateway-connectivity-options-in-vcf-9-1/)
- [Tomas Fojta — VCF Automation 9.0 Networking Deep Dive](https://fojta.wordpress.com/2025/12/05/vcf-automation-9-0-networking-deep-dive/)
- [William Lam — Optimized workflow for DTGW with VNA (découpage de l'External IP Block)](https://williamlam.com/2026/08/vcf-9-1-quick-tip-optimized-workflow-for-configuring-distributed-transit-gateway-dtgw-with-nsx-virtual-network-appliance-vna.html)
- [vstellar — VCF 9.1 Home Lab Part 6: Configure Network Connectivity](https://vstellar.com/2026/05/vcf-9-1-home-lab-series-part-6-configure-network-connectivity/) · [VCF 9 Part 4: NSX Edge Cluster Deployment](https://vstellar.com/2025/07/vcf-9-part-4-nsx-edge-cluster-deployment/)
- [sdn-warrior — VCF 9 NSX VPC Part 1: centralized Transit Gateway](https://sdn-warrior.org/posts/vcf9-nsx-vpc/)
- [mynetworker — VCF Automation Organization Networking](https://mynetworker.blog/2026/01/11/vcf-automation-in-vcf-9-provider-management-vcf-automation-organization-networking/) · [Devyn Harrington — Building an All Apps Organization](https://devynharrington.com/vcf/building-an-all-apps-organization-in-vcf-automation-9/)
