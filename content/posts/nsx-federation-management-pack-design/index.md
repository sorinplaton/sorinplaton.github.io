---
title: "Monitoring NSX Federation in VCF Operations, part 1: designing the management pack"
date: 2026-11-17
description: "VCF Operations 9.1.1 has an adapter instance for each NSX Local Manager but has no view of an NSX Federation: no Global Manager, no location sync, no RTEP tunnels, no stretched objects. This post documents the design of a custom management pack on a nested lab: what the built-in integration covers, which NSX API calls carry the federation state, the object model, the alerts, and what a review of the design against enterprise scale changed."
summary: "A two-location NSX 9.1 Federation runs in the lab and VCF Operations shows none of it. Part 1 of the management pack build: checking the built-in NSX integration, probing the Global Manager and Local Manager APIs, an object model of 12 types, the metric and alert list, and the eight changes that a review against enterprise scale forced before any code was written."
tags: ["VCF 9.1.1", "NSX Federation", "VCF Operations", "Management pack", "Integration SDK", "Nested lab"]
categories: ["Field notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-federation-mp/object-model.svg"]
params:
  seoDescription: "Designing a VCF Operations management pack for NSX 9.1 Federation: API calls, object model, alerts and the changes a review against enterprise scale forced."
---

VCF Operations 9.1.1 has an adapter instance for every NSX Local Manager in the lab and shows nothing of the federation that links two of them. The Global Manager, the sync state between locations, the RTEP tunnels and the stretched gateways have no object, no metric and no alert. This post describes the design of a management pack that adds them. The build and the install are in [part 2](/posts/nsx-federation-management-pack-install/).

## The lab

The federation runs on a nested VCF 9.1.1 lab. The Global Manager, `nsx-gm-01a` (10.1.1.37, NSX 9.1.1), is a single active node with no standby. The two locations run NSX 9.1.1: `site-a-wld01` (Local Manager `nsx-wld01-a`, cluster virtual IP 10.1.1.24) and `site-a-wld03` (Local Manager `nsx-shared-a`, cluster virtual IP 10.1.1.80). Each has a two-node edge cluster and its own RTEP VLAN. The Global Manager owns one active-active Tier-0 (`fed-t0`, four BGP sessions to the lab router), three Tier-1 gateways, five segments including the stretched `fed-stretched`, and one firewall policy, `fed-demo`.

Both locations sit inside one VCF instance, which is a lab shortcut and should not be copied into a real design. The lab has four NSX Local Managers in all, and two of them are in the federation.

The Location Manager page of the Global Manager shows the federation:

![The Global Manager's Location Manager page: one active Global Manager, no standby, two locations with latency and sync status, and a banner saying backups are not configured](/images/nsx-federation-mp/gm-location-manager.webp)

## Step 1: check what VCF Operations already collects

A pack that copies the built-in NSX integration doubles the object count and raises two alerts for one fault. So the first step is an inventory of what is already there, taken through the VCF Operations API.

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
- Collector: `opscollector-01a`, type `UNIFIED_CLOUD_PROXY`, state `UP`. This matters for the build, because adapters written with the Integration SDK run as containers on a collector and never on the analytics node.
- NSX integration: adapter kind `NSXTAdapter` with 50 object types and four instances, one per NSX Local Manager in the lab, including the two that are not in the federation. None of the 50 types is a Global Manager, a location or an RTEP tunnel, and no instance points at the Global Manager.
- Federation objects: the instance for `nsx-wld01-a` lists the edge cluster `fed-ec-a` and the RTEP IP pool, because those are local objects. A search of its inventory for the global objects (`fed-t0`, `fed-stretched`) returned nothing.
- A side finding: the instance for `nsx-shared-a`, the Local Manager of location `site-a-wld03`, exists but holds one object, itself. It is not collecting yet, so the built-in view of that location is empty. The pack is not affected, because it talks to the managers itself.

The built-in integration covers the per-location plumbing and stops at the federation boundary. The pack has to own the federation layer and link to the rest.

## Step 2: find the API calls that carry federation state

Every call below is a GET and was run against the live managers. The Global Manager serves its policy tree under `/global-manager/api/v1/global-infra`; a Local Manager exposes the objects it received under `/policy/api/v1/global-infra`.

**Is the federation connected?** Asked on the Global Manager:

```bash
curl -sk -u "$NSX_USER:$NSX_PW" https://nsx-gm-01a.site-a.vcf.lab/api/v1/sites/status
```

```json
{"remote_connections": [
  {"site_name": "site-a-wld01", "stubs": [{"address": "10.1.1.25", "connection_up": true}]},
  {"site_name": "site-a-wld03", "stubs": [{"address": "10.1.1.81", "connection_up": true}]}],
 "active_standby_sync_statuses": []}
```

The addresses in the answer, 10.1.1.25 and 10.1.1.81, are the node addresses of the two Local Managers. The same call on a Local Manager returns its view of the Global Manager and of the other location. Collected from every manager, it tells "the Global Manager lost a location" apart from "two locations lost each other".

**Is a stretched object realized everywhere?** One call per object, on the Global Manager:

```bash
curl -sk -u "$NSX_USER:$NSX_PW" \
 "https://nsx-gm-01a.site-a.vcf.lab/global-manager/api/v1/global-infra/realized-state/status?intent_path=/global-infra/segments/fed-stretched"
```

The answer carries a consolidated status and one status per location (`SUCCESS` for both here). The companion call `/global-infra/span?intent_path=...` lists the locations the object is supposed to reach.

**Are the cross-location tunnels up?** This lives on the Local Managers, per edge cluster and per edge node:

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

Two values did not turn up:

- **Latency between managers.** The Location Manager page shows it (3 ms and 2 ms in the screenshot above) and NSX raises an alarm on it (KB 345822), so NSX measures it. None of the fourteen paths tried returned it. The next attempt is to capture the call the page itself makes.
- **Firewall enforced on the host.** The lab has a live case: the rule in `fed-demo` is reported as realized on both Local Managers, while the hosts answer `firewall: disabled` to `nsxcli -c get firewall status` and no VM port has a filter. No manager API found so far exposes that host state.

Two facts about the lab itself become alerts: the Global Manager has no backup configured, and it runs alone with no standby.

## Step 3: the object model

A management pack starts with an object model: which things exist, what identifies them, and which object is the parent of which. The first draft has 12 types in four chains, shown in the diagram below. It still models edge nodes, gateways and segments itself, and step 5 comes back to that.

![Object model: Global Manager to Manager node; Location to Edge cluster to Edge node to RTEP tunnel; Tier-0 gateway to Tier-1 gateway to Segment, with BGP neighbor under Tier-0; Global policy to Rule](/images/nsx-federation-mp/object-model.svg)

Identifiers come from NSX, never from a manager address: the federation id and site id for managers and locations, the location and node name for edge nodes, the RTEP and peer addresses for tunnels and BGP sessions, the global policy path for gateways, segments and policies. An object keyed on an IP address or FQDN becomes a second object, with no history, the day a virtual IP changes or a Global Manager fails over.

## Step 4: metrics and alerts

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

The first release keeps the alert list short:

- a location lost its Global Manager
- two locations lost each other
- an inter-site BGP session or RTEP tunnel is down
- a stretched object is not realized somewhere in its span
- the sync failed
- an uplink BGP neighbor is down
- the Global Manager backup is missing or old
- no standby Global Manager is configured
- a location runs a different version from the Global Manager

Two of them would fire in the lab on day one: no backup and no standby Global Manager. The Global Manager and the locations run the same release, 9.1.1, which raises no alert.

## Step 5: reviewing the design for enterprise scale

The lab has two locations and one Global Manager node. The target is a pack a large enterprise could run: three-node Global Manager clusters, an active and a standby in different sites, many locations, thousands of stretched objects, several teams. The design was reviewed again on that basis, and eight points changed the plan.

1. **One Global Manager address is a design fault.** After a switchover the adapter would point at a demoted or dead manager at the moment it is needed. It needs the active and the standby address, has to detect the active one on every cycle, and has to keep collecting from the Local Managers when no Global Manager answers. Two managers both claiming the active role becomes a critical alert.
2. **Standby readiness moves to the first release.** For a team with a DR runbook, "is the standby in sync and can a switchover start" is the first question. The first draft of the plan ranked it last.
3. **Stop duplicating the built-in integration.** Edge nodes, gateways and segments already exist per Local Manager. The pack should attach its metrics and relationships to those objects where it can, and own only the federation types. As built, the pack links edge nodes and edge clusters to the built-in objects and keeps its own types ([part 2](/posts/nsx-federation-management-pack-install/)).
4. **Rules as objects do not scale.** Thousands of rules with four counters each, across twenty locations, give mostly zeros. The pack reports policy-level totals by default and creates rule objects only on request.
5. **Budget the API calls.** One realization call per stretched object does not fit a 5-minute cycle at scale, and NSX limits each client (100 requests per second and 40 concurrent by default). Collection has to run per Local Manager in parallel, with slower cycles for inventory than for status.
6. **Design against alert storms.** One lost location would raise the location alert, every tunnel, every BGP session and every stretched object. The alerts need a root-cause order and an awareness of maintenance windows.
7. **"Seconds since the last full sync" is the wrong signal.** The first draft planned that metric. A healthy federation runs on delta sync, and a full sync is the exception. The useful signals are sync status, queue occupancy and full-sync failures. As built, the pack keeps the age of the last full sync as a metric, with no alert, and alerts on sync status and full-sync failures.
8. **The pack has to monitor itself.** It reports its collection time, API call count and last success per location, so that "no data" is never read as "healthy".

One caution: twenty locations may be more than NSX 9.1 Federation supports. That has to be checked against the configuration maximums before the scale targets are written down.

## Step 6: the build route

VCF Operations offers two ways to build a pack: the no-code Management Pack Builder and the Integration SDK. The SDK was chosen because one adapter instance has to query several managers, discover the Local Managers from the Global Manager and follow a Global Manager failover. The derived values, such as a version gap or a storm-safe location state, also need code.

The cost is on the build side. An SDK adapter is a container image, so the build needs Python tooling and Docker, and the collector pulls the image from a container registry it can reach.

## Open at the end of part 1

- Install the SDK tooling and pick the registry.
- Decide how far the pack extends the built-in NSX objects.
- Build the collector core against the lab, with a mock for the cases the lab cannot show: a standby Global Manager, a switchover, twenty locations.
- Find the latency API and a way to read the host firewall state.

[Part 2](/posts/nsx-federation-management-pack-install/) covers the build, the install and what the pack monitors.

## Sources

- Lab: nested VCF 9.1.1, NSX 9.1.1 (Local Managers and Global Manager), VCF Operations 9.1.1. All API calls in this post were run on 17 November 2026.
- [VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction)
- Broadcom KB [324244](https://knowledge.broadcom.com/external/article/324244/split-brain-condition-after-a-global-man.html): split-brain after a Global Manager switchover
- Broadcom KB [378126](https://knowledge.broadcom.com/external/article/378126/nsx-apis-throws-error-message-client-ex.html): NSX API client rate limits
- Broadcom KB [345822](https://knowledge.broadcom.com/external/article/345822/gm-to-lm-network-latency-threshold-excee.html): Global Manager to Local Manager latency alarm
