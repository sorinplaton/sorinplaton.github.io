#!/usr/bin/env python3
"""
Interactive, per-host datapath mode change for an NSX-prepared vSphere cluster.

Moves a cluster between Standard, Enhanced Datapath - Standard and Enhanced Datapath -
Performance one host at a time: evacuate -> maintenance mode -> change the NSX host switch
mode on that host (through EDP Standard when needed) -> verify -> exit maintenance mode ->
next host. When every host is done, it can attach the matching Transport Node Profile (TNP)
to the cluster, so the cluster configuration agrees with the hosts.

Method: Gabe Rosas, vcfcore.tech, "How to Enable EDP Standard with Zero Impact".

  pip install pyvmomi requests
  ./edp_per_host.py              # asks for everything it needs
  ./edp_per_host.py --dry-run    # same questions, shows the plan, changes nothing

Every answer can be given up front instead (useful for a second run):
  --vcenter --vc-user --nsx --nsx-user --cluster --mode --numa --lcores --tnp --hosts
Passwords are always asked (or read from VC_PASS / NSX_PASS if set). They are never stored.

Modes:  STANDARD = Standard   ENS_INTERRUPT = Enhanced Datapath - Standard
        ENS = Enhanced Datapath - Performance (needs dedicated lcores per NUMA node)
"""
import argparse, getpass, os, ssl, sys, time
import requests, urllib3
from pyVmomi import vim
from pyVim.connect import SmartConnect, Disconnect
from pyVim.task import WaitForTask

urllib3.disable_warnings()
EP = "/policy/api/v1/infra/sites/default/enforcement-points/default"
MODES = [("ENS", "Enhanced Datapath - Performance"),
         ("ENS_INTERRUPT", "Enhanced Datapath - Standard"),
         ("STANDARD", "Standard")]
VMOTION_OPT = "config.vpxd.network.allowVmotionBetweenENSModes"


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ---------- prompts ----------
def ask(prompt, default=None, preset=None):
    if preset not in (None, ""):
        return preset
    while True:
        v = input(f"{prompt}{f' [{default}]' if default not in (None, '') else ''}: ").strip()
        if v:
            return v
        if default not in (None, ""):
            return str(default)


def ask_secret(prompt, env):
    if os.environ.get(env):
        return os.environ[env]
    while True:
        v = getpass.getpass(f"{prompt}: ")
        if v:
            return v


def ask_yes(prompt, default=False):
    v = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not v else v.startswith("y")


def choose(title, items, label, default=1):
    """items: list; label(item) -> str. Returns the chosen item."""
    print(f"\n{title}")
    for i, it in enumerate(items, 1):
        print(f"  {i}) {label(it)}")
    while True:
        v = ask("Choice", default)
        if v.isdigit() and 1 <= int(v) <= len(items):
            return items[int(v) - 1]
        print(f"  enter a number between 1 and {len(items)}")


# ---------- NSX ----------
class Nsx:
    def __init__(self, host, user, pw):
        self.b = f"https://{host}"; self.s = requests.Session(); self.s.verify = False; self.s.auth = (user, pw)

    def get(self, p):
        r = self.s.get(self.b + p, timeout=60); r.raise_for_status(); return r.json()

    def put(self, p, body):
        r = self.s.put(self.b + p, json=body, timeout=120)
        if r.status_code >= 400:
            try:
                msg = r.json().get("error_message", r.text)
            except ValueError:
                msg = r.text
            raise RuntimeError(f"PUT {p} -> {r.status_code}: {msg[:400]}")
        return r.json()

    def host_tns(self):
        return {t["display_name"]: t for t in self.get(EP + "/host-transport-nodes")["results"]}

    def host_tn(self, name):
        tn = self.host_tns().get(name)
        if not tn:
            raise KeyError(f"{name}: no NSX host transport node with that name")
        return tn

    def tn_state(self, tn):
        return self.get(f"{EP}/host-transport-nodes/{tn['id']}/state")


def tn_mode(tn):
    return tn["host_switch_spec"]["host_switches"][0]["host_switch_mode"]


def set_mode(tn, mode, cpu_config):
    for hs in tn["host_switch_spec"]["host_switches"]:
        hs["host_switch_mode"] = mode
        hs["cpu_config"] = cpu_config if mode == "ENS" else []
    return tn


def path(cur, target):
    """Mode hops NSX accepts: STANDARD <-> ENS must go through ENS_INTERRUPT."""
    if {cur, target} == {"STANDARD", "ENS"}:
        return ["ENS_INTERRUPT", target]
    return [target]


def wait_realized(nsx, name, timeout=1200):
    t0 = time.time(); last = None
    while time.time() - t0 < timeout:
        st = nsx.tn_state(nsx.host_tn(name))
        s = (st.get("state"), st.get("node_deployment_state", {}).get("state"))
        if s != last:
            log(f"   {name}: state={s[0]} deployment={s[1]}"); last = s
        if s == ("success", "success"):
            return int(time.time() - t0)
        if "failed" in str(s).lower():
            raise RuntimeError(f"{name} failed: {st.get('failure_message')}")
        time.sleep(10)
    raise TimeoutError(name)


# ---------- vCenter ----------
def vc_option(si, key):
    try:
        return si.content.setting.QueryView(key)[0].value
    except Exception:
        return None


def evacuate(h, cl):
    """Make sure the host has no powered-on VMs before maintenance mode."""
    drs = cl.configurationEx.drsConfig
    auto = drs.enabled and drs.defaultVmBehavior == "fullyAutomated"
    while True:
        vms = [vm.name for vm in h.vm if vm.runtime.powerState == "poweredOn"]
        if not vms or auto:
            return len(vms)
        print(f"\n   {h.name} still runs {len(vms)} powered-on VM(s): {', '.join(vms[:8])}{' ...' if len(vms) > 8 else ''}")
        print("   DRS is not fully automated, so the script will not move them.")
        v = input("   vMotion them away, then press Enter to check again (or type q to stop): ").strip().lower()
        if v == "q":
            raise SystemExit("stopped by user; nothing changed on this host")


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for o in ("vcenter", "vc-user", "nsx", "nsx-user", "cluster", "mode", "tnp", "hosts"):
        a.add_argument(f"--{o}")
    a.add_argument("--numa", help="NUMA node indexes for ENS lcores, comma separated (e.g. 0,1)")
    a.add_argument("--lcores", type=int, help="lcores per NUMA node for ENS")
    a.add_argument("--dry-run", action="store_true", help="ask, show the plan, change nothing")
    o = a.parse_args()

    print("NSX datapath mode change, one host at a time\n")

    # --- vCenter
    vc = ask("vCenter FQDN or IP", os.environ.get("VC"), o.vcenter)
    vc_user = ask("vCenter user", os.environ.get("VC_USER", "administrator@vsphere.local"), o.vc_user)
    vc_pw = ask_secret(f"Password for {vc_user}", "VC_PASS")
    try:
        si = SmartConnect(host=vc, user=vc_user, pwd=vc_pw, sslContext=ssl._create_unverified_context())
    except Exception as e:
        raise SystemExit(f"vCenter login failed: {e}")
    print(f"  connected to vCenter {vc}")

    # --- NSX
    nsx_host = ask("\nNSX Manager FQDN or IP (VIP if you use one)", os.environ.get("NSX"), o.nsx)
    nsx_user = ask("NSX user", os.environ.get("NSX_USER", "admin"), o.nsx_user)
    nsx = Nsx(nsx_host, nsx_user, ask_secret(f"Password for {nsx_user}@{nsx_host}", "NSX_PASS"))
    try:
        tns = nsx.host_tns()
    except Exception as e:
        raise SystemExit(f"NSX login failed: {e}")
    print(f"  connected to NSX {nsx_host}")

    # --- cluster
    c = si.RetrieveContent()
    view = c.viewManager.CreateContainerView(c.rootFolder, [vim.ClusterComputeResource], True)
    clusters = sorted(view.view, key=lambda x: x.name)
    if o.cluster:
        cl = next((x for x in clusters if x.name == o.cluster), None) or sys.exit(f"cluster {o.cluster} not found")
    else:
        cl = choose("Clusters in this vCenter:", clusters, lambda x: f"{x.name} ({len(x.host)} hosts)")
    hosts = sorted(cl.host, key=lambda h: h.name)
    if o.hosts:
        hosts = [h for h in hosts if h.name in o.hosts.split(",")]
    print(f"\nCurrent NSX host switch mode in {cl.name}:")
    for h in hosts:
        print(f"  {h.name:40s} {tn_mode(tns[h.name]) if h.name in tns else 'not an NSX transport node'}")
    missing = [h.name for h in hosts if h.name not in tns]
    if missing:
        raise SystemExit(f"these hosts are not NSX transport nodes: {', '.join(missing)}")

    # --- target mode
    if o.mode:
        mode = o.mode
    else:
        mode = choose("Target mode:", MODES, lambda m: f"{m[1]} ({m[0]})")[0]
    cpu_config = []
    if mode == "ENS":
        print("\nEDP Performance dedicates logical cores to the datapath on each NUMA node you list.")
        numa = ask("NUMA node indexes, comma separated", "0", o.numa)
        lcores = int(ask("lcores per NUMA node", 2, o.lcores and str(o.lcores)))
        cpu_config = [{"numa_node_index": int(n), "num_lcores": lcores} for n in numa.split(",")]

    # --- TNP to attach at the end
    tnps = [t for t in nsx.get("/policy/api/v1/infra/host-transport-node-profiles")["results"]
            if t.get("host_switch_spec", {}).get("host_switches")]
    match = [t for t in tnps if t["host_switch_spec"]["host_switches"][0]["host_switch_mode"] == mode]
    if o.tnp:
        tnp = next((t for t in tnps if t["id"] == o.tnp or t["display_name"] == o.tnp), None) or sys.exit(f"TNP {o.tnp} not found")
    elif match:
        tnp = choose(f"Transport Node Profile to attach to {cl.name} when all hosts are done:",
                     match + [None], lambda t: f"{t['display_name']} ({mode})" if t else "none, I will attach it myself")
    else:
        print(f"\nNo Transport Node Profile with mode {mode} exists. Clone the cluster's profile, set the mode,")
        print("and attach it after this run (the hosts will show Mismatch until then).")
        tnp = None

    # --- vCenter option for mixed-mode vMotion
    if vc_option(si, VMOTION_OPT) != "true":
        print(f"\nvCenter setting {VMOTION_OPT} is not true. Without it, vMotion between hosts")
        print("in different modes is blocked during the rollout.")
        if not o.dry_run and ask_yes("Set it to true now?", True):
            si.content.setting.UpdateOptions([vim.option.OptionValue(key=VMOTION_OPT, value="true")])
            print("  set")

    # --- plan
    todo = [(h, tn_mode(tns[h.name])) for h in hosts if tn_mode(tns[h.name]) != mode]
    print(f"\nPlan for {cl.name} -> {mode}{f' cpu_config {cpu_config}' if cpu_config else ''}")
    for h, cur in todo:
        print(f"  {h.name}: evacuate, maintenance mode, {' -> '.join([cur] + path(cur, mode))}, exit maintenance mode")
    for h in hosts:
        if (h, tn_mode(tns[h.name])) not in todo:
            print(f"  {h.name}: already {mode}, skipped")
    print(f"  then attach TNP {tnp['display_name']}" if tnp else "  then no TNP change")
    if o.dry_run:
        print("\nDry run: nothing changed."); Disconnect(si); return
    if input("\nType YES to start: ").strip() != "YES":
        print("Cancelled, nothing changed."); Disconnect(si); return
    pause = ask_yes("Pause for confirmation before each host?", True)

    # --- per host
    for h, cur in todo:
        if pause and not ask_yes(f"\nNext: {h.name} ({cur} -> {mode}). Go?", True):
            print("Stopped. Hosts done so far keep their new mode; run the script again to continue."); break
        log(f"== {h.name}: {cur} -> {mode}")
        n = evacuate(h, cl)
        t0 = time.time()
        if not h.runtime.inMaintenanceMode:
            log(f"   entering maintenance mode ({n} powered-on VM(s) for DRS to move)")
            WaitForTask(h.EnterMaintenanceMode_Task(timeout=0, evacuatePoweredOffVms=False))
        log(f"   in maintenance mode after {int(time.time() - t0)}s")
        for step in path(cur, mode):
            tn = set_mode(nsx.host_tn(h.name), step, cpu_config)
            nsx.put(f"{EP}/host-transport-nodes/{tn['id']}", tn)
            log(f"   host switch mode -> {step}: accepted, waiting for realization")
            log(f"   {step} realized in {wait_realized(nsx, h.name)}s")
        WaitForTask(h.ExitMaintenanceMode_Task(timeout=0)); log("   exited maintenance mode")
    else:
        # --- attach the TNP once every host matches it
        if tnp:
            tnc = next(t for t in nsx.get(EP + "/transport-node-collections")["results"]
                       if t["compute_collection_id"].endswith(cl._moId))
            tnc["transport_node_profile_id"] = tnp["path"]
            nsx.put(f"{EP}/transport-node-collections/{tnc['id']}", tnc)
            log(f"attached TNP {tnp['display_name']} to {cl.name}")
            t0 = time.time(); st = {}
            while time.time() - t0 < 900:
                st = nsx.get(f"{EP}/transport-node-collections/{tnc['id']}/state")
                if st.get("state") in ("SUCCESS", "FAILED_TO_REALIZE"):
                    break
                time.sleep(10)
            log(f"cluster state: {st.get('state')} {st.get('state_detail', '')}")
    Disconnect(si)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\ninterrupted")
