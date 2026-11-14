#!/usr/bin/env python3
"""
Per-host, maintenance-mode-first datapath mode change for an NSX-prepared vSphere cluster.

Follows the zero-impact method described on vcfcore.tech ("How to Enable EDP Standard with
Zero Impact"): one host at a time -> evacuate -> maintenance mode -> change the NSX host
switch mode on that host -> verify -> exit maintenance mode -> next host. When every host
is done, the matching Transport Node Profile (TNP) is attached to the cluster so the
cluster-level configuration agrees with the hosts (no further change is pushed).

  export NSX=nsx-edp.corp.local VC=vc-edp.corp.local NSX_PASS=... VC_PASS=...
  ./edp_per_host.py --cluster edp-cluster --mode ENS --lcores 2 --tnp edp-tnp-ens       # install EDP Performance
  ./edp_per_host.py --cluster edp-cluster --mode STANDARD --tnp edp-tnp                 # remove it (back to Standard)

Modes:  ENS = Enhanced Datapath - Performance   ENS_INTERRUPT = Enhanced Datapath - Standard
        STANDARD = standard datapath
Options: --hosts h1,h2  only these hosts   --dry-run  show the plan only
         --no-evacuate  refuse to touch a host that still runs VMs
         --xvmotion     lab only (no shared storage): storage+compute vMotion VMs off the host first
"""
import argparse, json, os, ssl, sys, time
import requests, urllib3
from pyVmomi import vim
from pyVim.connect import SmartConnect, Disconnect
from pyVim.task import WaitForTask

urllib3.disable_warnings()
EP = "/policy/api/v1/infra/sites/default/enforcement-points/default"


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class Nsx:
    def __init__(self, host, user, pw):
        self.b = f"https://{host}"; self.s = requests.Session(); self.s.verify = False; self.s.auth = (user, pw)

    def get(self, p):
        r = self.s.get(self.b + p); r.raise_for_status(); return r.json()

    def put(self, p, body):
        r = self.s.put(self.b + p, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"PUT {p} -> {r.status_code}: {r.json().get('error_message', r.text)[:400]}")
        return r.json()

    def host_tn(self, fqdn):
        for t in self.get(EP + "/host-transport-nodes")["results"]:
            if t["display_name"] == fqdn:
                return t
        raise KeyError(fqdn)

    def tn_state(self, tn):
        return self.get(f"{EP}/host-transport-nodes/{tn['id']}/state")


def set_mode(tn, mode, lcores, numa):
    for hs in tn["host_switch_spec"]["host_switches"]:
        hs["host_switch_mode"] = mode
        hs["cpu_config"] = [{"num_lcores": lcores, "numa_node_index": numa}] if mode == "ENS" else []
    return tn


def path(cur, target):
    """Mode hops NSX accepts: STANDARD <-> ENS must go through ENS_INTERRUPT."""
    if {cur, target} == {"STANDARD", "ENS"}:
        return ["ENS_INTERRUPT", target]
    return [target]


def wait_realized(nsx, fqdn, timeout=1200):
    t0 = time.time(); last = None
    while time.time() - t0 < timeout:
        st = nsx.tn_state(nsx.host_tn(fqdn))
        s = (st.get("state"), st.get("node_deployment_state", {}).get("state"))
        if s != last:
            log(f"   {fqdn}: state={s[0]} deployment={s[1]}"); last = s
        if s == ("success", "success"):
            return int(time.time() - t0)
        if "failed" in str(s).lower():
            raise RuntimeError(f"{fqdn} failed: {st.get('failure_message')}")
        time.sleep(10)
    raise TimeoutError(fqdn)


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--cluster", required=True); a.add_argument("--mode", required=True, choices=["ENS", "ENS_INTERRUPT", "STANDARD"])
    a.add_argument("--lcores", type=int, default=2); a.add_argument("--numa", type=int, default=0)
    a.add_argument("--tnp", help="TNP to attach to the cluster at the end (must match --mode)")
    a.add_argument("--hosts"); a.add_argument("--dry-run", action="store_true"); a.add_argument("--no-evacuate", action="store_true")
    a.add_argument("--xvmotion", action="store_true", help="lab only: move VMs with storage vMotion to another host's *-local datastore")
    o = a.parse_args()

    nsx = Nsx(os.environ["NSX"], os.environ.get("NSX_USER", "admin"), os.environ["NSX_PASS"])
    si = SmartConnect(host=os.environ["VC"], user=os.environ.get("VC_USER", "administrator@vsphere.local"),
                      pwd=os.environ["VC_PASS"], sslContext=ssl._create_unverified_context())
    c = si.RetrieveContent()
    v = c.viewManager.CreateContainerView(c.rootFolder, [vim.ClusterComputeResource], True)
    cl = next(x for x in v.view if x.name == o.cluster)
    hosts = sorted(cl.host, key=lambda h: h.name)
    if o.hosts:
        hosts = [h for h in hosts if h.name in o.hosts.split(",")]

    log(f"cluster {o.cluster}: {len(hosts)} host(s) -> mode {o.mode}" + (f" (lcores {o.lcores}, NUMA {o.numa})" if o.mode == "ENS" else ""))
    for h in hosts:
        tn = nsx.host_tn(h.name)
        cur = tn["host_switch_spec"]["host_switches"][0]["host_switch_mode"]
        if cur == o.mode:
            log(f"== {h.name}: already {o.mode}, skipped"); continue
        log(f"== {h.name}: {cur} -> {o.mode}")
        if o.dry_run:
            continue
        # 1. evacuate + maintenance mode (DRS evacuates powered-on VMs when it is fully automated)
        vms = [vm for vm in h.vm if vm.runtime.powerState == "poweredOn"]
        if vms and o.no_evacuate:
            raise SystemExit(f"{h.name} still runs {len(vms)} VM(s); evacuate first")
        if vms and o.xvmotion:
            # lab without shared storage: compute+storage vMotion to another host's local datastore
            tgt = next(x for x in sorted(cl.host, key=lambda x: x.name) if x != h and not x.runtime.inMaintenanceMode)
            ds = next(d for d in tgt.datastore if d.name.endswith("-local"))
            for vm in vms:
                t0 = time.time()
                WaitForTask(vm.RelocateVM_Task(vim.vm.RelocateSpec(host=tgt, datastore=ds, pool=cl.resourcePool)))
                log(f"   vMotion {vm.name} -> {tgt.name} ({int(time.time() - t0)}s)")
        if h.runtime.inMaintenanceMode:
            log("   already in maintenance mode")
        log(f"   entering maintenance mode ({len(vms)} powered-on VM(s) to evacuate)")
        t0 = time.time()
        if not h.runtime.inMaintenanceMode:
            WaitForTask(h.EnterMaintenanceMode_Task(timeout=0, evacuatePoweredOffVms=False))
        log(f"   in maintenance mode after {int(time.time() - t0)}s")
        # 2. change the host switch mode on this transport node only.
        #    NSX 4.2.4 rejects STANDARD <-> ENS directly (error 9544 "delete the HostSwitch and create
        #    it back"), but accepts each hop through ENS_INTERRUPT (EDP Standard).
        for step in path(cur, o.mode):
            tn = set_mode(nsx.host_tn(h.name), step, o.lcores, o.numa)
            nsx.put(f"{EP}/host-transport-nodes/{tn['id']}", tn)
            log(f"   host switch mode -> {step}: accepted, waiting for realization")
            log(f"   {step} realized in {wait_realized(nsx, h.name)}s")
        # 3. exit maintenance mode
        WaitForTask(h.ExitMaintenanceMode_Task(timeout=0)); log("   exited maintenance mode")

    if o.tnp and not o.dry_run:
        # 4. attach the matching TNP so the cluster config agrees with the hosts
        tncs = [t for t in nsx.get(EP + "/transport-node-collections")["results"]
                if t["compute_collection_id"].endswith(cl._moId)]
        tnc = tncs[0]
        tnc["transport_node_profile_id"] = f"/infra/host-transport-node-profiles/{o.tnp}"
        nsx.put(f"{EP}/transport-node-collections/{tnc['id']}", tnc)
        log(f"attached TNP {o.tnp} to {o.cluster}")
        t0 = time.time()
        while time.time() - t0 < 900:
            st = nsx.get(f"{EP}/transport-node-collections/{tnc['id']}/state")
            if st.get("state") in ("SUCCESS", "FAILED_TO_REALIZE"):
                break
            time.sleep(10)
        log(f"cluster TNC state: {st.get('state')} {st.get('state_detail', '')}")
    Disconnect(si)


if __name__ == "__main__":
    main()
