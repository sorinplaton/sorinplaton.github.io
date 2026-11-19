---
title: "Monitoring NSX Federation in VCF Operations, part 2: building, installing and what the pack captures"
date: 2026-11-18
description: "Part 2 of the NSX Federation management pack for VCF Operations 9.1.1: the adapter built with the Integration SDK, the install step by step with screenshots (HTTPS registry, certificate trust on the cloud proxy, unsigned pack, account), and what the pack monitors on a two-location lab federation: 70 objects, 419 metrics, 53 alert definitions, four custom groups, three dashboards, five views and a report. It also covers a Global Manager switchover and how to send the alerts by email."
summary: "The adapter collects a two-location NSX 9.1 Federation in about 12 seconds. Running it inside VCF Operations 9.1.1 takes an image registry served over HTTPS, a cloud proxy that trusts the registry certificate, and the setting that allows an unsigned pack. This post documents each install step with screenshots, lists what the pack monitors (objects, metrics, alerts, groups, dashboards, views), tests a Global Manager switchover, and shows how to send the alerts by email."
tags: ["VCF 9.1.1", "NSX Federation", "VCF Operations", "Management pack", "Integration SDK", "Holodeck"]
categories: ["Field notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-federation-mp/object-model.svg"]
params:
  seoDescription: "Building and installing a custom NSX Federation management pack in VCF Operations 9.1.1: HTTPS registry, certificate trust, unsigned pack, and the objects, metrics, alerts, groups, dashboards and views it provides."
---

[Part 1](/posts/nsx-federation-management-pack-design/) ended with a design and no code. This post covers the build, the install in VCF Operations 9.1.1 with screenshots, what the pack monitors (objects, metrics, alerts, groups, dashboards, views), a Global Manager switchover, and how to send the alerts by email. The pack described here is version 1.4.0, and its source is on GitHub: [sorinplaton/vcf-ops-nsx-federation-mp](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp). The adapter itself took less time than the install. Three separate checks in VCF Operations stood between the `.pak` file and a running collector.

## The lab

A nested VCF 9.1.1 Holodeck. Two Global Managers, each a single node: `nsx-gm-01a` (`gm-site-a`) is active and `nsx-gm-01b` (`gm-site-b`) is its standby. They federate two locations, `site-a-wld01` and `site-a-wld03`, each with a two-node edge cluster. VCF Operations is 9.1.1.0 build 25795063 with one cloud proxy, `opscollector-01a`. The build runs on a Linux jump host with Docker, 10.0.6.27. The same host runs the image registry on port 5000.

The standby was deployed from the same 9.1.1.0100 appliance as the active Global Manager, in the Small size, on the cluster of `site-a-wld03`, with an address and a DNS record in the management network. It was registered on the active Global Manager through the API, as `gm-site-b` in STANDBY mode, with its address, admin credentials and certificate thumbprint (`PUT /global-manager/api/v1/global-infra/global-managers/gm-site-b`). The first full sync completed within minutes.

## Build the adapter

The pack uses the VCF Operations Integration SDK 1.3.1. The SDK tools (`mp-init`, `mp-test`, `mp-build`) need a Python version the jump host does not carry, so they run from a small tooling image:

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y docker.io git && rm -rf /var/lib/apt/lists/*
RUN pip install vmware-aria-operations-integration-sdk
```

The adapter is two Python files:

- `nsxclient.py`: a read-only NSX REST client with cursor paging, a call budget per manager, back-off on HTTP 429 and 503, a certificate check by SHA-256 fingerprint, and a small on-disk cache for slow-moving data.
- `adapter.py`: the object model, the collection logic and the events.

The adapter instance takes one or two Global Manager addresses and one credential. It discovers the Local Managers from the Global Manager, and it compares the certificate of each Local Manager with the thumbprint the Global Manager already holds for it. The check has two modes: "enforce" stops the collection of a manager whose certificate does not match, and "report" collects and raises an alert. The lab account runs in "report". The account also accepts a separate credential for the Local Managers.

Before any install, the collection ran against the live managers from the tooling container:

| Test | Result |
| --- | --- |
| Normal run, one Global Manager | 65 objects, 60 API calls, about 12 seconds |
| Second Global Manager address unreachable | Collection continues, one extra event for the unreachable address |
| No Global Manager reachable | Both Local Managers still collected from the cached location list, 37 objects, marked degraded. The cache lives in the adapter container and is lost when the container restarts |

An SDK pack is not self-contained. The `.pak` file carries the object definitions and a pointer to a container image. The cloud proxy pulls that image from a registry when the adapter instance starts.

`mp-build` builds the pack. Given a registry address in `host:port` form, it wrote Docker Hub into the pack. A short script rewrites two values, `REGISTRY` and `REPOSITORY`, in the `.conf` file inside `adapter.zip`:

```
REGISTRY=10.0.6.27:5000
REPOSITORY=/nsxfederation
DIGEST=sha256:4085...86e9
```

### Traps in the SDK

Five things cost time during the build. None of them is in the SDK documentation:

- **Events with a fault key are dropped.** VCF Operations shows an adapter event that carries a fault key only when the pack ships a matching symptom definition. Without one, the event disappears without an error on either side. The pack sends its events without a fault key.
- **Recommendation IDs.** A recommendation ID has to start with `Recommendation-df-`. The SDK schema does not check it, and without the prefix VCF Operations rejects the whole alert file while the install still reports success.
- **Schema files in the content folders.** The project template keeps `traversalSpecsSchema.xsd` in `content/traversalspecs`. The installer reads every file of that folder as a traversal specification, fails on the schema with `Traversal specs root tag should be "AdapterKind"`, and the inventory tree never loads. The schema files have to stay out of the pack.
- **`CollectResult.get_objects_by_type`** compares the adapter type of each object with the requested object type, so it always returns an empty list. The adapter filters the objects itself.
- **Dashboards.** The SDK documentation says to build a dashboard in the UI and export it. The pack generates its dashboard JSON with a script instead, from the same object model as the adapter. The format was worked out from dashboards exported through the suite API (`/api/content/operations/export`). In the file, an object type is a placeholder such as `resourceKind:id:3_::_`, and a table at the top of the file maps each placeholder to an adapter kind and an object type.

Alert definitions, views and dashboards can be tested before any pack build: the suite API imports them from a zip file (`POST /suite-api/api/content/operations/import`), with no pack install and no unsigned-pack switch.

## Install the pack

The install has five steps, given here in the order that works. The failures met in the lab are kept as symptoms under the step that prevents them.

### Step 1: serve the registry over HTTPS

The lab uses a `registry:2` container on the jump host. The cloud proxy only speaks HTTPS to a registry, so the container needs a certificate:

```bash
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout certs/registry.key -out certs/registry.crt -subj "/CN=fedmp-registry" \
  -addext "subjectAltName=IP:10.0.6.27"

docker run -d --name fedmp-registry -p 10.0.6.27:5000:5000 \
  -v registry-data:/var/lib/registry -v $PWD/certs:/certs:ro \
  -e REGISTRY_HTTP_TLS_CERTIFICATE=/certs/registry.crt \
  -e REGISTRY_HTTP_TLS_KEY=/certs/registry.key registry:2
```

The first registry in the lab served plain HTTP. With it, the adapter instance was created on the cloud proxy and went from `STARTING` to `FAILED` with an empty status message. The registry log showed no request at all. A packet capture on the registry port gave the reason. The cloud proxy opened a connection and sent a TLS hello to a registry that served plain HTTP. The cloud proxy does not fall back to HTTP, so the registry was recreated with the certificate above.

### Step 2: make the cloud proxy trust the registry certificate

HTTPS alone is not enough for a self-signed certificate. Without trust on the cloud proxy, the registry log shows this (10.0.6.33 is the lab router, which hides the cloud proxy behind NAT):

```
http: TLS handshake error from 10.0.6.33:56544: remote error: tls: bad certificate
```

The cloud proxy runs Docker, and Docker trusts a private registry when its certificate sits in a folder named after the registry:

```
/etc/docker/certs.d/10.0.6.27:5000/ca.crt
```

SSH is disabled on the cloud proxy and stayed disabled. The file was written through vCenter guest operations (`govc guest.run` with the appliance root account).

An upgrade or a redeploy of the cloud proxy may remove that file. A registry with a certificate from an authority the cloud proxy already trusts avoids the step.

### Step 3: install the unsigned pack

The pack is not signed by Broadcom. The upload through the appliance API fails:

```
pak_manager.pakfile.no_signature
The PAK file ... is not signed. It is missing required signature files signature.mf and signature.cert
```

VCF Operations 9 blocks unsigned packs by default, and the "Ignore the PAK file signature checking" box is hidden in the upload dialog until the block is lifted. The setting that lifts it is a switch in the admin console (`https://<vcf-operations>/admin`), under Administrator Settings, Security Settings: "Allow unsigned PAK installation". Broadcom documents it in KB 415279. The page looks like this before the switch is turned on:

![The admin console of VCF Operations, Administrator Settings, Security Settings: "Allow unsigned PAK installation" is deactivated, with the button that activates it](/images/nsx-federation-mp/ops-admin-unsigned-pak.webp)

There are two ways to install the pack. Both turn the switch on first and off again after.

#### Method 1: by hand in the UI

1. In the admin console, click "Activate unsigned PAK installation".
2. In VCF Operations, open Administration, Integrations, Repository and choose to add a pack.
3. Select the `.pak` file and tick both boxes: install even if already installed, and "Ignore the PAK file signature checking".
4. Upload, accept the warning about the unsigned file, and let the install finish.
5. Back in the admin console, deactivate unsigned PAK installation again.

#### Method 2: scripted

The appliance upload API (`/casa/upgrade/cluster/pak/reserved/operation/upload`) has no parameter that skips the signature check, even with the switch on. A script has to use the same calls as the UI, and the UI protects them with an anti-forgery token (`secureToken`) that a plain HTTP client never receives. In VCF Operations 9.1.1 the token lives in the page loaded under `/vcf-operations/plug/ops/`, inside the main UI.

The lab used a headless Chrome on the jump host, driven through the Chrome DevTools Protocol:

1. Chrome logs in to the admin console and calls `clusterManagement.action` with `mainAction=enableUPI`. The same page offers `getUPIStatus` and `disableUPI`.
2. Chrome logs in to the product UI. A short script reads the session cookies and the `secureToken` value from the page.
3. A Python script reuses that session for three calls:

```
POST admin/services/solution/upload?uploadId=<ms>&ignoreSignatureChecking=true&secureToken=<token>
     multipart field "solution" = the .pak file, forceUpload=true, forceContent=true
POST solution.action   mainAction=install
POST solution.action   mainAction=getInstallStatus      (until "completed")
```

4. Chrome calls `disableUPI`.

The product UI login (item 2) has to come after the admin console login (item 1), because the login to the admin console logs the product UI session out. A session token read before that login is no longer valid.

The switch cannot be turned off while the install runs. During an upgrade the admin console answered `Cluster is in PAK installing state` until the install had finished. A script has to retry the last call.

These calls are internal to the UI. They are unsupported and can change with any release. The switch applies to the whole instance. In the lab it was on only for the duration of the upload.

After the install, the adapter kind `NSXFederation` and the solution `iSDK_NSXFederation` appear in VCF Operations. The pack shows under Administration, Integrations, Repository with its version:

![Administration, Integrations, Repository filtered on "Federation": the NSX Federation pack, version 1.4.0, with one account](/images/nsx-federation-mp/ops-repository-pack.webp)

### Step 4: create the account

The adapter instance, called an account in the UI, is created on the cloud proxy. It takes one or two Global Manager addresses and one credential. With both addresses, the pack follows the active role when the Global Managers switch over. Two more settings are optional: the security policies whose rules become objects, and the link to the built-in NSX integration.

With the registry trusted, the cloud proxy pulled the image on the next start of the instance, and the instance reached `STARTED`, then `DATA_RECEIVING`. The Accounts tab shows the instance as "Collecting":

![Administration, Integrations, Accounts: the account "NSX Federation - nsx-gm-01a" with the status Collecting, on the cloud proxy opscollector-01a](/images/nsx-federation-mp/ops-account-collecting.webp)

### Step 5: wait three collection cycles

The data does not arrive at once. With a five-minute interval:

| Cycle | What VCF Operations stores |
| --- | --- |
| 1 | The objects and their relationships |
| 2 | Properties and events; metric keys are registered |
| 3 | Metrics |

A check two minutes after the first cycle shows objects with no metrics, which looks like a broken pack. The metrics appear about ten minutes after the start. Rates need two cycles, so they appear one cycle after the other metrics.

### Upgrade the pack

A new version is installed the same way, with the unsigned-pack switch. After an upgrade, the adapter instance has to be stopped and started. Without that it can stay in `FAILED`, although the cloud proxy has already pulled the new image. In the lab one upgrade needed a second start: the instance stayed stopped although the start call had returned success.

A change in how the adapter identifies an object creates a new object. The old one stays in the inventory without data until it is deleted through the suite API (`DELETE /api/resources/{id}`), which also deletes its history.

## What the pack monitors

### Objects

The pack creates 70 objects of 14 types in the lab. Part 1 planned 12 types. The enterprise-scale review added the federation itself, the sync channel and the realization of an object at a location, and rules became opt-in.

| Object type | Count in the lab | Examples of what is collected |
| --- | --- | --- |
| Federation | 1 | Active Global Managers, locations healthy and collected, RTEP rollups, API errors, managers over the call budget |
| Global Manager | 2 | Standby in sync, entries left to send to the standby, backup state, open alarms, days to the first certificate expiry |
| Global Manager node | 2 | Online state |
| Location | 2 | Connection to the Global Manager, collector reachability, nodes online, full sync failures, inter-site BGP sessions, alarms, version |
| Sync channel | 9 | Connection state between two managers |
| Edge cluster | 2 | Inter-site status, members, RTEP addresses |
| Edge node | 4 | RTEP tunnels, inter-site BGP sessions |
| RTEP tunnel | 8 | BGP state, packets, drops and blocked packets in each direction |
| Tier-0 BGP neighbor | 16 | State, prefixes in and out, messages, time established; 4 uplink sessions and 12 internal sessions between edges |
| Tier-0 gateway | 1 | Span, locations realized, locations in error |
| Tier-1 gateway | 3 | Same as the Tier-0 |
| Segment | 5 | Same, plus subnets and connected gateway |
| Global security policy | 1 | Span, realization, rules total and disabled |
| Stretched object at a location (realization) | 14 | Realized or not, status text |

Global firewall rule objects are opt-in per policy and were left off for this install.

The pack reports its own health on the federation object: calls made, calls throttled, errors, and whether a manager ran out of its call budget.

An edge node and its tunnels are identified by location and node name, so a redeployed edge keeps its object and its history.

In the inventory, the object types sit in a tree and each object has a summary page. The screenshot below shows a location:

![VCF Operations inventory: the NSX Federation object types in the tree, and the summary of location site-a-wld01 with its related objects, alarm counts, backup age and the version mismatch alert](/images/nsx-federation-mp/ops-inventory-location.webp)

The pack also ships an inventory tree. To open it, go to Inventory, click Detailed View, select the last tab (the puzzle-piece icon) and pick NSX Federation in the drop-down. The tree starts at the federation. Under it sit the segments, the Tier-1 and Tier-0 gateways, the global policy, the Global Managers and the two locations. A location holds its edge cluster and its sync channels:

![The NSX Federation inventory tree in VCF Operations: the federation, its segments, gateways and policy, and location site-a-wld01 opened on its edge cluster and two sync channels](/images/nsx-federation-mp/ops-inventory-tree.webp)

### Custom groups

Four custom groups come with the pack: all objects of the pack (70 in the lab), managers and locations (7), inter-site connectivity (39) and stretched objects (24). A group collects the alerts and badges of its members, and a policy or a notification rule can take it as its scope. The groups sit under Inventory, Custom Groups, Environment. Membership follows the object type, so a new location or a new tunnel joins its group without an edit. A group per location would need the name of each location, so the pack does not ship one.

![Custom group "NSX Federation - Managers and locations": the two Global Managers, the federation, the two locations and the two Global Manager nodes, with no open alert](/images/nsx-federation-mp/ops-custom-groups.webp)

### Metrics

The pack sends 419 metrics per cycle in the lab once the rates are available.

The metrics sit in groups, as in the built-in integrations: Sync, Connectivity, Certificates, Alarms, Rates and so on, 17 groups in all. The metric tree of an object stays short enough to read. The key of a metric carries its group, `sync|standby_in_sync` for example.

![Metrics tab of Global Manager gm-site-a: the groups Alarms, Certificates, Cluster, Connectivity and Sync, and the chart of "Standby Global Managers in sync" at 1](/images/nsx-federation-mp/ops-metric-groups.webp)

Eighteen metrics are flagged as key indicators, up to three per object type: the active Global Managers and the share of healthy locations on the federation, the offline nodes and the standby sync on a Global Manager, the connection and full sync of a location, BGP and drops on a tunnel, and so on. VCF Operations lists them first in the summary of the object:

![Summary of gm-site-a: the open backup alert and, on the right, the key indicators "Nodes offline 0" and "Standby Global Managers in sync 1"](/images/nsx-federation-mp/ops-key-attributes.webp)

NSX returns packets, bytes, drops and rule hits as running totals. The adapter keeps the counters of the previous cycle and adds rates: packets, drops and blocked packets per minute and bytes per second on RTEP tunnels, rule hits per minute on policies, realizations and rules. Units are declared: hours, days, seconds, bytes, packets and per-minute rates.

Three metrics are derived by the adapter, because a symptom compares a metric with a fixed value and cannot subtract two metrics: Global Managers not reachable, inter-site BGP sessions not established, locations not realized.

Seven figures cover the whole federation, on the federation object: locations healthy, as a count and a percentage; RTEP tunnels; tunnels whose BGP session is down; RTEP drops per minute over all tunnels; days to the first certificate expiry on any manager; open critical alarms on all managers. A super metric could compute some of them, but it does nothing until a policy enables it, so the adapter computes all seven. In the lab they read two of two locations healthy (100 %), eight tunnels, none down, no drops, 331.6 days to the first certificate expiry (a Local Manager certificate) and no critical alarm.

The table below gives the coverage of each object type. The Metrics column counts the metric keys defined for the type. Not every object sends every key (only policy realizations carry counters, for example), so the column multiplied by the object counts does not give 419.

| Object type | Metrics | Alert definitions | Dashboard |
| --- | --- | --- | --- |
| Federation | 21 | 5 | Federation Overview |
| Global Manager | 12 | 17 | Federation Overview |
| Global Manager node | 1 | None, covered by the cluster alert of the Global Manager | Related objects only |
| Location | 14 | 18 | Federation Overview |
| Sync channel | 1 | 1 | Inter-site Connectivity |
| Edge cluster | 4 | 1 | Inter-site Connectivity |
| Edge node | 4 | 1 | Inter-site Connectivity |
| RTEP tunnel | 17 | 3 | Inter-site Connectivity |
| Tier-0 BGP neighbor | 7 | 2 | Inter-site Connectivity |
| Stretched Tier-0, Tier-1, segment | 4 each | 1 each | Stretched Objects |
| Global security policy | 8 | 1 | Stretched Objects |
| Stretched object at a location | 6 | 1 | Stretched Objects |
| Global firewall rule (opt-in) | 5 | None | None |

### Alerts

The pack has 53 alert definitions, 67 symptoms and 27 recommendations. The content is one XML file in `content/alertdefs`. A script generates it from a table of symptoms, alerts and recommendations. A symptom is a condition on one metric, one property or one event. Two symptoms use a dynamic threshold. A condition on a property looks like this:

```xml
<SymptomDefinition adapterKind="NSXFederation" id="SymptomDefinition-NSXFederation-gm_backup_off"
                   name="Backup is not configured on the Global Manager" resourceKind="global_manager">
    <State severity="immediate">
        <Condition key="backup_enabled" operator="=" thresholdType="static" type="property"
                   value="False" valueType="string"/>
    </State>
</SymptomDefinition>
```

A condition on an event matches the text of the event with a regular expression:

```xml
<Condition eventMsg="NSX alarm rtep_bgp_down:(?s).*" eventSubType="5" eventType="11"
           operator="regex" type="msg_event"/>
```

The 53 definitions, by object type:

| Object type | Definitions | Alerts |
| --- | --- | --- |
| Federation | 5 | No active Global Manager; split-brain (two active); a Global Manager not reachable by the collector; no standby configured; partial collection (API call budget used) |
| Global Manager | 17 | Cluster degraded; backup missing, old or failed; certificate about to expire; standby not in sync; 13 NSX federation alarms (see below) |
| Location | 18 | Connection to the active Global Manager lost; Local Manager not reachable by the collector; certificate not trusted; full sync not completed; inter-site BGP sessions down; version differs from the Global Manager; backup missing or old; certificate about to expire; Local Manager cluster degraded; 9 NSX federation alarms |
| Sync channel | 1 | Channel down |
| Edge cluster | 1 | Inter-site status is not UP |
| Edge node | 1 | All inter-site BGP sessions down |
| RTEP tunnel | 3 | Inter-site BGP session down on the tunnel; more than 60 dropped packets per minute in one direction; traffic above its usual range (dynamic threshold) |
| Tier-0 BGP neighbor | 2 | Session down; uplink session established with no prefixes received |
| Tier-0, Tier-1, segment, global security policy | 1 each | Locations in error |
| Stretched object at a location | 1 | Not realized |

The edge node alert fires only when the node has lost all its inter-site sessions, because a single lost session raises the tunnel alert. "Stretched object is not realized at a location" is a Warning, because the error alerts on the object itself are Immediate.

Each alert also sets how it counts and how fast it reacts:

- **Badge.** Eight alerts lower the Risk badge instead of Health: both backup alerts, both certificate expiry alerts, the certificate not trusted, no standby, the version mismatch and the call budget. These conditions cause trouble later, and Health is kept for what is broken now.
- **Wait and cancel cycles.** Nine symptoms that can flap need two collection cycles, 10 minutes, to raise an alert and two to clear it: sync channel down; inter-site BGP sessions down and none established on the location; no inter-site session on the edge node; inter-site BGP down on the tunnel; tunnel drops, received and sent; Tier-0 BGP neighbor down; no prefixes received. The dynamic threshold and the standby sync wait three cycles.
- **Compliance subtype.** Four alerts carry the compliance subtype: no standby, both backup alerts and the version mismatch. The Compliance page of VCF Operations 9.1 shows a banner that the older Compliance feature is deprecated in VCF 9.1 and points to Security Posture Management. The subtype therefore only classifies the alerts, and no custom benchmark was built on it.
- **Thresholds.** The backup age (24 hours) and the certificate warning (30 days) are fixed in the symptoms. A VCF Operations policy can override the threshold of a symptom for another value.
- **Dynamic threshold.** The tunnel traffic alert compares the receive and send rates with the range VCF Operations learns for each tunnel. It needs days of history before it can fire.

Four alerts fire in the lab, from two definitions: the missing backup on both Global Managers and the version mismatch on both locations. All four describe the lab correctly and count against Risk. Apart from the alarm alerts tested below, no other definition has fired, because the lab has no such fault.

An alert from a definition carries its recommendation and the symptom that triggered it:

![Detail of the alert "Global Manager backup is missing, old or failed" on gm-site-a: description, recommendation and the symptom "Backup is not configured on the Global Manager"](/images/nsx-federation-mp/ops-alert-recommendation.webp)

#### The standby Global Manager

The active Global Manager carries two metrics about its standby: standby Global Managers in sync (1 when all are), and entries left to send. A standby counts as in sync when its full sync is complete and the delta sync runs, and as out of sync on a failure or an error. NSX 9.1 reports the normal state of a standby as ONGOING, together with a delta sync, a completed full sync and no entries left to send, so the status text alone cannot be the test. The alert "Standby Global Manager is not in sync" fires after three cycles and clears after two. In the lab the metric reads 1 with no entries left.

#### Switchover test

A planned switchover was run on 19 November, to see what the pack reports when the active role moves. The switchover is one call on the standby Global Manager, the API form of "Make Active" in Location Manager:

```
PATCH https://<standby-gm>/global-manager/api/v1/global-infra/global-managers/gm-site-b
{"display_name": "gm-site-b", "mode": "ACTIVE"}
```

The same call as a PUT with the full object was refused with `Global Manager can only be activated locally`. The PATCH with only the name and the mode was accepted. Progress shows in `GET /api/v1/sites/switchover-status` on the new active Global Manager, in six steps.

| Time | NSX | Pack |
| --- | --- | --- |
| 07:26:31 | Switchover to gm-site-b requested; precheck | |
| 07:27 | | Cycle during the precheck: gm-site-a active, one active Global Manager |
| 07:28 | gm-site-a becomes standby | |
| 07:29:10 | Switchover complete | |
| 07:32 | | First full cycle: gm-site-b active, gm-site-a standby, both reachable, both locations connected, standby in sync |
| 07:42:32 | Switchback to gm-site-a requested | |
| 07:44:35 | Switchback complete | |
| 07:52 | | gm-site-a active again, standby in sync |

Each switchover took about two minutes. The pack followed the active role in its first cycle after the change, with no edit to the account, because the account lists both addresses. No alert opened or closed during the test, and the four lab alerts stayed as they were. For about a minute during the precheck, the new active Global Manager listed both Global Managers as active. The pack still counted one active Global Manager and raised no split-brain alert.

The Federation Overview dashboard with gm-site-b active:

![The Federation Overview dashboard during the switchover test: gm-site-b selected, its scoreboard with Standby in sync 1, nodes online 1, nodes offline 0 and certificate expiry 822.6 days, and the same two open alert definitions](/images/nsx-federation-mp/ops-switchover-gm-b.webp)

The test showed one side effect: the standby sync metrics belong to the active Global Manager. After the switchover, the former active stops sending them, and VCF Operations keeps showing their last value. The scoreboard of the former active therefore still showed "Standby in sync 1" while it was the standby itself. The value left behind was 1, so no alert was raised. What a stale 0 would do to the alert was not tested.

A planned switchover never leaves the federation without an active Global Manager, so the alerts "no active Global Manager" and "Global Manager not reachable" did not fire, as expected. Testing them takes an unplanned failure of the active Global Manager.

#### NSX alarms as named alerts

The adapter forwards the federation alarms of the Global Manager, and those of each Local Manager, as events with the text `NSX alarm <event_type>: <summary>`. The built-in NSX integration uses the same form and matches it to give 381 alarm types an alert of their own.

The pack gives the 13 federation alarm types of NSX 9.1 their own symptom, alert and recommendation: split-brain between Global Managers; sync errors and warnings between the Global Managers, between the Global Manager and a location, and between locations; the two latency warnings; the sync queue above its threshold; a Local Manager restored during its configuration import; RTEP BGP down; RTEP connectivity lost. The Global Manager gets all 13. A location gets 9, because the four alarms between Global Managers do not apply to it. The latency alarms partly cover the open item on latency between managers: NSX raises one when the latency stays above its threshold for two minutes.

The built-in NSX integration has the same alarms, but only for the managers added to it as NSX accounts. In the lab those are the Local Managers, so the alarms of the Global Manager reach VCF Operations only through this pack.

The lab had no open federation alarm. To test the definitions, two events were posted through the suite API (`POST /api/events`) in the form the adapter sends: `gm_to_lm_synchronization_warning` on the Global Manager and `rtep_bgp_down` on location site-a-wld01. Both raised their named alert within one cycle, and no "Notification event" alert appeared next to them. The alert list of the federation, its Global Manager and its locations (taken before the standby was added):

![Alerts of the federation, its Global Manager and its locations: the two NSX alarm alerts from the test events next to the lab alerts of the time](/images/nsx-federation-mp/ops-alerts-130.webp)

The alarm alert carries the symptom, the text of the event and the recommendation:

![Detail of the alert "NSX alarm: RTEP BGP neighbor down" on site-a-wld01: description, recommendation, and the symptom "Local Manager raised NSX alarm rtep_bgp_down" with the text of the test event](/images/nsx-federation-mp/ops-alarm-alert.webp)

Posting the same events again with a cancel time cleared both alerts in the next cycle.

Some conditions without a definition still arrive as events: a federation alarm whose type has no definition, on a Local Manager or on the Global Manager, and every critical alarm of the Global Manager, up to 20 per manager. The other NSX alarms of a location are counted in its metrics and not forwarded. Such an event surfaces as a "Notification event" alert on the object, with no recommendation. A condition that has an alert definition on a metric or a property sends no event. Otherwise the same condition would show twice, once as the alert and once as a "Notification event".

A replacement of the lab edges gave a real example. The NSX alarms `rtep_bgp_down` and `rtep_connectivity_lost` showed up as alerts on both location objects. VCF Operations cancelled each one about 15 minutes later, with no operator action.

#### Compared with the built-in integrations

The 1,219 alert definitions and 1,991 symptom definitions on the lab's VCF Operations were compared with the pack. The way the pack counts, reacts and names alarms follows from this comparison:

| | Built-in NSX | Built-in vCenter | Built-in vSAN | NSX Federation pack |
| --- | --- | --- | --- | --- |
| Alert definitions | 423 | 185 | 225 | 53 |
| Main trigger | NSX alarms as events (381), properties | Properties (702), vCenter faults (107) | Faults (504) | Metric thresholds (33), NSX alarms as events (22), properties (10), dynamic thresholds (2) |
| Badge | 405 Health, 18 Risk | 129 Health, 55 Risk, 1 Efficiency | 206 Health, 15 Risk, 4 Efficiency | 45 Health, 8 Risk |
| Symptoms with wait or cancel cycles above 1 | None | 31 | None | 12 |
| Compliance alerts | 9 | 2 | None | 4 |

No super metric was defined on the system.

### Dashboards

Three dashboards sit in a folder named "NSX Federation":

| Dashboard | Content |
| --- | --- |
| Federation Overview | Open alerts on the pack's objects; a list of the federation, the Global Managers and the locations; key values, properties and related objects of the selected one |
| Inter-site Connectivity | Sync channels and edge nodes, RTEP tunnels, Tier-0 BGP neighbors; key values of the selected row |
| Stretched Objects | Stretched gateways, segments and policies with their span; the realization of each object at each location |

Every dashboard works the same way: a list drives a scoreboard, and a click on a scoreboard value draws its trend.

Federation Overview with gm-site-a selected. The scoreboard of a Global Manager starts with the standby sync, and the open alerts are the backup and the version mismatch:

![The Federation Overview dashboard: two open alert definitions (backup, version mismatch), the list with gm-site-a and gm-site-b, and the scoreboard of gm-site-a with Standby in sync 1, nodes offline 0 and certificate expiry 822.9 days](/images/nsx-federation-mp/ops-dashboard-standby.webp)

With the federation selected, the scoreboard ends with the federation rollups: locations healthy, RTEP BGP down, RTEP drops per minute, certificate expiry and critical alarms:

![The Federation Overview dashboard with the federation selected: the scoreboard shows API errors, call budget, locations healthy 100, RTEP BGP down 0, RTEP drops per minute 0, certificate expiry 331.6 days and critical alarms 0](/images/nsx-federation-mp/ops-dashboard-rollups.webp)

Inter-site Connectivity shows the rates of one RTEP tunnel:

![The Inter-site Connectivity dashboard: sync channels and edge nodes, RTEP tunnels, Tier-0 BGP neighbors and the rates of the selected RTEP tunnel](/images/nsx-federation-mp/ops-dashboard-connectivity.webp)

Stretched Objects, with the segment fed-stretched selected:

![The Stretched Objects dashboard: the stretched gateways, segments and policies, the realization per location, and the key values, properties and related objects of segment fed-stretched](/images/nsx-federation-mp/ops-dashboard-stretched.webp)

Two behaviours cost time after the import:

- An imported dashboard shows empty widgets at first. The object lists show every object of the instance and the scoreboards show nothing. A background job in VCF Operations completes the import a few minutes later, and the widgets then get their settings.
- The object lists keep the default columns. The added metric columns sit to the right, behind a scroll bar. A saved column layout in the file was not applied at import.

### Views and a report

The five list views cover locations, sync channels, RTEP tunnels, Tier-0 BGP neighbors and the realization of stretched objects per location. A report, "NSX Federation - Health report", puts them in one PDF. A view is an XML file. A script generates it, as for the alerts and the dashboards. The Locations view:

![The list view "NSX Federation - Locations": both locations with version, connection to the Global Manager, collector reachability, inter-site BGP sessions and full sync failures](/images/nsx-federation-mp/ops-view-locations.webp)

### Link to the built-in NSX integration

An option on the adapter instance, off by default, links each edge node and edge cluster to the object of the built-in NSX integration. Part 1 left open how far the pack extends the built-in NSX objects, and this option is the answer so far.

With the option on, the edge cluster of site-a-wld01 and its two edge nodes got the built-in Edge Cluster and Transport Node objects as children after one cycle. Their alerts, such as the built-in "RTEP Connectivity Lost", can then be listed from the federation object. The edges of site-a-wld03 got no link: the Local Manager of that location is not an NSX account in VCF Operations, so the built-in objects do not exist. The pack keeps its own tunnel and edge alerts for that reason. For a location whose Local Manager is not added to the built-in integration, they are the only coverage.

## Sending the alerts by email

The pack raises the alerts and VCF Operations sends them. That takes two settings: an outbound instance that knows the mail server, and a notification rule that picks the alerts. Both are under Administration, Configurations, in the Alerts group. The lab has no outbound instance and no mail server, so the forms below were opened in the UI and not saved, and no email was sent.

### 1. The outbound instance

1. Open Administration, Configurations, Outbound Settings and click Add.
2. Choose the plugin type "Standard Email Plugin" and give the instance a name.
3. Fill in SMTP Host and SMTP Port, the sender address and the sender name. Tick "Use Secure Connection" and "Requires Authentication" if the mail server asks for them. The form also has a credential and a receiver address.
4. Click Test, then Save.

The form, as opened in the lab:

![Create New Outbound Instance with the Standard Email Plugin: SMTP host and port, secure connection, sender address and name, credential and receiver address](/images/nsx-federation-mp/ops-email-plugin.webp)

The same list offers a Webhook, Slack, ServiceNow and SNMP trap plugin. The notification rule below works with any of them.

### 2. The notification rule

1. Open Administration, Configurations, Notifications and click Add. The wizard has five steps, items 2 to 6 below.
2. **Notification:** a name, for example "NSX Federation alerts by email".
3. **Define Criteria:** under Object Scope choose "Object Type" and select the object types of the NSX Federation adapter. Under Alert Scope, Category can stay on all alerts or be narrowed to "Alert Definition" to pick single definitions. Criticality limits the rule, for example to Immediate and Critical. "Notify On" selects which status changes of an alert send a message.
4. **Set Outbound Method:** the email plugin and the outbound instance created above.
5. **Select Payload Template:** the template that formats the message. Templates are managed under Payload Templates on the same Configurations page.
6. **Test Notification**, then Create.

The last three wizard steps (outbound method, payload template, test) are described from their names only. Without an outbound instance the lab could not go through them. The Define Criteria step looks like this:

![The Define Criteria step of a notification rule: object scope, alert scope with category, criticality and control state, and the status changes to notify on](/images/nsx-federation-mp/ops-notification-rule.webp)

A rule on the pack's object types, limited to Immediate and Critical, sends the alerts of those two levels by email. The version mismatch and the other Information and Warning alerts stay in the UI. A second rule without the criticality limit, sent to a team mailbox, covers them.

## What is not there yet

In the pack:

- A signature. Every install and upgrade needs the unsigned-pack switch.

An independent review of the code against what a large organisation would need found more gaps. None of them shows in a healthy two-location lab:

- **Failed calls read as empty answers.** When a secondary NSX call fails, the adapter treats the answer as empty. A failed status call can then read as "location not connected" and a failed backup call as "backup not configured", and each raises an alert. A failed call has to mean "unknown".
- **Certificate trust.** The fingerprint is checked on a separate connection before the API calls, and a manager without a recorded thumbprint is not checked. There is no mode that validates a certificate chain and a host name.
- **Account rights.** The lab used the NSX `admin` account. A read-only role was not tested or documented.
- **Scale.** The collection makes one realization call per stretched object in every cycle. It was measured on two locations only, and no mock of a large federation was built.
- **Alert storms.** When a location goes down, the alerts of its tunnels, BGP sessions, channels and realizations fire next to the location alert. Nothing groups them under one root cause, and there is no maintenance window.
- **Self-monitoring.** API errors, throttling and collection time are metrics, but only the call budget has an alert.
- **Several federations.** Nothing checks that the listed Global Managers belong to the same federation, and the groups and dashboards are not scoped per federation.
- **Coverage.** Not collected yet: Tier-0 failover and the active edge per location, Local Manager nodes, onboarding state, sync queue and latency as metrics, configuration maximums, upgrade state.
- **Engineering.** There are no unit tests, no pipeline, and no clean-up of objects deleted in NSX.

Open from part 1:

- Latency between managers as a metric. NSX measures it and shows it in its UI; the API that carries it was not found. The NSX latency alarms raise an alert.
- The firewall state of the hosts, the second open item from part 1.
- The mock for the cases the lab cannot show. It was not built.

Tests:

- Most alert definitions, which need faults the lab does not have. A planned switchover and switchback were tested. A failure of the active Global Manager, a standby out of sync and split-brain were not. The alarm alerts were tested with posted events, not with real NSX alarms.
- The dynamic threshold, which needs days of history.
- The report.
- The email notification, which needs a mail server the lab does not have.

## Sources

- Lab: nested VCF 9.1.1 Holodeck, NSX 9.1.1.0 (Local Managers) and 9.1.1.0100 (Global Managers), VCF Operations 9.1.1.0 build 25795063. Build, install and tests from 17 to 19 November 2026.
- Source of the pack: [github.com/sorinplaton/vcf-ops-nsx-federation-mp](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp), tag v1.4.0
- [VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction)
- Broadcom KB [415279](https://knowledge.broadcom.com/external/article/415279/error-no-signature-found-on-pak-file-wh.html): "No signature found on PAK file" while installing a management pack
- [Docker: verify repository client with certificates](https://docs.docker.com/engine/security/certificates/)
