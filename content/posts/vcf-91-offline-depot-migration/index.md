---
title: "VCF 9.1 offline depot migration — old ODA to new ODA over plain HTTP"
date: 2026-09-22
description: "Runbook to clone a VCF 9.1 offline depot appliance onto a new one with nothing but scp, and expose the new depot to the VCF Installer over HTTP without auth."
summary: "A step-by-step runbook to clone an existing VCF 9.1 offline depot appliance (ODA) onto a new one using only scp, then expose it to the VCF Installer over plain HTTP — including the Photon root-password trap and the nginx ownership fix."
tags: ["VCF 9.1", "Offline depot", "ODA", "VCF Installer", "nginx"]
categories: ["Runbooks"]
showToc: true
tocOpen: false
---

Step-by-step runbook to clone an existing VCF 9.1 offline depot appliance (ODA) onto a new one using only `scp`, and expose the new depot to the VCF Installer over plain HTTP.

## Assumptions

| Item | Value |
|---|---|
| Old ODA | `old-oda` (replace with its FQDN/IP) |
| New ODA | `new-oda` (replace with its FQDN/IP) |
| Depot root on both | `/var/www/build` → `/var/www/build/PROD/{COMP,metadata,vsan}` |
| Non-root SSH user on both | `admin` (root SSH login is disabled) |
| Root access | log in as `admin`, then `sudo su` on each box; nothing can be installed on the new ODA |
| Web server | **nginx**, already present on the new ODA, running as user `nginx` (`/var/www` is `nginx`-owned on the appliance) |
| Target protocol | HTTP, no basic auth (supported in VCF 9.1 via the Installer API) |

Every command below runs as **root** unless stated otherwise. On each box, SSH in as `admin` and switch to root first:

```bash
sudo su
```

**How the copy works:** root on the **new** ODA runs `scp` and *pulls* from the old ODA, so it writes locally with full rights. The only thing needed is for `admin` to be able to *read* the old depot, which `chmod -R o+rX` grants (world read on files, traverse on directories) without touching owner or group. Step 4 sets the documented rights on the new ODA; the old ODA can be restored with the same commands.

---

## Step 0 — Pre-checks

**On old ODA:**

```bash
ls -l /var/www/build                # expect PROD, possibly an umds-patch-store symlink
du -sh /var/www/build/PROD          # size to move
```

**On new ODA:**

```bash
chage -M -1 root                    # clears Photon's expired-root-password error that breaks su/sudo
nginx -v                            # must already be present
grep -n '^user' /etc/nginx/nginx.conf   # the worker user; expect "user nginx;" — this is the owner used in Step 4
df -h /var/www/build                # needs at least the du value from the old ODA
mkdir -p /var/www/build/PROD
```

---

## Step 1 — Copy the ISOs (`PROD/COMP`)

`COMP` holds all component binaries (ISOs, OVAs, vlcp, tar).

**On old ODA** — let `admin` read the depot:

```bash
sudo su
chmod -R o+rX /var/www
```

**On new ODA** — pull as root:

```bash
sudo su
chmod -R o+rX /var/www              # optional: root writes locally, so not needed for the copy; Step 4 resets rights anyway
scp -rp admin@old-oda:/var/www/build/PROD/COMP /var/www/build/PROD/
ls /var/www/build/PROD/COMP         # expect VCENTER, NSX_T_MANAGER, SDDC_MANAGER_VCF, VROPS, ...
```

---

## Step 2 — Transfer the metadata (`PROD/metadata` + `PROD/vsan`)

Required: the VCF Installer's first request is `/PROD/metadata/productVersionCatalog/v1/productVersionCatalog.json`; without it (and its `.sig`) the depot connection fails with *Path not found*.

**On old ODA** — nothing to do; `admin` can already read after Step 1.

**On new ODA** — pull as root:

```bash
scp -rp admin@old-oda:/var/www/build/PROD/metadata admin@old-oda:/var/www/build/PROD/vsan /var/www/build/PROD/
ls /var/www/build/PROD                                       # expect COMP, metadata, vsan
ls /var/www/build/PROD/metadata/productVersionCatalog/v1/   # expect .json AND .sig
```

If Step 0 showed `umds-patch-store -> PROD/COMP/ESX_HOST/patch-store` on the old ODA, recreate it **on new ODA** (scp follows symlinks, so don't copy it as a directory):

```bash
ln -s PROD/COMP/ESX_HOST/patch-store /var/www/build/umds-patch-store
```

---

## Step 3 — Only if the old ODA has no metadata

The metadata is produced by the VCF Download Tool (VCFDT), not downloaded from the support portal. On any internet-connected machine with VCFDT 9.1 extracted:

```bash
vcf-download-tool configuration generate --software-depot-id
#  -> register the ID under Software Depot Registration in the VCF Business Services console,
#     save the returned activation code to a file
vcf-download-tool metadata download --depot-download-activation-code-file=<code-file> --depot-store=/tmp/depot
scp -rp /tmp/depot/PROD/metadata /tmp/depot/PROD/vsan admin@new-oda:~/
```

**On new ODA:**

```bash
mv /home/admin/metadata /home/admin/vsan /var/www/build/PROD/
```

> A freshly downloaded catalog may list newer 9.1.x builds than the binaries copied from the old ODA. If the Installer later reports a missing component, run
> `vcf-download-tool binaries download --vcf-version 9.1 --type INSTALL --depot-store=/tmp/depot --depot-download-activation-code-file=<code-file>`
> (it skips files already present) and copy the delta over the same way.

---

## Step 4 — File rights (per Broadcom documentation)

Target state from the Broadcom VCF 9.1 procedure *Set Up an Offline Depot* (last step, "Set ownership and permissions for the document root directory"). The doc assumes Apache (`/var/www/html`, user `apache`); this runbook substitutes the depot root `/var/www/build` and the nginx worker user `nginx` (from Step 0). Ownership and modes are the doc's.

| Path | Owner | Mode | Effect |
|---|---|---|---|
| `/var/www` | as shipped (`nginx`) | `755` | anyone can traverse to the document root |
| `/var/www/build` (DocumentRoot) | `nginx` | `755` | traversable and listable by the web server |
| every directory below DocumentRoot | `nginx` | `0500` | web server can read and traverse; no one else |
| every file below DocumentRoot | `nginx` | `0400` | web server can read; no one else, no write |

Verbatim from the doc:

```
chown apache -R /var/www/html/
find /var/www/html -type d -exec chmod 0500 {} \;
find /var/www/html -type f -exec chmod 0400 {} \;
chmod 755 /var/www/
chmod 755 /var/www/html/
```

**On new ODA** — same commands with path and user substituted. Keep the order: the `find` sets `/var/www/build` itself to `0500`, so the final `chmod 755` must come last:

```bash
rm -f /var/www/build/index.html     # from Broadcom's doc: a stray index.html would be served instead of the directory listing (used to verify PROD is at the root); usually a no-op here
chown nginx -R /var/www/build/
find /var/www/build -type d -exec chmod 0500 {} \;
find /var/www/build -type f -exec chmod 0400 {} \;
chmod 755 /var/www/ /var/www/build/
```

Verify:

```bash
stat -c '%U %a %n' /var/www /var/www/build /var/www/build/PROD /var/www/build/PROD/metadata/productVersionCatalog/v1/productVersionCatalog.json
#   nginx 755 /var/www          (2755 before = setgid bit; dropping it is harmless)
#   nginx 755 /var/www/build
#   nginx 500 /var/www/build/PROD
#   nginx 400 /var/www/build/PROD/metadata/productVersionCatalog/v1/productVersionCatalog.json
```

**On old ODA** — restore the documented rights loosened in Step 1 (optional; serving was never affected):

```bash
find /var/www/build -type d -exec chmod 0500 {} \;
find /var/www/build -type f -exec chmod 0400 {} \;
chmod 755 /var/www/ /var/www/build/
```

> The doc's other rights — `0400 root:root` on `server.key`/`server.crt` and `0400` on `.htpasswd` — belong to the HTTPS/basic-auth setup and do not apply to an HTTP-only depot.

---

## Step 5 — nginx on HTTP, no auth

Goal: one `server` block listening on port 80, `root /var/www/build`, directory listing on (so `http://new-oda/` shows `PROD`, the check Broadcom KB 413848 asks for), and no `auth_basic` / `ssl_*` directives on that block.

**On new ODA** — see what is already configured:

```bash
grep -n 'include\|listen\|server_name\|root \|index \|autoindex\|auth_basic\|ssl_certificate' /etc/nginx/nginx.conf
ls /etc/nginx/conf.d/ 2>/dev/null
grep -rn 'listen\|root \|auth_basic' /etc/nginx/conf.d/ 2>/dev/null
```

Then apply **one** of the following, depending on what the grep shows:

**Case A — a `server { listen 80; ... }` block already exists** (the upstream nginx default, `root html;` + `index index.html`). Edit that block with `vi` so it reads:

```nginx
server {
    listen       80;
    server_name  _;
    root         /var/www/build;
    autoindex    on;
    location / { }
}
```

Remove any `index`, `auth_basic`, `auth_basic_user_file` lines from it; leave other `server` blocks (e.g. a `listen 443 ssl` one) untouched — their `auth_basic` does not apply to port 80.

**Case B — no port-80 block, and `nginx.conf` has `include /etc/nginx/conf.d/*.conf;` inside `http { }`.** Create a drop-in:

```bash
cat > /etc/nginx/conf.d/depot-http.conf << 'EOF'
server {
    listen       80;
    server_name  _;
    root         /var/www/build;
    autoindex    on;
    location / { }
}
EOF
```

**Case C — no port-80 block and no `conf.d` include.** Paste the same `server { … }` block inside the `http { … }` section of `/etc/nginx/nginx.conf` with `vi`.

Then test, open the firewall, enable and verify:

```bash
nginx -t                                                                     # expect: syntax is ok / test is successful
sed -i '/^COMMIT/i -A INPUT -p tcp -m tcp --dport 80 -j ACCEPT' /etc/systemd/scripts/ip4save
systemctl restart iptables
systemctl enable --now nginx
systemctl reload nginx
systemctl status nginx --no-pager                                            # expect: active (running)
curl -s  http://new-oda/ | grep -o 'PROD'                                    # expect: PROD (directory listing works)
curl -sI http://new-oda/PROD/metadata/productVersionCatalog/v1/productVersionCatalog.json
```

`HTTP/1.1 200 OK` on the last curl means the depot is serving correctly.

| Result | Cause |
|---|---|
| `403 Forbidden` | Step 4 rights not applied, or `chown` used a user different from the `user` line in `nginx.conf` |
| `404 Not Found` | `root` not `/var/www/build`, or `PROD` is not directly under it |
| listing shows but no `PROD` | wrong `root`, or another `server` block is winning on port 80 (check `nginx -T \| grep -n listen`) |

---

## Step 6 — Point the VCF Installer at the new ODA

The VCF 9.1 Installer UI does not accept an `http://` depot; it must be set through the Installer API. The setting is carried over to the Fleet Depot Service automatically after deployment. From any box with `curl`:

```bash
INST=<vcf-installer-fqdn>
TOKEN=$(curl -sk -X POST "https://$INST/v1/tokens" -H 'Content-Type: application/json' \
  -d '{"username":"admin@local","password":"<installer-admin-password>"}' \
  | grep -o '"accessToken":"[^"]*' | cut -d'"' -f4)

curl -sk -X PUT "https://$INST/v1/system/settings/depot" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"depotConfiguration":{"isOfflineDepot":true,"url":"http://new-oda"}}'
```

Refresh the Installer UI: the depot shows as connected and the binaries list populates from the copied catalog.

---

## Quick verification checklist

- [ ] `ls /var/www/build/PROD` on new ODA shows `COMP`, `metadata`, `vsan`
- [ ] `productVersionCatalog.json` and `.sig` both present under `PROD/metadata/productVersionCatalog/v1/`
- [ ] Ownership `nginx` (the `user` in `nginx.conf`), dirs `0500`, files `0400`, `/var/www` and `/var/www/build` at `755`
- [ ] `curl -s http://new-oda/` lists `PROD`
- [ ] `curl -sI http://new-oda/PROD/metadata/productVersionCatalog/v1/productVersionCatalog.json` → `200 OK`
- [ ] Installer depot set via API with `isOfflineDepot: true` and an `http://` URL

## References

- Broadcom TechDocs — *Set Up an Offline Depot* (VCF 9.1): https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/lifecycle-management/binary-management-for-vmware-cloud-foundation/set-up-an-offline-depot-web-server-for-vmware-cloud-foundation.html
- William Lam — *VCF 9.1: New HTTP Offline Depot Support for VCF Installer & Fleet Depot Service*: https://williamlam.com/2026/05/vcf-9-1-new-http-offline-depot-support-for-vcf-installer-fleet-depot-service.html
- Broadcom KB 413848 — Installer *Path not found* on offline depot: https://knowledge.broadcom.com/external/article/413848/the-vmware-cloud-foundation-vcf-installe.html
- Broadcom KB 312168 — *VCF Offline Depot deployment*: https://knowledge.broadcom.com/external/article/312168/vcf-offline-depot-deployment.html
- vTAM — *VCF 9.1 patches installation experience* (VCFDT `metadata download`): https://vtam.nl/2026/07/14/vcf-9-1-patches-installation-experience/
