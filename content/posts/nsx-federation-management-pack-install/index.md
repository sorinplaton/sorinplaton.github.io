---
title: "Monitoring NSX Federation in VCF Operations, part 2: building, installing and what the pack captures"
date: 2026-11-18
description: "Part 2 of the NSX Federation management pack for VCF Operations 9.1.1: what the pack monitors on a two-location lab federation (76 objects, 586 metrics, 87 alert definitions, four custom groups, five dashboards, thirteen views and two reports), the build of the adapter with the Integration SDK, the install step by step with screenshots (HTTPS registry, certificate trust on the cloud proxy, unsigned pack, account), and how to send the alerts by email."
summary: "The adapter collects a two-location NSX 9.1 Federation in 25 to 45 seconds. Running it inside VCF Operations 9.1.1 takes an image registry served over HTTPS, a cloud proxy that trusts the registry certificate, and the setting that allows an unsigned pack. This post lists what the pack monitors (objects, metrics, alerts, groups, dashboards, views), documents each install step with screenshots, and shows how to send the alerts by email and how the pack was tested in the lab."
tags: ["VCF 9.1.1", "NSX Federation", "VCF Operations", "Management pack", "Integration SDK", "Lab"]
categories: ["Field notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-federation-mp/ops-dashboard-stretched.webp"]
params:
  seoDescription: "Building and installing a custom NSX Federation management pack in VCF Operations 9.1.1: HTTPS registry, certificate trust, unsigned pack, and the objects, metrics, alerts, groups, dashboards and views it provides."
---

**In short**

- In the lab, the pack shows a two-location NSX Federation in VCF Operations 9.1.1 as 76 objects, 586 metrics, 87 alert definitions and five dashboards.
- The install needs an image registry served over HTTPS, a cloud proxy that trusts the registry certificate, and the setting that allows an unsigned pack.
- One collection cycle of the lab federation takes 25 to 45 seconds.
- The steps are in [Install the pack](#install-the-pack).

[Part 1](/posts/nsx-federation-management-pack-design/) ended with a design and no code. The pack now runs in VCF Operations 9.1.1, and writing the adapter took less time than installing it.

The pack described here is version 1.13.0. Its source is on GitHub: [sorinplaton/vcf-ops-nsx-federation-mp](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp). Three files in the repository hold the full reference:

| File | Content |
| --- | --- |
| [docs/FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.md) | Every object type, metric, property and alert, with the condition, threshold and recommendation of each alert |
| [docs/INSTALL.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/INSTALL.md) | The install as a checklist, with every command and the machine it runs on |
| [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/OPERATIONS.md) | Account settings, certificate trust, sizing, upgrade, troubleshooting and tests |

## The lab

The lab is the federation described in [part 1](/posts/nsx-federation-management-pack-design/): two locations, `site-a-wld01` and `site-a-wld03`, and VCF Operations 9.1.1 with one cloud proxy, `opscollector-01a`. For this part it also has a standby Global Manager: `nsx-gm-01a` (`gm-site-a`) is active and `nsx-gm-01b` (`gm-site-b`) is its standby. The build runs on a Linux jump host with Docker, which also runs the image registry on port 5000.

![The part 2 lab: the active Global Manager nsx-gm-01a and the standby nsx-gm-01b, the locations site-a-wld01 and site-a-wld03 with their Local Managers and two-node edge clusters linked by RTEP, VCF Operations with the cloud proxy opscollector-01a, and the jump host that builds the pack and serves its image through the lab router](/images/nsx-federation-mp/lab-topology.svg?v=7)

The NSX Federation adapter in the drawing is the collector of the pack: a container that runs on the cloud proxy, reads the Global Managers and the Local Managers through the NSX API, and sends the result to VCF Operations.

The standby was deployed from the same 9.1.1 appliance as the active Global Manager, in the Small size, on the cluster of `site-a-wld03`, with an address and a DNS record in the management network.

It was registered on the active Global Manager through the API, as `gm-site-b` in STANDBY mode, with its address, admin credentials and certificate thumbprint (`PUT /global-manager/api/v1/global-infra/global-managers/gm-site-b`). The first full sync completed within minutes.

## What the pack monitors

The pack provides:

| Content | Count |
| --- | --- |
| Object types | 16 (76 objects of 15 types in the lab) |
| Metrics per cycle in the lab | 586, in 25 groups |
| Alert definitions | 87, with 153 symptoms and 53 recommendations |
| Custom groups | 4 |
| Dashboards | 5 |
| List views | 13 |
| Reports | 2 |

The complete list of objects, metrics and alerts is in [FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.md).

### Objects

In the lab the pack creates 76 objects of 15 types, among them 8 RTEP tunnels, 16 Tier-0 BGP neighbors and 14 realizations of stretched objects at a location. The sixteenth type, the global firewall rule, is opt-in per policy and was left off for this install. Edge nodes, tunnels, sync channels and Tier-0 service routers are identified by NSX IDs, so a renamed or redeployed edge node keeps its objects and their history. The pack also ships an inventory tree, under Inventory, Detailed View, last tab, NSX Federation. What each object type collects is in [the object types of FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.md#object-types).

### Custom groups

Four custom groups come with the pack, under Inventory, Custom Groups, Environment: all objects of the pack (76 members in the lab), managers and locations (9), inter-site connectivity (43) and stretched objects (24). Membership follows the object type, so a new location or a new tunnel joins its group without an edit. The script `install/federation_groups.py` adds one group per federation, "NSX Federation - <federation>", selected on the federation ID that every object of the pack carries. The groups are listed in [the groups section of FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.md#dashboards-views-reports-and-groups).

### Metrics

The 586 metrics of the lab sit in 25 groups, as in the built-in integrations, and the key of a metric carries its group, `sync|standby_in_sync` for example. Twenty-six of them are key indicators, listed first in the summary of an object. The adapter turns the running totals NSX returns into rates, such as drops per minute on an RTEP tunnel, and computes the share of packets a tunnel dropped once at least 50 packets have passed since the previous cycle. Each location also carries what the Location Manager page of NSX shows for it: replication flows, latency and sync queues, with the latency compared to the maximum RTT set in NSX (250 ms by default). Every metric and property is in [the metrics of FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.md#metrics-and-properties).

The federation object also carries figures for the whole federation. On 22 November they read:

| Figure | Lab value |
| --- | --- |
| Locations healthy | 2 of 2 (100%) |
| RTEP tunnels, BGP down, drops | 8, none down, no drops |
| Days to the first certificate expiry | 328.5 (a Local Manager certificate) |
| Open critical alarms | 1 |
| Largest clock difference | 0.07 seconds |
| Most used configuration limit | 97.6% |
| Overridden objects | None |
| Days to the first password expiry | 35 |
| Firewall rules to review | None |

### Alerts

The 87 alert definitions cover every object type, from a federation with no active Global Manager to a flapping Tier-0 BGP session. A symptom is a condition on one metric, one property or one event, here on a property:

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

Twenty-one alerts lower the Risk badge instead of Health, for backups, certificate expiry and configuration limits among others. Symptoms that can flap need two collection cycles (10 minutes) to raise an alert and two to clear it, and three alerts use a dynamic threshold that needs days of history. A VCF Operations policy can override a fixed threshold under Override Symptoms without a new build. Four alerts fire in the lab, from three definitions: the missing backup on both Global Managers, a paused NSX upgrade on the Local Manager of site-a-wld01, and a configuration limit at 97.6% on site-a-wld03. The fixed thresholds are in the [Thresholds table of FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.md#thresholds), and each definition with its condition and recommendation is in [the alerts of FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.md#alerts).

### NSX alarms and the built-in NSX integration

The adapter forwards the federation alarms of the Global Manager and of each Local Manager as events, in the same form as the built-in NSX integration, and gives the 15 federation alarm types of NSX 9.1.1 their own alert and recommendation. The built-in integration covers only the managers added to it as NSX accounts, so the alarms of the Global Manager reach VCF Operations only through this pack. For a Local Manager the built-in integration already monitors, the adapter leaves the alarms, edge alerts and configuration limit alert to it and links the edge objects to its own. In the lab that is site-a-wld01, and the pack raises these alerts for site-a-wld03. A federation alarm without a definition, and every critical alarm of the Global Manager, arrives as a "Notification event" alert with no recommendation. The rules are in [the collection summary of FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.md#what-the-collection-does).

### Dashboards

Five dashboards sit in a folder named "NSX Federation": Federation Overview, Location Matrix, Switchover Readiness, Inter-site Connectivity and Stretched Objects. In every dashboard a list drives a scoreboard, and in all of them except Stretched Objects a click on a scoreboard value draws its trend. The list of dashboards is in [FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.md#dashboards-views-reports-and-groups).

Federation Overview with gm-site-a selected. The open alerts are the backup and the paused upgrade:

![The Federation Overview dashboard: two open alert definitions (backup, paused upgrade), the list with gm-site-a and gm-site-b, and the scoreboard of gm-site-a with Standby in sync 1, nodes offline 0 and certificate expiry](/images/nsx-federation-mp/ops-dashboard-standby.webp)

With the federation selected, the scoreboard ends with the federation rollups. The screenshot dates from 19 November, so its certificate and alarm figures differ from the 22 November table above:

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

Nine list views cover locations, sync channels, RTEP tunnels, Tier-0 BGP neighbors, edge capacity, Tier-0 service routers, the firewall rule review, the recent changes and the realization of stretched objects, and four more show availability over the last 30 days. Two reports put them in a PDF: "NSX Federation - Health report", with the first nine views, and "NSX Federation - Availability report", with the four others, meant to be scheduled monthly on the federation object. The view names are in [FEATURES.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/FEATURES.md#dashboards-views-reports-and-groups).

## Install the pack

The install runs in six steps, from a jump host that builds the adapter and serves its image.

![Build and deployment of the pack: the GitHub repository is cloned on the jump host, the tooling container builds the image and the .pak, the image goes to the HTTPS registry on the jump host, the .pak is installed in VCF Operations with the unsigned PAK switch on, the account deploys the adapter on the cloud proxy, which pulls the image by digest through the lab router, reads the Global Managers and Local Managers over their REST API, and sends the data to the analytics node](/images/nsx-federation-mp/build-topology.svg?v=11)

### Jump host prerequisites

The jump host builds the pack, runs the tests and serves the image. Before the build it needs:

- **Linux with Docker Engine**. The SDK tools, the tests and the registry run in containers. The commands in this section are written for a fresh Ubuntu.
- **`git`**, to clone the repository.
- **Python 3**, any recent version, for the scripts that run outside the tooling image: `tools/gen_*.py` (alerts, dashboards, views, groups) and `tools/repack.py`.
- **`openssl`**, to create the registry certificate (step 1).
- **Root access through `sudo`**, for the package install and for the `docker` commands. The commands carry `sudo` where they need it, so the user does not have to be in the `docker` group.
- **Outbound access** to GitHub, Docker Hub and PyPI, for the repository, the `python:3.11-slim` and `registry:2` images and the SDK package.
- **TCP 443 to every NSX manager**, the Global Managers and the Local Managers, for the local test run (`tools/localrun.py`).
- **The registry port open to the cloud proxy**, 5000 in the lab. Here the cloud proxy reaches it through the lab router.

On a fresh Ubuntu, the tools install as follows. Docker comes from the Docker repository, so that `docker build` uses BuildKit.

*Run on: the jump host.*

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git openssl python3 python3-requests
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
sudo docker run --rm hello-world
```

The commands in the steps that follow read the jump host address from one variable, `IP`. It is the address the cloud proxy uses to reach the registry. `10.x.x.x` has to be replaced with that address, and the variable set again in every new shell:

```bash
IP=10.x.x.x
```

Two more tools are only needed for some steps, and can sit on another host:

- `govc` and access to the vCenter that runs the cloud proxy VM, to copy the certificate to the cloud proxy (step 3).
- `google-chrome`, `node` and the Python `requests` module, for the scripted install of the unsigned pack (optional, see docs/INSTALL.md in the repository), with HTTPS access to VCF Operations.

### Build the adapter

The pack uses the VCF Operations Integration SDK 1.3.1, introduced in [part 1](/posts/nsx-federation-management-pack-design/). Its tools need a Python version the jump host does not carry, so they run from a small tooling image:

*Found in: `tooling/Dockerfile` in the repository, built on the jump host.*

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends docker.io git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir vmware-aria-operations-integration-sdk pytest
WORKDIR /work
```

The adapter code is in two Python files:

| File | Role |
| --- | --- |
| `nsxclient.py` | A read-only NSX REST client with cursor paging, a call budget per manager, back-off on HTTP 429 and 503, a certificate check by SHA-256 fingerprint or by CA chain and host name, and a small on-disk cache for slow-moving data |
| `adapter.py` | The object model, the collection logic and the events |

The `.pak` file carries the object definitions and a pointer to the adapter image, which the cloud proxy pulls from the registry when the adapter instance starts. `mp-build` builds the pack and pushes the adapter image to the registry. The build commands are step 2 of the install, because they push the image to the registry of step 1.

The six steps below are in the order that works. Each failure the lab hit is described under the step that prevents it.

### Step 1: serve the registry over HTTPS

The cloud proxy talks to the registry over HTTPS only.

In production the image goes to the registry already in place (Harbor, for example). When its certificate comes from an authority the cloud proxy already trusts, step 3 is not needed. The lab has no such registry, so it runs a `registry:2` container on the jump host with a self-signed certificate:

*Run on: the jump host.*

```bash
IP=10.x.x.x
mkdir -p ~/fedmp-registry/certs && cd ~/fedmp-registry
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout certs/registry.key -out certs/registry.crt -subj "/CN=fedmp-registry" \
  -addext "subjectAltName=IP:$IP,IP:127.0.0.1,DNS:localhost"

sudo docker run -d --name fedmp-registry --restart unless-stopped \
  -p $IP:5000:5000 -p 127.0.0.1:5000:5000 \
  -v registry-data:/var/lib/registry -v $PWD/certs:/certs:ro \
  -e REGISTRY_HTTP_TLS_CERTIFICATE=/certs/registry.crt \
  -e REGISTRY_HTTP_TLS_KEY=/certs/registry.key registry:2
```

The build pushes the image to `localhost:5000`. The certificate therefore also names `127.0.0.1` and `localhost`, and the registry listens on the local address as well.

Check, from the jump host:

```bash
curl --cacert ~/fedmp-registry/certs/registry.crt https://$IP:5000/v2/_catalog
```

A new registry answers `{"repositories":[]}`. It stays empty until the build pushes the adapter image.

The address in the certificate is the one the cloud proxy uses to reach the registry. The same address names the certificate folder in step 3, and `tools/repack.py` writes it into the pack. A registry moved to another host therefore needs a new certificate, a new folder on the cloud proxy and a rewritten pack.

The first registry in the lab served plain HTTP. With it, the adapter instance went from `STARTING` to `FAILED` with an empty status message, and the registry log showed no request at all.

A packet capture on the registry port showed why: the cloud proxy opened a connection and sent a TLS hello. It does not fall back to HTTP, so the registry was recreated with the certificate above.

### Step 2: build the pack and push the image

*Run on: the jump host.*

```bash
IP=10.x.x.x
git clone --branch v1.13.0 https://github.com/sorinplaton/vcf-ops-nsx-federation-mp.git
cd vcf-ops-nsx-federation-mp
sudo docker build -t fedmp-tooling tooling

sudo docker run --rm --network host -u $(id -u):$(getent group docker | cut -d: -f3) -e HOME=$PWD/.sdkhome \
  -v /var/run/docker.sock:/var/run/docker.sock -v $(which docker):/usr/bin/docker -v $PWD:$PWD -w $PWD/pack \
  fedmp-tooling mp-build --no-ttl -r localhost:5000/nsxfederation \
  --registry-username lab --registry-password lab

python3 tools/repack.py pack/build/NSXFederation_1.13.0.pak $IP:5000 nsxfederation
curl -sk https://$IP:5000/v2/nsxfederation/tags/list
```

The image is pushed to `localhost:5000`. Docker accepts the self-signed certificate of a registry on the local address, so the jump host needs no certificate folder of its own. The registry of step 1 has no login. `mp-build` still asks for a user and a password, and any value works. The last command lists the version that was pushed.

When the registry address is given as `host:port`, `mp-build` writes Docker Hub into the pack instead of that address. A short script, `tools/repack.py`, rewrites two values, `REGISTRY` and `REPOSITORY`, in the `.conf` file inside `adapter.zip`. It writes the address the cloud proxy uses, `$IP:5000`:

*Found in: the `.conf` file inside `adapter.zip` in the `.pak`, rewritten by `tools/repack.py` on the jump host.*

```
REGISTRY=10.x.x.x:5000
REPOSITORY=/nsxfederation
DIGEST=sha256:dd80...b4cb
```

### Step 3: make the cloud proxy trust the registry certificate

This step applies to a registry whose certificate the cloud proxy does not trust yet, such as the self-signed one of the lab. Without that trust, the registry log shows this (10.0.6.33 is the lab router, which hides the cloud proxy behind NAT):

*Output: the log of the registry container on the jump host.*

```
http: TLS handshake error from 10.0.6.33:56544: remote error: tls: bad certificate
```

The cloud proxy runs Docker, and Docker trusts a private registry when its certificate sits in a folder named after the registry:

*Found on: the cloud proxy `opscollector-01a`.*

```
/etc/docker/certs.d/10.x.x.x:5000/ca.crt
```

SSH is disabled on the cloud proxy and stayed so. The file was written through vCenter guest operations, with `govc` and the appliance root account. The lab used `govc guest.run`. The two commands below do the same with `guest.mkdir` and `guest.upload`:

*Run on: any host with `govc` and access to the vCenter that runs the cloud proxy VM.*

```bash
IP=10.x.x.x
export GOVC_URL=<vcenter> GOVC_USERNAME=<user> GOVC_PASSWORD='<password>' GOVC_INSECURE=1
VM=<cloud-proxy-vm-name>
govc guest.mkdir -vm "$VM" -l 'root:<proxy-root-password>' -p /etc/docker/certs.d/$IP:5000
govc guest.upload -vm "$VM" -l 'root:<proxy-root-password>' -f ~/fedmp-registry/certs/registry.crt \
  /etc/docker/certs.d/$IP:5000/ca.crt
```

An upgrade or a redeploy of the cloud proxy may remove this file.

### Step 4: install the unsigned pack

The pack is not signed by Broadcom. The upload through the appliance API fails:

*Output: the response of the VCF Operations appliance API to an upload from the jump host.*

```
pak_manager.pakfile.no_signature
The PAK file ... is not signed. It is missing required signature files signature.mf and signature.cert
```

VCF Operations 9 blocks unsigned packs by default. The "Ignore the PAK file signature checking" box stays hidden in the upload dialog until the block is lifted.

The switch that lifts it, "Allow unsigned PAK installation", sits in the admin console (`https://<vcf-operations>/admin`) under Administrator Settings, Security Settings. Broadcom documents it in KB 415279. Before it is turned on, the page looks like this:

![The admin console of VCF Operations, Administrator Settings, Security Settings: "Allow unsigned PAK installation" is deactivated, with the button that activates it](/images/nsx-federation-mp/ops-admin-unsigned-pak.webp)

The switch stays on only for the install:

1. In the admin console, click "Activate unsigned PAK installation".
2. In VCF Operations, open Administration, Integrations, Repository and choose to add a pack.
3. Select the `.pak` file and tick both boxes: install even if already installed, and "Ignore the PAK file signature checking".
4. Upload, accept the warning about the unsigned file, and let the install finish.
5. Back in the admin console, deactivate unsigned PAK installation again. The console refuses while the install is still running.

The repository also holds a scripted version of these steps, `install/upi.sh` and `install/install_unsigned.sh`; [docs/INSTALL.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/INSTALL.md) describes it.

After the install, the adapter kind `NSXFederation` and the solution `iSDK_NSXFederation` appear in VCF Operations. The pack shows under Administration, Integrations, Repository with its version:

![Administration, Integrations, Repository: the NSX Federation pack, version 1.13.0, with one account](/images/nsx-federation-mp/ops-repository-pack.webp?v=9)

### Step 5: create the account

The adapter instance is called an account in the VCF Operations UI. Create it under Administration, Integrations, Accounts, as an account of the type NSX Federation, with the cloud proxy as its collector.

The account takes one or two Global Manager addresses and a credential, plus an optional one for the Local Managers. With both addresses, the pack follows the active role when the Global Managers switch over. One account monitors one federation.

The other settings are optional: the security policies whose rules become objects, how the pack works with the built-in NSX integration, and the deletion of objects that left NSX (off by default). [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/OPERATIONS.md) describes each setting.

Validate the connection and accept the Global Manager certificates when the account is saved. The adapter discovers the Local Managers from the Global Manager, and checks the certificate of each one against the thumbprint the Global Manager already holds for it. Once a certificate is checked, every connection of the cycle is bound to its fingerprint, so the credentials are not sent to a server that presents another certificate.

The certificate check has three modes:

| Mode | Behaviour |
| --- | --- |
| enforce (default) | Stops the collection of a manager whose certificate does not match or that has no trusted certificate on record |
| ca | Works like "enforce" and also trusts a certificate that chains to a trusted CA and is valid for the address the account uses |
| report | Collects and raises an alert |

With the default, the managers are not collected until a certificate is accepted.

In the "ca" mode, the address of the account must be a name or IP address listed in the certificate. The trusted CAs are those in the account setting Trusted CA certificates (PEM), which accepts PEM text pasted on one line, any CA certificate accepted on the account, and the CA store of the adapter image. A CA imported into the cloud proxy is not part of that store.

The pack only reads from NSX, so a read-only account is enough. In the lab, the account logs in with NSX's built-in `audit` user on the two Global Managers and the two Local Managers. It collects the same objects as with `admin`, with no failed call.

With the registry trusted, the cloud proxy pulls the image when the instance starts, and the instance goes to `STARTED`, then `DATA_RECEIVING`. The Accounts tab shows the instance as "Collecting":

![Administration, Integrations, Accounts: the account "NSX Federation - nsx-gm-01a" with the status Collecting, on the cloud proxy opscollector-01a](/images/nsx-federation-mp/ops-account-collecting.webp)

### Step 6: wait three collection cycles

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

Metrics return after three cycles and rates after four.

An install keeps the alert, symptom and recommendation definitions that the new version no longer ships. `install_unsigned.sh` runs `install/cleanup_content.py` after the install to delete those orphans. After an install in the UI, run the script by hand with `--delete`. It only deletes IDs that the pack generates, and it keeps an orphan that an alert definition outside the pack still uses.

A change in how the adapter identifies an object creates a new object. The old one stays in the inventory without data until it is deleted, which also deletes its history. `cleanup_content.py --legacy-objects` lists the edge nodes, RTEP tunnels and sync channels that are still identified by a name, and `--legacy-objects --delete` deletes them.

## Sending the alerts

The pack raises the alerts and VCF Operations sends them. That takes two settings, both under Administration, Configurations, in the Alerts group:

- an outbound instance that knows the mail server;
- a notification rule that picks the alerts.

The lab has no mail server, so the email screenshots show the forms before saving. The rule was tested with a webhook instead.

### The outbound instance

1. Open Administration, Configurations, Outbound Settings and click Add.
2. Choose the plugin type "Standard Email Plugin" and give the instance a name.
3. Fill in SMTP Host and SMTP Port, the sender address and the sender name. Tick "Use Secure Connection" and "Requires Authentication" if the mail server asks for them. The form also has a credential and a receiver address.
4. Click Test, then Save.

The form, as opened in the lab:

![Create New Outbound Instance with the Standard Email Plugin: SMTP host and port, secure connection, sender address and name, credential and receiver address](/images/nsx-federation-mp/ops-email-plugin.webp)

The same list offers a Webhook, Slack, ServiceNow and SNMP trap plugin. The notification rule below works with any of them.

### The notification rule

1. Open Administration, Configurations, Notifications and click Add. The wizard has five steps, items 2 to 6 below.
2. **Notification:** a name, for example "NSX Federation alerts by email".
3. **Define Criteria:** under Object Scope choose "Object Type" and select the object types of the NSX Federation adapter. Under Alert Scope, Category can stay on all alerts or be narrowed to "Alert Definition" to pick single definitions. Criticality limits the rule, for example to Immediate and Critical. "Notify On" selects which status changes of an alert send a message.
4. **Set Outbound Method:** the email plugin and the outbound instance created above.
5. **Select Payload Template:** the template that formats the message. Templates are managed under Payload Templates on the same Configurations page.
6. **Test Notification**, then Create.

The Define Criteria step looks like this:

![The Define Criteria step of a notification rule: object scope, alert scope with category, criticality and control state, and the status changes to notify on](/images/nsx-federation-mp/ops-notification-rule.webp)

A rule limited to Immediate and Critical sends only those two levels by email. The Information and Warning alerts stay in the UI. A second rule without the criticality limit, sent to a team mailbox, covers them.

### The same rule with a script

`install/notification_rules.py` creates the same rule for an outbound instance that already exists, with the default template of its plugin type.

It takes the alerts on every object type of the pack, at Immediate and Critical by default (`--min-criticality warning` adds the warnings). It sends them when they are new, updated and cancelled, so a ticket or a chat thread also learns that the alert cleared. `--group` limits it to the members of a group, for example the group of one federation.

In the lab, the outbound instance was a webhook pointing to a small HTTP listener on the jump host, and the rule was limited to the group of the federation.

Each payload carries the object, its type, the alert name, the criticality, the start and cancel dates, and the recommendation. The test outbound instance and the rule were deleted afterwards.

## How the collection behaves

The behaviours below change how an alert should be read. [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/OPERATIONS.md) has the full detail.

### Failed calls are reported as unknown

Most of the collection is made of secondary calls: cluster status, alarms, backups, certificates, sync status. When one of them fails, the adapter sends nothing for the values that depend on it.

A failed status call does not read as "location not connected", and a failed backup call does not read as "backup not configured". VCF Operations keeps the last known value, and no alert fires on data that was never received.

The federation object counts these calls in the metric "NSX API calls without an answer" and lists them in a property, with the manager, the call and the HTTP status. Four alerts watch the collection itself: data gaps, a slow or throttled collection, a collection without any Global Manager, and a manager that used its whole call budget.

A call that times out is retried once before it counts as failed. A manager that times out three times in a row, or keeps answering HTTP 429 or 503, is skipped for the rest of the cycle.

### One alert for a location outage

A location that loses its connection to the Global Manager takes many objects with it: tunnels, BGP sessions, sync channels and every stretched object that spans it. Each of them has an alert.

While the active Global Manager reports a location as not connected, the adapter writes the name of that location to a property, "Locations in outage", on the objects that depend on it. Twenty-two alert definitions, and the alarm alerts other than those between Global Managers, require that this property reads "none". They stay quiet during the outage, and "Location lost its connection to the active Global Manager" is the alert to act on.

In the alert XML this is a second symptom with `negateCondition="true"`:

*Found in: the alert file of the pack, in `content/alertdefs`.*

```xml
<SymptomSet applyOn="self" operator="and">
    <Symptom ref="SymptomDefinition-NSXFederation-seg_error"/>
    <Symptom ref="SymptomDefinition-NSXFederation-outage_segment" negateCondition="true"/>
</SymptomSet>
```

The outage symptom clears three cycles after the location is back, so the end of an outage raises no alert either. A fault at another location still raises its alert.

### The standby Global Manager

The standby carries its sync state: sync healthy (1 when in sync), entries left to send, minutes out of sync and hours since the last full sync. NSX 9.1.1 reports the normal state of a standby as ONGOING, so the pack also checks for a delta sync after a completed full sync. In the lab the sync metric reads 1 with no entries left.

Each standby also says whether it can take over as the active Global Manager now. It reads ready (1), or not ready (0) with the reasons in a property: not in sync, an unstable cluster, nodes offline, a different version, an expired certificate or an upgrade in progress. When a value could not be read, the metric is also 0 and the property says what is unknown. In the lab, gm-site-b reads ready.

Take a planned switchover: the question is whether the other site can take over tonight. By hand, that means logging in to the four NSX managers of this lab and comparing screens. The pack answers it in every collection cycle with two values on the federation object: "A standby Global Manager is ready to take over" and "RTEP tunnels with inter-site BGP down". The chain behind them:

| Step | Host called | What is read | What it gives |
| --- | --- | --- | --- |
| 1 | Both Global Managers | `/api/v1/sites/self`, `/api/v1/cluster/status`, certificates, upgrade summary | Which one is active, and the version, cluster state, certificate expiry and upgrade state of each |
| 2 | The active Global Manager | `/api/v1/sites`, `/api/v1/sites/status` | The sync state of the standby, and the address and certificate thumbprint of each Local Manager |
| 3 | Each Local Manager, at the address from step 2 | `/api/v1/transport-nodes` | The ID of each federation edge node |
| 4 | Each Local Manager | `/api/v1/transport-nodes/{id}/inter-site/statistics` and `/inter-site/bgp/summary` | The state and the packet counters of each RTEP tunnel |
| 5 | None, the adapter computes | The answers from steps 1 to 4 | Ready or not ready with the reasons, from steps 1 and 2. The number of tunnels with BGP down, from steps 3 and 4 |

Step 1 calls two hosts, step 3 calls hosts whose addresses come from step 2, and step 5 compares the answers from different managers. This chain is why the pack is built with the Integration SDK ([part 1](/posts/nsx-federation-management-pack-design/#step-6-the-build-route)).

### Versions and upgrades

NSX Federation is upgraded in a fixed order: the standby Global Manager, the active one, then the Local Managers. Three conditions break that order or block operations, and each has an alert:

- the Global Managers run different versions, in which case NSX refuses a switchover;
- a location runs a newer release than the Global Manager;
- an upgrade is in progress, paused or failed on a manager.

The release alert compares the first three numbers of the NSX version. The managers of the lab run 9.1.1 at two patch levels, which raises no alert.

### Edges, Tier-0 service routers and changes

One call per federation edge node gives its CPU, memory and file system use, and the state of each stretched Tier-0 service router on it. In the lab the datapath cores are nearly idle (below 1%).

What counts as a healthy Tier-0 depends on its HA mode. An active-active gateway needs an active service router at every location of its span. An active-standby gateway needs one active service router in the federation. When a service router changes HA state between two cycles, a failover for example, the gateway raises a warning for two cycles. In the lab, fed-t0 is active-active with four service routers, all active with routing up.

The adapter compares each cycle with the earlier ones and records changes: the active Global Manager, the versions, the locations, the edge nodes, the HA state of the service routers, and the stretched objects created, changed or deleted, with the user NSX recorded. Each change is an information event on its object. The federation lists the last ten, newest first, so its property history doubles as a change timeline.

A cycle in which a call failed reports no removal, so a gap in the data does not show up as a change. Stretched objects are read every 30 minutes, so a change to one can take up to 30 minutes to appear.

### Clocks, configuration limits and overrides

Each manager's clock is compared with the active Global Manager in every cycle, from the system time NSX returns. The alert fires above 5 seconds, or when the NTP service of the manager is not running. In the lab, the other three managers are within a tenth of a second of the active Global Manager.

Each Local Manager reports how much of each NSX configuration limit it uses, as on its Capacity page, rated with the thresholds NSX applies. Global objects count at every location of their span, so the locations show the federation as well. A limit at 100% or more counts as critical, whatever rating NSX gives it.

A search on each Local Manager lists the global objects that were changed locally. Each override counts on the location and is a change of the timeline. The lab has none.

### Password expiry, firewall rule review and availability

Once an hour, each manager is asked for its local users, which the `audit` account may read. The pack computes how many days remain before the password of its collection account expires. Once it expires, NSX refuses the login and the collection of that manager stops, so the alert comes first: a warning below 30 days, critical below 7. In the lab, the `audit` password expires in 35 days on site-a-wld01 and in 85 to 87 days on the other three managers.

Each global security policy is checked for three kinds of rules:

| Rule | Meaning | Alert |
| --- | --- | --- |
| Shadowed | Comes after an enabled rule of the same policy that matches all its traffic, so NSX never reaches it | Information |
| Unused | Its hit count, over the locations of its span, has not grown for 30 days (a setting of the account) | Information |
| Allow-all | ALLOW from any source to any destination on any service, applied everywhere | Warning |

These alerts count on the Risk badge. The check does not expand group membership, so it only reports rules that can never be hit. The default policies of NSX are left out.

Each cycle also sends an availability value, 100 or 0, for the federation, each Global Manager, each location and each stretched Tier-0. Averaged over a period, it gives the share of that period the object was available. The availability report uses it.

### Large federations

Realization is the only part of the collection that costs one call per object. With up to 200 stretched objects, each one is read in every cycle. Above that, the adapter reads an object again when it is new, changed, not fully realized, or on its turn in a sweep that covers every object in six cycles.

On the mock NSX, a federation with 610 stretched objects costs 610 realization calls in the first cycle and about 100 in each of the next. Sizing figures for larger federations are in [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/OPERATIONS.md).

### Diagnostics

`install/diagnostics.py` collects what a support request needs into one JSON file, and changes nothing. It covers the versions, each account with its settings and state, the objects per type, the collection health and the open alerts, then lists the problems it found, each with what to check. It reads no credential.

In the lab it reported one problem: 21 objects of the pack that receive no data. They are edge nodes, RTEP tunnels and sync channels that no longer match an object in NSX. `docs/OPERATIONS.md` has a troubleshooting table for the common cases.

The SDK library pins `cryptography` 44.0.0, which has several published vulnerabilities, and the adapter image replaces it with 50.0.2. CI checks the Python packages of the image against the published vulnerabilities on every push and every week, and produces an SBOM. `SECURITY.md` lists what the pack can do on NSX and on VCF Operations.

## Tests in the lab

The pack only reads from NSX, so no test sends traffic through it. The tests replace NSX with a mock, read the live managers, make chosen calls fail inside the adapter, or write values into VCF Operations as if the adapter had sent them.

| Test | What it does | What it changes | Run with |
| --- | --- | --- | --- |
| Test suite | Runs the adapter against a mock NSX that answers from an anonymised recording of the lab federation, and against a mock VCF Operations API | Nothing | `pytest` in the tooling container; GitHub Actions on every push and pull request |
| Local run | Runs one collection cycle against the live managers, with the same read-only GET calls the adapter makes inside VCF Operations | Nothing | `tools/localrun.py` in the tooling container |
| Fault injection | The local run, with chosen NSX calls made to fail with HTTP 500 inside the adapter process | Nothing on NSX | `tools/test_gaps.py` in the tooling container |
| Alert tests | Writes metrics, properties or events into VCF Operations through its suite API, in the form the adapter sends, then restores them | VCF Operations data, for a few cycles | Suite API calls |
| Change timeline | Sets the description of a stretched segment, then removes it | One segment, restored | NSX API on the active Global Manager |

### Test suite and local run

The test suite needs no lab. Its 162 tests cover the collection, the alert content and the scripts; the list is in the Tests section of [docs/OPERATIONS.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/v1.13.0/docs/OPERATIONS.md).

On the mock, a large federation (1,000 more segments, 200 more Tier-1 gateways and 100 more policies of 50 rules each) takes 1,440 calls on the active Global Manager in the first cycle after a start, within the budget of 2,000 per manager, and 233 in the following cycles.

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
| Second Global Manager address unreachable | Collection continues. "Global Managers not reachable" reads 1 on the federation |
| No Global Manager reachable | Both Local Managers are still collected from the cached location list, and "NSX Federation collection runs without a Global Manager" is raised. When a restart empties the cache, the list is read back from VCF Operations |

### Fault injection

`tools/test_gaps.py` wraps the local run and makes chosen calls answer HTTP 500 inside the adapter process; NSX itself sees no error. By default the location status, backup overview, alarm and cluster status calls fail on every manager. The variable `FAIL` replaces that list with other path fragments, separated by commas.

*Run on: the jump host, from the root of a clone of the repository.*

```bash
docker run --rm --network host -v $PWD:/work -e GM_HOSTS=<gm1>,<gm2> \
  -e NSX_USER=audit -e NSX_PASSWORD='<password>' fedmp-tooling python tools/test_gaps.py
```

On the lab, with the defaults, the run reported 15 calls without an answer and sent none of the values behind them.

### Location outage

The outage rule was tested without causing an outage. With the account stopped, the suite API wrote "one location in error" on the segment fed-stretched (`POST /api/resources/{id}/stats`), and the alert "Stretched segment is in error at a location" fired.

Writing the name of a location to the property "Locations in outage" (`POST /api/resources/{id}/properties`) cancelled the alert while the symptom of the fault stayed active. The values were then restored and the account started again.

### Latency, packet loss and BGP flaps

The first check read the packet counters of every RTEP tunnel on both edges at nearly the same moment, four times, two minutes apart. On the two tunnels that had dropped a packet, the received total matched the total sent by the peer exactly. NSX therefore counts a dropped packet in the total, and the pack computes the share of drops on that basis.

The second check tested the alerts. With the account stopped, the suite API wrote breaching values every minute for 14 minutes, then the account was started again:

| Value written | Alert raised |
| --- | --- |
| 6% and 20 drops per minute on a tunnel | "RTEP tunnel is dropping packets", Immediate |
| 30 blocked packets per minute on a tunnel | "RTEP tunnel is blocking packets" |
| One connection drop on an established uplink | The Tier-0 neighbor flap alert |
| One connection drop on the inter-site session of a tunnel | "RTEP tunnel inter-site BGP session is flapping" |
| 160 ms to the Global Manager | The latency alert, Warning, through the 150 ms condition (160 ms is only 64% of the 250 ms setting) |

During the test, the Inter-site Connectivity dashboard showed the values written to the selected tunnel. The scoreboard colors follow the alert thresholds:

![The RTEP tunnels list of the Inter-site Connectivity dashboard with one tunnel selected, and its key values during the alert test: Rx drops per minute at 20 in orange, Rx dropped at 6% in red, Tx blocked per minute at 30 and BGP drops per hour at 12 in orange, the other values with a threshold in green](/images/nsx-federation-mp/ops-dashboard-loss.webp)

The alert lists each symptom with the observed value and its threshold:

![Detail of the alert "RTEP tunnel is dropping packets" during the test: description, recommendation, and the symptoms with their values, 6% of received packets dropped against thresholds of 1% (Warning) and 5% (Immediate), and 20 drops per minute against a threshold of 10](/images/nsx-federation-mp/ops-alert-tunnel-drops.webp)

In normal operation the lab shows 1 ms to the Global Manager, empty sync queues and no drops.

### Change timeline

On 22 November at 01:48 UTC, the description of the stretched segment fed-stretched was set on the active Global Manager with a PATCH. The next read of the inventory, at 02:13 UTC, recorded "Stretched Segment fed-stretched changed by admin (revision 0 -> 1)".

It showed on the segment as a notification, which cleared at the next cycle, and it stayed in the last ten changes of the federation. Restoring the segment took a PUT of the whole segment without the description, at revision 2, because a PATCH with an empty description left the description in place.

### NSX alarm events

The lab had no open federation alarm. To test the definitions, two events were posted through the suite API (`POST /api/events`) in the form the adapter sends: `gm_to_lm_synchronization_warning` on the Global Manager and `rtep_bgp_down` on location site-a-wld01. Both raised their named alert within one cycle, and no "Notification event" alert appeared next to them.

The alarm alert carries the symptom, the text of the event and the recommendation:

![Detail of the alert "NSX alarm: RTEP BGP neighbor down" on site-a-wld01: description, recommendation, and the symptom "Local Manager raised NSX alarm rtep_bgp_down" with the text of the test event](/images/nsx-federation-mp/ops-alarm-alert.webp?v=2)

Posting the same events again with a cancel time cleared both alerts in the next cycle.

## What the lab showed

In the lab, one collection cycle covered 76 objects in 25 to 45 seconds. The Local Managers were still collected when the second Global Manager address was unreachable and when no Global Manager answered. With 15 calls made to fail, the pack sent none of the values behind them. During the simulated location outage, the alert on the stretched segment cancelled while its fault symptom stayed active. The written drop, blocking, flap and latency values raised their alerts, and the two test NSX alarm events raised their named alerts within one cycle.

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
