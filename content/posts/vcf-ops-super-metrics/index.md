---
title: "Super metrics in VCF Operations: one number from many, and when real-time metrics are the better tool"
date: 2026-10-28
description: "A super metric turns the values of many objects into one KPI stored on the object where the question is asked. This post covers the formula language, the depth rule that breaks most first attempts, the where clause, what super metrics cost a VCF Operations cluster, and how VCF 9.1 real-time metrics differ: 2-second data for a troubleshooting window, not a stored KPI."
summary: "The questions operators ask — the worst CPU ready in a cluster, how many VMs run hot — are not single metrics. A super metric is the formula that answers them, stored like a built-in metric. The syntax, the depth rule, the where clause, worked examples, the sizing cost, and a side-by-side with VCF 9.1 real-time metrics."
tags: ["VCF 9.1", "VCF Operations", "Super metrics", "Real-time metrics", "PromQL", "Holodeck"]
categories: ["Field notes"]
showToc: true
tocOpen: false
images: ["/images/super-metrics/fig-01-one-kpi.svg"]
params:
  seoDescription: "VCF Operations super metrics explained: formula syntax, the depth rule, the where clause, sizing cost, and when VCF 9.1 real-time metrics are the better tool."
---

The questions I get asked about a cluster are not single metrics. *Which VM waits most for CPU? How many VMs are running hot? How evenly are the hosts loaded?* Every object in VCF Operations reports its own values, and nothing adds them up for you. A **super metric** is the formula that does: it reads related objects, reduces them to one value, and stores that value on the object where the question was asked. From then on it behaves like any built-in metric: dashboards, alerts, reports, the API.

Most teams under-use them, usually because the first attempt showed no data and nobody knew why. This post is the version I wish I had read first: the formula language, the depth rule, the `where` clause, what super metrics cost the cluster, and when VCF 9.1's new real-time metrics are the better tool.

*October 2026 · VCF Operations 9.0 / 9.1 · syntax and menu paths from the 9.0 and 9.1 Infrastructure Operations guides on Broadcom TechDocs*

## From many VM values to one cluster KPI

![Four VMs in a cluster each report CPU Ready; a super metric assigned to the cluster takes the maximum at depth 2 and stores "Worst VM CPU Ready 7.4 %" on the cluster, which then feeds dashboards, alerts and reports. A table lists example questions: worst CPU ready (max), VMs above 60 % (count + where), provisioned guest memory (sum), host load spread (min / max).](/images/super-metrics/fig-01-one-kpi.svg)

*Read it left to right. The VMs report; the super metric on the cluster reaches two hops down, takes the max, and the result is stored on the cluster every collection cycle.*

The lifecycle has five steps, and the third one is the one people miss:

1. **Define the formula.**
2. **Assign an object type**: the object that will hold the result (the cluster, above).
3. **Activate it in a policy** that applies to those objects.
4. VCF Operations **computes and stores** it every collection cycle, like a built-in metric.
5. **Consume** it in dashboards, views, symptoms, reports.

The editor's *Preview* computes the formula on the fly for one object and keeps nothing. Only a super metric that is activated in the policy covering an object is persisted. So a super metric that validates and previews perfectly, then shows nothing in a view, is almost always missing step 3. Broadcom's [KB 429899](https://knowledge.broadcom.com/external/article/429899/view-not-displaying-super-metrics-in-ari.html) describes exactly this case.

Name it like a built-in metric while you are there: Title Case, spaces between words, the unit at the end — `Worst VM CPU Ready (%)`. The name shows up in dashboards, alerts and reports, and the description is the place to record why it exists and who owns it.

## Anatomy of a formula

```text
avg( ${adaptertype=VMWARE, objecttype=VirtualMachine, metric=cpu|usage_average, depth=2,
       where=(${metric=sys|poweredOn} == 1)} as vmcpu )
```

Read aloud: *the average CPU usage of virtual machines from the vCenter adapter, two hops below the object this is assigned to, counting only VMs whose own powered-on metric equals one.*

| Part | What it does | Rule to remember |
|---|---|---|
| `avg` `count` `max` `min` `sum` `combine` | Looping function: reduces the set of values the entry returns | `combine` merges all values into one timeline instead of one number |
| `${ … }` | Resource entry: one source of data | `${this, …}` reads only the assigned object itself |
| `adaptertype` | Adapter kind; `VMWARE` is the vCenter adapter | Always mandatory, even with `objecttype=*` |
| `objecttype` | Resource kind to iterate | `objecttype=*` takes every kind of that adapter that has the attribute |
| `metric` / `attribute` | `metric` is one instance; `attribute` is every instance | A 4-vCPU VM has four `cpu` instances |
| `depth` | Hops along the relationship chain | Default 1 · negative walks up to parents · never 0 |
| `where` | Filters which values are used | Same object only · numeric right operand |
| `as alias` | Names the entry for reuse | Case-insensitive · each name defined once |

In the editor you rarely type keys by hand: enter a few letters and press *ctrl+space*; it completes object types, metrics and properties. Do that rather than copying metric keys from a blog post, mine included — the keys in this post are the ones the documentation uses, but pick them from the list so they match your version.

### Functions and operators, briefly

Looping functions take a set (every VM, every vCPU instance) and reduce it. Single functions shape one value: `abs`, `ceil`, `floor`, `sqrt`, `pow(x, y)`, `log`, `log10`, `exp`, `rand()` and the trigonometric set. Operators are what you expect — `+ − * / %`, comparisons, `&& || !` — plus two worth knowing:

- **Arrays**: `min([x, y, z])` picks between expressions.
- **Ternary**: `condition ? if_true : if_false`. The condition is converted to a number (anything other than 0 is true) and *only the chosen branch is evaluated*, which is what makes this divide-by-zero guard safe:

```text
${this, metric=cpu|demandmhz} as a != 0 ? 1/a : -1
```

String operators (`equals`, `contains`, `startsWith`, `endsWith` and their `!` negations) exist too, but only inside a `where` condition — you cannot compute with strings.

## Depth: the rule that breaks most first attempts

![A vertical chain data center → cluster → ESX host → VM with the super metric assigned to the cluster. Arrows show depth 1 to the host, depth 2 to the VM and depth −1 up to the data center. A table lists cluster→hosts 1, cluster→VMs 2, cluster→data center −1, host→VMs 1, datastore→VMs −1, any object→itself via ${this}. A note: picking a metric in the editor writes depth=1.](/images/super-metrics/fig-02-depth.svg)

*Count hops along the relationship chain, starting from the object the super metric is assigned to.*

Depth is where most wrong or empty super metrics come from. From the cluster, hosts are depth 1, VMs are depth 2, and the data center is depth −1. The trap is the default: when you pick a metric in the editor it writes `depth=1`, so a cluster formula meant to read VMs silently reads hosts — or nothing of the type you named — until you change it to 2.

Datastores are the other trap. VMs are *parents* of a datastore, so from a datastore you reach its VMs with `depth=-1`. And because the datastore cluster is also a parent of the datastore, VM → datastore cluster cannot be done in one entry: −1 + 1 = 0, and depth 0 is not valid.

![Two VMs connect to datastore DS-01 inside a datastore cluster. A dashed direct path from the VMs to the datastore cluster is crossed out: −1 + 1 = 0, not allowed. Marker 1: super metric #1 on the datastore, depth −1, aggregates its VMs. Marker 2: super metric #2 on the datastore cluster, depth 1, sums super metric #1.](/images/super-metrics/fig-03-chain.svg)

*The documentation's own example: chain two super metrics.*

| Step | Assigned to | Formula | Result |
|---|---|---|---|
| SM #1 | Datastore | `count(${adaptertype=VMWARE, objecttype=VirtualMachine, metric=sys\|poweredOn, depth=-1, where=($value == 1)})` | Powered-on VMs per datastore |
| SM #2 | Datastore cluster | `sum(${adaptertype=VMWARE, objecttype=Datastore, metric=Super Metric\|sm_<id of SM #1>, depth=1})` | Powered-on VMs per datastore cluster |

In the editor, pick SM #1 from the metric list for SM #2 rather than typing its key. One caveat: a VM with disks on two datastores is counted on both.

## The where clause: choose which values count

![A cluster with two ESX hosts and four VMs; VM2 and VM4 are powered off and greyed out as excluded, VM1 reports 40 % and VM3 70 %. The formula avg over VMs at depth 2 with where powered-on equals 1 gives avg(40, 70) = 55 %. A table of rules: reads only the same object's metrics; right operand is a number; string operators allowed; $value is the entry's own value; $value.isFresh() ignores stale values.](/images/super-metrics/fig-04-where.svg)

*Two VMs are off. They still report 0 % CPU, so an unfiltered average says 27.5 % when the running VMs are at 55 %.*

The `where` clause tests each value before it enters the aggregation. It can look at a different metric of the *same* object — `where=(${metric=sys|poweredOn} == 1)` — or at the entry's own value with `$value`, as in `where=($value > 60)`. Two limits matter in practice:

- It cannot look at another object. "VMs above the cluster's SLA" is not expressible in one super metric, because the SLA belongs to the cluster, not the VM.
- The right operand must be a plain number: not another super metric, not a variable.

`$value.isFresh()` is the one to remember when objects stop reporting. A metric's last point is *fresh* when less time has passed than its expected publishing interval, so `where=($value.isFresh())` keeps stale last values out of your maximum.

### Aliases, ternaries and objecttype=*

Three features for longer formulas. An **alias** lets you write a resource entry once and reuse it; this host-load spread ratio from the documentation uses the same entry twice (and 0.0001 to avoid dividing by zero):

```text
(min(${adaptertype=VMWARE, objecttype=HostSystem, attribute=cpu|demand|active_longterm_load,
      depth=5, where=($value>=0)} as cpuload) + 0.0001) / (max(cpuload) + 0.0001)
```

Alias names are case-insensitive, cannot start with a digit or contain operators, each name is defined once, and one entry can carry several (`as a1 as a2`). The **ternary guard** we saw above protects divisions. And **`objecttype=*`** aggregates across every object type of an adapter without listing them — the adapter type stays mandatory:

```text
count(${adaptertype=VMWARE, objecttype=*, attribute=badge|health, depth=1})
```

## Creating, consuming, automating

The wizard lives under **Infrastructure Operations → Configuration → Super Metrics** (9.0; *Operate → Administration → Configurations* in 9.1): *Add*, name and description, the object type that holds the result, the formula, the unit, *Validate* and *Preview* on a real object, and finally the policy page — select the policy for the target objects and *Update*. Then check it where it lives: open an object of that type and look under *All Metrics → Super Metric*. The super metrics list shows each one's object types, formula and the policies it is activated in; use it to confirm step 3 before building dashboards on top.

Once stored, a super metric feeds any widget that takes a metric, list and trend views, scheduled reports, and the chain I use most: a **symptom** on the super metric with a threshold → an **alert definition** → a notification or action. It can be flagged as a KPI in the policy, and it appears in the system audit report, which is a quick way to inventory them.

Treat them as code. *Export* and *Import* on the toolbar move definitions between instances (re-check object type and policy after importing); the OPS CLI scripts the same; and the REST API manages definitions directly:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/suite-api/api/supermetrics` | List (search parameters supported) |
| `POST` | `/suite-api/api/supermetrics` | Create: `{"name", "formula", "description"}` |
| `PUT` | `/suite-api/api/supermetrics` | Update: the `id` is required |
| `GET` | `/suite-api/api/supermetrics/{id}` | Read one definition |
| `DELETE` | `/suite-api/api/supermetrics/{id}` | Delete one definition |

Keep the exported definitions in Git next to the dashboards that use them.

## Six formulas to start from

All assigned to the **cluster** unless noted; depth 2 for VMs, depth 1 for hosts. Confirm the metric keys in the editor.

| Name | Formula | Unit |
|---|---|---|
| Worst VM CPU Ready (%) | `max(${adaptertype=VMWARE, objecttype=VirtualMachine, metric=cpu\|readyPct, depth=2})` | % |
| Avg CPU of Powered-On VMs (%) | `avg(${adaptertype=VMWARE, objecttype=VirtualMachine, metric=cpu\|usage_average, depth=2, where=(${metric=sys\|poweredOn} == 1)})` | % |
| Busy VM Count | `count(${adaptertype=VMWARE, objecttype=VirtualMachine, metric=cpu\|usage_average, depth=2, where=($value > 60)})` | count |
| Provisioned Guest Memory (GB) | `sum(${adaptertype=VMWARE, objecttype=VirtualMachine, metric=mem\|guest_provisioned, depth=2}) / 1048576` | GB |
| Host Load Spread (ratio) | `(min(${adaptertype=VMWARE, objecttype=HostSystem, attribute=cpu\|demand\|active_longterm_load, depth=1, where=($value>=0)} as l) + 0.0001) / (max(l) + 0.0001)` | ratio |
| Objects with Health (count), on vCenter | `count(${adaptertype=VMWARE, objecttype=*, attribute=badge\|health, depth=1})` | count |

The memory metric is in KB, hence the division. The spread ratio is 1 when hosts are evenly loaded and approaches 0 when one host does all the work.

## What super metrics cost

There is nothing to deploy: super metrics are built into the VCF Operations analytics cluster. What they need is upstream — the input metrics collected by an adapter *and enabled in the policy* (several are disabled by default in 9.x), the super metric activated in a specific policy covering the target objects, and an account allowed to edit both.

What they cost is metric series. Each activated super metric yields one stored value per object it is active on, and those series count against the node limits in Broadcom's [VCF Operations 9.0 Sizing Guidelines, KB 397782](https://knowledge.broadcom.com/external/article/397782/vcf-operations-90-sizing-guidelines.html) (for 9.1.x the limits moved to [configmax.broadcom.com](https://configmax.broadcom.com)):

| Node size | vCPU | Memory GB | Max objects (one node) | Max metrics (one node) |
|---|---|---|---|---|
| Extra small | 2 | 8 | 700 | 140,000 |
| Small | 4 | 16 | 10,000 | 1,600,000 |
| Medium | 8 | 32 | 30,000 | 5,000,000 |
| Large | 16 | 48 | 44,000 | 8,000,000 |
| Extra large | 24 | 128 | 100,000 | 20,000,000 |

A rule of thumb, mine rather than Broadcom's: multiply super metrics by objects in scope. Ten super metrics on a hundred clusters add a thousand series, noise. The same ten on 2,000 hosts add 20,000, about 1.3 % of a Small node. On 50,000 VMs they add 500,000 — a third of a Small node. That is why super metrics belong on clusters and hosts, not on every VM, and why the Aria Operations best-practices guide says to apply them only to relevant objects and avoid heavy calculations on world-level objects.

Three habits keep them cheap:

- **Prefer `metric=` to `attribute=`.** `attribute` matches by pattern; overusing it slows the database and leaves gaps of more than one collection cycle in *every* super metric ([KB 335252](https://knowledge.broadcom.com/external/article/335252/vmware-aria-operations-super-metric-data.html)).
- **Keep depth to the real hops.** A wider reach reads more objects every cycle.
- **Retire unused super metrics.** They keep computing and storing while active.

The cluster itself has its own requirements that super metrics inherit: datastore latency under 10 ms, one vCPU per physical core at scale, nodes within 5 ms of each other and all the same size, no snapshots, and HA or continuous availability halving the object limit. Disk size and IOPS come from the sizing worksheet attached to the KB.

## When real-time metrics are the better tool

VCF 9.1 added **real-time metrics**: 20-second data by default, and 2-second esxtop data for hosts through a policy setting. They are often mentioned in the same breath as super metrics, and they answer a different question.

| | Super metrics | Real-time metrics (VCF 9.1) |
|---|---|---|
| **The question** | What is this KPI for the object, over time? | What is happening right now, second by second? |
| **Granularity** | Each collection cycle, 5 minutes by default | 2 s (ESX Top) · 20 s (Standard) · 60 s for clusters and NSX |
| **Defined by** | A formula, an object type and a policy | Collection tiers switched on in a policy |
| **Viewed in** | Dashboards, views, reports, alerts, the API | Workbench → Real Time Analysis; PromQL clients such as Grafana |
| **Runs on** | The VCF Operations analytics cluster | A real-time metrics service and store in VCF Management Services |
| **Best used for** | Continuous monitoring and alerting | A troubleshooting window, then switched off |

Use both: the super metric alert tells you a cluster is in trouble; real-time data shows which VM, and which second. The spikes that 5-minute averages smooth away are exactly what real-time data keeps ([KB 444403](https://knowledge.broadcom.com/external/article/444403/understanding-peak-or-worst-performance.html) explains how the two granularities disagree).

![ESX hosts and VMs, vCenter, NSX transport nodes and vSAN feed a real-time metrics service and a real-time metrics store inside VCF Management Services, deployed per VCF instance. The VCF Operations Workbench (Overview, Top N, Custom Query) and PromQL clients such as Grafana read from the store. A table: ESX hosts and VMs report at 2 s (about 21 esxtop metrics), 20 s (Standard) and 5 minutes (historical default).](/images/super-metrics/fig-05-realtime.svg)

*A separate pipeline. The 5-minute collection, super metrics and dashboards keep working as before.*

**How they are used.** In the Workbench, *Real Time Analysis* has three views. *Overview* streams 20-second data, or 2-second data when you switch the view to ESX Top on a selected host or VM. *Top N* ranks the heaviest consumers below a VM, host, cluster, datacenter, NSX or vSAN object — always at 20 seconds, even with ESX Top on. *Custom Query* takes PromQL; a `feature` label picks the series:

```text
cpu.ready.HOST{feature='ESX_TOP'}          # 2-second host data
cpu.ready.HOST{feature='TROUBLESHOOTING'}  # 20-second host data
```

The query field has no autocomplete, so use the *Metrics* help tab to find exact internal names. ESX Top ranges over six hours return an error, and 2-second data is resource-intensive: enable it for the troubleshooting window, then switch it off.

**What they produce.** Three tiers. *Essentials* is always on: a curated set across vCenter, ESX, NSX and vSAN. *Standard* adds more vCenter metrics at 20 seconds and needs the Large deployment type. *ESX Top* gives 2-second esxtop data for hosts and their VMs. Clusters and datacenters report every 60 seconds from the vCenter statistics registry, NSX transport nodes every 60 seconds. The full metric list is a spreadsheet, `VODAP-9.1-Metrics-List-Per-Provider.xlsx`, linked from the [Real-Time Metrics Details](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/infrastructure-operations/configuring-policies/using-the-monitoring-policy-workspace/policy-workspace/real-time-metrics-policy-workspace.html) page.

**What they require.** VCF 9.1, ideally the latest 9.1.1 binaries. Two components — *Real-time metrics* and *Real-time metrics store* — added under **Build → Lifecycle → VCF Management → Add Component**, on every VCF instance you want to analyse (the fleet lifecycle API does the same with component type `OPS_DATA_PLATFORM`). The Large size if you want the Standard tier. And the policy tiers, which only appear in a policy once the components are deployed: **Operate → Administration → Configurations → Policy Definition**, *Real time metrics* tile, Standard on the vCenter object type, ESX Top on Host System. Collection starts after one cycle.

For Grafana, the community has documented the connection: a Prometheus data source pointed at the real-time metrics FQDN with an `Authorization: Bearer` header and the vCenter UUID as `sourceId`, with a token that expires after 35 minutes, so [GrafanaRTMsync](https://github.com/makolos2/GrafanaRTMsync) refreshes it from cron. That part is not in Broadcom's documentation; treat it as a working recipe, not a supported interface. And check CPU, memory and storage for the Large size on the *Deploy Real-Time Metrics* page before scaling; I could not find published figures anywhere else.

## Five rules

1. **Assign where the question is asked.** Put the super metric on the object that should hold the answer, usually the cluster, and reach down with depth.
2. **Count the hops.** Change the default `depth=1` to the real number. Negative goes up; 0 is invalid, so chain two super metrics.
3. **Filter before you aggregate.** Use `where` to drop powered-off or stale objects. It sees the same object only, with a numeric right side.
4. **Activate it in the policy.** Preview proves the formula, not the data. Nothing is stored until the policy on those objects activates it.
5. **Name and keep them like code.** Title Case with the unit at the end, a description with owner and purpose, exported definitions in version control.

When a super metric looks wrong, check them in that order. And when the question is *what is happening right now*, reach for real-time metrics instead.

## Sources

- Broadcom TechDocs, VCF 9.0: [Configuring Super Metrics](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/infrastructure-operations/configuring-super-metrics.html) · [Create a Super Metric](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/infrastructure-operations/configuring-super-metrics/create-a-super-metric.html) · [Enhancing Your Super Metrics](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/infrastructure-operations/configuring-super-metrics/enhancing-a-super-metric-formula.html) · [Super Metric Functions and Operators](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/infrastructure-operations/configuring-super-metrics/super-metrics-tab/super-metric-functions-and-operators.html)
- Broadcom TechDocs, VCF 9.1: [Real-Time Metrics Details](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/infrastructure-operations/configuring-policies/using-the-monitoring-policy-workspace/policy-workspace/real-time-metrics-policy-workspace.html) · [Real-time Data in the Workbench](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/infrastructure-operations/troubleshooting-workbench-home-page/real-time-data-in-troubleshooting-workbench.html) · [What's New in VCF Operations 9.1](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/release-notes/vmware-cloud-foundation-9-1-0-0-release-notes/what-s-new/whats-new-vcf-ops.html)
- [VCF Operations API: Super Metrics](https://developer.broadcom.com/xapis/vcf-operations-api/9.0/super-metrics)
- Broadcom KBs: [429899](https://knowledge.broadcom.com/external/article/429899/view-not-displaying-super-metrics-in-ari.html) preview not persisted · [335252](https://knowledge.broadcom.com/external/article/335252/vmware-aria-operations-super-metric-data.html) attribute= and gaps · [397782](https://knowledge.broadcom.com/external/article/397782/vcf-operations-90-sizing-guidelines.html) 9.0 sizing · [324340](https://knowledge.broadcom.com/external/article/324340/vmware-aria-operations-sizing-guidelines.html) sizing index · [444403](https://knowledge.broadcom.com/external/article/444403/understanding-peak-or-worst-performance.html) peak metrics
- [Aria Operations 8.12 best practices: super metrics](https://docs.vmware.com/en/VMware-Aria-Operations/8.12/Best-Practices/GUID-D43C6734-CEF6-45B4-A2CA-B1451A7C915A.html) · [My Top 15 vRealize Operations Super Metrics](https://blogs.vmware.com/cloud-foundation/?p=26755) (older menu paths)
- Community, real-time metrics: [Broad-Cloud](https://broad-cloud.nl/realtime-metrics/) · [cloudpull.io](https://cloudpull.io/blog/vcf-9-1-real-time-data/) · [Jade Network Solutions, deploying via API](https://jadenetworksolutions.co.uk/post/vcf-91-apis/vcf-91-real-time-metrics-deploy/) · [GrafanaRTMsync](https://github.com/makolos2/GrafanaRTMsync)
