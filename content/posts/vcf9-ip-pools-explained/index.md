---
title: "Pools, blocks and IP Spaces in VCF 9.1: which one feeds which network component"
date: 2026-10-28
description: "VCF 9.1 asks for a Network Pool, two TEP pools, an External IP Block, a Private Transit Gateway IP Block, VPC private CIDRs, a service CIDR and — in VCF Automation — IP Spaces. One word, pool, for seven different objects. Here is what each one is, where it is defined, and which gateway, subnet or host actually consumes it."
summary: "Between SDDC Manager, NSX, the Supervisor wizard and VCF Automation, VCF 9.1 asks you for at least seven different 'pools'. This post maps every one of them to the network component that consumes it — Tier-0, Transit Gateway, VPC gateway, subnets, ESX hosts, Supervisor — with three diagrams, the NAT points, the overlap rules and the sizing traps of 9.1."
tags: ["VCF 9.1", "NSX", "VPC", "Transit Gateway", "VCF Automation", "Supervisor", "IP addressing", "Holodeck"]
categories: ["Field notes"]
showToc: true
tocOpen: false
images: ["/images/ip-pools/fig-01-pool-map.svg"]
---

VCF 9.1 asks for addresses in four different places: SDDC Manager wants a *Network Pool*, NSX wants *IP Address Pools* and *IP Address Blocks*, the Supervisor wizard wants *CIDRs*, and VCF Automation wants *IP Spaces*. They all look like "a range of IPs", they are all called a pool at some point, and they all do something different. The confusion is understandable: the same `10.x.0.0/16` typed into the wrong field either never leaves the fabric or gets advertised to your whole data centre.

This post is the map I wish I had had. For each pool: what it is, where you define it, which network component consumes it, and whether it is ever seen north of the Tier-0.

*October 2026 · Holodeck VCF 9.1.1 lab · centralized Transit Gateway unless stated otherwise · example addresses are the lab's*

## The map

![Every pool and block in a VCF 9.1 domain, mapped to the component that consumes it: External IP Block and Edge uplinks at the Tier-0, Private Transit Gateway IP Block and the 100.64 transit subnet at the Transit Gateway, SNAT and LB VIPs at the VPC gateway, three subnet types, Network Pool and TEP pools at the ESX hosts, management range and service CIDR at the Supervisor control plane.](/images/ip-pools/fig-01-pool-map.svg)

Read it top to bottom as a packet would, south to north. Everything in **blue** leaves the fabric with its own address. Everything in **green** stays inside one Transit Gateway. Everything in **purple** needs NAT from a blue block to get out. The **mustard** pills are plain VLANs that the overlay rides on, and the **dark** ones never leave the Supervisor.

| Pool / block | Defined in | Consumed by | Advertised north? |
|---|---|---|---|
| Network Pool | SDDC Manager | ESX vmkernel ports: vMotion, vSAN, NFS, Host Overlay (TEP) | No (plain VLANs) |
| Host TEP IP Pool | NSX, created by VCF from the Network Pool | Host transport nodes (GENEVE tunnel endpoints) | No |
| Edge TEP IP Pool | Edge cluster wizard (vCenter › Networks › Transit Gateways) | Edge transport nodes | No |
| **External IP Block** | NSX IP Address Blocks, visibility *External* | Tier-0 (advertised by BGP), public subnets, SNAT, 1:1 NAT, LB VIPs | **Yes** |
| **Private Transit Gateway IP Block** | NSX IP Address Blocks, visibility *Private* | Transit Gateway: private-TGW subnets, routed between VPCs of the project | No (9.1: optional) |
| VPC Private CIDRs / org Private IP Blocks | Supervisor wizard, vCenter VPC, or the org portal in VCFA | VPC gateway: private subnets, NAT'd on the way out | No |
| VPC transit subnet (`100.64.0.0/16`) | Transit Gateway, automatic | TGW ↔ VPC-gateway links | No |
| Service CIDR | Supervisor wizard | Kubernetes ClusterIP services inside the Supervisor | No |
| Management range | Supervisor wizard | 5 consecutive IPs on the management VLAN for the control plane | Routable VLAN |
| **IP Space** | VCF Automation provider portal | Provider Gateway (Tier-0 / VRF); becomes the org project's External IP Block | **Yes** |

## 1. The underlay: Network Pool and TEP pools

These are the only "pools" that are honest VLAN subnets with a physical gateway on the ToR.

**Network Pool (SDDC Manager).** Created when you commission hosts. It holds one range per traffic type — vMotion, vSAN, NFS and *Host Overlay* — and SDDC Manager hands an address from each range to every host's vmkernel port. In the lab the workload hosts sit on `wld-a-np01`. A Network Pool has nothing to do with NSX routing; it is per-host plumbing.

**Host TEP IP Pool (NSX).** The Host Overlay range of the Network Pool reappears in NSX as an *IP Address Pool* under Networking › IP Address Pools; VCF creates it for you during cluster creation, or you can pre-create one in NSX. Each ESX transport node takes one address per uplink from it; those addresses are the outer source and destination of every GENEVE packet. DHCP on the TEP VLAN is an accepted alternative.

**Edge TEP IP Pool.** Same idea for the Edge nodes, created on the fly in the *Setup Network Connectivity* wizard that deploys the Edge cluster (pick *IP Pool* as the assignment type and the wizard lets you define it in place). Edges also get their uplink VLANs here: a /29 or /30 point-to-point per ToR for the BGP peering of the Tier-0.

None of these is advertised anywhere, and none of them is needed in the TEP-less, VLAN-backed VPC model of 9.1.1 — that is the whole point of [the TEP-less post](/posts/vks-without-teps/).

## 2. NSX IP Address Blocks: the two that matter

Under NSX › Networking › IP Address Management › IP Address Blocks live the two objects that decide what leaves the fabric. VCF creates a `Day0 External Ip Block` and a `Day0 Private Tgw Ip Block` during bring-up; you can use them or replace them, but you must understand the *visibility* flag.

### External IP Block (visibility: External)

These are ranges VCF owns, they must not overlap with anything else in the physical network, and the Tier-0 advertises them to the upstream routers over BGP. Everything that has to be reachable from outside the VPC takes an address from here: public subnets, the SNAT address of each VPC, 1:1 NAT addresses, and every load-balancer VIP — including the Supervisor API VIP, which is why the API server FQDN resolves into this block and not into the management VLAN.

Two sizing traps:

- The block is **auto-subnetted**. A public subnet is carved out of it in the size the VPC asks for (/26 by default in the wizard). Plan for subnets, not for a count of IPs.
- The Supervisor is greedy. By default it reserves the *whole* Day-0 External IP Block at activation even though it uses only a slice of it, leaving nothing for anyone else. [William Lam's fix](https://williamlam.com/2026/08/vcf-9-1-quick-tip-optimized-workflow-for-configuring-distributed-transit-gateway-dtgw-with-nsx-virtual-network-appliance-vna.html) is to split the Day-0 block into several /26 subnets *before* activation: the Supervisor then takes one /26 and the rest stays free for VCF Automation and other external connections, on the same VLAN.

A connectivity profile accepts up to five External IP Blocks.

### Private Transit Gateway IP Block (visibility: Private)

This block feeds *private transit gateway subnets*. Its addresses are never advertised northbound and it is scoped to the project that owns the Transit Gateway. A private-TGW subnet is reachable from every VPC attached to the same Transit Gateway without NAT; that is what the Supervisor Services VPC uses so that service pods can be reached from the other VPCs of the project. To leave the project, traffic from such a subnet is source-NAT'd at the Transit Gateway to an External IP — unless you turn on the 9.1 *Private IP Blocks* switch on the external connection, which lets private-TGW subnets be extended through the external connection as they are (see the [VCF blog on Transit Gateway connectivity options](https://blogs.vmware.com/cloud-foundation/2026/05/12/transit-gateway-connectivity-options-in-vcf-9-1/)).

And the 9.1 trap: give this block a **/16**. A /24 was fine in 9.0; in 9.1 the Supervisor/VKS deployment stalls with a /24, as [vstellar](https://vstellar.com/2026/05/vcf-9-1-home-lab-series-part-6-configure-network-connectivity/) and others report. Broadcom documents no minimum; practitioners agree on the symptom. Since the block is never advertised, a /16 costs nothing. Just don't reuse the Day-0 default `172.31.0.0/16` if you also talk to AWS.

### The one nobody configures: the transit subnet

Between the Transit Gateway and each VPC gateway NSX allocates link addresses from `100.64.0.0/16` (the *VPC Transit Subnet* on the TGW). NSX picks it itself from the shared-address range and there is no reason to change it. You will see it in traceroutes; you should never type it.

## 3. Inside a VPC: three subnet types, three exits

Everything above only matters at the moment a subnet is created, because the **subnet type** decides which block the address comes from and where NAT happens.

![One VPC with a public, a private and a private-TGW subnet. Public addresses come from the External IP Block and reach the Tier-0 unchanged; private addresses come from the VPC private CIDR and are SNAT'd or 1:1-NAT'd at the VPC gateway; private-TGW addresses come from the Private Transit Gateway IP Block, are routed between VPCs by the Transit Gateway and SNAT'd there when they leave the project.](/images/ip-pools/fig-02-subnet-exits.svg)

| Subnet type | Address comes from | Advertised north? | NAT point | Reachable from |
|---|---|---|---|---|
| Public | External IP Block | yes, as itself | none | anywhere the block is routed |
| Private | VPC Private CIDR (vCenter) / org Private IP Block (VCFA) | only via NAT | VPC gateway: SNAT, or 1:1 NAT when published | inside the VPC |
| Private-TGW | Private Transit Gateway IP Block | no (9.1: optional extension) | Transit Gateway: SNAT | every VPC on the same TGW |

A few consequences that are easy to miss:

- **Private CIDRs may overlap** between VPCs and between organizations; the other two block types must be unique in the project, and the External block must be unique in the data centre.
- The **default SNAT** toggle lives in the VPC Connectivity Profile. Off means a private subnet has no way out at all until you publish a 1:1 NAT; the VLAN-backed VPC of 9.1.1 requires it off because there is no NAT engine on a plain VLAN.
- A **LoadBalancer VIP** is an External IP whichever subnet the backends sit in. Private subnet + LB VIP is the normal VKS pattern: nodes on private CIDR, API endpoint on an External IP.
- The **ToR only ever learns External blocks**. If a prefix from a private or private-TGW block shows up in your BGP table, someone set the wrong visibility on the block.

## 4. What VCF Automation calls it

VCF Automation (All Apps organizations) does not invent new network objects; it renames a few NSX ones and wraps them in quotas. The trick is to know which VCFA word points at which NSX object.

![VCF Automation provider objects on the left mapped to the NSX objects they create or consume on the right: Region to NSX Manager, IP Space to an External IP Address Block, Provider Gateway to a Tier-0, regional networking to the org's NSX Project, Transit Gateway and default VPC, org private IP blocks to VPC IP blocks, and org VPCs and namespaces to VPC subnets and profiles.](/images/ip-pools/fig-03-vcfa-to-nsx.svg)

**Region.** Binds one NSX Manager, the Supervisors behind it and a storage class. Usually one workload domain, and several Supervisors can share a Region only if they hang off the same NSX Manager.

**IP Space.** This is the one that confuses everyone, because it is the VCFA name for an External IP Block. An IP Space has two halves. Its *IP blocks* are the addresses south of the Provider Gateway — the ones tenants actually consume for subnets and services. Its *external reachability* is the list of prefixes north of the gateway that tenants are allowed to route to. NSX auto-subnets the IP blocks for the tenants' public subnets, for the source NAT of their private subnets and for their load-balancer VIPs — exactly the three jobs of an External IP Block. Consumption is metered per organization by quotas on single IPs, CIDRs and maximum subnet size.

**Provider Gateway.** A pointer to an NSX Tier-0 or Tier-0 VRF. You attach between one and five IP Spaces to it, and five CIDRs in total across them (so five IP Spaces means one CIDR each). It can be shared across organizations or dedicated to one — a dedicated VRF with its own IP Spaces is how you give a tenant both Internet and a private WAN.

**Regional networking.** The step that actually builds things. You give an organization a Provider Gateway and an Edge cluster in a Region, and VCF Automation creates an NSX Project named after the organization, a Default Transit Gateway inside it with an external connection to the Tier-0 behind the Provider Gateway, and a first VPC called `<region>-default-vpc` ([Fojta's deep dive](https://fojta.wordpress.com/2025/12/05/vcf-automation-9-0-networking-deep-dive/) walks through it). From that moment the organization *is* an NSX project: one project, one Transit Gateway, and the IP Space CIDRs appear as the External IP Block of that project's VPC Connectivity Profile.

**Org Private IP Blocks.** The tenant-side counterpart: organization administrators create and manage these themselves, inside the organization. These are VPC private CIDRs, nothing more: they feed private subnets, they may overlap with other tenants, and they never touch the provider's quota.

So the VCFA vocabulary collapses to one line: **IP Space = External IP Block with a quota; org Private IP Block = VPC private CIDR; Provider Gateway = Tier-0; regional networking = project + TGW + default VPC.**

## 5. Where the Supervisor sits in all this

The Supervisor adds three inputs of its own and consumes two of the blocks above.

- **Management range.** Five consecutive IPs on the management VLAN — the starting address becomes the floating IP, the next three go to the control-plane VMs, the fifth is kept for rolling upgrades. In the lab that is `10.1.1.188–.192`. This is the only Supervisor input that is a plain, routable VLAN address.
- **Service CIDR.** Kubernetes ClusterIP range. Never routed, must not overlap with anything the control plane can see, including the VKS guest clusters' own pod and service CIDRs (Antrea, non-routable, per cluster).
- **Private (VPC) CIDRs.** The VPC private block the Supervisor will carve private subnets from for VKS nodes and vSphere Pods.

Then it takes an NSX Project and a VPC Connectivity Profile, which bring the External and Private-TGW blocks with them, and creates two VPCs in that project: `kube-system_<id>` (the API VIP and its load balancers) and `vmware-system-supervisor-services-vpc_<id>` (Supervisor Services on private-TGW subnets), plus an SNAT rule for the control plane's egress — all drawn from *that project's* blocks.

In practice the Supervisor is activated in the **Default project**, so its system VPCs, its API VIP and its egress use the Default project's External block, and tenant organizations each get their own project with their own IP Space. The two never share address space; a tenant's VKS nodes just need a route to the Supervisor API VIP, and everything else goes through the VCFA Namespace Proxy. Default versus org project is a story of its own — the short version is: Default = admin context, editable from vCenter, not for multi-tenancy; org project = one per organization, own Transit Gateway, own blocks, quota through IP Spaces.

## 6. Planning cheat sheet

The lab's addressing, with the rule that applies to each line. Swap the numbers, keep the rules.

| Input | Lab value | Rule |
|---|---|---|
| Management VLAN (control plane) | `10.1.1.188–.192 /24`, GW `10.1.1.1` | 5 consecutive, different subnet from every workload CIDR |
| Host Overlay (Network Pool) | `10.1.2.0/24` | VLAN with a ToR gateway, MTU ≥ 1700 (9000 in practice) |
| Edge TEP pool | `10.1.3.0/24` | VLAN with a ToR gateway, routed to the host TEP VLAN |
| External IP Block | `10.60.0.0/24`, split in /26 | Unique in the data centre, advertised by BGP, up to 5 per profile |
| Private Transit Gateway IP Block | `10.250.0.0/16` | Unique in the project, never advertised, **/16 in 9.1** |
| Private (VPC) CIDRs | `10.244.0.0/16` | May overlap across VPCs; only reachable via NAT |
| Service CIDR | `172.29.0.0/16` | No overlap with anything, incl. VKS pod/service CIDRs |
| Transit subnet | `100.64.0.0/16` | Leave it alone |
| VCFA IP Space (per org) | `10.61.0.0/24` on `pgw-east` | Same rules as an External block, plus quotas |
| Org Private IP Block | `10.251.0.0/16` | Same rules as a VPC private CIDR |

Two checks before you click *Finish* in any of the wizards:

1. Open NSX › IP Address Blocks and read the **visibility** column. External for anything that must be reachable, Private for the Transit Gateway block. The wizard dropdowns filter on it, and an empty *External IP Blocks* list is almost always a block created with the wrong visibility — or stale wizard state; cancel and relaunch.
2. Ping the five management addresses and look up the API-server FQDN. The FQDN must *not* resolve yet to a management IP; it will get a VIP from the External block during activation, so the A and PTR records usually follow.

## Sources

- [Broadcom TechDocs — Virtual Private Cloud in NSX (VCF 9.1)](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/advanced-network-management/virtual-private-cloud-in-nsx.html)
- [Broadcom TechDocs — Create an IP Pool for Tunnel Endpoint IP Addresses](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/transport-zones-and-transport-nodes/create-an-ip-pool-for-tunnel-endpoint-ip-addresses.html)
- [Broadcom TechDocs — Managing IP Spaces in VCF Automation](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/provider-management/managing-networking-resources/managing-ip-spaces.html) · [Managing Provider Gateways](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/provider-management/managing-networking-resources/managing-provider-gateways.html)
- [VMware Cloud Foundation blog — Transit Gateway Connectivity Options in VCF 9.1](https://blogs.vmware.com/cloud-foundation/2026/05/12/transit-gateway-connectivity-options-in-vcf-9-1/)
- [Tomas Fojta — VCF Automation 9.0 Networking Deep Dive](https://fojta.wordpress.com/2025/12/05/vcf-automation-9-0-networking-deep-dive/)
- [William Lam — Optimized workflow for DTGW with VNA (External IP Block splitting)](https://williamlam.com/2026/08/vcf-9-1-quick-tip-optimized-workflow-for-configuring-distributed-transit-gateway-dtgw-with-nsx-virtual-network-appliance-vna.html)
- [vstellar — VCF 9.1 Home Lab Part 6: Configure Network Connectivity](https://vstellar.com/2026/05/vcf-9-1-home-lab-series-part-6-configure-network-connectivity/) · [VCF 9 Part 4: NSX Edge Cluster Deployment](https://vstellar.com/2025/07/vcf-9-part-4-nsx-edge-cluster-deployment/)
- [sdn-warrior — VCF 9 NSX VPC Part 1: centralized Transit Gateway](https://sdn-warrior.org/posts/vcf9-nsx-vpc/)
- [mynetworker — VCF Automation Organization Networking](https://mynetworker.blog/2026/01/11/vcf-automation-in-vcf-9-provider-management-vcf-automation-organization-networking/) · [Devyn Harrington — Building an All Apps Organization](https://devynharrington.com/vcf/building-an-all-apps-organization-in-vcf-automation-9/)
