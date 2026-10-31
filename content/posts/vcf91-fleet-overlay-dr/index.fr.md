---
title: "VCF 9.1 : composants du fleet sur un segment overlay NSX, et reprise au-delà de la latence d'un cluster étiré"
date: 2026-10-30
description: "Un bring-up VCF 9.1 greenfield ne peut pas placer VCF Operations et VCF Automation directement sur un segment overlay NSX : on les diffère, on construit la zone d'atterrissage NSX, puis on termine depuis le VCF Installer. Voici cette séquence, puis le design qu'elle prépare — la reprise du fleet avec Protection and Recovery (SRM et vSphere Replication) sur un segment étiré par NSX Federation, quand les sites sont trop éloignés pour un cluster étiré (plus de 5 ms de RTT) : ce qui se réplique, ce qui se restaure depuis une sauvegarde, l'option Continuous Availability sous 10 ms, et pourquoi il faut rétablir le réseau avant d'allumer la moindre VM."
summary: "Le chemin des composants différés est la seule manière supportée de poser VCF Operations et VCF Automation sur un segment overlay NSX dans un déploiement VCF 9.1 greenfield. La raison de le vouloir, c'est la reprise du fleet : étirer le segment avec NSX Federation, répliquer VCF Operations avec Protection and Recovery, restaurer VCF Automation et les services du fleet depuis une sauvegarde. Au-delà de la limite de 5 ms d'un cluster étiré, c'est un design de reprise à deux instances, pas un cluster étiré — avec la Continuous Availability d'Operations en option si le DCI reste sous 10 ms. Topologie, budget de latence, runbook de bascule, sept schémas."
tags: ["VCF 9.1", "NSX", "NSX Federation", "VCF Operations", "VCF Automation", "Reprise après sinistre", "SRM", "Protection and Recovery", "Design"]
categories: ["Notes"]
showToc: true
tocOpen: false
images: ["/images/fleet-overlay-dr/fig-02-dr-topology.svg"]
---

Deux questions arrivent souvent ensemble. La première : comment mettre VCF Operations et VCF Automation sur un segment overlay NSX dans un déploiement VCF 9.1 greenfield ? La seconde, quelques minutes plus tard : et comment les basculer vers le second centre de données, sachant que les sites sont plus éloignés que ne le permet un cluster étiré — plus de 5 ms de RTT, typiquement 10 ms ou plus sur un DCI régional — et qu'on possède déjà SRM ?

C'est en fait une seule question. Un segment overlay pour les composants du fleet ne mérite son cluster d'edges supplémentaire que si on compte l'étirer, et on l'étire pour une seule raison : la mobilité IP pour la reprise après sinistre. Ce billet prend donc les deux moitiés dans l'ordre — la séquence greenfield d'abord, puis le design de reprise qu'elle met en place : ce qui se réplique, ce qui se reconstruit depuis une sauvegarde, où la latence entre les sites vous contraint et où elle ne change rien, et l'ordre des opérations le jour J.

## Partie 1 — Greenfield : poser les composants du fleet sur un segment overlay

### Le bring-up ne le fera pas pour vous

Par défaut, le VCF Installer met tous les nœuds VCF Operations et VCF Automation sur le même port group distribué que le vCenter — le VLAN de gestion des VM. Il n'y a aucun champ dans l'assistant de bring-up pour un segment NSX, et il ne peut pas y en avoir : à ce stade, il n'existe ni cluster d'edges, ni Tier-0, ni segment overlay.

Ce que 9.1 offre à la place, c'est une option : **Deploy VCF Operations and VCF Automation to a specific vSphere distributed switch or NSX segment**. Cochez-la et l'Installer saute VCF Operations, le cloud proxy, le serveur de licences et VCF Automation, puis termine le bring-up avec SDDC Manager, vCenter, le cluster NSX Manager et le domaine de gestion. Vous ne l'obtenez que pour un *nouveau fleet VCF* ; elle n'est pas proposée quand on ajoute une instance à un fleet existant ou qu'on se connecte à un VCF Operations existant.

![Trois étapes de gauche à droite : bring-up du VCF Installer avec l'option de report ; NSX dans le domaine de gestion — cluster d'edges, Tier-0 avec BGP, Tier-1, segment overlay, DNS et routage ; composants différés déployés par le VCF Installer sur le segment. Un bandeau indique qu'ajouter Automation plus tard depuis VCF Operations le place sur le réseau de gestion.](/images/fleet-overlay-dr/fig-01-greenfield-path.svg)

### La séquence

1. **Bring-up avec l'option cochée.** Le reste de l'assistant est l'habituel : modèle de déploiement, réseau de gestion des VM, réseau de gestion VCF, overlay des hôtes. Vous vous retrouvez avec un domaine de gestion et aucun composant du fleet dessus.

2. **Construire la zone d'atterrissage dans NSX.** C'est le modèle *VCF Management Dedicated VLAN and NSX Overlay Segment* de la design library : un cluster d'edges dans le domaine de gestion (interface ou API de SDDC Manager), un Tier-0 en BGP avec la fabrique qui annonce les segments connectés, un Tier-1 attaché, et un segment overlay sur la transport zone overlay du domaine de gestion. Le segment doit être joignable depuis le VLAN de gestion des VM — SDDC Manager, vCenter, les NSX Manager, les hôtes ESX — et depuis le DNS, le NTP et votre source d'identité. Créez les enregistrements A et PTR de chaque nœud tout de suite ; l'Installer les vérifie.

3. **Déployer les composants différés depuis l'Installer.** Une fois les binaires dans le dépôt de l'Installer et le déploiement de l'instance affiché comme complet, allez dans *Deployment Wizard › Deploy deferred components*. Il demande le modèle de déploiement et la taille, le DNS et le NTP, le segment overlay (vous le verrez comme le port group que NSX a créé pour lui), puis les FQDN et IP des nœuds Operations, du serveur de licences et d'Automation. On peut envoyer la même chose en JSON à l'API de l'Installer, avec le nom du segment dans le champ réseau.

Un détail de dimensionnement pour la plage Automation : de 9.1.0.0 à 9.1.0.300, il faut cinq IP *contiguës* pour les nœuds Automation ; à partir de 9.1.0.400, cinq adresses, contiguës ou non. Réservez-les dans le sous-réseau du segment, avec les nœuds primary, replica et data d'Operations, le FQDN d'Automation et le serveur de licences.

### Un choix de placement à faire tôt

Voici le piège. Si vous sautez Automation au bring-up et que vous l'ajoutez plus tard depuis VCF Operations (*Build › Lifecycle › VCF Management Components*), Operations pose le cluster de service runtime sur le réseau de gestion, sans rien demander. Le seul endroit où vous pouvez choisir un port group vDS ou un segment NSX, c'est le chemin « composants différés » de l'Installer. Décidez donc du segment avant que quelqu'un n'ouvre la page Lifecycle.

![Un schéma de décision : VCF Automation sauté au bring-up se divise en deux chemins. Chemin A, Deploy deferred components du VCF Installer, demande un port group vDS ou un segment NSX et pose le service runtime d'Automation sur le segment overlay, à côté de VCF Operations, avec mobilité IP pour la reprise. Chemin B, VCF Operations Build › Lifecycle › VCF Management Components, n'offre aucun choix de réseau et le pose sur le VLAN de gestion des VM propre au site. Un bandeau note que le cloud proxy reste sur le VLAN de gestion dans les deux cas et que les segments VLAN brownfield restent supportés après une mise à niveau.](/images/fleet-overlay-dr/fig-06-placement-paths.svg)

Deux autres choses pendant qu'on y est. Le cloud proxy reste sur le VLAN de gestion des VM quoi que vous fassiez — il est propre au site et collecte localement, ce qui est exactement ce qu'on veut. Et le brownfield est une autre histoire : le KB 443842 confirme qu'Operations, Automation et le cluster de runtime de gestion sur des segments NSX *VLAN* restent pleinement supportés après une mise à niveau vers 9.1, et personne ne vous demande de les migrer. C'est en greenfield que le modèle overlay est prescrit.

## Partie 2 — Pourquoi le segment : la reprise du fleet entre deux instances

Voici maintenant la raison de tout ça. Le modèle *Dedicated VLAN and NSX Stretched Overlay Segment* de la design library prend le même segment et l'étire entre deux instances VCF avec NSX Federation. Tout l'intérêt, c'est la **mobilité IP** : VCF Operations revient sur le site de reprise avec les adresses, les FQDN et les certificats qu'il avait, et rien en aval — adaptateurs, cloud proxies, SDDC Manager enregistrés dans le fleet — n'a à changer.

### Ce que la latence entre les sites décide pour vous

Avant la topologie, le chiffre. Le temps aller-retour entre les sites classe chaque option en « étiré » ou « deux instances séparées », et tout ce qui dépasse 5 ms de RTT — la limite du cluster vSAN étiré, que le cluster VCF Operations partage justement — vous met dans le second groupe. La suite du billet part de là : un DCI régional de l'ordre de 10 ms ou plus, avec une exception intéressante entre 5 et 10 ms sur laquelle je reviens.

![Une droite de temps aller-retour entre sites en échelle logarithmique, avec une bande ombrée à partir de 5 ms marquant la latence au-delà de la limite d'un cluster étiré. Plafonds rouges à gauche : cluster vSAN étiré 5 ms, nœuds du cluster VCF Operations moins de 5 ms, multisite NSX à manager unique avec reprise automatique du plan de gestion 10 ms. Une fenêtre sarcelle de 5 à 10 ms marque la Continuous Availability de VCF Operations entre fault domains, avec le plafond du witness à 30 ms. Plafonds verts à droite : nœuds Operations vers les endpoints moins de 50 ms, réseaux étirés NSX Federation 150 ms, nœuds Operations vers cloud proxy moins de 300 ms.](/images/fleet-overlay-dr/fig-04-latency-ladder.svg)

| Limite | Valeur | Source | Ce que ça veut dire au-delà de 5 ms RTT |
|---|---|---|---|
| Cluster vSAN étiré, site à site | ≤ 5 ms RTT | exigences vSAN stretched cluster | **Exclu.** Pas de cluster de gestion étiré, pas de vSphere HA entre sites ; deux domaines de gestion. |
| Nœuds analytiques VCF Operations (primary, replica, data) | < 5 ms RTT | VCF Operations 9.0 sizing guidelines, KB 397782 | **Exclu en cluster unique.** On ne peut pas répartir les nœuds entre les sites comme un seul fault domain ; le cluster bascule comme un seul protection group — sauf si la Continuous Availability s'applique (ligne suivante). |
| Continuous Availability de VCF Operations, entre fault domains | < 10 ms RTT en moyenne, pics ≤ 20 ms sur 20 s ; witness ↔ fault domains ≤ 30 ms | modèle Continuous Availability VCF Operations (design library), KB 444771 | **Seulement sous 10 ms.** Le cluster Ops peut être réparti entre les deux instances au lieu d'être répliqué — voir l'option CA plus bas. |
| Multisite NSX à manager unique avec reprise automatique du plan de gestion | ≤ 10 ms RTT | design multisite NSX | **Hors sujet ici** : le design VCF à deux instances utilise Federation, pas un seul cluster NSX Manager étiré, et un DCI régional est de toute façon souvent à cette limite ou au-delà. |
| Nœuds VCF Operations → endpoints (vCenter, NSX, SDDC Manager) | < 50 ms RTT | KB 397782 | Correct. Après une bascule, Operations au site B peut continuer à collecter ce qui vit encore au site A. |
| NSX Federation, réseaux étirés (Global Manager ↔ locations) | ≤ 150 ms RTT | exigences NSX Federation | Correct, avec beaucoup de marge. |
| Nœuds VCF Operations → cloud proxies / collecteurs | < 300 ms RTT | sizing VCF Operations 9.x, KB 417347 | Correct. |
| vSphere Replication | pas de plafond de RTT ; asynchrone | Protection and Recovery 9.1 | Correct. Le RPO dépend de la bande passante et du taux de changement, pas de la latence ; le blueprint dit 15 minutes. |

C'est donc de la reprise après sinistre, pas de la haute disponibilité — avec l'exception que j'ai mentionnée : si le DCI reste sous 10 ms, VCF Operations lui-même peut fonctionner en Continuous Availability entre les deux instances. Le design de base, ce sont deux instances VCF, chacune avec son domaine de gestion, son SDDC Manager, son vCenter et son cluster NSX Manager, dans un seul fleet ; les composants du fleet vivent au site A et sont récupérés au site B. C'est exactement ce que décrit le blueprint *VCF Fleet Disaster Recovery* de 9.1, qui place le site de reprise « typiquement hors du rayon métro ».

### La topologie

![Deux zones en pointillés, site A instance protégée et site B instance de reprise, avec une bande DCI entre elles indiquée comme supérieure à 5 ms de RTT. Dans chaque site, de haut en bas : un routeur de fabrique, un cluster d'edges contenant un Tier-0 étiré (primaire en A, secondaire en B) et un Tier-1 étiré DR-only, un seul segment overlay dessiné comme une barre continue à travers les deux sites portant le cluster Operations, Ops for Networks, le service runtime d'Automation, l'instance de management services et le serveur de licences en A, et des cibles de réplique éteintes, une nouvelle instance Automation et l'instance de management services du site B en B. En dessous, un VLAN de gestion des VM propre à chaque site avec SDDC Manager, vCenter, cluster NSX Manager, cloud proxy, l'appliance Protection and Recovery et le Global Manager (actif en A, standby en B). vSphere Replication traverse le DCI entre les nœuds Operations ; la synchronisation des Global Manager et l'appairage des sites le traversent aussi ; les emplacements de sauvegarde SFTP des deux sites sont reliés.](/images/fleet-overlay-dr/fig-02-dr-topology.svg)

| Composant | Où il vit | Comment il revient (blueprint 9.1) | Mobilité IP requise |
|---|---|---|---|
| Cluster VCF Operations (nœuds primary, replica, data) | segment overlay étiré, site A | Protection and Recovery : vSphere Replication et un recovery plan — ou Continuous Availability entre les deux sites quand le DCI est sous 10 ms (voir plus bas) | **Oui** |
| VCF Operations for Networks (plateforme ; collecteurs propres à chaque site) | segment overlay étiré, site A | Protection and Recovery : réplication et un recovery plan | **Oui** |
| VCF Automation (cluster de service runtime) | segment overlay étiré, site A | **Non répliqué.** Nouvelle instance au site B, dernière sauvegarde restaurée | Non |
| Composants VCF management services : Identity Broker, fleet lifecycle, software depot, Salt RaaS, log management | instance de management services de chaque site | **Non répliqués.** Chacun restauré sur l'instance de management services existante du site B (un seul de chaque par instance de management services) | Non |
| Cloud proxies / collecteurs | VLAN de gestion des VM, un jeu par site | Non répliqués ; le site B a déjà les siens | Non |
| Serveur de licences | segment overlay étiré, site A | Pas dans le blueprint ; à traiter comme un redéploiement | Non |
| SDDC Manager, vCenter, cluster NSX Manager (Local Manager) | VLAN de gestion des VM, par instance | Restent où ils sont ; chaque instance garde les siens | Non |
| NSX Global Manager | VLAN de gestion des VM ; actif en A, standby en B | C'est vous qui promouvez le standby | Non |
| Appliance Protection and Recovery | VLAN de gestion des VM, une par instance, déploiement Light, domaine de gestion | Appairée A ↔ B | Non |

La ligne qui surprend le plus de monde, c'est Automation. Dans le blueprint 9.1, VCF Automation n'est *pas* protégé par SRM. On déploie une nouvelle instance sur le site de reprise et on y restaure une sauvegarde, et le blueprint dit en toutes lettres que cela ne demande aucune mobilité IP. Même chose pour Identity Broker et le reste des management services. Le segment étiré est là pour VCF Operations et Ops for Networks. Je mettrais quand même Automation dessus — la nouvelle instance peut remonter avec le même FQDN et, si vous gardez les adresses libres au site B, les mêmes IP, ce qui vous épargne un changement DNS et un certificat — mais c'est une commodité, pas ce qui fait marcher la reprise.

### Le côté NSX, concrètement

![Objets NSX Federation du segment étiré : un Global Manager actif au site A et un standby au site B reliés par une ligne de synchronisation ; le cluster Local Manager de chaque site ; un routeur ToR par site en eBGP avec un Tier-0 étiré actif/actif (primaire en A, secondaire en B avec AS-path prepend) ; des tunnels RTEP Geneve entre les deux clusters d'edges ; un Tier-1 étiré DR-only sous chaque Tier-0 ; une barre de segment unique à travers les deux sites. Un chemin vert épais marque tout le trafic nord-sud entrant et sortant par le site A ; un chemin gris pointillé au site B est dormant jusqu'au Network Recovery. Un bandeau liste les trois étapes après une panne du site A.](/images/fleet-overlay-dr/fig-07-nsx-federation.svg)

| Objet | Où on le crée | Réglages qui comptent |
|---|---|---|
| Global Manager | Actif au site A, standby au site B ; les deux sur le VLAN de gestion des VM | Promotion manuelle uniquement ; GM ↔ location ≤ 150 ms pour les réseaux étirés |
| Local Manager | Le cluster NSX Manager déployé par VCF dans chaque instance | Enregistré comme Location A / Location B sur le Global Manager |
| Cluster d'edges | Un par instance, dans le domaine de gestion | Deux nœuds ; interface RTEP sur son propre VLAN ; MTU RTEP de 1 700 de préférence (1 500 minimum) à travers le DCI |
| Tier-0 | Global Manager, étiré | HA actif/actif ; Location A primaire, Location B secondaire ; eBGP vers la fabrique dans les deux locations ; route map avec AS-path prepend ×2 sur les voisins de la location secondaire ; annonce les segments connectés |
| Tier-1 | Global Manager, étiré | DR-only (pas de service router, pas de service à état) ; même étendue que le Tier-0, donc suit sa location primaire. N'ajoutez des services que s'il vous faut le load balancer NSX devant Operations |
| Segment | Global Manager, sur la transport zone overlay de chaque location | Même sous-réseau et même passerelle dans les deux locations ; un VNI par location assemblé sur RTEP ; porte les nœuds Operations, Ops for Networks, Automation et le serveur de licences |

- **Federation.** Cluster Global Manager actif au site A, standby au site B. Chaque site garde son Local Manager (le cluster NSX Manager déployé par VCF) et son propre cluster d'edges dans le domaine de gestion. La limite GM ↔ location est de 150 ms pour les réseaux étirés, il y a de la marge.
- **Tier-0 étiré, actif/actif, primaire en A, secondaire en B.** La location secondaire annonce aussi les préfixes étirés ; c'est vous qui les rendez moins attractifs — le design VCF le fait avec une route map qui préfixe deux fois l'AS local sur les voisins BGP de la location secondaire — pour que le trafic entrant arrive en A tant que A est debout. Tout le trafic sortant part par la location primaire.
- **Tier-1 étiré, DR-only (sans service à état), même étendue que le Tier-0,** donc il suit simplement la location primaire du Tier-0. Si vous voulez le load balancer NSX devant Operations, le Tier-1 porte des services, et le blueprint veut un load balancer de secours avec la même configuration, prêt sur le site de reprise.
- **Un seul segment overlay étiré** sur ce Tier-1, créé sur le Global Manager, même sous-réseau et même passerelle dans les deux locations. Federation assemble un VNI par location en un seul domaine de diffusion à travers les tunnels RTEP entre les deux clusters d'edges. C'est du Geneve sur le DCI, donc donnez de la marge de MTU au chemin : le design VCF préfère 1 700 octets pour le trafic RTEP, avec 1 500 comme plancher.
- **Les VLAN de gestion des VM ne sont pas étirés,** et n'ont pas besoin de l'être. SDDC Manager, vCenter et les Local Manager ne bougent jamais ; seul le segment overlay porte ce qui bouge.

En régime établi, rien de visible pour l'utilisateur ne traverse le DCI. Ce qui le traverse : vSphere Replication, la synchronisation des Global Manager (GM ↔ GM et GM ↔ LM), le trafic de contrôle RTEP et le trafic BUM du segment. Assurez-vous que les interfaces RTEP et les ports VMkernel de réplication ont les routes, la MTU et les règles de pare-feu qu'il faut avant de déclarer le design terminé.

### Le côté Protection and Recovery

Les exigences du blueprint 9.1 sont assez précises pour être reprises telles quelles :

- une instance Protection and Recovery par instance VCF, dans le domaine de gestion, déploiement *Light*, sur le réseau de gestion des VM, IP statique, enregistrements A et PTR, NTP plutôt que VMware Tools (VCF-PB-DR-REQD-001 à 007)
- un port group, un VLAN et une interface VMkernel dédiés par hôte ESX pour le trafic de réplication, en adressage statique (REQD-008 à 011)
- vSphere Replication comme méthode — indépendante du stockage, pas de storage replication adapter à maintenir — ce qui veut dire que toutes les VM de gestion protégées sont dans le même cluster (REQD-013)
- la politique de réplication des VM de gestion : **pas de quiescing de l'OS invité** (les répliques sont cohérentes au crash ; toutes les appliances de gestion ne tolèrent pas le quiescing), **compression réseau activée**, **RPO 15 minutes**, **trois copies point-in-time sur 24 heures** (REQD-015 à 018)
- un ordre de démarrage priorisé pour les nœuds Operations et les nœuds Ops for Networks, tenu à jour quand vous ajoutez des nœuds (REQD-019, 020)
- chaque composant protégé configuré avec des serveurs DNS *et* NTP des deux instances, pour qu'il résolve toujours et garde l'heure après une bascule (REQD-021, 022)
- un emplacement de sauvegarde configuré dans VCF Operations pour les deux instances, avec les sauvegardes du site A disponibles sur le serveur SFTP du site B sous le chemin qui y est configuré — même serveur, ou réplication des sauvegardes (REQD-023, 024)
- un RTO de 4 heures ou moins (REQD-014)

La dernière paire est celle qu'on oublie. Automation et les management services sont *restaurés*, pas répliqués. Si votre cible SFTP est une VM au site A, il n'y a rien à restaurer au site B.

Une note opérationnelle de plus : Protection and Recovery gère son propre cycle de vie (pas VCF Operations). Le blueprint recommande d'installer son management pack et de pointer les adaptateurs vSphere Replication et Site Recovery vers le cloud proxy ou le collector group local de chaque instance.

### Des répliques cohérentes au crash d'un cluster analytique distribué

C'est la partie du design qui décide de votre RPO, alors ça vaut la peine de détailler ce qui se passe vraiment quand SRM réplique VCF Operations.

**Ce qui est répliqué.** Le cluster Operations n'est pas une appliance avec une base de données ; ce sont trois nœuds ou plus (primary, replica, data) qui partagent un seul stockage analytique distribué, chaque objet étant écrit sur deux nœuds. vSphere Replication, de son côté, travaille une machine virtuelle à la fois. Chaque nœud est répliqué indépendamment, sur son propre cycle de 15 minutes, et rien ne coordonne ces cycles. Il n'y a pas de groupe de cohérence : quand les répliques arrivent au site B, le disque du nœud primary reflète un instant, celui du nœud replica un instant légèrement différent, ceux des nœuds data d'autres encore.

**Pourquoi « cohérent au crash » est le mot honnête.** Chaque réplique ressemble à ce que vous obtiendriez en coupant l'alimentation de ce nœud à cet instant — complète, mais avec ce qui était en vol pas encore écrit. Et d'un nœud à l'autre, les instants ne sont pas les mêmes. Sur un cluster de bases de données, c'est exactement la situation pour laquelle son propre code de récupération est conçu : au premier démarrage au site B, le cluster se comporte comme après une coupure de courant généralisée, réconcilie les stockages entre nœuds, et remonte. Le blueprint s'appuie là-dessus. Il désactive *volontairement* le quiescing de l'OS invité (REQD-015) — mettre un nœud analytique en pause au milieu d'une écriture peut le bloquer, et ça ne réglerait de toute façon pas le problème entre nœuds.

**Le filet de sécurité que le blueprint vous donne.** Deux choses : trois copies point-in-time conservées sur 24 heures (REQD-018), et le RPO de 15 minutes (REQD-017). Si la réplique la plus récente remonte avec un cluster qui refuse de démarrer proprement, vous rebasculez sur le point-in-time précédent plutôt que de déboguer un stockage à moitié réconcilié. Et si aucun point-in-time ne fonctionne, le dernier recours est le même que pour le reste du fleet — une nouvelle instance restaurée depuis une sauvegarde — une raison de plus pour que la cible SFTP soit joignable depuis le site B.

**Ce que ça coûte sur le lien.** Deux budgets séparés :

- *La synchronisation initiale.* Chaque nœud Operations fait quelques centaines de gigaoctets de disque ; un cluster medium avec sa plateforme Ops for Networks dépasse largement le téraoctet. À travers le DCI, cette première copie complète prend des heures au mieux et peut prendre des jours sur un lien étroit — et tant qu'elle n'est pas terminée, vous n'avez aucune protection. Amorcez les répliques (la première synchronisation peut se faire à partir d'une copie des fichiers de VM placée au site B) ou planifiez-la dans une fenêtre où elle ne concurrencera pas la production.
- *Le régime établi.* Un RPO de 15 minutes signifie que chaque tranche de 15 minutes de changements sur chaque nœud doit traverser le DCI en moins de 15 minutes, avec de la marge pour les reprises. Les nœuds analytiques écrivent en permanence, donc mesurez le taux de changement sur le vrai cluster — vSphere Replication l'affiche par VM — plutôt que de le deviner, et gardez la compression réseau activée (REQD-016) : elle réduit nettement l'empreinte de ce type de données, au prix d'un peu de CPU sur les hôtes source.

**Peut-on resserrer le RPO ?** vSphere Replication en 9.1 descend à 5 minutes, et plus bas avec le droit d'usage Live Recovery / Advanced Cyber Compliance. Techniquement rien ne l'empêche, mais un RPO plus serré multiplie le trafic en régime établi et n'améliore que marginalement le tableau « tous les nœuds à peu près au même instant » — les nœuds ne sont toujours pas coordonnés. Les 15 minutes du blueprint sont un équilibre délibéré, et pour une plateforme de supervision, quinze minutes de métriques perdues sont généralement une perte acceptable. Si ce n'est pas le cas, c'est le signal pour regarder l'option Continuous Availability ci-dessous plutôt que le calendrier de réplication.

### L'option : Continuous Availability de VCF Operations au lieu de la réplication

Le blueprint Fleet DR 9.1 laisse une porte ouverte : la Continuous Availability (CA) de VCF Operations peut être utilisée entre les sites si les exigences de latence sont respectées. La CA n'est pas un mécanisme de reprise — c'est un modèle de disponibilité différent pour le cluster Operations, et elle remplace la réplication pour ce seul composant.

![Deux instances VCF côte à côte, chacune un fault domain VCF Operations : le nœud primary et les nœuds data au site A, le primary replica et le même nombre de nœuds data au site B, tous sur le segment overlay étiré avec une flèche d'appariement synchrone entre les fault domains. Un nœud witness dans un troisième site est relié aux deux fault domains par des liens routés étiquetés 30 ms. Operations for Networks est toujours répliqué avec vSphere Replication. Chaque site a son propre collector group et son cloud proxy sur son VLAN de gestion. Un bandeau liste ce qui change par rapport à la réplication.](/images/fleet-overlay-dr/fig-05-ops-ca.svg)

**Ce que c'est.** Le cluster analytique est divisé en deux fault domains, un par instance VCF, et chaque objet vit sur un nœud primaire dans un fault domain et un nœud réplica dans l'autre. Un witness dans un troisième emplacement départage quand les fault domains se perdent de vue : le côté qui atteint encore le witness reste en ligne, l'autre s'arrête. On peut perdre un fault domain entier sans perte de données. Perdez des paires de nœuds dans les deux et vous pouvez perdre des données pour de bon ; perdez les deux fault domains et vous restaurez depuis une sauvegarde.

**Exigences** (modèle CA de la design library, VCF-OPS-REQD-CA-001 à 009 ; KB de dimensionnement 397782 ; KB 444771) :

| Exigence | Valeur |
|---|---|
| Latence entre fault domains | < 10 ms RTT en moyenne, pics jusqu'à 20 ms sur des intervalles de 20 secondes |
| Latence à l'intérieur d'un fault domain | < 5 ms RTT |
| Latence witness ↔ chaque fault domain | ≤ 30 ms RTT |
| Placement du witness | Un troisième emplacement, joignable en L3 depuis les deux fault domains ; 2 vCPU, 8 Go ; un seul witness par cluster |
| Disposition des nœuds | Même nombre de nœuds, même taille, dans chaque fault domain ; au plus 1 / 4 / 8 / 6 nœuds par fault domain pour Small / Medium / Large / Extra Large |
| Réseau | Nœuds analytiques des deux fault domains sur le même VLAN et le même sous-réseau IP — exactement ce que le segment overlay étiré vous donne entre deux instances VCF ; la design library liste le segment NSX étiré comme placement supporté pour la CA |
| Capacité | Environ le double du calcul et du stockage d'un cluster mono-site |
| Déploiement | Partir d'une instance Simple ou High Availability, déployer la seconde instance VCF, déployer les nœuds réplicas depuis le template et le witness au troisième site ; si vous partez de High Availability, le primary replica est reconfiguré en nœud data |
| Collecte | Un collector group par instance VCF, adaptateurs locaux sur le groupe local, anti-affinité DRS sur les collecteurs ; un cloud proxy par instance |
| Certificat | Le SAN doit inclure le FQDN de chaque nœud du cluster (et celui du load balancer, si vous en utilisez un) |

**Comment ça s'intègre.** La CA vit dans la fenêtre que l'échelle de latence laisse entre la limite du cluster étiré et celle de Federation : plus de 5 ms (donc pas de cluster unique, pas de vSAN étiré) mais moins de 10 ms (pour que les fault domains Ops puissent encore s'apparier). Un DCI régional à 6–9 ms se qualifie ; un à 12 ms non, et le KB sur les alertes de latence CA est clair : la CA cesse de se comporter correctement quand les seuils sont dépassés de façon répétée. Le troisième site pour le witness est l'autre verrou. Il peut être petit, mais ce doit être un domaine de panne distinct avec une joignabilité routée vers les deux instances.

Si les deux conditions tiennent, le design change à un seul endroit :

- **VCF Operations sort du recovery plan.** Les deux fault domains tournent en permanence ; perdez le site A et le fault domain du site B continue de servir les utilisateurs sans bascule, sans RPO sur les données d'Operations et sans changement d'adresse. Vous redéployez les nœuds perdus sur le site survivant ensuite.
- **Il vous faut toujours le segment étiré,** pour une seconde raison désormais : la CA veut un seul sous-réseau pour les deux fault domains, et le segment est le moyen pour deux instances VCF d'en partager un.
- **Network Recovery reste l'étape zéro.** Les nœuds Ops du site B sont vivants, mais leur chemin nord-sud dépend toujours de la location primaire du Tier-0 étiré. Tant que le Global Manager n'est pas promu et la location B rendue primaire, personne hors du segment ne peut les atteindre. Les étapes 1–3 du runbook ne changent pas.
- **Ops for Networks n'est pas couvert par la CA** et reste sur vSphere Replication avec son recovery plan ; Automation et les management services sont toujours restaurés depuis une sauvegarde ; l'exigence SFTP est la même.
- **Le coût en régime établi se déplace sur le DCI.** Le trafic de réplication devient un trafic d'appariement continu entre les fault domains, et la latence doit tenir tout le temps, pas seulement pendant une bascule. Surveillez-la — le KB sur l'alerte de latence personnalisée existe parce que les clusters sortent de la spec sans bruit.

Le choix entre les deux modèles est donc d'abord une question de latence, ensuite une question de budget. Sous 10 ms, avec un troisième site pour y mettre le witness, la CA vous offre un RPO nul et un RTO nul sur le seul composant dont dépend le reste du fleet, au prix de son doublement. Au-delà de 10 ms, la réplication est la seule option et 15 minutes est le RPO documenté.

## Partie 3 — Le jour J : le runbook, dans l'ordre

![Trois couloirs d'étapes numérotées. Couloir NSX : promouvoir le Global Manager standby, lancer Network Recovery pour rendre la location B primaire pour le Tier-0 et le Tier-1 étirés, vérifier que le préfixe est annoncé depuis B. Couloir Protection and Recovery : exécuter le recovery plan en mode disaster recovery, ordre de priorité Operations primary, replica, data, puis Operations for Networks, puis une invite pour remonter les collecteurs du site B. Couloir VCF Operations : déployer une nouvelle instance VCF Automation en B et restaurer la dernière sauvegarde, restaurer les composants des management services sur l'instance de B, valider et planifier la reprotection. Une note latérale explique pourquoi le réseau passe en premier et que seuls les nœuds Operations ont besoin de mobilité IP.](/images/fleet-overlay-dr/fig-03-failover-runbook.svg)

**Réparez le réseau d'abord.** C'est la partie qui piège ceux qui supposent qu'« étiré » veut dire « automatique ». Non. Le KB 441764 est direct : les passerelles étirées de NSX Federation ne basculent pas d'elles-mêmes entre locations, et une panne complète du cluster d'edges de la location primaire est une interruption du plan de données jusqu'à ce que quelqu'un lance le workflow Network Recovery. Le Global Manager ne bascule pas tout seul non plus (KB 444980).

1. **Promouvoir le Global Manager standby** au site B (*System › Location Manager › Make Active*). Ça prend quelques minutes ; le Local Manager du site B continue de commuter entre-temps.
2. **Lancer Network Recovery** sur le Global Manager désormais actif : choisir la location B comme nouvelle primaire pour le Tier-0 étiré. Un Tier-1 DR-only de même étendue le suit ; un Tier-1 ayant sa propre étendue, vous le choisissez à part. Tant que ce n'est pas fait, le segment existe sur les hôtes du site B mais n'a aucun chemin nord-sud — le KB 412806 documente exactement ce symptôme lors d'un test de bascule, le trafic essayant toujours de sortir par le site mort.
3. **Vérifier le préfixe.** Le Tier-0 du site B annonce maintenant le sous-réseau du segment comme primaire. Le DNS ne change pas : mêmes adresses, mêmes noms.

**Puis le recovery plan,** en mode disaster recovery. (La planned migration, c'est le même plan avec une synchronisation finale ; c'est celle qu'on utilise pour les déplacements de maintenance.)

4. Le protection group contient le cluster VCF Operations et la plateforme Ops for Networks. Ils démarrent au site B, sur le même segment, depuis la dernière réplique. (Avec l'option CA, le protection group ne contient qu'Ops for Networks — le fault domain Operations du site B tourne déjà.)
5. Priorité de démarrage : Operations primary, puis replica, puis data ; plateforme Ops for Networks, puis ses collecteurs. (La validated solution 9.0, écrite quand l'appliance de fleet management était une VM répliquée à part, démarre cette VM en premier ; en 9.1, le service de fleet lifecycle est restauré sur l'instance de management services — étape 8.)
6. Une invite du plan vous rappelle d'allumer ou de confirmer le cloud proxy et le collecteur du site B, et de repointer tout adaptateur épinglé à un collecteur du site A. Seuls les endpoints du site B sont joignables à ce stade ; sous 50 ms, Operations en B continuerait volontiers à collecter ce qui reste en A.

**Puis ce qui n'est pas répliqué.**

7. **VCF Automation.** Depuis VCF Operations au site B, *Fleet Management › Lifecycle › VCF Management Components* : déployer une nouvelle instance Automation — sur le segment étiré, même FQDN, mêmes IP si vous les avez réservées — puis *Backup and Restore › Restore* depuis la dernière sauvegarde du cluster. L'instance du site A doit être éteinte, et il vous faut une sauvegarde saine connue. C'est à ça que servait l'exigence SFTP.
8. **Composants des management services.** Restaurer Identity Broker, le software depot et le reste sur l'instance de management services existante du site B. La note du blueprint compte ici : une seule instance de chaque composant par instance de management services, donc le site B ne doit pas déjà faire tourner son propre Identity Broker ou son propre dépôt.
9. **Valider, puis préparer le retour.** Connexions via Identity Broker, adaptateurs au vert, organisations et VPC d'Automation visibles. Quand le site A revient, ré-enregistrer son Global Manager comme standby (il revient partiellement actif et doit être rétrogradé, sinon conflits), reprotéger dans l'autre sens, et planifier la planned migration de retour.

### Ce que la latence fait le jour J

- **Les étapes 1–3** sont du plan de contrôle au site B ; la latence vers un site A mort n'a aucune importance.
- **Les étapes 4–6** sont locales au site B. La réplique y était déjà ; le RPO de 15 minutes est ce qu'on perd, et le RTT n'y ajoute rien.
- **Le hairpin est un risque de planned migration, pas de sinistre.** Déplacez Operations en B pendant que A est sain en laissant le Tier-0 primaire en A, et chaque flux nord-sud d'Operations passe par A — environ deux fois le RTT entre les sites sur chaque appel d'adaptateur et chaque clic (20–24 ms sur un DCI à 10–12 ms). Changez la location primaire dans la planned migration aussi.
- **Les collecteurs Ops for Networks et les cloud proxies** tolèrent des centaines de millisecondes ; rien à faire.
- **Les restaurations (étapes 7–8)** lisent la cible SFTP. Au site B, ou en A et répliquée : c'est local. En A et non répliquée : vous vous êtes arrêté à l'étape 7.

## Ce que j'écrirais dans le document de design

1. **Différer Operations et Automation au bring-up** et les poser sur le segment overlay depuis l'Installer. La page Lifecycle d'Operations ne sait pas mettre Automation sur un segment.
2. **Un seul segment overlay étiré, NSX Federation, un Tier-1 DR-only, un Tier-0 actif/actif avec A primaire.** Écrire le workflow Network Recovery comme étape zéro du plan de reprise et le répéter ; les passerelles ne bougeront pas toutes seules.
3. **Deux instances, pas un cluster étiré.** Passé 5 ms de RTT, vSAN étiré est hors jeu. Concevoir pour un RPO de 15 minutes et un RTO de 4 heures ou moins sur les composants répliqués, et l'écrire.
4. **Répliquer Operations et Ops for Networks seulement** — ou, si le DCI est sous 10 ms et que vous avez un troisième site pour le witness, faire tourner Operations en Continuous Availability et ne répliquer qu'Ops for Networks. Dans les deux cas, Automation, Identity Broker et les services du fleet reviennent d'une sauvegarde : budgétez le temps et amenez les sauvegardes SFTP au site B.
5. **DNS et NTP des deux sites sur chaque composant protégé,** avant le premier test.
6. **Tester avec un réseau de test isolé, puis avec une planned migration,** et inclure le redéploiement-restauration d'Automation dans le test. C'est l'étape la plus lente et celle qui a le plus de préconditions.

Rien d'exotique là-dedans. C'est le design SRM à deux sites qu'on construit depuis des années, avec deux différences faciles à rater : le segment étiré est un objet Federation qu'un humain doit déplacer, et la moitié du fleet se restaure au lieu de se répliquer.

## Sources

- [Deploy Deferred Components on NSX Overlay Segments (VCF 9.1)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/deployment/deploying-a-new-vmware-cloud-foundation-or-vmware-vsphere-foundation-private-cloud-/deploying-vcf-operations-and-vcf-automation-on-custom-networking/deploy-vcf-operations-and-vcf-automation-on-nsx-segments.html)
- [Deploying Deferred Components on Custom Networking (VCF 9.1)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/deployment/deploying-a-new-vmware-cloud-foundation-or-vmware-vsphere-foundation-private-cloud-/deploying-vcf-operations-and-vcf-automation-on-custom-networking.html)
- [Start a New VCF Fleet or VCF Instance Deployment — assistant de l'Installer (VCF 9.1)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/deployment/deploying-a-new-vmware-cloud-foundation-or-vmware-vsphere-foundation-private-cloud-/deploy-a-new-vcf-fleet-or-a-new-vcf-instance.html)
- [VCF Management Dedicated VLAN and NSX Overlay Segment Network Model (design library)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/design/design-library/fleet-level-components-networking-detailed-design/logical-application-virtual-network-design-for-vmware-cloud-foundation.html)
- [VCF Management Dedicated VLAN and NSX Stretched Overlay Segment Network Model (design library)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/design/design-library/fleet-level-components-networking-detailed-design/fleet-level-components-on-stretched-nsx-overlay-segment-model.html)
- [VCF Fleet Disaster Recovery — design blueprint (VCF 9.1)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/design/design-blueprints-for/security-modernization/vcf-disaster-recovery.html) et ses [Design Elements](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/design/design-blueprints-for/security-modernization/vcf-disaster-recovery/design-elements-for-vcf-disaster-recovery.html)
- [Site Protection and Disaster Recovery for VMware Cloud Foundation — validated solution (9.x)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vvs/9-X/site-protection-and-disaster-recovery-for-vmware-cloud-foundation.html), dont [Detailed Design](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vvs/9-X/site-protection-and-disaster-recovery-for-vmware-cloud-foundation/detailed-design-for-site-protection-and-disaster-recovery.html), [Network Design](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vvs/9-X/site-protection-and-disaster-recovery-for-vmware-cloud-foundation/detailed-design-for-site-protection-and-disaster-recovery/network-design-for-site-protection-vvs.html) et [Initiate a Disaster Recovery of VCF Automation](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vvs/9-X/site-protection-and-disaster-recovery-for-vmware-cloud-foundation/operational-guidance-for-site-protection-vvs/failover-of-the-sddc-management-applications/perform-disaster-recovery-of-the-sddc-management-applications/initiate-a-disaster-recovery-of-vcf-automation-for-site-protection-and-disaster-recovery-for.html)
- [Protection and Recovery 9.1 Release Notes](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/protection-and-recovery/9-1/release-notes/protection-and-recovery-91-release-notes.html) · [KB 444864 — transition depuis SRM et vSphere Replication autonomes](https://knowledge.broadcom.com/external/article/444864/vcf-protection-and-recovery-91-transitio.html)
- [KB 397782 — VCF Operations 9.0 sizing guidelines (latence entre nœuds)](https://knowledge.broadcom.com/external/article/397782/vcf-operations-90-sizing-guidelines.html) · [KB 417347 — latence vers le cloud proxy](https://knowledge.broadcom.com/external/article/417347/error-failed-to-propagate-configuration.html)
- [Continuous Availability VCF Operations Model (design library, VCF 9.1)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/design/design-library/vcf-operations-design/continuous-availability-vcf-operations-model.html) · [KB 444771 — seuils d'alerte de latence entre nœuds et fault domains VCF Operations](https://knowledge.broadcom.com/external/article/444771/custom-alert-for-network-latency-between.html)
- [KB 441764 — les passerelles étirées ne basculent pas automatiquement entre locations dans NSX Federation](https://knowledge.broadcom.com/external/article/441764/stretched-gateways-do-not-automatically.html) · [KB 444980 — la bascule du Global Manager est manuelle](https://knowledge.broadcom.com/external/article/444980/nsx-global-manager-failover-process-and.html) · [KB 412806 — le trafic sort toujours par le site primaire après bascule](https://knowledge.broadcom.com/external/article/412806/federation-disaster-recovery-failover-te.html)
- [KB 443842 — support brownfield d'Operations et Automation sur segments NSX VLAN après mise à niveau vers 9.1](https://knowledge.broadcom.com/external/article/443842/vcf-91-brownfield-upgrade-support-for-vc.html)
- [vStellar — VCF 9.1 home lab part 10 : deploy VCF Automation (note sur le placement en Day-2)](https://vstellar.com/2026/06/vcf-9-1-home-lab-series-part-10-deploy-and-configure-vcf-automation/)
