---
title: "Les super metrics dans VCF Operations : un seul chiffre à partir de plusieurs, et quand les métriques temps réel font mieux"
date: 2026-10-28
description: "Une super metric transforme les valeurs de nombreux objets en un KPI stocké sur l'objet où la question se pose. Ce billet couvre le langage de formule, la règle de profondeur qui casse la plupart des premiers essais, la clause where, ce que les super metrics coûtent à un cluster VCF Operations, et en quoi les métriques temps réel de VCF 9.1 diffèrent : des données à 2 secondes pour une fenêtre de dépannage, pas un KPI stocké."
summary: "Les questions des opérateurs — le pire CPU ready d'un cluster, combien de VM tournent à chaud — ne sont pas des métriques simples. Une super metric est la formule qui y répond, stockée comme une métrique native. La syntaxe, la règle de profondeur, la clause where, des exemples prêts à l'emploi, le coût en dimensionnement, et une comparaison côte à côte avec les métriques temps réel de VCF 9.1."
tags: ["VCF 9.1", "VCF Operations", "Super metrics", "Métriques temps réel", "PromQL", "Holodeck"]
categories: ["Notes"]
showToc: true
tocOpen: false
images: ["/images/super-metrics/fig-01-one-kpi.svg"]
params:
  seoDescription: "Super metrics de VCF Operations : syntaxe, règle de profondeur, clause where, coût en dimensionnement, et quand les métriques temps réel de VCF 9.1 font mieux."
---

Les questions qu'on me pose sur un cluster ne sont pas des métriques simples. *Quelle VM attend le plus le CPU ? Combien de VM tournent à chaud ? Les hôtes sont-ils chargés uniformément ?* Chaque objet de VCF Operations rapporte ses propres valeurs, et rien ne les additionne pour vous. Une **super metric** est la formule qui le fait : elle lit des objets liés, les réduit à une valeur, et stocke cette valeur sur l'objet où la question a été posée. À partir de là, elle se comporte comme n'importe quelle métrique native : tableaux de bord, alertes, rapports, API.

La plupart des équipes les sous-utilisent, souvent parce que le premier essai n'a rien affiché et que personne ne savait pourquoi. Ce billet est la version que j'aurais aimé lire en premier : le langage de formule, la règle de profondeur, la clause `where`, ce que les super metrics coûtent au cluster, et quand les nouvelles métriques temps réel de VCF 9.1 sont le meilleur outil.

*Octobre 2026 · VCF Operations 9.0 / 9.1 · syntaxe et chemins de menu tirés des guides Infrastructure Operations 9.0 et 9.1 sur Broadcom TechDocs*

## De plusieurs valeurs de VM à un KPI de cluster

![Quatre VM d'un cluster rapportent chacune leur CPU Ready ; une super metric assignée au cluster prend le maximum à la profondeur 2 et stocke « Worst VM CPU Ready 7,4 % » sur le cluster, qui alimente ensuite tableaux de bord, alertes et rapports. Un tableau liste des questions types : pire CPU ready (max), VM au-dessus de 60 % (count + where), mémoire invitée provisionnée (sum), répartition de charge des hôtes (min / max).](/images/super-metrics/fig-01-one-kpi.svg)

*À lire de gauche à droite. Les VM rapportent ; la super metric sur le cluster descend de deux sauts, prend le max, et le résultat est stocké sur le cluster à chaque cycle de collecte.*

Le cycle de vie compte cinq étapes, et c'est la troisième que l'on oublie :

1. **Définir la formule.**
2. **Assigner un type d'objet** : l'objet qui portera le résultat (le cluster, ci-dessus).
3. **L'activer dans une politique** qui s'applique à ces objets.
4. VCF Operations **la calcule et la stocke** à chaque cycle de collecte, comme une métrique native.
5. **La consommer** dans les tableaux de bord, vues, symptômes, rapports.

Le *Preview* de l'éditeur calcule la formule à la volée pour un objet et ne conserve rien. Seule une super metric activée dans la politique qui couvre un objet est persistée. Une super metric qui se valide et se prévisualise parfaitement, puis n'affiche rien dans une vue, manque donc presque toujours l'étape 3. La [KB 429899](https://knowledge.broadcom.com/external/article/429899/view-not-displaying-super-metrics-in-ari.html) de Broadcom décrit exactement ce cas.

Nommez-la comme une métrique native pendant que vous y êtes : majuscules à chaque mot, des espaces, l'unité à la fin — `Worst VM CPU Ready (%)`. Le nom apparaît dans les tableaux de bord, alertes et rapports, et la description est l'endroit pour noter pourquoi elle existe et qui en est responsable.

## Anatomie d'une formule

```text
avg( ${adaptertype=VMWARE, objecttype=VirtualMachine, metric=cpu|usage_average, depth=2,
       where=(${metric=sys|poweredOn} == 1)} as vmcpu )
```

À voix haute : *la moyenne d'utilisation CPU des machines virtuelles de l'adaptateur vCenter, deux sauts sous l'objet d'assignation, en ne comptant que les VM dont la propre métrique « allumée » vaut un.*

| Partie | Ce qu'elle fait | Règle à retenir |
|---|---|---|
| `avg` `count` `max` `min` `sum` `combine` | Fonction d'itération : réduit l'ensemble des valeurs retournées | `combine` fusionne toutes les valeurs en une seule série au lieu d'un seul nombre |
| `${ … }` | Entrée de ressource : une source de données | `${this, …}` ne lit que l'objet assigné lui-même |
| `adaptertype` | Type d'adaptateur ; `VMWARE` est l'adaptateur vCenter | Toujours obligatoire, même avec `objecttype=*` |
| `objecttype` | Type de ressource à parcourir | `objecttype=*` prend tous les types de cet adaptateur qui ont l'attribut |
| `metric` / `attribute` | `metric` est une instance ; `attribute`, toutes les instances | Une VM à 4 vCPU a quatre instances `cpu` |
| `depth` | Sauts le long de la chaîne de relations | 1 par défaut · négatif remonte aux parents · jamais 0 |
| `where` | Filtre les valeurs utilisées | Même objet seulement · opérande de droite numérique |
| `as alias` | Nomme l'entrée pour la réutiliser | Insensible à la casse · chaque nom défini une fois |

Dans l'éditeur, on tape rarement les clés à la main : quelques lettres puis *ctrl+espace*, et il complète types d'objets, métriques et propriétés. Faites-le plutôt que de copier des clés depuis un billet de blog, le mien inclus — celles de ce billet sont celles de la documentation, mais choisissez-les dans la liste pour qu'elles correspondent à votre version.

### Fonctions et opérateurs, en bref

Les fonctions d'itération prennent un ensemble (chaque VM, chaque instance vCPU) et le réduisent. Les fonctions simples transforment une valeur : `abs`, `ceil`, `floor`, `sqrt`, `pow(x, y)`, `log`, `log10`, `exp`, `rand()` et la trigonométrie. Les opérateurs sont ceux qu'on attend — `+ − * / %`, comparaisons, `&& || !` — plus deux à connaître :

- **Tableaux** : `min([x, y, z])` choisit entre des expressions.
- **Ternaire** : `condition ? si_vrai : si_faux`. La condition est convertie en nombre (tout sauf 0 est vrai) et *seule la branche choisie est évaluée*, ce qui rend sûre cette garde contre la division par zéro :

```text
${this, metric=cpu|demandmhz} as a != 0 ? 1/a : -1
```

Les opérateurs de chaîne (`equals`, `contains`, `startsWith`, `endsWith` et leurs négations `!`) existent aussi, mais uniquement dans une condition `where` — on ne calcule pas avec des chaînes.

## La profondeur : la règle qui casse la plupart des premiers essais

![Une chaîne verticale data center → cluster → hôte ESX → VM, la super metric assignée au cluster. Des flèches montrent la profondeur 1 vers l'hôte, 2 vers la VM et −1 vers le data center. Un tableau : cluster→hôtes 1, cluster→VM 2, cluster→data center −1, hôte→VM 1, datastore→VM −1, tout objet→lui-même via ${this}. Une note : choisir une métrique dans l'éditeur écrit depth=1.](/images/super-metrics/fig-02-depth.svg)

*Comptez les sauts le long de la chaîne de relations, en partant de l'objet auquel la super metric est assignée.*

La profondeur est la source de la plupart des super metrics fausses ou vides. Depuis le cluster, les hôtes sont à la profondeur 1, les VM à 2, et le data center à −1. Le piège est la valeur par défaut : quand on choisit une métrique dans l'éditeur, il écrit `depth=1`, et une formule de cluster censée lire les VM lit silencieusement les hôtes — ou rien du type nommé — tant qu'on ne la passe pas à 2.

Les datastores sont l'autre piège. Les VM sont *parents* d'un datastore : depuis un datastore, on atteint ses VM avec `depth=-1`. Et comme le cluster de datastores est lui aussi parent du datastore, VM → cluster de datastores ne se fait pas en une seule entrée : −1 + 1 = 0, et la profondeur 0 n'est pas valide.

![Deux VM reliées au datastore DS-01 dans un cluster de datastores. Un chemin direct en pointillé des VM vers le cluster de datastores est barré : −1 + 1 = 0, interdit. Repère 1 : super metric n° 1 sur le datastore, profondeur −1, agrège ses VM. Repère 2 : super metric n° 2 sur le cluster de datastores, profondeur 1, somme la super metric n° 1.](/images/super-metrics/fig-03-chain.svg)

*L'exemple même de la documentation : chaîner deux super metrics.*

| Étape | Assignée à | Formule | Résultat |
|---|---|---|---|
| SM n° 1 | Datastore | `count(${adaptertype=VMWARE, objecttype=VirtualMachine, metric=sys\|poweredOn, depth=-1, where=($value == 1)})` | VM allumées par datastore |
| SM n° 2 | Cluster de datastores | `sum(${adaptertype=VMWARE, objecttype=Datastore, metric=Super Metric\|sm_<id de la SM n° 1>, depth=1})` | VM allumées par cluster de datastores |

Dans l'éditeur, choisissez la SM n° 1 dans la liste des métriques pour la SM n° 2 plutôt que de taper sa clé. Une réserve : une VM avec des disques sur deux datastores est comptée sur les deux.

## La clause where : choisir les valeurs qui comptent

![Un cluster avec deux hôtes ESX et quatre VM ; VM2 et VM4 sont éteintes, grisées et exclues, VM1 rapporte 40 % et VM3 70 %. La formule avg sur les VM à la profondeur 2 avec where allumée égale 1 donne avg(40, 70) = 55 %. Un tableau de règles : ne lit que les métriques du même objet ; l'opérande de droite est un nombre ; opérateurs de chaîne permis ; $value est la valeur de l'entrée ; $value.isFresh() ignore les valeurs périmées.](/images/super-metrics/fig-04-where.svg)

*Deux VM sont éteintes. Elles rapportent quand même 0 % de CPU : sans filtre, la moyenne dit 27,5 % alors que les VM en marche sont à 55 %.*

La clause `where` teste chaque valeur avant qu'elle n'entre dans l'agrégation. Elle peut regarder une autre métrique du *même* objet — `where=(${metric=sys|poweredOn} == 1)` — ou la valeur de l'entrée elle-même avec `$value`, comme dans `where=($value > 60)`. Deux limites comptent en pratique :

- Elle ne peut pas regarder un autre objet. « Les VM au-dessus du SLA du cluster » ne s'exprime pas en une seule super metric, parce que le SLA appartient au cluster, pas à la VM.
- L'opérande de droite doit être un nombre : ni une autre super metric, ni une variable.

`$value.isFresh()` est celle à retenir quand des objets cessent de rapporter. Le dernier point d'une métrique est *frais* quand il s'est écoulé moins de temps que son intervalle de publication attendu ; `where=($value.isFresh())` écarte les dernières valeurs périmées de votre maximum.

### Alias, ternaires et objecttype=*

Trois fonctions pour les formules longues. Un **alias** permet d'écrire une entrée de ressource une fois et de la réutiliser ; ce ratio de répartition de charge des hôtes, tiré de la documentation, utilise la même entrée deux fois (et 0,0001 pour éviter la division par zéro) :

```text
(min(${adaptertype=VMWARE, objecttype=HostSystem, attribute=cpu|demand|active_longterm_load,
      depth=5, where=($value>=0)} as cpuload) + 0.0001) / (max(cpuload) + 0.0001)
```

Les noms d'alias sont insensibles à la casse, ne commencent pas par un chiffre, ne contiennent pas d'opérateur, chaque nom se définit une seule fois, et une entrée peut en porter plusieurs (`as a1 as a2`). La **garde ternaire** vue plus haut protège les divisions. Et **`objecttype=*`** agrège sur tous les types d'objets d'un adaptateur sans les lister — le type d'adaptateur reste obligatoire :

```text
count(${adaptertype=VMWARE, objecttype=*, attribute=badge|health, depth=1})
```

## Créer, consommer, automatiser

L'assistant se trouve sous **Infrastructure Operations → Configuration → Super Metrics** (9.0 ; *Operate → Administration → Configurations* en 9.1) : *Add*, nom et description, le type d'objet qui porte le résultat, la formule, l'unité, *Validate* et *Preview* sur un objet réel, puis la page des politiques — sélectionnez la politique des objets cibles et *Update*. Vérifiez ensuite là où elle vit : ouvrez un objet de ce type et regardez sous *All Metrics → Super Metric*. La liste des super metrics montre pour chacune ses types d'objets, sa formule et les politiques où elle est activée ; servez-vous-en pour confirmer l'étape 3 avant de bâtir des tableaux de bord dessus.

Une fois stockée, une super metric alimente tout widget qui prend une métrique, les vues de liste et de tendance, les rapports planifiés, et la chaîne que j'utilise le plus : un **symptôme** sur la super metric avec un seuil → une **définition d'alerte** → une notification ou une action. Elle peut être marquée comme KPI dans la politique, et elle figure dans le rapport d'audit système, un moyen rapide d'en faire l'inventaire.

Traitez-les comme du code. *Export* et *Import* dans la barre d'outils déplacent les définitions entre instances (revérifiez type d'objet et politique après import) ; l'OPS CLI scripte la même chose ; et l'API REST gère les définitions directement :

| Méthode | Chemin | Usage |
|---|---|---|
| `GET` | `/suite-api/api/supermetrics` | Lister (paramètres de recherche pris en charge) |
| `POST` | `/suite-api/api/supermetrics` | Créer : `{"name", "formula", "description"}` |
| `PUT` | `/suite-api/api/supermetrics` | Mettre à jour : l'`id` est requis |
| `GET` | `/suite-api/api/supermetrics/{id}` | Lire une définition |
| `DELETE` | `/suite-api/api/supermetrics/{id}` | Supprimer une définition |

Gardez les définitions exportées dans Git, à côté des tableaux de bord qui s'en servent.

## Six formules pour commencer

Toutes assignées au **cluster** sauf mention contraire ; profondeur 2 pour les VM, 1 pour les hôtes. Confirmez les clés de métriques dans l'éditeur.

| Nom | Formule | Unité |
|---|---|---|
| Worst VM CPU Ready (%) | `max(${adaptertype=VMWARE, objecttype=VirtualMachine, metric=cpu\|readyPct, depth=2})` | % |
| Avg CPU of Powered-On VMs (%) | `avg(${adaptertype=VMWARE, objecttype=VirtualMachine, metric=cpu\|usage_average, depth=2, where=(${metric=sys\|poweredOn} == 1)})` | % |
| Busy VM Count | `count(${adaptertype=VMWARE, objecttype=VirtualMachine, metric=cpu\|usage_average, depth=2, where=($value > 60)})` | nombre |
| Provisioned Guest Memory (GB) | `sum(${adaptertype=VMWARE, objecttype=VirtualMachine, metric=mem\|guest_provisioned, depth=2}) / 1048576` | Go |
| Host Load Spread (ratio) | `(min(${adaptertype=VMWARE, objecttype=HostSystem, attribute=cpu\|demand\|active_longterm_load, depth=1, where=($value>=0)} as l) + 0.0001) / (max(l) + 0.0001)` | ratio |
| Objects with Health (count), sur vCenter | `count(${adaptertype=VMWARE, objecttype=*, attribute=badge\|health, depth=1})` | nombre |

La métrique mémoire est en Ko, d'où la division. Le ratio de répartition vaut 1 quand les hôtes sont chargés uniformément et tend vers 0 quand un seul hôte fait tout le travail.

## Ce que coûtent les super metrics

Rien à déployer : les super metrics font partie du cluster analytique de VCF Operations. Ce dont elles ont besoin est en amont — les métriques d'entrée collectées par un adaptateur *et activées dans la politique* (plusieurs sont désactivées par défaut en 9.x), la super metric activée dans une politique précise couvrant les objets cibles, et un compte autorisé à modifier les deux.

Ce qu'elles coûtent, ce sont des séries de métriques. Chaque super metric activée produit une valeur stockée par objet sur lequel elle est active, et ces séries comptent dans les limites par nœud des [VCF Operations 9.0 Sizing Guidelines, KB 397782](https://knowledge.broadcom.com/external/article/397782/vcf-operations-90-sizing-guidelines.html) de Broadcom (pour 9.1.x, les limites ont déménagé sur [configmax.broadcom.com](https://configmax.broadcom.com)) :

| Taille de nœud | vCPU | Mémoire Go | Objets max (un nœud) | Métriques max (un nœud) |
|---|---|---|---|---|
| Extra small | 2 | 8 | 700 | 140 000 |
| Small | 4 | 16 | 10 000 | 1 600 000 |
| Medium | 8 | 32 | 30 000 | 5 000 000 |
| Large | 16 | 48 | 44 000 | 8 000 000 |
| Extra large | 24 | 128 | 100 000 | 20 000 000 |

Une règle empirique, la mienne et non celle de Broadcom : multipliez les super metrics par les objets concernés. Dix super metrics sur cent clusters ajoutent mille séries, du bruit. Les mêmes dix sur 2 000 hôtes en ajoutent 20 000, environ 1,3 % d'un nœud Small. Sur 50 000 VM, elles en ajoutent 500 000 — un tiers d'un nœud Small. C'est pourquoi les super metrics ont leur place sur les clusters et les hôtes, pas sur chaque VM, et pourquoi le guide de bonnes pratiques d'Aria Operations recommande de ne les appliquer qu'aux objets pertinents et d'éviter les calculs lourds sur les objets de niveau « world ».

Trois habitudes les gardent peu coûteuses :

- **Préférez `metric=` à `attribute=`.** `attribute` fait une correspondance par motif ; en abuser ralentit la base de données et laisse des trous de plus d'un cycle de collecte dans *toutes* les super metrics ([KB 335252](https://knowledge.broadcom.com/external/article/335252/vmware-aria-operations-super-metric-data.html)).
- **Gardez la profondeur au nombre réel de sauts.** Une portée plus large lit plus d'objets à chaque cycle.
- **Retirez les super metrics inutilisées.** Elles continuent de calculer et de stocker tant qu'elles sont actives.

Le cluster lui-même a ses propres exigences, dont les super metrics héritent : latence du datastore sous 10 ms, un vCPU par cœur physique aux maximums, des nœuds à moins de 5 ms les uns des autres et tous de la même taille, pas de snapshots, et la HA ou la disponibilité continue qui divisent par deux la limite d'objets. L'espace disque et les IOPS viennent de la feuille de dimensionnement jointe à la KB.

## Quand les métriques temps réel font mieux

VCF 9.1 a ajouté les **métriques temps réel** : des données à 20 secondes par défaut, et des données esxtop à 2 secondes pour les hôtes via un réglage de politique. On les cite souvent dans la même phrase que les super metrics, et elles répondent à une autre question.

| | Super metrics | Métriques temps réel (VCF 9.1) |
|---|---|---|
| **La question** | Quelle est la valeur de ce KPI pour l'objet, dans le temps ? | Que se passe-t-il maintenant, seconde par seconde ? |
| **Granularité** | Chaque cycle de collecte, 5 minutes par défaut | 2 s (ESX Top) · 20 s (Standard) · 60 s pour les clusters et NSX |
| **Définies par** | Une formule, un type d'objet et une politique | Des paliers de collecte activés dans une politique |
| **Consultées dans** | Tableaux de bord, vues, rapports, alertes, API | Workbench → Real Time Analysis ; clients PromQL comme Grafana |
| **Exécutées sur** | Le cluster analytique de VCF Operations | Un service et un magasin temps réel dans VCF Management Services |
| **Idéales pour** | La surveillance et les alertes en continu | Une fenêtre de dépannage, puis désactivées |

Utilisez les deux : l'alerte sur la super metric vous dit qu'un cluster est en difficulté ; les données temps réel montrent quelle VM, et à quelle seconde. Les pics que les moyennes sur 5 minutes lissent sont exactement ce que le temps réel conserve (la [KB 444403](https://knowledge.broadcom.com/external/article/444403/understanding-peak-or-worst-performance.html) explique comment les deux granularités divergent).

![Les hôtes ESX et VM, vCenter, les nœuds de transport NSX et vSAN alimentent un service de métriques temps réel et un magasin de métriques temps réel dans VCF Management Services, déployés par instance VCF. Le Workbench de VCF Operations (Overview, Top N, Custom Query) et des clients PromQL comme Grafana lisent le magasin. Un tableau : hôtes ESX et VM rapportent à 2 s (environ 21 métriques esxtop), 20 s (Standard) et 5 minutes (historique par défaut).](/images/super-metrics/fig-05-realtime.svg)

*Un pipeline séparé. La collecte à 5 minutes, les super metrics et les tableaux de bord continuent de fonctionner comme avant.*

**Comment on les utilise.** Dans le Workbench, *Real Time Analysis* offre trois vues. *Overview* diffuse des données à 20 secondes, ou à 2 secondes quand on passe la vue en ESX Top sur un hôte ou une VM choisis. *Top N* classe les plus gros consommateurs sous une VM, un hôte, un cluster, un datacenter, un objet NSX ou vSAN — toujours à 20 secondes, même avec ESX Top activé. *Custom Query* accepte du PromQL ; une étiquette `feature` choisit la série :

```text
cpu.ready.HOST{feature='ESX_TOP'}          # données d'hôte à 2 secondes
cpu.ready.HOST{feature='TROUBLESHOOTING'}  # données d'hôte à 20 secondes
```

Le champ de requête n'a pas d'autocomplétion ; servez-vous de l'onglet d'aide *Metrics* pour trouver les noms internes exacts. Les plages ESX Top de plus de six heures renvoient une erreur, et les données à 2 secondes sont gourmandes : activez-les pour la fenêtre de dépannage, puis désactivez-les.

**Ce qu'elles produisent.** Trois paliers. *Essentials* est toujours actif : un ensemble choisi couvrant vCenter, ESX, NSX et vSAN. *Standard* ajoute des métriques vCenter à 20 secondes et exige le type de déploiement Large. *ESX Top* fournit des données esxtop à 2 secondes pour les hôtes et leurs VM. Les clusters et datacenters rapportent toutes les 60 secondes depuis le registre de statistiques de vCenter, les nœuds de transport NSX toutes les 60 secondes. La liste complète des métriques est une feuille de calcul, `VODAP-9.1-Metrics-List-Per-Provider.xlsx`, liée depuis la page [Real-Time Metrics Details](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/infrastructure-operations/configuring-policies/using-the-monitoring-policy-workspace/policy-workspace/real-time-metrics-policy-workspace.html).

**Ce qu'elles exigent.** VCF 9.1, idéalement les derniers binaires 9.1.1. Deux composants — *Real-time metrics* et *Real-time metrics store* — ajoutés sous **Build → Lifecycle → VCF Management → Add Component**, sur chaque instance VCF à analyser (l'API de cycle de vie de la flotte fait la même chose avec le type de composant `OPS_DATA_PLATFORM`). La taille Large si vous voulez le palier Standard. Et les paliers de politique, qui n'apparaissent dans une politique qu'une fois les composants déployés : **Operate → Administration → Configurations → Policy Definition**, tuile *Real time metrics*, Standard sur le type d'objet vCenter, ESX Top sur Host System. La collecte démarre après un cycle.

Pour Grafana, la communauté a documenté la connexion : une source de données Prometheus pointant vers le FQDN des métriques temps réel avec un en-tête `Authorization: Bearer` et l'UUID du vCenter en `sourceId`, avec un jeton qui expire après 35 minutes, que [GrafanaRTMsync](https://github.com/makolos2/GrafanaRTMsync) rafraîchit depuis cron. Cette partie n'est pas dans la documentation Broadcom ; prenez-la comme une recette qui fonctionne, pas comme une interface prise en charge. Et vérifiez CPU, mémoire et stockage de la taille Large sur la page *Deploy Real-Time Metrics* avant de redimensionner ; je n'ai trouvé de chiffres publiés nulle part ailleurs.

## Cinq règles

1. **Assignez là où la question se pose.** Placez la super metric sur l'objet qui doit porter la réponse, en général le cluster, et descendez avec la profondeur.
2. **Comptez les sauts.** Remplacez le `depth=1` par défaut par le vrai nombre. Négatif remonte ; 0 est invalide, donc chaînez deux super metrics.
3. **Filtrez avant d'agréger.** Utilisez `where` pour écarter les objets éteints ou périmés. Elle ne voit que le même objet, avec un nombre à droite.
4. **Activez-la dans la politique.** Le Preview prouve la formule, pas les données. Rien n'est stocké tant que la politique de ces objets ne l'active pas.
5. **Nommez-les et gérez-les comme du code.** Majuscules à chaque mot avec l'unité à la fin, une description avec responsable et objectif, les définitions exportées sous contrôle de version.

Quand une super metric semble fausse, vérifiez-les dans cet ordre. Et quand la question est *que se passe-t-il maintenant*, prenez plutôt les métriques temps réel.

## Sources

- Broadcom TechDocs, VCF 9.0 : [Configuring Super Metrics](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/infrastructure-operations/configuring-super-metrics.html) · [Create a Super Metric](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/infrastructure-operations/configuring-super-metrics/create-a-super-metric.html) · [Enhancing Your Super Metrics](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/infrastructure-operations/configuring-super-metrics/enhancing-a-super-metric-formula.html) · [Super Metric Functions and Operators](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/infrastructure-operations/configuring-super-metrics/super-metrics-tab/super-metric-functions-and-operators.html)
- Broadcom TechDocs, VCF 9.1 : [Real-Time Metrics Details](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/infrastructure-operations/configuring-policies/using-the-monitoring-policy-workspace/policy-workspace/real-time-metrics-policy-workspace.html) · [Real-time Data in the Workbench](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/infrastructure-operations/troubleshooting-workbench-home-page/real-time-data-in-troubleshooting-workbench.html) · [What's New in VCF Operations 9.1](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/release-notes/vmware-cloud-foundation-9-1-0-0-release-notes/what-s-new/whats-new-vcf-ops.html)
- [VCF Operations API : Super Metrics](https://developer.broadcom.com/xapis/vcf-operations-api/9.0/super-metrics)
- KB Broadcom : [429899](https://knowledge.broadcom.com/external/article/429899/view-not-displaying-super-metrics-in-ari.html) preview non persisté · [335252](https://knowledge.broadcom.com/external/article/335252/vmware-aria-operations-super-metric-data.html) attribute= et trous · [397782](https://knowledge.broadcom.com/external/article/397782/vcf-operations-90-sizing-guidelines.html) dimensionnement 9.0 · [324340](https://knowledge.broadcom.com/external/article/324340/vmware-aria-operations-sizing-guidelines.html) index de dimensionnement · [444403](https://knowledge.broadcom.com/external/article/444403/understanding-peak-or-worst-performance.html) métriques de pic
- [Aria Operations 8.12, bonnes pratiques : super metrics](https://docs.vmware.com/en/VMware-Aria-Operations/8.12/Best-Practices/GUID-D43C6734-CEF6-45B4-A2CA-B1451A7C915A.html) · [My Top 15 vRealize Operations Super Metrics](https://blogs.vmware.com/cloud-foundation/?p=26755) (anciens chemins de menu)
- Communauté, métriques temps réel : [Broad-Cloud](https://broad-cloud.nl/realtime-metrics/) · [cloudpull.io](https://cloudpull.io/blog/vcf-9-1-real-time-data/) · [Jade Network Solutions, déploiement par API](https://jadenetworksolutions.co.uk/post/vcf-91-apis/vcf-91-real-time-metrics-deploy/) · [GrafanaRTMsync](https://github.com/makolos2/GrafanaRTMsync)
