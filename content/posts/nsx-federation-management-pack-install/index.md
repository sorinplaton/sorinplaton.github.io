---
title: "Monitoring NSX Federation in VCF Operations, part 2: building, installing and what the pack captures"
date: 2026-11-18
description: "Part 2 of the NSX Federation management pack for VCF Operations 9.1.1: the adapter built with the Integration SDK, the install step by step with screenshots (HTTPS registry, certificate trust on the cloud proxy, unsigned pack, account), and what the pack monitors on a two-location lab federation: 76 objects, 586 metrics, 87 alert definitions, four custom groups, five dashboards, thirteen views, two reports and a policy for lab federations. It also covers a Global Manager switchover and how to send the alerts by email."
summary: "The adapter collects a two-location NSX 9.1 Federation in 25 to 45 seconds. Running it inside VCF Operations 9.1.1 takes an image registry served over HTTPS, a cloud proxy that trusts the registry certificate, and the setting that allows an unsigned pack. This post documents each install step with screenshots, lists what the pack monitors (objects, metrics, alerts, groups, dashboards, views), tests a Global Manager switchover, and shows how to send the alerts by email."
tags: ["VCF 9.1.1", "NSX Federation", "VCF Operations", "Management pack", "Integration SDK", "Lab"]
categories: ["Field notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-federation-mp/ops-dashboard-stretched.webp"]
params:
  seoDescription: "Building and installing a custom NSX Federation management pack in VCF Operations 9.1.1: HTTPS registry, certificate trust, unsigned pack, and the objects, metrics, alerts, groups, dashboards and views it provides."
---

[Part 1](/posts/nsx-federation-management-pack-design/) ended with a design and no code. Writing the adapter took less time than installing it: VCF Operations makes three checks before a `.pak` file becomes a working collection. This post covers the build, the install in VCF Operations 9.1.1, what the pack monitors, a Global Manager switchover, and sending the alerts by email.

The pack described here is version 1.13.0, and its source is on GitHub: [sorinplaton/vcf-ops-nsx-federation-mp](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp). [docs/FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.md) lists every object type, metric, property and alert, with the condition and recommendation of each alert.

## The lab

The lab runs VCF 9.1.1 with two Global Managers, each a single node: `nsx-gm-01a` (`gm-site-a`) is active and `nsx-gm-01b` (`gm-site-b`) is its standby. They federate two locations, `site-a-wld01` and `site-a-wld03`, each with a two-node edge cluster. VCF Operations is 9.1.1 with one cloud proxy, `opscollector-01a`. The build runs on a Linux jump host with Docker, which also runs the image registry on port 5000.

![The part 2 lab: the active Global Manager nsx-gm-01a and the standby nsx-gm-01b, the locations site-a-wld01 and site-a-wld03 with their Local Managers and two-node edge clusters linked by RTEP, VCF Operations with the cloud proxy opscollector-01a, and the jump host that builds the pack and serves its image through the lab router](/images/nsx-federation-mp/lab-topology.svg?v=7)

The standby was deployed from the same 9.1.1 appliance as the active Global Manager, in the Small size, on the cluster of `site-a-wld03`, with an address and a DNS record in the management network. It was registered on the active Global Manager through the API, as `gm-site-b` in STANDBY mode, with its address, admin credentials and certificate thumbprint (`PUT /global-manager/api/v1/global-infra/global-managers/gm-site-b`). The first full sync completed within minutes.

## Build the adapter

![Build and deployment of the pack: the GitHub repository is cloned on the jump host, the tooling container builds the image and the .pak, the image goes to the HTTPS registry on the jump host, the .pak is installed in VCF Operations with the unsigned PAK switch on, the account deploys the adapter on the cloud proxy, which pulls the image by digest through the lab router, reads the Global Managers and Local Managers over their REST API, and sends the data to the analytics node](/images/nsx-federation-mp/build-topology.svg?v=11)

### Jump host prerequisites

The jump host builds the pack, runs the tests and serves the image. Before the build it needs:

- **Linux with Docker Engine**, and a user in the `docker` group. The SDK tools, the tests and the registry run in containers.
- **`git`**, to clone the repository.
- **Python 3**, any recent version, for the scripts that run outside the tooling image: `tools/gen_*.py` (alerts, dashboards, views, groups, policy) and `tools/repack.py`.
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

- `nsxclient.py`: a read-only NSX REST client with cursor paging, a call budget per manager, back-off on HTTP 429 and 503, a certificate check by SHA-256 fingerprint or by CA chain and host name, and a small on-disk cache for slow-moving data.
- `adapter.py`: the object model, the collection logic and the events.

The adapter instance, which the VCF Operations UI calls an account, takes one or two Global Manager addresses and one credential (a second credential for the Local Managers is optional). It discovers the Local Managers from the Global Manager, and it compares the certificate of each Local Manager with the thumbprint the Global Manager already holds for it. A Global Manager is trusted by the certificate accepted on the account in VCF Operations, or, in the "ca" trust mode, by its CA chain. Once a certificate is checked, every connection of the cycle is bound to its fingerprint, so the credentials are not sent to a server that presents another certificate.

One account monitors one federation. A listed Global Manager that answers for another federation is reported and left out, and an optional setting names the federation object when VCF Operations monitors several.

### The pack file and its image

An SDK pack is not self-contained. The `.pak` file carries the object definitions and a pointer to a container image. The cloud proxy pulls that image from a registry when the adapter instance starts.

`mp-build` builds the pack and pushes the adapter image to the registry, so the registry of install step 1 must be running, and trusted by the jump host's Docker, before the build. The full command is in step 4 of [docs/INSTALL.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/INSTALL.md). When the registry address is given as `host:port`, `mp-build` writes Docker Hub into the pack instead of that address. A short script, `tools/repack.py`, rewrites two values, `REGISTRY` and `REPOSITORY`, in the `.conf` file inside `adapter.zip`:

*Found in: the `.conf` file inside `adapter.zip` in the `.pak`, rewritten by `tools/repack.py` on the jump host.*

```
REGISTRY=<jump-host-ip>:5000
REPOSITORY=/nsxfederation
DIGEST=sha256:dd80...b4cb
```

## Install the pack

The install has five steps, in the order that works. Where the lab hit a failure, its symptom is kept under the step that prevents it. [docs/INSTALL.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/INSTALL.md) has the same steps as a checklist, with every command and the machine it runs on.

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

SSH is disabled on the cloud proxy and stayed so: the file was written through vCenter guest operations (`govc guest.run` with the appliance root account). An upgrade or a redeploy of the cloud proxy may remove it.

### Step 3: install the unsigned pack

The pack is not signed by Broadcom. The upload through the appliance API fails:

*Output: the response of the VCF Operations appliance API to an upload from the jump host.*

```
pak_manager.pakfile.no_signature
The PAK file ... is not signed. It is missing required signature files signature.mf and signature.cert
```

VCF Operations 9 blocks unsigned packs by default, and the "Ignore the PAK file signature checking" box is hidden in the upload dialog until the block is lifted. The switch that lifts it, "Allow unsigned PAK installation", sits in the admin console (`https://<vcf-operations>/admin`) under Administrator Settings, Security Settings, and Broadcom documents it in KB 415279. Before it is turned on, the page looks like this:

![The admin console of VCF Operations, Administrator Settings, Security Settings: "Allow unsigned PAK installation" is deactivated, with the button that activates it](/images/nsx-federation-mp/ops-admin-unsigned-pak.webp)

The install turns the switch on first and off again after:

1. In the admin console, click "Activate unsigned PAK installation".
2. In VCF Operations, open Administration, Integrations, Repository and choose to add a pack.
3. Select the `.pak` file and tick both boxes: install even if already installed, and "Ignore the PAK file signature checking".
4. Upload, accept the warning about the unsigned file, and let the install finish.
5. Back in the admin console, deactivate unsigned PAK installation again. The console refuses while the install is still running.

The repository also holds a scripted version of these steps, `install/upi.sh` and `install/install_unsigned.sh`; [docs/INSTALL.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/INSTALL.md) describes it.

After the install, the adapter kind `NSXFederation` and the solution `iSDK_NSXFederation` appear in VCF Operations. The pack shows under Administration, Integrations, Repository with its version:

![Administration, Integrations, Repository: the NSX Federation pack, version 1.13.0, with one account](/images/nsx-federation-mp/ops-repository-pack.webp?v=9)

### Step 4: create the account

Create the account under Administration, Integrations, Accounts, as an account of the type NSX Federation, with the cloud proxy as its collector. It takes one or two Global Manager addresses and a credential, plus an optional one for the Local Managers. With both addresses, the pack follows the active role when the Global Managers switch over. The other settings are optional: the security policies whose rules become objects, how the pack works with the built-in NSX integration (links and alarm alerts, both automatic by default), and the deletion of objects that left NSX (off by default). [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/OPERATIONS.md) describes each setting.

Validate the connection and accept the Global Manager certificates when the account is saved. The certificate check has three modes. "enforce" stops the collection of a manager whose certificate does not match or that has no trusted certificate on record. "ca" works like "enforce" and also trusts a certificate that chains to a trusted CA and is valid for the address the account uses, so that address must be a name or IP address listed in the certificate. The trusted CAs are those in the account setting Trusted CA certificates (PEM), which accepts PEM text pasted on one line, any CA certificate accepted on the account, and the CA store of the adapter image. A CA imported into the cloud proxy is not part of that store. "report" collects and raises an alert. The default is "enforce": without an accepted certificate the managers are not collected.

The pack only reads from NSX, so a read-only account is enough. In the lab, the account logs in with NSX's built-in `audit` user on the two Global Managers and the two federated Local Managers, and collects the same objects as with `admin`, with no failed call.

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

Metrics return after three cycles and rates after four. An install keeps the alert, symptom and recommendation definitions that the new version no longer ships. `install_unsigned.sh` runs `install/cleanup_content.py` after the install to delete those orphans. After an install in the UI, run the script by hand with `--delete`. It only deletes IDs that the pack generates, and it keeps an orphan that an alert definition outside the pack still uses.

A change in how the adapter identifies an object creates a new object. The old one stays in the inventory without data until it is deleted, which also deletes its history. `cleanup_content.py --legacy-objects` lists the edge nodes, RTEP tunnels and sync channels that are still identified by a name, and `--legacy-objects --delete` deletes them.

## What the pack monitors

The complete list of objects, metrics and alerts is in [FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.md).

### Objects

The pack defines 16 object types. In the lab it creates 76 objects of 15 types. The sixteenth type, the global firewall rule, is opt-in per policy and was left off for this install.

| Object type | Count in the lab | Examples of what is collected |
| --- | --- | --- |
| Federation | 1 | Active Global Managers, locations healthy and collected, switchover readiness, RTEP rollups, largest clock difference, most used NSX configuration limit, changes seen and the last ten of them, API errors, managers over the call budget |
| Global Manager | 2 | Ready or not to take over as the active Global Manager, with the reasons, standby in sync and how long it has been out of sync, entries left to send, last full sync, version against the active Global Manager, upgrade status, backup state, open alarms, days to the first certificate expiry, clock difference with the active Global Manager and NTP state |
| Manager node | 4 | Online state of each Global Manager and Local Manager node |
| Location | 2 | Healthy or not, connection to the Global Manager, replication flows, latency and sync queues, last seen by the Global Manager, onboarding status, collector reachability and time since the last collection, nodes online, full sync failures and duration, inter-site BGP sessions, alarms, version, upgrade status, clock difference with the Global Manager and NTP state, use of the NSX configuration limits, global objects overridden at the location |
| Sync channel | 9 | Connection state between two managers |
| Edge cluster | 2 | Inter-site status, members, RTEP addresses |
| Edge node | 4 | RTEP tunnels, inter-site BGP sessions, status, CPU of the datapath and service cores, system and datapath memory, fullest memory pool and file system |
| Tier-0 service router | 4 | HA state, routing status, BGP neighbors up and down, last HA state change |
| RTEP tunnel | 8 | BGP state and connection drops per hour, packets, drops, share of packets dropped and blocked packets in each direction |
| Tier-0 BGP neighbor | 16 | State, prefixes in and out, messages, time established, connection drops per hour; 4 uplink sessions and 12 internal sessions between edges |
| Tier-0 gateway | 1 | Span, locations realized, locations in error, service routers active and with routing down, locations without an active service router |
| Tier-1 gateway | 3 | Same as the Tier-0 |
| Segment | 5 | Same, plus subnets and connected gateway |
| Global security policy | 1 | Span, realization, rules total and disabled |
| Stretched object at a location (realization) | 14 | Realized or not, status text |

The pack reports its own health on the federation object: calls made, calls throttled, errors, and whether a manager ran out of its call budget.

An edge node is identified by its NSX transport node ID, and each of its tunnels by that ID and its two RTEP addresses. A sync channel is identified by the site IDs of its two ends. A Tier-0 service router is identified by the NSX IDs of its gateway and of its edge node. An edge node that is renamed or redeployed keeps its objects and their history, and so does a renamed location or Global Manager.

In the inventory, the object types sit in a tree and each object has a summary page, here for a location:

![VCF Operations inventory: the NSX Federation object types in the tree, and the summary of location site-a-wld01 with its related objects, its key indicators and the alert on a paused NSX upgrade](/images/nsx-federation-mp/ops-inventory-location.webp)

The pack also ships an inventory tree. To open it, go to Inventory, click Detailed View, select the last tab (the puzzle-piece icon) and pick NSX Federation in the drop-down. The tree starts at the federation. Under it sit the segments, the Tier-1 and Tier-0 gateways, the global policy, the Global Managers and the two locations. A Global Manager holds its manager node and its sync channels. A location holds its manager node, its sync channels and its edge cluster, with the edge nodes and their tunnels. A Tier-0 holds its BGP neighbors, and each stretched object holds its realization per location:

![The NSX Federation inventory tree in VCF Operations: the federation, its segments, gateways and policy, and location site-a-wld01 opened on its manager node, its sync channels and its edge cluster](/images/nsx-federation-mp/ops-inventory-tree.webp)

### Custom groups

Four custom groups come with the pack: all objects of the pack (76 in the lab), managers and locations (9), inter-site connectivity (43) and stretched objects (24). A group collects the alerts and badges of its members, and a policy or a notification rule can take it as its scope. The groups sit under Inventory, Custom Groups, Environment. Membership follows the object type, so a new location or a new tunnel joins its group without an edit. These four groups hold the objects of every federation. A script in the repository, `install/federation_groups.py`, adds one group per federation, "NSX Federation - <federation>". Every object of the pack carries the ID of its federation as a property, and the group selects on it, so it never takes objects of another federation. In the lab, that group holds the 76 objects of the federation. Another 21 objects of the pack in the lab receive no data and carry no ID, so they stay out.

![Custom group "NSX Federation - Managers and locations": the two Global Managers, the federation, the two locations and the four manager nodes](/images/nsx-federation-mp/ops-custom-groups.webp)

### Metrics

Once the rates are available, the pack sends 586 metrics per cycle in the lab. They sit in 25 groups, as in the built-in integrations (Sync, Connectivity, Certificates, Alarms, Rates, Capacity, Time, Availability and others), which keeps the metric tree of an object short. The key of a metric carries its group, `sync|standby_in_sync` for example.

![Metrics tab of Global Manager gm-site-a: the groups Alarms, Certificates, Cluster, Connectivity and Sync, and the chart of "Standby sync healthy (1 = in sync)" at 1](/images/nsx-federation-mp/ops-metric-groups.webp)

Twenty-six metrics are flagged as key indicators, up to five per object type: the active Global Managers, the share of healthy locations and the switchover readiness on the federation, the offline nodes and the standby sync on a Global Manager, the health, connection and full sync of a location, BGP, drops and the share of packets dropped on a tunnel, the datapath CPU of an edge node, the routing of a Tier-0 service router, the active service routers of a Tier-0, and so on. VCF Operations lists them first in the summary of the object:

![Summary of gm-site-a: the open backup alert and, on the right, the key indicators "Nodes offline 0" and "Standby sync healthy (1 = in sync) 1"](/images/nsx-federation-mp/ops-key-attributes.webp)

NSX returns packets, bytes, drops and rule hits as running totals. The adapter keeps the counters of the previous cycle and adds rates: packets, drops and blocked packets per minute and bytes per second on RTEP tunnels, rule hits per minute on policies, realizations and rules, and connection drops per hour on Tier-0 BGP neighbors and on the inter-site BGP session of each RTEP tunnel. From the same counters it computes the share of packets an RTEP tunnel dropped in each direction. NSX counts a dropped packet in the packet total (see Latency, packet loss and BGP flaps), so the share is the drops divided by the total. It needs at least 50 packets since the previous cycle, so a single lost packet on an idle tunnel does not show as a large percentage. Units are declared: seconds, minutes, hours, days, milliseconds, bytes, bytes per second, packets, calls, errors, percentages, per-minute and per-hour rates.

Three metrics are derived by the adapter, because a symptom compares a metric with a fixed value and cannot subtract two metrics: Global Managers not reachable, inter-site BGP sessions not established, locations not realized.

Twelve figures cover the whole federation, on the federation object: locations healthy, as a count and a percentage; RTEP tunnels; tunnels whose BGP session is down; RTEP drops per minute over all tunnels; days to the first certificate expiry on any manager; open critical alarms on all managers; the largest clock difference with the active Global Manager; the most used NSX configuration limit of any location; the global objects overridden at any location; the fewest days left on a password of the collection account; the global firewall rules to review. A super metric could compute some of them, but it does nothing until a policy enables it, so the adapter computes all twelve. On 22 November they read two of two locations healthy (100%), eight tunnels, none down, no drops, 328.5 days to the first certificate expiry (a Local Manager certificate), one critical alarm, a clock difference of 0.07 seconds, 97.6% for the most used limit, no overridden object, 35 days for the first password to expire and no rule to review.

Each location also carries what the Location Manager page of NSX shows for it: the state of its three replication flows with the Global Manager, when the Global Manager last saw it, the latency, how full its sync queues are, and its onboarding status. The pack makes the same calls as that page on the active Global Manager. The replication flows come from `GET /api/v1/federation/replication/gm/sites/<site-id>/status`, whose round-trip time field NSX 9.1.1 leaves empty. The "Latest Latency" column of the page comes from another call, `GET /global-manager/api/v1/observability/flow-details`, which returns, for each direction between the Global Manager and a location, the latency NSX measured and the current and maximum size of its sync queues. The pack keeps the higher latency of the two directions and compares it with the maximum RTT set for the location in NSX (250 ms by default). For the queues, it reports how full the fullest one is, as a percentage. NSX measures no latency between the two Global Managers, and its own page shows NA there.

In the coverage table below, the Metrics column counts the metric keys defined for each type. Not every object sends every key (only policy realizations carry counters, for example), so the column multiplied by the object counts does not give 586.

| Object type | Metrics | Alert definitions | Dashboard |
| --- | --- | --- | --- |
| Federation | 36 | 9 | Federation Overview, Location Matrix, Switchover Readiness |
| Global Manager | 18 | 24 | Federation Overview, Switchover Readiness |
| Manager node | 1 | None, covered by the cluster alert of its Global Manager or location | Related objects only |
| Location | 31 | 28 | Federation Overview, Location Matrix |
| Sync channel | 1 | 1 | Inter-site Connectivity |
| Edge cluster | 4 | 1 | Inter-site Connectivity |
| Edge node | 11 | 6 | Inter-site Connectivity |
| Tier-0 service router | 4 | 1 | Inter-site Connectivity |
| RTEP tunnel | 21 | 5 | Inter-site Connectivity |
| Tier-0 BGP neighbor | 8 | 3 | Inter-site Connectivity |
| Stretched Tier-0 | 10 | 3 | Stretched Objects |
| Stretched Tier-1, segment | 4 each | 1 each | Stretched Objects |
| Global security policy | 11 | 3 | Stretched Objects |
| Stretched object at a location | 6 | 1 | Stretched Objects |
| Global firewall rule (opt-in) | 5 | None | None |

### Alerts

The pack has 87 alert definitions, 153 symptoms and 53 recommendations. Twelve recommendations end with a link to the Broadcom KB article or documentation page for the fault. The content is one XML file in `content/alertdefs`, which a script generates from a table of symptoms, alerts and recommendations. A symptom is a condition on one metric, one property or one event, and four symptoms use a dynamic threshold. A condition on a property looks like this:

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

The 87 definitions, by object type:

| Object type | Definitions | Alerts |
| --- | --- | --- |
| Federation | 9 | No active Global Manager; split-brain (two active); a Global Manager not reachable by the collector; no standby configured; partial collection (API call budget used); collection with data gaps; collection slow or throttled; collection without a Global Manager; a listed Global Manager of another federation |
| Global Manager | 24 | Cluster degraded; backup missing, old or failed; certificate about to expire; standby not in sync; no role in the federation (mode NONE); version differs from the active Global Manager; NSX upgrade in progress, paused or failed; clock not in step with the active Global Manager; password of the collection account about to expire; 15 NSX federation alarms (see below) |
| Location | 28 | Connection to the active Global Manager lost; Local Manager not reachable by the collector; certificate not trusted; full sync not completed; inter-site BGP sessions down; high latency to the Global Manager; NSX release differs from the Global Manager; backup missing, old or failed; certificate about to expire; Local Manager cluster degraded; a replication flow in error; NSX release newer than the Global Manager; NSX upgrade in progress, paused or failed; clock not in step with the Global Manager; global objects overridden; NSX configuration limit close to its maximum; password of the collection account about to expire; 11 NSX federation alarms |
| Sync channel | 1 | Channel down |
| Edge cluster | 1 | Inter-site status is not UP |
| Edge node | 6 | All inter-site BGP sessions down; status not UP; datapath CPU high; datapath CPU above its usual range (dynamic threshold); memory high; file system filling up |
| Tier-0 service router | 1 | Routing or HA state down |
| RTEP tunnel | 5 | Inter-site BGP session down on the tunnel; inter-site BGP session flapping; packets dropped in one direction; packets blocked in one direction; traffic above its usual range (dynamic threshold) |
| Tier-0 BGP neighbor | 3 | Session down; session flapping (dropped since the previous cycle and up again); uplink session established with no prefixes received |
| Tier-0 | 3 | Locations in error; no active service router (in the federation, or at a location for an active-active gateway); a service router changed HA state |
| Tier-1, segment | 1 each | Locations in error |
| Global security policy | 3 | Locations in error; rules never hit (shadowed or unused); a rule that allows any traffic |
| Stretched object at a location | 1 | Not realized |

The edge node alert fires only when the node has lost all its inter-site sessions, because a single lost session raises the tunnel alert. "Stretched object is not realized at a location" is a Warning, because the error alerts on the object itself are Immediate.

Each alert also sets which badge it lowers and how fast it reacts:

- **Badge.** Twenty-one alerts lower the Risk badge instead of Health: both backup alerts, both certificate expiry alerts, the certificate not trusted, no standby, the release mismatch, the call budget, the data gaps, the slow or throttled collection, the Global Managers on different versions, the location ahead of the Global Manager, both upgrade alerts, the edge file system filling up, the overridden global objects, the NSX configuration limit, both password alerts and both firewall rule review alerts. These conditions cause trouble later, and Health is kept for what is broken now.
- **Wait and cancel cycles.** The symptoms that can flap need two collection cycles, 10 minutes, to raise an alert and two to clear it: a replication flow in error; sync channel down; inter-site BGP sessions down and none established on the location; no inter-site session on the edge node; inter-site BGP down on the tunnel; dropped and blocked packets on a tunnel; latency to the Global Manager; Tier-0 BGP neighbor down; no prefixes received; an edge node status other than UP; a Tier-0 service router down; a Tier-0 without an active service router. A BGP flap, on a Tier-0 neighbor or on the inter-site session of a tunnel, is the exception: one session drop raises the alert at the next cycle, and the alert clears after two cycles, so a flap stays visible for 10 minutes. The data gaps and the slow collection also wait two cycles, and so do a full sync that did not complete and a stretched object that is not realized, so a sync or a change in progress raises nothing. The dynamic threshold, the standby sync, the throttling and the edge CPU, memory and disk alerts wait three. A clock difference waits two cycles, since one reading can be off by the duration of the call; an NTP service that is not running raises the alert at once. A symptom that keeps an alert quiet, such as the maintenance mode of an edge, clears one cycle after the symptoms it covers, so leaving maintenance raises nothing. The outage symptoms clear after three cycles (see One alert for a location outage).
- **Compliance subtype.** Eight alerts carry the compliance subtype: no standby, both backup alerts, the Global Managers on different versions, both release alerts of a location, and both firewall rule review alerts. The Compliance page of VCF Operations 9.1.1 shows a banner that the older Compliance feature is deprecated in VCF 9.1 and points to Security Posture Management, so the subtype only classifies the alerts.
- **Thresholds.** Most symptoms test a state: a session down, a node offline, an object in error. Those that compare a metric with a number are listed in the [Thresholds table of FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.md#thresholds): the backup age (24 hours), the certificate expiry (30 and 7 days), the collection duration (4 minutes), the latency to the Global Manager (150 ms, or 80% and 100% of the maximum RTT set in NSX), and on RTEP tunnels the dropped packets (more than 1%, or 5% for Immediate, together with more than 10 per minute) and the blocked packets (more than 10 per minute), the datapath CPU of an edge node (75%, 85% for Immediate), its memory (80% and 90%), its fullest memory pool (85%) and file system (80% and 90%), the clock difference with the active Global Manager (5 seconds, 60 for Immediate), and the days left on the password of the collection account (30 and 7). The NSX configuration limits use the thresholds NSX itself applies (70% and 100% by default). The drop alert needs both the share and the count: a fixed count alone means a lot on a quiet tunnel and nothing on a busy one. A VCF Operations policy can override a threshold without a new build: Policy Definition, edit the policy, Alerts and Symptoms, Symptom Definitions, select the symptom and change the value under Override Symptoms.
- **Dynamic threshold.** The tunnel traffic alert compares the receive and send rates with the range VCF Operations learns for each tunnel. The latency alert does the same with the round-trip time of each location, so a link that goes from 1 ms to 40 ms is reported long before it reaches 150 ms. A third does it with the datapath CPU of each edge node. All three need days of history before they can fire.

Four alerts fire in the lab, from three definitions: the missing backup on both Global Managers; a paused NSX upgrade on the Local Manager of site-a-wld01, whose upgrade coordinator holds a target version it has not applied; and an NSX configuration limit on site-a-wld03, where 830 of the 850 services the Local Manager supports are in use (97.6%, rated WARNING by NSX). The Local Manager of site-a-wld01 is at the same level, and that alert is left to the built-in NSX integration, which monitors it. All four are correct for the lab and count against Risk. The other definitions have nothing to report here; Tests in the lab describes how they were verified.

An alert from a definition carries its recommendation and the symptom that triggered it:

![Detail of the alert "Global Manager backup is missing, old or failed" on gm-site-a: description, recommendation and the symptom "Backup is not configured on the Global Manager"](/images/nsx-federation-mp/ops-alert-recommendation.webp)

#### NSX alarms as named alerts

The adapter forwards the federation alarms of the Global Manager, and those of each Local Manager, as events with the text `NSX alarm <event_type>: <summary>`. The built-in NSX integration uses the same form and matches it to give 381 alarm types an alert of their own.

The pack gives the 15 federation alarm types of NSX 9.1.1 their own symptom, alert and recommendation: split-brain between Global Managers; sync errors and warnings between the Global Managers, between the Global Manager and a location, and between locations; the two latency warnings; the sync queue above its threshold; a Local Manager restored during its configuration import; RTEP BGP down; RTEP connectivity lost; and two alarms about the Active Directory group sync used by Identity Firewall, a failed delta sync with the directory and the connection to the LDAP server lost. The Global Manager gets all 15. A location gets 11, because the four alarms between Global Managers do not apply to it. NSX raises a latency alarm when the latency between two managers stays above its threshold for two minutes.

The built-in NSX integration has the same alarms, but only for the managers added to it as NSX accounts. The alarms of the Global Manager reach VCF Operations only through this pack. For a Local Manager, the built-in integration and the pack would each raise an alert for the same alarm. So the adapter asks VCF Operations which NSX clusters the built-in integration monitors, and leaves the federation alarms of those Local Managers to it. In the lab, site-a-wld01 has an account in the built-in integration that collects, and the built-in account of site-a-wld03 exists but does not collect: a property on each location reads "the built-in NSX integration" for the first and "this pack" for the second. A setting forces either behaviour. NSX raises alarms of its own for the status, CPU, memory and file system of an edge node and for its configuration limits, and the built-in integration forwards them. The status, CPU, memory and file system alerts of an edge node, and the alert on the NSX configuration limits, are therefore left to the built-in integration for the Local Managers it monitors. For these alerts only that list decides, and the setting does not apply.

Some conditions without a definition still arrive as events: a federation alarm whose type has no definition, on a Local Manager or on the Global Manager, and every critical alarm of the Global Manager, up to 20 per manager. Such an event surfaces as a "Notification event" alert on the object, with no recommendation. The other NSX alarms of a location are counted in its metrics and not forwarded. A condition that has an alert definition on a metric or a property sends no event, so it never shows twice, once as the alert and once as a "Notification event".

### Dashboards

Five dashboards sit in a folder named "NSX Federation":

| Dashboard | Content |
| --- | --- |
| Federation Overview | Open alerts on the pack's objects; a list of the federation, the Global Managers and the locations; key values, properties and related objects of the selected one |
| Location Matrix | One row per location with the checks that decide its health: connection to the Global Manager, collector reach, nodes offline, inter-site BGP, full sync, replication flows, latency, sync queue, backup, certificates, critical alarms, version; the federation figures; key values and properties of the selected location |
| Switchover Readiness | The Global Managers with their mode, readiness, reachability, sync state, nodes offline, version and backup age; the federation figures; key values and properties of the selected Global Manager, with the reasons it cannot take over |
| Inter-site Connectivity | Sync channels, edge nodes with their CPU and memory, Tier-0 service routers, RTEP tunnels, Tier-0 BGP neighbors; key values of the selected row |
| Stretched Objects | Stretched gateways, segments and policies with their span; the realization of each object at each location |

In every dashboard a list drives a scoreboard. In all of them except Stretched Objects, a click on a scoreboard value draws its trend.

Federation Overview with gm-site-a selected. The scoreboard of a Global Manager starts with the standby sync, and the open alerts are the backup and the paused upgrade:

![The Federation Overview dashboard: two open alert definitions (backup, paused upgrade), the list with gm-site-a and gm-site-b, and the scoreboard of gm-site-a with Standby in sync 1, nodes offline 0 and certificate expiry](/images/nsx-federation-mp/ops-dashboard-standby.webp)

With the federation selected, the scoreboard ends with the federation rollups: locations healthy, RTEP BGP down, RTEP drops per minute, certificate expiry and critical alarms:

![The Federation Overview dashboard with the federation selected: the scoreboard shows API errors, call budget, locations healthy 100, RTEP BGP down 0, RTEP drops per minute 0, certificate expiry 330.9 days and critical alarms 0](/images/nsx-federation-mp/ops-dashboard-rollups.webp)

Inter-site Connectivity shows the rates of one RTEP tunnel:

![The Inter-site Connectivity dashboard: sync channels and edge nodes, RTEP tunnels, Tier-0 BGP neighbors and the rates of the selected RTEP tunnel](/images/nsx-federation-mp/ops-dashboard-connectivity.webp)

Location Matrix, with site-a-wld03 selected. Both locations are healthy and the federation has a standby ready to take over:

![The Location Matrix dashboard: the two locations with Healthy 1 and GM connection 1, the federation figures (2 locations, 2 healthy, 100%, RTEP BGP down 0, ready to take over 1), and the key values and properties of site-a-wld03](/images/nsx-federation-mp/ops-dashboard-matrix.webp)

Switchover Readiness, with the standby gm-site-b selected. It is ready to take over, in sync, on the same version as the active Global Manager:

![The Switchover Readiness dashboard: gm-site-a active and gm-site-b standby, the federation with ready to take over 1, and the key values and properties of gm-site-b with the readiness "ready"](/images/nsx-federation-mp/ops-dashboard-switchover.webp)

Stretched Objects, with the segment fed-stretched selected:

![The Stretched Objects dashboard: the stretched gateways, segments and policies, the realization per location, and the key values and properties of segment fed-stretched](/images/nsx-federation-mp/ops-dashboard-stretched.webp)

### Views and reports

Nine list views cover locations, sync channels, RTEP tunnels, Tier-0 BGP neighbors, edge capacity, Tier-0 service routers, the firewall rule review, the recent changes and the realization of stretched objects per location. Four more show availability over the last 30 days, one each for the federation, the Global Managers, the locations and the stretched Tier-0 gateways, with the lowest values, the inter-site BGP and latency figures and the number of Tier-0 HA changes. Two reports put them in a PDF: "NSX Federation - Health report" with the first nine views, and "NSX Federation - Availability report" with the four others, meant to be scheduled monthly on the federation object. The thirteen views and the two reports sit in one XML file, which a script generates, as for the alerts and the dashboards. The Locations view:

![The list view "NSX Federation - Locations": both locations with version, connection to the Global Manager, collector reachability, inter-site BGP sessions and full sync failures](/images/nsx-federation-mp/ops-view-locations.webp)

### A policy for lab and test federations

Some alerts are expected on a lab federation. In this one, the Global Managers have no backup and a Local Manager sits at 97.6% of a configuration limit. The repository holds a VCF Operations policy, "NSX Federation - Non-production", that turns off eleven such alerts: backups, no standby Global Manager, the firewall rule review, overrides, configuration limits and edge capacity. The alerts for outages, sync, failover and realization stay on. The policy export file is imported under Administration, Configurations, Policy Definition, Import, or with `install/import_policy.py`, then assigned to the group of the lab federation, which `install/federation_groups.py --policy` does in the run that creates the group. In the lab, assigning the policy cancelled the two backup alerts at the next cycle, and removing it brought them back. The other federations keep the default policy. The policy is not inside the pack: a policy in a pack is a fragment of the describe file, and the SDK drops a required attribute of that fragment while it builds the pack, so the build fails.

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

While the active Global Manager reports a location as not connected, the adapter writes the name of that location to a property, "Locations in outage", on the objects that depend on it: the location, its edge cluster, edge nodes and BGP neighbors, the tunnels and sync channels with one end there, the stretched objects whose span includes it with their realization there, and the Global Managers. A fault at another location still raises its alert. Twenty-two alert definitions require that this property reads "none", and so do the alarm alerts, except the alarms between Global Managers. They stay quiet during the outage, and "Location lost its connection to the active Global Manager" is the alert to act on. In the alert XML this is a second symptom with `negateCondition="true"`:

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

Four metrics describe the standby: sync healthy (1 when in sync), entries left to send, minutes out of sync and hours since the last full sync. The active Global Manager carries them for all its standbys, and each standby carries its own sync state. A standby counts as in sync when NSX reports it in sync, or when a delta sync runs after a completed full sync. Any other state counts as out of sync. NSX 9.1.1 reports the normal state of a standby as ONGOING, together with a delta sync, a completed full sync and no entries left to send, so the status text alone cannot be the test. The alert "Standby Global Manager is not in sync" fires after three cycles and clears after two. In the lab the metric reads 1 with no entries left.

Each standby also says whether it can take over as the active Global Manager now. It reads ready (1), or not ready (0) with the reasons in a property: not in sync, a cluster status other than STABLE, nodes offline, service groups not stable, a version different from the active Global Manager, an expired certificate, or an upgrade in progress, paused or pausing. When a value could not be read, for example when the collector cannot reach the standby or the active Global Manager reports no sync state for it, the metric is also 0 and the property says what is unknown. The federation reads ready when one of its standbys does. No alert is attached to the readiness, since the conditions behind it already have their own alerts. In the lab, gm-site-b reads ready.

### Versions and upgrades

NSX Federation is upgraded in a fixed order: the standby Global Manager, the active one, then the Local Managers. Three conditions break that order or block operations, and each has an alert: the Global Managers run different versions, in which case NSX refuses a switchover; a location runs a newer release than the Global Manager; an upgrade is in progress, paused or failed on a manager. The upgrade status comes from the upgrade coordinator of each manager (`/api/v1/upgrade/summary`).

The release alert compares the first three numbers of the NSX version. The Local Managers and the Global Managers of the lab run the same release, 9.1.1, at two patch levels, which raises no alert.

### What the adapter reads from VCF Operations

The cloud proxy gives an adapter a connection to VCF Operations itself. The pack uses it for three things: the list of NSX clusters the built-in NSX integration monitors (for the alarm, edge and configuration limit alerts), the location list of earlier cycles when no Global Manager answers and the cache of the adapter container is empty, and, when the setting is on, the deletion of objects that NSX no longer returns and that have had no data for a set number of days. A property on the federation object says whether the connection works. It reads "ok" in the lab. Without it, and with the default settings, the pack raises every alarm, edge and configuration limit alert itself, links nothing and deletes nothing. When the connection fails for a while, the adapter keeps the last list it read, so these alerts do not move between the two integrations.

### Edge capacity and Tier-0 failover

One call per federation edge node, `GET /api/v1/transport-nodes/<id>/status` on its Local Manager, gives two things.

The first is the load of the edge: the CPU of the datapath cores (average and busiest core), the CPU of the other cores, which run the services, system and datapath memory, the fullest datapath memory pool and the fullest file system. NSX gives each as a current value, and VCF Operations keeps the history. In the lab the datapath cores are nearly idle (below 1%). The service cores of the two site-a-wld01 edges read between 50% and 100% in the readings taken for this post, those of site-a-wld03 between 13% and 50%.

The second is the state of each stretched Tier-0 gateway on the edge, its service router. Each one becomes an object with its HA state, its routing status and the number of BGP neighbors up and down. On the gateway, the adapter counts the service routers, the active ones and those with routing down over the locations of its span. What counts as healthy depends on the HA mode. An active-active gateway needs an active service router at every location of its span, so the adapter also counts the locations without one. An active-standby gateway needs one active service router in the federation. When a service router changes HA state between two cycles, a failover for example, the gateway raises a warning for two cycles. A service router that an edge stops reporting, while the edge itself answers, reads ABSENT, so a failover through a rebooting edge is still seen. When one edge of the span does not answer, the totals of the gateway are not sent for that cycle, but a location whose edges all answered and that has no active service router is still counted.

In the lab, fed-t0 is active-active with four service routers, two per location, all active with routing up.

### Change timeline

The adapter keeps in its cache what it saw in the earlier cycles and compares each new cycle with it. It records these changes:

- the active Global Manager, and the role and version of each Global Manager;
- the locations the active Global Manager lists, added, removed or renamed, and their versions;
- the edge nodes of the RTEP edge clusters of each location;
- the HA state of each Tier-0 service router;
- the stretched gateways, segments and global security policies created, changed (a new revision in NSX, with the user NSX recorded) or deleted, and the primary location of a gateway;
- the global objects overridden at a location.

Each change is an information event on the object it concerns, which VCF Operations shows as a notification on that object for one cycle. The federation counts the changes of each cycle and lists the last ten, newest first, with the time in UTC. The property history of the federation therefore doubles as a change timeline. A setting turns the events off and keeps the list.

The first cycle after an empty cache, for a new container or a new account, only records the state. A cycle in which a call failed reports no removal, and an object counts as added only against a complete earlier read, so a gap in the data does not show up as a change. The stretched objects come from the inventory of the Global Manager, which the adapter reads every 30 minutes, so a change to them shows up within 30 minutes.

### Clocks, configuration limits and overrides

Each manager's clock is read in every cycle from the system time NSX returns (`GET /api/v1/node/status`), compared with the moment halfway through the call. The clock of the cloud proxy cancels out of the difference between two managers read in the same cycle, and the calls are timed on a monotonic clock, so a step of that clock during the cycle changes nothing. Each Global Manager and location gets its difference with the active Global Manager, and a property says ahead or behind, and how precise the reading is. The alert fires above 5 seconds, 60 for Immediate, or when the NTP service of the manager is not running. In the lab, the other three managers are within a tenth of a second of the active Global Manager.

Each Local Manager reports how much of each NSX configuration limit it uses, as on its Capacity page (`GET /policy/api/v1/infra/capacity/dashboard/usage`), and rates each limit with its own thresholds. The Global Manager has no such call, but global objects count at every location of their span, so the locations show the federation as well. A limit whose maximum is 1, such as the single compute manager of a Local Manager, is full by design and left out. A limit at 100% or more counts as critical, whatever rating NSX gives it: in the lab, NSX rated the compute manager limit, at 100%, as a warning.

A search on each Local Manager (`overridden:true`) lists the global objects that were changed locally. A location then runs a configuration that differs from the Global Manager, so each override counts on the location and is a change of the timeline. The lab has none.

### Password expiry, firewall rule review and availability

Once an hour, each manager is asked for its local users (`GET /api/v1/node/users`, which the `audit` account may read). For each user NSX gives the days since the password changed and the number of days a password stays valid, 90 by default. For the account the pack collects with, the difference is the number of days before its password expires. Once it expires, NSX refuses the login and the collection of that manager stops, so the alert comes first: a warning below 30 days, critical below 7, Immediate on the last day. A password that never expires reads 9999 days. NSX does not hold an LDAP or VIDM account, and for one of those a property says that its directory sets the expiry. In the lab, the `audit` password expires in 35 days on site-a-wld01 and in 85 to 87 days on the other three managers.

Each global security policy is checked for three kinds of rules. A shadowed rule comes after an enabled rule of the same policy that matches all its traffic: the same or wider sources, destinations, services, context profiles and Applied To, and the same or both directions and IP versions. NSX never reaches such a rule. Group membership is not expanded, and a negated source or destination list must be the same in both rules, so the check only reports rules that can never be hit; a rule covered through a nested group is not found. An unused rule is one whose hit count, summed over the locations of its span, has not grown for 30 days, a setting of the account. A rule that never had a hit counts from the day the pack first saw it, so no rule reads unused in the first month. A smaller count, after a reset of the statistics, starts the period again, and a cycle in which a location does not answer sends no value. The third kind is an ALLOW rule from any source to any destination on any service, in a rule and a policy that apply everywhere. Shadowed and unused rules raise an information alert, an allow-all rule a warning, both on the Risk badge. The default policies of NSX are left out.

Each cycle also sends an availability value, 100 or 0. The federation is available when exactly one Global Manager answers in the active role, a Global Manager when it answers with no node offline, a location when it is healthy, and a stretched Tier-0 when it has an active service router wherever its HA mode needs one. Averaged over a period, the value gives the share of that period the object was available. A cycle in which the adapter did not run sends nothing, so it counts neither way.

### Realization of stretched objects

Realization is the only part of the collection that costs one call per object. With up to 200 stretched objects, each one is read in every cycle. Above that, the adapter reads an object again when it is new, changed in NSX, not fully realized, or on its turn in a sweep that covers every object in six cycles, and keeps the last state otherwise. On the mock NSX, a federation with 610 stretched objects costs 610 realization calls in the first cycle and about 100 in each of the next.

### Diagnostics and dependencies

`install/diagnostics.py` collects what a support request needs into one JSON file, and changes nothing. It covers the versions, each account with its settings, state and collector, the objects per type, the collection health of each federation, and the open alerts. It then lists the problems it found, each with what to check. It reads no credential. In the lab it reported one problem: 21 objects of the pack that receive no data. They are edge nodes, RTEP tunnels and sync channels that no longer match an object in NSX. `docs/OPERATIONS.md` has a troubleshooting table for the common cases.

A Trivy scan of the adapter image found two Python packages with known vulnerabilities and a fixed release. The SDK library pins `cryptography` 44.0.0, which has several published vulnerabilities, and only uses it to load a PEM certificate. The Dockerfile replaces it with 50.0.2, and the certificate of a Global Manager still loads with it. It also upgrades `setuptools` of the base image to 84.2.1. The other 101 high and critical findings (99 high, 2 critical) are in Debian packages of the SDK's base image that Debian 12 has not fixed. The repository records the Python packages of the built image, and CI checks them against the published vulnerabilities on every push and every week. CI also produces an SBOM. `SECURITY.md` lists what the pack can do on NSX and on VCF Operations.

## Tests in the lab

The pack only reads from NSX, so no test sends traffic through it: the tests replace NSX with a mock, read the live managers, make chosen calls fail inside the adapter, or write values into VCF Operations as if the adapter had sent them. Only five tests call NSX: the local run and the fault injection, with the same read-only GET calls the adapter makes, and the switchover, the change timeline test and the firewall rule review test, the only ones that change NSX itself.

| Test | What it does | What it changes | Run with |
| --- | --- | --- | --- |
| Test suite | Runs the adapter against a mock NSX that answers from an anonymised recording of the lab federation, and against a mock VCF Operations API | Nothing | `pytest` in the tooling container; GitHub Actions on every push and pull request |
| Local run | Runs one collection cycle against the live managers, with the same read-only GET calls the adapter makes inside VCF Operations | Nothing | `tools/localrun.py` in the tooling container |
| Fault injection | The local run, with chosen NSX calls made to fail with HTTP 500 inside the adapter process | Nothing on NSX | `tools/test_gaps.py` in the tooling container |
| Alert tests | Writes metrics, properties or events into VCF Operations through its suite API, in the form the adapter sends, then restores them | VCF Operations data, for a few cycles | Suite API calls |
| Switchover | Moves the active role to the standby Global Manager and back | The NSX federation, as a planned operation | NSX API on the standby Global Manager |
| Change timeline | Sets the description of a stretched segment, then removes it | One segment, restored | NSX API on the active Global Manager |
| Firewall rule review | Adds two identical ALLOW rules to a global security policy, then deletes them | One policy, restored | NSX API on the active Global Manager |

The test suite needs no lab. Its 162 tests cover a normal cycle, failed calls, a location outage, the share of packets a tunnel dropped (with no value on an idle tunnel or after a counter reset), BGP flaps on Tier-0 neighbors and on RTEP tunnels, the latency and sync queues read from the flow details, a Global Manager of another federation, the cached location list and the one read back from VCF Operations, the call budget, certificate trust, versions and upgrades, tiered realization on 600 more segments, the deletion of objects that left NSX, edge capacity, Tier-0 service routers in both HA modes, the change timeline (first cycle, failover, partial reads), clock differences (including a step of the clock of the cloud proxy), configuration limits and overrides, password expiry, the firewall rule review (shadowed, allow-all and unused rules, partial reads, counter resets), availability, a federation of 1,310 stretched objects, the consistency of the alert content and of the policy with the object model, the federation ID on every object, and the scripts for the groups, the notification rule and the diagnostics. On the mock, that large federation (1,000 more segments, 200 more Tier-1 gateways and 100 more policies of 50 rules each) takes 1,440 calls on the active Global Manager in the first cycle after a start, within the budget of 2,000 per manager, and 233 in the following cycles. The busiest Local Manager takes about 120.

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
| Normal run, both Global Manager addresses | 76 objects, about 60 API calls (about 100 when the cached inventory is reloaded), 25 to 45 seconds |
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

### Latency, packet loss and BGP flaps

Two checks back these metrics. The first read the packet counters of every RTEP tunnel on both edges at nearly the same moment, four times, two minutes apart. On the two tunnels that had dropped a packet, the received total matched the total sent by the peer exactly. NSX therefore counts a dropped packet in the total, and the pack computes the share of drops on that basis. The second tested the alerts the same way as the outage rule. With the account stopped, the suite API wrote breaching values every minute for 14 minutes, then the account was started again. A tunnel at 6% and 20 drops per minute raised "RTEP tunnel is dropping packets" as Immediate. A tunnel with 30 blocked packets per minute raised "RTEP tunnel is blocking packets". One connection drop on an established uplink raised the Tier-0 neighbor flap alert, and one on the inter-site session of a tunnel raised "RTEP tunnel inter-site BGP session is flapping". At 160 ms to the Global Manager, the latency alert came up as Warning through the 150 ms condition, since 160 ms is only 64% of the 250 ms setting.

During the test, the Inter-site Connectivity dashboard showed the values written to the selected tunnel. The scoreboard colors follow the alert thresholds:

![The RTEP tunnels list of the Inter-site Connectivity dashboard with one tunnel selected, and its key values during the alert test: Rx drops per minute at 20 in orange, Rx dropped at 6% in red, Tx blocked per minute at 30 and BGP drops per hour at 12 in orange, the other values with a threshold in green](/images/nsx-federation-mp/ops-dashboard-loss.webp)

The alert lists each symptom with the observed value and its threshold:

![Detail of the alert "RTEP tunnel is dropping packets" during the test: description, recommendation, and the symptoms with their values, 6% of received packets dropped against thresholds of 1% (Warning) and 5% (Immediate), and 20 drops per minute against a threshold of 10](/images/nsx-federation-mp/ops-alert-tunnel-drops.webp)

In normal operation the lab shows 1 ms to the Global Manager, empty sync queues and no drops.

### Change timeline test

On 22 November at 01:48 UTC, the description of the stretched segment fed-stretched was set on the active Global Manager with a PATCH. The next read of the inventory, at 02:13 UTC, recorded "Stretched Segment fed-stretched changed by admin (revision 0 -> 1)". It showed on the segment as a notification, which cleared at the next cycle, and it stayed in the last ten changes of the federation. Restoring the segment took a second attempt: a PATCH with an empty description left the description in place, and a PUT of the whole segment without the description restored it, at revision 2.

### Firewall rule review test

On 22 November at 14:10 UTC, two ALLOW rules were added to the global security policy fed-demo on the active Global Manager: fedmp-test-allow, from the group fed-vm-str-a to the group fed-vm-str-b on ICMP, and fedmp-test-allow-again, the same rule placed after it. The default rule of both Local Managers already allows that traffic, so nothing changed on the network. The first cycle after the pack was installed again read the new inventory, at 14:37 UTC. The policy showed three rules, one of them shadowed, with the property reading "fedmp-test-allow-again (by fedmp-test-allow)". "Global firewall rules to review" read 1 on the federation, and the information alert "Global security policy has rules that are never hit" opened on fed-demo. The two rules were deleted at 14:38 UTC. The next read of the inventory saw them gone, and by 15:12 UTC the policy was back to one rule and the alert had cleared.

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

A planned switchover never leaves the federation without an active Global Manager, so the alerts "NSX Federation has no active Global Manager" and "Global Manager is not reachable by the collector" did not fire.

### NSX alarm events

The lab had no open federation alarm. To test the definitions, two events were posted through the suite API (`POST /api/events`) in the form the adapter sends: `gm_to_lm_synchronization_warning` on the Global Manager and `rtep_bgp_down` on location site-a-wld01. Both raised their named alert within one cycle, and no "Notification event" alert appeared next to them.

The alarm alert carries the symptom, the text of the event and the recommendation:

![Detail of the alert "NSX alarm: RTEP BGP neighbor down" on site-a-wld01: description, recommendation, and the symptom "Local Manager raised NSX alarm rtep_bgp_down" with the text of the test event](/images/nsx-federation-mp/ops-alarm-alert.webp?v=2)

Posting the same events again with a cancel time cleared both alerts in the next cycle.

## Sending the alerts

The pack raises the alerts and VCF Operations sends them. That takes two settings: an outbound instance that knows the mail server, and a notification rule that picks the alerts. Both are under Administration, Configurations, in the Alerts group. The lab has no mail server, so the email screenshots show the forms before saving. A webhook was tested end to end instead, with a script (step 3).

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

### 3. The same rule with a script

`install/notification_rules.py` creates the rule of step 2 for an outbound instance that already exists, with the default template of its plugin type. It takes the alerts on every object type of the pack, at Immediate and Critical by default (`--min-criticality warning` adds the warnings), and sends them when they are new, updated and cancelled, so a ticket or a chat thread also learns that the alert cleared. `--group` limits it to the members of a group, for example the group of one federation.

In the lab, the outbound instance was a webhook pointing to a small HTTP listener on the jump host, and the rule was limited to the group of the federation. Assigning the "NSX Federation - Non-production" policy to that group cancelled the two Global Manager backup alerts, and the listener received two payloads with the status "Canceled". When the policy was removed, the alerts came back, and two payloads with the status "Active" arrived about three seconds later. Each payload carries the object, its type, the alert name, the criticality, the start and cancel dates, and the recommendation. The test outbound instance and the rule were deleted afterwards.

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
| Alert definitions | 423 | 185 | 225 | 87 |
| Symptom definitions by condition | NSX alarms as events (381), properties (99), metric thresholds (12) | Properties (329), vCenter faults (76), metric thresholds (57), events (33), dynamic thresholds (4) | Faults (430), properties (20), metric thresholds (2) | Metric thresholds (78), properties (45), NSX alarms as events (26), dynamic thresholds (4) |
| Badge | 405 Health, 18 Risk | 129 Health, 55 Risk, 1 Efficiency | 206 Health, 15 Risk, 4 Efficiency | 66 Health, 21 Risk |
| Symptoms with wait or cancel cycles above 1 | None | 45 | 2 | 66 |
| Alerts of the compliance subtype | 22 | 42 | 12 | 8 |

## Sources

- Lab: VCF 9.1.1, NSX 9.1.1 (Local Managers and Global Managers), VCF Operations 9.1.1. Build, install and tests from 17 to 22 November 2026.
- Source of the pack: [github.com/sorinplaton/vcf-ops-nsx-federation-mp](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp), tag [v1.13.0](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/tree/v1.13.0)
- Install guide with a checklist: [docs/INSTALL.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/INSTALL.md)
- Objects, metrics and alerts of version 1.13.0: [docs/FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.md); account settings and sizing: [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/OPERATIONS.md)
- [VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction)
- Broadcom KB [415279](https://knowledge.broadcom.com/external/article/415279/error-no-signature-found-on-pak-file-wh.html): "No signature found on PAK file" while installing a management pack
- [Docker: verify repository client with certificates](https://docs.docker.com/engine/security/certificates/)
- Broadcom KB [324244](https://knowledge.broadcom.com/external/article/324244/split-brain-condition-after-a-global-man.html): split-brain after a Global Manager failover
- Broadcom KB [378126](https://knowledge.broadcom.com/external/article/378126/nsx-apis-throws-error-message-client-ex.html): NSX API client rate limits
- Broadcom KB [345822](https://knowledge.broadcom.com/external/article/345822/gm-to-lm-network-latency-threshold-excee.html): Global Manager to Local Manager latency alarm
