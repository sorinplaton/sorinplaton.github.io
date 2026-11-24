---
title: "NSX Enhanced Datapath: what it is, how to enable it, how to change modes"
date: 2026-11-14
description: "What EDP Standard and EDP Performance do on NSX 4.2.4, how to enable EDP Performance one host at a time through EDP Standard on a cluster that already runs NSX, and how to change modes or go back to Standard, from the UI and the API."
summary: "NSX Enhanced Datapath (EDP) adds a flow cache to the ESX host switch and has two modes, EDP Standard and EDP Performance. The post enables EDP Performance on a cluster that already runs NSX 4.2.4, one host at a time in maintenance mode, from the UI, the API or a script. NSX rejects a direct switch from Standard to Performance, so every mode change goes through EDP Standard, the way back included."
tags: ["NSX", "Enhanced Datapath", "EDP", "ENS", "vSphere 8", "Transport Node Profile", "Holodeck"]
categories: ["Field notes"]
showToc: true
tocOpen: false
images: ["/images/edp-perf/fig-01-mode-transitions.svg"]
params:
  seoDescription: "NSX Enhanced Datapath explained: the two EDP modes, enabling EDP Performance host by host, and changing modes through EDP Standard. UI and API steps."
---

NSX Enhanced Datapath (EDP) is a faster packet path in the ESX host switch. It comes in two modes, EDP Standard and EDP Performance (the old "ENS" mode). This guide explains what EDP does, then enables EDP Performance on a cluster that already runs NSX in Standard mode, from the UI, with the API and with a script. A later part covers moving between modes, the way back to Standard included, and the post closes with a few things to keep in mind.

*November 2026 · NSX 4.2.4.1 · vCenter 8.0 U3 · ESX 8.0 U3i · names in the examples are from the lab*

## What is Enhanced Datapath

EDP borrows ideas from DPDK. The standard datapath parses and classifies every packet through the full switching and firewall pipeline. EDP adds a flow cache: the first packet of a flow takes the full path, and the packets after it reuse the result. It is also NUMA aware and tries to keep packet processing on the NUMA node of the NIC. Both EDP modes need NICs and drivers certified for them.

EDP Standard runs on interrupts and reserves no cores, so it suits general-purpose clusters, where it uses less CPU for the same traffic. EDP Performance dedicates logical cores that poll the NICs all the time. Latency goes down, and the mode is meant for telco network functions (NFV) and other packet-heavy workloads. The price is those cores, which VMs cannot use even when no traffic flows. The lab is nested, so none of this is measured here; the post only shows how to set the mode and change it safely.

### Three host switch modes, three names

The UI and the API use different names for the same mode. The last column shows what changes on the host.

| UI (Advanced Configuration › Mode) | API `host_switch_mode` | What changes on the host |
|---|---|---|
| Standard | `STANDARD` | Normal datapath, vendor driver (`nvmxnet3` here) |
| Enhanced Datapath – Standard | `ENS_INTERRUPT` | ENS driver (`nvmxnet3_ens`), interrupt driven, no dedicated cores |
| Enhanced Datapath – Performance | `ENS` | ENS driver, polling on dedicated logical cores; `cpu_config` is mandatory |

`cpu_config` is a list of `{numa_node_index, num_lcores}` pairs. Those cores are taken away from VMs and spin for the datapath, so on real hardware size the list carefully.

Newer Broadcom documentation, including KB 402229, calls EDP Performance "EDP Dedicated". The mode is the same and is still `ENS` in the API. The NSX 4.2.4 UI in this lab shows "Enhanced Datapath – Performance", and the post uses that name.

## How to enable EDP

Enabling EDP Performance means moving every host to it without taking NSX off the cluster. It is done host by host, each host in maintenance mode, and each one goes through EDP Standard on its way to Performance. NSX rejects the direct change; the section on moving between modes, further down, explains why.

### Before you start

1. The NICs must be ENS capable. On every host, `esxcfg-nics -e` must show `ENS Capable = True` for the uplinks of the NSX switch. Check the Broadcom Compatibility Guide for the exact driver and firmware.
2. vCenter has to allow vMotion between hosts in different modes, because during the rollout some hosts are on one mode and some on the other. Set the vCenter advanced setting `config.vpxd.network.allowVmotionBetweenENSModes = true` (vCenter › Configure › Advanced Settings, or `govc option.set config.vpxd.network.allowVmotionBetweenENSModes true`).
3. Clone the Transport Node Profile: same host switch, same uplink profile, same IP pool, only the mode differs, plus `cpu_config` for Performance. Don't attach it yet. In the examples they are `edp-tnp` (Standard) and `edp-tnp-ens` (Performance).
4. You need room to empty one host at a time, with DRS fully automated or by manual vMotion. The host has to be empty before you touch it.

### The quick flow

Turning EDP Performance on and turning it off take the same three steps. Only the direction of the hops and the profile attached at the end differ.

1. **Prepare once.** Clone the cluster's Transport Node Profile and set the target mode on the copy (`edp-tnp-ens`, EDP Performance with 2 lcores on NUMA 0). In vCenter, allow vMotion between hosts in different modes. The way back needs nothing new, since the Standard profile `edp-tnp` is still there.
2. **One host at a time.** vMotion the VMs off the host and put it in maintenance mode. Change its host switch in two hops, `STANDARD → ENS_INTERRUPT → ENS` (or the reverse), about 10 seconds each. Check the host, exit maintenance mode, and move on to the next one.
3. **Finish at cluster level.** Attach the matching profile to the cluster. Every host already matches it, so NSX goes straight to `SUCCESS`.

![End-to-end flow. Enable: start with all hosts STANDARD on edp-tnp, prepare edp-tnp-ens once, then for each host empty it, enter maintenance mode, hop STANDARD to ENS_INTERRUPT to ENS, exit maintenance mode; finish by attaching edp-tnp-ens to the cluster. Remove: the same loop with ENS to ENS_INTERRUPT to STANDARD, then attach edp-tnp.](/images/edp-perf/fig-04-quick-flow.svg)

### From the UI

Start with the host empty and in maintenance mode.

**1. Open the host, not the cluster.** System › Fabric › Hosts, expand the cluster, click the three dots next to the host, **Configure NSX**.

![NSX Hosts view with edp-cluster expanded and the node menu of esx-edp-01 open on Configure NSX.](/images/edp-perf/gui-01-host-menu-configure-nsx.webp)

**2. Edit the host switch.** Next past *Host details*. On *Prepare Host*, the three dots on the `edp-vds` row › **Edit**, then expand **Advanced Configuration**. The mode is at the bottom.

![Prepare Host step: the edp-vds host switch in edit mode, with Advanced Configuration expanded and Mode set to Standard.](/images/edp-perf/gui-02-edit-host-switch-standard.webp)

**3. First hop.** Open the Mode list. From Standard the UI offers only Standard and Enhanced Datapath – Standard. Performance is not in the list: that is how the UI enforces the two-hop rule explained in *Why a direct switch fails*, further down. Pick **Enhanced Datapath – Standard**, **Add**, **Finish**. About ten seconds later the host is realized as `ENS_INTERRUPT`.

![Mode dropdown when the host is in Standard: only Standard and Enhanced Datapath – Standard are offered.](/images/edp-perf/gui-03-modes-from-standard.webp)

**4. Second hop.** Configure NSX on the same host again, Edit, Advanced Configuration. Now the list has all three modes. Pick **Enhanced Datapath – Performance**, and a mandatory **CPU Config** field appears next to it.

![Mode dropdown when the host is in Enhanced Datapath – Standard: Standard, Enhanced Datapath – Standard and Enhanced Datapath – Performance are all offered.](/images/edp-perf/gui-04-modes-from-edp-standard.webp)

![Mode set to Enhanced Datapath – Performance with the required CPU Config field and its Set link.](/images/edp-perf/gui-05-performance-cpu-config.webp)

**5. Give it cores.** **Set** › **Add**: NUMA node index `0`, lcores per NUMA node `2`. Add, Save, then Add and Finish in the wizard.

![CPU Config dialog with one row: NUMA Node Index 0, LCores per NUMA Node 2.](/images/edp-perf/gui-06-cpu-config-dialog.webp)

**6. Check it on the host.** `nsxdp-cli ens switch list` now shows an ENS switch with two lcores, and `esxcfg-nics -e` shows the NSX uplinks running on the `_ens` driver with `ENS Driven = True`. vmnic0 (management) and vmnic3 (vMotion, separate VDS) are untouched. Exit maintenance mode and move on to the next host.

![nsxdp-cli ens switch list shows DvsPortset-0 with MTU 9000 and 2 lcores; esxcfg-nics -e shows vmnic1 and vmnic2 on nvmxnet3_ens with ENS Driven True.](/images/edp-perf/cli-02-host-ens-verified.webp)

While you go host by host, the cluster still points to the old profile and NSX marks the converted hosts as *Mismatch*. This is expected and clears at the last step.

![NSX Hosts view during the rollout: one host shows Mismatch in the NSX Configuration column.](/images/edp-perf/gui-09-mismatch-during-rollout.webp)

**7. Attach the profile to the cluster.** When every host is on Performance: select the cluster, **Configure NSX**, pick the Performance profile (`edp-tnp-ens`), Save. The hosts already match it, so there is nothing to push and the cluster goes to *Prepared* with the new profile.

![edp-cluster with Applied Profile edp-tnp-ens, three hosts up, all NSX Configuration Success.](/images/edp-perf/gui-07-cluster-edp-tnp-ens.webp)

### With the API

The UI wizard writes the policy *host transport node* object, and the API path works on that same object: read it, change the mode, write it back, wait, then repeat for the second hop.

Set up the session once. The snippet prompts for the NSX Manager and its credentials and keeps them only in shell variables:

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

Maintenance mode is handled by vCenter. `govc` does not prompt on its own, so its variables are read from prompts as well:

```bash
read -rp  "vCenter FQDN or IP: " VC
read -rp  "vCenter user [administrator@vsphere.local]: " VC_USER; VC_USER=${VC_USER:-administrator@vsphere.local}
read -rsp "vCenter password: " VC_PASS; echo
export GOVC_URL="https://$VC" GOVC_USERNAME="$VC_USER" GOVC_PASSWORD="$VC_PASS" GOVC_INSECURE=1
```

Empty the host and put it in maintenance mode before the first hop (with DRS fully automated, `govc` waits while DRS moves the VMs off):

```bash
govc host.maintenance.enter "$HOST"
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

Hop 2, `ENS` with its cores. The snippet asks for the NUMA node and the number of lcores, then reads the node again because the revision has changed:

```bash
read -rp "NUMA node index [0]: " NUMA; NUMA=${NUMA:-0}
read -rp "lcores per NUMA node [2]: " LCORES; LCORES=${LCORES:-2}
nsx "https://$NSX$EP/host-transport-nodes/$TN" \
  | jq --argjson n "$NUMA" --argjson l "$LCORES" \
       '.host_switch_spec.host_switches[].host_switch_mode = "ENS"
      | .host_switch_spec.host_switches[].cpu_config = [{"numa_node_index":$n,"num_lcores":$l}]' > tn-hop2.json
nsx -X PUT "https://$NSX$EP/host-transport-nodes/$TN" -d @tn-hop2.json
```

Wait for the second hop the same way, then take the host out of maintenance mode and move on to the next one:

```bash
nsx "https://$NSX$EP/host-transport-nodes/$TN/state" \
  | jq -r '.state, .node_deployment_state.state'      # success / success
govc host.maintenance.exit "$HOST"
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

### The whole loop with a script

A small Python script runs the whole loop (evacuate, maintenance mode, both hops, wait, exit, next host, attach the profile), using pyVmomi for vCenter and the NSX policy API: [edp_per_host.py](/files/edp-perf/edp_per_host.py) (`pip install pyvmomi requests`). Run it with no arguments and it asks for everything: vCenter and NSX addresses, user names and passwords (typed hidden, never saved), then the cluster, the target mode, the lcores for Performance and the profile to attach, picked from lists it reads from your environment. It checks the vCenter vMotion setting, shows the plan, and only starts after you type `YES`. It can pause before each host, and `--dry-run` stops after the plan.

![Interactive start of edp_per_host.py: prompts for vCenter and NSX addresses and passwords, picks the cluster from a list, shows each host's current mode, offers the three target modes, asks for NUMA node and lcores, offers the matching Transport Node Profile, and prints the per-host plan.](/images/edp-perf/cli-07-script-prompts.webp)

It works out the hops by itself and skips hosts that are already in the target mode. If DRS is not fully automated, it waits for you to move the VMs off each host. A run looks like this:

![Script output: esx-edp-01 already ENS and skipped; esx-edp-02 and 03 each get a vMotion, maintenance mode, ENS_INTERRUPT realized in 10 s, ENS realized in 10 s, exit maintenance mode; then the TNP edp-tnp-ens is attached and the cluster state is SUCCESS.](/images/edp-perf/cli-03-api-install.webp)

## How to transition between modes

The enable steps above take each host from Standard to Performance in two hops. The sections below explain why NSX needs the middle hop and which rules apply to any change of mode, then walk through the way back to Standard.

### Why a direct switch fails

In the Transport Node Profile the mode is a single dropdown. Change it from Standard to Performance and NSX 4.2.4 refuses, telling you to delete the host switch. On a live cluster that means pulling the overlay out from under every VM.

Gabe Rosas describes a far less disruptive method for EDP Standard on [vcfcore.tech](https://vcfcore.tech/2025/11/25/how-to-enable-edp-standard-with-zero-impact/): change one host at a time while it sits in maintenance mode, and attach the new profile to the cluster only at the end. With one extra hop the same method reaches Performance, and it works for the way back too.

### The rules in short

- A direct `STANDARD` ⇄ `ENS` change is rejected with error 9544, both at cluster level and per host.
- Going through the middle mode is accepted: `STANDARD` → `ENS_INTERRUPT` → `ENS`, one host at a time, about 10 seconds per hop. Removal is the mirror image.
- With the host in maintenance mode, the datapath change does not reach the workloads. They only see the vMotion that empties each host.
- Once every host is converted, attach the matching Transport Node Profile to the cluster. NSX has nothing left to change and goes straight to `SUCCESS`.

![The three NSX host switch modes. STANDARD to ENS_INTERRUPT and back is accepted; ENS_INTERRUPT to ENS and back is accepted; STANDARD to ENS directly, in either direction, is rejected with error 9544.](/images/edp-perf/fig-01-mode-transitions.svg)

The same rules hold for any change of mode, Standard ⇄ EDP Standard ⇄ EDP Performance: the per-host loop is the one used for enabling, and the script accepts any target mode. The next section uses the way back from Performance to Standard as the example.

### Back to Standard

On each host the hops run the other way, `ENS` → `ENS_INTERRUPT` → `STANDARD`. Skipping the middle step and going straight from `ENS` to `STANDARD` hits the same error 9544.

**From the UI** (esx-edp-02): Configure NSX on the host, Edit the host switch, Advanced Configuration. From Performance, the list offers EDP Standard and Performance only, no Standard.

![Mode dropdown when the host is in Performance: only Enhanced Datapath – Standard and Enhanced Datapath – Performance are offered.](/images/edp-perf/gui-08-modes-from-performance.webp)

Pick **Enhanced Datapath – Standard**, Finish. Run Configure NSX again and pick **Standard**, Finish. The ENS switch is gone and the uplinks are back on the regular driver:

![After removal: nsxdp-cli reports No ENS switch configured and esxcfg-nics -e shows every vmnic on nvmxnet3 with ENS Driven False.](/images/edp-perf/cli-04-host-standard-verified.webp)

**From the API**: the same two PUTs with `ENS_INTERRUPT` and then `STANDARD`, `cpu_config` back to `[]`. Or run the script again and pick **Standard** as the target mode:

![Script output for the removal: esx-edp-01 and esx-edp-03 each evacuated, put in maintenance mode, ENS to ENS_INTERRUPT then to STANDARD in about 10 s per hop, and taken out of maintenance mode; esx-edp-02 skipped because it was already Standard.](/images/edp-perf/cli-05-api-remove.webp)

**Last step, cluster level**: select the cluster, Configure NSX, choose the Standard profile, Save.

![NSX Installation dialog on the cluster with Transport Node Profile edp-tnp selected.](/images/edp-perf/gui-10-cluster-apply-edp-tnp.webp)

![edp-cluster back on Applied Profile edp-tnp with every host's NSX Configuration Success.](/images/edp-perf/gui-11-cluster-back-to-standard.webp)

One of the lab hosts showed *Degraded* right after the removal, with NSX reporting `uplink2` down while ESX reported both links up. That was a cached pNIC status left over from the driver swap: asking for the real-time status (`GET /api/v1/transport-nodes/<id>/status?source=realtime`) came back `UP`, and the UI caught up a moment later. Check the real-time status before opening a ticket.

## Things to keep in mind

Clone the Transport Node Profile instead of editing the one attached to the cluster, because a change to the attached profile hits every host at once. Attach the clone only once every host already matches it. Until then, *Mismatch* on the converted hosts is normal, and so is a stale *Degraded* right after the driver changes.

In Performance mode, `cpu_config` takes logical cores away from VMs on every NUMA node you list, so size it against the traffic you actually expect. Measure what EDP Performance brings on the hardware and drivers you will run it on.

The per-host, maintenance-mode method comes from Gabe Rosas' [How to Enable EDP Standard with Zero Impact in VCF 5.2](https://vcfcore.tech/2025/11/25/how-to-enable-edp-standard-with-zero-impact/) and the [enable_uens.py follow-up](https://vcfcore.tech/2026/02/25/using-enable_uens-py-for-edp-transitions-in-vcf-5-2/), which cover EDP Standard. This guide adds the second hop to Performance and the way back.
