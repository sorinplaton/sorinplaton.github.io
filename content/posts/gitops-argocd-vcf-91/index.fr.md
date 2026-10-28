---
title: "GitOps sur VCF 9.1 : ce que demande vraiment le Supervisor Service Argo CD"
date: 2026-09-30
description: "Argo CD comme Supervisor Service, un cluster VKS et une application construits à partir d'un seul repo Git, de bout en bout dans un lab Holodeck."
summary: "Un commit, six VM : Argo CD, exécuté comme vSphere Supervisor Service, construit un cluster VKS et y déploie podinfo à partir d'un seul repo Git — chaque étape, chaque piège et, dans la partie 2, qui contrôle quoi et ce qui casse."
tags: ["VCF 9.1", "Argo CD", "GitOps", "VKS", "Supervisor", "VCF Automation", "Holodeck"]
categories: ["Notes"]
showToc: true
tocOpen: false
---

## Le moment {#the-moment}

On fait le push d'un fichier YAML de 40 lignes sur `main` et on appuie sur Sync. Cinquante-neuf secondes plus tard, le Supervisor a créé le premier objet machine virtuelle.

Quelques minutes plus tard (davantage sur un Holodeck imbriqué), cette VM démarre dans vCenter, et une toute nouvelle API Kubernetes répond sur `10.200.200.7:6443`. Au final, le cluster compte six VM, trois nodes de control plane et trois workers, avec podinfo qui tourne à l'intérieur.

Personne n'a ouvert d'assistant, personne n'a lancé Terraform, personne n'avait de credentials de cluster sur un serveur de build.

Ensuite, un commit d'une ligne fait passer podinfo d'un pod à trois en 16 secondes et, lorsqu'on le ramène à un seul à la main, Argo CD le remet en quatre secondes dans l'état décrit par Git.

<figure class="shot">
<img src="/images/gitops/p1-01.webp" loading="lazy" alt="vSphere Client, namespace podinfo-demo-vkjhr, onglet VMs : six VM demo-vks-cluster, toutes Powered On" />
</figure>

*vSphere Client, namespace `podinfo-demo-vkjhr` : trois VM de control plane `demo-vks-cluster-l6l4p-*` et trois workers `node-pool-1`, toutes Powered On. Chacune d'elles existe grâce à un seul commit.*

<figure class="diagram">
<img src="/images/gitops/diagram-run-timeline.webp" loading="lazy" alt="l'exécution complète · sept étapes avec les durées mesurées dans le lab, du commit au self-heal" />
<figcaption aria-hidden="true">l'exécution complète · sept étapes avec les durées mesurées dans le lab, du commit au self-heal</figcaption>
</figure>

C'est précisément ce qu'un cluster Kubernetes ordinaire ne sait pas faire et que VMware Cloud Foundation sait faire : la même boucle GitOps qui déploie les containers crée et redimensionne aussi les machines sur lesquelles ils tournent, parce que le vSphere Supervisor expose les VM, les clusters Kubernetes à base de VM et les vSphere Pods comme des objets Kubernetes.

Ce billet construit tout cela à partir d'un Supervisor nu, dans un lab Holodeck imbriqué, avec chaque commande et chaque « piège ».

La partie 2 traite de qui a le droit de modifier quoi une fois que plus personne ne déploie à la main, et de quoi faire le jour où ça ne fonctionne pas.

> **Si vous n'avez jamais touché à Kubernetes, trois phrases suffisent pour suivre.**
>
> Git est le livre de recettes : il contient la description exacte de chaque cluster et de chaque application que vous voulez.
>
> Argo CD est un cuisinier qui relit le livre toutes les quelques minutes, prépare tout ce qui y est écrit et, si quelqu'un touche à une assiette, la refait discrètement comme le livre l'indique.
>
> Le vSphere Supervisor est une cuisine capable de construire à la fois les fours (les machines virtuelles) et les plats (les containers) à partir de la même recette ; c'est pourquoi un seul livre peut décrire un cluster entier, et pas seulement ce qui tourne dedans.

## Ce que vous n'avez pas eu à faire {#what-you-did-not-have-to-do}

Quiconque a construit cela sur un Kubernetes nu reconnaîtra la liste. Tout ce qui figure dans la colonne de droite est le travail de la plateforme sur VCF.

<div class="tbl">

| Sur une pile Kubernetes ordinaire | Sur VCF 9.1 |
|---|---|
| Amorcer Cluster API, choisir un provider, créer des modèles de machines, maintenir les images de nodes | Faire le commit d'un objet `Cluster` de 40 lignes ; le Supervisor construit des nodes Photon à partir de sa bibliothèque de contenu |
| Installer et patcher Argo CD vous-même, cluster par cluster | Activer l'Argo CD Operator une fois par Supervisor ; un objet `ArgoCD` par équipe ; les upgrades se résument à changer un numéro de version |
| Choisir et exploiter un CNI, un load balancer et un ingress | Chaque namespace reçoit son propre VPC NSX, son SNAT et son load balancer ; Antrea est fourni avec le cluster |
| Construire les garde-fous de zéro : quotas, classes de stockage, qui peut créer quoi | Les classes de VM, les politiques de stockage, les quotas de namespace et les rôles vSphere SSO existent déjà et s'appliquent aux objets créés depuis Git |
| Gérer les VM et les containers dans deux chaînes d'outils | Un seul repo peut contenir le cluster VKS, les applications qui y tournent et, à côté, des machines virtuelles VM Service |
| Découvrir à 2 h du matin qu'une image de node, une version de CNI et une version de Kubernetes ne s'entendent pas | Les versions de Kubernetes sont des bundles validés ; le service Argo CD suit une nouvelle version mineure de VKS avec deux à quatre mois de retard, et le dit |

</div>

## Argo CD en une page {#argo-cd-in-one-page}

Argo CD est un service qui tourne dans Kubernetes et qui fait une seule chose : il maintient un environnement en exécution identique à un ensemble de fichiers YAML stockés dans Git.

Vous décrivez ce que vous voulez, vous en faites le commit, et Argo CD récupère le repo, le compare à ce qui tourne réellement, applique ce qui manque, puis continue de vérifier. Si quelqu'un modifie le système en direct à la main, Argo CD détecte la dérive et, si vous le lui permettez, remet les choses en place.

Git est la source de vérité et personne ne déploie à la main ; cette façon de travailler, c'est le **GitOps**.

<figure class="diagram">
<img src="/images/gitops/diagram-gitops-loop.webp" loading="lazy" alt="la boucle GitOps · commit, pull, application, correction de la dérive" />
<figcaption aria-hidden="true">la boucle GitOps · commit, pull, application, correction de la dérive</figcaption>
</figure>

Quatre objets portent tout le modèle :

- Une **Application** associe un dossier d'un repo Git à une cible (un endpoint de cluster plus un namespace) et porte la politique de sync : manuelle, automatisée, self-heal, prune.
- Un **Project** (`AppProject`) est le périmètre dans lequel une Application doit tenir : quels repos elle peut lire, sur quelles cibles elle peut écrire, quels types d'objets elle peut créer et quand elle peut se synchroniser.
- Un **cluster secret** est une cible enregistrée : l'endpoint d'API plus les credentials qu'Argo CD y utilise.
- La **politique RBAC** dit quels humains peuvent faire quoi sur quelles Applications et quels Projects.

Ce que chacun des deux publics y gagne :

<div class="tbl">

|        | Administrateur de plateforme | Développeurs et ingénieurs de plateforme |
|---|---|---|
| Avant | Chaque nouveau cluster, node pool ou upgrade est un ticket et une session `kubectl` ; personne ne peut dire qui a changé quoi | Déploient avec des pipelines qui font un push dans le cluster et gardent les credentials du cluster sur un serveur de build |
| Après | Fixe les limites une fois (namespaces, quotas, Projects, cibles) et remet un repo aux équipes. Chaque changement est un commit avec un auteur, un réviseur et un diff ; un upgrade de cluster est un changement d'une ligne ; le retour arrière, c'est `git revert` | Ouvrent une pull request. Argo CD fait le pull du changement, donc aucun credential de cluster ne quitte la plateforme. L'interface montre ce qui est déployé où, si cela correspond à Git et pourquoi c'est en mauvaise santé |

</div>

Sur VCF, la portée est plus large que sur un cluster Kubernetes ordinaire, parce que le Supervisor expose les VM, les vSphere Pods et les clusters VKS comme des objets Kubernetes.

Le même repo peut donc créer le cluster VKS, y déployer les applications et provisionner à côté des machines virtuelles VM Service.

### Argo CD comme service VCF Automation (Tech Preview 9.1.1) {#argo-cd-as-a-vcf-automation-service-911-tech-preview}

Depuis VCF 9.1.1, le même Argo CD est aussi livré par VCF Automation : un provider admin installe le service GitOps une fois par région depuis Service Management, un organization admin déploie des instances et rattache des cibles depuis le portail de l'organisation, et les utilisateurs se connectent avec leurs credentials VCF Automation.

La boucle ne change pas ; la nomenclature (bill of materials) 9.1.1 liste les deux formes à la même version, 1.2.0, avec le même numéro de build : c'est le même Operator.

<figure class="diagram">
<img src="/images/gitops/diagram-vcfa-tech-preview.webp" loading="lazy" alt="pour comparaison · Argo CD comme service VCF Automation, Tech Preview 9.1.1" />
<figcaption aria-hidden="true">pour comparaison · Argo CD comme service VCF Automation, Tech Preview 9.1.1</figcaption>
</figure>

<div class="tbl">

|                                                    | Supervisor Service (ce billet) | Service VCF Automation (Tech Preview) |
|---|---|---|
| Installé par | L'admin vSphere, une fois par Supervisor, depuis vCenter | Le provider admin, une fois par région, depuis Service Management |
| Instance Argo CD créée par | Un objet `ArgoCD` appliqué à un namespace | L'org admin, depuis le portail d'organisation VCF Automation |
| Namespaces et clusters VKS enregistrés par | `vcf argocd cluster add`, ou la politique d'auto-attach de 9.1.1 | Rattachés dans le portail d'organisation |
| Connexion | Comptes locaux Argo CD, ou OIDC configuré à la main | Credentials VCF Automation, OIDC configuré automatiquement |
| Construire, mettre à l'échelle et faire l'upgrade des clusters VKS depuis Git | Oui, objets Cluster dans un namespace cible | Pareil : même Operator, mêmes objets Cluster, même repo |
| Portée d'une instance | Les namespaces et clusters que vous enregistrez, sur ce Supervisor | Une instance par namespace d'hébergement ; namespaces et clusters VKS de la même région |
| Limite d'échelle | Aucune indiquée | 30 vSphere Namespaces, toutes instances confondues, dans un VCF Automation |
| Prérequis | Un Supervisor | Un Harbor régional dans la région |
| Statut en 9.1.1 | Supporté depuis VCF 9.1 | Tech Preview |

</div>

La force du service VCF Automation, c'est l'intégration des équipes et l'identité, pas la construction des clusters. Les tenants obtiennent Argo CD en libre-service plutôt que par ticket, rattachent leurs namespaces et leurs clusters VKS dans le portail sans kubeconfig ni CLI, et se connectent avec leurs comptes VCF Automation ; c'est d'ailleurs ainsi que les utilisateurs fédérés dans VCF Automation accèdent à Argo CD (l'Operator seul ne prend en charge que les comptes locaux et OIDC).

Construire, mettre à l'échelle et mettre à niveau des clusters VKS depuis Git fonctionne de la même façon dans les deux cas, parce que les deux exécutent le même Operator contre le même Supervisor.

En 9.1.1, le Supervisor Service reste le choix supporté. Comme les deux exécutent le même Operator, le repo et les étapes de ce billet devraient s'appliquer tels quels une fois que l'option VCF Automation sera sortie de Tech Preview.

## Le lab, et où vit chaque élément de configuration {#the-lab-and-where-every-piece-of-configuration-lives}

Tout l'environnement est un Holodeck VCF 9.1.0 imbriqué : domaine de gestion, domaine de workload avec NSX 9.1, un Supervisor avec la mise en réseau NSX VPC et une instance VCF Automation.

Autour se trouvent les deux routeurs que fournit Holodeck : le **holorouter** (une VM Photon OS qui sert de gateway par défaut pour chaque VLAN Holodeck et héberge aussi le DNS du lab sous forme de pod en réseau hôte) et un **VyOS** sur le réseau externe du lab, qui route entre Holodeck et le reste du lab. Chaque changement de configuration de ce billet a été fait sur l'une de ces deux VM, dans NSX ou dans le Supervisor. Rien n'a été modifié sur l'équipement physique.

<figure class="diagram">
<img src="/images/gitops/diagram-lab-topology.webp" loading="lazy" alt="topologie complète du lab · réseau externe, holorouter, domaines de gestion et de workload, Supervisor" />
<figcaption aria-hidden="true">topologie complète du lab · réseau externe, holorouter, domaines de gestion et de workload, Supervisor</figcaption>
</figure>

<div class="tbl">

| Composant | Où il se trouve | Adresse |
|---|---|---|
| Hôte de rebond (Ubuntu) | Réseau externe du lab | route `10.1.0.0/20` via `10.0.6.33`, DNS `10.0.4.17` |
| VyOS | Réseau externe du lab, en bordure de Holodeck | `10.0.6.33` ; route statique `10.200.200.0/24` vers le holorouter |
| Holorouter | Entre le réseau externe et chaque VLAN Holodeck | `eth1.10` `10.1.1.1/24`, `eth1.24` `10.1.8.129/25`, `eth1.200` `10.200.200.1/24` |
| Technitium DNS | Pod en réseau hôte sur le holorouter | `10.1.1.1`, ne répond en UDP qu'aux VLAN directement connectés |
| vCenter, SDDC Manager, NSX Manager, VCF Operations, VCF Automation | Domaine de gestion `mgmt-a` | VLAN 10, `10.1.1.0/24` ; SDDC Manager `10.1.1.5` |
| vCenter `vc-wld01-a` | Domaine de workload `wld01` | là où les Supervisor Services sont activés |
| NSX Manager `nsx-wld01-a` | Domaine de workload `wld01` | `10.1.1.24` ; projets NSX `default` et `holo-east` |
| Cluster VNA | Domaine de workload, dessert les deux projets | SNAT `10.1.8.130` (projet default), `10.200.200.3` (holo-east) |
| Namespace d'hébergement `argocd-demo` | Supervisor, VPC `vpc-argocd-demo`, projet `holo-east` | VIP Argo CD `10.200.200.5` |
| Namespace cible `podinfo-demo-vkjhr` | Supervisor, son propre VPC, projet `holo-east` | VM du cluster VKS (control plane 10.200.200.7:6443, nodes 10.17.17.x), LoadBalancer podinfo 10.200.200.8 et les vSphere Pods podinfo |

</div>

C'est tout le réseau dont vous avez besoin pour suivre les étapes. La façon dont le VPC du tenant atteint l'extérieur par une Distributed VLAN Connection (DVC) sur le VLAN 200, avec son propre schéma et le tableau complet des objets NSX, se trouve dans la partie 2 sous *Sous le capot*, à côté des pièges qu'elle explique.

## Avant de commencer {#before-you-start}

<div class="tbl">

| Élément | Utilisé dans ce lab |
|---|---|
| VCF | Holodeck 9.1.0, NSX 9.1 dans le domaine de workload, Supervisor avec la mise en réseau NSX VPC |
| Argo CD | Argo CD Supervisor Service 1.2.0, qui embarque Argo CD 3.4.4 upstream |
| Tenant | Organisation VCF Automation `holo-east` avec deux vSphere Namespaces, chacun sur son propre VPC dans le projet NSX `holo-east` |
| Connectivité externe | Une Distributed VLAN Connection sur le VLAN 200, gateway `10.200.200.1/24`, bloc externe `10.200.200.0/24` |
| Hôte de rebond | Ubuntu sur le réseau externe avec `kubectl` et la VCF CLI, dont le plugin `argocd` fournit les commandes Argo CD ; sa configuration fait l'objet de l'annexe A dans la partie 2 |
| Application | podinfo 6.15.0 (`ghcr.io/stefanprodan/podinfo`), une petite application web qui affiche quel pod a servi la page |
| CLI | VCF CLI 9.0.2 avec le plugin argocd v1.2.0. Il n'y a pas de binaire argocd autonome : chaque commande Argo CD de ce billet se tape donc vcf argocd … |
| Images de nodes | Bibliothèque de contenu abonnée VKS-1 (138 éléments, download à la demande), définie comme bibliothèque de contenu du Kubernetes Service du Supervisor |
| Cluster | Kubernetes v1.35.5+vmware.1-vkr.1, Photon OS, cluster class builtin-generic-v3.6.0 ; control plane best-effort-small (2 vCPU, 4 Gio), workers best-effort-xsmall (2 vCPU, 2 Gio) |
| Limites du namespace cible | podinfo-demo-vkjhr relevé à 200000Mi de stockage et 20000Mi de mémoire (24000Mi pour la septième VM à l'étape 6) ; les valeurs par défaut de la petite classe, 100000Mi et 10000Mi, ne peuvent pas contenir six VM de nodes |
| Repo | [github.com/sorinplaton/vcf-gitops](https://github.com/sorinplaton/vcf-gitops/tree/blog-part1), public ; chaque fichier de ce billet s'y trouve au tag `blog-part1` |

</div>

Ce billet n'utilise qu'Argo CD sur VCF ; une comparaison côte à côte clôt la section « Argo CD en une page ».

L'organisation VCF Automation existe ici parce que c'est par elle que le tenant a obtenu son projet NSX, ses VPC et ses namespaces ; Argo CD lui-même ne passe jamais par elle.

### Le repo {#the-repository}

Tout ce que ce billet applique se trouve dans Git, et les liens pointent vers le tag [`blog-part1`](https://github.com/sorinplaton/vcf-gitops/tree/blog-part1) : ils continuent donc de montrer ce qui a tourné dans le lab même quand `main` aura évolué. Les blocs de code ci-dessous montrent les lignes qui comptent ; les fichiers complets sont à un clic.

<div class="tbl">

| Dossier | Ce qu'il contient | Surveillé par Argo CD |
|---|---|---|
| `clusters/` | La définition du cluster VKS | Oui, par `demo-vks-cluster` |
| `apps/` | podinfo pour le cluster VKS | Oui, par `podinfo-vks` |
| `argocd-apps/` | Les trois Applications en YAML, et une Application racine | Non : appliquées avec kubectl (étape 6) |
| `platform/argocd/` | L'objet ArgoCD en service et ses config maps, exportés | Non |
| `examples/` | La politique RBAC, les Projects et la protection contre la suppression de la partie 2, non appliqués dans le lab | Non |
| `lab/` | Les changements de routeur propres à Holodeck | Non |
| `tools/capture/` | Le script qui a pris les captures d'écran | Non |

</div>

<figure class="shot">
<img src="/images/gitops/p1-02.webp" loading="lazy" alt="Repo GitHub sorinplaton/vcf-gitops au tag blog-part1 : apps, argocd-apps, clusters, examples, lab, platform, tools et le README" />
</figure>

*Le repo au tag `blog-part1`.*

### Vérifications préalables : huit contrôles avant l'étape 1 {#preflight-eight-checks-before-step-1}

Chaque ligne du tableau est un test tenant en une seule commande, que vous pouvez lancer depuis l'hôte de rebond, avec l'endroit de ce billet qui explique la correction. Les lignes marquées **Holodeck** sont des problèmes propres à un lab imbriqué ; sur un VCF de production, le routeur et le DNS de l'équipe réseau y satisfont normalement déjà, mais lancez le test quand même.

<div class="tbl">

| Contrôle | Test | Réussi quand | Correction dans |
|---|---|---|---|
| Argo CD Supervisor Service installé | `kubectl api-resources \| grep argocd` | `argocds` apparaît dans la liste | [Étape 1](#step-1-enable-the-argo-cd-supervisor-service) |
| La gateway DVC répond (**Holodeck**) | `ping 10.200.200.1` | elle répond | [Étape 2](#step-2-namespaces-the-vpc-load-balancer-and-the-network-nobody-documents) |
| Le DNS répond en UDP depuis le VLAN du VPC (**Holodeck**) | Maintenant, depuis l'hôte de rebond : `dig @10.1.1.1 projects.packages.broadcom.com +tcp`. Plus tard, depuis le VLAN 200 : une résolution UDP depuis un node VKS ou le pull d'image d'un vSphere Pod | TCP répond maintenant ; la résolution depuis le VLAN 200 répond plus tard (un timeout UDP depuis l'hôte de rebond est normal sur Holodeck) | [Étape 2](#step-2-namespaces-the-vpc-load-balancer-and-the-network-nobody-documents) |
| Le VPC de l'organisation a un load balancer | `kubectl get vpcnetworkconfiguration <ns-config> -o jsonpath='{.status.vpcs[0].nsxLoadBalancerPath}'` | un chemin `/vpc-lbs/` | [Étape 2](#step-2-namespaces-the-vpc-load-balancer-and-the-network-nobody-documents) |
| Des versions de Kubernetes existent | `kubectl get kr` | votre version est READY et COMPATIBLE True | [Étape 4](#before-the-first-commit-three-things-the-supervisor-needs) |
| Une cluster class prend en charge cette version | `kubectl apply --dry-run=server -f cluster.yaml` | `created (server dry run)` | [Étape 4](#before-the-first-commit-three-things-the-supervisor-needs) |
| Le namespace cible peut contenir six VM | `kubectl -n <ns> get storagepolicyquota` | limite supérieure à 120 Gio plus une marge, et mémoire supérieure à 18 Gio | [Étape 4](#before-the-first-commit-three-things-the-supervisor-needs) |
| Les nodes VKS peuvent résoudre les registries (**Holodeck**) | depuis un node, une requête DNS UDP vers son résolveur | une réponse | [Étape 5](#step-5-register-the-vks-cluster-and-deploy-podinfo) |

</div>

Chaque capture d'écran de ce billet provient de l'exécution réelle.

## Étape 1. Activer le Supervisor Service Argo CD {#step-1-enable-the-argo-cd-supervisor-service}

L'Argo CD Operator est un Supervisor Service, installé une fois par Supervisor depuis le vSphere Client. C'est un Operator Kubernetes construit par Broadcom dont le seul rôle est le cycle de vie des instances Argo CD : il apprend au Supervisor un nouveau type d'objet, `ArgoCD`, et chaque fois qu'un tel objet apparaît dans un namespace, il construit, met à niveau et répare l'instance correspondante.

Dans la nomenclature 9.1.1, le service est en version **1.2.0** et embarque Argo CD **3.4.4** upstream (la version qu'a rapportée l'API une fois l'instance du lab en bonne santé).

**D'où vient le YAML.** Le download de l'Argo CD Supervisor Service sur le portail de support Broadcom est un petit bundle : la définition du service `supervisor-service-argocd-1.2.0-<build>.yml` (avec des variantes `-legacy-` et `-depot-` pour les installations air-gapped), la CLI `argocd` personnalisée et les notes de version de l'Operator.

Le YAML du service est ce que vous remettez à vCenter ; à l'intérieur, `spec.template.spec.fetch.imgpkgBundle[].image` pointe vers le bundle OCI à partir duquel l'Operator est réellement installé, sur le registry de Broadcom :

    projects.packages.broadcom.com/vsphere/supervisor/argocd-service/1.2.0/argocd-service:v1.2.0_vmware.1

Ce bundle est aussi lisible depuis l'hôte de rebond, ce qui est le moyen le plus rapide de voir exactement ce que l'Operator va déployer et quelles valeurs de `spec.version` il connaît :

    # copy the exact image reference from the service YAML you downloaded
    grep -A2 imgpkgBundle supervisor-service-argocd-1.2.0-*.yml
    imgpkg pull -b projects.packages.broadcom.com/vsphere/supervisor/argocd-service/1.2.0/argocd-service:v1.2.0_vmware.1 \
      -o ~/argocd-service-1.2.0
    ls ~/argocd-service-1.2.0/          # package YAML, CRD, sample ArgoCD manifests

Dans un lab air-gapped, c'est cette même référence qui est copiée dans un software depot local avant l'enregistrement du YAML `-depot-` ; le guide air-gapped listé dans les sources décrit la procédure, et VCF Download Tool 9.1.1 récupère tout le bundle avec `artifacts download --component SUPERVISOR_SERVICE_ARGOCD`.

Ensuite, dans le vSphere Client, allez dans **Supervisor Management \> Services**, cliquez sur **Add**, faites l'upload du YAML du service puis, sur la nouvelle carte Argo CD, choisissez **Install on Supervisors** et sélectionnez le Supervisor.

<figure class="shot">
<img src="/images/gitops/p1-03.webp" loading="lazy" alt="Carte ArgoCD Service dans le vSphere Client, statut Active, une version active, installée sur un Supervisor" />
</figure>

*La carte ArgoCD Service sous Supervisor Management \> Services une fois installée : une version active, un Supervisor. C'est la seule étape d'installation de tout le billet.*

Vérifiez depuis l'hôte de rebond que le Supervisor connaît maintenant ce nouveau nom :

    vcf context use sv-a:argocd-demo-wqfbg
    kubectl api-resources | grep argocd

`argocds` dans le groupe `argocd-service.vsphere.vmware.com` signifie que l'Operator a enregistré son type. Une mise en garde pour plus tard : un upgrade du Supervisor est bloqué si un Supervisor Service comme Argo CD ou Harbor est dans une version que le Supervisor cible ne prend pas en charge ; gardez donc le service à jour depuis la même page Services.

## Étape 2. Les namespaces, le load balancer du VPC et le réseau que personne ne documente {#step-2-namespaces-the-vpc-load-balancer-and-the-network-nobody-documents}

Argo CD a besoin d'un **namespace d'hébergement** dans lequel tourner et d'au moins un **namespace cible** dans lequel déployer.

Dans ce lab, les deux appartiennent à l'organisation VCF Automation `holo-east`, qui les a créés chacun sur son propre VPC ; dans un lab uniquement vCenter, vous les créeriez sous **Supervisor Management \> Namespaces** en associant une classe de VM et une politique de stockage au namespace cible, puisque c'est là qu'atterriront les VM du cluster VKS.

Dans un cas comme dans l'autre, le compte qui appliquera l'objet `ArgoCD` doit avoir le rôle *edit* ou *owner* sur le namespace d'hébergement.

<figure class="shot">
<img src="/images/gitops/p1-04.webp" loading="lazy" alt="vSphere Client, Supervisor Management, liste des Namespaces de supervisor-wld01-a avec argocd-demo-wqfbg et podinfo-demo-vkjhr" />
</figure>

*Supervisor Management \> Namespaces : le namespace d'hébergement `argocd-demo-wqfbg` et la cible `podinfo-demo-vkjhr`, chacun avec sa consommation de CPU, de mémoire et de stockage.*

Trois conditions doivent être réunies avant que le premier pod démarre, et aucune n'est vérifiée pour vous.

> **Propre à Holodeck, d'ici jusqu'au load balancer.** Sur un VCF de production, la gateway DVC est votre routeur physique et le DNS est votre DNS d'entreprise : lancez les deux tests ci-dessous pour le confirmer, et sautez la correction sur le holorouter.
>
> Dans un lab Holodeck, le VLAN 200 n'appartient à personne tant que vous ne le confiez pas à quelqu'un.

**La gateway DVC (Distributed VLAN Connection) doit exister.** VCF Automation inscrit le VLAN, l'adresse de gateway et le bloc externe dans NSX, et s'arrête là.

Quelqu'un doit posséder `10.200.200.1` sur le VLAN 200, sinon chaque paquet passé par le SNAT quitte le VNA vers une adresse MAC qui n'existe pas. Vérifiez depuis l'hôte de rebond, qui se trouve sur le réseau externe du lab, avant d'aller plus loin :

    ping 10.200.200.1          # the DVC gateway: must answer
    ip route get 10.200.200.3  # the SNAT address: must have a route

**Le DNS doit répondre en UDP depuis le nouveau VLAN.** Le récupérateur d'images d'ESXi (image fetcher) résout d'abord le registry par une requête UDP.

Le client n'est pas l'hôte de rebond. La résolution du registry est une requête UDP qui part du VLAN 200 : l'image fetcher d'ESXi l'envoie pour les vSphere Pods, les VM des nodes l'envoient pour un cluster VKS.

Un serveur DNS qui ne répond en UDP qu'à ses propres VLAN, comme celui de Holodeck, laisse expirer chacune de ces requêtes alors que `dig +tcp` depuis l'hôte de rebond réussit et masque le problème. Testez donc en UDP, depuis le VLAN 200 :

<div class="tbl">

| Lancé depuis | Quand | Ce que ça prouve |
|---|---|---|
| L'hôte de rebond, sur le réseau externe du lab | Avant que quoi que ce soit existe | Seulement que le serveur DNS est en marche. Sur Holodeck, l'UDP depuis cet endroit expire même une fois le VLAN 200 corrigé, parce que l'hôte de rebond n'est pas sur l'un des VLAN propres au holorouter |
| Un node VKS, sur le VLAN 200 | Une fois le cluster créé (étape 5) | Le vrai chemin client pour les clusters VKS. Un timeout ici est ce que corrige la modification DNS de l'étape 5 |
| Les pods eux-mêmes | Après le premier déploiement de vSphere Pods | Pour les vSphere Pods, le pull d'image fait office de test : pods Running et aucun `ErrImagePull` |

</div>

    # 1. Jump host (outer lab network): proves the server is up, nothing more
    dig @10.1.1.1 projects.packages.broadcom.com +tcp   # must answer
    dig @10.1.1.1 projects.packages.broadcom.com        # UDP: times out on Holodeck from here

    # 2. A VKS node (VLAN 200), once the cluster exists: the real client path
    kubectl --context demo-vks-cluster -n kube-system exec ds/antrea-agent -c antrea-agent -- \
      getent hosts ghcr.io                              # must print an address

    # 3. vSphere Pods: the image pull is the test
    kubectl -n podinfo-demo-vkjhr get pods             # Running, no ErrImagePull

Dans ce lab, les deux ont été corrigés d'un coup en faisant du VLAN 200 une sous-interface du holorouter, si bien que le VPC est devenu un client local du DNS :

    # holorouter, persisted as /usr/local/bin/holo-vlan200.sh in a oneshot systemd unit
    ip link add link eth1 name eth1.200 type vlan id 200
    ip link set eth1.200 mtu 8000 up
    ip addr add 10.200.200.1/24 dev eth1.200
    arping -U -c 3 -I eth1.200 10.200.200.1    # tell the VNA the gateway MAC changed

    # DNS redirect for the VKS nodes, which ask 10.1.1.1 directly (see below)
    iptables -t nat -I PREROUTING -i eth1.200 -d 10.1.1.1/32 -p udp --dport 53 \
      -j DNAT --to-destination 10.200.200.1:53

    # VyOS: release the gateway, keep forwarding the block to the holorouter
    configure
    set protocols static route 10.200.200.0/24 next-hop 10.0.6.33
    # next-hop the holorouter ip
    commit; save

La sous-interface suffit pour les vSphere Pods. La ligne `iptables` est destinée aux nodes VKS construits à l'étape 4 : ils utilisent `10.1.1.1` comme résolveur et, depuis le VLAN 200, cette adresse ne répondait pas en UDP alors que l'adresse du holorouter sur le VLAN 200 répondait ; la redirection envoie donc leurs requêtes vers `10.200.200.1`. Elle a été trouvée à l'étape 5, quand le premier pod n'arrivait pas à récupérer son image ; les deux modifications du holorouter vivent dans le même script, de sorte qu'elles survivent à un redémarrage.

**Le VPC de l'organisation a besoin d'un load balancer, et il est désactivé par défaut.** Avec le load balancing NSX, un service `LoadBalancer` obtient sa VIP d'un load balancer situé sur le VPC du namespace. Les VPC que le Supervisor avait créés pour ses propres namespaces en avaient un ; les deux VPC créés par VCF Automation pour `holo-east` n'en avaient pas (`load_balancer_vpc_endpoint.enabled: false`, rien sous `vpc-lbs`). Tant qu'il n'en existe pas, chaque service `LoadBalancer` du namespace reste à `<pending>`, et NCP inscrit dans ses logs `NCP00270 … Lbs <ns> is not found` sous l'événement générique « Generic error occurred during realizing network for Service ».

La correction la plus simple est d'éviter le problème : quand vous créez le VPC dans le portail tenant de VCF Automation, passez **Enable load balancing** à Yes.

Le formulaire est à No par défaut, et son propre avertissement indique ce que cela casse.

<figure class="shot">
<img src="/images/gitops/p1-05.webp" loading="lazy" alt="Formulaire de création de VPC de VCF Automation : le bouton Enable load balancing est à No, avec l'avertissement indiquant que la désactivation du load balancing empêchera vSphere Kubernetes Service et les autres fonctions nécessitant des load balancers de fonctionner dans les namespaces associés à ce VPC" />
</figure>

*Le formulaire de création de VPC dans VCF Automation. Enable load balancing est à No par défaut ; l'avertissement juste en dessous précise que cela empêche VKS « and other capabilities requiring Load Balancers » de fonctionner dans les namespaces du VPC. Activez-le avant de cliquer sur Create.*

Si le VPC existe déjà sans load balancer, ajoutez-en un depuis l'interface de NSX Manager :

1.  Connectez-vous au NSX Manager du domaine de workload et passez le sélecteur **Project**, en haut, de *Default* à `holo-east`.
2.  Allez dans **VPCs \> Virtual Private Clouds**, dépliez le VPC de l'organisation (ici `vpc-argocd-demo-isedcb` pour le namespace d'hébergement et `vpc-podinfo-demo-l2x8e4` pour la cible) et ouvrez sa section **Network Services**.
3.  Cliquez sur **Add Load Balancer**, taille **Small**, puis enregistrez. Recommencez pour le second VPC de l'organisation.
4.  Attendez la synchronisation suivante du NSX operator, puis confirmez que le Supervisor l'a pris en compte :

<!-- -->

    kubectl get vpcnetworkconfiguration <ns-config> \
      -o jsonpath='{.status.vpcs[0].nsxLoadBalancerPath}'
    # /orgs/default/projects/.../vpcs/vpc-argocd-demo-.../vpc-lbs/lb-...

Dans un cas comme dans l'autre, le load balancer doit exister avant qu'`argocd-server` puisse obtenir une VIP.

<figure class="shot">
<img src="/images/gitops/p1-06.webp" loading="lazy" alt="NSX Manager, projet holo-east, VPC vpc-podinfo-demo-l2x8e4 déplié : CIDR privé 10.17.17.0/24, Network Services avec NSX Load Balancer View Configuration" />
</figure>

*NSX Manager, projet `holo-east` : le VPC cible `vpc-podinfo-demo-l2x8e4` déplié, avec son NSX Load Balancer sous Network Services.*

<figure class="shot">
<img src="/images/gitops/p1-07.webp" loading="lazy" alt="Terminal : kubectl get vpcnetworkconfiguration, puis le nsxLoadBalancerPath du namespace podinfo se terminant par vpc-lbs/lb-fhhy" />
</figure>

*Le côté Supervisor du même load balancer : le `nsxLoadBalancerPath` de `podinfo-demo-vkjhr` se termine par `/vpc-lbs/lb-fhhy`. Une sortie vide ici signifie que les services LoadBalancer de ce namespace resteront en attente.*

## Étape 3. Déployer l'instance Argo CD {#step-3-deploy-the-argo-cd-instance}

Un petit objet, c'est tout ce dont l'Operator a besoin. Le manifeste minimal ci-dessous est celui de la page *Example Argo CD Manifests* de Broadcom pour le Supervisor Service, avec les noms du lab ; la liste complète des champs se trouve dans l'*Argo CD Custom Resource Reference* et, une fois l'Operator installé, le Supervisor peut vous l'afficher :

    kubectl explain argocd.spec --recursive        # every field the 1.2.0 Operator accepts
    kubectl get crd argocds.argocd-service.vsphere.vmware.com -o yaml | less

Enregistrez-le sous `argocd-instance.yaml`. Le seul champ obligatoire est `spec.version` ; le nombre de réplicas (un de chaque par défaut) détermine le mode non HA ou HA.

Voici l'objet tel qu'il tourne dans le lab, exporté dans [`platform/argocd/argocd-instance.yaml`](https://github.com/sorinplaton/vcf-gitops/blob/blog-part1/platform/argocd/argocd-instance.yaml) :

    apiVersion: argocd-service.vsphere.vmware.com/v1alpha1
    kind: ArgoCD
    metadata:
      name: argocd-1
      namespace: argocd-demo-wqfbg      # the hosting namespace
    spec:
      version: 3.4.4+vmware.1-vks.1     # pick from the versions your Operator lists
      enableLoadBalancer: true          # argocd-server gets a VIP from the VPC load balancer
      serverSideDiff: true

    vcf context use sv-a:argocd-demo-wqfbg
    kubectl apply -f argocd-instance.yaml
    kubectl get argocd -n argocd-demo-wqfbg -w

Ce qui se passe en dessous mérite d'être connu avant que quelque chose tourne mal. L'Operator n'exécute pas Argo CD directement ; il génère un **`PackageInstall` kapp** (Carvel), qui se réconcilie toutes les 10 minutes et possède les véritables objets Kubernetes.

> **Pas envie d'attendre 10 minutes ?** Déclenchez une réconciliation immédiate avec la CLI Carvel, par exemple après avoir supprimé un Job en échec :
>
> `kctrl package installed kick -i argocd-1-argocd-pkgi -n argocd-demo-wqfbg`
>
> Cela ne force qu'un seul passage ; la période de 10 minutes, qui appartient à l'Operator, ne change pas. Cela n'a rien à voir non plus avec le polling de Git toutes les 3 minutes par Argo CD lui-même, à l'étape 6.

Ces objets sont les déploiements Argo CD upstream (`argocd-server`, `argocd-repo-server`, `argocd-application-controller`, `argocd-redis`), un Job ponctuel `argocd-redis-secret-init` qui écrit le secret `argocd-redis`, et un service `LoadBalancer` pour l'interface.

Sur un Supervisor, chaque pod est une petite VM ; chacun de ces composants tourne donc comme une PodVM dans le namespace d'hébergement : les pulls d'images sont faits par l'image fetcher d'ESXi à travers le SNAT du VPC, et un pod dont le secret monté est manquant passe directement à la phase terminale `ProviderFailed` au lieu d'attendre en `ContainerCreating`.

    kubectl get packageinstall,pods,svc -n argocd-demo-wqfbg

Un état sain ressemble à ceci : le PackageInstall à *Reconcile succeeded*, quatre pods `1/1 Running`, le job `argocd-redis-secret-init` à *Completed*, et `argocd-server` avec une `EXTERNAL-IP` issue du bloc externe du VPC. Dans ce lab, cette VIP était `10.200.200.5`.

<figure class="shot">
<img src="/images/gitops/p1-08.webp" loading="lazy" alt="Terminal : ArgoCD argocd-1 Ready, PackageInstall Reconcile succeeded, pods Argo CD Running et job d'initialisation du secret redis Completed" />
</figure>

*L'état sain, tel qu'il apparaît dans le terminal : `argocd-1` Ready, le PackageInstall à Reconcile succeeded, quatre pods Running et `argocd-redis-secret-init` Completed.*

<figure class="shot">
<img src="/images/gitops/p1-09.webp" loading="lazy" alt="Terminal : service argocd-server, type LoadBalancer, IP externe 10.200.200.5, ports 80 et 443" />
</figure>

*`argocd-server` avec sa VIP, `10.200.200.5`, fournie par le load balancer du VPC.*

<figure class="shot">
<img src="/images/gitops/p1-10.webp" loading="lazy" alt="Inventaire du vSphere Client montrant les namespaces du Supervisor, dont le namespace svc-argocd-service de l&#39;Argo CD Operator, le namespace du service d&#39;auto-attach et trois pods podinfo qui tournent comme VM sur esx-05a" />
</figure>

Dans l'inventaire vCenter : `argocd-demo-wqfbg` est le namespace d'hébergement ; `svc-argocd-service-o335x` est l'endroit où tourne l'Operator lui-même ; `svc-auto-attach-5gei8` est le service d'auto-attach VKS de 9.1.1 ; et les trois pods `podinfo-*` de `podinfo-demo-vkjhr` sont des vSphere Pods que Git a déployés directement dans un namespace (on y revient à la fin du billet).

Connectez-vous. L'Operator prend en charge deux modes d'authentification : les comptes locaux Argo CD, ou l'authentification unique OIDC auprès de votre propre fournisseur d'identité. Le lab utilise le compte local `admin` ; son mot de passe initial se trouve dans le secret que l'instance crée dans le namespace d'hébergement (vérifiez `kubectl get secrets -n <hosting-ns>` pour le nom exact selon la version de votre Operator ; upstream, il s'appelle `argocd-initial-admin-secret`).

    kubectl -n argocd-demo-wqfbg get secret argocd-initial-admin-secret \
      -o jsonpath='{.data.password}' | base64 -d; echo
    vcf argocd login 10.200.200.5 --username admin --insecure
    # 'admin:login' logged in successfully
    vcf argocd version          # the plugin, v1.2.0; the UI's top-left shows the server, v3.4.4

Un détail propre à ce build : l'instance est déployée avec `cluster.inClusterEnabled=false`, si bien que, contrairement à Argo CD upstream, elle ne s'enregistre pas elle-même comme cible.

Le namespace d'hébergement est l'endroit où vit Argo CD, pas un endroit où il déploie, jusqu'à ce que vous enregistriez un namespace à l'étape suivante.

## Étape 4. Enregistrer le namespace et créer un cluster VKS depuis Git {#step-4-register-the-namespace-and-create-a-vks-cluster-from-git}

Argo CD sur VCF a deux sortes de cibles, et bien les distinguer constitue l'essentiel de la conception.

- **Un vSphere Namespace** (cible 1) donne à Git les objets propres au Supervisor : un `Cluster` que le Supervisor transforme en VM de nodes, des machines virtuelles VM Service, des vSphere Pods. Les droits d'Argo CD s'arrêtent aux namespaces que vous enregistrez ; cette cible construit donc de l'infrastructure, et rien d'autre.
- **Un cluster VKS** (cible 2) donne à Git ce qui tourne à l'intérieur de ce cluster : Deployments, Services, charts Helm. Ici, Argo CD détient cluster-admin ; cette cible meuble donc la maison que la cible 1 a construite.

Même moteur, deux cibles : l'une pour l'infrastructure de l'équipe plateforme, l'autre pour les workloads de l'équipe applicative.

Ce billet utilise les deux. La cible 1 est le namespace `podinfo-demo-vkjhr` : l'Application `demo-vks-cluster` construit les six VM par son intermédiaire à cette étape, et l'application Helm `podinfo`, à la fin du billet, y exécute des vSphere Pods.

La cible 2 est `demo-vks-cluster` lui-même, enregistré à l'étape 5 : l'Application `podinfo-vks` y déploie podinfo.

<figure class="diagram">
<img src="/images/gitops/diagram-two-targets.webp" loading="lazy" alt="deux cibles · le namespace construit les VM, le cluster exécute les applications" />
<figcaption aria-hidden="true">deux cibles · le namespace construit les VM, le cluster exécute les applications</figcaption>
</figure>

Connectez d'abord le repo, puis configurez la cible 1. La CLI personnalisée reconnaît un Supervisor : elle donne à l'instance des credentials liés aux namespaces que vous indiquez, de sorte qu'elle ne pourra jamais toucher qu'à ces namespaces, et elle enregistre la cible sous une adresse interne au cluster avec le namespace comme paramètre de contexte, `https://kubernetes.default.svc/?context=<namespace>`, plutôt que sous l'IP externe du Supervisor.

C'est cette URL que vous passerez désormais à `--dest-server`.

    vcf argocd repo add https://github.com/sorinplaton/vcf-gitops.git --name vcf-gitops
    # Repository 'https://github.com/sorinplaton/vcf-gitops.git' added   (public repo: no token)
    vcf argocd cluster add <supervisor-context> \
      --namespace podinfo-demo-vkjhr --kubeconfig ~/.kube/config
    vcf argocd cluster list

<figure class="shot">
<img src="/images/gitops/p1-11.webp" loading="lazy" alt="Argo CD Settings &gt; Clusters avec une cible enregistrée : le namespace du Supervisor, URL https://kubernetes.default.svc/?context=podinfo-demo-vkjhr, version v1.32.9, statut de connexion Successful" />
</figure>

*Cible 1 enregistrée : le namespace du Supervisor enregistré comme cluster Argo CD. Remarquez la forme de l'URL, la version de Kubernetes propre au Supervisor (v1.32.9) et le test de connexion. Le cluster VKS apparaîtra comme seconde ligne, sur le port 6443, à l'étape 5.*

### Avant le premier commit : trois choses dont le Supervisor a besoin {#before-the-first-commit-three-things-the-supervisor-needs}

Argo CD appliquera sans broncher un objet Cluster que le Supervisor est incapable de construire. Rien de ce qui suit n'est vérifié au moment du sync, et chacun de ces points a coûté une heure ou plus à ce lab (ne me demandez pas comment je le sais) ; vérifiez-les donc en premier.

**Les versions de Kubernetes.** Le Supervisor ne construit des VM de nodes qu'à partir d'une version qu'il connaît. Sur un Supervisor neuf, `kubectl get kr` peut renvoyer `No resources found` même avec une bibliothèque abonnée rattachée, parce qu'ajouter une bibliothèque aux bibliothèques de contenu générales du Supervisor ne suffit pas.

Dans le vSphere Client, créez une bibliothèque de contenu abonnée (ici `VKS-1`, URL d'abonnement `https://wp-content.broadcom.com/v2/latest/lib.json`, download à la demande), puis allez dans **Supervisor Management \> Supervisors \>** votre Supervisor **\> Configure \> Supervisor \> General** et, dans la section **Kubernetes Service**, définissez cette bibliothèque comme bibliothèque de contenu.

Gardez à l'esprit qu'il s'agit d'un raccourci de lab : une seule bibliothèque abonnée pointée directement sur le flux public de Broadcom. En production, les bibliothèques de contenu sont gérées de façon centralisée selon un modèle publication-abonnement : une bibliothèque de publication, qui peut se trouver sur un site air-gapped ([Broadcom : remote content library publisher for Supervisor releases](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/vsphere-supervisor-installation-and-configuration/updating-vsphere-supervisor/updating-the-vsphere-with-tanzu-environment/configuring-a-subscribed-content-library-for-supervisor-images-in-air-gapped-environment/create-a-remote-content-library-pulisher-in-a-local-environment.html)), à laquelle est abonnée la bibliothèque du Kubernetes Service de chaque Supervisor. Les images de VM destinées aux tenants peuvent aussi être proposées de façon centralisée sous forme de provider content libraries dans VCF Automation ([Broadcom : gestion des bibliothèques de contenu dans VCF Automation](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/organization-management/adding-and-managing-content-libraries.html)).

Les versions sont apparues 39 secondes plus tard :

    kubectl get kr
    NAME                       VERSION                  READY   COMPATIBLE   CREATED
    v1.35.5---vmware.1-vkr.1   v1.35.5+vmware.1-vkr.1   True    True         14h

<figure class="shot">
<img src="/images/gitops/p1-12.webp" loading="lazy" alt="vSphere Client, Supervisor supervisor-wld01-a, Configure, General, section Kubernetes Service dépliée avec la bibliothèque de contenu VKS-1, type Subscribed" />
</figure>

*Supervisor Management \> Supervisors \> `supervisor-wld01-a` \> Configure \> Supervisor \> General, section Kubernetes Service dépliée : `VKS-1`, Subscribed, est la bibliothèque à partir de laquelle le Supervisor construit les VM de nodes.*

**Une cluster class assez récente pour cette version.** Les namespaces du tenant ne contenaient que `builtin-generic-v3.1.0` à `v3.3.0`, toutes étiquetées `max-version-supported: v1.32`. Les classes plus récentes se trouvent dans `vmware-system-vks-public`, et un Cluster peut les référencer d'un namespace à l'autre. `builtin-generic-v3.6.0` n'a pas de limite supérieure ; elle s'associe donc à v1.35.5 :

    kubectl get clusterclass -A --show-labels | grep max-version
    kubectl apply --dry-run=server -f clusters/demo-vks-cluster/cluster.yaml   # must say "created (server dry run)"

**De la place dans le namespace pour six VM.** Chaque VM de node reçoit un disque de 20 Gio, et les six VM demandent 18 Gio de mémoire au total.

La classe de namespace `small` appliquée par VCF Automation autorise 100000Mi de stockage (environ 98 Gio) et 10000Mi de mémoire ; la cinquième VM a donc été refusée par le webhook d'admission du quota de stockage.

Augmentez les deux avant de lancer le sync. Dans le portail d'organisation VCF Automation, les limites apparaissent sous **Manage & Govern \> Namespaces \>** le namespace, la mémoire sous Capacity et le stockage sous Storage class, en Go (20000Mi s'affiche sous la forme 19.53 GB, 200000Mi sous la forme 195.31 GB). En tant qu'organization admin depuis l'hôte de rebond, la modification est la suivante :

    kubectl --server https://auto-a.site-a.vcf.lab/cci/kubernetes -n default-project \
      patch supervisornamespace podinfo-demo-vkjhr --type json -p '[
      {"op":"replace","path":"/spec/classConfigOverrides/storageClasses/0/limit","value":"200000Mi"},
      {"op":"replace","path":"/spec/classConfigOverrides/zones/0/memoryLimit","value":"20000Mi"}]'
    kubectl -n podinfo-demo-vkjhr get storagepolicyquota    # limit: 200000Mi

<figure class="shot">
<img src="/images/gitops/p1-13.webp" loading="lazy" alt="VCF Automation, organisation holo-east, namespace podinfo-demo-vkjhr, section Capacity avec une limite de mémoire de 19.53 GB" />
</figure>

*VCF Automation, Manage & Govern \> Namespaces \> `podinfo-demo-vkjhr` : la limite de mémoire après la modification, 19.53 GB (20000Mi).*

<figure class="shot">
<img src="/images/gitops/p1-14.webp" loading="lazy" alt="VCF Automation, même namespace, page défilée jusqu'à Storage class : cluster-wld01-01a-vsan-storage-policy avec une limite de 195.31 GB" />
</figure>

*Même page, Storage class : `cluster-wld01-01a-vsan-storage-policy` à 195.31 GB (200000Mi).*

Faites le commit de la définition du cluster sous `clusters/demo-vks-cluster/cluster.yaml` ([fichier complet](https://github.com/sorinplaton/vcf-gitops/blob/blog-part1/clusters/demo-vks-cluster/cluster.yaml), inchangé depuis le commit `7ec073f`).

Les lignes qui comptent : la cluster class et la version choisies plus haut, trois nodes de control plane en `best-effort-small` et un pool de trois workers en `best-effort-xsmall`, défini par une entrée `variables.overrides` propre au pool.

L'annotation `argocd.argoproj.io/sync-wave: '0'` du fichier complet fait en sorte que le cluster se synchronise avant tout ce qui en dépend, lorsque des add-ons seront ajoutés.

    # excerpt of clusters/demo-vks-cluster/cluster.yaml
    kind: Cluster
    metadata:
      name: demo-vks-cluster
    spec:
      topology:
        classRef:
          name: builtin-generic-v3.6.0
          namespace: vmware-system-vks-public
        version: v1.35.5+vmware.1-vkr.1
        controlPlane:
          replicas: 3                         # vmClass best-effort-small (topology variable)
        workers:
          machineDeployments:
          - class: node-pool
            name: node-pool-1
            replicas: 3                       # vmClass best-effort-xsmall (per-pool override)

Créez l'Application qui cible le namespace du Supervisor et synchronisez-la. Laissez celle-ci en sync manuel : elle construit de l'infrastructure, et vous voulez qu'une personne appuie sur le bouton.

    vcf argocd app create demo-vks-cluster \
      --repo https://github.com/sorinplaton/vcf-gitops.git --path clusters/demo-vks-cluster \
      --dest-server 'https://kubernetes.default.svc/?context=podinfo-demo-vkjhr' \
      --dest-namespace podinfo-demo-vkjhr
    vcf argocd app sync demo-vks-cluster
    vcf argocd app wait demo-vks-cluster --timeout 3600
    kubectl -n podinfo-demo-vkjhr get cluster,machines -o wide     # the real finish line

**Durée.** Environ 30 minutes pour une construction 3 + 3 sans incident. Ce lab a pris plus de temps à cause d'échecs de démarrage et d'un quota de stockage, deux sujets traités dans la partie 2.

**Lire `app wait`.** « Healthy » signifie qu'Argo CD a appliqué l'objet Cluster, pas que les six VM sont prêtes, et sa `Duration` est celle du sync, pas de la construction.

La vraie ligne d'arrivée est la dernière commande ci-dessus : attendez que `kubectl get cluster,machines` affiche `CP AVAILABLE 3` et `W AVAILABLE 3`.

Et le Cluster se déclare Healthy un peu trop tôt : ici, l'application est passée à Healthy avec cinq machines sur six en Running.

<figure class="shot">
<img src="/images/gitops/p1-15.webp" loading="lazy" alt="Interface Argo CD, application demo-vks-cluster en vue arborescente, santé Progressing, statut de sync Synced sur HEAD 7ec073f" />
</figure>

*Argo CD, application `demo-vks-cluster` Synced sur `7ec073f`, santé Progressing. L'application ne contient qu'un seul objet, le Cluster ; tout le reste est le travail du Supervisor. (Utilisez la vue arborescente : la vue Network est vide pour une application qui ne contient qu'un Cluster.)*

<figure class="shot">
<img src="/images/gitops/p1-16.webp" loading="lazy" alt="vSphere Client, namespace podinfo-demo-vkjhr, une VM de control plane Powered On" />
</figure>

*Au bout de dix minutes : la première VM de control plane, Powered On.*

<figure class="shot">
<img src="/images/gitops/p1-17.webp" loading="lazy" alt="vSphere Client, namespace podinfo-demo-vkjhr, cinq VM, dont une encore Powered Off, Recent Tasks en cours" />
</figure>

*Cinq VM, la deuxième VM de control plane encore Powered Off, Recent Tasks bien occupé.*

<figure class="shot">
<img src="/images/gitops/p1-18.webp" loading="lazy" alt="Terminal : vcf argocd app wait se termine Synced et Healthy, puis kubectl get cluster et machines montre 3 control plane et 3 workers Running" />
</figure>

*`app wait` se termine Synced / Healthy ; `kubectl get cluster,machines` en dessous montre la vraie ligne d'arrivée, trois machines de control plane et trois machines worker Running.*

<figure class="shot">
<img src="/images/gitops/p1-19.webp" loading="lazy" alt="vSphere Client, namespace podinfo-demo-vkjhr, Compute, Kubernetes clusters : demo-vks-cluster Available, 3 workers, v1.35.5, control plane 10.200.200.7" />
</figure>

*vCenter sait qu'il s'agit d'un cluster, pas seulement de six VM : Supervisor Management \> Namespaces \> `podinfo-demo-vkjhr` \> Compute \> VMware Resources \> Kubernetes clusters affiche `demo-vks-cluster`, Available, 3 workers, v1.35.5, control plane à 10.200.200.7.*

## Étape 5. Enregistrer le cluster VKS et déployer podinfo {#step-5-register-the-vks-cluster-and-deploy-podinfo}

Enregistrez maintenant la cible 2. **Ce qui change par rapport à la cible 1 :** la commande Argo CD est la même, le contexte kube ne l'est pas. Ajouter `--tanzu-kubernetes-cluster-namespace` et `--tanzu-kubernetes-cluster-name` à la connexion fournit un contexte pour l'API propre au cluster ; `vcf argocd cluster add` enregistre donc `https://10.200.200.7:6443` au lieu de l'URL de namespace interne, et n'a pas besoin de `--namespace`.

Les droits changent en même temps. Au lieu d'un RoleBinding limité à un vSphere Namespace, la CLI crée un service account `argocd-manager` dans `kube-system` avec un ClusterRole, un ClusterRoleBinding et un token de longue durée. C'est le comportement upstream standard, et c'est ce qu'il faut garder en tête en lisant la section RBAC plus bas.

    kubectl vsphere login --server 10.1.8.132 --vsphere-username administrator@vsphere.local \
      --insecure-skip-tls-verify \
      --tanzu-kubernetes-cluster-namespace podinfo-demo-vkjhr \
      --tanzu-kubernetes-cluster-name demo-vks-cluster      # creates the kube context demo-vks-cluster
    vcf argocd cluster add demo-vks-cluster -y
    #  ServiceAccount "argocd-manager" created in namespace "kube-system"
    #  ClusterRole "argocd-manager-role" created
    #  ClusterRoleBinding "argocd-manager-role-binding" created
    #  Created bearer token secret "argocd-manager-long-lived-token" for ServiceAccount "argocd-manager"
    #  Cluster 'https://10.200.200.7:6443' added
    vcf argocd cluster list
    SERVER                                                                     NAME              VERSION  STATUS
    https://kubernetes.default.svc/?context=podinfo-demo-vkjhr (1 namespaces)  podinfo-demo      v1.32.9  Successful
    https://10.200.200.7:6443                                                  demo-vks-cluster  v1.35.5  Successful

<figure class="shot">
<img src="/images/gitops/p1-20.webp" loading="lazy" alt="Interface Argo CD, Settings, Clusters : demo-vks-cluster sur 10.200.200.7:6443 et podinfo-demo sur kubernetes.default.svc, tous deux Successful" />
</figure>

*Les deux cibles enregistrées. Settings \> Clusters d'Argo CD avec le cluster VKS sur le port 6443 (Kubernetes v1.35.5) et le namespace du Supervisor sur son URL interne (v1.32.9), tous deux Successful. La ligne du cluster affiche Unknown tant qu'aucune application ne le cible ; c'est purement cosmétique.*

> **En 9.1.1, pourquoi pas l'auto-attach ?** Un composant d'auto-attach VKSM peut enregistrer les nouveaux clusters VKS pour vous, mais seulement une fois qu'une politique existe, et il n'y en a aucune par défaut. Si `kubectl get entitymanagementpolicies -A` ne renvoie rien, enregistrez à la main comme ci-dessus. Détails dans la partie 2, *L'auto-attach en 9.1.1*.

**Propre à Holodeck : le DNS des nodes.** Les nodes VKS résolvent les registries via `10.1.1.1`. Sans la redirection DNS de l'étape 2, le premier pod a échoué avec `lookup ghcr.io on 127.0.0.53:53 ... i/o timeout`. Une fois celle-ci en place, vérifiez depuis un node avant de déployer :

    kubectl --context demo-vks-cluster -n kube-system exec ds/antrea-agent -c antrea-agent -- \
      getent hosts ghcr.io                              # must print an address

<figure class="shot">
<img src="/images/gitops/p1-21.webp" loading="lazy" alt="Terminal : getent hosts ghcr.io depuis un node VKS renvoie 140.82.114.33, et le resolv.conf du node indique nameserver 10.1.1.1" />
</figure>

*La vérification après la correction, lancée depuis un node par son pod `antrea-agent` : le résolveur est toujours `10.1.1.1`, et `ghcr.io` se résout maintenant.*

Faites le commit de l'application sous `apps/podinfo/podinfo.yaml` ([version de son premier commit, `9313393`](https://github.com/sorinplaton/vcf-gitops/blob/9313393/apps/podinfo/podinfo.yaml), avec un réplica). podinfo est une petite application web en Go conçue pour ce genre de démo : sa page affiche le pod qui l'a servie, si bien que la mise à l'échelle se voit dans un navigateur.

Gardez le security context ; c'est lui qui permet au pod de tourner sous la Pod Security Admission restreinte qu'imposent les clusters VKS. Le Service est de type `LoadBalancer`, si bien que le navigateur l'atteint par le load balancer du VPC sans port-forward.

    # excerpt of apps/podinfo/podinfo.yaml
    kind: Deployment
    spec:
      replicas: 1                             # step 6 changes this line to 3
      template:
        spec:
          securityContext:
            runAsNonRoot: true
            runAsUser: 1000
            seccompProfile: {type: RuntimeDefault}
          containers:
          - name: podinfo
            image: ghcr.io/stefanprodan/podinfo:6.15.0
            securityContext:
              allowPrivilegeEscalation: false
              readOnlyRootFilesystem: true
              capabilities: {drop: ["ALL"]}
    ---
    kind: Service
    spec:
      type: LoadBalancer
      ports: [{name: http, port: 9898, targetPort: http}]

    vcf argocd app create podinfo-vks \
      --repo https://github.com/sorinplaton/vcf-gitops.git --path apps/podinfo \
      --dest-server https://10.200.200.7:6443 --dest-namespace podinfo \
      --sync-option CreateNamespace=true \
      --sync-policy automated --self-heal --auto-prune
    vcf argocd app wait podinfo-vks
    kubectl --context demo-vks-cluster -n podinfo get svc podinfo
    # EXTERNAL-IP 10.200.200.8   PORT(S) 9898

Le load balancer a attribué `10.200.200.8` après le sync, et la page répondait sur `http://10.200.200.8:9898` une seconde plus tard.

<figure class="shot">
<img src="/images/gitops/p1-22.webp" loading="lazy" alt="Vue Network de podinfo-vks dans Argo CD avec trois Pods ; l'infobulle sur l'un des Pods indique Node demo-vks-cluster-node-pool-1-t7n2z-87mq4-f74d6" />
</figure>

*Argo CD, `podinfo-vks`, vue Network : le trafic entre par `10.200.200.8`, passe par le Service `podinfo` et atteint trois Pods. L'infobulle sur l'un des Pods montre le node sur lequel il tourne, `demo-vks-cluster-node-pool-1-t7n2z-87mq4-f74d6`, l'une des VM worker présentées en haut du billet.*

<figure class="shot">
<img src="/images/gitops/p1-23.webp" loading="lazy" alt="Page podinfo dans un navigateur sur 10.200.200.8:9898 : greetings from podinfo v6.15.0, servie par podinfo-5448f4f4fb-jtzj8" />
</figure>

*podinfo sur `http://10.200.200.8:9898`, « greetings from podinfo v6.15.0 », servie par le pod `podinfo-5448f4f4fb-jtzj8`. Un container sur une VM qu'un commit a créée.*

## Étape 6. Prouver que c'est du GitOps {#step-6-prove-it-is-gitops}

Jusqu'ici, rien que `kubectl apply` n'aurait pu faire. Deux changements prouvent le contraire. `podinfo-vks` a déjà le sync automatisé, le self-heal et le prune activés ; pour une application qui ne les a pas, une seule commande suffit :

    vcf argocd app set podinfo-vks --sync-policy automated --self-heal --auto-prune

Modifiez maintenant `apps/podinfo/podinfo.yaml` dans Git, remplacez `replicas: 1` par `replicas: 3`, faites le commit et le push. Ne faites rien d'autre. Argo CD fait du polling sur le repo (`timeout.reconciliation`, trois minutes par défaut ; le bouton Refresh de l'interface accélère les choses), si bien que le délai dépend du moment du cycle où tombe le push. Ici, le push du commit `448b0d3` a été fait et trois pods tournaient 16 secondes plus tard, sans que personne ait tapé de sync.

<figure class="shot">
<img src="/images/gitops/p1-24.webp" loading="lazy" alt="À gauche : diff du commit GitHub 448b0d3 qui fait passer replicas de 1 à 3. À droite : vue Network de podinfo-vks dans Argo CD avec trois Pods, Synced sur 448b0d3" />
</figure>

*La cause et l'effet sans aucun terminal à l'image : le diff du commit à gauche (de `replicas: 1` à `replicas: 3`), Argo CD à droite avec trois Pods, Synced sur `448b0d3`.*

Ensuite, cassez-le à la main et regardez-le se réparer :

    kubectl --context demo-vks-cluster -n podinfo scale deployment podinfo --replicas=1
    kubectl --context demo-vks-cluster -n podinfo get pods -w

Le nombre tombe à un, Argo CD détecte la dérive et, quatre secondes plus tard, trois pods sont de nouveau Ready.

Git a gagné.

Cherchez ce sync au bon endroit : `argocd app history` ne liste que les syncs vers de nouvelles révisions, alors qu'un self-heal est consigné dans le statut de l'Application sous la forme `initiatedBy: automated`.

<figure class="shot">
<img src="/images/gitops/p1-25.gif" loading="lazy" alt="Animation : vue Network de podinfo-vks dans Argo CD ; après une mise à l'échelle manuelle à un réplica, deux pods se terminent, l'application passe OutOfSync et, en six secondes environ, deux nouveaux pods apparaissent et elle revient à Synced et Healthy" />
</figure>

*Le même self-heal, tel qu'il apparaît dans Argo CD, à une image par seconde : deux pods se terminent, l'application bascule en OutOfSync et, environ six secondes plus tard, elle est revenue à trois pods, Synced et Healthy.*

<figure class="shot">
<img src="/images/gitops/p1-26.webp" loading="lazy" alt="Terminal : kubectl scale à 1 réplica, sortie du watch avec deux pods Terminating et deux nouveaux pods Running, historique de l'application et opération de sync automatisée" />
</figure>

*Le self-heal : la mise à l'échelle manuelle à un, deux pods Terminating et deux nouveaux Running dans le watch, et la dernière opération de l'Application, `initiatedBy={"automated":true}`, phase Succeeded.*

### Retour à la cible 1 : une septième VM à partir d'une ligne {#target-1-again-a-seventh-vm-from-one-line}

La même manœuvre fonctionne sur le cluster lui-même.

Au préalable, la limite de mémoire du namespace est passée de 20000Mi à 24000Mi dans VCF Automation, parce que sept VM de nodes demandent 20 Gio ; le stockage, à 200000Mi, avait déjà de la place.

Ensuite, une ligne a changé dans `cluster.yaml`, de `replicas: 3` à `replicas: 4` sous `node-pool-1`, par la [pull request \#2](https://github.com/sorinplaton/vcf-gitops/pull/2).

<figure class="shot">
<img src="/images/gitops/p1-27.webp" loading="lazy" alt="Vue arborescente de demo-vks-cluster dans Argo CD, OutOfSync par rapport à HEAD 038e8a7, Healthy, sync automatique non activé" />
</figure>

*Argo CD voit le merge et attend : `demo-vks-cluster` est OutOfSync par rapport à `038e8a7` et le reste, parce que l'infrastructure est en sync manuel.*

    vcf argocd app sync demo-vks-cluster

<figure class="shot">
<img src="/images/gitops/p1-28.webp" loading="lazy" alt="vSphere Client, namespace podinfo-demo-vkjhr, sept VM demo-vks-cluster Powered On, le nouveau worker en démarrage, Recent Tasks en train de promouvoir ses disques" />
</figure>

*Sept VM : le nouveau worker `…-6tx57` est sous tension et démarre pendant que Recent Tasks promeut ses disques.*

<figure class="shot">
<img src="/images/gitops/p1-29.webp" loading="lazy" alt="Terminal : Cluster avec 3 machines de control plane et 4 machines worker Ready et Available, sept Machines Running, sept nodes Ready, le nouveau, âgé de 60 secondes" />
</figure>

*La ligne d'arrivée : control plane 3/3, workers 4/4, sept nodes Ready, le plus récent âgé d'une minute.*

La nouvelle VM a démarré du premier coup. La première construction n'avait pas eu cette chance : avec cinq VM en cours de provisionnement simultanément, quatre se sont bloquées au démarrage sur le stockage imbriqué du lab (partie 2, *Quand la construction du cluster se bloque*). Une VM provisionnée seule n'a pas eu ce problème.

Un upgrade du cluster fonctionne de la même façon : modifiez la ligne `version` et faites le commit.

### Les Applications ont aussi leur place dans Git {#the-applications-belong-in-git-too}

Les commandes `vcf argocd app create` sont pratiques, mais elles laissent les Applications elles-mêmes en dehors de Git.

Le repo contient les trois en YAML dans [`argocd-apps/`](https://github.com/sorinplaton/vcf-gitops/tree/blog-part1/argocd-apps), exportées à partir des objets en service. `kubectl diff -f argocd-apps/` n'a rien affiché ; les fichiers décrivent donc exactement ce qui tourne, et reconstruire le côté Argo CD du lab tient en une commande :

    kubectl -n argocd-demo-wqfbg apply -f argocd-apps/demo-vks-cluster.yaml \
      -f argocd-apps/podinfo-vks.yaml -f argocd-apps/podinfo.yaml

<figure class="shot">
<img src="/images/gitops/p1-30.webp" loading="lazy" alt="Page Applications d'Argo CD, vue en tuiles : demo-vks-cluster, podinfo et podinfo-vks, tous Healthy et Synced" />
</figure>

*Un seul écran pour l'infrastructure et les applications : `demo-vks-cluster` (les six VM), `podinfo-vks` (l'application qui tourne dedans) et `podinfo` (les vSphere Pods), tous Healthy et Synced.*

La réorganisation du repo elle-même est passée par une pull request révisée, et elle montre GitOps qui se comporte comme il se doit : Argo CD a pris en compte le commit de merge tout seul et la révision qu'il rapporte est passée de `448b0d3` à `b62223d`, mais aucun sync n'a eu lieu et aucune machine n'a changé, parce que rien n'avait changé dans les dossiers surveillés.

<figure class="shot">
<img src="/images/gitops/p1-31.webp" loading="lazy" alt="À gauche : pull request GitHub 1, après le merge. À droite : terminal avec vcf argocd app history demo-vks-cluster montrant une seule entrée et app get montrant Synced sur b62223d" />
</figure>

*À gauche, la pull request après son merge ; à droite, `demo-vks-cluster` n'a toujours qu'un seul sync dans son historique (la construction initiale à partir de `7ec073f`) tout en se déclarant Synced sur le commit de merge `b62223d`.*

L'étape suivante habituelle est un *app of apps* : une Application racine qui pointe vers `argocd-apps/`, afin qu'Argo CD gère ses propres Applications. [`argocd-apps/root.yaml`](https://github.com/sorinplaton/vcf-gitops/blob/blog-part1/argocd-apps/root.yaml) est dans le repo, mais n'est pas appliqué.

<figure class="diagram">
<img src="/images/gitops/diagram-app-of-apps.webp" loading="lazy" alt="app of apps · appliqué à la main aujourd'hui, Application racine bloquée sur son propre namespace" />
<figcaption aria-hidden="true">app of apps · appliqué à la main aujourd'hui, Application racine bloquée sur son propre namespace</figcaption>
</figure>

Les Applications vivent dans le namespace d'hébergement de l'instance, et cette instance tourne avec `cluster.inClusterEnabled=false` : ce namespace n'est donc pas une cible enregistrée. En faire une donnerait à l'instance un accès en écriture à son propre namespace, ce qui relève d'une décision de conception plutôt que d'une correction de lab.

D'ici là, les Applications sont versionnées et révisées dans Git, et appliquées avec kubectl. Si une Application racine est un jour appliquée, gardez-la en sync manuel avec le prune désactivé : faire un prune d'une Application supprime tout ce qu'elle a construit (partie 2).

## Le raccourci : Git directement dans un namespace, sans cluster {#the-shortcut-git-straight-into-a-namespace-no-cluster}

Tout ce qui précède a utilisé la cible 2 pour placer podinfo dans un cluster VKS. La cible 1 peut l'exécuter à elle seule.

La même instance Argo CD porte une seconde application, `podinfo`, qui installe le chart Helm podinfo upstream directement dans le namespace du Supervisor `podinfo-demo-vkjhr`.

Il n'y a aucun cluster à construire : chaque pod devient un vSphere Pod, une petite VM que l'hôte ESXi exécute directement ; c'est pourquoi l'inventaire des PodVM de l'étape 3 montre des entrées `podinfo-*` à côté des VM de nodes du cluster.

> **Ce qui décide de la façon dont un pod est construit.** Argo CD n'a pas de valeur par défaut : chaque Application nomme une destination, et c'est l'API derrière elle qui construit.
>
> - **La destination est le namespace du Supervisor (cible 1) :** les pods d'un Deployment deviennent des vSphere Pods sur ESXi, un objet `Cluster` devient des VM de nodes VKS, une `VirtualMachine` devient une VM VM Service.
> - **La destination est le cluster VKS (cible 2) :** les pods sont des pods ordinaires sur les nodes worker du cluster.
>
> Règle empirique : un cluster quand l'application en a besoin (CRD, operators, DaemonSets, droits à l'échelle du cluster), le namespace quand elle a seulement besoin de tourner.
>
> Les vSphere Pods nécessitent un Supervisor adossé à NSX, comme dans ce lab.

    vcf argocd app create podinfo \
      --repo https://stefanprodan.github.io/podinfo --helm-chart podinfo --revision '6.*' \
      --dest-server 'https://kubernetes.default.svc/?context=podinfo-demo-vkjhr' \
      --dest-namespace podinfo-demo-vkjhr \
      --sync-policy automated --auto-prune
    vcf argocd app list      # argocd-demo-wqfbg/podinfo   Synced  Healthy  Auto-Prune

<figure class="shot">
<img src="/images/gitops/p1-32.webp" loading="lazy" alt="vSphere Client, namespace podinfo-demo-vkjhr, onglet Compute, Core Kubernetes Pods : trois pods podinfo Running, vSphere Pod Yes" />
</figure>

*vSphere Client, Supervisor Management \> Namespaces \> `podinfo-demo-vkjhr` \> Compute \> Core Kubernetes \> Pods : les trois pods `podinfo-69547cd6b7-*`, Running, avec la colonne vSphere Pod à Yes. Les trois tournent sur `esx-05a` lui-même, pas sur une VM de node.*

<figure class="shot">
<img src="/images/gitops/p1-33.webp" loading="lazy" alt="Application Argo CD podinfo, vue arborescente : Service, Deployment, ReplicaSet et trois Pods issus du chart Helm podinfo 6.15.0, Healthy et Synced" />
</figure>

*Les trois mêmes pods vus du côté d'Argo CD : application `podinfo`, chart Helm `6.*` résolu en 6.15.0, Healthy et Synced.*

Quelle cible utiliser pour une application :

<div class="tbl">

|                    | vSphere Pods (cible 1) | Dans un cluster VKS (cible 2) |
|---|---|---|
| Ce qui est construit | Une petite VM par pod, rien d'autre | D'abord six VM de nodes, puis les pods à l'intérieur |
| Délai jusqu'au premier pod | Quelques secondes | D'abord la construction du cluster |
| Le Kubernetes que vous obtenez | L'API du Supervisor, limitée à un namespace | Votre propre cluster, avec cluster-admin via la cible enregistrée |
| Idéal pour | Des services isolés et des agents qui doivent se trouver à côté des VM | Tout ce qui a besoin d'objets à l'échelle du cluster : CRD, operators, DaemonSets, la plupart des charts Helm |

</div>

## À essayer ensuite {#try-this-next}

Trois choses que vous pouvez faire dans le même lab dès demain, chacune en un seul commit :

- **Mettre à niveau le cluster.** Remplacez `version:` dans `cluster.yaml` par une version plus récente que `kubectl get kr` liste et que votre cluster class prend en charge, synchronisez `demo-vks-cluster` et regardez le Supervisor remplacer les VM de control plane et les VM worker une à une.
- **Mettre une VM dans le même repo.** Ajoutez un objet VM Service `VirtualMachine` sous `vms/` avec le namespace du Supervisor comme destination ; Git possède alors une VM et une application en containers côte à côte.
- **Transformer `apps/` en ApplicationSet.** Activez le contrôleur ApplicationSet sur l'instance, utilisez le générateur de répertoires Git sur `apps/*`, et chaque nouveau dossier devient une application déployée sans `argocd app create`.

Lisez ensuite la partie 2 : c'est là que se trouvent les questions de l'équipe plateforme. Qui peut modifier quoi une fois que plus personne ne lance kubectl apply, ce que protège réellement une instance Argo CD par équipe, ce qu'il faut changer avant la production, et tout ce qui a arrêté ce lab la première fois, du VLAN que personne n'écoutait aux VM de nodes qui refusaient de démarrer. [Partie 2 : qui contrôle quoi, et ce qui casse](#part-2-who-controls-what-and-what-breaks)

## Vocabulaire {#vocabulary}

<div class="tbl">

| Terme | Signification dans ce billet |
|---|---|
| **Supervisor** | Le control plane Kubernetes que vSphere exécute sur un cluster une fois Workload Management activé. Tous les autres objets présentés ici sont créés par son intermédiaire. |
| **vSphere Namespace** | Une frontière de tenant sur le Supervisor : limites de ressources, permissions, classes de VM, politiques de stockage et, avec la mise en réseau VPC, son propre VPC. |
| **Cluster VKS** | Un cluster Kubernetes composé de VM (nodes de control plane et nodes worker) que le Supervisor crée et gère tout au long de leur cycle de vie à partir d'un objet `Cluster`. |
| **Supervisor Service** | Un module complémentaire activé une fois par Supervisor depuis le vSphere Client. L'Argo CD Operator, Harbor et Contour sont livrés de cette façon. |
| **Operator** | Un contrôleur qui possède le cycle de vie d'un produit : il surveille son type d'objet (`ArgoCD`) et construit, met à niveau et répare l'instance correspondante. |
| **Instance Argo CD** | Un Argo CD en fonctionnement : serveur d'API, repo server, application controller et Redis, déployés comme PodVM dans un namespace d'hébergement, avec une interface sur une VIP de load balancer. |
| **PackageInstall (kapp)** | L'objet Carvel que génère l'Operator pour installer l'instance ; il se réconcilie toutes les 10 minutes et possède les objets Kubernetes. |
| **PodVM** | La façon dont un pod s'exécute sur un Supervisor : comme une petite VM. Un pod impossible à construire (secret manquant) échoue de façon définitive en `ProviderFailed`. |
| **Application** | L'unité de déploiement d'Argo CD : un dossier Git, une cible, une politique de sync. |
| **AppProject** | La clôture autour des Applications : repos autorisés, destinations, types de ressources, fenêtres de sync. |
| **Cible / cluster secret** | Une destination enregistrée : un endpoint d'API plus les credentials qu'Argo CD y détient, créée par `argocd cluster add`. |
| **Sync, dérive, self-heal, prune** | Le sync applique Git à la cible ; la dérive est toute différence entre les deux ; le self-heal annule automatiquement la dérive ; le prune supprime ce que Git ne contient plus. |
| **Sync wave** | Une annotation qui ordonne les ressources au sein d'un même sync, pour qu'un cluster soit en place avant ses add-ons. |
| **VPC** | NSX Virtual Private Cloud : le domaine réseau isolé derrière un vSphere Namespace, avec ses propres subnets, son SNAT et un load balancer facultatif. |
| **Transit gateway (TGW)** | La gateway NSX par projet qui relie les VPC à l'extérieur ; ici, elle effectue le SNAT sur le cluster VNA. |
| **Distributed VLAN Connection (DVC)** | La connectivité externe d'une transit gateway par un simple VLAN, sans Tier-0 ni edge : un ID de VLAN, une adresse de gateway externe et un bloc d'IP sur ce VLAN. |
| **VNA** | Le cluster d'appliances NSX qui fournit les services avec état (SNAT, LB) aux VPC sur une Distributed VLAN Connection. |
| **NCP** | Le NSX Container Plug-in du Supervisor, qui réalise les Services et les namespaces dans NSX. |
| **Holodeck** | Le lab VCF imbriqué de Broadcom : toute une flotte sur un seul hôte ESXi, avec le holorouter comme gateway et DNS. |
| Kubernetes release (kr) | Un objet du Supervisor qui nomme une version de Kubernetes et l'image d'OS de node qui l'implémente. Les releases n'apparaissent qu'une fois qu'une bibliothèque de contenu est définie comme bibliothèque du Kubernetes Service du Supervisor. |
| ClusterClass | Le modèle à partir duquel un objet Cluster est construit. Chaque version prend en charge une plage de versions de Kubernetes ; la classe et la version doivent donc être choisies ensemble. |
| MachineHealthCheck | Le chien de garde de Cluster API. Il supprime et recrée une VM de node qui n'a pas rejoint le cluster dans un délai donné (une heure dans ce lab). |

</div>

### Sources {#sources}

- *The VLAN Nobody Was Listening On*, le post-mortem du lab derrière ce pas-à-pas : chaque impasse que le lab Holodeck a rencontrée en premier, pour que le vôtre n'ait pas à le faire (Argo CD Service 1.2.0, Argo CD 3.4.4, Holodeck VCF 9.1.0)
- *Argo CD for VKS: vCenter vs VCF Automation*, l'article explicatif qui l'accompagne, pour les deux voies de livraison et la façon dont elles passent à l'échelle entre régions
- [Install the VMware Argo CD Operator as a Supervisor Service](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-service-administration-and-development/9-0/using-supervisor-services/using-argo-cd-service/install-argo-cd-service.html), Broadcom TechDocs
- [Manage Resources in vSphere Namespaces with Argo CD](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vsphere-supervisor-services-and-standalone-components/latest/using-supervisor-services/using-argo-cd-service/manage-resources-in-vsphere-namespaces-with-argo-cd.html) et [Manage Resources in VKS Clusters by Using Argo CD](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vsphere-supervisor-services-and-standalone-components/latest/using-supervisor-services/using-argo-cd-service/using-argocd-to-manage-resources-on-the-supervisor.html), Broadcom TechDocs
- [Deploy a VKS Cluster and Configure VKS Add-ons by Using Argo CD](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-consumption/latest/argo-cd/deploy-a-vks-custer-by-using-argo-cd.html) et [Deploy an Example Guestbook Application on a VKS Cluster with Argo CD](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-consumption/latest/argo-cd/deploy-an-example-guestbook-application-on-a-vks-cluster-with-argo-cd.html), Broadcom TechDocs
- Bundle de documentation VMware Cloud Foundation 9.1 (PDF) : notes de version de VCF Automation 9.1.1.0 (p. 632), nomenclature 9.1.1 (p. 565), *VCF Edge: Single Node with Argo CD Design Blueprint*, conception de l'authentification et du dimensionnement (p. 1371–1373), commandes artifacts de VCF Download Tool (p. 3978–3980)
- [Argo CD RBAC configuration](https://argo-cd.readthedocs.io/en/stable/operator-manual/rbac/) et [Projects](https://argo-cd.readthedocs.io/en/stable/user-guide/projects/), documentation Argo CD upstream
- README de [holodeck-automation](https://github.com/sorinplaton/holodeck-automation), pour les exigences de route et de DNS de l'hôte de rebond
- [Air-gapped VCF 9.1 guide](https://github.com/vmware/vsphere-supervisor/blob/main/airgapped/air-gapped-vcf91.md), vmware/vsphere-supervisor sur GitHub, pour les commandes d'installation de la VCF CLI, les variantes du YAML du Supervisor Service et la référence du bundle `argocd-service`
- [VCF CLI context command reference](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/building-your-cloud-applications/getting-started-with-the-tools-for-building-applications/installing-and-using-vcf-cli-v9/command-reference2/system-commands(1)/context2.html) et [Connect to a VKS Cluster with VCF CLI](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/vsphere-supervisor-installation-and-configuration/connecting-to-vsphere-with-tanzu-clusters/connect-to-a-tkg-service-cluster-as-a-vcenter-single-sign-on-user-with-kubectl.html), Broadcom TechDocs
- [Using VCF CLI to login to vSphere Supervisor when configured with VCF Automation](https://williamlam.com/2025/12/quick-tip-using-vcf-cli-to-login-to-vsphere-supervisor-when-configured-with-vcf-automation.html), William Lam
- [sorinplaton/vcf-gitops au tag blog-part1](https://github.com/sorinplaton/vcf-gitops/tree/blog-part1), le repo d'où proviennent chaque fichier et chaque commit de ce billet

------------------------------------------------------------------------

## Partie 2 : qui contrôle quoi, et ce qui casse {#part-2-who-controls-what-and-what-breaks}

La partie 1 a construit un cluster VKS et une application à partir d'un seul repo Git. Cette partie s'adresse au lecteur qui se pose maintenant les deux questions que toute équipe plateforme pose ensuite : si plus personne ne lance `kubectl apply`, qui a le droit de modifier quoi, et que se passe-t-il le jour où ça ne fonctionne pas ?

## Qui contrôle quoi {#who-controls-what}

GitOps ne supprime pas le contrôle d'accès ; il le déplace. Une fois que plus personne ne lance `kubectl apply`, les questions deviennent : qui peut modifier l'état désiré, qu'est-ce qu'Argo CD a le droit d'en faire, et que peut atteindre physiquement le moteur ? Quatre couches y répondent, et chacune appartient à une personne différente.

<figure class="diagram">
<img src="/images/gitops/diagram-control-layers.webp" loading="lazy" alt="couches de contrôle · Git, Argo CD, credentials des cibles, vSphere" />
<figcaption aria-hidden="true">couches de contrôle · Git, Argo CD, credentials des cibles, vSphere</figcaption>
</figure>

### Couche 1 : Git décide de ce qui peut devenir l'état désiré {#layer-1-git-decides-what-may-become-desired-state}

C'est le contrôle CI que la plupart des équipes ont déjà. Protégez `main` pour que rien n'y arrive sans revue, exigez une revue de pull request et utilisez `CODEOWNERS` pour que le dossier `clusters/` demande un réviseur de l'équipe plateforme tandis que `apps/podinfo/` demande l'équipe applicative. Donnez à Argo CD un token **en lecture seule** quand vous lancez `argocd repo add` : il ne fait jamais que du pull, il ne devrait donc jamais pouvoir faire de push. Avec cela en place, un mauvais changement nécessite qu'un réviseur le laisse passer, et la piste d'audit, c'est l'historique des commits.

### Couche 2 : l'instance Argo CD décide de qui peut agir, et à l'intérieur de quelle clôture {#layer-2-the-argo-cd-instance-decides-who-may-act-and-inside-which-fence}

Les contrôles sont ici propres à chaque instance : chaque équipe qui reçoit son propre objet `ArgoCD` en reçoit donc sa propre copie. Trois objets comptent. La **politique RBAC** associe des utilisateurs ou des groupes SSO à des rôles, et des rôles à des verbes ; toute personne connectée est en lecture seule tant qu'une politique ne dit pas le contraire. L'**AppProject** est la clôture dans laquelle une Application doit tenir : quels repos peuvent servir de source, quelles paires cluster-namespace peuvent servir de destination, quels types de ressources peuvent être créés et quand les syncs peuvent s'exécuter. La **politique de sync de l'Application** est le réglage propre à chaque application : le sync manuel signifie qu'un humain appuie sur le bouton après revue ; le mode automatisé signifie que Git est appliqué dès qu'il change ; `selfHeal` annule la dérive manuelle ; `prune` supprime les objets retirés de Git.

L'organisation recommandée est de deux Projects par instance : `infra` (le Supervisor comme seule destination, types `cluster.x-k8s.io` et `addons.kubernetes.vmware.com` autorisés) et `apps` (uniquement les endpoints de clusters VKS, aucun type à l'échelle du cluster). Une équipe applicative dont les Applications vivent dans `apps` ne peut pas en pointer une vers le Supervisor, même si son rôle RBAC l'autorise à créer des Applications, parce que le Project refuse la destination. Le `policy.csv` complet et le YAML de l'`AppProject` se trouvent à l'annexe B.

### Couche 3 : les credentials qu'Argo CD détient sur chaque cible {#layer-3-the-credentials-argo-cd-holds-on-each-target}

Quoi que disent la politique RBAC et les Projects, le moteur ne peut faire que ce que son service account sur la cible lui permet. `argocd cluster add` crée ces credentials et les stocke sous forme de cluster secrets dans le namespace d'hébergement, et les deux cibles donnent des droits très différents :

<div class="tbl">

| Cible | Service account | Droits | Conséquence |
|---|---|---|---|
| Namespace du Supervisor | Credentials liés par un RoleBinding à chaque namespace que vous avez indiqué | Limités au namespace | L'instance ne peut créer des objets du Supervisor que dans les namespaces que vous avez nommés ; les namespaces des autres tenants sont inaccessibles, même pour `admin` |
| Cluster VKS | `argocd-manager` dans `kube-system` | `cluster-admin` | Tout ce qu'Argo CD applique dans ce cluster est sans restriction ; la clôture, c'est l'AppProject, alors ne le sautez pas |

</div>

C'est pourquoi **une instance Argo CD par équipe constitue une vraie frontière de sécurité sur VCF**, et pas seulement une question de propreté : l'instance d'une équipe ne détient des credentials que pour ses propres namespaces et clusters. L'Operator rend les instances peu coûteuses ; profitez-en.

### L'auto-attach en 9.1.1 : enregistrer la cible 2 sans la CLI {#auto-attach-on-911-registering-target-2-without-the-cli}

La partie 1 enregistre le cluster VKS à la main avec `vcf argocd cluster add`. À partir de 9.1.1, le Supervisor Service livre aussi un composant d'auto-attach VKSM qui peut rattacher lui-même les nouveaux clusters VKS à une instance Argo CD, en créant les credentials de la couche 3 ci-dessus sans que personne lance la CLI. Il n'ajoute pas de nouveau type de cible ; il automatise l'enregistrement de la cible 2.

Il agit selon une politique, et aucune politique n'existe par défaut. Dans ce lab, le pod d'auto-attach tournait dans `svc-auto-attach-5gei8`, et pourtant :

    kubectl get entitymanagementpolicies -A
    # No resources found

Une heure et 46 minutes après la fin du sync du cluster, `vcf argocd cluster list` n'affichait toujours qu'une seule ligne, le vSphere Namespace. Le format de la politique lui-même n'a pas été testé ici.

Deux conséquences pratiques en découlent. D'abord, vérifiez avant de compter dessus : si cette commande ne renvoie rien, enregistrez le cluster à la main comme dans la partie 1. Ensuite, une fois qu'une politique existe, traitez-la comme n'importe quel autre contrôle de la couche 3 : elle décide des clusters qu'une instance Argo CD peut atteindre, elle a donc sa place dans la même revue que les service accounts `argocd-manager` qu'elle remplacerait autrement.

### Couche 4 : vSphere décide qui peut même atteindre la plateforme {#layer-4-vsphere-decides-who-may-reach-the-platform-at-all}

Sous tout cela se trouvent vSphere SSO et les permissions de namespace. Créer l'objet `ArgoCD` nécessite *edit* sur le namespace d'hébergement ; créer un cluster VKS par la cible 1 nécessite le service account de l'instance, que le propriétaire du namespace a accordé avec `cluster add` ; et ce que ce cluster peut être est borné par les classes de VM, les politiques de stockage et les quotas que l'admin vSphere a associés au namespace. Les humains atteignent un cluster VKS avec `vcf context create`, qui fait correspondre leur rôle vSphere sur le namespace à un rôle dans le cluster, et le cluster lui-même impose le profil restreint de la Pod Security Admission ; c'est pourquoi podinfo porte un security context.

### Alors, qui contrôle un cluster VKS ? {#so-who-controls-a-vks-cluster}

Trois parties, par ordre de préséance :

1.  **L'admin vSphere** possède le namespace dans lequel vit le cluster. Il décide des classes de VM et du stockage que le cluster peut utiliser, de qui a *edit* sur le namespace et de l'admission ou non d'une instance Argo CD par `cluster add`. Supprimez le namespace, et le cluster disparaît avec lui.
2.  **L'équipe plateforme, par Git et le Project `infra`.** La forme du cluster (versions, nombres de nodes, add-ons) est l'objet `Cluster` dans Git ; la modifier suppose un commit révisé et un sync par quelqu'un dont le rôle Argo CD autorise le sync de `infra/*`.
3.  **L'équipe applicative, par Git et le Project `apps`.** Elle contrôle ce qui tourne dans le cluster et rien du cluster lui-même : la destination de son Project est l'endpoint d'API VKS, son rôle RBAC n'atteint que `apps/*`, et même les credentials cluster-admin d'Argo CD ne peuvent être utilisés que par des Applications qui tiennent dans ce Project.

Les opérations du jour 2 en découlent naturellement. Mettre à niveau un cluster, c'est un commit sur `version` par l'équipe plateforme ; mettre à l'échelle un node pool, c'est un commit sur `replicas` ; ajouter une équipe, c'est un nouveau Project, un nouveau dossier Git avec sa ligne `CODEOWNERS` et une ligne `g` dans la politique RBAC.

### Supprimer une Application supprime ce qu'elle a construit {#deleting-an-application-deletes-what-it-built}

La même boucle qui transforme un commit en six VM transforme une suppression en zéro. Supprimer une Application depuis l'interface d'Argo CD, ou avec `argocd app delete`, est par défaut une suppression en cascade : chaque ressource que gère l'Application est supprimée avec elle. Pour `demo-vks-cluster`, cette ressource est l'objet Cluster, et le Supervisor y répond en supprimant les trois VM de control plane, les trois VM worker, leurs disques et tout ce qui tourne dans le cluster, y compris les workloads de `podinfo-vks`. La cible VKS dans Argo CD reste pointée vers une API qui n'existe plus.

Deux autres chemins mènent au même résultat :

- **Le prune.** Avec le prune activé sur l'Application, supprimer ou renommer `clusters/demo-vks-cluster/cluster.yaml` dans Git supprime le cluster au sync suivant. C'est l'une des raisons pour lesquelles `demo-vks-cluster` tourne en sync manuel et sans prune.
- **kubectl sur l'Application.** Quand l'Application porte le finalizer `resources-finalizer.argocd.argoproj.io`, `kubectl delete application` agit en cascade exactement comme l'interface. Sans le finalizer, les ressources restent.

Les garde-fous, de celui qui protège le plus à celui qui protège le moins :

1.  **Marquer l'objet Cluster pour qu'Argo CD ne le supprime jamais, ni directement ni par prune.** `Delete=false` le conserve quand l'Application est supprimée ; `Prune=false` le conserve quand il disparaît de Git, et l'Application s'affiche alors OutOfSync. Le cluster de la partie 1 ne porte pas cette annotation ; un repo de production devrait la porter. L'extrait se trouve dans le repo sous examples/protect-cluster.yaml.

<!-- -->

    metadata:
      name: demo-vks-cluster
      annotations:
        argocd.argoproj.io/sync-wave: '0'
        argocd.argoproj.io/sync-options: Delete=false,Prune=false

1.  **Supprimer sans cascade quand on retire une Application volontairement.** `argocd app delete demo-vks-cluster --cascade=false` ne retire que le suivi par Argo CD ; le cluster et ses VM continuent de tourner.
2.  **Retirer le verbe delete.** Dans la politique RBAC, n'accordez `applications, delete` sur `infra/*` qu'à un rôle d'urgence (break-glass), pas au rôle plateforme du quotidien. La ligne `p, role:platform, applications, *, */*, allow` de l'annexe B est trop large pour la production précisément pour cette raison.
3.  **Les fenêtres de sync aident contre le prune, pas contre les suppressions.** Une fenêtre d'interdiction sur le Project `infra` bloque les syncs, et donc le prune, en dehors des heures de changement, mais elle n'empêche pas quelqu'un de supprimer l'Application.

Rien du côté vSphere n'annule la suppression : les VM sont retirées par le Supervisor comme lors de n'importe quel démantèlement Cluster API. La récupération consiste à synchroniser de nouveau l'Application et à attendre une nouvelle construction, avec de nouvelles VM et un cluster vide.

## Recommandations pour la production {#recommendations-for-production}

Le lab a volontairement pris des raccourcis. Avant que ce modèle porte quoi que ce soit d'important :

- **Mettez l'instance en HA.** Réglez les réplicas de `server`, `repo` et `controller` à plus d'un dans la spec `ArgoCD` ; l'Operator s'occupe du reste. Relevez les limites de ressources par défaut (controller 2 CPU / 2 Go, repo server 1 CPU / 0,5 Go) dès que vous avez plus d'une poignée d'applications ; Broadcom qualifie les valeurs par défaut de valeurs de preuve de concept.
- **OIDC, pas l'admin local.** Raccordez l'instance à votre fournisseur d'identité et associez les groupes dans la politique RBAC ; faites disparaître le mot de passe admin initial par rotation.
- **Sync manuel sur `infra`, automatisé sur `apps`.** Un changement du nombre de nodes ou de la version de Kubernetes devrait attendre qu'une personne appuie sur *Sync* ; le déploiement d'une application, non.
- **Activez le contrôleur ApplicationSet dès le premier jour.** Il est livré avec l'Operator mais désactivé par défaut ; vous en voudrez dès que vous aurez un deuxième cluster.
- **Un Harbor régional.** Répliquez l'image podinfo et chaque paquet d'add-on dans Harbor, pour qu'une construction de cluster ne dépende jamais de l'accès Internet des workers.
- **Prenez possession de la gateway DVC avant d'activer le service.** En production, l'adresse de gateway d'une Distributed VLAN Connection appartient à l'équipe réseau ; confirmez qu'elle répond au ping et à l'ARP, et que le DNS répond en UDP depuis le nouveau VLAN, avant le premier pull d'image.
- **Protégez l'infrastructure contre la suppression.** Annotez chaque objet Cluster avec `argocd.argoproj.io/sync-options: Delete=false,Prune=false`, et réservez le verbe delete sur `infra/*` à un rôle d'urgence, comme décrit plus haut.

## Sous le capot : comment le VPC du tenant atteint l'extérieur {#under-the-hood-how-the-tenant-vpc-reaches-the-outside}

La partie 1 s'en est tenue à un seul schéma du lab. Celui-ci zoome sur la mise en réseau du Supervisor : comment un pod du projet `holo-east` sort par la transit gateway et le VLAN 200, côte à côte avec les namespaces système sur le VLAN 24. C'est le contexte de la plupart de ce qui suit dans *Quand ça casse*. Le même modèle VPC fonctionne aussi sans overlay : [VKS on a VLAN-backed VPC without NSX TEPs](/fr/posts/vks-without-teps/) construit un domaine de workload sans TEP à côté de celui-ci, et une instance Argo CD configurée comme celle-ci y déploie également.

<figure class="diagram">
<img src="/images/gitops/diagram-lab-network.webp" loading="lazy" alt="réseau du lab · projets holo-east et default, VLAN 200 et VLAN 24, holorouter, VyOS" />
<figcaption aria-hidden="true">réseau du lab · projets holo-east et default, VLAN 200 et VLAN 24, holorouter, VyOS</figcaption>
</figure>

Le tenant `holo-east` a été créé comme organisation VCF Automation. Cela lui donne son propre projet NSX, une transit gateway, un bloc d'IP privé et deux VPC, un par vSphere Namespace. Sa connectivité externe est une **Distributed VLAN Connection** (DVC) plutôt qu'un Tier-0 adossé à des edges : la transit gateway applique le SNAT au trafic sortant sur le cluster VNA, vers une adresse du bloc externe, et remet la trame à quiconque répond à l'ARP pour l'adresse de gateway de la DVC sur le VLAN 200. Le trafic entrant ne demande aucun routage, parce que le côté physique voit le bloc externe comme directement connecté. Pas de BGP, pas de routes statiques côté NSX, pas d'edge.

Les namespaces système du Supervisor utilisent exactement le même modèle dans le projet NSX *default* sur le VLAN 24, et tiraient des images depuis des jours. Ils constituaient donc le groupe témoin parfait : la seule différence entre ce qui fonctionnait et ce qui ne fonctionnait pas, c'était qui se trouvait à l'autre bout du VLAN.

<div class="tbl">

| Objet | Où il est configuré | Valeur |
|---|---|---|
| VPC vpc-argocd-demo | Créé par VCF Automation pour le namespace de l'organisation, dans le projet NSX holo-east | pods 172.31.0.0/27 (issu du bloc TGW privé), privé 10.24.24.0/24 |
| SNAT de la transit gateway | NSX, réalisé automatiquement par le profil de connectivité | source sortante 10.200.200.3 |
| DVC d-vlan-1 | NSX, créée par la mise en réseau régionale de VCF Automation | vlan_id 200, gateway_addresses 10.200.200.1/24, bloc 10.200.200.0/24 |
| DVC du projet default | NSX, créée à l'activation du Supervisor | VLAN 24, gateway 10.1.8.129/25, bloc 10.1.8.128/25 ; SNAT 10.1.8.130 |
| Load balancer du VPC | Interface NSX, sur chaque VPC de l'organisation, taille Small ; ajouté à la main | publié au Supervisor sous la forme nsxLoadBalancerPath ; VIP Argo CD 10.200.200.5 |
| Holorouter eth1.24 | Déjà présent dans Holodeck | 10.1.8.129/25, VLAN 24 |
| Holorouter eth1.200 | Ajouté comme sous-interface, rendu persistant par une unité systemd oneshot | 10.200.200.1/24, VLAN 200, MTU 8000 |
| DNS du holorouter | Technitium, inchangé | répond sur 10.1.1.1 (VLAN 10) ; UDP uniquement depuis les VLAN directement connectés |
| DNAT du holorouter pour le VLAN 200 | Ajouté pour les nodes VKS (partie 1, étape 5) | UDP 53 vers 10.1.1.1 depuis le VLAN 200 redirigé vers 10.200.200.1:53 |
| VyOS eth1 vif 200 | Supprimé ; il a brièvement possédé 10.200.200.1 le deuxième jour | remplacé par la route statique 10.200.200.0/24 next-hop 10.0.6.33, pour que le routage amont atteigne toujours le bloc |

</div>

Pourquoi la gateway a fini sur le holorouter et non sur le VyOS, c'est l'histoire du post-mortem, résumée juste en dessous. En bref : placer le VLAN 200 à côté du VLAN 24 sur le routeur qui héberge déjà le DNS a fait du nouveau VPC un client local de ce DNS, le seul type de client auquel il répond en UDP. Les nodes VKS ont été une surprise plus tardive : ils sont configurés avec `10.1.1.1` comme résolveur et n'obtenaient toujours pas de réponse UDP de cette adresse, alors que l'adresse du holorouter sur le VLAN 200, `10.200.200.1`, leur répondait. D'où la règle DNAT supplémentaire.

## Quand ça casse {#when-it-breaks}

La première tentative de l'étape 3 dans ce lab n'a produit absolument aucun pod en fonctionnement. L'enquête complète se trouve dans le post-mortem qui accompagne ce billet, *The VLAN Nobody Was Listening On* ; la version courte, ce sont trois causes racines et les débris qu'elles laissent derrière elles.

<div class="tbl">

| Cause racine | Symptôme | Correction |
|---|---|---|
| **Personne sur le VLAN 200.** VCF Automation a créé la DVC avec la gateway `10.200.200.1`, mais aucun routeur ne possédait cette adresse | Chaque objet `Image` bloqué en *Resolving* puis en *Failed* ; le fetcher signalait `lookup projects.packages.broadcom.com: i/o timeout` | Sous-interface `eth1.200` avec `10.200.200.1/24` sur le holorouter, à côté du VLAN 24 |
| **Le DNS répondait en TCP mais pas en UDP depuis l'extérieur.** Le DNS du lab ne répondait qu'aux clients situés sur son propre VLAN ou sur un VLAN directement connecté ; une gateway sur le VyOS faisait du VPC un client extérieur | Même timeout une fois la gateway en place ; `dig +tcp` fonctionnait, `dig` tout court non | Même correction : avec le VLAN 200 sur le holorouter, le VPC est un client local |
| **Les VPC créés à l'avance n'ont pas de load balancer.** Le NSX operator ne crée de LB de VPC que pour les VPC qu'il crée lui-même | `argocd-server` EXTERNAL-IP `<pending>` ; NCP inscrit dans ses logs `NCP00270 Lbs <ns> is not found` sous un générique *Generic error occurred during realizing network for Service* | Créer le LB sur chaque VPC de l'organisation dans l'interface NSX, attendre une synchronisation de l'operator |

</div>

Deux choses restent cassées une fois le réseau corrigé :

- Le Job ponctuel `argocd-redis-secret-init` a épuisé ses tentatives pendant la panne, et kapp ne recrée pas un Job en échec qui existe déjà. Chaque nouveau pod meurt alors avec `secret "argocd-redis" not found`. Supprimez le Job ; la réconciliation suivante du `PackageInstall` (10 minutes) le fait revenir.
- Comme les pods du Supervisor sont des VM, un pod dont un secret manque passe à la phase terminale `ProviderFailed` et le ReplicaSet le remplace toutes les 15 secondes. Trois heures ont produit 264 cadavres. `kubectl delete pods -n <ns> --field-selector status.phase=Failed` fait le ménage.

<!-- -->

    kubectl delete job argocd-redis-secret-init -n argocd-demo-wqfbg
    kubectl delete pods -n argocd-demo-wqfbg --field-selector status.phase=Failed

La leçon qui vaut au-delà de Holodeck : l'adresse de gateway d'une Distributed VLAN Connection est une promesse que vous faites à NSX. Confirmez-la par ping et ARP, et testez le DNS en UDP depuis le nouveau VLAN, avant d'accuser l'Operator.

### Quand la construction du cluster se bloque {#when-the-cluster-build-stalls}

La partie 1 cite les durées des étapes sans incident. En temps réel, ce lab a mis 11 heures 43 minutes entre le sync et un cluster 3 + 3 prêt, et rien de ce temps n'était imputable à Git ni à Argo CD : l'application était Synced en quelques secondes et l'est restée pendant que le Supervisor se battait contre les problèmes ci-dessous. Argo CD n'aide pas beaucoup ici, parce que l'application ne contient qu'un objet Cluster et affiche Progressing jusqu'à la fin. Surveillez plutôt le Supervisor :

    kubectl -n podinfo-demo-vkjhr get cluster,machines -o wide
    kubectl -n podinfo-demo-vkjhr get vm -o wide
    kubectl -n podinfo-demo-vkjhr get events --sort-by=.lastTimestamp | tail -20

<div class="tbl">

| Symptôme | Ce que c'était | Correction |
|---|---|---|
| `kubectl get kr` répond `No resources found`, si bien qu'aucune version ne peut être référencée | Une bibliothèque de contenu était rattachée au Supervisor, mais aucune n'était définie comme bibliothèque du Kubernetes Service | Définir la bibliothèque abonnée comme bibliothèque de contenu du Kubernetes Service (partie 1, étape 4) ; les versions apparaissent en moins d'une minute |
| VM Powered On sans IP ; condition de Machine `WaitingForNetworkAddress` ; condition de VM `VMware Tools is not running` ; la console affiche `Failed to mount /sysroot` et `Entering emergency mode` | Quatre des cinq premières VM de nodes se sont arrêtées au démarrage. Aucune cause racine n'a été établie. Le log de promotion de disque d'une VM bloquée montrait des débits tombant à 0 ko/s, contre 85 Mo/s pour la VM qui a démarré, ce qui désigne le stockage imbriqué sous charge parallèle | Rien n'a été modifié. Le MachineHealthCheck du cluster a remplacé chaque machine bloquée après son délai de démarrage d'une heure, et les remplaçantes ont fini par démarrer. Les deux dernières VM, créées une à la fois, ont démarré du premier coup. vCenter refuse la réinitialisation manuelle de ces VM, même pour administrator@vsphere.local ; laissez donc Cluster API s'en charger |
| `vcf argocd app wait` expire après 3600 s avec l'application en Synced / Progressing | La construction était toujours en cours, pour la raison ci-dessus | Augmenter `--timeout`, ou cesser d'attendre Argo CD et surveiller les Machines |
| Une VM n'est jamais créée ; la VSphereMachine indique `insufficient storage quota for storage policy` | Six disques de nodes de 20 Gio ne tiennent pas dans les 100000Mi de la classe de namespace `small` | Relever la limite de stockage du namespace (et la mémoire, pour 18 Gio de VM de nodes) dans VCF Automation ; la VM est créée quelques secondes après le changement |
| Pods en `ImagePullBackOff` : `lookup ghcr.io on 127.0.0.53:53 ... i/o timeout` | Le résolveur des nodes, 10.1.1.1, ne répondait pas en UDP depuis le VLAN 200 | DNAT sur le holorouter de 10.1.1.1:53 vers 10.200.200.1:53 pour le VLAN 200 (partie 1, étape 5) |
| Le cluster VKS n'apparaît jamais dans Settings \> Clusters | L'auto-attach tourne mais n'a aucune EntityManagementPolicy sur laquelle agir | `vcf argocd cluster add demo-vks-cluster`, ou définir la politique |
| `error: You must be logged in to the server (Unauthorized)` au bout de quelques heures | Le token de `kubectl vsphere login` a expiré | Se reconnecter ; les longues constructions durent plus longtemps qu'une session |

</div>

<figure class="shot">
<img src="/images/gitops/p2-34.webp" loading="lazy" alt="Console de VM d'un worker bloqué : FAILED Failed to mount /sysroot, deux lignes Dependency failed, Entering emergency mode" />
</figure>

*À quoi ressemblait un node bloqué vu de vCenter pendant la première construction, capturé avec `govc vm.console -capture` : l'invité n'a jamais monté son système de fichiers racine, si bien que VMware Tools n'a jamais démarré et que la VM n'a jamais rapporté d'IP. La septième VM, ajoutée plus tard toute seule, a démarré du premier coup.*

La leçon pour une démo : construisez le cluster la veille, ou au moins lancez le sync avant l'arrivée du public. Le passage du commit à la VM est réel et rapide ; le stockage d'un lab imbriqué, non.

## Annexe A : l'hôte de rebond {#appendix-a-the-jump-host}

Tout ce qui se trouve dans la partie 1 est lancé depuis une VM Ubuntu sur le réseau externe du lab, construite avec cloud-init. Elle n'est pas dans Holodeck ; elle a donc besoin de deux choses avant que le moindre outil VCF fonctionne : une route vers les réseaux Holodeck via le VyOS, et la résolution de noms pour `*.vcf.lab`. Les deux vivent dans netplan, pour survivre à un redémarrage.

``` yaml
# example file name; on the lab jump host the same route and DNS sit in a NetworkManager-generated file under /etc/netplan
network:
  version: 2
  ethernets:
    ens192:
      routes:
      - to: 10.1.0.0/20          # Holodeck management, VLAN 24 and the SNAT/VIP block behind VLAN 200
        via: 10.0.6.33           # the VyOS
      nameservers:
        addresses: [10.0.4.17]   # resolves *.vcf.lab; fall back to IPs if you skip this
        search: [vcf.lab]
```

    sudo netplan apply
    ip route get 10.1.1.24            # NSX Manager of the workload domain, via 10.0.6.33
    dig +short sddcmanager-a.site-a.vcf.lab

Les outils, dans l'ordre où ils sont utilisés. Tous les binaires Broadcom proviennent du download VCF 9.1 sur le portail de support (VCF Consumption CLI, son bundle de plugins et l'Argo CD Supervisor Service, avec lequel est livrée la CLI `argocd` personnalisée).

    # VCF CLI and its plugin bundle
    tar -xzvf VCF-Consumption-CLI-Linux_AMD64-9.1.0.tar.gz
    sudo install ./vcf-cli-linux_amd64 /usr/local/bin/vcf
    mkdir -p ~/vcf-plugin-bundle
    tar -xvzf VCF-Consumption-CLI-PluginBundle-Linux_AMD64-9.1.0.0.25305443.tar.gz -C ~/vcf-plugin-bundle/
    vcf plugin install all --local-source ~/vcf-plugin-bundle/
    vcf version && vcf plugin list     # argocd  v1.2.0  installed

    # kubectl and the vSphere plugin, served by the Supervisor itself
    curl -sk https://<supervisor-ip>/wcp/plugin/linux-amd64/vsphere-plugin.zip -o vsphere-plugin.zip
    unzip -o vsphere-plugin.zip && sudo install bin/kubectl bin/kubectl-vsphere /usr/local/bin/

    # Argo CD CLI: on this jump host it is the argocd plugin of the VCF CLI, typed `vcf argocd ...`.
    # The Argo CD Supervisor Service download also carries a standalone customised binary;
    # if you install that one instead, drop the `vcf` prefix from the commands in Part 1.
    vcf argocd version                 # v1.2.0 (the plugin takes no --client flag)

    # Carvel tools, to read the service bundle the Operator installs from
    curl -L https://carvel.dev/install.sh | sudo bash     # imgpkg, kapp, ytt, kbld

    # everything else
    sudo apt-get install -y git dnsutils jq

Connectez-vous une fois par Supervisor et une fois par cluster VKS. `vcf context create` écrit les credentials dans `~/.kube/config` (ou `$KUBECONFIG`), si bien que `kubectl` et `argocd` les utilisent sans autre option ; `kubectl vsphere login` est l'équivalent plus ancien et fonctionne toujours.

    # Supervisor, as the vSphere admin; the context name is yours to pick
    vcf context create sv-a --endpoint https://<supervisor-ip> \
      --username administrator@vsphere.local --type k8s
    vcf context use sv-a:argocd-demo-wqfbg          # hosting namespace

    # a VKS cluster, once it exists (Part 1, step 5)
    vcf context create demo-vks --endpoint https://<supervisor-ip> \
      --username administrator@vsphere.local --type k8s \
      --workload-cluster-namespace podinfo-demo-vkjhr --workload-cluster-name demo-vks-cluster
    vcf context list

    # what the lab run actually used, and what to re-run when kubectl says Unauthorized
    kubectl vsphere login --server 10.1.8.132 --vsphere-username administrator@vsphere.local \
      --insecure-skip-tls-verify                                      # Supervisor context 10.1.8.132
    kubectl vsphere login --server 10.1.8.132 --vsphere-username administrator@vsphere.local \
      --insecure-skip-tls-verify --tanzu-kubernetes-cluster-namespace podinfo-demo-vkjhr \
      --tanzu-kubernetes-cluster-name demo-vks-cluster                # context demo-vks-cluster

<figure class="shot">
<img src="/images/gitops/p2-35.webp" loading="lazy" alt="Terminal sur l'hôte de rebond : vcf version v9.0.2, vcf plugin list avec argocd v1.2.0, client kubectl v1.31.11, vcf argocd version v1.2.0, vcf context list" />
</figure>

*La boîte à outils de l'hôte de rebond : VCF CLI v9.0.2 avec le plugin `argocd` v1.2.0, le `kubectl` du Supervisor (v1.31.11) et les contextes VCF Automation.*

Le repo `holodeck-automation`, sur le même hôte, contient les utilitaires PowerShell employés autour du lab (par exemple pour activer SSH sur les nodes VNA via l'API NSX) ; aucun n'est nécessaire pour ce pas-à-pas.

## Annexe B : politique RBAC et AppProject {#appendix-b-rbac-policy-and-appproject}

La politique RBAC vit dans la ConfigMap `argocd-rbac-cm` du namespace d'hébergement. La politique par défaut pour toute personne connectée est `role:readonly` ; seul `admin` a `role:admin` d'emblée.

    # argocd-rbac-cm  data.policy.csv
    p, role:platform, applications, *,      */*,        allow
    p, role:platform, projects,     *,      *,          allow
    p, role:platform, clusters,     *,      *,          allow
    p, role:app-team, applications, get,    apps/*,     allow
    p, role:app-team, applications, sync,   apps/*,     allow
    p, role:app-team, applications, action/*, apps/*,  allow
    g, platform-admins@holo-east, role:platform
    g, podinfo-devs@holo-east,    role:app-team

L'équipe applicative peut voir et synchroniser les Applications du Project `apps` et y exécuter des actions (redémarrer un rollout, par exemple), mais ne peut ni créer d'Applications, ni toucher au Project `infra`, ni enregistrer de cibles. Avec des comptes locaux, les lignes `g` nomment le compte plutôt qu'un groupe ; avec OIDC, elles nomment le claim de groupe.

Le Project `apps` limite les sources à ce repo et les destinations au cluster VKS et aux namespaces `podinfo*`, n'autorise aucun objet à l'échelle du cluster, met sur liste noire `ResourceQuota` et `LimitRange`, et interdit les syncs à partir du vendredi 22 h pendant 48 heures. Le YAML complet est [`examples/projects/appproject-apps.yaml`](https://github.com/sorinplaton/vcf-gitops/blob/blog-part1/examples/projects/appproject-apps.yaml), et la politique ci-dessus est [`examples/rbac/policy.csv`](https://github.com/sorinplaton/vcf-gitops/blob/blog-part1/examples/rbac/policy.csv).

Deux mises en garde. `CreateNamespace=true` sur `podinfo-vks` crée un Namespace, qui est un objet à l'échelle du cluster ; avec une `clusterResourceWhitelist` vide, il faut donc soit mettre `Namespace` sur liste blanche, soit créer le namespace en dehors de l'application. Et rien de tout cela n'a été appliqué dans le lab : l'`argocd-rbac-cm` en service a un `policy.csv` vide ([export](https://github.com/sorinplaton/vcf-gitops/blob/blog-part1/platform/argocd/argocd-rbac-cm.yaml)) et les trois Applications tournent dans le Project `default`.

Un second Project, `infra`, autorise les types `cluster.x-k8s.io` et `addons.kubernetes.vmware.com`, avec le Supervisor comme seule destination.

### Sources {#sources-1}

- *The VLAN Nobody Was Listening On*, le post-mortem du lab sur lequel repose cette partie
- [Argo CD RBAC configuration](https://argo-cd.readthedocs.io/en/stable/operator-manual/rbac/) et [Projects](https://argo-cd.readthedocs.io/en/stable/user-guide/projects/), documentation Argo CD upstream
- Bundle de documentation VMware Cloud Foundation 9.1 : *VCF Edge: Single Node with Argo CD Design Blueprint*, conception du dimensionnement et de l'authentification (p. 1371–1373)
- README de [holodeck-automation](https://github.com/sorinplaton/holodeck-automation) ; [Air-gapped VCF 9.1 guide](https://github.com/vmware/vsphere-supervisor/blob/main/airgapped/air-gapped-vcf91.md) ; [VCF CLI context command reference](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/building-your-cloud-applications/getting-started-with-the-tools-for-building-applications/installing-and-using-vcf-cli-v9/command-reference2/system-commands(1)/context2.html)
- [sorinplaton/vcf-gitops au tag blog-part1](https://github.com/sorinplaton/vcf-gitops/tree/blog-part1), le repo à partir duquel la partie 1 déploie, avec les exemples de la partie 2 sous `examples/`

Chaque fichier et chaque commit cité ici se trouve dans [sorinplaton/vcf-gitops](https://github.com/sorinplaton/vcf-gitops/tree/blog-part1) au tag `blog-part1`.
