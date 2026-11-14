---
title: "NSX Enhanced Datapath Performance: turning it on and off one host at a time"
date: 2026-11-14
description: "How to move an NSX 4.2.4 cluster from the standard datapath to Enhanced Datapath – Performance and back again, without taking NSX off the hosts. NSX refuses the direct jump, so every host goes through EDP Standard first. GUI path and API path."
summary: "NSX 4.2.4 will not switch a host straight from Standard to Enhanced Datapath – Performance; it wants you to delete the host switch. You can skip that by going one host at a time in maintenance mode, through EDP Standard, then attach the new profile to the cluster. Same path back. Both the UI and the API."
tags: ["NSX", "Enhanced Datapath", "EDP", "ENS", "vSphere 8", "Transport Node Profile", "Holodeck"]
categories: ["Field notes"]
showToc: true
tocOpen: false
images: ["/images/edp-perf/fig-01-mode-transitions.svg"]
params:
  seoDescription: "Enable and remove NSX Enhanced Datapath – Performance per host, through EDP Standard, in maintenance mode. GUI and API steps."
---

I wanted to test Enhanced Datapath – Performance (EDP Performance, the old "ENS" mode) on a cluster that already runs NSX in standard mode, and then take it back off. On paper it is one dropdown in the Transport Node Profile. In practice NSX 4.2.4 refuses the change and tells you to delete the host switch, which on a live cluster means pulling the overlay out from under every VM.

Gabe Rosas describes a much gentler way on [vcfcore.tech](https://vcfcore.tech/2025/11/25/how-to-enable-edp-standard-with-zero-impact/) for EDP Standard: change one host at a time while it sits in maintenance mode, and only attach the new profile to the cluster at the end. The same method gets you to Performance and back if you add one hop. This post is that procedure, in the UI and with the API.

*November 2026 · NSX 4.2.4.1 · vCenter 8.0 U3 · ESX 8.0 U3i · names in the examples are from my lab*

## The short version

- NSX has three host switch modes: `STANDARD`, `ENS_INTERRUPT` (EDP Standard) and `ENS` (EDP Performance).
- `STANDARD` ⇄ `ENS` in one step is rejected with error 9544, both at cluster level and per host.
- `STANDARD` → `ENS_INTERRUPT` → `ENS` is accepted, one host at a time, about 10 seconds per hop. Removal is the mirror image.
- Do it with the host in maintenance mode and the workloads never notice the datapath change. The only thing they see is the vMotion that empties each host.
- Once every host is converted, attach the matching Transport Node Profile to the cluster. NSX has nothing left to change and goes straight to `SUCCESS`.

![The three NSX host switch modes. STANDARD to ENS_INTERRUPT and back is accepted; ENS_INTERRUPT to ENS and back is accepted; STANDARD to ENS directly, in either direction, is rejected with error 9544.](/images/edp-perf/fig-01-mode-transitions.svg)

## The quick flow

This is the whole exercise on one page. It's the same three steps for turning EDP Performance on and for turning it off. The direction of the hops and the profile you attach at the end are the only differences.

1. **Prepare once.** Clone the cluster's Transport Node Profile and set the target mode on the copy (`edp-tnp-ens`, EDP Performance with 2 lcores on NUMA 0). In vCenter, allow vMotion between hosts in different modes. For the way back you need nothing new, since the Standard profile `edp-tnp` is still there.
2. **One host at a time.** vMotion the VMs off the host and put it in maintenance mode. Change its host switch in two hops, `STANDARD → ENS_INTERRUPT → ENS` (or the reverse), about 10 seconds each. Check the host, exit maintenance mode, and move on to the next one.
3. **Finish at cluster level.** Attach the matching profile to the cluster. Every host already matches it, so NSX goes straight to `SUCCESS`.

![End-to-end flow. Enable: start with all hosts STANDARD on edp-tnp, prepare edp-tnp-ens once, then for each host empty it, enter maintenance mode, hop STANDARD to ENS_INTERRUPT to ENS, exit maintenance mode; finish by attaching edp-tnp-ens to the cluster. Remove: the same loop with ENS to ENS_INTERRUPT to STANDARD, then attach edp-tnp.](/images/edp-perf/fig-04-quick-flow.svg)

## Three modes, three names

The UI, the API and the ESX shell each call the same thing something different, which does not help.

| UI (Advanced Configuration › Mode) | API `host_switch_mode` | What changes on the host |
|---|---|---|
| Standard | `STANDARD` | Normal datapath, vendor driver (`nvmxnet3` here) |
| Enhanced Datapath – Standard | `ENS_INTERRUPT` | ENS driver (`nvmxnet3_ens`), interrupt driven, no dedicated cores |
| Enhanced Datapath – Performance | `ENS` | ENS driver, polling on dedicated logical cores; `cpu_config` is mandatory |

`cpu_config` is a list of `{numa_node_index, num_lcores}` pairs. Those cores are taken away from VMs and spin for the datapath, so size it on real hardware with care.

## Before you start

1. The NICs must be ENS capable. On every host, `esxcfg-nics -e` must show `ENS Capable = True` for the uplinks of the NSX switch. Check the Broadcom Compatibility Guide for the exact driver and firmware.
2. vCenter has to allow vMotion between hosts in different modes, because during the rollout some hosts are on one mode and some on the other. Set the vCenter advanced setting `config.vpxd.network.allowVmotionBetweenENSModes = true` (vCenter › Configure › Advanced Settings, or `govc option.set config.vpxd.network.allowVmotionBetweenENSModes true`).
3. Clone the Transport Node Profile: same host switch, same uplink profile, same IP pool, only the mode differs, plus `cpu_config` for Performance. Don't attach it yet. In the examples they are `edp-tnp` (Standard) and `edp-tnp-ens` (Performance).
4. You need room to empty one host at a time, with DRS fully automated or by manual vMotion. The host has to be empty before you touch it.

## Enabling EDP Performance from the UI

Start with the host empty and in maintenance mode.

**1. Open the host, not the cluster.** System › Fabric › Hosts, expand the cluster, click the three dots next to the host, **Configure NSX**.

![NSX Hosts view with edp-cluster expanded and the node menu of esx-edp-01 open on Configure NSX.](/images/edp-perf/gui-01-host-menu-configure-nsx.webp)

**2. Edit the host switch.** Next past *Host details*. On *Prepare Host*, the three dots on the `edp-vds` row › **Edit**, then expand **Advanced Configuration**. The mode is at the bottom.

![Prepare Host step: the edp-vds host switch in edit mode, with Advanced Configuration expanded and Mode set to Standard.](/images/edp-perf/gui-02-edit-host-switch-standard.webp)

**3. First hop.** Open the Mode list. From Standard the UI offers only Standard and Enhanced Datapath – Standard. Performance is missing from the list: that is the two-hop rule, as the UI shows it. Pick **Enhanced Datapath – Standard**, **Add**, **Finish**. About ten seconds later the host is realized as `ENS_INTERRUPT`.

![Mode dropdown when the host is in Standard: only Standard and Enhanced Datapath – Standard are offered.](/images/edp-perf/gui-03-modes-from-standard.webp)

**4. Second hop.** Configure NSX on the same host again, Edit, Advanced Configuration. Now the list has all three modes. Pick **Enhanced Datapath – Performance**, and a mandatory **CPU Config** field appears next to it.

![Mode dropdown when the host is in Enhanced Datapath – Standard: Standard, Enhanced Datapath – Standard and Enhanced Datapath – Performance are all offered.](/images/edp-perf/gui-04-modes-from-edp-standard.webp)

![Mode set to Enhanced Datapath – Performance with the required CPU Config field and its Set link.](/images/edp-perf/gui-05-performance-cpu-config.webp)

**5. Give it cores.** **Set** › **Add**: NUMA node index `0`, lcores per NUMA node `2`. Add, Save, then Add and Finish in the wizard.

![CPU Config dialog with one row: NUMA Node Index 0, LCores per NUMA Node 2.](/images/edp-perf/gui-06-cpu-config-dialog.webp)

**6. Check it on the host.** `nsxdp-cli ens switch list` now shows an ENS switch with two lcores, and `esxcfg-nics -e` shows the NSX uplinks running on the `_ens` driver with `ENS Driven = True`. vmnic0 (management) and vmnic3 (vMotion, separate VDS) are untouched. Exit maintenance mode and move on to the next host.

![nsxdp-cli ens switch list shows DvsPortset-0 with MTU 9000 and 2 lcores; esxcfg-nics -e shows vmnic1 and vmnic2 on nvmxnet3_ens with ENS Driven True.](/images/edp-perf/cli-02-host-ens-verified.webp)

While you go host by host, the cluster still points to the old profile and NSX marks the converted hosts as *Mismatch*. That is expected and goes away at the last step.

![NSX Hosts view during the rollout: one host shows Mismatch in the NSX Configuration column.](/images/edp-perf/gui-09-mismatch-during-rollout.webp)

**7. Attach the profile to the cluster.** When every host is on Performance: select the cluster, **Configure NSX**, pick the Performance profile (`edp-tnp-ens`), Save. The hosts already match it, so there is nothing to push and the cluster goes to *Prepared* with the new profile.

![edp-cluster with Applied Profile edp-tnp-ens, three hosts up, all NSX Configuration Success.](/images/edp-perf/gui-07-cluster-edp-tnp-ens.webp)

## Enabling EDP Performance with the API

The UI wizard writes the same object, the policy *host transport node*, so the API path is: read it, change the mode, write it back, wait, and do it a second time.

Set up the session once. It asks for the NSX Manager and its credentials and keeps them in shell variables only:

```bash
read -rp  "NSX Manager FQDN or IP: " NSX
read -rp  "NSX user [admin]: " NSX_USER; NSX_USER=${NSX_USER:-admin}
read -rsp "NSX password: " NSX_PASS; echo
EP=/policy/api/v1/infra/sites/default/enforcement-points/default
nsx() { curl -sk -u "$NSX_USER:$NSX_PASS" -H 'Content-Type: application/json' "$@"; }
nsx "https://$NSX$EP/host-transport-nodes" \
  | jq -r '.results[] | [.display_name, .host_switch_spec.host_switches[0].host_switch_mode] | @tsv'
```

Pick the host and look up its transport node:

```bash
read -rp "Host to change (name as listed above): " HOST
TN=$(nsx "https://$NSX$EP/host-transport-nodes" | jq -r --arg h "$HOST" '.results[] | select(.display_name==$h) | .id')
echo "transport node: $TN"
```

Hop 1, `ENS_INTERRUPT`. Keep the whole object, change only the mode, PUT it back (the `_revision` you read has to go back with it):

```bash
nsx "https://$NSX$EP/host-transport-nodes/$TN" \
  | jq '.host_switch_spec.host_switches[].host_switch_mode = "ENS_INTERRUPT"
      | .host_switch_spec.host_switches[].cpu_config = []' > tn-hop1.json
nsx -X PUT "https://$NSX$EP/host-transport-nodes/$TN" -d @tn-hop1.json
```

Wait until the node is realized:

```bash
nsx "https://$NSX$EP/host-transport-nodes/$TN/state" \
  | jq -r '.state, .node_deployment_state.state'      # success / success
```

Hop 2, `ENS` with its cores. It asks for the NUMA node and the number of lcores, then reads the node again, because the revision moved:

```bash
read -rp "NUMA node index [0]: " NUMA; NUMA=${NUMA:-0}
read -rp "lcores per NUMA node [2]: " LCORES; LCORES=${LCORES:-2}
nsx "https://$NSX$EP/host-transport-nodes/$TN" \
  | jq --argjson n "$NUMA" --argjson l "$LCORES" \
       '.host_switch_spec.host_switches[].host_switch_mode = "ENS"
      | .host_switch_spec.host_switches[].cpu_config = [{"numa_node_index":$n,"num_lcores":$l}]' > tn-hop2.json
nsx -X PUT "https://$NSX$EP/host-transport-nodes/$TN" -d @tn-hop2.json
```

Maintenance mode is a vCenter job, not an NSX one. With `govc`, which asks nothing by itself, so set its variables from prompts too:

```bash
read -rp  "vCenter FQDN or IP: " VC
read -rp  "vCenter user [administrator@vsphere.local]: " VC_USER; VC_USER=${VC_USER:-administrator@vsphere.local}
read -rsp "vCenter password: " VC_PASS; echo
export GOVC_URL="https://$VC" GOVC_USERNAME="$VC_USER" GOVC_PASSWORD="$VC_PASS" GOVC_INSECURE=1
govc host.maintenance.enter "$HOST"     # before hop 1
govc host.maintenance.exit  "$HOST"     # after hop 2
```

When all hosts are done, attach the profile to the cluster by updating its transport node collection:

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

I wrapped the whole loop (evacuate, maintenance mode, both hops, wait, exit, next host, attach the profile) in a small Python script that uses pyVmomi for vCenter and the NSX policy API: [edp_per_host.py](/files/edp-perf/edp_per_host.py) (`pip install pyvmomi requests`). Run it with no arguments and it asks for everything: vCenter and NSX addresses, user names and passwords (typed hidden, never saved), then the cluster, the target mode, the lcores for Performance and the profile to attach, picked from lists it reads from your environment. It checks the vCenter vMotion setting, shows the plan, and only starts after you type `YES`. It can pause before each host, and `--dry-run` stops after the plan.

![Interactive start of edp_per_host.py: prompts for vCenter and NSX addresses and passwords, picks the cluster from a list, shows each host's current mode, offers the three target modes, asks for NUMA node and lcores, offers the matching Transport Node Profile, and prints the per-host plan.](/images/edp-perf/cli-07-script-prompts.webp)

It works out the hops by itself and skips hosts that are already in the target mode. If DRS is not fully automated, it waits for you to move the VMs off each host. A run looks like this:

![Script output: esx-edp-01 already ENS and skipped; esx-edp-02 and 03 each get a vMotion, maintenance mode, ENS_INTERRUPT realized in 10 s, ENS realized in 10 s, exit maintenance mode; then the TNP edp-tnp-ens is attached and the cluster state is SUCCESS.](/images/edp-perf/cli-03-api-install.webp)

## Removing it: back to Standard

Same road, the other way: `ENS` → `ENS_INTERRUPT` → `STANDARD`, host by host, in maintenance mode, then attach the Standard profile. Going straight from `ENS` to `STANDARD` hits the same error 9544.

**From the UI** (esx-edp-02): Configure NSX on the host, Edit the host switch, Advanced Configuration. From Performance, the list offers EDP Standard and Performance only, no Standard.

![Mode dropdown when the host is in Performance: only Enhanced Datapath – Standard and Enhanced Datapath – Performance are offered.](/images/edp-perf/gui-08-modes-from-performance.webp)

Pick **Enhanced Datapath – Standard**, Finish. Run Configure NSX again and pick **Standard**, Finish. The ENS switch is gone and the uplinks are back on the regular driver:

![After removal: nsxdp-cli reports No ENS switch configured and esxcfg-nics -e shows every vmnic on nvmxnet3 with ENS Driven False.](/images/edp-perf/cli-04-host-standard-verified.webp)

**From the API**: the same two PUTs with `ENS_INTERRUPT` and then `STANDARD`, `cpu_config` back to `[]`. Or run the script again and pick **Standard** as the target mode:

![Script output for the removal: esx-edp-01 and esx-edp-03 each evacuated, put in maintenance mode, ENS to ENS_INTERRUPT then to STANDARD in about 10 s per hop, and taken out of maintenance mode; esx-edp-02 skipped because it was already Standard.](/images/edp-perf/cli-05-api-remove.webp)

**Last step, cluster level**: select the cluster, Configure NSX, choose the Standard profile, Save.

![NSX Installation dialog on the cluster with Transport Node Profile edp-tnp selected.](/images/edp-perf/gui-10-cluster-apply-edp-tnp.webp)

![edp-cluster back on Applied Profile edp-tnp with every host's NSX Configuration Success.](/images/edp-perf/gui-11-cluster-back-to-standard.webp)

One of my hosts showed *Degraded* right after the removal, with NSX reporting `uplink2` down while ESX reported both links up. That was a cached pNIC status left over from the driver swap: asking for the real-time status (`GET /api/v1/transport-nodes/<id>/status?source=realtime`) came back `UP`, and the UI caught up a moment later. Check that before you open a ticket.

## Things to keep in mind

Clone the Transport Node Profile instead of editing the one attached to the cluster, because editing the attached profile is the all-hosts-at-once path. Attach the clone only once every host already matches it. Until then, *Mismatch* on the converted hosts is normal, and so is a stale *Degraded* right after the driver changes.

Performance mode's lcores are real cores: `cpu_config` takes logical cores away from VMs on every NUMA node you list, so size it against the traffic you actually expect. Measure what EDP Performance buys you on the hardware and drivers you will run it on.

Credit where it is due: the per-host, maintenance-mode method comes from Gabe Rosas' [How to Enable EDP Standard with Zero Impact in VCF 5.2](https://vcfcore.tech/2025/11/25/how-to-enable-edp-standard-with-zero-impact/) and the [enable_uens.py follow-up](https://vcfcore.tech/2026/02/25/using-enable_uens-py-for-edp-transitions-in-vcf-5-2/). All I added was the second hop to Performance and the way back.
