---
title: "Argo CD comme VCF Service : prise en main de la tech preview de VCF 9.1.1"
date: 2026-10-22
description: "VCF 9.1.1 livre Argo CD comme VCF Service : un tenant commande une instance dans VCF Automation et se connecte en SSO. Le remplacement du Supervisor Service installé depuis vCenter dans un labo Holodeck, la reprise des applications en cours d'exécution, et les six choses qui m'ont fait perdre du temps."
summary: "Dans VCF 9.1.1, Argo CD devient un VCF Service (tech preview) : installé une fois depuis VCF Automation, commandé par le tenant comme un namespace ou une VM, avec le SSO de VCF Automation au lieu d'un mot de passe admin partagé, et des objets ManagedEntity au lieu de tokens de cluster. La migration depuis l'ancien Supervisor Service, le YAML, la démo, et ce qui m'a fait trébucher."
tags: ["VCF 9.1.1", "Argo CD", "GitOps", "VCF Automation", "VKS", "Supervisor", "Tech preview", "Holodeck"]
categories: ["Notes"]
showToc: true
tocOpen: false
---

VCF 9.1.1 livre Argo CD comme **VCF Service** (tech preview) : un tenant commande une instance Argo CD dans VCF Automation de la même façon qu'il commande un namespace ou une VM, et s'y connecte avec son compte VCF Automation. J'ai remplacé par ce service l'Argo CD installé depuis vCenter sur mon premier workload domain en une journée, et j'ai repris les applications en cours d'exécution sans les redéployer.

Pour le point de départ, la version installée depuis vCenter est celle décrite dans [GitOps sur VCF 9.1 : ce que demande vraiment l'Argo CD Supervisor Service](/fr/posts/gitops-argocd-vcf-91/). Ce billet décrit ce qui change quand le même package passe plutôt par VCF Automation.

## Ce qui a changé

Jusqu'ici, Argo CD sur VCF était un **Supervisor Service** qu'un administrateur vSphere installait Supervisor par Supervisor depuis vCenter. Chaque instance avait son propre compte local `admin`, et l'équipe plateforme distribuait les mots de passe. Dans la 9.1.1, le même package (`argocd-service.vsphere.vmware.com`, version 1.2.0) s'installe depuis **VCF Automation**, et l'instance est créée par l'API de VCF Automation. Ce seul changement branche l'instance sur le modèle de tenants de VCF Automation : org, projet, région, authentification unique et comptes de service.

|  | Supervisor Service (avant) | VCF Service (tech preview 9.1.1) |
| --- | --- | --- |
| Installé depuis | vCenter, par Supervisor | portail Provider de VCF Automation, tous les Supervisors d'un coup |
| Instance créée par | l'administrateur vSphere, API du Supervisor | le tenant, API de VCF Automation (ou Build & Deploy → Services → GitOps) |
| Connexion | `admin` local + mot de passe | SSO de VCF Automation (OIDC), `vcf argocd login --sso` |
| Qui peut l'utiliser | quiconque a le mot de passe | les utilisateurs de VCF Automation, associés à des rôles Argo CD |
| Clusters | des Secrets avec des tokens | des objets `ManagedEntity`, aucun token à manipuler |

## Le labo

Un Holodeck VCF 9.1 imbriqué (site A) sur deux serveurs Dell. Org `holo-east`, région `east-1`, un workload domain avec un Supervisor et deux clusters VKS (`demo-vks-cluster`, `overlay-public`). Le repo Git est [github.com/sorinplaton/vcf-gitops](https://github.com/sorinplaton/vcf-gitops) ; tout ce qui figure dans ce billet se trouve dans `platform/argocd-vcfa/`.

## Pourquoi c'est important

Le gros gain : Argo CD cesse d'être une installation à part avec un mot de passe partagé et devient un service offert aux tenants du cloud privé.

**Libre-service pour les tenants.** Un administrateur d'org crée une instance depuis **Build & Deploy → Services → GitOps** ou avec un seul fichier YAML. Aucun ticket à l'équipe vSphere.

![Libre-service : un administrateur d'org crée l'instance Argo CD depuis le portail ou un seul fichier YAML, directement dans le namespace du projet ; aucun ticket à l'équipe vSphere](/images/argocd-tp/why-self-service.webp)

**Une seule connexion.** L'instance est enregistrée automatiquement comme client OIDC de VCF Automation (client, URI de redirection, PKCE). Les utilisateurs cliquent sur *Log in via VCF Automation* ; personne ne distribue le mot de passe `admin`.

![Une seule connexion : l'utilisateur ouvre l'interface d'Argo CD, est renvoyé vers VCF Automation pour l'authentification OIDC, et aucun mot de passe admin n'est distribué](/images/argocd-tp/why-one-login.webp)

**La multilocation est intégrée.** L'instance réside dans un namespace de projet, reçoit son propre compte de service VCF Automation et apparaît dans le portail de l'org à côté des VM et des clusters Kubernetes.

![Multilocation : dans l'org holo-east, le namespace du projet contient côte à côte des VM, des clusters Kubernetes et l'instance Argo CD, avec son propre compte de service](/images/argocd-tp/why-tenancy.webp)

**Des clusters sans tokens.** Un `ManagedEntity` pointe vers un cluster VKS ou un namespace de Supervisor ; le service écrit le secret de cluster d'Argo CD et tient ses credentials à jour.

![Des clusters sans tokens : un ManagedEntity enregistre la cible, le service écrit le secret de cluster dans l'instance Argo CD et le tient à jour, et Argo CD s'en sert pour déployer sur demo-vks-cluster](/images/argocd-tp/why-clusters-without-tokens.webp)

**Le cycle de vie depuis un seul endroit.** Le fournisseur fait l'upload et l'upgrade du service une seule fois dans VCF Automation ; il arrive sur chaque Supervisor. Les tenants choisissent, par instance, une version d'Argo CD prise en charge (3.4.4 ou 3.3.12 dans ce build).

![Cycle de vie : le fournisseur fait l'upload ou l'upgrade du service une fois dans VCF Automation et il arrive sur chaque Supervisor ; les tenants choisissent la version d'Argo CD par instance](/images/argocd-tp/why-lifecycle.webp)

**Du GitOps pour la plateforme aussi.** Le même Argo CD peut déployer les applications *et* les clusters VKS sur lesquels elles tournent : un commit peut ajouter un worker node aussi facilement qu'il modifie une application.

![GitOps pour la plateforme : Argo CD récupère les manifestes depuis GitHub, et un seul commit peut modifier podinfo ou l'objet Cluster demo-vks-cluster lui-même](/images/argocd-tp/why-platform-gitops.webp)

![Le portail de l'org : Build & Deploy → Services → GitOps liste l'instance avec son projet et son état](/images/argocd-tp/org-portal-gitops.webp)

*Le portail de l'org : **Build & Deploy → Services → GitOps** liste l'instance avec son projet et son état.*

## Comment les morceaux s'assemblent

![Architecture du labo : vous vous connectez par VCF Automation, Argo CD, dans son propre VPC, récupère le contenu depuis GitHub et déploie sur deux clusters VKS du Supervisor wld01](/images/argocd-tp/diagram-lab-architecture.webp)

L'instance occupe son propre namespace et son propre VPC. VCF Automation décide qui peut se connecter, Git contient ce qui doit tourner, et les ManagedEntities disent où.

## L'installation, étape par étape

Six étapes ont mené le labo de l'ancien Supervisor Service à une instance fonctionnelle avec SSO. Les étapes 1 et 2 sont des clics dans VCF Automation ; les étapes 3 à 5 sont du YAML dans Git, appliqué avec `platform/argocd-vcfa/apply.sh`.

1. **Retirer l'ancien service.** Sauvegarder les anciennes Applications, les secrets de cluster et les ManagedEntities (`kubectl get -o yaml`), supprimer l'ancienne instance `ArgoCD`, puis désinstaller l'Argo CD Supervisor Service installé depuis vCenter.
2. **Faire entrer le package dans VCF Automation et l'installer.** Faire le download de `VCF_SERVICE_ARGOCD` avec VCF Download Tool 9.1.1 (`vcf-download-tool artifacts download … --component=VCF_SERVICE_ARGOCD` ; l'outil 9.1.0 n'a pas de commande `artifacts`). Faire l'upload du `.tar` du dossier `-images/` sous **Service Management → Partner Services → Upload**, puis cliquer sur **Install**. Il apparaît sous **VCF Services** et est poussé vers chaque Supervisor.

   ![Portail Provider, Service Management : ArgoCD Service 1.2.0.25642124, Healthy](/images/argocd-tp/provider-service-management.webp)

3. **Réseau et namespace** (`./apply.sh network`, par l'API CCI de VCF Automation) : un VPC `vpc-argocd-gitops`, son attachement au transit gateway et son load balancer, puis un namespace de Supervisor de classe `medium`. Le namespace est créé à partir de `generateName: argocd-gitops-` ; il a été créé sous le nom `argocd-gitops-9xsrd`.
4. **L'instance** (`./apply.sh instance`, par l'endpoint de namespace que renvoie VCF Automation) :

   ```yaml
   apiVersion: argocd-service.vsphere.vmware.com/v1alpha1
   kind: ArgoCD
   metadata:
     name: argocd-gitops
   spec:
     version: 3.4.4+vmware.1-vks.1
     serverSideDiff: true        # required to manage VKS clusters
     enableLoadBalancer: true
     rbac:
       defaultPolicy: role:readonly
       policy: |
         g, <VCF Automation user id>, role:admin
   ```

   Environ deux minutes plus tard, elle était `Ready` à `https://10.200.200.5`, avec un client OIDC et un compte de service VCF Automation dans son statut.
5. **Donner son rôle au compte de service.** Dans l'org holo-east, attribuer à `status.serviceAccounts.platform` le rôle **ArgoCD Instance** (un rôle global que VCF Automation publie dans l'org).
6. **Se connecter.** Ouvrir l'interface et cliquer sur *Log in via VCF Automation*, ou lancer `vcf argocd login 10.200.200.5 --sso`.

![La page de connexion d'Argo CD propose Log in via VCF Automation](/images/argocd-tp/login-page.webp)

*La page de connexion d'Argo CD propose **Log in via VCF Automation**.*

![Le bouton redirige vers la page de connexion de l'org holo-east de VCF Automation (OIDC avec PKCE)](/images/argocd-tp/login-vcfa-redirect.webp)

*Le bouton redirige vers la page de connexion de l'org holo-east de VCF Automation (OIDC avec PKCE).*

## Les clusters et la première application

Trois objets `ManagedEntity` ont enregistré les deux clusters VKS et un namespace de Supervisor, et la première application a repris le podinfo en cours d'exécution sans redémarrer un seul pod.

Sur le même Supervisor, un cluster se résume à un court objet (appliqué au Supervisor dans `argocd-gitops-9xsrd`) :

```yaml
apiVersion: argocd-service.vsphere.vmware.com/v1alpha1
kind: ManagedEntity
metadata:
  name: demo-vks-cluster
spec:
  clusterName: demo-vks-cluster
  targetRef: {apiGroup: cluster.x-k8s.io, kind: Cluster, name: demo-vks-cluster, namespace: podinfo-demo-dx643}
```

Le compte de service Supervisor de l'instance (`argocd-k8s-sa`) a d'abord besoin du rôle `edit` dans le namespace qui contient les clusters, par un RoleBinding. Une fois ce dernier en place, le service écrit un secret de cluster Argo CD par entité :

![Settings → Clusters : demo-vks-cluster, overlay-public et le namespace podinfo-demo](/images/argocd-tp/settings-clusters.webp)

*Settings → Clusters : `demo-vks-cluster`, `overlay-public` et le namespace `podinfo-demo`.*

La première Application pointe vers `apps/podinfo` dans le repo, avec `demo-vks-cluster` comme destination, et la synchronisation automatique, le prune et le self-heal activés. Le workload tournait déjà depuis l'ancien Argo CD, donc la nouvelle instance l'a trouvé identique à Git et en a simplement pris possession : dans l'arbre, les pods ont 6 jours et 20 heures.

![podinfo-vks : Synced to HEAD, Healthy, le Service, le Deployment, le ReplicaSet et trois pods](/images/argocd-tp/podinfo-synced.webp)

*`podinfo-vks` : Synced to HEAD, Healthy ; le Service, le Deployment, le ReplicaSet et trois pods.*

## La démo : Git est la seule porte d'entrée

La démo montre une idée de trois façons : ce qui est dans Git est ce qui tourne, et rien d'autre ne survit. podinfo est un bon sujet, parce que chaque changement se voit dans un navigateur.

![podinfo sur demo-vks-cluster avant la démo : la page bleue par défaut, servie par l'un des trois pods](/images/argocd-tp/podinfo-before.webp)

*podinfo sur `demo-vks-cluster` avant la démo : la page bleue par défaut, servie par l'un des trois pods.*

1. **Un commit modifie le site en ligne.** La [PR #5](https://github.com/sorinplaton/vcf-gitops/pull/5) ajoute deux variables d'environnement à `apps/podinfo/podinfo.yaml` : `PODINFO_UI_COLOR: "#2e7d32"` et `PODINFO_UI_MESSAGE: "Deployed by Argo CD on VCF 9.1.1"`. Faites-en le merge, appuyez sur **Refresh** dans Argo CD (ou attendez le polling périodique de Git, toutes les 3 à 5 minutes), et regardez l'application passer OutOfSync, se synchroniser d'elle-même et déployer trois nouveaux pods. Rechargez la page : elle est verte, avec le nouveau message.
2. **Self-heal.** Supprimez le Deployment podinfo sur le cluster VKS. Argo CD le marque Missing et le recrée en quelques secondes.
3. **Mise à l'échelle par Git.** Remplacez `replicas: 3` par `6` et faites un commit ; l'arbre des ressources gagne trois pods.
4. **La plateforme depuis Git.** L'Application `demo-vks-cluster` gère le cluster VKS lui-même à partir de `clusters/demo-vks-cluster/cluster.yaml`. Augmentez le nombre de workers, faites un commit, et VCF construit un nouveau worker node.

## Ce qui m'a fait trébucher

C'est une tech preview, et six choses m'ont coûté du temps. Aucune ne bloque l'installation une fois qu'on la connaît.

| Symptôme | Cause | Correctif |
| --- | --- | --- |
| la connexion à VCF Automation échouait et les tokens étaient rejetés | l'horloge de l'appliance VCF Automation retardait de 48 minutes ; chrony ne corrige l'horloge par saut que lors de ses trois premières mises à jour | `sudo chronyc makestep` et `sudo hwclock --systohc` sur l'appliance |
| `metadata.generateName: Required value` sur le namespace | VCF Automation n'accepte que `generateName` pour un SupervisorNamespace | le créer une fois avec `kubectl create`, puis retrouver son nom |
| le VPC, l'attachement et le load balancer sont apparus après un `--dry-run=server` | l'API CCI ignore le dry run côté serveur pour ces types et les crée | ne jamais se fier à un dry run contre VCF Automation |
| `permission denied: applications, create` après une connexion SSO réussie | l'instance démarre avec une politique RBAC Argo CD vide | définir `spec.rbac` sur l'objet `ArgoCD` |
| ManagedEntity bloqué en `Failed` : compte de service non lié | créé avant son RoleBinding ; le contrôleur ne réessaie pas | appliquer d'abord le RoleBinding, puis supprimer et réappliquer l'entité |
| anciens objets Application disparus | la réinstallation du service a recréé les CRD d'Argo CD | sauvegarder les Applications avant la bascule et les réappliquer depuis Git |

## En conclusion

Argo CD comme VCF Service fait du GitOps quelque chose qu'un tenant commande, auquel il se connecte avec le même compte et qu'il pointe vers ses propres clusters, sans mots de passe ni tokens qui circulent. La tech preview a encore des aspérités (une politique RBAC vide, un contrôleur qui ne réessaie pas), mais l'installation tient en une poignée de fichiers YAML stockés dans Git comme tout le reste.

Tout ce qui est montré ici se trouve dans [sorinplaton/vcf-gitops](https://github.com/sorinplaton/vcf-gitops), dossier `platform/argocd-vcfa/` ([PR #4](https://github.com/sorinplaton/vcf-gitops/pull/4)).
