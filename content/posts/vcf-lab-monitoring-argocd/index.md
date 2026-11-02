---
title: "Grafana and Prometheus for a VCF lab, built by Argo CD on the management domain"
date: 2026-11-02
description: "One Grafana for the whole Holodeck lab: the two Dell servers through their iDRACs, three vCenters, the nested ESXi hosts and the VKS cluster itself. Argo CD, the VCF 9.1.1 tech-preview service, builds the cluster and the monitoring stack from Git on the management domain. The steps, the YAML, and the eleven things that cost time."
summary: "Argo CD as a VCF Service on the management domain creates a VKS cluster from Git and installs kube-prometheus-stack, a vCenter exporter and an iDRAC exporter on it. One Grafana shows the power draw and CPU temperature of the Dells, CPU ready of the nested ESXi VMs, three vCenters and the cluster. What it took, from the Supervisor rebuild to the VKS node firewall."
tags: ["VCF 9.1.1", "Argo CD", "GitOps", "Grafana", "Prometheus", "VKS", "Supervisor", "Tech preview", "Holodeck", "Dell iDRAC"]
categories: ["Field notes"]
showToc: true
tocOpen: false
params:
  seoDescription: "Argo CD (VCF 9.1.1 tech preview) builds a VKS cluster and Grafana/Prometheus from Git to watch a VCF lab: Dells, three vCenters, VKS. Steps and pitfalls."
---

My Holodeck lab runs on two Dell servers, and until now "is the lab healthy?" meant four browser tabs: two iDRACs, the outer vCenter and the nested one. This post puts it all in one Grafana: power and CPU temperature of the Dells, CPU ready of the nested ESXi VMs, the three vCenters and the Kubernetes cluster the dashboards run on.

Nothing is installed by hand. The Argo CD VCF Service (the VCF 9.1.1 tech preview from [the previous post](/posts/argocd-vcf-service-tech-preview/)) runs on the **management domain** this time. It creates the VKS cluster from Git, then installs Prometheus, Grafana and two exporters on it from the same repo.

![Grafana, Lab: Dell power and thermal. Power draw, hottest CPU, inlet temperature and health of both Dells, then the time series from their iDRACs](/images/lab-monitoring/grafana-power-thermal.webp)

*The dashboard I open first: power, CPU temperature, inlet and fans of both Dells, read from their iDRACs every minute.*

## What it watches

| Source | Exporter | What you see |
| --- | --- | --- |
| iDRAC9 of the R740, iDRAC8 of the R730xd | [idrac_exporter](https://github.com/mrlhansen/idrac_exporter) 2.6.3 (Redfish) | power draw per PSU, CPU, inlet and exhaust temperatures, fan speeds, system health, lifecycle log warnings |
| outer vCenter (the Dells and the nested ESXi VMs) | [vmware_exporter](https://github.com/pryorda/vmware_exporter) 0.18.4 | CPU and memory of the Dells, CPU ready and memory of every nested ESXi VM, datastore space |
| vc-mgmt-a and vc-wld01-a (the nested VCF) | the same exporter, two more sections | host CPU and memory per domain, vSAN datastores, VM inventory |
| the VKS cluster itself | kube-prometheus-stack 92.17.0 | nodes, pods, API server, kubelet, CoreDNS, Prometheus itself |

Every account the exporters use is read-only: a vCenter role with just `System.Anonymous`, `System.View` and `System.Read`, and an iDRAC user with the `ReadOnly` role.

## How the pieces fit

![Lab architecture: Argo CD in the platform-ops namespace pulls from GitHub and creates the VKS cluster mon-mgmt; Prometheus scrapes the two exporters, which read the three vCenters and the two iDRACs with read-only accounts](/images/lab-monitoring/diagram-architecture.svg)

A new VCF Automation org, `platform-ops`, owns the lab tooling, so the application tenants (`holo-east`, `tepless`) stay separate. Its region `mgmt-a` is the management-domain Supervisor. The org has one namespace, `platform-ops-mj3pd`, with the Argo CD instance in it. Argo CD has three Applications:

1. **`mon-mgmt-cluster`** creates the VKS cluster `mon-mgmt` (one control plane, two workers) from `clusters/mon-mgmt/cluster.yaml`.
2. **`monitoring-stack`** installs the kube-prometheus-stack Helm chart on that cluster: Prometheus with 15 days of data on a 30 GB vSAN volume, Grafana behind an NSX load balancer, Alertmanager and node-exporter.
3. **`monitoring-exporters`** deploys `apps/monitoring` with Kustomize: the two exporters, their ServiceMonitors and the two lab dashboards as ConfigMaps that Grafana's sidecar picks up.

![Argo CD, Applications: mon-mgmt-cluster, monitoring-exporters and monitoring-stack, all Healthy and Synced](/images/lab-monitoring/argocd-applications.webp)

*The Argo CD instance on the management domain: the cluster, the Helm chart and the exporters are three Applications.*

## Step by step

The repo is [github.com/sorinplaton/vcf-gitops](https://github.com/sorinplaton/vcf-gitops/tree/mgmt-platform-ops), branch `mgmt-platform-ops`. The steps below link to its folders.

### 1. A management Supervisor that VCF Automation can use

VCF Automation regions need a Supervisor with NSX VPC networking, and the Argo CD service needs vSphere Pods and a load balancer. The Supervisor Holodeck had deployed on the management cluster used VDS networking, so I disabled it and enabled it again with NSX VPC networking (`platform/mgmt-supervisor/rebuild.sh`, about 30 minutes).

It then sat at "No node accepting vSphere Pods" for hours. vLCM refuses to install the spherelet on the hosts while the vSAN health check *Can vSAN cluster receive a host* is not green, and the nested management vSAN was yellow. The causes were space, an old on-disk format, and a controller that is not on the HCL because it is virtual. A 450 GB disk more per nested host, plus silencing the checks that can never pass in a nested lab, cleared it. `vsan-health.py` in the same folder shows which test is blocking.

### 2. Provider steps in VCF Automation

Once per lab, as the provider (`platform/platform-ops/provider.py` does it through the API):

- region `mgmt-a` from the new Supervisor;
- a tenant external network: VLAN 204 on the Holodeck router, `10.204.204.0/24`. VCF Automation does not accept the Day-0 external block;
- the org `platform-ops`, its admin, a region quota and the regional networking settings;
- the Argo CD VCF Service enabled for region `mgmt-a`.

### 3. Namespace, Argo CD instance, managed entities

As `platform-admin` of the new org, `platform/platform-ops/apply.sh` applies the VPC, its attachment and load balancer, the namespace, then the `ArgoCD` object:

```yaml
apiVersion: argocd-service.vsphere.vmware.com/v1alpha1
kind: ArgoCD
metadata:
  name: argocd-platform
spec:
  version: 3.4.4+vmware.1-vks.1
  serverSideDiff: true
  enableLoadBalancer: true
  rbac:
    defaultPolicy: role:readonly
    policy: |
      g, <platform-admin user id>, role:admin
```

A few minutes later it answered on `https://10.204.204.3`. Two more steps are needed. First, give the instance's service account the **ArgoCD Instance** role in the org. Then register the targets with `ManagedEntity` objects: the namespace itself, and the VKS cluster `mon-mgmt`, which does not exist yet.

### 4. The cluster, from Git

`argocd-apps-mgmt/mon-mgmt-cluster.yaml` points Argo CD at `clusters/mon-mgmt`. The cluster is a plain Cluster API object: class `builtin-generic-v3.7.0`, Kubernetes v1.35.6, Photon 5, best-effort-small control plane and two best-effort-medium workers. The one non-default setting is the node firewall rule for node-exporter (see the pitfalls):

```yaml
    variables:
    - name: node
      value:
        firewall:
          inboundRules:
          - {protocol: tcp, fromPort: 9100, toPort: 9100, source: 172.24.0.0/23}
```

### 5. Prometheus and Grafana

`argocd-apps-mgmt/monitoring-stack.yaml` is an Argo CD Application with the Helm chart and its values inline. The values that matter here:

- Grafana gets a `LoadBalancer` Service, so NSX gives it `10.204.204.6`.
- The admin password comes from an existing Secret (`grafana-admin`) and is never written in Git.
- Grafana and Prometheus get PVCs on the management vSAN storage policy.
- `serviceMonitorSelectorNilUsesHelmValues: false`, so Prometheus picks up the exporters' ServiceMonitors.
- The etcd, scheduler, controller-manager and kube-proxy scrapes are switched off, because pods cannot reach those VKS control-plane ports and the targets would stay down forever.
- Three community VMware dashboards (grafana.com 18017, 18018, 18019) are downloaded at start-up.

### 6. Read-only accounts and the exporters

The exporters need one account on each vCenter and each iDRAC. These are lab credentials, so they live in two Secrets created once with `kubectl`, and the repo only has examples (`apps/monitoring/secrets.example/`). On each vCenter:

```sh
govc role.create "Monitoring Read-Only" System.Anonymous System.View System.Read
govc sso.user.create -p "$MONITOR_PASSWORD" monitor
govc permissions.set -principal monitor@vsphere.local -role "Monitoring Read-Only" -propagate=true /
```

On each iDRAC, a Redfish `PATCH` on a free account slot with `{"UserName": "monitor", "RoleId": "ReadOnly", "Enabled": true}`. The iDRAC8 keeps its accounts at a different path (see the pitfalls). Then the third Application, `monitoring-exporters`, syncs `apps/monitoring`.

![Argo CD, monitoring-exporters tree: two dashboard ConfigMaps, the idrac and vmware Services, Deployments, ReplicaSets and pods, and two ServiceMonitors](/images/lab-monitoring/argocd-exporters-tree.webp)

*`monitoring-exporters` in Argo CD: Kustomize output, synced from the branch.*

## The dashboards

![Grafana, Lab: physical Dells and nested ESXi. Powered-on VMs per vCenter, Dell CPU and memory, CPU ready and memory of the nested ESXi VMs, host CPU and memory per nested domain, datastore free space](/images/lab-monitoring/grafana-nested-hosts.webp)

*Lab: physical Dells and nested ESXi. The panel that pays for the whole project is **CPU ready** of the nested ESXi VMs: it shows when the Dells are overcommitted long before vSAN latency does.*

![Grafana, VMware Overview (grafana.com 18017): VM counts, uptime, VMs per datastore, host CPU and memory, datastore usage and provisioning, VM CPU, memory, disk latency and swap](/images/lab-monitoring/grafana-vmware-overview.webp)

*The community VMware Overview across the three vCenters, once its data source input is mapped (see below). The provisioned-space bars above 100 % are thin-provisioned nested disks.*

![Grafana, Node Exporter / Nodes for the three VKS nodes of mon-mgmt](/images/lab-monitoring/grafana-node-exporter.webp)

*The cluster watches itself: node-exporter on the three VKS nodes.*

![Prometheus targets page: every target up, including vmware-exporter sections mgmt, wld01 and outer, both iDRACs and node-exporter on the three nodes](/images/lab-monitoring/prometheus-targets.webp)

*Prometheus target health: both iDRACs are up. The R730xd's iDRAC8 takes about 11 s per scrape, the R740's iDRAC9 about 4 s.*

What the first hour showed:

- The R730xd runs above 100 % of its nominal CPU.
- The R740's CPU 1 swings between 75 and 95 °C while the server draws 400 to 530 W. Its iDRAC lifecycle log has a row of "CPU 1 temperature is greater than the upper critical threshold" events, now on the dashboard.
- CPU ready of the nested ESXi VMs peaks at 4 to 5 % per vCPU, a little higher on the R740 than on the R730xd.

I knew most of it, but now there is a history instead of a feeling, and the history settled a hardware question. The R730xd is the busy one, but its VMs barely wait for CPU, so new CPUs there would buy little. The R740 is the one that needs attention, and for cooling, not cores.

## What tripped me up

Eleven things cost time. Most come from the nested lab or the tech preview, and each one has a one-line fix once you know it.

| Symptom | Cause | Fix |
| --- | --- | --- |
| No VCF Automation region from the management Supervisor | it was built with VDS networking | rebuild the Supervisor with NSX VPC networking |
| Supervisor stuck at "No node accepting vSphere Pods" | vLCM installs the spherelet only while vSAN health is green; nested vSAN was yellow | add capacity, silence the checks that cannot pass in a nested lab (`vsan-health.py`) |
| Argo CD instance Ready but cannot manage anything | the service account has no role | give it **ArgoCD Instance** in the org. Through the API it is `PUT /cloudapi/1.0.0/serviceAccounts/<id>` with `roles`; `roleEntityRefs` is rejected |
| VKS cluster stuck, no node VMs: NSX error 610711 "Private ip blocks are not configured in parent VPC" | VKS node subnets come from the VPC's private block | add `privateIPs: [172.24.0.0/23]` to the VPC |
| "Could not resolve KR/OSImage" | Photon's OS version is `5`, not `5.0` | `os-name=photon,os-version=5` |
| ManagedEntity `mon-mgmt` stuck Failed | created before the cluster existed; the controller does not retry | delete and re-apply it once the cluster is up |
| node-exporter only up on the node Prometheus runs on | the VKS node firewall drops everything outside its allowlist; NSX was innocent (the VPC default rule allows it) | `node.firewall.inboundRules` for TCP 9100 from the node subnet; the change rolls the nodes |
| `password policy is not satisfied` creating `monitor@vsphere.local` | the vCenter SSO default policy allows at most 20 characters | a shorter password |
| Redfish `Accounts/3 is invalid` on the R730xd | iDRAC8 2.x keeps accounts at `/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/` | try that path when `/AccountService/Accounts` fails |
| idrac_exporter `ImagePullBackOff`, then `cannot unmarshal !!map into bool` | images are tagged `2.6.3` (no `v`); 2.x wants `metrics: {system: true}`, not maps | fix the tag and the config; the iDRAC8 also needs a 90 s scrape timeout |
| VMware community dashboards show red triangles and N/A | their data source input is `DS_PROMETHEUS-VICTORIAMETRICS` | `datasource: [{name: DS_PROMETHEUS-VICTORIAMETRICS, value: prometheus}]` in the Grafana values |

The node firewall one is worth remembering beyond monitoring. VKS nodes run a host firewall (nftables, policy drop on input). It allows SSH, BGP, kubelet, the VKS agents, NodePorts and Geneve, and nothing else. Any DaemonSet that listens on a host port needs a rule in the cluster spec, and the change replaces the nodes.

## Wrap-up

The lab now watches itself from the management domain, and all of it is in Git: the cluster, the chart values, the exporters and the dashboards. Rebuilding it is three Argo CD Applications plus two Secrets. The Argo CD VCF Service did its part without trouble once the Supervisor underneath was right. Most of the time went into the nested lab: vSAN health, the VKS node firewall and a ten-year-old iDRAC.

Everything is in [sorinplaton/vcf-gitops](https://github.com/sorinplaton/vcf-gitops/tree/mgmt-platform-ops), branch `mgmt-platform-ops`: `platform/mgmt-supervisor/`, `platform/platform-ops/`, `clusters/mon-mgmt/`, `apps/monitoring/` and `argocd-apps-mgmt/`.
