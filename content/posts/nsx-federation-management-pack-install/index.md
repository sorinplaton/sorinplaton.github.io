---
title: "Monitoring NSX Federation in VCF Operations, part 2: building, installing and what the pack captures"
date: 2026-11-18
description: "Part 2 of the NSX Federation management pack for VCF Operations 9.1.1: the adapter built with the Integration SDK, the three obstacles between a .pak file and a running collector (unsigned pack, HTTPS-only registry, untrusted certificate), the 65 objects, 312 metrics and four alerts the pack produces on a two-location lab federation, the 22 alert definitions and three dashboards added in version 1.1, and the steps to send the alerts by email."
summary: "The adapter collects a two-location NSX 9.1 Federation in 12 seconds and 60 API calls. Getting it to run inside VCF Operations 9.1.1 took three fixes: allowing an unsigned pack, serving the image registry over HTTPS, and making the cloud proxy trust the registry certificate. This post documents each step and lists what the pack captures. It also covers the alert definitions and dashboards of version 1.1 and how to send the alerts by email."
tags: ["VCF 9.1.1", "NSX Federation", "VCF Operations", "Management pack", "Integration SDK", "Holodeck"]
categories: ["Field notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-federation-mp/object-model.svg"]
params:
  seoDescription: "Building and installing a custom NSX Federation management pack in VCF Operations 9.1.1: unsigned pack, private registry, certificate trust, the objects and metrics it captures, and its alert definitions and dashboards."
---

[Part 1](/posts/nsx-federation-management-pack-design/) ended with a design and no code. This post covers the build, the install in VCF Operations 9.1.1, and what the pack shows once it collects. The adapter itself took less time than the install. Three separate checks in VCF Operations stood between the `.pak` file and a running collector.

## The lab

A nested VCF 9.1.1 Holodeck. One Global Manager (`nsx-gm-01a`, single active node, no standby) federates two locations, `site-a-wld01` and `site-a-wld03`, each with a two-node edge cluster. VCF Operations is 9.1.1.0 build 25795063 with one cloud proxy, `opscollector-01a`. The build runs on a Linux jump host with Docker.

## Step 1: build the adapter

The pack uses the VCF Operations Integration SDK 1.3.1. The SDK tools (`mp-init`, `mp-test`, `mp-build`) need a Python version the jump host does not carry, so they run from a small tooling image:

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y docker.io git && rm -rf /var/lib/apt/lists/*
RUN pip install vmware-aria-operations-integration-sdk
```

The adapter is two Python files:

- `nsxclient.py`: a read-only NSX REST client with cursor paging, a call budget per manager, back-off on HTTP 429 and 503, certificate pinning by SHA-256 fingerprint, and a small on-disk cache for slow-moving data.
- `adapter.py`: the object model, the collection logic and the events.

The adapter instance takes one or two Global Manager addresses and one credential. It discovers the Local Managers from the Global Manager, and it trusts each Local Manager by the certificate thumbprint the Global Manager already holds for it.

Before any install, the collection ran against the live managers from the tooling container:

| Test | Result |
| --- | --- |
| Normal run | 65 objects, 60 API calls, about 12 seconds |
| Second Global Manager address unreachable | Collection continues, one extra event for the unreachable address |
| No Global Manager reachable | Both Local Managers still collected from the cached location list, 37 objects, marked degraded |

## Step 2: a registry for the adapter image

An SDK pack is not self-contained. The `.pak` file carries the object definitions and a pointer to a container image. The cloud proxy pulls that image from a registry when the adapter instance starts. The lab uses a `registry:2` container on the jump host.

`mp-build` wrote the wrong registry into the pack for a `host:port` address (it fell back to Docker Hub). A short script rewrites two values, `REGISTRY` and `REPOSITORY`, in the `.conf` file inside `adapter.zip`:

```
REGISTRY=10.0.6.27:5000
REPOSITORY=/nsxfederation
DIGEST=sha256:4085...86e9
```

## Step 3: VCF Operations refuses an unsigned pack

The pack is not signed by Broadcom. The upload through the appliance API fails:

```
pak_manager.pakfile.no_signature
The PAK file ... is not signed. It is missing required signature files signature.mf and signature.cert
```

VCF Operations 9 blocks unsigned packs by default, and the "Ignore the PAK file signature checking" box is hidden in the upload dialog until the block is lifted. The switch is in the admin console (`https://<vcf-operations>/admin`), under Administrator Settings, Security Settings: "Allow unsigned PAK installation". Broadcom documents it in KB 415279.

![The admin console of VCF Operations, Administrator Settings, Security Settings: "Allow unsigned PAK installation" is deactivated, with the button that activates it](/images/nsx-federation-mp/ops-admin-unsigned-pak.webp)

With the switch on, there are two ways to install the pack.

### Method 1: by hand in the UI

1. In the admin console, click "Activate unsigned PAK installation".
2. In VCF Operations, open Administration, Integrations, Repository and choose to add a pack.
3. Select the `.pak` file and tick both boxes: install even if already installed, and "Ignore the PAK file signature checking".
4. Upload, accept the warning about the unsigned file, and let the install finish.
5. Back in the admin console, deactivate unsigned PAK installation again.

### Method 2: scripted

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

Step 2 has to come after step 1, because the login to the admin console logs the product UI session out. A session token read before that login is no longer valid.

These calls are internal to the UI. They are unsupported and can change with any release. The switch applies to the whole instance. In the lab it was on only for the duration of the upload.

The first version installed was 1.0.0. After the install, the adapter kind `NSXFederation` and the solution `iSDK_NSXFederation` appear in VCF Operations. The pack shows under Administration, Integrations, Repository with its version. The screenshot dates from after the last upgrade in this post, to 1.1.1.

![Administration, Integrations, Repository filtered on "Federation": the NSX Federation pack, version 1.1.1, with one account](/images/nsx-federation-mp/ops-repository-pack.webp)

## Step 4: the cloud proxy only speaks HTTPS to a registry

The adapter instance was created on the cloud proxy and went from `STARTING` to `FAILED` with an empty status message. The registry log showed no request at all.

A packet capture on the registry port gave the reason. The cloud proxy opened a connection and sent a TLS hello to a registry that served plain HTTP. The cloud proxy does not fall back to HTTP. The registry was recreated with a certificate:

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

## Step 5: the cloud proxy does not trust the certificate

With HTTPS in place the registry log changed (10.0.6.33 is the lab router, which hides the cloud proxy behind NAT):

```
http: TLS handshake error from 10.0.6.33:56544: remote error: tls: bad certificate
```

The cloud proxy runs Docker, and Docker trusts a private registry when its certificate sits in a folder named after the registry:

```
/etc/docker/certs.d/10.0.6.27:5000/ca.crt
```

SSH is disabled on the cloud proxy and stayed disabled. The file was written through vCenter guest operations (`govc guest.run` with the appliance root account). On the next start the cloud proxy pulled the image and the instance reached `STARTED`, then `DATA_RECEIVING`. The Accounts tab shows the instance as "Collecting". This screenshot also dates from version 1.1.1; the page looked the same with 1.0.0.

![Administration, Integrations, Accounts: the account "NSX Federation - nsx-gm-01a" with the status Collecting, on the cloud proxy opscollector-01a](/images/nsx-federation-mp/ops-account-collecting.webp)

An upgrade or a redeploy of the cloud proxy may remove that file. A registry with a certificate from an authority the cloud proxy already trusts avoids the step.

## Step 6: the first three collection cycles

The data does not arrive at once. With a five-minute interval:

| Cycle | What VCF Operations stores |
| --- | --- |
| 1 | The 65 objects and their relationships |
| 2 | Properties and events; metric keys are registered |
| 3 | Metrics |

A check two minutes after the first cycle shows objects with no metrics, which looks like a broken pack. The metrics appear about ten minutes after the start.

## What the pack captures

65 objects of 14 types, 312 metrics per cycle with versions 1.0.0 and 1.0.1. Part 1 planned 12 types. The enterprise-scale check added the federation itself, the sync channel and the realization of an object at a location, and rules became opt-in.

| Object type | Count in the lab | Examples of what is collected |
| --- | --- | --- |
| Federation | 1 | Active Global Managers, locations collected against total, API errors, managers over the call budget |
| Global Manager | 1 | Standby sync status, backup state, open alarms, days to the first certificate expiry |
| Manager node | 1 | Online state |
| Location | 2 | Connection to the Global Manager, collector reachability, nodes online, full sync failures, inter-site BGP sessions, alarms, version |
| Sync channel | 6 | Connection state between two managers |
| Edge cluster | 2 | Inter-site status, members, RTEP addresses |
| Edge node | 4 | RTEP tunnels, inter-site BGP sessions |
| RTEP tunnel | 8 | BGP state, packets, drops and blocked packets in each direction |
| BGP neighbor | 16 | State, prefixes in and out, messages, time established; 4 uplink sessions and 12 internal sessions between edges |
| Tier-0 gateway | 1 | Span, locations realized, locations in error |
| Tier-1 gateway | 3 | Same as the Tier-0 |
| Segment | 5 | Same, plus subnets and connected gateway |
| Global security policy | 1 | Span, realization, rules total and disabled |
| Realization (object at a location) | 14 | Realized or not, status text |

Rule objects are opt-in per policy and were left off for this install.

The pack reports its own health on the federation object: calls made, calls throttled, errors, and whether a manager ran out of its call budget.

![VCF Operations inventory: the NSX Federation object types in the tree, and the summary of location site-a-wld01 with its related objects, alarm counts, backup age and the version mismatch alert](/images/nsx-federation-mp/ops-inventory-location.webp)

### Alerts on day one

Versions 1.0.0 and 1.0.1 ship no alert definition. An event sent by the adapter surfaces in VCF Operations as a "Notification event" alert on the object.

Version 1.0.0 showed three alerts:

- **Warning:** no standby Global Manager is configured for this federation.
- **Information, twice:** version mismatch, each location runs 9.1.1.0 while the Global Manager runs 9.1.1.0100.

A fourth was expected and missing: backup is not configured on the Global Manager. The adapter raised that event in a local run, and the Global Manager object carried the property `backup_enabled = False`, but no alert appeared.

The cause was in the adapter. The backup event carried a fault key, and the three visible events did not. VCF Operations shows an event with a fault key only when the pack ships a matching symptom definition, and this pack has none. Such an event is dropped without an error on either side.

Version 1.0.1 sends every event without a fault key. After the upgrade the adapter instance stayed in `FAILED` until it was stopped and started. The cloud proxy had already pulled the new image. On the first cycle the fourth alert appeared:

- **Immediate:** backup is not configured on Global Manager `gm-site-a`.

![VCF Operations alerts filtered on "Global Manager": the backup alert on gm-site-a, the missing standby on the federation object, and the version mismatch on both locations](/images/nsx-federation-mp/ops-alerts.webp)

All four describe the lab correctly.

The open NSX alarms of each location arrive the same way. While edge nodes were being replaced in the lab on 18 November, the alarms `rtep_bgp_down` and `rtep_connectivity_lost` showed up as alerts on both location objects. VCF Operations cancelled each one about 15 minutes later, with no action in VCF Operations.

## Version 1.1: alert definitions and dashboards

Events as alerts have two limits. They carry no recommendation, and every one is named "Notification event". Version 1.1 adds real alert definitions and three dashboards to the pack.

### Alert definitions

The content is one XML file in `content/alertdefs` with 33 symptoms, 22 alert definitions and 18 recommendations. A script generates it from a table of symptoms, alerts and recommendations. Each symptom is a condition on one metric or one property:

```xml
<SymptomDefinition adapterKind="NSXFederation" id="SymptomDefinition-NSXFederation-gm_backup_off"
                   name="Backup is not configured on the Global Manager" resourceKind="global_manager">
    <State severity="immediate">
        <Condition key="backup_enabled" operator="=" thresholdType="static" type="property"
                   value="False" valueType="string"/>
    </State>
</SymptomDefinition>
```

| Object type | Alerts |
| --- | --- |
| Federation | No active Global Manager; split-brain (two active); a Global Manager not reachable by the collector; no standby configured; partial collection (API call budget used) |
| Global Manager | Cluster degraded; backup missing, old or failed; certificate about to expire |
| Location | Connection to the active Global Manager lost; Local Manager not reachable by the collector; certificate not trusted; full sync not completed; inter-site BGP sessions down; version differs from the Global Manager; backup missing or old; certificate about to expire; Local Manager cluster degraded |
| Sync channel | Channel down |
| Edge node | Inter-site BGP sessions down |
| Tier-0 BGP neighbor | Session down; uplink session established with no prefixes received |
| Stretched object at a location | Not realized |

Three changes in the adapter go with the definitions:

- Three derived metrics, because a symptom compares a metric with a fixed value and cannot subtract two metrics: Global Managers not reachable, inter-site BGP sessions not established, locations not realized. With them the pack sends 331 metrics per cycle in the lab.
- A condition that has an alert definition no longer sends an event. Without that change the same condition would show twice, once as the alert and once as a "Notification event".
- Conditions without a definition stay events: the forwarded NSX alarms and the sync status of a standby Global Manager, which the lab cannot test.

The symptoms and alert definitions were tested before any pack build, at first without the recommendations. The suite API imports alert content from a zip file (`POST /suite-api/api/content/operations/import`). It needs neither a pack install nor the unsigned-pack switch. About five minutes after the import the four alerts that match the lab fired: no standby, Global Manager backup, and the version mismatch on both locations. The other 18 have not fired, because the lab has no such fault.

The SDK schema does not check one rule: a recommendation ID has to start with `Recommendation-df-`. Without that prefix VCF Operations rejects the whole alert file. Version 1.1.1 exists to correct that prefix. Installed with the pack, the definitions raise the same four alerts, now under their own names:

![The alert list filtered on "Global Manager" with version 1.1.1: the four alerts now carry the names of their definitions](/images/nsx-federation-mp/ops-alerts-defined.webp)

An alert from a definition also carries its recommendation and the symptom that triggered it:

![Detail of the alert "Global Manager backup is missing, old or failed" on gm-site-a: description, recommendation and the symptom "Backup is not configured on the Global Manager"](/images/nsx-federation-mp/ops-alert-recommendation.webp)

### Dashboards

Three dashboards sit in a folder named "NSX Federation":

| Dashboard | Content |
| --- | --- |
| Federation Overview | Open alerts on the pack's objects; a list of the federation, the Global Managers and the locations; key values, properties and related objects of the selected one |
| Inter-site Connectivity | Sync channels and edge nodes, RTEP tunnels, Tier-0 BGP neighbors; key values of the selected row |
| Stretched Objects | Stretched gateways, segments and policies with their span; the realization of each object at each location |

Every dashboard works the same way: a list drives a scoreboard, and a click on a scoreboard value draws its trend.

![The Federation Overview dashboard: the four open alerts grouped under three definitions, the list of federation objects with gm-site-a selected, its key values, related objects and properties](/images/nsx-federation-mp/ops-dashboard-overview.webp)

![The Inter-site Connectivity dashboard: sync channels and edge nodes, RTEP tunnels, Tier-0 BGP neighbors and the key values of the selected edge node](/images/nsx-federation-mp/ops-dashboard-connectivity.webp)

![The Stretched Objects dashboard: the stretched gateways, segments and policies, the realization per location, and the key values, properties and related objects of segment fed-stretched](/images/nsx-federation-mp/ops-dashboard-stretched.webp)

A dashboard file is JSON, and the SDK documentation describes one way to get it: build the dashboard in the UI and export it. For this pack a script writes the JSON from the same object model as the adapter. The format was worked out from the dashboards VCF Operations exports through the suite API (`/api/content/operations/export`). In the file, an object type is a placeholder such as `resourceKind:id:3_::_`, and a table at the top of the file maps each placeholder to an adapter kind and an object type.

Two behaviours cost time:

- An imported dashboard shows empty widgets at first. The object lists show every object of the instance and the scoreboards show nothing. A background job in VCF Operations completes the import a few minutes later, and the widgets then get their settings.
- The object lists keep the default columns. The added metric columns sit to the right, behind a scroll bar. A saved column layout in the file was not applied at import.

### Coverage by object type

Each object type of the pack was compared with the built-in NSX integration (50 object types, 423 alert definitions). The result per type:

| Object type | Metrics | Alert definitions | Dashboard |
| --- | --- | --- | --- |
| Federation | 14 | 5 | Federation Overview |
| Global Manager | 10 | 3 | Federation Overview |
| Global Manager node | 1 | None, covered by the cluster alert of the Global Manager | Related objects only |
| Location | 14 | 9 | Federation Overview |
| Sync channel | 1 | 1 | Inter-site Connectivity |
| Edge cluster | 4 | None | Inter-site Connectivity |
| Edge node | 4 | 1 | Inter-site Connectivity |
| RTEP tunnel | 9 | None | Inter-site Connectivity |
| Tier-0 BGP neighbor | 7 | 2 | Inter-site Connectivity |
| Stretched Tier-0, Tier-1, segment | 4 each | None, covered by the realization alert | Stretched Objects |
| Global security policy | 7 | None, covered by the realization alert | Stretched Objects |
| Stretched object at a location | 5 | 1 | Stretched Objects |
| Global firewall rule (opt-in) | 4 | None | None |

The review found these gaps:

- **RTEP tunnel.** A tunnel whose BGP session is down raises the alert of its edge node. The tunnel itself has no alert, and neither do dropped or blocked packets.
- **Counters are totals.** Packets, bytes, drops and rule hits are the running totals NSX returns. The pack computes no rate, so a threshold on drops per minute is not possible yet.
- **Edge cluster.** The inter-site status of the cluster is a property with no alert.
- **Stretched objects in error.** The "locations in error" metric has no alert. An object that fails at a location only raises the alert of that realization.
- **Global Manager alarms.** The pack counts the open alarms of the Global Manager and forwards only those of the Local Managers as events.
- **Units.** No metric declares a unit, so hours, days, bytes and packets show as plain numbers.
- **Views, reports and super metrics.** The pack ships none.

## Sending the alerts by email

The pack raises the alerts and VCF Operations sends them. That takes two settings: an outbound instance that knows the mail server, and a notification rule that picks the alerts. Both are under Administration, Configurations, in the Alerts group. The lab has no outbound instance and no mail server, so the forms below were opened in the UI and not saved, and no email was sent.

### 1. The outbound instance

1. Open Administration, Configurations, Outbound Settings and click Add.
2. Choose the plugin type "Standard Email Plugin" and give the instance a name.
3. Fill in SMTP Host and SMTP Port, the sender address and the sender name. Tick "Use Secure Connection" and "Requires Authentication" if the mail server asks for them.
4. Click Test, then Save.

![Create New Outbound Instance with the Standard Email Plugin: SMTP host and port, secure connection, sender address and name, credential and receiver address](/images/nsx-federation-mp/ops-email-plugin.webp)

The same list offers a Webhook, Slack, ServiceNow and SNMP trap plugin. The rule in the next step works with any of them.

### 2. The notification rule

1. Open Administration, Configurations, Notifications and click Add. The wizard has five steps.
2. **Notification:** a name, for example "NSX Federation alerts by email".
3. **Define Criteria:** under Object Scope choose "Object Type" and select the object types of the NSX Federation adapter. Under Alert Scope, Category can stay on all alerts or be narrowed to "Alert Definition" to pick single definitions. Criticality limits the rule, for example to Immediate and Critical. "Notify On" selects which status changes of an alert send a message.
4. **Set Outbound Method:** the email plugin and the instance from part 1.
5. **Select Payload Template:** the template that formats the message. Templates are managed under Payload Templates on the same Configurations page.
6. **Test Notification**, then Create.

Steps 4 to 6 come from the step names of the wizard. Without an outbound instance the lab could not go through them.

![The Define Criteria step of a notification rule: object scope, alert scope with category, criticality and control state, and the status changes to notify on](/images/nsx-federation-mp/ops-notification-rule.webp)

A rule on the pack's object types, limited to Immediate and Critical, sends the outages by email. The version mismatch and the missing standby stay in the UI. A second rule without the criticality limit, sent to a team mailbox, covers them.

## What is not there yet

- An inventory tree (traversal specification).
- The gaps listed under coverage: alerts for RTEP tunnels, edge clusters, stretched objects in error and Global Manager alarms, rates for the counters, and units on the metrics.
- A test of the email notification, which needs a mail server the lab does not have.
- Tests for 18 of the 22 alert definitions, which need faults the lab does not have.
- Clean-up of replaced objects. An edge node that is redeployed gets a new ID in NSX and becomes a new object. After the edge replacement, 26 old edge node and tunnel objects stayed in the inventory without data, next to the 65 live ones.
- Latency between managers. NSX measures it and shows it in its UI; the API that carries it was not found.
- The firewall state of the hosts, the second open item from part 1.
- Links to the objects of the built-in NSX integration.
- Tests for a standby Global Manager, a switchover and split-brain. The lab has a single Global Manager.
- A signed pack. Every new version needs the unsigned-pack switch.

## Sources

- Lab: nested VCF 9.1.1 Holodeck, NSX 9.1.1.0 (Local Managers) and 9.1.1.0100 (Global Manager), VCF Operations 9.1.1.0 build 25795063. Build and install on 17 and 18 November 2026, versions 1.0.1, 1.1.0 and 1.1.1 on 18 November.
- [VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction)
- Broadcom KB [415279](https://knowledge.broadcom.com/external/article/415279/error-no-signature-found-on-pak-file-wh.html): "No signature found on PAK file" while installing a management pack
- [Docker: verify repository client with certificates](https://docs.docker.com/engine/security/certificates/)
