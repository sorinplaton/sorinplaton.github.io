---
title: "Argo CD as a VCF Service: hands-on with the VCF 9.1.1 tech preview"
date: 2026-10-22
description: "VCF 9.1.1 ships Argo CD as a VCF Service: a tenant orders an instance from VCF Automation and logs in with SSO. Replacing the vCenter-installed Supervisor Service with it on a Holodeck lab, adopting the running apps, and the six things that cost time."
summary: "In VCF 9.1.1 Argo CD becomes a VCF Service (tech preview): installed once from VCF Automation, ordered by the tenant like a namespace or a VM, with VCF Automation SSO instead of a shared admin password and ManagedEntity objects instead of cluster tokens. The migration from the old Supervisor Service, the YAML, the demo, and what tripped me up."
tags: ["VCF 9.1.1", "Argo CD", "GitOps", "VCF Automation", "VKS", "Supervisor", "Tech preview", "Holodeck"]
categories: ["Field notes"]
showToc: true
tocOpen: false
---

VCF 9.1.1 ships Argo CD as a **VCF Service** (tech preview): a tenant orders an Argo CD instance from VCF Automation the same way it orders a namespace or a VM, and logs in with its VCF Automation account. I replaced the vCenter-installed Argo CD on my first workload domain with it in one day, and adopted the running apps without redeploying them.

If you want the starting point, the vCenter-installed version is the one in [GitOps on VCF 9.1: what the Argo CD Supervisor Service really takes](/posts/gitops-argocd-vcf-91/). This post is what changes when the same package comes through VCF Automation instead.

## What changed

Until now, Argo CD on VCF was a **Supervisor Service** that a vSphere admin installed per Supervisor from vCenter. Each instance had its own local `admin` account, and the platform team handed out passwords. In 9.1.1 the same package (`argocd-service.vsphere.vmware.com`, version 1.2.0) is installed from **VCF Automation**, and the instance is created through the VCF Automation API. That one change wires the instance into the VCF Automation tenant model: org, project, region, single sign-on and service accounts.

|  | Supervisor Service (before) | VCF Service (9.1.1 tech preview) |
| --- | --- | --- |
| Installed from | vCenter, per Supervisor | VCF Automation Provider portal, all Supervisors at once |
| Instance created by | vSphere admin, Supervisor API | the tenant, VCF Automation API (or Build & Deploy → Services → GitOps) |
| Login | local `admin` + password | VCF Automation SSO (OIDC), `vcf argocd login --sso` |
| Who can use it | whoever has the password | VCF Automation users, mapped to Argo CD roles |
| Clusters | Secrets with tokens | `ManagedEntity` objects, no tokens to handle |

## The lab

A nested VCF 9.1 Holodeck (site A) on two Dell servers. Org `holo-east`, region `east-1`, one workload domain with a Supervisor and two VKS clusters (`demo-vks-cluster`, `overlay-public`). The Git repo is [github.com/sorinplaton/vcf-gitops](https://github.com/sorinplaton/vcf-gitops); everything in this post lives in `platform/argocd-vcfa/`.

## Why it matters

The big win is that Argo CD stops being a side install with a shared password and becomes a tenant service of the private cloud.

**Self-service for tenants.** An org admin creates an instance from **Build & Deploy → Services → GitOps** or with one YAML file. No ticket to the vSphere team.

![Self-service: an org admin creates the Argo CD instance from the portal or one YAML file, straight into the project namespace; no ticket to the vSphere team](/images/argocd-tp/why-self-service.webp)

**One login.** The instance is registered as an OIDC client of VCF Automation automatically (client, redirect URIs, PKCE). Users click *Log in via VCF Automation*; nobody hands out the `admin` password.

![One login: the user opens the Argo CD UI, is sent to VCF Automation for the OIDC sign-in, and no admin password is handed out](/images/argocd-tp/why-one-login.webp)

**Tenancy is built in.** The instance lives in a project namespace, gets its own VCF Automation service account, and is visible in the org portal next to VMs and Kubernetes clusters.

![Tenancy: inside the holo-east org, the project namespace holds VMs, Kubernetes clusters and the Argo CD instance side by side, with its own service account](/images/argocd-tp/why-tenancy.webp)

**Clusters without tokens.** A `ManagedEntity` points at a VKS cluster or a Supervisor namespace; the service writes the Argo CD cluster secret and keeps its credentials current.

![Clusters without tokens: a ManagedEntity registers the target, the service writes the cluster secret in the Argo CD instance and keeps it current, and Argo CD deploys to demo-vks-cluster with it](/images/argocd-tp/why-clusters-without-tokens.webp)

**Lifecycle from one place.** The provider uploads and upgrades the service once in VCF Automation; it lands on every Supervisor. Tenants pick a supported Argo CD version (3.4.4 or 3.3.12 in this build) per instance.

![Lifecycle: the provider uploads or upgrades the service once in VCF Automation and it lands on every Supervisor; tenants pick the Argo CD version per instance](/images/argocd-tp/why-lifecycle.webp)

**GitOps for the platform too.** The same Argo CD can deploy apps *and* the VKS clusters they run on, so a commit can add a worker node as easily as it changes an app.

![GitOps for the platform: Argo CD pulls manifests from GitHub and one commit can change podinfo or the demo-vks-cluster Cluster object itself](/images/argocd-tp/why-platform-gitops.webp)

![The org portal: Build & Deploy → Services → GitOps lists the instance with its project and status](/images/argocd-tp/org-portal-gitops.webp)

*The org portal: **Build & Deploy → Services → GitOps** lists the instance with its project and status.*

## How the pieces fit

![Lab architecture: you sign in through VCF Automation, Argo CD in its own VPC pulls from GitHub and deploys to two VKS clusters on the wld01 Supervisor](/images/argocd-tp/diagram-lab-architecture.webp)

The instance sits in its own namespace and VPC. VCF Automation handles who may sign in, Git holds what should run, and ManagedEntities say where it runs.

## Install, step by step

Six steps took the lab from the old Supervisor Service to a running, SSO-enabled instance. Steps 1–2 are clicks in VCF Automation; steps 3–5 are YAML in Git, applied with `platform/argocd-vcfa/apply.sh`.

1. **Remove the old service.** Back up the old Applications, cluster secrets and ManagedEntities (`kubectl get -o yaml`), delete the old `ArgoCD` instance, then uninstall the vCenter-installed Argo CD Supervisor Service.
2. **Get the package into VCF Automation and install it.** Download `VCF_SERVICE_ARGOCD` with VCF Download Tool 9.1.1 (`vcf-download-tool artifacts download … --component=VCF_SERVICE_ARGOCD`; the 9.1.0 tool has no `artifacts` command). Upload the `.tar` from the `-images/` folder under **Service Management → Partner Services → Upload**, then **Install** it. It shows up under **VCF Services** and is pushed to every Supervisor.

   ![Provider portal, Service Management: ArgoCD Service 1.2.0.25642124, Healthy](/images/argocd-tp/provider-service-management.webp)

3. **Network and namespace** (`./apply.sh network`, through the VCF Automation CCI API): a VPC `vpc-argocd-gitops`, its transit-gateway attachment and load balancer, then a Supervisor namespace from the `medium` class. The namespace is created from `generateName: argocd-gitops-`; it came up as `argocd-gitops-9xsrd`.
4. **The instance** (`./apply.sh instance`, through the namespace endpoint VCF Automation returns):

   ```yaml
   apiVersion: argocd-service.vsphere.vmware.com/v1alpha1
   kind: ArgoCD
   metadata:
     name: argocd-gitops
   spec:
     version: 3.4.4+vmware.1-vks.1
     serverSideDiff: true        # required to manage VKS clusters
     enableLoadBalancer: true
     rbac:
       defaultPolicy: role:readonly
       policy: |
         g, <VCF Automation user id>, role:admin
   ```

   About two minutes later it was `Ready` at `https://10.200.200.5`, with an OIDC client and a VCF Automation service account in its status.
5. **Give the service account its role.** In the holo-east org, give `status.serviceAccounts.platform` the **ArgoCD Instance** role (a global role VCF Automation publishes to the org).
6. **Log in.** Open the UI and click *Log in via VCF Automation*, or run `vcf argocd login 10.200.200.5 --sso`.

![The Argo CD login page offers Log in via VCF Automation](/images/argocd-tp/login-page.webp)

*The Argo CD login page offers **Log in via VCF Automation**.*

![The button redirects to the holo-east org login of VCF Automation (OIDC with PKCE)](/images/argocd-tp/login-vcfa-redirect.webp)

*The button redirects to the holo-east org login of VCF Automation (OIDC with PKCE).*

## Clusters and the first app

Three `ManagedEntity` objects registered both VKS clusters and one Supervisor namespace, and the first app adopted the running podinfo without restarting a pod.

On the same Supervisor, a cluster is one short object (applied to the Supervisor in `argocd-gitops-9xsrd`):

```yaml
apiVersion: argocd-service.vsphere.vmware.com/v1alpha1
kind: ManagedEntity
metadata:
  name: demo-vks-cluster
spec:
  clusterName: demo-vks-cluster
  targetRef: {apiGroup: cluster.x-k8s.io, kind: Cluster, name: demo-vks-cluster, namespace: podinfo-demo-dx643}
```

The instance's Supervisor service account (`argocd-k8s-sa`) needs `edit` in the namespace that holds the clusters first, through a RoleBinding. With that in place, the service writes one Argo CD cluster secret per entity:

![Settings → Clusters: demo-vks-cluster, overlay-public and the podinfo-demo namespace](/images/argocd-tp/settings-clusters.webp)

*Settings → Clusters: `demo-vks-cluster`, `overlay-public` and the `podinfo-demo` namespace.*

The first Application points at `apps/podinfo` in the repo, destination `demo-vks-cluster`, with automated sync, prune and self-heal. The workload was already running from the old Argo CD, so the new instance found it identical to Git and just took ownership: the pods are 6 days and 20 hours old in the tree.

![podinfo-vks: Synced to HEAD, Healthy, the Service, Deployment, ReplicaSet and three pods](/images/argocd-tp/podinfo-synced.webp)

*`podinfo-vks`: Synced to HEAD, Healthy; the Service, Deployment, ReplicaSet and three pods.*

## The demo: Git is the only way in

The demo shows one idea three ways: what is in Git is what runs, and nothing else survives. podinfo is a good subject because every change is visible in a browser.

![podinfo on demo-vks-cluster before the demo: the default blue page, served by one of three pods](/images/argocd-tp/podinfo-before.webp)

*podinfo on `demo-vks-cluster` before the demo: the default blue page, served by one of three pods.*

1. **A commit changes the live site.** [PR #5](https://github.com/sorinplaton/vcf-gitops/pull/5) adds two environment variables to `apps/podinfo/podinfo.yaml`: `PODINFO_UI_COLOR: "#2e7d32"` and `PODINFO_UI_MESSAGE: "Deployed by Argo CD on VCF 9.1.1"`. Merge it, press **Refresh** in Argo CD (or wait for the 3–5 minute poll), and watch the app go OutOfSync, sync itself and roll three new pods. Reload the page: it is green with the new message.
2. **Self-heal.** Delete the podinfo Deployment on the VKS cluster. Argo CD marks it Missing and recreates it within seconds.
3. **Scale through Git.** Change `replicas: 3` to `6` and commit; the resource tree grows three pods.
4. **The platform from Git.** The `demo-vks-cluster` Application manages the VKS cluster itself from `clusters/demo-vks-cluster/cluster.yaml`. Raise the worker count, commit, and VCF builds a new worker node.

## What tripped me up

It is a tech preview, and six things cost time. None of them blocks the install once you know it.

| Symptom | Cause | Fix |
| --- | --- | --- |
| VCF Automation login failed and tokens were rejected | the VCF Automation appliance clock was 48 minutes slow; chrony only steps the clock in its first three updates | `sudo chronyc makestep` and `sudo hwclock --systohc` on the appliance |
| `metadata.generateName: Required value` on the namespace | VCF Automation only accepts `generateName` for a SupervisorNamespace | create it once with `kubectl create`, look the name up afterwards |
| VPC, attachment and load balancer appeared after a `--dry-run=server` | the CCI API ignores server dry run for these kinds and creates them | never rely on a dry run against VCF Automation |
| `permission denied: applications, create` after a good SSO login | the instance starts with an empty Argo CD RBAC policy | set `spec.rbac` on the `ArgoCD` object |
| ManagedEntity stuck `Failed`: service account not bound | created before its RoleBinding; the controller does not retry | apply the RoleBinding first, then delete and re-apply the entity |
| Old Application objects gone | reinstalling the service recreated the Argo CD CRDs | back up Applications before the swap and re-apply them from Git |

## Wrap-up

Argo CD as a VCF Service turns GitOps into something a tenant orders, logs into with the same account and points at its own clusters, with no passwords or tokens handed around. The tech preview still has rough edges (an empty RBAC policy, a controller that does not retry), but the install is a handful of YAML files that live in Git like everything else.

Everything shown here is in [sorinplaton/vcf-gitops](https://github.com/sorinplaton/vcf-gitops), folder `platform/argocd-vcfa/` ([PR #4](https://github.com/sorinplaton/vcf-gitops/pull/4)).
