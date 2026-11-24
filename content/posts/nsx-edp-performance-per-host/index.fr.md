---
title: "NSX Enhanced Datapath : ce que c'est, comment l'activer, comment changer de mode"
date: 2026-11-14
description: "Ce que font EDP Standard et EDP Performance sous NSX 4.2.4, comment activer EDP Performance un hôte à la fois en passant par EDP Standard sur un cluster qui tourne déjà avec NSX, et comment changer de mode ou revenir à Standard, dans l'interface et par l'API."
summary: "NSX Enhanced Datapath (EDP) ajoute un cache de flux au host switch ESX et existe en deux modes, EDP Standard et EDP Performance. Le billet active EDP Performance sur un cluster qui tourne déjà avec NSX 4.2.4, un hôte à la fois en mode maintenance, dans l'interface, par l'API ou avec un script. NSX refuse le passage direct de Standard à Performance : tout changement de mode passe donc par EDP Standard, retour compris."
tags: ["NSX", "Enhanced Datapath", "EDP", "ENS", "vSphere 8", "Transport Node Profile", "Holodeck"]
categories: ["Notes"]
showToc: true
tocOpen: false
images: ["/images/edp-perf/fig-01-mode-transitions.svg"]
params:
  seoDescription: "NSX Enhanced Datapath expliqué : les deux modes EDP, l'activation d'EDP Performance hôte par hôte et le changement de mode via EDP Standard. Interface et API."
---

NSX Enhanced Datapath (EDP) est un chemin de paquets plus rapide dans le host switch ESX. Il existe en deux modes, EDP Standard et EDP Performance (l'ancien mode « ENS »). Ce guide explique ce que fait EDP, puis active EDP Performance sur un cluster qui tourne déjà avec NSX en mode Standard, dans l'interface, par l'API et avec un script. Une partie suivante traite du passage d'un mode à l'autre, retour à Standard compris, et quelques points à garder en tête terminent le billet.

*Novembre 2026 · NSX 4.2.4.1 · vCenter 8.0 U3 · ESX 8.0 U3i · les noms des exemples viennent du lab*

## Qu'est-ce qu'Enhanced Datapath

EDP reprend des idées de DPDK. Le datapath standard analyse et classe chaque paquet à travers tout le pipeline de commutation et de firewall. EDP ajoute un cache de flux : le premier paquet d'un flux suit le chemin complet, et les suivants réutilisent le résultat. Il tient aussi compte de NUMA et cherche à traiter les paquets sur le nœud NUMA de la carte réseau. Les deux modes EDP exigent des cartes réseau et des drivers certifiés.

EDP Standard fonctionne par interruptions et ne réserve aucun cœur. Il convient donc aux clusters généralistes, où il consomme moins de CPU pour le même trafic. EDP Performance dédie des cœurs logiques qui interrogent les cartes réseau en permanence. La latence baisse, et ce mode vise les fonctions réseau télécom (NFV) et les autres workloads qui traitent beaucoup de paquets. Le prix, ce sont ces cœurs, que les VM ne peuvent pas utiliser même sans trafic. Le lab est en nested : rien de tout cela n'est mesuré ici, le billet montre seulement comment régler le mode et en changer sans risque.

### Trois modes de host switch, trois noms

L'interface et l'API donnent des noms différents au même mode. La dernière colonne montre ce qui change sur l'hôte.

| Interface (Advanced Configuration › Mode) | API `host_switch_mode` | Ce qui change sur l'hôte |
|---|---|---|
| Standard | `STANDARD` | Datapath normal, driver du constructeur (`nvmxnet3` ici) |
| Enhanced Datapath – Standard | `ENS_INTERRUPT` | Driver ENS (`nvmxnet3_ens`), piloté par interruptions, pas de cœurs dédiés |
| Enhanced Datapath – Performance | `ENS` | Driver ENS, polling sur des cœurs logiques dédiés ; `cpu_config` est obligatoire |

`cpu_config` est une liste de paires `{numa_node_index, num_lcores}`. Ces cœurs sont retirés aux VM et tournent en boucle pour le datapath, donc sur du vrai matériel, dimensionnez la liste avec soin.

La documentation récente de Broadcom, dont le KB 402229, appelle EDP Performance « EDP Dedicated ». Le mode reste le même, toujours `ENS` dans l'API. L'interface de NSX 4.2.4 de ce lab affiche « Enhanced Datapath – Performance », et le billet garde ce nom.

## Activer EDP

Activer EDP Performance, c'est y amener tous les hôtes sans retirer NSX du cluster. On procède hôte par hôte, chacun en mode maintenance, et chaque hôte passe par EDP Standard avant d'arriver à Performance. NSX refuse le passage direct ; la partie sur les changements de mode, plus bas, explique pourquoi.

### Avant de commencer

1. Les cartes réseau doivent être compatibles ENS. Sur chaque hôte, `esxcfg-nics -e` doit afficher `ENS Capable = True` pour les uplinks du switch NSX. Vérifiez le driver et le firmware exacts dans le Broadcom Compatibility Guide.
2. vCenter doit autoriser vMotion entre hôtes dans des modes différents, puisque pendant le déploiement certains hôtes sont dans un mode et d'autres dans l'autre. Réglez le paramètre avancé de vCenter `config.vpxd.network.allowVmotionBetweenENSModes = true` (vCenter › Configure › Advanced Settings, ou `govc option.set config.vpxd.network.allowVmotionBetweenENSModes true`).
3. Clonez le Transport Node Profile : même host switch, même uplink profile, même pool d'IP, seul le mode change, plus `cpu_config` pour Performance. Ne l'attachez pas encore. Dans les exemples, ce sont `edp-tnp` (Standard) et `edp-tnp-ens` (Performance).
4. Il faut de la place pour vider un hôte à la fois, avec DRS en mode entièrement automatisé ou par vMotion manuel. L'hôte doit être vide avant d'y toucher.

### Le déroulé en un coup d'œil

L'activation et le retrait d'EDP Performance suivent les trois mêmes étapes. Seuls changent le sens des passages d'un mode à l'autre et le profil qu'on attache à la fin.

1. **Préparer une fois.** Cloner le Transport Node Profile du cluster et régler le mode cible sur la copie (`edp-tnp-ens`, EDP Performance avec 2 lcores sur le NUMA 0). Dans vCenter, autoriser le vMotion entre hôtes de modes différents. Pour le retour, rien à préparer : le profil Standard `edp-tnp` existe toujours.
2. **Un hôte à la fois.** Migrer les VM hors de l'hôte par vMotion et le passer en mode maintenance. Changer son host switch en deux étapes, `STANDARD → ENS_INTERRUPT → ENS` (ou l'inverse), environ 10 secondes chacune. Vérifier l'hôte, sortir du mode maintenance, passer au suivant.
3. **Terminer au niveau du cluster.** Attacher le profil correspondant au cluster. Tous les hôtes y sont déjà conformes, donc NSX passe directement à `SUCCESS`.

![Déroulé complet. Activation : tous les hôtes en STANDARD sur edp-tnp, préparation unique d'edp-tnp-ens, puis pour chaque hôte évacuation, mode maintenance, passage STANDARD vers ENS_INTERRUPT vers ENS, sortie du mode maintenance ; on termine en attachant edp-tnp-ens au cluster. Retrait : même boucle avec ENS vers ENS_INTERRUPT vers STANDARD, puis on attache edp-tnp.](/images/edp-perf/fig-04-quick-flow.svg)

### Dans l'interface

Commencez avec l'hôte vide et en mode maintenance.

**1. Ouvrir l'hôte, pas le cluster.** System › Fabric › Hosts, déplier le cluster, cliquer sur les trois points à côté de l'hôte, **Configure NSX**.

![Vue Hosts de NSX avec edp-cluster déplié et le menu de esx-edp-01 ouvert sur Configure NSX.](/images/edp-perf/gui-01-host-menu-configure-nsx.webp)

**2. Modifier le host switch.** Next pour passer *Host details*. Sur *Prepare Host*, les trois points de la ligne `edp-vds` › **Edit**, puis déplier **Advanced Configuration**. Le mode est en bas.

![Étape Prepare Host : le host switch edp-vds en édition, Advanced Configuration déplié et Mode sur Standard.](/images/edp-perf/gui-02-edit-host-switch-standard.webp)

**3. Premier passage.** Ouvrir la liste Mode. Depuis Standard, l'interface ne propose que Standard et Enhanced Datapath – Standard. Performance n'apparaît pas dans la liste : c'est ainsi que l'interface impose la règle des deux étapes, expliquée plus bas dans *Pourquoi le passage direct échoue*. Choisir **Enhanced Datapath – Standard**, **Add**, **Finish**. Une dizaine de secondes plus tard, l'hôte est réalisé en `ENS_INTERRUPT`.

![Liste Mode quand l'hôte est en Standard : seuls Standard et Enhanced Datapath – Standard sont proposés.](/images/edp-perf/gui-03-modes-from-standard.webp)

**4. Second passage.** De nouveau Configure NSX sur le même hôte, Edit, Advanced Configuration. La liste propose maintenant les trois modes. Choisir **Enhanced Datapath – Performance** : un champ **CPU Config** obligatoire apparaît à côté.

![Liste Mode quand l'hôte est en Enhanced Datapath – Standard : Standard, Enhanced Datapath – Standard et Enhanced Datapath – Performance sont proposés.](/images/edp-perf/gui-04-modes-from-edp-standard.webp)

![Mode réglé sur Enhanced Datapath – Performance avec le champ CPU Config obligatoire et son lien Set.](/images/edp-perf/gui-05-performance-cpu-config.webp)

**5. Lui donner des cœurs.** **Set** › **Add** : NUMA node index `0`, lcores per NUMA node `2`. Add, Save, puis Add et Finish dans l'assistant.

![Boîte CPU Config avec une ligne : NUMA Node Index 0, LCores per NUMA Node 2.](/images/edp-perf/gui-06-cpu-config-dialog.webp)

**6. Vérifier sur l'hôte.** `nsxdp-cli ens switch list` affiche maintenant un switch ENS avec deux lcores, et `esxcfg-nics -e` montre les uplinks NSX sur le driver `_ens` avec `ENS Driven = True`. vmnic0 (gestion) et vmnic3 (vMotion, VDS séparé) ne bougent pas. Sortir du mode maintenance et passer à l'hôte suivant.

![nsxdp-cli ens switch list montre DvsPortset-0 avec un MTU de 9000 et 2 lcores ; esxcfg-nics -e montre vmnic1 et vmnic2 sur nvmxnet3_ens avec ENS Driven True.](/images/edp-perf/cli-02-host-ens-verified.webp)

Pendant qu'on avance hôte par hôte, le cluster pointe toujours vers l'ancien profil et NSX marque les hôtes convertis en *Mismatch*. C'est attendu, et l'état se corrige à la dernière étape.

![Vue Hosts de NSX pendant le déploiement : un hôte affiche Mismatch dans la colonne NSX Configuration.](/images/edp-perf/gui-09-mismatch-during-rollout.webp)

**7. Attacher le profil au cluster.** Quand tous les hôtes sont en Performance : sélectionner le cluster, **Configure NSX**, choisir le profil Performance (`edp-tnp-ens`), Save. Les hôtes y correspondent déjà, il n'y a donc rien à pousser et le cluster passe à *Prepared* avec le nouveau profil.

![edp-cluster avec Applied Profile edp-tnp-ens, trois hôtes up, NSX Configuration Success partout.](/images/edp-perf/gui-07-cluster-edp-tnp-ens.webp)

### Par l'API

L'assistant de l'interface écrit l'objet *host transport node* de l'API policy, et le chemin API travaille sur ce même objet : le lire, changer le mode, le réécrire, attendre, puis recommencer pour la seconde étape.

On prépare la session une fois. Le bloc demande le NSX Manager et ses identifiants, et ne les garde que dans des variables du shell :

```bash
read -rp  "NSX Manager FQDN or IP: " NSX
read -rp  "NSX user [admin]: " NSX_USER; NSX_USER=${NSX_USER:-admin}
read -rsp "NSX password: " NSX_PASS; echo
EP=/policy/api/v1/infra/sites/default/enforcement-points/default
nsx() { curl -sk -u "$NSX_USER:$NSX_PASS" -H 'Content-Type: application/json' "$@"; }
nsx "https://$NSX$EP/host-transport-nodes" \
  | jq -r '.results[] | [.display_name, .host_switch_spec.host_switches[0].host_switch_mode] | @tsv'
```

On choisit l'hôte et on récupère son transport node :

```bash
read -rp "Host to change (name as listed above): " HOST
TN=$(nsx "https://$NSX$EP/host-transport-nodes" | jq -r --arg h "$HOST" '.results[] | select(.display_name==$h) | .id')
echo "transport node: $TN"
```

Le mode maintenance relève de vCenter. `govc` ne pose aucune question de lui-même, donc ses variables sont aussi saisies par des questions :

```bash
read -rp  "vCenter FQDN or IP: " VC
read -rp  "vCenter user [administrator@vsphere.local]: " VC_USER; VC_USER=${VC_USER:-administrator@vsphere.local}
read -rsp "vCenter password: " VC_PASS; echo
export GOVC_URL="https://$VC" GOVC_USERNAME="$VC_USER" GOVC_PASSWORD="$VC_PASS" GOVC_INSECURE=1
```

On vide l'hôte et on le met en mode maintenance avant la première étape (avec DRS entièrement automatisé, `govc` attend que DRS déplace les VM) :

```bash
govc host.maintenance.enter "$HOST"
```

Première étape, `ENS_INTERRUPT`. On garde l'objet entier, on ne change que le mode, et on le renvoie par PUT (le `_revision` lu doit repartir avec) :

```bash
nsx "https://$NSX$EP/host-transport-nodes/$TN" \
  | jq '.host_switch_spec.host_switches[].host_switch_mode = "ENS_INTERRUPT"
      | .host_switch_spec.host_switches[].cpu_config = []' > tn-hop1.json
nsx -X PUT "https://$NSX$EP/host-transport-nodes/$TN" -d @tn-hop1.json
```

Attendre que le transport node soit réalisé :

```bash
nsx "https://$NSX$EP/host-transport-nodes/$TN/state" \
  | jq -r '.state, .node_deployment_state.state'      # success / success
```

Deuxième étape, `ENS` avec ses cœurs. Le bloc demande le nœud NUMA et le nombre de lcores, puis relit le transport node, car la révision a changé :

```bash
read -rp "NUMA node index [0]: " NUMA; NUMA=${NUMA:-0}
read -rp "lcores per NUMA node [2]: " LCORES; LCORES=${LCORES:-2}
nsx "https://$NSX$EP/host-transport-nodes/$TN" \
  | jq --argjson n "$NUMA" --argjson l "$LCORES" \
       '.host_switch_spec.host_switches[].host_switch_mode = "ENS"
      | .host_switch_spec.host_switches[].cpu_config = [{"numa_node_index":$n,"num_lcores":$l}]' > tn-hop2.json
nsx -X PUT "https://$NSX$EP/host-transport-nodes/$TN" -d @tn-hop2.json
```

On attend la deuxième étape de la même façon, puis on sort l'hôte du mode maintenance et on passe au suivant :

```bash
nsx "https://$NSX$EP/host-transport-nodes/$TN/state" \
  | jq -r '.state, .node_deployment_state.state'      # success / success
govc host.maintenance.exit "$HOST"
```

Une fois tous les hôtes faits, on attache le profil au cluster en mettant à jour sa transport node collection :

```bash
nsx "https://$NSX/policy/api/v1/infra/host-transport-node-profiles" | jq -r '.results[] | [.id, .host_switch_spec.host_switches[0].host_switch_mode] | @tsv'
nsx "https://$NSX$EP/transport-node-collections" | jq -r '.results[] | [.id, .compute_collection_id] | @tsv'
read -rp "Transport node collection id of the cluster: " TNC
read -rp "Profile id to attach: " TNP
nsx "https://$NSX$EP/transport-node-collections/$TNC" \
  | jq --arg p "/infra/host-transport-node-profiles/$TNP" '.transport_node_profile_id = $p' > tnc.json
nsx -X PUT "https://$NSX$EP/transport-node-collections/$TNC" -d @tnc.json
nsx "https://$NSX$EP/transport-node-collections/$TNC/state" | jq -r .state   # SUCCESS
```

### Toute la boucle avec un script

Un petit script Python enchaîne toute la boucle (évacuation, mode maintenance, les deux étapes, attente, sortie, hôte suivant, puis attachement du profil), avec pyVmomi pour vCenter et l'API policy de NSX : [edp_per_host.py](/files/edp-perf/edp_per_host.py) (`pip install pyvmomi requests`). Lancé sans argument, il demande tout : adresses de vCenter et de NSX, utilisateurs et mots de passe (saisis masqués, jamais enregistrés), puis le cluster, le mode visé, les lcores pour Performance et le profil à attacher, choisis dans des listes lues dans votre environnement. Il vérifie le paramètre vMotion de vCenter, affiche le plan et ne démarre qu'après que vous avez tapé `YES`. Il peut faire une pause avant chaque hôte, et `--dry-run` s'arrête après le plan.

![Démarrage interactif d'edp_per_host.py : questions sur les adresses et mots de passe de vCenter et NSX, choix du cluster dans une liste, mode actuel de chaque hôte, choix parmi les trois modes, nœud NUMA et lcores, Transport Node Profile correspondant proposé, puis plan hôte par hôte.](/images/edp-perf/cli-07-script-prompts.webp)

Il calcule lui-même les étapes et ignore les hôtes déjà dans le mode visé. Si DRS n'est pas entièrement automatisé, il attend que vous ayez déplacé les VM de chaque hôte. Une exécution ressemble à ceci :

![Sortie du script : esx-edp-01 déjà en ENS, ignoré ; esx-edp-02 et 03 ont chacun un vMotion, le mode maintenance, ENS_INTERRUPT réalisé en 10 s, ENS réalisé en 10 s, la sortie du mode maintenance ; puis le TNP edp-tnp-ens est attaché et l'état du cluster est SUCCESS.](/images/edp-perf/cli-03-api-install.webp)

## Passer d'un mode à l'autre

Pour l'activation, plus haut, chaque hôte va de Standard à Performance en deux étapes. Les sections suivantes expliquent pourquoi NSX exige l'étape du milieu et quelles règles valent pour tout changement de mode, puis détaillent le retour à Standard.

### Pourquoi le passage direct échoue

Dans le Transport Node Profile, le mode n'est qu'une liste déroulante. Si on le fait passer de Standard à Performance, NSX 4.2.4 refuse et demande de supprimer le host switch. Sur un cluster en production, cela revient à retirer l'overlay sous chaque VM.

Gabe Rosas décrit sur [vcfcore.tech](https://vcfcore.tech/2025/11/25/how-to-enable-edp-standard-with-zero-impact/) une méthode bien moins brutale pour EDP Standard : on change un hôte à la fois pendant qu'il est en mode maintenance, et on n'attache le nouveau profil au cluster qu'à la fin. Avec une étape de plus, la même méthode mène jusqu'à Performance, et elle sert aussi pour le retour.

### Les règles en bref

- Le passage direct `STANDARD` ⇄ `ENS` est refusé avec l'erreur 9544, au niveau du cluster comme hôte par hôte.
- En passant par le mode du milieu, c'est accepté : `STANDARD` → `ENS_INTERRUPT` → `ENS`, un hôte à la fois, environ 10 secondes par étape. Le retrait suit le chemin inverse.
- Avec l'hôte en mode maintenance, le changement de datapath n'atteint pas les workloads. Ils ne voient que le vMotion qui vide chaque hôte.
- Une fois tous les hôtes convertis, on attache le Transport Node Profile correspondant au cluster. NSX n'a plus rien à changer et passe directement à `SUCCESS`.

![Les trois modes de host switch NSX. STANDARD vers ENS_INTERRUPT et retour : accepté ; ENS_INTERRUPT vers ENS et retour : accepté ; STANDARD vers ENS en direct, dans un sens comme dans l'autre : refusé avec l'erreur 9544.](/images/edp-perf/fig-01-mode-transitions.svg)

Ces règles valent pour tout changement de mode, Standard ⇄ EDP Standard ⇄ EDP Performance : la boucle hôte par hôte est celle de l'activation, et le script accepte n'importe quel mode visé. La section suivante prend pour exemple le retour de Performance à Standard.

### Retour à Standard

Sur chaque hôte, les étapes s'enchaînent dans l'autre sens : `ENS` → `ENS_INTERRUPT` → `STANDARD`. Sauter l'étape du milieu et passer directement de `ENS` à `STANDARD` donne la même erreur 9544.

**Dans l'interface** (esx-edp-02) : Configure NSX sur l'hôte, Edit sur le host switch, Advanced Configuration. Depuis Performance, la liste ne propose qu'EDP Standard et Performance, pas Standard.

![Liste Mode quand l'hôte est en Performance : seuls Enhanced Datapath – Standard et Enhanced Datapath – Performance sont proposés.](/images/edp-perf/gui-08-modes-from-performance.webp)

Choisir **Enhanced Datapath – Standard**, Finish. Relancer Configure NSX et choisir **Standard**, Finish. Le switch ENS a disparu et les uplinks sont revenus sur le driver habituel :

![Après le retrait : nsxdp-cli indique No ENS switch configured et esxcfg-nics -e montre chaque vmnic sur nvmxnet3 avec ENS Driven False.](/images/edp-perf/cli-04-host-standard-verified.webp)

**Par l'API** : les deux mêmes PUT avec `ENS_INTERRUPT` puis `STANDARD`, `cpu_config` remis à `[]`. Ou relancer le script et choisir **Standard** comme mode visé :

![Sortie du script pour le retrait : esx-edp-01 et esx-edp-03 sont chacun évacués, mis en mode maintenance, passent de ENS à ENS_INTERRUPT puis à STANDARD en 10 s environ par étape, et sortent du mode maintenance ; esx-edp-02 est ignoré car déjà en Standard.](/images/edp-perf/cli-05-api-remove.webp)

**Dernière étape, au niveau du cluster** : sélectionner le cluster, Configure NSX, choisir le profil Standard, Save.

![Boîte NSX Installation sur le cluster avec le Transport Node Profile edp-tnp sélectionné.](/images/edp-perf/gui-10-cluster-apply-edp-tnp.webp)

![edp-cluster revenu sur Applied Profile edp-tnp, NSX Configuration Success sur chaque hôte.](/images/edp-perf/gui-11-cluster-back-to-standard.webp)

Un des hôtes du lab est apparu *Degraded* juste après le retrait : NSX voyait `uplink2` down alors qu'ESX voyait les deux liens up. C'était un état pNIC en cache, resté du changement de driver : l'état en temps réel (`GET /api/v1/transport-nodes/<id>/status?source=realtime`) répondait `UP`, et l'interface a suivi peu après. Vérifiez l'état en temps réel avant d'ouvrir un ticket.

## À garder en tête

Clonez le Transport Node Profile au lieu de modifier celui qui est attaché au cluster, car une modification du profil attaché touche tous les hôtes à la fois. N'attachez le clone qu'une fois que tous les hôtes y correspondent. D'ici là, *Mismatch* sur les hôtes convertis est normal, tout comme un *Degraded* périmé juste après le changement de driver.

En mode Performance, `cpu_config` retire des cœurs logiques aux VM sur chaque nœud NUMA listé, donc dimensionnez-le selon le trafic que vous attendez vraiment. Mesurez ce qu'EDP Performance vous apporte sur le matériel et les drivers sur lesquels il tournera.

La méthode par hôte en mode maintenance vient du billet de Gabe Rosas [How to Enable EDP Standard with Zero Impact in VCF 5.2](https://vcfcore.tech/2025/11/25/how-to-enable-edp-standard-with-zero-impact/) et de sa [suite sur enable_uens.py](https://vcfcore.tech/2026/02/25/using-enable_uens-py-for-edp-transitions-in-vcf-5-2/), qui portent sur EDP Standard. Ce guide y ajoute la seconde étape vers Performance et le chemin du retour.
