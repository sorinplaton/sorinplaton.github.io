---
title: "NSX in VCF 9.1.2: standalone NSX is back, plus four smaller changes"
date: 2026-11-25
description: "VCF 9.1.2 is a maintenance release, but NSX 9.1.2 has one change that matters for design: you can once again install, upgrade and operate NSX on its own, without SDDC Manager or a VCF domain. I start with standalone NSX, then go through the four smaller changes: vDefend on vSphere Foundation, static TEP addresses in a transport node profile, a bigger subnet for Avi Service Engines, and ODS runbooks you can start from the UI. The last part covers what changed around NSX in VCF and the order to upgrade in."
summary: "NSX 9.1.2 brings back the standalone lifecycle that NSX 9.0 took away: install or upgrade from NSX 4.2.x without SDDC Manager, with VCF Operations kept only for licensing. It also gives vSphere Foundation a security-only NSX Manager for vDefend, static TEP addressing in transport node profiles, a larger subnet for Avi Service Engines on tier-1 Supervisors, and ODS runbooks you start from the NSX Manager UI. Seven figures, the integration changes in SDDC Manager, HCX and VCF Operations, and the upgrade and patch order."
tags: ["VCF 9.1", "NSX", "Standalone NSX", "vDefend", "vSphere Foundation", "Upgrade", "Avi", "Field notes"]
categories: ["Field notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-912/fig-01-standalone-vs-full-stack.svg"]
params:
  seoDescription: "What NSX 9.1.2 adds in VCF 9.1.2: standalone NSX, security-only NSX for vSphere Foundation, static TEPs, Avi SE subnet, ODS runbooks, and the upgrade order."
---

VCF 9.1.2 came out on 24 November 2026. It is a maintenance release and most of it is supportability work, but there is one NSX change you will want to know about before your next design. Since NSX 9.0, [a standalone NSX installation or upgrade was not supported](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/release-notes/vmware-cloud-foundation-90-release-notes/platform-product-support-notes/product-support-notes-nsx.html): NSX came with VCF or not at all. With NSX 9.1.2 the standalone lifecycle is back, and you can install, upgrade and run NSX on its own again.

I read the 9.1.2 release notes and the NSX guide in the VCF 9.1.x documentation with three questions in mind: what is new in NSX, what changed in the way NSX fits into VCF, and in which order you upgrade. Where the documentation is thin, I say so. Two of the drawings are my own sketches of things the docs only describe in words, and they are labelled that way.

## What NSX 9.1.2 ships

NSX 9.1.2.0 is build 25929667 in the VCF 9.1.2 bill of materials, and five things are new.

| Feature | What it does | Who it is for |
|---|---|---|
| Standalone NSX lifecycle | Install, upgrade and operate NSX from NSX Manager, with no SDDC Manager and no VCF domain | NSX 4.2.x estates outside VCF, and VVF customers who buy vDefend |
| NSX entitlement in VVF | vSphere Foundation can run a security-only NSX Manager for vDefend, without overlay networking | vSphere Foundation customers who add vDefend |
| Static host TEP IP and MAC | A transport node profile can hand out static TEP addresses without ARP conflicts or false override detection | Clusters that need fixed, documented TEP addresses |
| Avi SE data network CIDR | On a Supervisor with NSX tier-1, Avi Service Engines get their own subnet and can grow past the old /28 | Tier-1 Supervisors that need more Service Engines |
| ODS runbooks in the UI | Start diagnostic runbooks from NSX Manager, watch them run, download what they collect | Anyone who troubleshoots NSX without API tooling |

The first two change where NSX can run at all. The other three are smaller and make day-to-day work easier, and I take them in that order.

## 1. Standalone NSX

In full-stack VCF, NSX is one component of the fleet: VCF Operations drives its lifecycle, SDDC Manager owns the domains, and NSX shows up through the VCF bill of materials. Standalone NSX takes SDDC Manager and the domains out of the picture. You still need vCenter and ESX hosts, and you still need VCF Operations and a license server, but only to supply the licence entitlement (more on that under Licensing below).

![Two dashed zones side by side. On the left, full-stack VCF 9.1.2: VCF Operations, SDDC Manager, vCenter, the NSX Manager cluster and ESX transport nodes stacked top to bottom, with NSX delivered through the VCF bill of materials. On the right, standalone NSX 9.1.2: a license server and VCF Operations joined to vCenter by a dashed entitlement line, then vCenter registered as compute manager, NSX Manager handling upgrades, backup, certificates, passwords and authentication, and ESX transport nodes at 9.1.2 for a new install or 8.0 U3 or later when upgrading. A footer lists the limits.](/images/nsx-912/fig-01-standalone-vs-full-stack.svg)

Everything else in the lifecycle runs from the NSX Manager UI or the NSX API: upgrades, patching, backup and restore, inventory, certificates, passwords, authentication, monitoring and logs. Local Manager and Global Manager are both supported, so NSX Federation works, and stateless hosts work too.

### A new install

Before you start you need a vSphere distributed switch, DNS records for the managers, and an MTU of 1700 or more on the VDS (9000 is recommended). Then:

1. Install the first NSX Manager node and log in.
2. Activate the vCenter and NSX licence entitlements.
3. Register vCenter as a compute manager.
4. Optionally deploy two more nodes to form the three-node cluster.
5. Create transport zones, and an NSX Edge cluster if you need one.
6. Prepare the ESX hosts as transport nodes with a transport node profile.
7. Add a tier-0 gateway if you want routing.

Deploy the new NSX Manager itself on ESX 9.1.2 or later.

### An upgrade from NSX 4.2.x

If you already run NSX outside VCF, start here. Your source has to be NSX 4.2.0 or later, with vCenter and ESX on 8.0 Update 3 or later. The NSX Upgrade Coordinator drives most of it, in this order: pre-checks with the `.pub` bundle, the `.mub` upgrade bundle, the management plane, vCenter (upgraded with vSphere), the hosts, the edges, finalize, and the licence activation last.

For the hosts you have two options:

| Option | What happens | What stays |
|---|---|---|
| NSX VIBs only | The Upgrade Coordinator upgrades the NSX VIBs on the hosts | vCenter and ESX stay on 8.x |
| Full ESX upgrade | You upgrade vCenter and ESX to 9.1.2, and the NSX VIBs come with the ESX upgrade | Nothing stays on 8.x |

The first option gets you to NSX 9.1.2 while the vSphere 8 hosts stay where they are, which gives you time to plan the rest.

### Licensing

Since VCF 9 there is no NSX licence key to type anymore. The license server holds the entitlements, VCF Operations assigns a primary licence to vCenter, and NSX picks up its entitlement from that vCenter. Standalone NSX works the same way, and that is why you still need VCF Operations and a license server in the checklist above.

A new install or an upgrade starts a 90-day evaluation, and once it runs out, enforcement restricts access to NSX features. Put the entitlement activation in the rollout plan.

### The limits

- Multi NSX Manager with a single vCenter is not supported in standalone mode, and as far as the guide goes, that is the only feature you lose.
- The standalone upgrade is not for an NSX that is already part of a full-stack VCF deployment; that one upgrades with the rest of VCF.
- You can converge standalone NSX into full-stack VCF later, but the converge creates a VCF management domain and there is no way back.

## 2. A security-only NSX for vSphere Foundation

The feature is "NSX Entitlement in VVF": you can deploy NSX and vDefend directly in a vSphere Foundation environment and bring network security and threat protection to VVF workloads.

Know the limits before you promise anything. In 9.1.2 and later, VVF runs a pared-down NSX Manager restricted to a security-only configuration, and overlay networking is not available in this deployment model. In other words, this is vDefend for workloads on VLAN-backed networks, with no VPCs and no overlay segments.

![A dashed zone labelled vSphere Foundation. Inside, vCenter as compute manager and a pared-down, security-only NSX Manager sit above a thick bar labelled VLAN-backed workload networks, with five workload VMs hanging from the bar and protected by vDefend. A dashed line from NSX Manager to the bar is labelled vDefend policy. To the right, a greyed box says overlay networking is not in this model, and a second box says VCF Installer does not install NSX for a VVF deployment and that standalone NSX supports the NSX platform for vDefend virtual patching. A footer says the drawing is a sketch because the guide gives no topology.](/images/nsx-912/fig-02-vvf-security-only.svg)

What you won't find is one place that says how NSX gets into a VVF environment. The deployment guide says VCF Installer does not require or install NSX for a VVF-model deployment, and the release notes say standalone NSX supports the NSX platform for vDefend virtual patching, a feature for customers who purchase VVF and vDefend. Read the two together and my reading is that NSX for VVF comes in through the standalone install described above.

Don't look for the step-by-step activation in the 9.1.x documentation set either: the NSX guide only points to a topic called *Activating VMware vDefend with VMware vSphere Foundation (VVF) Deployment*. And one more thing before you size anything: License Hub is a mandatory component when you use the vDefend add-on, according to the VCF design guide.

## 3. Static TEP IP and MAC in a transport node profile

Two terms first. A TEP, or tunnel endpoint, is the VMkernel adapter on each host that carries Geneve overlay traffic, and a transport node profile (TNP) is the template NSX applies to every host in a cluster, TEP addressing included. If you want the full map of TEP pools and the other address pools in VCF 9.1, I drew it in [an earlier post](/posts/vcf9-ip-pools-explained/).

![On the left, a transport node profile symbol connected to a thick bar labelled Host TEP VLAN, with three ESX hosts below it, each with a TEP vmk that has a fixed IP and MAC. On the right, four rows show how a host TEP gets its address: DHCP and IP pool, both usable in a TNP or on a single host; static IP list, single host only per KB 431877; and, highlighted in green, static IP and MAC in the TNP, new in 9.1.2, without ARP conflicts or false override detection in vSphere configuration profiles.](/images/nsx-912/fig-03-static-tep.svg)

DHCP and IP pools work in a TNP or on a single host. A static IP list, until now, was per host only: per [KB 431877](https://knowledge.broadcom.com/external/article/431877/migration-of-host-transport-node-vtep-in.html), it is only available when you configure a single host. NSX 9.1.2 adds a fourth way: you can now allocate static IP and MAC addresses for host TEPs in a transport node profile, without causing IP (ARP) conflicts or false override detection in vSphere configuration profiles.

That is as far as the release notes go. You won't find the UI fields or the API properties in them, so check the NSX 9.1.2 API reference before you automate it.

### Where vSphere configuration profiles come in

Since vSphere configuration profiles are part of that sentence, it helps to know how they and NSX share a cluster. vSphere Configuration Profiles (VCP) keep every host of a cluster on one desired configuration, in four stages: validation, compliance check, impact, remediation. Since VCF 9.1, VCP also manages the overlay TEP vmk from vCenter, with a service type called vNetworking.

Before you prepare a VCP cluster for NSX, run these two commands on every host in it:

```bash
esxcli network firewall ruleset set -e true -r DVFilter
esxcli system syslog config set --remote-host-max-msg-len 4096
```

Then the workflow goes like this:

1. Create a VDS host switch.
2. Enable VCP on the cluster.
3. Under *Desired State › Configuration › Draft*, create or import a draft.
4. Add the keys `esx/network/vmknics`, `esx/network/net_stacks`, `esx/network_vds/switches` and `nsx/fabric/transport_node_config` or `transport_node_profile`.
5. Run the pre-check, apply, then check compliance.

From then on, NSX Manager shows a VCP capsule on the cluster under *System › Fabric › Hosts › Clusters* and blocks create, update and uninstall there. To attach a new TNP, you create it in NSX and then update the cluster draft in vCenter.

VCP compares each host with the cluster document, so a static TEP address that differs from host to host could look like drift. I think that is the false override detection 9.1.2 removes, but the mechanism isn't explained anywhere, so take it as my reading.

## 4. A dedicated subnet for Avi Service Engines

This one is for Kubernetes load balancing. On a Supervisor with NSX tier-1 networking, NCP (the NSX Container Plugin) builds the segments and gateways, and when Avi does the load balancing, its Service Engines need a data network. That network was capped at a /28, so 16 addresses, which Broadcom itself calls too restrictive.

![Two dashed zones. On the left, before 9.1.2: an NSX tier-1 gateway above a short bar labelled Avi SE data network, /28, with three Service Engines and the caption 16 addresses in a /28. On the right, NSX 9.1.2: an NCPSetting CRD carrying the SE data subnet CIDR points to the tier-1 gateway, above a long bar labelled Avi SE data network, your CIDR, with six Service Engines and the caption room to scale Service Engines beyond the /28 limit. A footer says the subnet maps to the Services subnet of the VPC service profile after a move to NSX VPC, and that the drawing is a sketch.](/images/nsx-912/fig-04-avi-se-subnet.svg)

In 9.1.2 you pick the Service Engine subnet CIDR yourself. It goes in the NCPSetting custom resource, the same CRD you already use for NCP settings. If the Supervisor later moves from NSX tier-1 to NSX VPC networking, the subnet maps to the existing Services subnet in the VPC service profile.

Keep in mind that since 9.1 the vSphere Client no longer deploys a Supervisor with classic NSX segment networking; only the API still does (see the [Supervisor release notes for 9.1](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-service-administration-and-development/9-1/release-notes/vmware-vsphere-supervisor-release-notes.html)). So in practice you will mostly set this on Supervisors you already run. There is no sample NCPSetting in the release notes, so take the field name from the NCP documentation.

## 5. ODS runbooks in the NSX Manager UI

Anyone who has debugged NSX by hand knows why the Online Diagnostic System (ODS) exists. It is mostly manual work (log analysis and commands run with root privilege), it means a lot of back-and-forth with support, you repeat the same workflow for every issue of the same kind, and it takes specialist knowledge.

ODS packages those workflows as runbooks. A runbook runs inside the NSX node, does health checks, data collection and root cause analysis, and hands back a report, sometimes with artifacts such as packet captures. Predefined runbooks ship with NSX; dynamic runbooks are packages Broadcom has tested and certified for targeted issues.

Until now you could only invoke runbooks through the API. From 9.1.2 you can start them from the UI as well.

![Five boxes in a row joined by arrows. One, an alarm fires, one that is mapped to a runbook. Two, NSX recommends the KB article and the runbook to run. Three, you invoke it under Plan & Troubleshoot, ODS Runbooks. Four, it runs in the node: NSX Edge, ESX host or NSX Manager. Five, you get a report through View Details, plus artifacts to download. A footer lists the predefined runbooks available in the UI and the two dynamic runbooks.](/images/nsx-912/fig-05-ods-runbook-flow.svg)

Here is the click-path, starting from the runbook list:

1. Log in to NSX Manager as a local admin user, at `https://<nsx-manager>/login.jsp?local=true`.
2. Go to *Plan & Troubleshoot › ODS Runbooks*.
3. Open the runbook's three-dot menu and click *Invoke Runbook*.
4. Fill in the inputs; the information icon explains each one.
5. Click *Invoke Runbook*, then *View Details* when it completes.

Earlier runs stay in the *Runbook Invocation* tab. To add a dynamic runbook, use *Import Runbook*, pick the file, set the target nodes and import it.

Seven of the predefined runbooks you'll find in the UI, and what they diagnose:

| Runbook | What it diagnoses |
|---|---|
| EdgeHealth | Edge down during deployments and upgrades; suggests workarounds |
| BgpNeighborState | Why a BGP neighbor is down, with optional BGP and BFD capture |
| EdgeDpBfd | Edge BFD issues; also triggered when a BFD session goes down |
| OverlayTunnel | An overlay tunnel that is missing or down between two VTEPs |
| IPSecVPNSession | VPN session failures: proposals, authentication, traffic selectors |
| AdfCollect | ESX datapath performance data |
| CorfuServer | Corfu layout stability, disk IO, compactor health |

LBScale and CcpTNConnection are in the UI as well, and you can launch CorfuServer and CcpTNConnection straight from an active alarm with the *Invoke Runbook* link. Not every predefined runbook made it into the UI, though; the rest still go through the API.

## API changes

If you automate NSX, there are 23 new NSX operations: 13 in the Policy API, 5 in the Manager API and 5 in the Autonomous Edge API. No operation is deprecated or deleted.

- **ODS (Policy API):** list runbooks, search invocations, filter arguments, query dynamic runbook packages.
- **Tier-0 gateway firewall DNS resolver (Policy API):** `dns-resolver-config`, `dns-resolver-status` and `dns-resolved-ips` under `/infra/tier-0s/{id}/firewall/`.
- **fqdn-resolver service (Manager and Autonomous Edge APIs):** status, start, stop and restart under `/node/services/fqdn-resolver`.
- **IDS signature data:** CVSS v3 scores and version requirements on signatures, MITRE tactic and technique IDs.

One rename to check your scripts for: a structure property went from `datapth_tx_pkts` to `datapath_tx_pkts` in `HostScrxDebugStats`.

## What changed around NSX in VCF

Outside NSX itself, SDDC Manager, HCX and VCF Operations each changed something that touches NSX. Here they are next to the two changes covered above.

| Area | Before 9.1.2 | In 9.1.2 |
|---|---|---|
| NSX lifecycle | Only through the VCF bill of materials | Standalone NSX restored, with a one-way converge into full-stack VCF |
| vSphere Foundation | No NSX entitlement | Security-only NSX Manager for vDefend, no overlay |
| Converge and import | Hosts with several management or vMotion vmks, or a vCenter on another VLAN, not supported | Both supported |
| NSX shared across domains | VCF Operations showed its certificates under one domain | Fixed: shown under every domain that shares it |
| HCX network extension | DVPG to DVPG extension needed NSX transport nodes at the destination | Not needed, with a DVPG-backed network profile |
| VCF Operations for networks | No standalone platform instances | Extra instances with their own Day 0 and Day 2 lifecycle |

You have already read about the first two rows, and the certificate row is only a fix in how VCF Operations displays a shared NSX's certificates. The converge, HCX and VCF Operations for networks rows need a bit more explanation:

- Converge with several vmk adapters: hosts can have several management and vMotion VMkernel adapters, IPv4 and IPv6, as long as each host has at least one of each.
- Converge a vCenter on another VLAN: an existing vCenter can sit on a management VLAN other than the VCF management VLAN, on a distributed port group for that VLAN.
- HCX without NSX transport nodes: create a DVPG-backed network profile and assign it to the HCX Intrasite Control network when you build the compute profile at the destination.
- VCF Operations for networks: you manage the extra platform instances in their own UI and API, outside the VCF lifecycle.

## Upgrade order

A VCF 9.0.x instance going to 9.1.x has a strict order, and the figure follows it; a 5.2.x instance has its own table, which is close to it. Don't improvise with the sequence: get it wrong and you get errors, licences that cannot be assigned to vCenter for example.

![Seven boxes in a row joined by arrows. One, fleet: VCF Operations first, then SDDC Manager, management services, Automation, HCX. Two, Global Managers, Federation only: standby cluster then active. Three, NSX Manager, upgraded with vCenter as one step of the domain upgrade plan. Four, VKS 3.6.0 or later, then the Supervisor. Five, ESX, vLCM images only, where the NSX VIBs are upgraded. Six, NSX Edge clusters, then NSX finalize. Seven, clean-up: VDS version, Tools, VM compatibility, vSAN disk format. Boxes two, three and six are outlined as NSX management and control plane steps, and box five as the NSX data plane on the hosts.](/images/nsx-912/fig-06-upgrade-order.svg)

Within that order, NSX comes up at three points, or four if you run Federation:

- **Global Managers, before anything else in NSX.** VCF doesn't manage their lifecycle, so you download the `.pub` and `.mub` bundles yourself and upgrade the Upgrade Coordinator, then the standby cluster, then the active one.
- **NSX Manager, together with vCenter.** The two upgrade as a single step (next section).
- **ESX.** Since VCF 9 the NSX VIBs are upgraded in the ESX step, so hosts no longer go into maintenance mode while NSX Manager upgrades. ESX can only go after NSX Manager and vCenter.
- **NSX Edge clusters, then finalize.** Until finalize is done you cannot configure the new NSX features. Give auto-reconciliation about 15 minutes before you schedule the next upgrade.

Before you start, back up SDDC Manager and every vCenter and NSX Manager, make sure no password or certificate has expired, and run the prechecks in VCF Operations. The forward-only rule applies too: the target must have been released after the version you are on.

### vCenter and NSX Manager are one step

Since VCF 9.1, the domain upgrade plan handles vCenter and NSX Manager as a single step. You build the plan in VCF Operations (*Build › Lifecycle › VCF Instances*, your domain, *Upgrades*, *Plan Domain Upgrade*) and pick a scenario.

![Two rows of numbered boxes. The optimized maintenance window row has five steps: prepare vCenter with no downtime, upgrade NSX Manager, switch over vCenter, upgrade ESX hosts, NSX Edge and finalize, with a green bar above steps two and three labelled one maintenance window. The sequential upgrade row has four steps, each in its own maintenance window: upgrade NSX Manager, upgrade vCenter in place or with prepare and switch over, upgrade ESX hosts, NSX Edge and finalize. A footer lists the constraints.](/images/nsx-912/fig-07-vcenter-nsx-step.svg)

The optimized scenario prepares the new vCenter first, with no downtime, then upgrades NSX Manager and switches vCenter over inside one maintenance window. The catch: the vCenter target version has to match the current SDDC Manager version. The sequential scenario gives each component its own window. If you come from 5.2.x, your only vCenter option is the reduced downtime upgrade.

### Patching 9.1.x to 9.1.2

If you are already on 9.1.0.x or 9.1.1.x, stay away from *Upgrade All* in VCF Operations until the 9.1.2 patch dependencies are met, and patch component by component instead, in this order:

1. Patch the VCF management services fleet lifecycle component first.
2. Patch VCF Operations on its own, with no other lifecycle operation running.
3. From 9.1.0.x only: patch the management services runtime before the identity broker or Salt RaaS it hosts, and VCF Automation before the migration service engine.
4. Then patch the other components, NSX included.

While a software depot is being patched, every other patch waits until it finishes, so plan for that.

Of the NSX resolved issues, these are the ones I would read first: VPC creation on the default transit gateway failing on a transport zone mismatch, a centralized transit gateway losing its transit subnet after an upgrade, the Manager upgrade stuck early on controller maintenance mode, and an edge routing deadlock with dynamic BGP neighbors.

And the known issues worth planning for:

- VM-based DFW groups can go stale for a few minutes during a rolling Manager upgrade. They resync on their own.
- Very large DFW IP sets: stay at 1.5 million realized IPs or fewer per host and 800,000 per VM filter (KB 441679).
- A local backup race can pause the Manager upgrade with "Failed to create Local backup". Just retry it.

## What I would do with this

- **Running NSX 4.2.x outside VCF?** You now have a path to 9.1.2 that doesn't start with SDDC Manager. Plan for VCF Operations and a license server anyway, and keep an eye on the 90-day evaluation clock.
- **Running vSphere Foundation and want vDefend?** You can have it now, with a security-only NSX Manager. Just don't expect overlay networking from it.
- **Already on full-stack VCF 9.1?** Then 9.1.2 is a patch for you. Follow the patch order, leave *Upgrade All* alone until the dependencies are met, and read the NSX known issues before the maintenance window.
- **Still on 5.2 or 9.0?** You can go straight to 9.1.2, as long as you keep the order: fleet, Global Managers, NSX Manager with vCenter, ESX, then edges and finalize.

Broadcom does update release notes after a release, so give them another read before you plan.

## Sources

- [VMware Cloud Foundation 9.1.2.0 Release Notes](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/release-notes/vmware-cloud-foundation-9-1-2-0-release-notes.html): Getting to 9.1.2, and the NSX 9.1.2.0, SDDC Manager 9.1.2.0 and API changelog sections
- [VCF Operations 9.1.2.0 Release Notes](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/release-notes/vmware-cloud-foundation-9-1-2-0-release-notes/vcf-operations-9-1-2-0-release-notes.html): HCX, VCF Operations for networks, shared NSX certificates
- [VMware Cloud Foundation 9.1.x documentation](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1.html): NSX guide (Standalone NSX, transport node profiles, ODS runbooks, vDefend), Deployment, Convergence and Upgrade guide
- [NSX product support notes, VCF 9.0](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/release-notes/vmware-cloud-foundation-90-release-notes/platform-product-support-notes/product-support-notes-nsx.html): standalone installation or upgrade not supported from NSX 9.0
- [VMware vSphere Supervisor Release Notes, 9.1](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-service-administration-and-development/9-1/release-notes/vmware-vsphere-supervisor-release-notes.html): Supervisor with classic NSX segment networking, API only
- [KB 431877](https://knowledge.broadcom.com/external/article/431877/migration-of-host-transport-node-vtep-in.html): host TEP IP assignment options, static IP list on a single host
