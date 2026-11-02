---
title: "Grafana et Prometheus pour un labo VCF, construits par Argo CD sur le management domain"
date: 2026-11-02
description: "Un seul Grafana pour tout le labo Holodeck : les deux serveurs Dell par leurs iDRAC, trois vCenter, les hôtes ESXi imbriqués et le cluster VKS lui-même. Argo CD, le service en tech preview de VCF 9.1.1, construit le cluster et la pile de supervision depuis Git sur le management domain. Les étapes, le YAML, et les onze choses qui m'ont fait perdre du temps."
summary: "Argo CD comme VCF Service sur le management domain crée un cluster VKS depuis Git et y installe kube-prometheus-stack, un exporter vCenter et un exporter iDRAC. Un seul Grafana montre la consommation et la température CPU des Dell, le CPU ready des VM ESXi imbriquées, trois vCenter et le cluster. Ce qu'il a fallu, de la reconstruction du Supervisor au firewall des nodes VKS."
tags: ["VCF 9.1.1", "Argo CD", "GitOps", "Grafana", "Prometheus", "VKS", "Supervisor", "Tech preview", "Holodeck", "Dell iDRAC"]
categories: ["Notes"]
showToc: true
tocOpen: false
params:
  seoDescription: "Argo CD (tech preview VCF 9.1.1) construit un cluster VKS et Grafana/Prometheus depuis Git pour un labo VCF : Dell, trois vCenter, VKS. Étapes et pièges."
---

Mon labo Holodeck tourne sur deux serveurs Dell. Jusqu'ici, savoir si le labo allait bien voulait dire ouvrir quatre onglets : deux iDRAC, le vCenter extérieur et le vCenter imbriqué. Ce billet regroupe tout dans un seul Grafana : consommation et température CPU des Dell, CPU ready des VM ESXi imbriquées, les trois vCenter et le cluster Kubernetes qui héberge les tableaux de bord.

Rien n'est installé à la main. Le VCF Service Argo CD (la tech preview de VCF 9.1.1 décrite dans [le billet précédent](/fr/posts/argocd-vcf-service-tech-preview/)) tourne cette fois sur le **management domain**. Il crée le cluster VKS depuis Git, puis y installe Prometheus, Grafana et deux exporters depuis le même repo.

![Grafana, Lab: Dell power and thermal. Consommation, CPU le plus chaud, température d'entrée d'air et état des deux Dell, puis les séries temporelles de leurs iDRAC](/images/lab-monitoring/grafana-power-thermal.webp)

*Le tableau de bord que j'ouvre en premier : consommation, température CPU, entrée d'air et ventilateurs des deux Dell, lus sur leurs iDRAC toutes les minutes.*

## Ce qui est surveillé

| Source | Exporter | Ce qu'on voit |
| --- | --- | --- |
| iDRAC9 du R740, iDRAC8 du R730xd | [idrac_exporter](https://github.com/mrlhansen/idrac_exporter) 2.6.3 (Redfish) | consommation par alimentation, températures CPU, entrée et sortie d'air, vitesse des ventilateurs, état du système, avertissements du journal Lifecycle |
| vCenter extérieur (les Dell et les VM ESXi imbriquées) | [vmware_exporter](https://github.com/pryorda/vmware_exporter) 0.18.4 | CPU et mémoire des Dell, CPU ready et mémoire de chaque VM ESXi imbriquée, espace des datastores |
| vc-mgmt-a et vc-wld01-a (le VCF imbriqué) | le même exporter, deux sections de plus | CPU et mémoire des hôtes par domaine, datastores vSAN, inventaire des VM |
| le cluster VKS lui-même | kube-prometheus-stack 92.17.0 | nodes, pods, API server, kubelet, CoreDNS, Prometheus lui-même |

Tous les comptes utilisés par les exporters sont en lecture seule : un rôle vCenter limité à `System.Anonymous`, `System.View` et `System.Read`, et un utilisateur iDRAC avec le rôle `ReadOnly`.

## Comment les pièces s'assemblent

![Architecture du labo : Argo CD dans le namespace platform-ops tire depuis GitHub et crée le cluster VKS mon-mgmt ; Prometheus interroge les deux exporters, qui lisent les trois vCenter et les deux iDRAC avec des comptes en lecture seule](/images/lab-monitoring/diagram-architecture.svg)

Une nouvelle org VCF Automation, `platform-ops`, porte les outils du labo, ce qui garde les tenants applicatifs (`holo-east`, `tepless`) à part. Sa région `mgmt-a` est le Supervisor du management domain. L'org a un namespace, `platform-ops-mj3pd`, qui contient l'instance Argo CD. Argo CD y gère trois Applications :

1. **`mon-mgmt-cluster`** crée le cluster VKS `mon-mgmt` (un control plane, deux workers) depuis `clusters/mon-mgmt/cluster.yaml`.
2. **`monitoring-stack`** installe le chart Helm kube-prometheus-stack sur ce cluster : Prometheus avec 15 jours de données sur un volume vSAN de 30 Go, Grafana derrière un load balancer NSX, Alertmanager et node-exporter.
3. **`monitoring-exporters`** déploie `apps/monitoring` avec Kustomize : les deux exporters, leurs ServiceMonitors et les deux tableaux de bord du labo, sous forme de ConfigMaps que le sidecar de Grafana récupère.

![Argo CD, Applications : mon-mgmt-cluster, monitoring-exporters et monitoring-stack, toutes Healthy et Synced](/images/lab-monitoring/argocd-applications.webp)

*L'instance Argo CD du management domain : le cluster, le chart Helm et les exporters sont trois Applications.*

## Étape par étape

Le repo est [github.com/sorinplaton/vcf-gitops](https://github.com/sorinplaton/vcf-gitops/tree/mgmt-platform-ops), branche `mgmt-platform-ops`. Les étapes ci-dessous renvoient à ses dossiers.

### 1. Un Supervisor de management utilisable par VCF Automation

Les régions VCF Automation demandent un Supervisor en réseau NSX VPC, et le service Argo CD a besoin des vSphere Pods et d'un load balancer. Le Supervisor que Holodeck avait déployé sur le cluster de management utilisait le réseau VDS : je l'ai désactivé puis réactivé en NSX VPC (`platform/mgmt-supervisor/rebuild.sh`, une trentaine de minutes).

Il est ensuite resté des heures sur « No node accepting vSphere Pods ». vLCM refuse d'installer le spherelet sur les hôtes tant que le test de santé vSAN *Can vSAN cluster receive a host* n'est pas vert, et le vSAN de management imbriqué était jaune. Les causes : l'espace disque, un ancien format sur disque, et un contrôleur absent de la HCL parce qu'il est virtuel. Un disque de 450 Go de plus par hôte imbriqué, et la mise en sourdine des tests qui ne peuvent jamais passer dans un labo imbriqué, ont réglé le problème. `vsan-health.py`, dans le même dossier, montre quel test bloque.

### 2. Étapes fournisseur dans VCF Automation

Une fois par labo, en tant que fournisseur (`platform/platform-ops/provider.py` le fait par l'API) :

- la région `mgmt-a` à partir du nouveau Supervisor ;
- un réseau externe pour le tenant : VLAN 204 sur le routeur Holodeck, `10.204.204.0/24`. VCF Automation n'accepte pas le bloc externe du Day-0 ;
- l'org `platform-ops`, son administrateur, un quota de région et les paramètres réseau régionaux ;
- le VCF Service Argo CD activé pour la région `mgmt-a`.

### 3. Namespace, instance Argo CD, managed entities

En tant que `platform-admin` de la nouvelle org, `platform/platform-ops/apply.sh` applique le VPC, son attachement et son load balancer, le namespace, puis l'objet `ArgoCD` :

```yaml
apiVersion: argocd-service.vsphere.vmware.com/v1alpha1
kind: ArgoCD
metadata:
  name: argocd-platform
spec:
  version: 3.4.4+vmware.1-vks.1
  serverSideDiff: true
  enableLoadBalancer: true
  rbac:
    defaultPolicy: role:readonly
    policy: |
      g, <id utilisateur platform-admin>, role:admin
```

Quelques minutes plus tard, l'instance répondait sur `https://10.204.204.3`. Il reste deux étapes. D'abord, donner au compte de service de l'instance le rôle **ArgoCD Instance** dans l'org. Ensuite, enregistrer les cibles avec des objets `ManagedEntity` : le namespace lui-même, et le cluster VKS `mon-mgmt`, qui n'existe pas encore.

### 4. Le cluster, depuis Git

`argocd-apps-mgmt/mon-mgmt-cluster.yaml` pointe Argo CD vers `clusters/mon-mgmt`. Le cluster est un simple objet Cluster API : classe `builtin-generic-v3.7.0`, Kubernetes v1.35.6, Photon 5, control plane best-effort-small et deux workers best-effort-medium. Le seul réglage non standard est la règle de firewall des nodes pour node-exporter (voir les pièges) :

```yaml
    variables:
    - name: node
      value:
        firewall:
          inboundRules:
          - {protocol: tcp, fromPort: 9100, toPort: 9100, source: 172.24.0.0/23}
```

### 5. Prometheus et Grafana

`argocd-apps-mgmt/monitoring-stack.yaml` est une Application Argo CD qui contient le chart Helm et ses valeurs. Les valeurs qui comptent ici :

- Grafana reçoit un Service `LoadBalancer`, et NSX lui donne `10.204.204.6`.
- Le mot de passe admin vient d'un Secret existant (`grafana-admin`) et n'est jamais écrit dans Git.
- Grafana et Prometheus ont des PVC sur la politique de stockage vSAN du management.
- `serviceMonitorSelectorNilUsesHelmValues: false`, pour que Prometheus prenne les ServiceMonitors des exporters.
- Les scrapes etcd, scheduler, controller-manager et kube-proxy sont désactivés : les pods n'atteignent pas ces ports du control plane VKS, et les cibles resteraient en échec pour toujours.
- Trois tableaux de bord VMware communautaires (grafana.com 18017, 18018, 18019) sont téléchargés au démarrage.

### 6. Comptes en lecture seule et exporters

Les exporters ont besoin d'un compte sur chaque vCenter et chaque iDRAC. Ce sont des credentials du labo : ils vivent dans deux Secrets créés une fois avec `kubectl`, et le repo n'en contient que des exemples (`apps/monitoring/secrets.example/`). Sur chaque vCenter :

```sh
govc role.create "Monitoring Read-Only" System.Anonymous System.View System.Read
govc sso.user.create -p "$MONITOR_PASSWORD" monitor
govc permissions.set -principal monitor@vsphere.local -role "Monitoring Read-Only" -propagate=true /
```

Sur chaque iDRAC, un `PATCH` Redfish sur un emplacement de compte libre avec `{"UserName": "monitor", "RoleId": "ReadOnly", "Enabled": true}`. L'iDRAC8 range ses comptes à un autre chemin (voir les pièges). Ensuite, la troisième Application, `monitoring-exporters`, synchronise `apps/monitoring`.

![Argo CD, arbre de monitoring-exporters : deux ConfigMaps de tableaux de bord, les Services, Deployments, ReplicaSets et pods idrac et vmware, et deux ServiceMonitors](/images/lab-monitoring/argocd-exporters-tree.webp)

*`monitoring-exporters` dans Argo CD : la sortie Kustomize, synchronisée depuis la branche.*

## Les tableaux de bord

![Grafana, Lab: physical Dells and nested ESXi. VM allumées par vCenter, CPU et mémoire des Dell, CPU ready et mémoire des VM ESXi imbriquées, CPU et mémoire des hôtes par domaine imbriqué, espace libre des datastores](/images/lab-monitoring/grafana-nested-hosts.webp)

*Lab: physical Dells and nested ESXi. Le panneau qui justifie tout le projet est le **CPU ready** des VM ESXi imbriquées : il montre que les Dell sont surchargés bien avant que la latence vSAN ne le fasse.*

![Grafana, VMware Overview (grafana.com 18017) : nombre de VM, uptime, VM par datastore, CPU et mémoire des hôtes, usage et provisionnement des datastores, CPU, mémoire, latence disque et swap des VM](/images/lab-monitoring/grafana-vmware-overview.webp)

*Le VMware Overview communautaire sur les trois vCenter, une fois sa source de données reliée (voir plus bas). Les barres de provisionnement au-dessus de 100 % sont les disques thin des hôtes imbriqués.*

![Grafana, Node Exporter / Nodes pour les trois nodes VKS de mon-mgmt](/images/lab-monitoring/grafana-node-exporter.webp)

*Le cluster se surveille lui-même : node-exporter sur les trois nodes VKS.*

![Page des cibles Prometheus : toutes les cibles up, dont les sections mgmt, wld01 et outer de vmware-exporter, les deux iDRAC et node-exporter sur les trois nodes](/images/lab-monitoring/prometheus-targets.webp)

*Santé des cibles Prometheus : les deux iDRAC sont up. L'iDRAC8 du R730xd met environ 11 s par scrape, l'iDRAC9 du R740 environ 4 s.*

Ce que la première heure a montré :

- Le R730xd tourne au-dessus de 100 % de son CPU nominal.
- Le CPU 1 du R740 oscille entre 75 et 95 °C pendant que le serveur consomme de 400 à 530 W. Son journal Lifecycle iDRAC aligne les événements « CPU 1 temperature is greater than the upper critical threshold », maintenant visibles sur le tableau de bord.
- Le CPU ready des VM ESXi imbriquées culmine à 4 à 5 % par vCPU, un peu plus sur le R740 que sur le R730xd.

J'en connaissais l'essentiel, mais il y a maintenant un historique au lieu d'une impression, et cet historique a tranché une question matérielle. Le R730xd est le plus chargé, mais ses VM n'attendent presque pas le CPU : de nouveaux CPU n'y apporteraient pas grand-chose. C'est le R740 qui demande de l'attention, et pour le refroidissement, pas pour des cœurs.

## Ce qui m'a fait trébucher

Onze choses m'ont coûté du temps. La plupart viennent du labo imbriqué ou de la tech preview, et chacune se corrige en une ligne une fois qu'on la connaît.

| Symptôme | Cause | Correction |
| --- | --- | --- |
| Pas de région VCF Automation possible avec le Supervisor de management | il était construit en réseau VDS | reconstruire le Supervisor en NSX VPC |
| Supervisor bloqué sur « No node accepting vSphere Pods » | vLCM n'installe le spherelet que si la santé vSAN est verte ; le vSAN imbriqué était jaune | ajouter de la capacité, mettre en sourdine les tests qui ne peuvent pas passer en imbriqué (`vsan-health.py`) |
| Instance Argo CD Ready mais incapable de gérer quoi que ce soit | le compte de service n'a pas de rôle | lui donner **ArgoCD Instance** dans l'org. Par l'API : `PUT /cloudapi/1.0.0/serviceAccounts/<id>` avec `roles` ; `roleEntityRefs` est refusé |
| Cluster VKS bloqué, aucune VM de node : erreur NSX 610711 « Private ip blocks are not configured in parent VPC » | les subnets des nodes VKS viennent du bloc privé du VPC | ajouter `privateIPs: [172.24.0.0/23]` au VPC |
| « Could not resolve KR/OSImage » | la version d'OS de Photon est `5`, pas `5.0` | `os-name=photon,os-version=5` |
| ManagedEntity `mon-mgmt` bloquée en Failed | créée avant le cluster ; le contrôleur ne réessaie pas | la supprimer et la réappliquer une fois le cluster prêt |
| node-exporter up seulement sur le node où tourne Prometheus | le firewall des nodes VKS bloque tout ce qui n'est pas dans sa liste ; NSX n'y était pour rien (la règle par défaut du VPC autorise ce trafic) | `node.firewall.inboundRules` pour TCP 9100 depuis le subnet des nodes ; le changement remplace les nodes |
| `password policy is not satisfied` à la création de `monitor@vsphere.local` | la politique SSO par défaut de vCenter limite les mots de passe à 20 caractères | un mot de passe plus court |
| Redfish `Accounts/3 is invalid` sur le R730xd | l'iDRAC8 2.x range ses comptes sous `/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/` | essayer ce chemin quand `/AccountService/Accounts` échoue |
| idrac_exporter en `ImagePullBackOff`, puis `cannot unmarshal !!map into bool` | les images sont taguées `2.6.3` (sans `v`) ; la 2.x attend `metrics: {system: true}`, pas des maps | corriger le tag et la config ; l'iDRAC8 demande aussi un timeout de scrape de 90 s |
| Tableaux de bord VMware communautaires avec des triangles rouges et N/A | leur entrée de source de données s'appelle `DS_PROMETHEUS-VICTORIAMETRICS` | `datasource: [{name: DS_PROMETHEUS-VICTORIAMETRICS, value: prometheus}]` dans les valeurs Grafana |

Le firewall des nodes vaut la peine d'être retenu au-delà de la supervision. Les nodes VKS ont un firewall local (nftables, politique drop en entrée). Il autorise SSH, BGP, le kubelet, les agents VKS, les NodePorts et Geneve, et rien d'autre. Tout DaemonSet qui écoute sur un port de l'hôte demande une règle dans la spec du cluster, et le changement remplace les nodes.

## En résumé

Le labo se surveille maintenant lui-même depuis le management domain, et tout est dans Git : le cluster, les valeurs du chart, les exporters et les tableaux de bord. Le reconstruire, c'est trois Applications Argo CD et deux Secrets. Le VCF Service Argo CD a fait sa part sans difficulté une fois le Supervisor en dessous correct. L'essentiel du temps est parti dans le labo imbriqué : santé vSAN, firewall des nodes VKS et un iDRAC vieux de dix ans.

Tout est dans [sorinplaton/vcf-gitops](https://github.com/sorinplaton/vcf-gitops/tree/mgmt-platform-ops), branche `mgmt-platform-ops` : `platform/mgmt-supervisor/`, `platform/platform-ops/`, `clusters/mon-mgmt/`, `apps/monitoring/` et `argocd-apps-mgmt/`.
