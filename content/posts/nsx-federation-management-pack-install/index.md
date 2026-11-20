---
title: "Monitoring NSX Federation in VCF Operations, part 2: building, installing and what the pack captures"
date: 2026-11-18
description: "Part 2 of the NSX Federation management pack for VCF Operations 9.1.1: the adapter built with the Integration SDK, the install step by step with screenshots (HTTPS registry, certificate trust on the cloud proxy, unsigned pack, account), and what the pack monitors on a two-location lab federation: 72 objects, 447 metrics, 63 alert definitions, four custom groups, three dashboards, five views and a report. It also covers a Global Manager switchover and how to send the alerts by email."
summary: "The adapter collects a two-location NSX 9.1 Federation in 10 to 25 seconds. Running it inside VCF Operations 9.1.1 takes an image registry served over HTTPS, a cloud proxy that trusts the registry certificate, and the setting that allows an unsigned pack. This post documents each install step with screenshots, lists what the pack monitors (objects, metrics, alerts, groups, dashboards, views), tests a Global Manager switchover, and shows how to send the alerts by email."
tags: ["VCF 9.1.1", "NSX Federation", "VCF Operations", "Management pack", "Integration SDK", "Lab"]
categories: ["Field notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-federation-mp/ops-dashboard-stretched.webp"]
params:
  seoDescription: "Building and installing a custom NSX Federation management pack in VCF Operations 9.1.1: HTTPS registry, certificate trust, unsigned pack, and the objects, metrics, alerts, groups, dashboards and views it provides."
---

[Part 1](/posts/nsx-federation-management-pack-design/) ended with a design and no code. Writing the adapter took less time than installing it: three separate checks in VCF Operations stood between the `.pak` file and a working collection. This post covers the build, the install in VCF Operations 9.1.1 with screenshots, what the pack monitors (objects, metrics, alerts, groups, dashboards, views), a Global Manager switchover, and how to send the alerts by email.

The pack described here is version 1.7.8, and its source is on GitHub: [sorinplaton/vcf-ops-nsx-federation-mp](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp). [docs/FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.7.8/docs/FEATURES.md) lists every object type, metric, property and alert of the pack, with the condition and the recommendation of each alert.

## The lab

The lab runs VCF 9.1.1 with two Global Managers, each a single node: `nsx-gm-01a` (`gm-site-a`) is active and `nsx-gm-01b` (`gm-site-b`) is its standby. They federate two locations, `site-a-wld01` and `site-a-wld03`, each with a two-node edge cluster. VCF Operations is 9.1.1 with one cloud proxy, `opscollector-01a`. The build runs on a Linux jump host with Docker, 10.0.6.27. The same host runs the image registry on port 5000.

![The part 2 lab: the active Global Manager nsx-gm-01a and the standby nsx-gm-01b, the locations site-a-wld01 and site-a-wld03 with their Local Managers and two-node edge clusters linked by RTEP, VCF Operations with the cloud proxy opscollector-01a, and the jump host that builds the pack and serves its image through the lab router](/images/nsx-federation-mp/lab-topology.svg?v=6)

The standby was deployed from the same 9.1.1 appliance as the active Global Manager, in the Small size, on the cluster of `site-a-wld03`, with an address and a DNS record in the management network. It was registered on the active Global Manager through the API, as `gm-site-b` in STANDBY mode, with its address, admin credentials and certificate thumbprint (`PUT /global-manager/api/v1/global-infra/global-managers/gm-site-b`). The first full sync completed within minutes.

## Build the adapter

The figure below shows the path from the source code to the adapter running on the cloud proxy.

![Build and deployment of the pack: the GitHub repository is cloned on the jump host, the tooling container builds the image and the .pak, the image goes to the HTTPS registry on the jump host, the .pak is installed in VCF Operations with the unsigned PAK switch on, the account deploys the adapter on the cloud proxy, which pulls the image by digest through the lab router, reads the Global Managers and Local Managers over their REST API, and sends the data to the analytics node](/images/nsx-federation-mp/build-topology.svg?v=2)

### Jump host prerequisites

The jump host builds the pack, runs the tests and serves the image. Before the build it needs:

- **Linux with Docker Engine**, and a user in the `docker` group. The SDK tools, the tests and the image registry all run in containers.
- **`git`**, to clone the repository.
- **Python 3**, any recent version, for the scripts that run outside the tooling image: `tools/gen_*.py` (alerts, dashboards, views, groups) and `tools/repack.py`.
- **`openssl`**, to create the registry certificate (step 1).
- **Root access**, once, to copy that certificate to `/etc/docker/certs.d/<registry>/ca.crt`. The jump host's own Docker must trust the registry before it can push the image.
- **Outbound access** to GitHub, Docker Hub and PyPI, for the repository, the `python:3.11-slim` and `registry:2` images and the SDK package.
- **TCP 443 to every NSX manager**, the Global Managers and the Local Managers, for the local test run (`tools/localrun.py`).
- **The registry port open to the cloud proxy**, 5000 in the lab. Here the cloud proxy reaches it through the lab router.

Two more tools are only needed for some steps, and can sit on another host:

- `govc` and access to the vCenter that runs the cloud proxy VM, to copy the certificate to the cloud proxy (step 2).
- `google-chrome`, `node` and the Python `requests` module, for the scripted install of the unsigned pack (optional, see docs/INSTALL.md in the repository), with HTTPS access to VCF Operations.

### The tooling image and the adapter code

The pack uses the VCF Operations Integration SDK 1.3.1. The SDK tools (`mp-init`, `mp-test`, `mp-build`) need a Python version the jump host does not carry, so they run from a small tooling image:

*Found in: `tooling/Dockerfile` in the repository, built on the jump host.*

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends docker.io git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir vmware-aria-operations-integration-sdk pytest
WORKDIR /work
```

The adapter code is in two Python files:

- `nsxclient.py`: a read-only NSX REST client with cursor paging, a call budget per manager, back-off on HTTP 429 and 503, a certificate check by SHA-256 fingerprint, and a small on-disk cache for slow-moving data.
- `adapter.py`: the object model, the collection logic and the events.

The adapter instance, which the VCF Operations UI calls an account, takes one or two Global Manager addresses and one credential (a second credential for the Local Managers is optional). It discovers the Local Managers from the Global Manager, and it compares the certificate of each Local Manager with the thumbprint the Global Manager already holds for it. A Global Manager is trusted by the certificate accepted on the account in VCF Operations. Once a certificate is checked, every connection of the cycle is bound to its fingerprint, so the credentials are not sent to a server that presents another certificate.

One account monitors one federation. A listed Global Manager that answers for another federation is reported and left out, and an optional setting names the federation object when VCF Operations monitors several.

### The pack file and its image

An SDK pack is not self-contained. The `.pak` file carries the object definitions and a pointer to a container image. The cloud proxy pulls that image from a registry when the adapter instance starts.

`mp-build` builds the pack and pushes the adapter image to the registry, so the registry of install step 1 must be running, and trusted by the jump host's Docker, before the build. The full command is in step 4 of [docs/INSTALL.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/main/docs/INSTALL.md). When the registry address is given as `host:port`, `mp-build` writes Docker Hub into the pack instead of that address. A short script, `tools/repack.py`, rewrites two values, `REGISTRY` and `REPOSITORY`, in the `.conf` file inside `adapter.zip`:

*Found in: the `.conf` file inside `adapter.zip` in the `.pak`, rewritten by `tools/repack.py` on the jump host.*

```
REGISTRY=<jump-host-ip>:5000
REPOSITORY=/nsxfederation
DIGEST=sha256:dd80...b4cb
```

## Install the pack

The install has five steps, given here in the order that works. The failures met in the lab are kept as symptoms under the step that prevents them. The same steps, as a checklist with every command and the machine it runs on, are in [docs/INSTALL.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/main/docs/INSTALL.md).

### Step 1: serve the registry over HTTPS

The cloud proxy pulls the adapter image from a registry, and it only speaks HTTPS to it. In production the image goes to the registry already in place (Harbor, for example). When its certificate comes from an authority the cloud proxy already trusts, step 2 is not needed. The lab has no such registry, so it runs a `registry:2` container on the jump host with a self-signed certificate:

*Run on: the jump host.*

```bash
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout certs/registry.key -out certs/registry.crt -subj "/CN=fedmp-registry" \
  -addext "subjectAltName=IP:<jump-host-ip>"

docker run -d --name fedmp-registry -p <jump-host-ip>:5000:5000 \
  -v registry-data:/var/lib/registry -v $PWD/certs:/certs:ro \
  -e REGISTRY_HTTP_TLS_CERTIFICATE=/certs/registry.crt \
  -e REGISTRY_HTTP_TLS_KEY=/certs/registry.key registry:2
```

The first registry in the lab served plain HTTP. With it, the adapter instance was created on the cloud proxy and went from `STARTING` to `FAILED` with an empty status message. The registry log showed no request at all. A packet capture on the registry port showed why: the cloud proxy opened a connection and sent a TLS hello. It does not fall back to HTTP, so the registry was recreated with the certificate above.

### Step 2: make the cloud proxy trust the registry certificate

This step applies to a registry whose certificate the cloud proxy does not trust yet, such as the self-signed one of the lab. Without that trust, the registry log shows this (10.0.6.33 is the lab router, which hides the cloud proxy behind NAT):

*Output: the log of the registry container on the jump host.*

```
http: TLS handshake error from 10.0.6.33:56544: remote error: tls: bad certificate
```

The cloud proxy runs Docker, and Docker trusts a private registry when its certificate sits in a folder named after the registry:

*Found on: the cloud proxy `opscollector-01a`.*

```
/etc/docker/certs.d/<jump-host-ip>:5000/ca.crt
```

SSH is disabled on the cloud proxy and stayed disabled. The file was written through vCenter guest operations (`govc guest.run` with the appliance root account).

An upgrade or a redeploy of the cloud proxy may remove that file.

### Step 3: install the unsigned pack

The pack is not signed by Broadcom. The upload through the appliance API fails:

*Output: the response of the VCF Operations appliance API to an upload from the jump host.*

```
pak_manager.pakfile.no_signature
The PAK file ... is not signed. It is missing required signature files signature.mf and signature.cert
```

VCF Operations 9 blocks unsigned packs by default, and the "Ignore the PAK file signature checking" box is hidden in the upload dialog until the block is lifted. The setting that lifts it is a switch in the admin console (`https://<vcf-operations>/admin`), under Administrator Settings, Security Settings: "Allow unsigned PAK installation". Broadcom documents it in KB 415279. The page looks like this before the switch is turned on:

![The admin console of VCF Operations, Administrator Settings, Security Settings: "Allow unsigned PAK installation" is deactivated, with the button that activates it](/images/nsx-federation-mp/ops-admin-unsigned-pak.webp)

The install turns the switch on first and off again after:

1. In the admin console, click "Activate unsigned PAK installation".
2. In VCF Operations, open Administration, Integrations, Repository and choose to add a pack.
3. Select the `.pak` file and tick both boxes: install even if already installed, and "Ignore the PAK file signature checking".
4. Upload, accept the warning about the unsigned file, and let the install finish.
5. Back in the admin console, deactivate unsigned PAK installation again. The console refuses while the install is still running.

The repository also holds a scripted version of these steps, `install/upi.sh` and `install/install_unsigned.sh`; [docs/INSTALL.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/main/docs/INSTALL.md) describes it.

After the install, the adapter kind `NSXFederation` and the solution `iSDK_NSXFederation` appear in VCF Operations. The pack shows under Administration, Integrations, Repository with its version:

![Administration, Integrations, Repository: the NSX Federation pack, version 1.7.8, with one account](/images/nsx-federation-mp/ops-repository-pack.webp)

### Step 4: create the account

Create the account under Administration, Integrations, Accounts, as an account of the type NSX Federation, with the cloud proxy as its collector. It takes one or two Global Manager addresses and a credential. With both addresses, the pack follows the active role when the Global Managers switch over. The other settings are optional: the security policies whose rules become objects, how the pack works with the built-in NSX integration (links and alarm alerts, both automatic by default), and the deletion of objects that left NSX (off by default). [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.7.8/docs/OPERATIONS.md) describes each setting.

Validate the connection and accept the Global Manager certificates when the account is saved. The certificate check has two modes: "enforce" stops the collection of a manager whose certificate does not match or that has no trusted certificate on record, and "report" collects and raises an alert. The default is "enforce": without an accepted certificate the managers are not collected.

The account also accepts a separate credential for the Local Managers. The pack only reads from NSX, so a read-only account is enough. In the lab, the account logs in with NSX's built-in `audit` user on the two Global Managers and the two federated Local Managers, and collects the same objects as with `admin`, with no failed call.

With the registry trusted, the cloud proxy pulls the image when the instance starts, and the instance goes to `STARTED`, then `DATA_RECEIVING`. The Accounts tab shows the instance as "Collecting":

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

An upgrade has three steps:

1. Install the new version the same way, with the unsigned-pack switch.
2. Stop and start the account, so that the adapter instance runs the new image.
3. Check that the account reaches Collecting, and start it again if it stays stopped.

Metrics return after three cycles and rates after four. A symptom definition that the new version no longer ships stays in VCF Operations unused; delete it under Symptom Definitions.

A change in how the adapter identifies an object creates a new object. The old one stays in the inventory without data until it is deleted through the suite API (`DELETE /api/resources/{id}`), which also deletes its history.

## What the pack monitors

The complete list of objects, metrics and alerts is in [FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.7.8/docs/FEATURES.md).

### Objects

The pack defines 15 object types. In the lab it creates 72 objects of 14 types. The fifteenth type, the global firewall rule, is opt-in per policy and was left off for this install.

| Object type | Count in the lab | Examples of what is collected |
| --- | --- | --- |
| Federation | 1 | Active Global Managers, locations healthy and collected, RTEP rollups, API errors, managers over the call budget |
| Global Manager | 2 | Standby in sync and how long it has been out of sync, entries left to send, last full sync, version against the active Global Manager, upgrade status, backup state, open alarms, days to the first certificate expiry |
| Manager node | 4 | Online state of each Global Manager and Local Manager node |
| Location | 2 | Connection to the Global Manager, replication flows, last seen by the Global Manager, onboarding status, collector reachability and time since the last collection, nodes online, full sync failures and duration, inter-site BGP sessions, alarms, version, upgrade status |
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

The pack reports its own health on the federation object: calls made, calls throttled, errors, and whether a manager ran out of its call budget.

An edge node is identified by location and node name, and each of its tunnels by that node and its two RTEP addresses, so a redeployed edge keeps its objects and their history.

In the inventory, the object types sit in a tree and each object has a summary page. The screenshot below shows a location:

![VCF Operations inventory: the NSX Federation object types in the tree, and the summary of location site-a-wld01 with its related objects, its key indicators and the alert on a paused NSX upgrade](/images/nsx-federation-mp/ops-inventory-location.webp)

The pack also ships an inventory tree. To open it, go to Inventory, click Detailed View, select the last tab (the puzzle-piece icon) and pick NSX Federation in the drop-down. The tree starts at the federation. Under it sit the segments, the Tier-1 and Tier-0 gateways, the global policy, the Global Managers and the two locations. A Global Manager holds its manager node and its sync channels. A location holds its manager node, its sync channels and its edge cluster, with the edge nodes and their tunnels. A Tier-0 holds its BGP neighbors, and each stretched object holds its realization per location:

![The NSX Federation inventory tree in VCF Operations: the federation, its segments, gateways and policy, and location site-a-wld01 opened on its manager node, its sync channels and its edge cluster](/images/nsx-federation-mp/ops-inventory-tree.webp)

### Custom groups

Four custom groups come with the pack: all objects of the pack (72 in the lab), managers and locations (9), inter-site connectivity (39) and stretched objects (24). A group collects the alerts and badges of its members, and a policy or a notification rule can take it as its scope. The groups sit under Inventory, Custom Groups, Environment. Membership follows the object type, so a new location or a new tunnel joins its group without an edit.

![Custom group "NSX Federation - Managers and locations": the two Global Managers, the federation, the two locations and the four manager nodes](/images/nsx-federation-mp/ops-custom-groups.webp)

### Metrics

The pack sends 447 metrics per cycle in the lab once the rates are available.

The metrics sit in groups, as in the built-in integrations: Sync, Connectivity, Certificates, Alarms, Rates and so on, 17 groups in all. The metric tree of an object stays short enough to read. The key of a metric carries its group, `sync|standby_in_sync` for example.

![Metrics tab of Global Manager gm-site-a: the groups Alarms, Certificates, Cluster, Connectivity and Sync, and the chart of "Standby sync healthy (1 = in sync)" at 1](/images/nsx-federation-mp/ops-metric-groups.webp)

Eighteen metrics are flagged as key indicators, up to three per object type: the active Global Managers and the share of healthy locations on the federation, the offline nodes and the standby sync on a Global Manager, the connection and full sync of a location, BGP and drops on a tunnel, and so on. VCF Operations lists them first in the summary of the object:

![Summary of gm-site-a: the open backup alert and, on the right, the key indicators "Nodes offline 0" and "Standby sync healthy (1 = in sync) 1"](/images/nsx-federation-mp/ops-key-attributes.webp)

NSX returns packets, bytes, drops and rule hits as running totals. The adapter keeps the counters of the previous cycle and adds rates: packets, drops and blocked packets per minute and bytes per second on RTEP tunnels, rule hits per minute on policies, realizations and rules. Units are declared: seconds, minutes, hours, days, milliseconds, bytes, bytes per second, packets, calls, errors, percentages and per-minute rates.

Three metrics are derived by the adapter, because a symptom compares a metric with a fixed value and cannot subtract two metrics: Global Managers not reachable, inter-site BGP sessions not established, locations not realized.

Seven figures cover the whole federation, on the federation object: locations healthy, as a count and a percentage; RTEP tunnels; tunnels whose BGP session is down; RTEP drops per minute over all tunnels; days to the first certificate expiry on any manager; open critical alarms on all managers. A super metric could compute some of them, but it does nothing until a policy enables it, so the adapter computes all seven. In the lab they read two of two locations healthy (100%), eight tunnels, none down, no drops, 330.9 days to the first certificate expiry (a Local Manager certificate) and no critical alarm.

Each location also carries what the Location Manager page of NSX shows for it: the state of its three replication flows with the Global Manager, when the Global Manager last saw it, the round-trip time when NSX reports one, and its onboarding status. The pack makes the same call as that page: `GET /api/v1/federation/replication/gm/sites/<site-id>/status` on the active Global Manager. In the lab the three flows read SYNCHRONIZING and the round-trip time is empty.

The table below gives the coverage of each object type. The Metrics column counts the metric keys defined for the type. Not every object sends every key (only policy realizations carry counters, for example), so the column multiplied by the object counts does not give 447.

| Object type | Metrics | Alert definitions | Dashboard |
| --- | --- | --- | --- |
| Federation | 28 | 9 | Federation Overview |
| Global Manager | 14 | 20 | Federation Overview |
| Manager node | 1 | None, covered by the cluster alert of its Global Manager or location | Related objects only |
| Location | 21 | 21 | Federation Overview |
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

The pack has 63 alert definitions, 98 symptoms and 34 recommendations. Eleven recommendations end with a link to the Broadcom KB article or documentation page for the fault. The content is one XML file in `content/alertdefs`, which a script generates from a table of symptoms, alerts and recommendations. A symptom is a condition on one metric, one property or one event, and two symptoms use a dynamic threshold. A condition on a property looks like this:

*Found in: the alert file of the pack, in `content/alertdefs`.*

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

*Found in: the alert file of the pack, in `content/alertdefs`.*

```xml
<Condition eventMsg="NSX alarm rtep_bgp_down:(?s).*" eventSubType="5" eventType="11"
           operator="regex" type="msg_event"/>
```

The 63 definitions, by object type:

| Object type | Definitions | Alerts |
| --- | --- | --- |
| Federation | 9 | No active Global Manager; split-brain (two active); a Global Manager not reachable by the collector; no standby configured; partial collection (API call budget used); collection with data gaps; collection slow or throttled; collection without a Global Manager; a listed Global Manager of another federation |
| Global Manager | 20 | Cluster degraded; backup missing, old or failed; certificate about to expire; standby not in sync; no role in the federation (mode NONE); version differs from the active Global Manager; NSX upgrade in progress, paused or failed; 13 NSX federation alarms (see below) |
| Location | 21 | Connection to the active Global Manager lost; Local Manager not reachable by the collector; certificate not trusted; full sync not completed; inter-site BGP sessions down; NSX release differs from the Global Manager; backup missing, old or failed; certificate about to expire; Local Manager cluster degraded; a replication flow in error; NSX release newer than the Global Manager; NSX upgrade in progress, paused or failed; 9 NSX federation alarms |
| Sync channel | 1 | Channel down |
| Edge cluster | 1 | Inter-site status is not UP |
| Edge node | 1 | All inter-site BGP sessions down |
| RTEP tunnel | 3 | Inter-site BGP session down on the tunnel; more than 60 dropped packets per minute in one direction; traffic above its usual range (dynamic threshold) |
| Tier-0 BGP neighbor | 2 | Session down; uplink session established with no prefixes received |
| Tier-0, Tier-1, segment, global security policy | 1 each | Locations in error |
| Stretched object at a location | 1 | Not realized |

The edge node alert fires only when the node has lost all its inter-site sessions, because a single lost session raises the tunnel alert. "Stretched object is not realized at a location" is a Warning, because the error alerts on the object itself are Immediate.

Each alert also sets which badge it lowers and how fast it reacts:

- **Badge.** Fourteen alerts lower the Risk badge instead of Health: both backup alerts, both certificate expiry alerts, the certificate not trusted, no standby, the release mismatch, the call budget, the data gaps, the slow or throttled collection, the Global Managers on different versions, the location ahead of the Global Manager and both upgrade alerts. These conditions cause trouble later, and Health is kept for what is broken now.
- **Wait and cancel cycles.** Ten symptoms that can flap need two collection cycles, 10 minutes, to raise an alert and two to clear it: a replication flow in error; sync channel down; inter-site BGP sessions down and none established on the location; no inter-site session on the edge node; inter-site BGP down on the tunnel; tunnel drops, received and sent; Tier-0 BGP neighbor down; no prefixes received. The data gaps and the slow collection also wait two cycles, and so do a full sync that did not complete and a stretched object that is not realized, so a sync or a change in progress raises nothing. The dynamic threshold, the standby sync and the throttling wait three. The outage symptoms clear after three cycles (see One alert for a location outage).
- **Compliance subtype.** Six alerts carry the compliance subtype: no standby, both backup alerts, the Global Managers on different versions, and both release alerts of a location. The Compliance page of VCF Operations 9.1 shows a banner that the older Compliance feature is deprecated in VCF 9.1 and points to Security Posture Management, so the subtype only classifies the alerts.
- **Thresholds.** The backup age (24 hours) and the certificate warning (30 days) are fixed in the symptoms. A VCF Operations policy can override the threshold of a symptom for another value.
- **Dynamic threshold.** The tunnel traffic alert compares the receive and send rates with the range VCF Operations learns for each tunnel. It needs days of history before it can fire.

Three alerts fire in the lab, from two definitions: the missing backup on both Global Managers, and a paused NSX upgrade on the Local Manager of site-a-wld01, whose upgrade coordinator holds a target version it has not applied. All three describe the lab correctly and count against Risk. The other definitions have nothing to report on this federation; the section Tests in the lab lists what was verified and how.

An alert from a definition carries its recommendation and the symptom that triggered it:

![Detail of the alert "Global Manager backup is missing, old or failed" on gm-site-a: description, recommendation and the symptom "Backup is not configured on the Global Manager"](/images/nsx-federation-mp/ops-alert-recommendation.webp)

#### NSX alarms as named alerts

The adapter forwards the federation alarms of the Global Manager, and those of each Local Manager, as events with the text `NSX alarm <event_type>: <summary>`. The built-in NSX integration uses the same form and matches it to give 381 alarm types an alert of their own.

The pack gives the 13 federation alarm types of NSX 9.1 their own symptom, alert and recommendation: split-brain between Global Managers; sync errors and warnings between the Global Managers, between the Global Manager and a location, and between locations; the two latency warnings; the sync queue above its threshold; a Local Manager restored during its configuration import; RTEP BGP down; RTEP connectivity lost. The Global Manager gets all 13. A location gets 9, because the four alarms between Global Managers do not apply to it. NSX raises a latency alarm when the latency between two managers stays above its threshold for two minutes.

The built-in NSX integration has the same alarms, but only for the managers added to it as NSX accounts. The alarms of the Global Manager reach VCF Operations only through this pack. For a Local Manager, the built-in integration and the pack would each raise an alert for the same alarm. The adapter therefore asks VCF Operations which NSX clusters the built-in integration monitors, and leaves the federation alarms of those Local Managers to it. In the lab, site-a-wld01 has an account in the built-in integration that collects, and the built-in account of site-a-wld03 exists but does not collect: a property on each location reads "the built-in NSX integration" for the first and "this pack" for the second. A setting forces either behaviour.

Some conditions without a definition still arrive as events: a federation alarm whose type has no definition, on a Local Manager or on the Global Manager, and every critical alarm of the Global Manager, up to 20 per manager. Such an event surfaces as a "Notification event" alert on the object, with no recommendation. The other NSX alarms of a location are counted in its metrics and not forwarded. A condition that has an alert definition on a metric or a property sends no event, so it never shows twice, once as the alert and once as a "Notification event".

### Dashboards

Three dashboards sit in a folder named "NSX Federation":

| Dashboard | Content |
| --- | --- |
| Federation Overview | Open alerts on the pack's objects; a list of the federation, the Global Managers and the locations; key values, properties and related objects of the selected one |
| Inter-site Connectivity | Sync channels and edge nodes, RTEP tunnels, Tier-0 BGP neighbors; key values of the selected row |
| Stretched Objects | Stretched gateways, segments and policies with their span; the realization of each object at each location |

In every dashboard a list drives a scoreboard. In Federation Overview and Inter-site Connectivity, a click on a scoreboard value draws its trend.

Federation Overview with gm-site-a selected. The scoreboard of a Global Manager starts with the standby sync, and the open alerts are the backup and the paused upgrade:

![The Federation Overview dashboard: two open alert definitions (backup, paused upgrade), the list with gm-site-a and gm-site-b, and the scoreboard of gm-site-a with Standby in sync 1, nodes offline 0 and certificate expiry](/images/nsx-federation-mp/ops-dashboard-standby.webp)

With the federation selected, the scoreboard ends with the federation rollups: locations healthy, RTEP BGP down, RTEP drops per minute, certificate expiry and critical alarms:

![The Federation Overview dashboard with the federation selected: the scoreboard shows API errors, call budget, locations healthy 100, RTEP BGP down 0, RTEP drops per minute 0, certificate expiry 330.9 days and critical alarms 0](/images/nsx-federation-mp/ops-dashboard-rollups.webp)

Inter-site Connectivity shows the rates of one RTEP tunnel:

![The Inter-site Connectivity dashboard: sync channels and edge nodes, RTEP tunnels, Tier-0 BGP neighbors and the rates of the selected RTEP tunnel](/images/nsx-federation-mp/ops-dashboard-connectivity.webp)

Stretched Objects, with the segment fed-stretched selected:

![The Stretched Objects dashboard: the stretched gateways, segments and policies, the realization per location, and the key values and properties of segment fed-stretched](/images/nsx-federation-mp/ops-dashboard-stretched.webp)

### Views and a report

The five list views cover locations, sync channels, RTEP tunnels, Tier-0 BGP neighbors and the realization of stretched objects per location. A report, "NSX Federation - Health report", puts them in one PDF. The five views and the report sit in one XML file, which a script generates, as for the alerts and the dashboards. The Locations view:

![The list view "NSX Federation - Locations": both locations with version, connection to the Global Manager, collector reachability, inter-site BGP sessions and full sync failures](/images/nsx-federation-mp/ops-view-locations.webp)

### Link to the built-in NSX integration

The adapter links each edge node and edge cluster to the object of the built-in NSX integration, for every Local Manager that integration monitors. A setting turns the links off or forces them.

In the lab, the edge cluster of site-a-wld01 and its two edge nodes have the built-in Edge Cluster and Transport Node objects as children. Their alerts, such as the built-in "RTEP Connectivity Lost", can then be listed from the federation object. The edges of site-a-wld03 got no link: the built-in account of that Local Manager does not collect, so the built-in objects do not exist. The pack keeps its own tunnel and edge alerts for that reason. For a location whose Local Manager is not added to the built-in integration, they are the only coverage.

## How the collection behaves

### Failed calls are reported as unknown

Most of the collection is made of secondary calls: cluster status, alarms, backups, certificates, sync status. When one of them fails, with an HTTP error or a timeout, the adapter sends nothing for the values that depend on it. A failed status call does not read as "location not connected", and a failed backup call does not read as "backup not configured". VCF Operations keeps the last known value, and no alert fires on data that was never received.

The federation object counts these calls in the metric "NSX API calls without an answer" and lists them in a property, with the manager, the call and the HTTP status. Four alerts watch the collection itself: data gaps for two cycles, a collection that takes more than four minutes or is throttled by NSX, a collection without any Global Manager, and a manager that used its whole call budget.

A call that times out or loses its connection is retried once before it counts as failed. A manager that times out three times in a row, or keeps answering HTTP 429 or 503, is skipped for the rest of the cycle. A Local Manager whose cluster status call fails together with most of its other calls is reported as not reached. The adapter asks a Local Manager for the statistics of a policy only when that location is in the known span of the policy. If the policy is realized there and the statistics call returns 404, the call counts as failed and the counters are not sent for that cycle. If the span is not known yet, a 404 means the policy does not exist on that Local Manager, and the call is not counted as failed.

### One alert for a location outage

A location that loses its connection to the Global Manager takes many objects with it: the tunnels and BGP sessions of the other locations towards it, the sync channels, and every stretched object that spans it. Each of them has an alert, and all of them would fire next to the alert of the location.

While the active Global Manager reports a location as not connected, the adapter writes the name of that location to a property, "Locations in outage", on the objects that depend on it: the location, its edge cluster, edge nodes and BGP neighbors, the tunnels and sync channels with one end there, the stretched objects whose span includes it with their realization there, and the Global Managers. A fault at another location still raises its alert. Thirteen alert definitions require that this property reads "none", and so do the alarm alerts, except the alarms between Global Managers. They stay quiet during the outage, and "Location lost its connection to the active Global Manager" is the alert to act on. In the alert XML this is a second symptom with `negateCondition="true"`:

*Found in: the alert file of the pack, in `content/alertdefs`.*

```xml
<SymptomSet applyOn="self" operator="and">
    <Symptom ref="SymptomDefinition-NSXFederation-seg_error"/>
    <Symptom ref="SymptomDefinition-NSXFederation-outage_segment" negateCondition="true"/>
</SymptomSet>
```

The outage symptom clears three cycles after the location is back, at least one cycle after the symptoms it keeps quiet, so the end of an outage raises no alert either.

The sync channels have a second property of the same kind. The channels of a Global Manager that does not answer, or of every Global Manager when none is active, stay quiet behind the alert on the federation.

A second, unrelated fault on the objects of the location in outage raises no alert while the outage lasts. Its metrics and properties are still collected.

### The standby Global Manager

Four metrics describe the standby: sync healthy (1 when in sync), entries left to send, minutes out of sync and hours since the last full sync. The active Global Manager carries them for all its standbys, and each standby carries its own sync state. A standby counts as in sync when NSX reports it in sync, or when a delta sync runs after a completed full sync. Any other state counts as out of sync. NSX 9.1 reports the normal state of a standby as ONGOING, together with a delta sync, a completed full sync and no entries left to send, so the status text alone cannot be the test. The alert "Standby Global Manager is not in sync" fires after three cycles and clears after two. In the lab the metric reads 1 with no entries left.

### Versions and upgrades

NSX Federation is upgraded in a fixed order: the standby Global Manager, the active one, then the Local Managers. Three conditions break that order or block operations, and each has an alert: the Global Managers run different versions, in which case NSX refuses a switchover; a location runs a newer release than the Global Manager; an upgrade is in progress, paused or failed on a manager. The upgrade status comes from the upgrade coordinator of each manager (`/api/v1/upgrade/summary`).

The release alert compares the first three numbers of the NSX version. The Local Managers and the Global Managers of the lab run the same release, 9.1.1, at two patch levels, which raises no alert.

### What the adapter reads from VCF Operations

The cloud proxy gives an adapter a connection to VCF Operations itself. The pack uses it for three things: the list of NSX clusters the built-in NSX integration monitors, the location list of earlier cycles when no Global Manager answers and the cache of the adapter container is empty, and, when the setting is on, the deletion of objects that NSX no longer returns and that have had no data for a set number of days. A property on the federation object says whether the connection works. It reads "ok" in the lab. Without it, the pack raises every alarm alert itself, links nothing and deletes nothing.

### Realization of stretched objects

Realization is the only part of the collection that costs one call per object. With up to 200 stretched objects, each one is read in every cycle. Above that, the adapter reads an object again when it is new, changed in NSX, not fully realized, or on its turn in a sweep that covers every object in six cycles, and keeps the last state otherwise. On the mock NSX, a federation with 610 stretched objects costs 610 realization calls in the first cycle and about 100 in each of the next.

## Tests in the lab

The pack only reads from NSX, so no test sends traffic through it: the tests replace NSX with a mock, read the live managers, make chosen calls fail inside the adapter, or write values into VCF Operations as if the adapter had sent them. Apart from the switchover, only the local run and the fault injection call NSX, with the same read-only GET calls the adapter makes. The switchover is the one test that changes NSX itself.

| Test | What it does | What it changes | Run with |
| --- | --- | --- | --- |
| Test suite | Runs the adapter against a mock NSX that answers from an anonymised recording of the lab federation, and against a mock VCF Operations API | Nothing | `pytest` in the tooling container; GitHub Actions on every push and pull request |
| Local run | Runs one collection cycle against the live managers, with the same read-only GET calls the adapter makes inside VCF Operations | Nothing | `tools/localrun.py` in the tooling container |
| Fault injection | The local run, with chosen NSX calls made to fail with HTTP 500 inside the adapter process | Nothing on NSX | `tools/test_gaps.py` in the tooling container |
| Alert tests | Writes metrics, properties or events into VCF Operations through its suite API, in the form the adapter sends, then restores them | VCF Operations data, for a few cycles | Suite API calls |
| Switchover | Moves the active role to the standby Global Manager and back | The NSX federation, as a planned operation | NSX API on the standby Global Manager |

The test suite needs no lab. Its 69 tests cover a normal cycle, failed calls, a location outage, a Global Manager of another federation, the cached location list and the one read back from VCF Operations, the call budget, certificate trust, versions and upgrades, tiered realization on 600 more segments, the deletion of objects that left NSX, and the consistency of the alert content with the object model.

*Run on: the jump host, from the root of a clone of the repository.*

```bash
docker build -t fedmp-tooling tooling
docker run --rm -u $(id -u):$(id -g) -v $PWD:/work -w /work fedmp-tooling \
  python -m pytest -q tests -p no:cacheprovider
```

The local run needs network access to the managers and a read-only NSX account. It prints the objects, metrics and properties of one cycle. `LM_USER` and `LM_PASSWORD` set a separate account for the Local Managers.

*Run on: the jump host, from the root of a clone of the repository.*

```bash
docker run --rm --network host -v $PWD:/work -e GM_HOSTS=<gm1>,<gm2> \
  -e NSX_USER=audit -e NSX_PASSWORD='<password>' fedmp-tooling python tools/localrun.py
```

Results on the lab:

| Test | Result |
| --- | --- |
| Normal run, both Global Manager addresses | 72 objects, about 45 API calls (about 75 when the cached inventory is reloaded), 10 to 25 seconds |
| Second Global Manager address unreachable | Collection continues. "Global Managers not reachable" reads 1 on the federation, and a Global Manager collected in an earlier cycle reads 0 on "Reachable by the collector" |
| No Global Manager reachable | Both Local Managers are still collected from the cached location list. The property "Collection mode" of the federation object reads degraded, which raises "NSX Federation collection runs without a Global Manager". The cache lives in the adapter container. When a restart empties it, the list is read back from VCF Operations |

### Fault injection

`tools/test_gaps.py` wraps the local run and makes chosen calls answer HTTP 500 inside the adapter process; NSX itself sees no error. By default the location status, backup overview, alarm and cluster status calls fail on every manager. The variable `FAIL` replaces that list with other path fragments, separated by commas.

*Run on: the jump host, from the root of a clone of the repository.*

```bash
docker run --rm --network host -v $PWD:/work -e GM_HOSTS=<gm1>,<gm2> \
  -e NSX_USER=audit -e NSX_PASSWORD='<password>' fedmp-tooling python tools/test_gaps.py
```

On the lab, with the defaults, the run reported 15 calls without an answer and sent none of the values behind them.

### Location outage

The rule that keeps dependent alerts quiet during a location outage (see One alert for a location outage) was tested on the lab without causing an outage. With the account stopped, the suite API wrote "one location in error" on the segment fed-stretched (`POST /api/resources/{id}/stats`), and the alert "Stretched segment is in error at a location" fired. Writing the name of a location to the property "Locations in outage" (`POST /api/resources/{id}/properties`) cancelled the alert while the symptom of the fault stayed active. The values were then restored and the account started again.

### Switchover test

A planned switchover was run on 19 November, to see what the pack reports when the active role moves. The switchover is one call on the standby Global Manager, the API form of "Make Active" in Location Manager:

*Run on: the jump host, against the API of the standby Global Manager.*

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

Each switchover took two to three minutes. The pack followed the active role in its first cycle after the change, with no edit to the account, because the account lists both addresses. No alert opened or closed during the test. For about a minute during the precheck, the new active Global Manager listed both Global Managers as active. The pack still counted one active Global Manager and raised no split-brain alert.

After a switchover the former active Global Manager is a standby and carries its own sync value, as described under The standby Global Manager.

A planned switchover never leaves the federation without an active Global Manager, so the alerts "no active Global Manager" and "Global Manager not reachable" did not fire, as expected.

### NSX alarm events

The lab had no open federation alarm. To test the definitions, two events were posted through the suite API (`POST /api/events`) in the form the adapter sends: `gm_to_lm_synchronization_warning` on the Global Manager and `rtep_bgp_down` on location site-a-wld01. Both raised their named alert within one cycle, and no "Notification event" alert appeared next to them.

The alarm alert carries the symptom, the text of the event and the recommendation:

![Detail of the alert "NSX alarm: RTEP BGP neighbor down" on site-a-wld01: description, recommendation, and the symptom "Local Manager raised NSX alarm rtep_bgp_down" with the text of the test event](/images/nsx-federation-mp/ops-alarm-alert.webp?v=2)

Posting the same events again with a cancel time cleared both alerts in the next cycle.

## Sending the alerts by email

The pack raises the alerts and VCF Operations sends them. That takes two settings: an outbound instance that knows the mail server, and a notification rule that picks the alerts. Both are under Administration, Configurations, in the Alerts group. The lab has no mail server, so the screenshots show the forms before saving and the rule was not run end to end.

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

The Define Criteria step looks like this:

![The Define Criteria step of a notification rule: object scope, alert scope with category, criticality and control state, and the status changes to notify on](/images/nsx-federation-mp/ops-notification-rule.webp)

A rule on the pack's object types, limited to Immediate and Critical, sends the alerts of those two levels by email. The Information and Warning alerts stay in the UI. A second rule without the criticality limit, sent to a team mailbox, covers them.

## Notes for pack developers

### Traps in the SDK

Five things cost time during the build. The first four are not in the SDK documentation:

- **Events with a fault key are dropped.** VCF Operations shows an adapter event that carries a fault key only when the pack ships a matching symptom definition. Without one, the event disappears without an error on either side. The pack sends its events without a fault key.
- **Recommendation IDs.** A recommendation ID has to start with `Recommendation-df-`. The SDK schema does not check it, and without the prefix VCF Operations rejects the whole alert file while the install still reports success.
- **Schema files in the content folders.** The project template keeps `traversalSpecsSchema.xsd` in `content/traversalspecs`. The installer reads every file of that folder as a traversal specification, fails on the schema with `Traversal specs root tag should be "AdapterKind"`, and the inventory tree never loads. The schema files have to stay out of the pack.
- **`CollectResult.get_objects_by_type`** compares the adapter type of each object with the requested object type, so it always returns an empty list. The adapter filters the objects itself.
- **Dashboards.** The SDK documentation says to build a dashboard in the UI and export it. The pack generates its dashboard JSON with a script instead, from the same object model as the adapter. The format was worked out from dashboards exported through the suite API (`/api/content/operations/export`). In the file, an object type is a placeholder such as `resourceKind:id:3_::_`, and a table at the top of the file maps each placeholder to an adapter kind and an object type.

Alert definitions, views and dashboards can be tested before any pack build: the suite API imports them from a zip file (`POST /suite-api/api/content/operations/import`), with no pack install and no unsigned-pack switch.

### Dashboard import

Two behaviours cost time after a dashboard import:

- An imported dashboard shows empty widgets at first. The object lists show every object of the instance and the scoreboards show nothing. A background job in VCF Operations completes the import a few minutes later, and the widgets then get their settings.
- The object lists keep the default columns. The added metric columns sit to the right, behind a scroll bar. A saved column layout in the file was not applied at import.

### Compared with the built-in integrations

The 1,253 alert definitions and 2,047 symptom definitions on the lab's VCF Operations were compared with the pack, counting the definitions of each adapter kind. The pack's choice of badges, reaction times and alarm names follows from this comparison:

| | Built-in NSX | Built-in vCenter | Built-in vSAN | NSX Federation pack |
| --- | --- | --- | --- | --- |
| Alert definitions | 423 | 185 | 225 | 63 |
| Symptom definitions by condition | NSX alarms as events (381), properties (99), metric thresholds (12) | Properties (329), vCenter faults (76), metric thresholds (57), events (33), dynamic thresholds (4) | Faults (430), properties (20), metric thresholds (2) | Metric thresholds (38), properties (36), NSX alarms as events (22), dynamic thresholds (2) |
| Badge | 405 Health, 18 Risk | 129 Health, 55 Risk, 1 Efficiency | 206 Health, 15 Risk, 4 Efficiency | 49 Health, 14 Risk |
| Symptoms with wait or cancel cycles above 1 | None | 45 | 2 | 31 |
| Alerts of the compliance subtype | 22 | 42 | 12 | 6 |

## Sources

- Lab: VCF 9.1.1, NSX 9.1.1 (Local Managers and Global Managers), VCF Operations 9.1.1. Build, install and tests from 17 to 19 November 2026.
- Source of the pack: [github.com/sorinplaton/vcf-ops-nsx-federation-mp](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp), tag [v1.7.8](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/tree/v1.7.8)
- Install guide with a checklist: [docs/INSTALL.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/main/docs/INSTALL.md)
- Objects, metrics and alerts of version 1.7.8: [docs/FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.7.8/docs/FEATURES.md); account settings and sizing: [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.7.8/docs/OPERATIONS.md)
- [VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction)
- Broadcom KB [415279](https://knowledge.broadcom.com/external/article/415279/error-no-signature-found-on-pak-file-wh.html): "No signature found on PAK file" while installing a management pack
- [Docker: verify repository client with certificates](https://docs.docker.com/engine/security/certificates/)
- Broadcom KB [324244](https://knowledge.broadcom.com/external/article/324244/split-brain-condition-after-a-global-man.html): split-brain after a Global Manager switchover
- Broadcom KB [378126](https://knowledge.broadcom.com/external/article/378126/nsx-apis-throws-error-message-client-ex.html): NSX API client rate limits
- Broadcom KB [345822](https://knowledge.broadcom.com/external/article/345822/gm-to-lm-network-latency-threshold-excee.html): Global Manager to Local Manager latency alarm
