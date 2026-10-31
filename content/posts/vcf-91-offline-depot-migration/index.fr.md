---
title: "Migration d'un offline depot VCF 9.1 — de l'ancien ODA au nouveau, en HTTP simple"
date: 2026-09-22
description: "Runbook pour cloner une appliance offline depot VCF 9.1 vers une nouvelle sans autre outil que scp, puis exposer le nouveau depot au VCF Installer en HTTP, sans authentification."
summary: "Un runbook étape par étape pour cloner une appliance offline depot (ODA) VCF 9.1 existante vers une nouvelle avec seulement scp, puis l'exposer au VCF Installer en HTTP simple — y compris le piège du mot de passe root de Photon et le correctif de propriété pour nginx."
tags: ["VCF 9.1", "Offline depot", "ODA", "VCF Installer", "nginx"]
categories: ["Runbooks"]
showToc: true
tocOpen: false
params:
  seoDescription: "Runbook : cloner une appliance offline depot VCF 9.1 vers une nouvelle avec scp seulement, puis la servir au VCF Installer en HTTP, sans authentification."
---

Runbook étape par étape pour cloner une appliance offline depot (ODA) VCF 9.1 existante vers une nouvelle avec seulement `scp`, puis exposer le nouveau depot au VCF Installer en HTTP simple.

## Hypothèses

| Élément | Valeur |
|---|---|
| Ancien ODA | `old-oda` (remplacer par son FQDN/IP) |
| Nouvel ODA | `new-oda` (remplacer par son FQDN/IP) |
| Racine du depot sur les deux | `/var/www/build` → `/var/www/build/PROD/{COMP,metadata,vsan}` |
| Utilisateur SSH non root sur les deux | `admin` (la connexion SSH en root est désactivée) |
| Accès root | se connecter en `admin`, puis `sudo su` sur chaque machine ; rien ne peut être installé sur le nouvel ODA |
| Serveur web | **nginx**, déjà présent sur le nouvel ODA, qui tourne sous l'utilisateur `nginx` (`/var/www` appartient à `nginx` sur l'appliance) |
| Protocole cible | HTTP, sans basic auth (pris en charge dans VCF 9.1 via l'API de l'Installer) |

Toutes les commandes ci-dessous s'exécutent en **root**, sauf mention contraire. Sur chaque machine, connectez-vous en SSH avec `admin` et passez d'abord en root :

```bash
sudo su
```

**Fonctionnement de la copie :** root sur le **nouvel** ODA lance `scp` et *tire* les données depuis l'ancien ODA ; il écrit donc localement avec tous les droits. La seule chose nécessaire est que `admin` puisse *lire* l'ancien depot, ce que permet `chmod -R o+rX` (lecture pour tous sur les fichiers, traversée sur les répertoires) sans toucher au propriétaire ni au groupe. L'étape 4 applique les droits documentés sur le nouvel ODA ; l'ancien ODA peut être remis en état avec les mêmes commandes.

---

## Étape 0 — Vérifications préalables

**Sur l'ancien ODA :**

```bash
ls -l /var/www/build                # expect PROD, possibly an umds-patch-store symlink
du -sh /var/www/build/PROD          # size to move
```

**Sur le nouvel ODA :**

```bash
chage -M -1 root                    # clears Photon's expired-root-password error that breaks su/sudo
nginx -v                            # must already be present
grep -n '^user' /etc/nginx/nginx.conf   # the worker user; expect "user nginx;" — this is the owner used in Step 4
df -h /var/www/build                # needs at least the du value from the old ODA
mkdir -p /var/www/build/PROD
```

---

## Étape 1 — Copier les ISO (`PROD/COMP`)

`COMP` contient tous les binaires des composants (ISO, OVA, vlcp, tar).

**Sur l'ancien ODA** — permettre à `admin` de lire le depot :

```bash
sudo su
chmod -R o+rX /var/www
```

**Sur le nouvel ODA** — tirer les données en root :

```bash
sudo su
chmod -R o+rX /var/www              # optional: root writes locally, so not needed for the copy; Step 4 resets rights anyway
scp -rp admin@old-oda:/var/www/build/PROD/COMP /var/www/build/PROD/
ls /var/www/build/PROD/COMP         # expect VCENTER, NSX_T_MANAGER, SDDC_MANAGER_VCF, VROPS, ...
```

---

## Étape 2 — Transférer les métadonnées (`PROD/metadata` + `PROD/vsan`)

Obligatoire : la première requête du VCF Installer porte sur `/PROD/metadata/productVersionCatalog/v1/productVersionCatalog.json` ; sans ce fichier (et son `.sig`), la connexion au depot échoue avec *Path not found*.

**Sur l'ancien ODA** — rien à faire ; `admin` peut déjà lire après l'étape 1.

**Sur le nouvel ODA** — tirer les données en root :

```bash
scp -rp admin@old-oda:/var/www/build/PROD/metadata admin@old-oda:/var/www/build/PROD/vsan /var/www/build/PROD/
ls /var/www/build/PROD                                       # expect COMP, metadata, vsan
ls /var/www/build/PROD/metadata/productVersionCatalog/v1/   # expect .json AND .sig
```

Si l'étape 0 a montré `umds-patch-store -> PROD/COMP/ESX_HOST/patch-store` sur l'ancien ODA, recréez-le **sur le nouvel ODA** (scp suit les liens symboliques, donc ne le copiez pas comme un répertoire) :

```bash
ln -s PROD/COMP/ESX_HOST/patch-store /var/www/build/umds-patch-store
```

---

## Étape 3 — Seulement si l'ancien ODA n'a pas de métadonnées

Les métadonnées sont produites par le VCF Download Tool (VCFDT) et ne viennent pas d'un download depuis le portail de support. Sur n'importe quelle machine connectée à Internet où VCFDT 9.1 est extrait :

```bash
vcf-download-tool configuration generate --software-depot-id
#  -> register the ID under Software Depot Registration in the VCF Business Services console,
#     save the returned activation code to a file
vcf-download-tool metadata download --depot-download-activation-code-file=<code-file> --depot-store=/tmp/depot
scp -rp /tmp/depot/PROD/metadata /tmp/depot/PROD/vsan admin@new-oda:~/
```

**Sur le nouvel ODA :**

```bash
mv /home/admin/metadata /home/admin/vsan /var/www/build/PROD/
```

> Un catalogue dont on vient de faire le download peut lister des builds 9.1.x plus récents que les binaires copiés depuis l'ancien ODA. Si l'Installer signale plus tard un composant manquant, lancez
> `vcf-download-tool binaries download --vcf-version 9.1 --type INSTALL --depot-store=/tmp/depot --depot-download-activation-code-file=<code-file>`
> (il ignore les fichiers déjà présents) et copiez le delta de la même façon.

---

## Étape 4 — Droits sur les fichiers (selon la documentation Broadcom)

État cible tiré de la procédure Broadcom VCF 9.1 *Set Up an Offline Depot* (dernière étape, « Set ownership and permissions for the document root directory »). La doc suppose Apache (`/var/www/html`, utilisateur `apache`) ; ce runbook y substitue la racine du depot `/var/www/build` et l'utilisateur worker de nginx, `nginx` (vu à l'étape 0). Les propriétaires et les modes sont ceux de la doc.

| Chemin | Propriétaire | Mode | Effet |
|---|---|---|---|
| `/var/www` | tel que livré (`nginx`) | `755` | tout le monde peut traverser jusqu'à la racine des documents |
| `/var/www/build` (DocumentRoot) | `nginx` | `755` | traversable et listable par le serveur web |
| chaque répertoire sous DocumentRoot | `nginx` | `0500` | le serveur web peut lire et traverser ; personne d'autre |
| chaque fichier sous DocumentRoot | `nginx` | `0400` | le serveur web peut lire ; personne d'autre, pas d'écriture |

Tel quel dans la doc :

```
chown apache -R /var/www/html/
find /var/www/html -type d -exec chmod 0500 {} \;
find /var/www/html -type f -exec chmod 0400 {} \;
chmod 755 /var/www/
chmod 755 /var/www/html/
```

**Sur le nouvel ODA** — les mêmes commandes, avec le chemin et l'utilisateur substitués. Respectez l'ordre : le `find` met `/var/www/build` lui-même à `0500`, donc le `chmod 755` final doit venir en dernier :

```bash
rm -f /var/www/build/index.html     # from Broadcom's doc: a stray index.html would be served instead of the directory listing (used to verify PROD is at the root); usually a no-op here
chown nginx -R /var/www/build/
find /var/www/build -type d -exec chmod 0500 {} \;
find /var/www/build -type f -exec chmod 0400 {} \;
chmod 755 /var/www/ /var/www/build/
```

Vérification :

```bash
stat -c '%U %a %n' /var/www /var/www/build /var/www/build/PROD /var/www/build/PROD/metadata/productVersionCatalog/v1/productVersionCatalog.json
#   nginx 755 /var/www          (2755 before = setgid bit; dropping it is harmless)
#   nginx 755 /var/www/build
#   nginx 500 /var/www/build/PROD
#   nginx 400 /var/www/build/PROD/metadata/productVersionCatalog/v1/productVersionCatalog.json
```

**Sur l'ancien ODA** — rétablir les droits documentés, assouplis à l'étape 1 (facultatif ; le service n'a jamais été affecté) :

```bash
find /var/www/build -type d -exec chmod 0500 {} \;
find /var/www/build -type f -exec chmod 0400 {} \;
chmod 755 /var/www/ /var/www/build/
```

> Les autres droits de la doc — `0400 root:root` sur `server.key`/`server.crt` et `0400` sur `.htpasswd` — relèvent de la configuration HTTPS/basic auth et ne s'appliquent pas à un depot uniquement HTTP.

---

## Étape 5 — nginx en HTTP, sans authentification

Objectif : un seul bloc `server` à l'écoute sur le port 80, `root /var/www/build`, le listage des répertoires activé (pour que `http://new-oda/` affiche `PROD`, la vérification que demande le KB Broadcom 413848), et aucune directive `auth_basic` / `ssl_*` dans ce bloc.

**Sur le nouvel ODA** — voir ce qui est déjà configuré :

```bash
grep -n 'include\|listen\|server_name\|root \|index \|autoindex\|auth_basic\|ssl_certificate' /etc/nginx/nginx.conf
ls /etc/nginx/conf.d/ 2>/dev/null
grep -rn 'listen\|root \|auth_basic' /etc/nginx/conf.d/ 2>/dev/null
```

Appliquez ensuite **un seul** des cas suivants, selon ce que montre le grep :

**Cas A — un bloc `server { listen 80; ... }` existe déjà** (la configuration par défaut de nginx en amont, `root html;` + `index index.html`). Modifiez ce bloc avec `vi` pour qu'il ressemble à ceci :

```nginx
server {
    listen       80;
    server_name  _;
    root         /var/www/build;
    autoindex    on;
    location / { }
}
```

Retirez-en toute ligne `index`, `auth_basic`, `auth_basic_user_file` ; ne touchez pas aux autres blocs `server` (par exemple un bloc `listen 443 ssl`) — leur `auth_basic` ne s'applique pas au port 80.

**Cas B — aucun bloc sur le port 80, et `nginx.conf` contient `include /etc/nginx/conf.d/*.conf;` dans `http { }`.** Créez un fichier de configuration dédié :

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

**Cas C — aucun bloc sur le port 80 et aucun include de `conf.d`.** Collez le même bloc `server { … }` dans la section `http { … }` de `/etc/nginx/nginx.conf` avec `vi`.

Ensuite, testez, ouvrez le firewall, activez et vérifiez :

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

Un `HTTP/1.1 200 OK` sur le dernier curl signifie que le depot sert correctement les fichiers.

| Résultat | Cause |
|---|---|
| `403 Forbidden` | droits de l'étape 4 non appliqués, ou `chown` a utilisé un utilisateur différent de la ligne `user` de `nginx.conf` |
| `404 Not Found` | `root` n'est pas `/var/www/build`, ou `PROD` n'est pas directement dessous |
| le listage s'affiche, mais sans `PROD` | mauvais `root`, ou un autre bloc `server` l'emporte sur le port 80 (vérifier `nginx -T \| grep -n listen`) |

---

## Étape 6 — Pointer le VCF Installer vers le nouvel ODA

L'interface du VCF Installer 9.1 n'accepte pas de depot `http://` ; il faut le définir par l'API de l'Installer. Le réglage est repris automatiquement par le Fleet Depot Service après le déploiement. Depuis n'importe quelle machine qui a `curl` :

```bash
INST=<vcf-installer-fqdn>
TOKEN=$(curl -sk -X POST "https://$INST/v1/tokens" -H 'Content-Type: application/json' \
  -d '{"username":"admin@local","password":"<installer-admin-password>"}' \
  | grep -o '"accessToken":"[^"]*' | cut -d'"' -f4)

curl -sk -X PUT "https://$INST/v1/system/settings/depot" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"depotConfiguration":{"isOfflineDepot":true,"url":"http://new-oda"}}'
```

Actualisez l'interface de l'Installer : le depot apparaît comme connecté et la liste des binaires se remplit à partir du catalogue copié.

---

## Liste de vérification rapide

- [ ] `ls /var/www/build/PROD` sur le nouvel ODA affiche `COMP`, `metadata`, `vsan`
- [ ] `productVersionCatalog.json` et `.sig` sont tous deux présents sous `PROD/metadata/productVersionCatalog/v1/`
- [ ] Propriétaire `nginx` (le `user` de `nginx.conf`), répertoires en `0500`, fichiers en `0400`, `/var/www` et `/var/www/build` en `755`
- [ ] `curl -s http://new-oda/` liste `PROD`
- [ ] `curl -sI http://new-oda/PROD/metadata/productVersionCatalog/v1/productVersionCatalog.json` → `200 OK`
- [ ] Depot de l'Installer défini par l'API avec `isOfflineDepot: true` et une URL `http://`

## Références

- Broadcom TechDocs — *Set Up an Offline Depot* (VCF 9.1) : https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/lifecycle-management/binary-management-for-vmware-cloud-foundation/set-up-an-offline-depot-web-server-for-vmware-cloud-foundation.html
- William Lam — *VCF 9.1: New HTTP Offline Depot Support for VCF Installer & Fleet Depot Service* : https://williamlam.com/2026/05/vcf-9-1-new-http-offline-depot-support-for-vcf-installer-fleet-depot-service.html
- KB Broadcom 413848 — *Path not found* de l'Installer sur un offline depot : https://knowledge.broadcom.com/external/article/413848/the-vmware-cloud-foundation-vcf-installe.html
- KB Broadcom 312168 — *VCF Offline Depot deployment* : https://knowledge.broadcom.com/external/article/312168/vcf-offline-depot-deployment.html
- vTAM — *VCF 9.1 patches installation experience* (`metadata download` de VCFDT) : https://vtam.nl/2026/07/14/vcf-9-1-patches-installation-experience/
