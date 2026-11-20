---
title: "Monitoring NSX Federation in VCF Operations, part 1: designing the management pack"
date: 2026-11-17
description: "VCF Operations 9.1.1 has an adapter instance for each NSX Local Manager but has no view of an NSX Federation: no Global Manager, no location sync, no RTEP tunnels, no stretched objects. This post documents the design of a custom management pack on a lab: what the built-in integration covers, which NSX API calls carry the federation state, the object model, the metrics, and what a review of the design against enterprise scale changed."
summary: "A two-location NSX 9.1 Federation runs in the lab and VCF Operations shows none of it. Part 1 of the management pack build: checking the built-in NSX integration, probing the Global Manager and Local Manager APIs, a first object model of 12 types, the metric and property list, and the changes that a review against enterprise scale forced before any code was written."
tags: ["VCF 9.1.1", "NSX Federation", "VCF Operations", "Management pack", "Integration SDK", "Lab"]
categories: ["Field notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-federation-mp/gm-location-manager.webp"]
params:
  seoDescription: "Designing a VCF Operations management pack for NSX 9.1 Federation: API calls, object model, metrics and the changes a review against enterprise scale forced."
---

VCF Operations 9.1.1 has an adapter instance for every NSX Local Manager in the lab and shows nothing of the federation that links two of them. The Global Manager, the sync state between locations, the RTEP tunnels and the stretched gateways have no object, no metric and no alert. This post describes the design of a management pack that adds them. The build and the install are in [part 2](/posts/nsx-federation-management-pack-install/).

## The lab

The federation runs on a VCF 9.1.1 lab. The Global Manager, `nsx-gm-01a` (10.1.1.37, NSX 9.1.1), is a single active node with no standby. The two locations run NSX 9.1.1: `site-a-wld01` (Local Manager `nsx-wld01-a`, cluster virtual IP 10.1.1.24) and `site-a-wld03` (Local Manager `nsx-wld03-a`, cluster virtual IP 10.1.1.80). Each has a two-node edge cluster and its own RTEP VLAN. The Global Manager owns one active-active Tier-0 (`fed-t0`, four BGP sessions to the lab router), three Tier-1 gateways, five segments including the stretched `fed-stretched`, and one firewall policy, `fed-demo`. A standby Global Manager, `nsx-gm-01b`, was added later, before the install, and the pack is tested with both Global Managers ([part 2](/posts/nsx-federation-management-pack-install/)).

The lab has four NSX Local Managers in all, and two of them are in the federation.

The Location Manager page of the Global Manager shows the federation:

![The Global Manager's Location Manager page: one active Global Manager, no standby, two locations with latency and sync status, and a banner saying backups are not configured](/images/nsx-federation-mp/gm-location-manager.webp)

## Step 1: check what VCF Operations already collects

A pack that copies the built-in NSX integration doubles the object count and raises two alerts for one fault. So the first step is an inventory of what is already there, taken through the VCF Operations API.

*Run on: the jump host, against the VCF Operations API.*

```bash
OPS=ops-a.site-a.vcf.lab
TOKEN=$(curl -sk https://$OPS/suite-api/api/auth/token/acquire \
  -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d '{"username":"admin","password":"'"$OPS_PW"'","authSource":"local"}' | jq -r .token)

# version, collectors, NSX adapter instances
curl -sk -H "Authorization: OpsToken $TOKEN" -H 'Accept: application/json' \
  https://$OPS/suite-api/api/versions/current
curl -sk -H "Authorization: OpsToken $TOKEN" -H 'Accept: application/json' \
  https://$OPS/suite-api/api/collectors
curl -sk -H "Authorization: OpsToken $TOKEN" -H 'Accept: application/json' \
  "https://$OPS/suite-api/api/adapters?adapterKindKey=NSXTAdapter"
```

What came back:

- Version: VCF Operations 9.1.1.
- Collector (cloud proxy): `opscollector-01a`, type `UNIFIED_CLOUD_PROXY`, state `UP`. This matters for the build, because adapters written with the Integration SDK run as containers on a collector and never on the analytics node.
- NSX integration: adapter kind `NSXTAdapter` with 50 object types and four instances, one per NSX Local Manager in the lab, including the two that are not in the federation. None of the 50 types is a Global Manager, a location or an RTEP tunnel, and no instance points at the Global Manager.
- Federation objects: the instance for `nsx-wld01-a` lists the edge cluster `fed-ec-a` and the RTEP IP pool, because those are local objects. A search of its inventory for the global objects (`fed-t0`, `fed-stretched`) returned nothing.

The built-in integration covers the per-location plumbing and stops at the federation boundary. The pack has to own the federation layer and link to the rest.

## Step 2: find the API calls that carry federation state

Every call below is a GET and was run against the live managers. The Global Manager serves its policy tree under `/global-manager/api/v1/global-infra`; a Local Manager exposes the objects it received under `/policy/api/v1/global-infra`.

**Is the federation connected?** Asked on the Global Manager:

*Run on: the jump host, against the API of the active Global Manager.*

```bash
curl -sk -u "$NSX_USER:$NSX_PW" https://nsx-gm-01a.site-a.vcf.lab/api/v1/sites/status
```

*Output: the Global Manager's response to the call above.*

```json
{"remote_connections": [
  {"site_name": "site-a-wld01", "stubs": [{"address": "10.1.1.25", "connection_up": true}]},
  {"site_name": "site-a-wld03", "stubs": [{"address": "10.1.1.81", "connection_up": true}]}],
 "active_standby_sync_statuses": []}
```

The addresses in the response, 10.1.1.25 and 10.1.1.81, are the node addresses of the two Local Managers. The same call on a Local Manager returns its view of the Global Manager and of the other location. Collected from every manager, it tells "the Global Manager lost a location" apart from "two locations lost each other".

**Is a stretched object realized everywhere?** One call per object, on the Global Manager:

*Run on: the jump host, against the API of the active Global Manager.*

```bash
curl -sk -u "$NSX_USER:$NSX_PW" \
 "https://nsx-gm-01a.site-a.vcf.lab/global-manager/api/v1/global-infra/realized-state/status?intent_path=/global-infra/segments/fed-stretched"
```

The response carries a consolidated status and one status per location (`SUCCESS` for both here). The companion call `/global-infra/span?intent_path=...` lists the locations the object is supposed to reach.

**Are the cross-location tunnels up?** This lives on the Local Managers, per edge cluster and per edge node:

*Run on: the jump host, against the API of the Local Manager `nsx-wld01-a`.*

```bash
curl -sk -u "$NSX_USER:$NSX_PW" \
 https://nsx-wld01-a.site-a.vcf.lab/api/v1/edge-clusters/<edge-cluster-id>/inter-site/status
curl -sk -u "$NSX_USER:$NSX_PW" \
 https://nsx-wld01-a.site-a.vcf.lab/api/v1/transport-nodes/<edge-node-id>/inter-site/statistics
```

The first returns the inter-site BGP sessions of each edge node (2 total, 2 established on `edge-wld01-01a`) with source and neighbor RTEP addresses. The second returns bytes, packets, dropped and blocked packets per tunnel.

The full map, as verified:

| Question | Manager | API path |
| --- | --- | --- |
| Global Manager role and cluster | Global | `/api/v1/cluster/status`, `/global-infra/global-managers` |
| Location list, versions, RTEP addresses | Global | `/api/v1/sites`, `/global-infra/sites`, `/global-infra/federation-config` |
| Connection between managers | Both | `/api/v1/sites/status` |
| Full sync state | Local | `/policy/api/v1/infra/full-sync-states` |
| Span and realization of a stretched object | Global | `/global-infra/span`, `/global-infra/realized-state/status` |
| Inter-site BGP and RTEP tunnel statistics | Local | `/api/v1/edge-clusters/{id}/inter-site/status`, `/api/v1/transport-nodes/{id}/inter-site/bgp/summary`, `.../inter-site/statistics` |
| Tier-0 uplink BGP neighbors | Local | `/policy/api/v1/global-infra/tier-0s/{id}/locale-services/{location}/bgp/neighbors/status` |
| Rule statistics of a global security policy | Local | `/policy/api/v1/global-infra/domains/default/security-policies/{id}/statistics` |
| Backup, certificates, alarms | Both | `/api/v1/cluster/backups/overview`, `/api/v1/trust-management/certificates`, `/api/v1/alarms` |

## Step 3: the object model

A management pack starts with an object model: which things exist, what identifies them, and which object is the parent of which. The first draft has 12 types in four chains, shown in the diagram below. It models edge nodes, gateways and segments itself, and step 5 explains why the pack as built still does. The pack as built has 15 types: these 12, plus the federation itself, the sync channel between two managers and the realization of a stretched object at each location ([part 2](/posts/nsx-federation-management-pack-install/)).

![Object model: Global Manager to Manager node; Location to Edge cluster to Edge node to RTEP tunnel; Tier-0 gateway to Tier-1 gateway to Segment, with BGP neighbor under Tier-0; Global policy to Rule](/images/nsx-federation-mp/object-model.svg?v=2)

Identifiers come from NSX, never from a manager address: the federation id and site id for managers and locations, the location and node name for edge nodes, the RTEP and peer addresses for tunnels and BGP sessions, the global policy path for gateways, segments and policies. An object keyed on an IP address or FQDN becomes a second object, with no history, the day a virtual IP changes or a Global Manager fails over. In the pack as built, a sync channel is identified by the names NSX gives its two ends, a location or a Global Manager, as in `site-a-wld01 -> gm-site-a` (`gm-site-a` is the name of `nsx-gm-01a` in the federation).

## Step 4: metrics and properties

Each object type gets a short list. Metrics are numbers kept over time; properties are state and configuration.

| Object type | Metrics | Properties |
| --- | --- | --- |
| Global Manager | nodes online and offline, open alarms, hours since last backup, days to certificate expiry | mode (active or standby), cluster status, version |
| Location | connection up from each peer, version gap to the Global Manager | Local Manager address, RTEP addresses, full-sync stage |
| Edge cluster, Edge node | inter-site BGP sessions total and established | inter-site status, RTEP address and VLAN |
| RTEP tunnel | bytes and packets in and out, dropped, blocked | local and remote RTEP, remote location |
| Tier-0, Tier-1, Segment | locations in span, realized, in error | status per location, HA mode, subnets |
| BGP neighbor | prefixes in and out, seconds established, connection drops | state, remote AS |
| Global security policy, Rule | rule count, hit, packet and byte counts per location | action, enabled, status |

## Step 5: reviewing the design for enterprise scale

The design was then reviewed against an enterprise target: three-node Global Manager clusters in an active and a standby site, many locations and thousands of stretched objects. The review changed the collection. The adapter follows the active Global Manager and reports standby readiness from the first release, rules are counted on their policy and become objects only for the policies selected on the account, each Local Manager gets an API call budget, and sync health comes from the sync status and the full-sync failures. It changed the alerts too: a lost location raises one alert instead of a storm, and the pack reports on its own collection so that "no data" is never read as "healthy" ([part 2](/posts/nsx-federation-management-pack-install/) shows how each point was built).

The review also asked for reuse of the built-in Edge objects. The pack as built keeps its own edge cluster, edge node, gateway and segment objects and links its edge objects to the built-in ones, because the built-in integration does not collect the global objects and covers only the Local Managers that have an account in it.

## Step 6: the build route

VCF Operations offers two ways to build a pack: the no-code Management Pack Builder and the Integration SDK. The SDK was chosen because one adapter instance has to query several managers, discover the Local Managers from the Global Manager and follow a Global Manager failover. The derived values, such as a version gap or a location state that does not set off an alert storm, and the check of certificate thumbprints also need code.

The cost is on the build side. An SDK adapter is a container image, so the build needs Python tooling and Docker, and the collector pulls the image from a container registry it can reach.

## The Integration SDK in short

The [Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction) is a set of tools and a Python library for writing a management pack in code. The library models the objects that the pack sends to VCF Operations, with their metrics, properties, events and relationships. Each adapter is a container image with an HTTP server that calls the pack's code, and it runs on a cloud proxy. Three commands cover the work: `mp-init` creates the project, `mp-test` runs the adapter locally against a simulated VCF Operations, and `mp-build` packages the adapter, its metadata and its content into a `.pak` file.

## The limits of the Management Pack Builder

The Management Pack Builder is the no-code route: a pack is designed in screens of VCF Operations, "without writing a single line of code" ([VMware blog](https://blogs.vmware.com/cloud-foundation/2026/02/04/extending-visibility-with-vmware-cloud-foundations-management-pack-builder/)). Beyond the lack of code, two of its limits weigh more than the build cost of the SDK.

- **The version break.** After an upgrade of VCF Operations to 9.1.1, custom packs built with an earlier version of the builder stop collecting, and their objects show stale or missing metrics, properties and alerts. The fix is per pack: open each design in the updated builder, make a small change so that the builder registers a modification, run the upgrade in the install step and check that the adapter instances return to "Data Receiving" (Broadcom KB [454434](https://knowledge.broadcom.com/external/article/454434)). Some of Broadcom's own packs also needed their 9.1.1 versions after the same upgrade (KB [454432](https://knowledge.broadcom.com/external/article/454432)). A builder pack is repaired in the builder, one design at a time; an SDK pack is rebuilt from its own source.
- **Events and alerts.** The builder shipped with VCF 9.0 dropped the direct mapping of polled events to alerts. A Broadcom employee answered on the [community forum](https://community.broadcom.com/vmware-cloud-foundation/discussion/vcf-ops-v9-built-in-pack-builder-is-inferior) that VCF Operations has other means to raise alerts from events and that there are no plans to bring the mapping back, though he would raise it with the team.

## Open at the end of part 1

- Install the SDK tooling and pick the registry.
- Build the collector core against the lab, with a mock for what a two-location lab cannot show, such as hundreds of stretched objects.
- Find the API call behind the latency that the Location Manager page shows.

[Part 2](/posts/nsx-federation-management-pack-install/) covers the build, the install and what the pack monitors.

## Sources

- Lab: VCF 9.1.1, NSX 9.1.1 (Local Managers and Global Manager), VCF Operations 9.1.1. All API calls in this post were run on 17 November 2026.
- [VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction)
- Broadcom KB [454434](https://knowledge.broadcom.com/external/article/454434): packs built with Management Pack Builder do not collect after the upgrade to VCF Operations 9.1.1
- Broadcom KB [454432](https://knowledge.broadcom.com/external/article/454432): management packs not collecting data after the upgrade to VCF Operations 9.1.1
- [VMware blog: extending visibility with the Management Pack Builder](https://blogs.vmware.com/cloud-foundation/2026/02/04/extending-visibility-with-vmware-cloud-foundations-management-pack-builder/)
- [Broadcom community: the built-in pack builder in VCF Operations 9](https://community.broadcom.com/vmware-cloud-foundation/discussion/vcf-ops-v9-built-in-pack-builder-is-inferior)
