---
title: "NSX Enhanced Datapath Performance : l'activer et le retirer un hôte à la fois"
date: 2026-11-14
description: "Comment faire passer un cluster NSX 4.2.4 du datapath standard à Enhanced Datapath – Performance, puis revenir en arrière, sans retirer NSX des hôtes. NSX refuse le saut direct : chaque hôte passe d'abord par EDP Standard. Chemin dans l'interface, chemin par l'API, l'erreur obtenue si on saute une étape, et ce que le changement a réellement coûté à un workload en cours d'exécution."
summary: "NSX 4.2.4 refuse de faire passer un hôte directement de Standard à Enhanced Datapath – Performance ; il demande de supprimer le host switch. On peut l'éviter en procédant hôte par hôte en mode maintenance, en passant par EDP Standard, puis on attache le nouveau profil au cluster. Même chemin au retour. Interface et API, avec l'impact mesuré."
tags: ["NSX", "Enhanced Datapath", "EDP", "ENS", "vSphere 8", "Transport Node Profile", "Holodeck"]
categories: ["Notes"]
showToc: true
tocOpen: false
images: ["/images/edp-perf/fig-01-mode-transitions.svg"]
params:
  seoDescription: "Activer puis retirer NSX Enhanced Datapath – Performance hôte par hôte, via EDP Standard, en mode maintenance. Interface, API, erreur 9544, impact mesuré."
---

Je voulais tester Enhanced Datapath – Performance (EDP Performance, l'ancien mode « ENS ») sur un cluster qui tourne déjà avec NSX en mode standard, puis le retirer. Sur le papier, c'est une liste déroulante dans le Transport Node Profile. En pratique, NSX 4.2.4 refuse le changement et vous demande de supprimer le host switch, ce qui, sur un cluster en production, revient à retirer l'overlay sous chaque VM.

Gabe Rosas décrit une manière beaucoup plus douce sur [vcfcore.tech](https://vcfcore.tech/2025/11/25/how-to-enable-edp-standard-with-zero-impact/) pour EDP Standard : on change un hôte à la fois pendant qu'il est en mode maintenance, et on n'attache le nouveau profil au cluster qu'à la fin. La même méthode permet d'aller jusqu'à Performance et d'en revenir, à condition d'ajouter une étape. Ce billet décrit cette procédure, faite deux fois (une fois dans l'interface, une fois par l'API), avec l'erreur que l'on obtient en sautant l'étape et les pertes de ping mesurées en route.

*Novembre 2026 · NSX 4.2.4.1 · vCenter 8.0 U3 · ESX 8.0 U3i imbriqué · noms et adresses de mon lab*

## En bref

- NSX connaît trois modes de host switch : `STANDARD`, `ENS_INTERRUPT` (EDP Standard) et `ENS` (EDP Performance).
- `STANDARD` ⇄ `ENS` en une seule étape est refusé avec l'erreur 9544, au niveau du cluster comme hôte par hôte.
- `STANDARD` → `ENS_INTERRUPT` → `ENS` est accepté, un hôte à la fois, environ 10 secondes par étape. Le retrait suit le chemin inverse.
- Avec l'hôte en mode maintenance, les workloads ne voient pas le changement de datapath. Les seules pertes que j'ai mesurées venaient des vMotion qui vidaient chaque hôte : 0,2 à 0,6 seconde par migration.
- Une fois tous les hôtes convertis, on attache le Transport Node Profile correspondant au cluster. NSX n'a plus rien à changer et passe directement à `SUCCESS`.

![Les trois modes de host switch NSX. STANDARD vers ENS_INTERRUPT et retour : accepté ; ENS_INTERRUPT vers ENS et retour : accepté ; STANDARD vers ENS en direct, dans un sens comme dans l'autre : refusé avec l'erreur 9544.](/images/edp-perf/fig-01-mode-transitions.svg)

## Le lab

Trois hôtes ESX 8.0 U3i imbriqués sur un Dell R730xd, un vCenter dédié, un NSX Manager, et deux petites VM Ubuntu sur un segment overlay qui se pinguent cinq fois par seconde pendant tout le test. Un lab imbriqué ne dit rien du débit d'EDP. Je voulais savoir ce que coûte chaque étape du changement, et il le montre précisément.

![Schéma du lab : vCenter vc-edp, NSX Manager nsx-edp et trois hôtes imbriqués esx-edp-01 à 03, chacun avec edp-vds sur vmnic1 et vmnic2 pour NSX, un VDS vMotion séparé sur vmnic3 et la gestion sur vmnic0.](/images/edp-perf/fig-03-lab.svg)

Le réseau vMotion est volontairement sur son propre VDS. EDP change tout le host switch, et je ne voulais pas que vMotion vive sur le switch que j'étais en train de modifier.

## Trois modes, trois noms

L'interface, l'API et le shell ESX donnent chacun un nom différent à la même chose, ce qui n'aide pas.

| Interface (Advanced Configuration › Mode) | API `host_switch_mode` | Ce qui change sur l'hôte |
|---|---|---|
| Standard | `STANDARD` | Datapath normal, driver du constructeur (`nvmxnet3` ici) |
| Enhanced Datapath – Standard | `ENS_INTERRUPT` | Driver ENS (`nvmxnet3_ens`), piloté par interruptions, pas de cœurs dédiés |
| Enhanced Datapath – Performance | `ENS` | Driver ENS, polling sur des cœurs logiques dédiés ; `cpu_config` est obligatoire |

`cpu_config` est une liste de paires `{numa_node_index, num_lcores}`. Ces cœurs sont retirés aux VM et tournent en boucle pour le datapath : sur du vrai matériel, dimensionnez-les avec soin. Mes hôtes imbriqués ont un seul nœud NUMA et j'ai donné deux cœurs à EDP.

## Ce qui ne marche pas

Mon premier essai était le plus évident : modifier le Transport Node Profile du cluster, mettre le mode à ENS, enregistrer.

```
ERROR 9544 Cannot update HostSwitch 50 02 2a 0a ... Mode STANDARD to new Mode ENS.
Please delete the HostSwitch and create it back with intended Mode.
```

Pointer le cluster vers un second profil déjà réglé sur `ENS` donne la même réponse, cette fois hôte par hôte dans l'état de la collection (`FAILED_TO_REALIZE`, « 3 hosts in the cluster are in error state »). Un appel API par hôte qui va directement à `ENS` aussi.

![État de la transport node collection après avoir basculé le cluster sur un profil ENS : FAILED_TO_REALIZE, erreur 9544 sur chaque hôte.](/images/edp-perf/cli-01-error-9544.webp)

NSX dit ce qu'il accepterait : supprimer le host switch et le recréer. Il y a trois façons d'y arriver.

| Méthode | Ce que voient les VM | Quand l'utiliser |
|---|---|---|
| Retirer NSX du cluster, le préparer à nouveau avec le nouveau profil | Toutes les VM de tous les hôtes du cluster perdent leurs segments overlay jusqu'à la nouvelle préparation (près de 8 minutes pour trois hôtes dans mon lab : environ 1,5 minute pour désinstaller les VIB, 6 pour préparer de nouveau les hôtes) | Cluster vide, nouveau déploiement, jamais en production |
| Changer le mode dans le profil du cluster (quand NSX l'autorise, par exemple Standard → EDP Standard) | Le trafic vSwitch de tous les hôtes est perturbé en même temps ; selon vcfcore.tech, de quelques secondes à beaucoup plus, et les connexions peuvent ne pas se rétablir d'elles-mêmes | Lab uniquement |
| Un hôte à la fois, en mode maintenance, en passant par EDP Standard | Rien pendant le changement de mode lui-même. Seulement les vMotion qui vident chaque hôte, comme un jour de patch | Production |

J'ai essayé la première avant de lire le billet de Gabe. Elle fonctionne, mais sur un cluster avec des workloads, on n'en veut jamais. La suite de ce billet décrit la troisième.

## Avant de commencer

1. Les cartes réseau doivent être compatibles ENS. Sur chaque hôte, `esxcfg-nics -e` doit afficher `ENS Capable = True` pour les uplinks du switch NSX. C'est le cas de vmxnet3, d'où le fait que cela marche en imbriqué ; sur du matériel, vérifiez le driver et le firmware exacts dans le Broadcom Compatibility Guide.
2. vCenter doit autoriser vMotion entre hôtes dans des modes différents, puisque pendant le déploiement certains hôtes sont dans un mode et d'autres dans l'autre. Réglez le paramètre avancé de vCenter `config.vpxd.network.allowVmotionBetweenENSModes = true` (vCenter › Configure › Advanced Settings, ou `govc option.set config.vpxd.network.allowVmotionBetweenENSModes true`).
3. Clonez le Transport Node Profile : même host switch, même uplink profile, même pool d'IP, seul le mode change, plus `cpu_config` pour Performance. Ne l'attachez pas encore. Les miens s'appellent `edp-tnp` (Standard) et `edp-tnp-ens` (Performance).
4. Il faut de la place pour vider un hôte à la fois, avec DRS en mode entièrement automatisé ou par vMotion manuel. L'hôte doit être vide avant d'y toucher.

![Déroulé par hôte : préparation unique, puis pour chaque hôte évacuation, mode maintenance, étape 1 vers ENS_INTERRUPT, étape 2 vers ENS (ou STANDARD), vérification et sortie du mode maintenance ; une fois tous les hôtes faits, on attache le TNP correspondant au cluster.](/images/edp-perf/fig-02-per-host-workflow.svg)

## Activer EDP Performance dans l'interface

J'ai fait esx-edp-01 entièrement dans l'interface NSX. L'hôte était déjà en mode maintenance, sa VM déplacée ailleurs.

**1. Ouvrir l'hôte, pas le cluster.** System › Fabric › Hosts, déplier le cluster, cliquer sur les trois points à côté de l'hôte, **Configure NSX**.

![Vue Hosts de NSX avec edp-cluster déplié et le menu de esx-edp-01 ouvert sur Configure NSX.](/images/edp-perf/gui-01-host-menu-configure-nsx.webp)

**2. Modifier le host switch.** Next pour passer *Host details*. Sur *Prepare Host*, les trois points de la ligne `edp-vds` › **Edit**, puis déplier **Advanced Configuration**. Le mode est en bas.

![Étape Prepare Host : le host switch edp-vds en édition, Advanced Configuration déplié et Mode sur Standard.](/images/edp-perf/gui-02-edit-host-switch-standard.webp)

**3. Première étape.** Ouvrir la liste Mode. Depuis Standard, l'interface ne propose que Standard et Enhanced Datapath – Standard. Performance est absent de la liste : c'est la règle des deux étapes, telle que l'interface la montre. Choisir **Enhanced Datapath – Standard**, **Add**, **Finish**. Une dizaine de secondes plus tard, l'hôte est réalisé en `ENS_INTERRUPT`.

![Liste Mode quand l'hôte est en Standard : seuls Standard et Enhanced Datapath – Standard sont proposés.](/images/edp-perf/gui-03-modes-from-standard.webp)

**4. Deuxième étape.** De nouveau Configure NSX sur le même hôte, Edit, Advanced Configuration. La liste propose maintenant les trois modes. Choisir **Enhanced Datapath – Performance** : un champ **CPU Config** obligatoire apparaît à côté.

![Liste Mode quand l'hôte est en Enhanced Datapath – Standard : Standard, Enhanced Datapath – Standard et Enhanced Datapath – Performance sont proposés.](/images/edp-perf/gui-04-modes-from-edp-standard.webp)

![Mode réglé sur Enhanced Datapath – Performance avec le champ CPU Config obligatoire et son lien Set.](/images/edp-perf/gui-05-performance-cpu-config.webp)

**5. Lui donner des cœurs.** **Set** › **Add** : NUMA node index `0`, lcores per NUMA node `2`. Add, Save, puis Add et Finish dans l'assistant.

![Boîte CPU Config avec une ligne : NUMA Node Index 0, LCores per NUMA Node 2.](/images/edp-perf/gui-06-cpu-config-dialog.webp)

**6. Vérifier sur l'hôte.** `nsxdp-cli ens switch list` affiche maintenant un switch ENS avec deux lcores, et `esxcfg-nics -e` montre les uplinks NSX sur le driver `_ens` avec `ENS Driven = True`. vmnic0 (gestion) et vmnic3 (vMotion, VDS séparé) ne bougent pas. Sortir du mode maintenance et passer à l'hôte suivant.

![nsxdp-cli ens switch list montre DvsPortset-0 avec un MTU de 9000 et 2 lcores ; esxcfg-nics -e montre vmnic1 et vmnic2 sur nvmxnet3_ens avec ENS Driven True.](/images/edp-perf/cli-02-host-ens-verified.webp)

Pendant qu'on avance hôte par hôte, le cluster pointe toujours vers l'ancien profil et NSX marque les hôtes convertis en *Mismatch*. C'est attendu, et cela disparaît à la dernière étape.

![Vue Hosts de NSX pendant le déploiement : un hôte affiche Mismatch dans la colonne NSX Configuration.](/images/edp-perf/gui-09-mismatch-during-rollout.webp)

**7. Attacher le profil au cluster.** Quand tous les hôtes sont en Performance : sélectionner le cluster, **Configure NSX**, choisir le profil Performance (`edp-tnp-ens`), Save. Les hôtes y correspondent déjà, il n'y a donc rien à pousser et le cluster passe à *Prepared* avec le nouveau profil.

![edp-cluster avec Applied Profile edp-tnp-ens, trois hôtes up, NSX Configuration Success partout.](/images/edp-perf/gui-07-cluster-edp-tnp-ens.webp)

## Activer EDP Performance par l'API

Les hôtes 2 et 3 sont passés par l'API. L'assistant de l'interface écrit le même objet, le *host transport node* de l'API policy. Le chemin API consiste donc à le lire, changer le mode, le réécrire, attendre, et recommencer une seconde fois.

Trouver le transport node de l'hôte :

```bash
NSX=nsx-edp.corp.local
EP=/policy/api/v1/infra/sites/default/enforcement-points/default
curl -sk -u admin "https://$NSX$EP/host-transport-nodes" \
  | jq -r '.results[] | [.id, .display_name, .host_switch_spec.host_switches[0].host_switch_mode] | @tsv'
```

Première étape, `ENS_INTERRUPT`. On garde l'objet entier, on ne change que le mode, et on le renvoie par PUT (le `_revision` lu doit repartir avec) :

```bash
TN=esx-edp-02-808e47a5-f31f-4824-a52f-83ac9e597257host-16
curl -sk -u admin "https://$NSX$EP/host-transport-nodes/$TN" > tn.json
jq '.host_switch_spec.host_switches[].host_switch_mode = "ENS_INTERRUPT"
  | .host_switch_spec.host_switches[].cpu_config = []' tn.json > tn-hop1.json
curl -sk -u admin -X PUT -H 'Content-Type: application/json' \
  "https://$NSX$EP/host-transport-nodes/$TN" -d @tn-hop1.json
```

Attendre que le node soit réalisé :

```bash
curl -sk -u admin "https://$NSX$EP/host-transport-nodes/$TN/state" \
  | jq -r '.state, .node_deployment_state.state'      # success / success
```

Deuxième étape, `ENS` avec ses cœurs. Relire le node d'abord : la révision a changé.

```bash
curl -sk -u admin "https://$NSX$EP/host-transport-nodes/$TN" \
  | jq '.host_switch_spec.host_switches[].host_switch_mode = "ENS"
      | .host_switch_spec.host_switches[].cpu_config = [{"numa_node_index":0,"num_lcores":2}]' > tn-hop2.json
curl -sk -u admin -X PUT -H 'Content-Type: application/json' \
  "https://$NSX$EP/host-transport-nodes/$TN" -d @tn-hop2.json
```

Le mode maintenance relève de vCenter, pas de NSX : `govc host.maintenance.enter esx-edp-02.corp.local` avant la première étape, `govc host.maintenance.exit` après la seconde.

Une fois tous les hôtes faits, on attache le profil au cluster en mettant à jour la transport node collection :

```bash
curl -sk -u admin "https://$NSX$EP/transport-node-collections/edp-cluster-tnc" \
  | jq '.transport_node_profile_id = "/infra/host-transport-node-profiles/edp-tnp-ens"' > tnc.json
curl -sk -u admin -X PUT -H 'Content-Type: application/json' \
  "https://$NSX$EP/transport-node-collections/edp-cluster-tnc" -d @tnc.json
curl -sk -u admin "https://$NSX$EP/transport-node-collections/edp-cluster-tnc/state" | jq -r .state   # SUCCESS
```

J'ai regroupé toute la boucle (évacuation, mode maintenance, les deux étapes, attente, sortie, hôte suivant, puis attache du profil) dans un petit script Python qui utilise pyVmomi pour vCenter et l'API policy de NSX : [edp_per_host.py](/files/edp-perf/edp_per_host.py). Il calcule lui-même les étapes et ignore les hôtes déjà dans le mode visé, ce qui lui a permis de reprendre après l'hôte que j'avais fait à la main :

![Sortie du script : esx-edp-01 déjà en ENS, ignoré ; esx-edp-02 et 03 ont chacun un vMotion, le mode maintenance, ENS_INTERRUPT réalisé en 10 s, ENS réalisé en 10 s, la sortie du mode maintenance ; puis le TNP edp-tnp-ens est attaché et l'état du cluster est SUCCESS.](/images/edp-perf/cli-03-api-install.webp)

Les deux hôtes, vMotion compris, ont pris une minute et demie.

## Le retrait : retour à Standard

Même route dans l'autre sens : `ENS` → `ENS_INTERRUPT` → `STANDARD`, hôte par hôte, en mode maintenance, puis on attache le profil Standard. Passer directement de `ENS` à `STANDARD` donne la même erreur 9544.

**Dans l'interface** (esx-edp-02) : Configure NSX sur l'hôte, Edit sur le host switch, Advanced Configuration. Depuis Performance, la liste ne propose qu'EDP Standard et Performance, pas Standard.

![Liste Mode quand l'hôte est en Performance : seuls Enhanced Datapath – Standard et Enhanced Datapath – Performance sont proposés.](/images/edp-perf/gui-08-modes-from-performance.webp)

Choisir **Enhanced Datapath – Standard**, Finish. Relancer Configure NSX et choisir **Standard**, Finish. Le switch ENS a disparu et les uplinks sont revenus sur le driver habituel :

![Après le retrait : nsxdp-cli indique No ENS switch configured et esxcfg-nics -e montre chaque vmnic sur nvmxnet3 avec ENS Driven False.](/images/edp-perf/cli-04-host-standard-verified.webp)

**Par l'API** : les deux mêmes PUT avec `ENS_INTERRUPT` puis `STANDARD`, `cpu_config` remis à `[]`. Ou le script avec `--mode STANDARD` :

![Sortie du script pour le retrait : esx-edp-01 et esx-edp-03 sont chacun évacués, mis en mode maintenance, passent de ENS à ENS_INTERRUPT puis à STANDARD en 10 s environ par étape, et sortent du mode maintenance ; esx-edp-02 est ignoré car déjà en Standard.](/images/edp-perf/cli-05-api-remove.webp)

**Dernière étape, au niveau du cluster** : sélectionner le cluster, Configure NSX, choisir le profil Standard, Save.

![Boîte NSX Installation sur le cluster avec le Transport Node Profile edp-tnp sélectionné.](/images/edp-perf/gui-10-cluster-apply-edp-tnp.webp)

![edp-cluster revenu sur Applied Profile edp-tnp, NSX Configuration Success sur chaque hôte.](/images/edp-perf/gui-11-cluster-back-to-standard.webp)

Un de mes hôtes est apparu *Degraded* juste après le retrait : NSX voyait `uplink2` down alors qu'ESX voyait les deux liens up. C'était un état pNIC en cache, resté du changement de driver : l'état en temps réel (`GET /api/v1/transport-nodes/<id>/status?source=realtime`) répondait `UP`, et l'interface a suivi peu après. Vérifiez-le avant d'ouvrir un ticket.

## Ce que cela a coûté

`edp-vm1` a pingué `edp-vm2` à travers l'overlay toutes les 200 ms pendant tout le test, les VM suivant les évacuations d'un hôte à l'autre.

![Résumé des pings : installation, 2880 sondes, 4 perdues (0,14 %), trois courtes coupures ; retrait, 2305 sondes, 8 perdues (0,35 %), quatre courtes coupures.](/images/edp-perf/cli-06-ping-impact.webp)

| | Sondes | Perdues | Plus longue coupure |
|---|---|---|---|
| Installation (Standard → Performance, 3 hôtes) | 2 880 | 4 (0,14 %) | 0,4 s |
| Retrait (Performance → Standard, 3 hôtes) | 2 305 | 8 (0,35 %) | 0,6 s |

Chaque coupure correspond, à la seconde près, à la bascule d'un vMotion qui vidait un hôte. Aucune ne correspond à un changement de mode, parce qu'au moment où le mode changeait il ne restait plus rien sur l'hôte à perturber. Dans mon lab, ces vMotion sont des storage vMotion entre datastores locaux d'hôtes imbriqués ; sur du vrai matériel avec du stockage partagé, attendez-vous à des coupures plus courtes.

## Avant de l'essayer sur de vrais hôtes

Il n'y a pas de passage direct `STANDARD` ⇄ `ENS` dans NSX 4.2.4. Le profil le refuse, l'API par hôte le refuse, et l'interface ne le propose même pas : prévoyez deux réalisations par hôte. Chaque étape prend une dizaine de secondes, et en mode maintenance ces dix secondes sont sans conséquence, puisque aucune VM ne doit être sur l'hôte pendant que son datapath change.

Clonez le Transport Node Profile au lieu de modifier celui qui est attaché au cluster, car modifier le profil attaché, c'est le chemin « tous les hôtes à la fois ». N'attachez le clone qu'une fois que tous les hôtes y correspondent. D'ici là, *Mismatch* sur les hôtes convertis est normal, tout comme un *Degraded* périmé juste après le changement de driver.

Les lcores du mode Performance sont de vrais cœurs : `cpu_config` retire des cœurs logiques aux VM sur chaque nœud NUMA listé : dimensionnez-le selon le trafic que vous attendez vraiment. Un lab imbriqué convient pour répéter la procédure et ne sert à rien pour des chiffres de performance : pour savoir ce qu'EDP Performance vous apporte, mesurez-le sur le matériel et les drivers sur lesquels il tournera.

La méthode par hôte en mode maintenance vient du billet de Gabe Rosas [How to Enable EDP Standard with Zero Impact in VCF 5.2](https://vcfcore.tech/2025/11/25/how-to-enable-edp-standard-with-zero-impact/) et de sa [suite sur enable_uens.py](https://vcfcore.tech/2026/02/25/using-enable_uens-py-for-edp-transitions-in-vcf-5-2/). Je n'ai ajouté que la seconde étape vers Performance et le chemin du retour.
