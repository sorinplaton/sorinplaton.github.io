---
title: "VCF 9.1: fleet components on an NSX overlay segment, and DR beyond stretched-cluster latency"
date: 2026-10-30
description: "A greenfield VCF 9.1 bring-up can't put VCF Operations and VCF Automation on an NSX overlay segment directly: you defer them, build the NSX landing zone, then finish from the VCF Installer. Here is that sequence, then the design it prepares for — fleet DR with Protection and Recovery (SRM and vSphere Replication) over a segment stretched with NSX Federation, when the sites are too far apart for a stretched cluster (more than 5 ms RTT): what gets replicated, what gets restored from backup, the Continuous Availability option under 10 ms, and why the network has to be recovered before any VM powers on."
summary: "The deferred-components path is the only supported way to land VCF Operations and VCF Automation on an NSX overlay segment in a greenfield VCF 9.1 deployment. The reason to want it is fleet DR: stretch the segment with NSX Federation, replicate VCF Operations with Protection and Recovery, restore VCF Automation and the fleet services from backup. Past the 5 ms stretched-cluster limit this is a two-instance DR design, not a stretched cluster — with Ops Continuous Availability as an option if the DCI stays under 10 ms. Topology, latency budget, failover runbook, seven diagrams."
tags: ["VCF 9.1", "NSX", "NSX Federation", "VCF Operations", "VCF Automation", "Disaster recovery", "SRM", "Protection and Recovery", "Design"]
categories: ["Field notes"]
showToc: true
tocOpen: false
images: ["/images/fleet-overlay-dr/fig-02-dr-topology.svg"]
---

Two questions tend to show up together. The first: how do I get VCF Operations and VCF Automation onto an NSX overlay segment in a greenfield VCF 9.1 deployment? The second, usually a few minutes later: and how do I fail them over to the second data centre, given that the sites are further apart than a stretched cluster allows — more than 5 ms RTT, typically 10 ms or more over a regional DCI — and we already own SRM?

They are really one question. An overlay segment for the fleet components only earns its extra edge cluster if you intend to stretch it, and you stretch it for one reason: IP mobility for disaster recovery. So this post takes both halves in order — the greenfield sequence first, then the DR design it sets up: what gets replicated, what gets rebuilt from backup, where the inter-site latency constrains you and where it doesn't, and the order of operations on the day.

*October 2026 · VCF 9.1.1 documentation and design library, checked in the Holodeck lab · "Protection and Recovery" is the 9.1 name of the VMware Live Recovery appliance (Site Recovery Manager plus vSphere Replication); I write SRM when I mean the orchestration function.*

## Part 1 — Greenfield: landing the fleet components on an overlay segment

### Bring-up won't do it for you

Out of the box, the VCF Installer puts every VCF Operations and VCF Automation node on the same distributed port group as vCenter — the VM management VLAN. There is no field in the bring-up wizard for an NSX segment, and there can't be: at that point there is no edge cluster, no Tier-0 and no overlay segment yet.

What 9.1 gives you instead is an option: **Deploy VCF Operations and VCF Automation to a specific vSphere distributed switch or NSX segment**. Tick it and the Installer skips VCF Operations, the cloud proxy, the license server and VCF Automation, and finishes bring-up with SDDC Manager, vCenter, the NSX Manager cluster and the management domain. You only get this for a *new VCF fleet*; it isn't offered when you add an instance to an existing fleet or connect to an existing VCF Operations.

![Three stages left to right: VCF Installer bring-up with the defer option; NSX in the management domain — edge cluster, Tier-0 with BGP, Tier-1, overlay segment, DNS and routing; VCF Installer deferred components onto the segment. A footer notes that adding Automation later from VCF Operations places it on the management network.](/images/fleet-overlay-dr/fig-01-greenfield-path.svg)

### The sequence

1. **Bring-up with the option ticked.** The rest of the wizard is the usual: deployment model, VM management network, VCF management network, host overlay. You end up with a management domain and no fleet components on it.

2. **Build the landing zone in NSX.** This is the design library's *VCF Management Dedicated VLAN and NSX Overlay Segment* model: an edge cluster in the management domain (SDDC Manager UI or API), a Tier-0 peering with the fabric over BGP and advertising connected segments, a Tier-1 attached to it, and an overlay segment on the management domain's overlay transport zone. The segment has to be reachable from the VM management VLAN — SDDC Manager, vCenter, the NSX Managers, the ESX hosts — and from DNS, NTP and your identity source. Create the A and PTR records for every node now; the Installer checks them.

3. **Deploy the deferred components from the Installer.** With the binaries in the Installer's depot and the instance deployment showing complete, go to *Deployment Wizard › Deploy deferred components*. It asks for the deployment model and scale, DNS and NTP, the overlay segment (you'll see it as the port group NSX created for it), and the FQDNs and IPs for the Operations nodes, the license server and Automation. You can post the same thing as a JSON spec to the Installer API, with the segment name in the network field.

One sizing detail for the Automation range: 9.1.0.0 through 9.1.0.300 want five *contiguous* IPs for the Automation nodes; 9.1.0.400 and later take five addresses, contiguous or not. Carve them out of the segment's subnet along with the Operations primary, replica and data nodes, the Automation FQDN and the license server.

### A placement decision you have to make early

Here is the trap. If you skip Automation at bring-up and add it later from VCF Operations (*Build › Lifecycle › VCF Management Components*), Operations drops the service-runtime cluster on the management network, and it doesn't ask. The only place you get to choose a vDS port group or an NSX segment is the Installer's deferred-components path. So decide the segment before anyone opens the Lifecycle page.

![A decision diagram: VCF Automation skipped at bring-up branches into two paths. Path A, the VCF Installer's Deploy deferred components, asks for a vDS port group or NSX segment and lands the Automation service runtime on the overlay segment, next to VCF Operations, with IP mobility for DR. Path B, VCF Operations Build › Lifecycle › VCF Management Components, offers no network choice and lands it on the per-site VM management VLAN. A footer notes the cloud proxy stays on the management VLAN in both cases and that brownfield VLAN-backed segments remain supported after an upgrade.](/images/fleet-overlay-dr/fig-06-placement-paths.svg)

Two more things while we're here. The cloud proxy stays on the VM management VLAN whatever you do — it's site-specific and collects locally, which is exactly what you want. And brownfield is a different story: KB 443842 confirms that Operations, Automation and the management runtime cluster on NSX *VLAN-backed* segments stay fully supported after an upgrade to 9.1, and nobody is asking you to migrate them. Greenfield is where the overlay model is prescribed.

## Part 2 — Why the segment: fleet DR between two instances

Now the reason you built all that. The design library's *Dedicated VLAN and NSX Stretched Overlay Segment* model takes the same segment and stretches it between two VCF instances with NSX Federation. The whole point is **IP mobility**: VCF Operations comes back at the recovery site with the addresses, FQDNs and certificates it had, so nothing downstream — adapters, cloud proxies, the SDDC Managers registered with the fleet — has to change.

### What the inter-site latency decides for you

Before the topology, the number. The round-trip time between the sites sorts every option into "stretched" or "two separate instances", and anything above 5 ms RTT — the vSAN stretched-cluster limit, which the VCF Operations cluster happens to share — puts you in the second group. The rest of this post assumes that: a regional DCI in the 10 ms range or more, with one interesting exception between 5 and 10 ms that I'll come to.

![A log-scale number line of round-trip time between sites, with a shaded band from 5 ms upward marking latency beyond the stretched-cluster limit. Red ceilings to the left: vSAN stretched cluster 5 ms, VCF Operations cluster nodes under 5 ms, NSX single-manager multisite with automatic management-plane recovery 10 ms. A teal window from 5 to 10 ms marks VCF Operations Continuous Availability between fault domains, with the witness ceiling at 30 ms. Green ceilings to the right: Operations nodes to endpoints under 50 ms, NSX Federation stretched networks 150 ms, Operations nodes to cloud proxy under 300 ms.](/images/fleet-overlay-dr/fig-04-latency-ladder.svg)

| Limit | Value | Source | What it means above 5 ms RTT |
|---|---|---|---|
| vSAN stretched cluster, site to site | ≤ 5 ms RTT | vSAN stretched cluster requirements | **Out.** No stretched management cluster, no vSphere HA across sites; two management domains. |
| VCF Operations analytics nodes (primary, replica, data) | < 5 ms RTT | VCF Operations 9.0 sizing guidelines, KB 397782 | **Out as a single cluster.** You can't spread the nodes across the sites as one fault domain; the cluster fails over as one protection group — unless Continuous Availability applies (next row). |
| VCF Operations Continuous Availability, between fault domains | < 10 ms RTT average, peaks ≤ 20 ms over 20 s; witness ↔ fault domains ≤ 30 ms | Continuous Availability VCF Operations model (design library), KB 444771 | **Only below 10 ms.** The Ops cluster can be split across the two instances instead of replicated — see the CA option further down. |
| NSX single-manager multisite with automatic management-plane recovery | ≤ 10 ms RTT | NSX multisite design | **Not relevant here**: the two-instance VCF design uses Federation, not one stretched NSX Manager cluster, and a regional DCI is usually at or past this limit anyway. |
| VCF Operations nodes → endpoints (vCenter, NSX, SDDC Manager) | < 50 ms RTT | KB 397782 | Fine. After a failover, Operations at site B can keep collecting from whatever is still alive at site A. |
| NSX Federation, stretched networks (Global Manager ↔ locations) | ≤ 150 ms RTT | NSX Federation requirements | Fine, with plenty of margin. |
| VCF Operations nodes → cloud proxies / collectors | < 300 ms RTT | VCF Operations 9.x sizing, KB 417347 | Fine. |
| vSphere Replication | no RTT ceiling; asynchronous | Protection and Recovery 9.1 | Fine. The RPO depends on bandwidth and change rate, not latency; the blueprint says 15 minutes. |

So this is disaster recovery, not high availability — with the one exception I mentioned: if the DCI stays under 10 ms, VCF Operations itself can run in Continuous Availability across the two instances. The base design is two VCF instances, each with its own management domain, SDDC Manager, vCenter and NSX Manager cluster, in one fleet; the fleet components live at site A and are recovered at site B. That is exactly what the 9.1 *VCF Fleet Disaster Recovery* blueprint describes, and it places the recovery site "typically outside the metro radius".

### The topology

![Two dashed zones, Site A protected instance and Site B recovery instance, with a DCI band between them labelled as more than 5 ms RTT. In each site, top to bottom: a fabric router, an edge cluster containing a stretched Tier-0 (primary at A, secondary at B) and a stretched DR-only Tier-1, one overlay segment drawn as a single bar across both sites carrying the Operations cluster, Ops for Networks, the Automation service runtime, the management services instance and the license server at A, and powered-off replica targets, a new Automation instance and the site-B management services instance at B. Below, a per-site VM management VLAN with SDDC Manager, vCenter, NSX Manager cluster, cloud proxy, the Protection and Recovery appliance and the Global Manager (active at A, standby at B). vSphere Replication crosses the DCI between the Operations nodes; Global Manager sync and site pairing cross it too; SFTP backup locations at both sites are linked.](/images/fleet-overlay-dr/fig-02-dr-topology.svg)

| Component | Where it lives | How it comes back (9.1 blueprint) | Needs IP mobility |
|---|---|---|---|
| VCF Operations cluster (primary, replica, data nodes) | stretched overlay segment, site A | Protection and Recovery: vSphere Replication and a recovery plan — or Continuous Availability across both sites when the DCI is under 10 ms (see below) | **Yes** |
| VCF Operations for Networks (platform; collectors are per site) | stretched overlay segment, site A | Protection and Recovery: replication and a recovery plan | **Yes** |
| VCF Automation (service-runtime cluster) | stretched overlay segment, site A | **Not replicated.** New instance at site B, latest backup restored | No |
| VCF management services components: Identity Broker, fleet lifecycle, software depot, Salt RaaS, log management | management services instance at each site | **Not replicated.** Each one restored onto site B's existing management services instance (one of each per management services instance) | No |
| Cloud proxies / collectors | VM management VLAN, one set per site | Not replicated; site B already has its own | No |
| License server | stretched overlay segment, site A | Not in the blueprint; treat it as a redeploy | No |
| SDDC Manager, vCenter, NSX Manager cluster (Local Manager) | VM management VLAN, per instance | Stay where they are; each instance keeps its own | No |
| NSX Global Manager | VM management VLAN; active at A, standby at B | You promote the standby | No |
| Protection and Recovery appliance | VM management VLAN, one per instance, Light deployment, management domain | Site-paired A ↔ B | No |

The row that surprises most people is Automation. In the 9.1 blueprint, VCF Automation is *not* protected by SRM. You deploy a new instance at the recovery site and restore a backup into it, and the blueprint says in so many words that this needs no IP mobility. Same for Identity Broker and the rest of the management services. The stretched segment is there for VCF Operations and Ops for Networks. I'd still put Automation on it — the new instance can come up with the same FQDN and, if you keep the addresses free at site B, the same IPs, which saves you a DNS change and a certificate — but that's a convenience, not what makes the recovery work.

### The NSX side, concretely

![NSX Federation objects for the stretched segment: an active Global Manager at site A and a standby at site B with a sync line; each site's Local Manager cluster; a ToR router per site peering eBGP with a stretched active/active Tier-0 (primary at A, secondary at B with AS-path prepend); RTEP Geneve tunnels between the two edge clusters; a stretched DR-only Tier-1 under each Tier-0; one segment bar across both sites. A thick green path marks all north–south traffic entering and leaving through site A; a dashed grey path at site B is dormant until Network Recovery. A footer lists the three steps after a site-A failure.](/images/fleet-overlay-dr/fig-07-nsx-federation.svg)

| Object | Where it is created | Settings that matter |
|---|---|---|
| Global Manager | Site A active, site B standby; both on the VM management VLAN | Manual promotion only; GM ↔ location ≤ 150 ms for stretched networks |
| Local Manager | The NSX Manager cluster VCF deployed in each instance | Registered as Location A / Location B on the Global Manager |
| Edge cluster | One per instance, in the management domain | Two nodes; RTEP interface on its own VLAN; RTEP MTU 1,700 preferred (1,500 minimum) across the DCI |
| Tier-0 | Global Manager, stretched | HA active/active; Location A primary, Location B secondary; eBGP to the fabric in both locations; route map with AS-path prepend ×2 on the secondary location's peers; advertises connected segments |
| Tier-1 | Global Manager, stretched | DR-only (no service router, no stateful services); same span as the Tier-0, so it follows its primary location. Add services only if you need the NSX load balancer in front of Operations |
| Segment | Global Manager, on the overlay transport zone of each location | Same subnet and gateway in both locations; one VNI per location stitched over RTEP; holds the Operations nodes, Ops for Networks, Automation and the license server |

- **Federation.** Global Manager cluster active at site A, standby at site B. Each site keeps its own Local Manager (the NSX Manager cluster VCF deployed) and its own edge cluster in the management domain. The GM-to-location limit is 150 ms for stretched networks, so there's room.
- **Stretched Tier-0, active/active, primary at A, secondary at B.** The secondary location advertises the stretched prefixes too; you make them less attractive — the VCF design does it with a route map that prepends the local AS twice on the secondary location's BGP peers — so ingress lands at A as long as A is up. All egress leaves through the primary location.
- **Stretched Tier-1, DR-only (no stateful services), same span as the Tier-0,** so it simply follows the Tier-0's primary location. If you want the NSX load balancer in front of Operations, the Tier-1 carries services, and the blueprint wants a standby load balancer with the same configuration ready at the recovery site.
- **One stretched overlay segment** on that Tier-1, created on the Global Manager, same subnet and gateway in both locations. Federation stitches one VNI per location into a single broadcast domain over the RTEP tunnels between the two edge clusters. That is Geneve across the DCI, so give the path MTU headroom: the VCF design prefers 1,700 bytes for RTEP traffic, with 1,500 as the floor.
- **The VM management VLANs are not stretched,** and don't need to be. SDDC Manager, vCenter and the Local Managers never move; only the overlay segment carries things that do.

In steady state nothing user-facing crosses the DCI. What does: vSphere Replication, Global Manager sync (GM ↔ GM and GM ↔ LM), RTEP control traffic and BUM traffic for the segment. Make sure the RTEP interfaces and the replication VMkernel ports have the routes, MTU and firewall rules they need before you call the design done.

### The Protection and Recovery side

The 9.1 blueprint's requirements are specific enough that you can lift them as they are:

- one Protection and Recovery instance per VCF instance, in the management domain, *Light* deployment, on the VM management network, static IP, A and PTR records, NTP rather than VMware Tools (VCF-PB-DR-REQD-001 to 007)
- a dedicated port group, VLAN and VMkernel interface per ESX host for replication traffic, static addressing (REQD-008 to 011)
- vSphere Replication as the method — storage-agnostic, no storage replication adapter to maintain — which means all the protected management VMs sit in the same cluster (REQD-013)
- the replication policy for the management VMs: **no guest-OS quiescing** (replicas are crash-consistent; not every management appliance tolerates quiescing), **network compression on**, **RPO 15 minutes**, **three point-in-time copies over 24 hours** (REQD-015 to 018)
- a prioritised startup order for the Operations nodes and the Ops for Networks nodes, kept up to date when you add nodes (REQD-019, 020)
- every protected component configured with DNS *and* NTP servers from both instances, so it still resolves and keeps time after a failover (REQD-021, 022)
- a backup location configured in VCF Operations for both instances, with the site-A backups available on site B's SFTP server under the path configured there — same server, or replicate the backups (REQD-023, 024)
- an RTO of 4 hours or less (REQD-014)

The last pair is the one people forget. Automation and the management services are *restored*, not replicated. If your SFTP target is a VM at site A, there is nothing to restore from at site B.

One more operational note: Protection and Recovery manages its own lifecycle (VCF Operations doesn't). The blueprint recommends installing its management pack and pointing the vSphere Replication and Site Recovery adapters at each instance's local cloud proxy or collector group.

### Crash-consistent replicas of a distributed analytics cluster

Worth stating plainly, because it drives the RPO decision. The Operations cluster is a distributed analytics store, and vSphere Replication is per-VM and crash-consistent; there is no multi-VM consistency group. The blueprint accepts this (quiescing is off by design), the three point-in-time copies are your fallback if the latest replica doesn't come up cleanly, and 15 minutes is the documented RPO. vSphere Replication in 9.1 can go tighter with the right entitlement, but the first thing to budget is the *initial* full sync of several hundred gigabytes of Operations nodes across the DCI. Seed it or schedule it, and check that the steady-state change rate fits the link with compression on.

### The option: VCF Operations Continuous Availability instead of replication

The 9.1 Fleet DR blueprint leaves a door open: VCF Operations Continuous Availability (CA) can be used between the sites if the latency requirements are met. CA isn't a recovery mechanism — it's a different availability model for the Operations cluster, and it replaces replication for that one component.

![Two VCF instances side by side, each a VCF Operations fault domain: the primary node and data nodes at site A, the primary replica and the same number of data nodes at site B, all on the stretched overlay segment with a synchronous pairing arrow between the fault domains. A witness node in a third site is connected to both fault domains over routed links labelled 30 ms. Operations for Networks is still replicated with vSphere Replication. Each site has its own collector group and cloud proxy on its management VLAN. A footer lists what changes versus replication.](/images/fleet-overlay-dr/fig-05-ops-ca.svg)

**What it is.** The analytics cluster is split into two fault domains, one per VCF instance, and every object lives on a primary node in one fault domain and a replica node in the other. A witness in a third location breaks ties when the fault domains lose each other: whichever side can still reach the witness stays up, the other shuts down. You can lose one whole fault domain with no data loss. Lose node pairs across both and you can lose data for good; lose both fault domains and you're restoring from backup.

**Requirements** (design library CA model, VCF-OPS-REQD-CA-001 to 009; sizing KB 397782; KB 444771):

| Requirement | Value |
|---|---|
| Latency between fault domains | < 10 ms RTT average, peaks up to 20 ms over 20-second intervals |
| Latency within a fault domain | < 5 ms RTT |
| Latency witness ↔ each fault domain | ≤ 30 ms RTT |
| Witness placement | A third location, reachable over L3 from both fault domains; 2 vCPU, 8 GB; one witness per cluster |
| Node layout | Same number of nodes, same size, in each fault domain; at most 1 / 4 / 8 / 6 nodes per fault domain for Small / Medium / Large / Extra Large |
| Network | Analytics nodes of both fault domains on the same VLAN and IP subnet — which is exactly what the stretched overlay segment gives you across two VCF instances; the design library lists the NSX stretched segment as a supported placement for CA |
| Capacity | Roughly double the compute and storage of a single-site cluster |
| Deployment | Start from a Simple or High Availability instance, deploy the second VCF instance, deploy the replica nodes from template and the witness at the third site; if you started from High Availability, the primary replica gets reconfigured as a data node |
| Collection | A collector group per VCF instance, local adapters on the local group, DRS anti-affinity on the collectors; a cloud proxy per instance |
| Certificate | The SAN has to include every cluster node's FQDN (and the load balancer's, if you use one) |

**How it fits.** CA lives in the window the latency ladder leaves between the stretched-cluster limit and the Federation limit: more than 5 ms (so no single cluster, no vSAN stretched) but less than 10 ms (so the Ops fault domains can still pair). A regional DCI at 6–9 ms qualifies; one at 12 ms doesn't, and the KB on CA latency alerts is clear that CA stops behaving when the thresholds are breached consistently. The third site for the witness is the other gate. It can be small, but it has to be a separate failure domain with routed reachability to both instances.

If both hold, the design changes in exactly one place:

- **VCF Operations drops out of the recovery plan.** Both fault domains run all the time; lose site A and the fault domain at site B keeps serving users with no failover, no RPO on Operations data and no re-IP. You redeploy the lost nodes in the surviving site afterwards.
- **You still need the stretched segment,** now for a second reason: CA wants one subnet across both fault domains, and the segment is how two VCF instances share one.
- **Network Recovery is still step zero.** The Ops nodes at site B are alive, but their north–south path still hangs off the stretched Tier-0's primary location. Until the Global Manager is promoted and Location B made primary, nobody outside the segment can reach them. Steps 1–3 of the runbook don't change.
- **Ops for Networks isn't covered by CA** and stays on vSphere Replication with its recovery plan; Automation and the management services are still restored from backup; the SFTP requirement is the same.
- **The steady-state cost moves onto the DCI.** Replication traffic becomes continuous pairing traffic between the fault domains, and the latency has to hold all the time, not just during a failover. Watch it — the KB about the custom latency alert exists because clusters drift out of spec quietly.

So the choice between the two models is a latency question first and a budget question second. Under 10 ms, with a third site to put the witness in, CA buys you zero RPO and zero RTO on the one component the rest of the fleet depends on, for the price of doubling it. Above 10 ms, replication is the only option and 15 minutes is the documented RPO.

## Part 3 — The day it happens: the runbook, in order

![Three lanes of numbered steps. NSX lane: promote the standby Global Manager, run Network Recovery to make Location B primary for the stretched Tier-0 and Tier-1, check the prefix is advertised from B. Protection and Recovery lane: run the recovery plan in disaster-recovery mode, priority order Operations primary, replica, data nodes, then Operations for Networks, then a prompt to bring up the site-B collectors. VCF Operations lane: deploy a new VCF Automation instance at B and restore the latest backup, restore the management services components onto B's instance, validate and plan the reprotect. A side note explains why the network goes first and that only the Operations nodes need IP mobility.](/images/fleet-overlay-dr/fig-03-failover-runbook.svg)

**Fix the network first.** This is the part that catches people who assume "stretched" means "automatic". It doesn't. KB 441764 is direct about it: stretched gateways in NSX Federation do not fail over between locations on their own, and a complete edge-cluster failure at the primary location is a data-plane outage until someone runs the Network Recovery workflow. The Global Manager doesn't fail over by itself either (KB 444980).

1. **Promote the standby Global Manager** at site B (*System › Location Manager › Make Active*). It takes a few minutes; the site-B Local Manager keeps forwarding in the meantime.
2. **Run Network Recovery** on the now-active Global Manager: pick Location B as the new primary for the stretched Tier-0. A DR-only Tier-1 with the same span follows it; a Tier-1 with its own span you pick separately. Until this is done the segment exists on site B's hosts but has no north–south path — KB 412806 documents exactly that symptom in a failover test, traffic still trying to leave through the dead site.
3. **Check the prefix.** Site B's Tier-0 now advertises the segment's subnet as primary. DNS doesn't change: same addresses, same names.

**Then the recovery plan,** in disaster-recovery mode. (Planned migration is the same plan with a final sync; that's the one you use for maintenance moves.)

4. The protection group holds the VCF Operations cluster and the Ops for Networks platform. They power on at site B, on the same segment, from the latest replica. (With the CA option, the protection group holds Ops for Networks only — the Operations fault domain at site B is already running.)
5. Startup priority: Operations primary, then replica, then data nodes; Ops for Networks platform, then its collectors. (The 9.0 validated solution, written when the fleet-management appliance was a separate replicated VM, starts that VM first; in 9.1 the fleet lifecycle service is restored onto the management services instance instead — step 8.)
6. A prompt in the plan reminds you to power on or confirm the site-B cloud proxy and collector, and to re-point any adapter that was pinned to a site-A collector. Only site-B endpoints are reachable at this point; at under 50 ms, Operations at B would happily keep collecting from anything left at A.

**Then the things that aren't replicated.**

7. **VCF Automation.** From VCF Operations at site B, *Fleet Management › Lifecycle › VCF Management Components*: deploy a new Automation instance — on the stretched segment, same FQDN, same IPs if you reserved them — then *Backup and Restore › Restore* from the latest backup on the cluster. The site-A instance has to be powered off, and you need a known-good backup. That is what the SFTP requirement was for.
8. **Management services components.** Restore Identity Broker, the software depot and the rest onto site B's existing management services instance. The blueprint's footnote matters here: only one instance of each component per management services instance, so site B must not already be running its own Identity Broker or depot.
9. **Validate, then plan the way back.** Logins through Identity Broker, adapters green, Automation organisations and VPCs visible. When site A comes back, re-add its Global Manager as standby (it returns partially active and has to be demoted, or you get conflicts), reprotect in the other direction, and schedule the planned migration home.

### What the latency does on the day

- **Steps 1–3** are control-plane work at site B; latency to a dead site A is irrelevant.
- **Steps 4–6** are local to site B. The replica was already there; the 15-minute RPO is what you lose, and the RTT adds nothing to it.
- **Hairpinning is a planned-migration risk, not a disaster one.** Move Operations to B while A is healthy and leave the Tier-0 primary at A, and every north–south flow from Operations goes through A — roughly twice the inter-site RTT on every adapter call and every click (20–24 ms on a 10–12 ms DCI). Flip the primary location as part of the planned migration too.
- **Ops for Networks collectors and cloud proxies** tolerate hundreds of milliseconds; nothing to do.
- **Restores (steps 7–8)** read from the SFTP target. At site B, or at A and replicated: local. At A and not replicated: you stopped at step 7.

## What I'd write into the design document

1. **Defer Operations and Automation at bring-up** and land them on the overlay segment from the Installer. The Lifecycle page in Operations can't put Automation on a segment.
2. **One stretched overlay segment, NSX Federation, a DR-only Tier-1, an active/active Tier-0 with A primary.** Write the Network Recovery workflow down as step zero of the DR plan and rehearse it; the gateways will not move themselves.
3. **Two instances, not a stretched cluster.** Past 5 ms RTT, vSAN stretched is off the table. Design for RPO 15 minutes and RTO 4 hours or less on the replicated components, and say so.
4. **Replicate Operations and Ops for Networks only** — or, if the DCI is under 10 ms and you have a third site for the witness, run Operations in Continuous Availability and replicate only Ops for Networks. Either way Automation, Identity Broker and the fleet services come back from backup, so budget the time and get the SFTP backups to site B.
5. **DNS and NTP from both sites on every protected component,** before the first test.
6. **Test with an isolated test network, then with a planned migration,** and include the Automation redeploy-and-restore in the test. It is the slowest step and the one with the most preconditions.

None of this is exotic. It's the two-site SRM design we've been building for years, with two differences that are easy to miss: the stretched segment is a Federation object that a human has to move, and half of the fleet is restored rather than replicated.

## Sources

- [Deploy Deferred Components on NSX Overlay Segments (VCF 9.1)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/deployment/deploying-a-new-vmware-cloud-foundation-or-vmware-vsphere-foundation-private-cloud-/deploying-vcf-operations-and-vcf-automation-on-custom-networking/deploy-vcf-operations-and-vcf-automation-on-nsx-segments.html)
- [Deploying Deferred Components on Custom Networking (VCF 9.1)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/deployment/deploying-a-new-vmware-cloud-foundation-or-vmware-vsphere-foundation-private-cloud-/deploying-vcf-operations-and-vcf-automation-on-custom-networking.html)
- [Start a New VCF Fleet or VCF Instance Deployment — Installer wizard (VCF 9.1)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/deployment/deploying-a-new-vmware-cloud-foundation-or-vmware-vsphere-foundation-private-cloud-/deploy-a-new-vcf-fleet-or-a-new-vcf-instance.html)
- [VCF Management Dedicated VLAN and NSX Overlay Segment Network Model (design library)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/design/design-library/fleet-level-components-networking-detailed-design/logical-application-virtual-network-design-for-vmware-cloud-foundation.html)
- [VCF Management Dedicated VLAN and NSX Stretched Overlay Segment Network Model (design library)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/design/design-library/fleet-level-components-networking-detailed-design/fleet-level-components-on-stretched-nsx-overlay-segment-model.html)
- [VCF Fleet Disaster Recovery — design blueprint (VCF 9.1)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/design/design-blueprints-for/security-modernization/vcf-disaster-recovery.html) and its [Design Elements](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/design/design-blueprints-for/security-modernization/vcf-disaster-recovery/design-elements-for-vcf-disaster-recovery.html)
- [Site Protection and Disaster Recovery for VMware Cloud Foundation — validated solution (9.x)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vvs/9-X/site-protection-and-disaster-recovery-for-vmware-cloud-foundation.html), including [Detailed Design](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vvs/9-X/site-protection-and-disaster-recovery-for-vmware-cloud-foundation/detailed-design-for-site-protection-and-disaster-recovery.html), [Network Design](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vvs/9-X/site-protection-and-disaster-recovery-for-vmware-cloud-foundation/detailed-design-for-site-protection-and-disaster-recovery/network-design-for-site-protection-vvs.html) and [Initiate a Disaster Recovery of VCF Automation](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vvs/9-X/site-protection-and-disaster-recovery-for-vmware-cloud-foundation/operational-guidance-for-site-protection-vvs/failover-of-the-sddc-management-applications/perform-disaster-recovery-of-the-sddc-management-applications/initiate-a-disaster-recovery-of-vcf-automation-for-site-protection-and-disaster-recovery-for.html)
- [Protection and Recovery 9.1 Release Notes](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/protection-and-recovery/9-1/release-notes/protection-and-recovery-91-release-notes.html) · [KB 444864 — transition from standalone SRM and vSphere Replication](https://knowledge.broadcom.com/external/article/444864/vcf-protection-and-recovery-91-transitio.html)
- [KB 397782 — VCF Operations 9.0 sizing guidelines (node latency)](https://knowledge.broadcom.com/external/article/397782/vcf-operations-90-sizing-guidelines.html) · [KB 417347 — cloud proxy latency](https://knowledge.broadcom.com/external/article/417347/error-failed-to-propagate-configuration.html)
- [Continuous Availability VCF Operations Model (design library, VCF 9.1)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/design/design-library/vcf-operations-design/continuous-availability-vcf-operations-model.html) · [KB 444771 — latency alert thresholds between VCF Operations nodes and fault domains](https://knowledge.broadcom.com/external/article/444771/custom-alert-for-network-latency-between.html)
- [KB 441764 — stretched gateways do not automatically fail over between locations in NSX Federation](https://knowledge.broadcom.com/external/article/441764/stretched-gateways-do-not-automatically.html) · [KB 444980 — Global Manager failover is manual](https://knowledge.broadcom.com/external/article/444980/nsx-global-manager-failover-process-and.html) · [KB 412806 — traffic still routing to the primary site after failover](https://knowledge.broadcom.com/external/article/412806/federation-disaster-recovery-failover-te.html)
- [KB 443842 — brownfield support for Operations and Automation on NSX VLAN segments after upgrade to 9.1](https://knowledge.broadcom.com/external/article/443842/vcf-91-brownfield-upgrade-support-for-vc.html)
- [vStellar — VCF 9.1 home lab part 10: deploy VCF Automation (Day-2 placement note)](https://vstellar.com/2026/06/vcf-9-1-home-lab-series-part-10-deploy-and-configure-vcf-automation/)
