---
title: "NSX Enhanced Datapath Performance: turning it on and off one host at a time"
date: 2026-11-14
description: "How to move an NSX 4.2.4 cluster from the standard datapath to Enhanced Datapath – Performance and back again, without taking NSX off the hosts. NSX refuses the direct jump, so every host goes through EDP Standard first. GUI path, API path, the error you hit if you skip a step, and what the change actually cost a running workload."
summary: "NSX 4.2.4 will not switch a host straight from Standard to Enhanced Datapath – Performance; it wants you to delete the host switch. You can skip that by going one host at a time in maintenance mode, through EDP Standard, then attach the new profile to the cluster. Same path back. Both the UI and the API, with measured impact."
tags: ["NSX", "Enhanced Datapath", "EDP", "ENS", "vSphere 8", "Transport Node Profile", "Holodeck"]
categories: ["Field notes"]
showToc: true
tocOpen: false
images: ["/images/edp-perf/fig-01-mode-transitions.svg"]
params:
  seoDescription: "Enable and remove NSX Enhanced Datapath – Performance per host, through EDP Standard, in maintenance mode. GUI and API steps, error 9544, measured impact."
---

I wanted to test Enhanced Datapath – Performance (EDP Performance, the old "ENS" mode) on a cluster that already runs NSX in standard mode, and then take it back off. On paper it is one dropdown in the Transport Node Profile. In practice NSX 4.2.4 refuses the change and tells you to delete the host switch, which on a live cluster means pulling the overlay out from under every VM.

Gabe Rosas describes a much gentler way on [vcfcore.tech](https://vcfcore.tech/2025/11/25/how-to-enable-edp-standard-with-zero-impact/) for EDP Standard: change one host at a time while it sits in maintenance mode, and only attach the new profile to the cluster at the end. The same method gets you to Performance and back if you add one hop. This post is that procedure, done twice (once in the UI, once with the API), with the error you hit when you skip the hop and the ping loss I measured along the way.

*November 2026 · NSX 4.2.4.1 · vCenter 8.0 U3 · nested ESX 8.0 U3i · names and addresses are from my lab*

## The short version

- NSX has three host switch modes: `STANDARD`, `ENS_INTERRUPT` (EDP Standard) and `ENS` (EDP Performance).
- `STANDARD` ⇄ `ENS` in one step is rejected with error 9544, both at cluster level and per host.
- `STANDARD` → `ENS_INTERRUPT` → `ENS` is accepted, one host at a time, about 10 seconds per hop. Removal is the mirror image.
- Do it with the host in maintenance mode and the workloads never notice the datapath change. The only loss I saw came from the vMotions that emptied each host: 0.2 to 0.6 seconds per move.
- Once every host is converted, attach the matching Transport Node Profile to the cluster. NSX has nothing left to change and goes straight to `SUCCESS`.

![The three NSX host switch modes. STANDARD to ENS_INTERRUPT and back is accepted; ENS_INTERRUPT to ENS and back is accepted; STANDARD to ENS directly, in either direction, is rejected with error 9544.](/images/edp-perf/fig-01-mode-transitions.svg)

## The lab

Three nested ESX 8.0 U3i hosts on a Dell R730xd, a dedicated vCenter, one NSX Manager, and two small Ubuntu VMs on an overlay segment that ping each other five times a second the whole time. A nested lab tells you nothing about EDP throughput. I wanted to know what each step of the change costs, and it shows that exactly.

![Lab layout: vCenter vc-edp, NSX Manager nsx-edp and three nested hosts esx-edp-01 to 03, each with edp-vds on vmnic1 and vmnic2 for NSX, a separate vMotion VDS on vmnic3 and management on vmnic0.](/images/edp-perf/fig-03-lab.svg)

The vMotion network sits on its own VDS on purpose. EDP changes the whole host switch, and I did not want vMotion living on the switch I was changing.

## Three modes, three names

The UI, the API and the ESX shell each call the same thing something different, which does not help.

| UI (Advanced Configuration › Mode) | API `host_switch_mode` | What changes on the host |
|---|---|---|
| Standard | `STANDARD` | Normal datapath, vendor driver (`nvmxnet3` here) |
| Enhanced Datapath – Standard | `ENS_INTERRUPT` | ENS driver (`nvmxnet3_ens`), interrupt driven, no dedicated cores |
| Enhanced Datapath – Performance | `ENS` | ENS driver, polling on dedicated logical cores; `cpu_config` is mandatory |

`cpu_config` is a list of `{numa_node_index, num_lcores}` pairs. Those cores are taken away from VMs and spin for the datapath, so size it on real hardware with care. My nested hosts have one NUMA node and I gave EDP two cores.

## What does not work

My first try was the obvious one: edit the cluster's Transport Node Profile, set the mode to ENS, save.

```
ERROR 9544 Cannot update HostSwitch 50 02 2a 0a ... Mode STANDARD to new Mode ENS.
Please delete the HostSwitch and create it back with intended Mode.
```

Pointing the cluster at a second profile that already has `ENS` set gives the same answer, this time per host in the collection state (`FAILED_TO_REALIZE`, "3 hosts in the cluster are in error state"). So does a per-host API call that goes straight to `ENS`.

![NSX transport node collection state after swapping the cluster to an ENS profile: FAILED_TO_REALIZE, error 9544 on every host.](/images/edp-perf/cli-01-error-9544.webp)

NSX tells you what it would accept: remove the host switch and recreate it. There are three ways to get there.

| Way | What the VMs see | When it makes sense |
|---|---|---|
| Remove NSX from the cluster, re-prepare with the new profile | Every VM on every host of the cluster loses its overlay segments until the hosts are prepared again (close to 8 minutes for three hosts in my lab: about 1.5 minutes to uninstall the VIBs, 6 to prepare the hosts again) | Empty cluster, greenfield, never on a live one |
| Flip the mode in the cluster profile (where NSX allows it, e.g. Standard → EDP Standard) | The vSwitch traffic of all hosts is disrupted at the same time; per vcfcore.tech, for a few seconds up to many seconds, and connections may not come back on their own | Lab only |
| One host at a time, in maintenance mode, through EDP Standard | Nothing during the mode change itself. Only the vMotions that empty each host, as on any host patching day | Production |

I tried the first one before reading Gabe's post. It works, but on a cluster with workloads you never want it. The rest of this post is the third.

## Before you start

1. The NICs must be ENS capable. On every host, `esxcfg-nics -e` must show `ENS Capable = True` for the uplinks of the NSX switch. vmxnet3 is, which is why this works nested; on hardware check the Broadcom Compatibility Guide for the exact driver and firmware.
2. vCenter has to allow vMotion between hosts in different modes, because during the rollout some hosts are on one mode and some on the other. Set the vCenter advanced setting `config.vpxd.network.allowVmotionBetweenENSModes = true` (vCenter › Configure › Advanced Settings, or `govc option.set config.vpxd.network.allowVmotionBetweenENSModes true`).
3. Clone the Transport Node Profile: same host switch, same uplink profile, same IP pool, only the mode differs, plus `cpu_config` for Performance. Don't attach it yet. Mine are `edp-tnp` (Standard) and `edp-tnp-ens` (Performance).
4. You need room to empty one host at a time, with DRS fully automated or by manual vMotion. The host has to be empty before you touch it.

![Per-host workflow: prepare once, then for each host evacuate, enter maintenance mode, hop 1 to ENS_INTERRUPT, hop 2 to ENS (or STANDARD), verify and exit maintenance mode; when every host is done, attach the matching TNP to the cluster.](/images/edp-perf/fig-02-per-host-workflow.svg)

## Enabling EDP Performance from the UI

I did esx-edp-01 entirely in the NSX UI. The host was already in maintenance mode with its VM moved away.

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

Hosts 2 and 3 went through the API. The UI wizard writes the same object, the policy *host transport node*, so the API path is: read it, change the mode, write it back, wait, and do it a second time.

Find the transport node of the host:

```bash
NSX=nsx-edp.corp.local
EP=/policy/api/v1/infra/sites/default/enforcement-points/default
curl -sk -u admin "https://$NSX$EP/host-transport-nodes" \
  | jq -r '.results[] | [.id, .display_name, .host_switch_spec.host_switches[0].host_switch_mode] | @tsv'
```

Hop 1, `ENS_INTERRUPT`. Keep the whole object, change only the mode, PUT it back (the `_revision` you read has to go back with it):

```bash
TN=esx-edp-02-808e47a5-f31f-4824-a52f-83ac9e597257host-16
curl -sk -u admin "https://$NSX$EP/host-transport-nodes/$TN" > tn.json
jq '.host_switch_spec.host_switches[].host_switch_mode = "ENS_INTERRUPT"
  | .host_switch_spec.host_switches[].cpu_config = []' tn.json > tn-hop1.json
curl -sk -u admin -X PUT -H 'Content-Type: application/json' \
  "https://$NSX$EP/host-transport-nodes/$TN" -d @tn-hop1.json
```

Wait until the node is realized:

```bash
curl -sk -u admin "https://$NSX$EP/host-transport-nodes/$TN/state" \
  | jq -r '.state, .node_deployment_state.state'      # success / success
```

Hop 2, `ENS` with its cores. Read the node again first: the revision moved.

```bash
curl -sk -u admin "https://$NSX$EP/host-transport-nodes/$TN" \
  | jq '.host_switch_spec.host_switches[].host_switch_mode = "ENS"
      | .host_switch_spec.host_switches[].cpu_config = [{"numa_node_index":0,"num_lcores":2}]' > tn-hop2.json
curl -sk -u admin -X PUT -H 'Content-Type: application/json' \
  "https://$NSX$EP/host-transport-nodes/$TN" -d @tn-hop2.json
```

Maintenance mode is a vCenter job, not an NSX one. `govc host.maintenance.enter esx-edp-02.corp.local` before hop 1, `govc host.maintenance.exit` after hop 2.

When all hosts are done, attach the profile to the cluster by updating the transport node collection:

```bash
curl -sk -u admin "https://$NSX$EP/transport-node-collections/edp-cluster-tnc" \
  | jq '.transport_node_profile_id = "/infra/host-transport-node-profiles/edp-tnp-ens"' > tnc.json
curl -sk -u admin -X PUT -H 'Content-Type: application/json' \
  "https://$NSX$EP/transport-node-collections/edp-cluster-tnc" -d @tnc.json
curl -sk -u admin "https://$NSX$EP/transport-node-collections/edp-cluster-tnc/state" | jq -r .state   # SUCCESS
```

I wrapped the whole loop (evacuate, maintenance mode, both hops, wait, exit, next host, attach the profile) in a small Python script that uses pyVmomi for vCenter and the NSX policy API: [edp_per_host.py](/files/edp-perf/edp_per_host.py). It works out the hops by itself and skips hosts that are already in the target mode, which is how it picked up after the host I had done by hand:

![Script output: esx-edp-01 already ENS and skipped; esx-edp-02 and 03 each get a vMotion, maintenance mode, ENS_INTERRUPT realized in 10 s, ENS realized in 10 s, exit maintenance mode; then the TNP edp-tnp-ens is attached and the cluster state is SUCCESS.](/images/edp-perf/cli-03-api-install.webp)

The two hosts, vMotions included, took a minute and a half.

## Removing it: back to Standard

Same road, the other way: `ENS` → `ENS_INTERRUPT` → `STANDARD`, host by host, in maintenance mode, then attach the Standard profile. Going straight from `ENS` to `STANDARD` hits the same error 9544.

**From the UI** (esx-edp-02): Configure NSX on the host, Edit the host switch, Advanced Configuration. From Performance, the list offers EDP Standard and Performance only, no Standard.

![Mode dropdown when the host is in Performance: only Enhanced Datapath – Standard and Enhanced Datapath – Performance are offered.](/images/edp-perf/gui-08-modes-from-performance.webp)

Pick **Enhanced Datapath – Standard**, Finish. Run Configure NSX again and pick **Standard**, Finish. The ENS switch is gone and the uplinks are back on the regular driver:

![After removal: nsxdp-cli reports No ENS switch configured and esxcfg-nics -e shows every vmnic on nvmxnet3 with ENS Driven False.](/images/edp-perf/cli-04-host-standard-verified.webp)

**From the API**: the same two PUTs with `ENS_INTERRUPT` and then `STANDARD`, `cpu_config` back to `[]`. Or the script with `--mode STANDARD`:

![Script output for the removal: esx-edp-01 and esx-edp-03 each evacuated, put in maintenance mode, ENS to ENS_INTERRUPT then to STANDARD in about 10 s per hop, and taken out of maintenance mode; esx-edp-02 skipped because it was already Standard.](/images/edp-perf/cli-05-api-remove.webp)

**Last step, cluster level**: select the cluster, Configure NSX, choose the Standard profile, Save.

![NSX Installation dialog on the cluster with Transport Node Profile edp-tnp selected.](/images/edp-perf/gui-10-cluster-apply-edp-tnp.webp)

![edp-cluster back on Applied Profile edp-tnp with every host's NSX Configuration Success.](/images/edp-perf/gui-11-cluster-back-to-standard.webp)

One of my hosts showed *Degraded* right after the removal, with NSX reporting `uplink2` down while ESX reported both links up. That was a cached pNIC status left over from the driver swap: asking for the real-time status (`GET /api/v1/transport-nodes/<id>/status?source=realtime`) came back `UP`, and the UI caught up a moment later. Check that before you open a ticket.

## What it cost

`edp-vm1` pinged `edp-vm2` over the overlay every 200 ms for the whole test, the VMs following the evacuations from host to host.

![Ping summary: install window 2880 probes, 4 lost (0.14%), three short gaps; removal window 2305 probes, 8 lost (0.35%), four short gaps.](/images/edp-perf/cli-06-ping-impact.webp)

| | Probes | Lost | Longest gap |
|---|---|---|---|
| Install (Standard → Performance, 3 hosts) | 2,880 | 4 (0.14 %) | 0.4 s |
| Removal (Performance → Standard, 3 hosts) | 2,305 | 8 (0.35 %) | 0.6 s |

Each gap lines up to the second with the switchover of a vMotion that was emptying a host. None of them lines up with a mode change, because when the mode changed there was nothing left on the host to disrupt. The vMotions in my lab are storage vMotions between local datastores of nested hosts, so on real hardware with shared storage expect shorter blips than these.

## Before you try it on real hosts

There is no direct `STANDARD` ⇄ `ENS` change in NSX 4.2.4. The profile refuses it, the per-host API refuses it, and the UI does not even list it, so plan for two realizations per host. Each hop takes about ten seconds, and in maintenance mode those ten seconds are harmless, because no VM should be on the host while its datapath changes.

Clone the Transport Node Profile instead of editing the one attached to the cluster, because editing the attached profile is the all-hosts-at-once path. Attach the clone only once every host already matches it. Until then, *Mismatch* on the converted hosts is normal, and so is a stale *Degraded* right after the driver changes.

Performance mode's lcores are real cores: `cpu_config` takes logical cores away from VMs on every NUMA node you list, so size it against the traffic you actually expect. A nested lab is fine for rehearsing the procedure and useless for performance numbers, so if you want to know what EDP Performance buys you, measure it on the hardware and drivers you will run it on.

Credit where it is due: the per-host, maintenance-mode method comes from Gabe Rosas' [How to Enable EDP Standard with Zero Impact in VCF 5.2](https://vcfcore.tech/2025/11/25/how-to-enable-edp-standard-with-zero-impact/) and the [enable_uens.py follow-up](https://vcfcore.tech/2026/02/25/using-enable_uens-py-for-edp-transitions-in-vcf-5-2/). All I added was the second hop to Performance and the way back.
