---
title: "Superviser NSX Federation dans VCF Operations, partie 2 : construction, installation et ce que le pack remonte"
date: 2026-11-18
description: "Partie 2 du management pack NSX Federation pour VCF Operations 9.1.1 : l'adaptateur construit avec l'Integration SDK, les trois obstacles entre un fichier .pak et un collecteur qui tourne (pack non signé, registre en HTTPS uniquement, certificat non approuvé), et les 65 objets, 312 métriques et quatre alertes que le pack produit sur une fédération de lab à deux locations."
summary: "L'adaptateur collecte une NSX Federation 9.1 à deux locations en 12 secondes et 60 appels d'API. Le faire tourner dans VCF Operations 9.1.1 a demandé trois corrections : autoriser un pack non signé, servir le registre d'images en HTTPS, et faire approuver le certificat du registre par le cloud proxy. Ce billet documente chaque étape et liste ce que le pack remonte."
tags: ["VCF 9.1.1", "NSX Federation", "VCF Operations", "Management pack", "Integration SDK", "Holodeck"]
categories: ["Notes"]
showToc: true
tocOpen: false
images: ["/images/nsx-federation-mp/object-model.svg"]
params:
  seoDescription: "Construire et installer un management pack NSX Federation sur mesure dans VCF Operations 9.1.1 : pack non signé, registre privé, confiance du certificat, et les objets, métriques et alertes remontés."
---

La [partie 1](/fr/posts/nsx-federation-management-pack-design/) s'est terminée sur une conception et sans code. Ce billet couvre la construction, l'installation dans VCF Operations 9.1.1, et ce que le pack montre une fois la collecte en route. L'adaptateur lui-même a pris moins de temps que l'installation. Trois contrôles distincts de VCF Operations se dressaient entre le fichier `.pak` et un collecteur qui tourne.

## Le lab

Un Holodeck VCF 9.1.1 imbriqué. Un Global Manager (`nsx-gm-01a`, un seul nœud actif, pas de standby) fédère deux locations, `site-a-wld01` et `site-a-wld03`, chacune avec un edge cluster de deux nœuds. VCF Operations est en 9.1.1.0 build 25795063 avec un seul cloud proxy, `opscollector-01a`. La construction se fait sur un jump host Linux avec Docker.

## Étape 1 : construire l'adaptateur

Le pack utilise le VCF Operations Integration SDK 1.3.1. Les outils du SDK (`mp-init`, `mp-test`, `mp-build`) demandent une version de Python absente du jump host, ils s'exécutent donc depuis une petite image d'outillage :

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y docker.io git && rm -rf /var/lib/apt/lists/*
RUN pip install vmware-aria-operations-integration-sdk
```

L'adaptateur tient en deux fichiers Python :

- `nsxclient.py` : un client REST NSX en lecture seule, avec pagination par curseur, un budget d'appels par manager, un back-off sur les HTTP 429 et 503, l'épinglage du certificat par empreinte SHA-256, et un petit cache sur disque pour les données qui évoluent lentement.
- `adapter.py` : le modèle d'objets, la logique de collecte et les événements.

L'instance d'adaptateur prend une ou deux adresses de Global Manager et un seul identifiant. Elle découvre les Local Managers à partir du Global Manager, et elle fait confiance à chaque Local Manager d'après l'empreinte de certificat que le Global Manager détient déjà pour lui.

Avant toute installation, la collecte a tourné face aux managers en service, depuis le conteneur d'outillage :

| Test | Résultat |
| --- | --- |
| Exécution normale | 65 objets, 60 appels d'API, environ 12 secondes |
| Seconde adresse de Global Manager injoignable | La collecte continue, un événement supplémentaire pour l'adresse injoignable |
| Aucun Global Manager joignable | Les deux Local Managers restent collectés à partir de la liste de locations en cache, 37 objets, marqués dégradés |

## Étape 2 : un registre pour l'image de l'adaptateur

Un pack SDK n'est pas autonome. Le fichier `.pak` contient les définitions d'objets et un pointeur vers une image de conteneur. Le cloud proxy tire cette image depuis un registre au démarrage de l'instance d'adaptateur. Le lab utilise un conteneur `registry:2` sur le jump host.

`mp-build` a écrit le mauvais registre dans le pack pour une adresse de la forme `host:port` (il s'est rabattu sur Docker Hub). Un court script réécrit deux valeurs, `REGISTRY` et `REPOSITORY`, dans le fichier `.conf` contenu dans `adapter.zip` :

```
REGISTRY=10.0.6.27:5000
REPOSITORY=/nsxfederation
DIGEST=sha256:4085...86e9
```

## Étape 3 : VCF Operations refuse un pack non signé

Le pack n'est pas signé par Broadcom. Le téléversement par l'API de l'appliance échoue :

```
pak_manager.pakfile.no_signature
The PAK file ... is not signed. It is missing required signature files signature.mf and signature.cert
```

VCF Operations 9 bloque par défaut les packs non signés, et la case « Ignore the PAK file signature checking » reste masquée dans la boîte de dialogue de téléversement tant que le blocage n'est pas levé. L'interrupteur se trouve dans la console d'administration (`https://<vcf-operations>/admin`), sous Administrator Settings, Security Settings : « unsigned PAK installation ». Broadcom le documente dans la KB 415279.

Une fois l'interrupteur activé, il y a deux façons d'installer le pack.

### Méthode 1 : à la main dans l'interface

1. Dans la console d'administration, passer « unsigned PAK installation » à enabled.
2. Dans VCF Operations, ouvrir Administration, Integrations, Repository et choisir d'ajouter un pack.
3. Sélectionner le fichier `.pak` et cocher les deux cases : installer même si le pack est déjà installé, et « Ignore the PAK file signature checking ».
4. Téléverser, accepter l'avertissement sur le fichier non signé, et laisser l'installation se terminer.
5. Repasser « unsigned PAK installation » à disabled.

### Méthode 2 : par script

L'API de téléversement de l'appliance (`/casa/upgrade/cluster/pak/reserved/operation/upload`) n'a aucun paramètre pour sauter le contrôle de signature, même avec l'interrupteur activé. Un script doit utiliser les mêmes appels que l'interface, et l'interface les protège par un jeton anti-forgery (`secureToken`) qu'un simple client HTTP ne reçoit jamais. Dans VCF Operations 9.1.1, le jeton se trouve dans la page chargée sous `/vcf-operations/plug/ops/`, à l'intérieur de l'interface principale.

Le lab a utilisé un Chrome headless sur le jump host, piloté par le Chrome DevTools Protocol :

1. Chrome se connecte à la console d'administration et appelle `clusterManagement.action` avec `mainAction=enableUPI`. La même page propose `getUPIStatus` et `disableUPI`.
2. Chrome se connecte à l'interface du produit. Un court script lit les cookies de session et la valeur de `secureToken` dans la page.
3. Un script Python réutilise cette session pour trois appels :

```
POST admin/services/solution/upload?uploadId=<ms>&ignoreSignatureChecking=true&secureToken=<token>
     multipart field "solution" = the .pak file, forceUpload=true, forceContent=true
POST solution.action   mainAction=install
POST solution.action   mainAction=getInstallStatus      (until "completed")
```

4. Chrome appelle `disableUPI`.

Ces appels sont internes à l'interface. Ils ne sont pas supportés et peuvent changer à chaque version. L'interrupteur s'applique à toute l'instance. Dans le lab, il n'est resté activé que le temps du téléversement.

Après l'installation, le type d'adaptateur `NSXFederation` et la solution `iSDK_NSXFederation` 1.0.0 apparaissent dans VCF Operations.

## Étape 4 : le cloud proxy ne parle qu'en HTTPS à un registre

L'instance d'adaptateur a été créée sur le cloud proxy et est passée de `STARTING` à `FAILED` avec un message d'état vide. Le journal du registre ne montrait aucune requête.

Une capture de paquets sur le port du registre a donné la raison. Le cloud proxy ouvrait une connexion et envoyait un hello TLS à un registre qui servait du HTTP en clair. Le cloud proxy ne se rabat pas sur HTTP. Le registre a été recréé avec un certificat :

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

## Étape 5 : le cloud proxy ne fait pas confiance au certificat

Avec le HTTPS en place, le journal du registre a changé (10.0.6.33 est le routeur du lab, qui masque le cloud proxy derrière du NAT) :

```
http: TLS handshake error from 10.0.6.33:56544: remote error: tls: bad certificate
```

Le cloud proxy fait tourner Docker, et Docker fait confiance à un registre privé quand son certificat se trouve dans un dossier qui porte le nom du registre :

```
/etc/docker/certs.d/10.0.6.27:5000/ca.crt
```

SSH est désactivé sur le cloud proxy et l'est resté. Le fichier a été écrit par les guest operations de vCenter (`govc guest.run` avec le compte root de l'appliance). Au démarrage suivant, le cloud proxy a tiré l'image et l'instance a atteint `STARTED`, puis `DATA_RECEIVING`.

Une mise à niveau ou un redéploiement du cloud proxy peut supprimer ce fichier. Un registre dont le certificat vient d'une autorité que le cloud proxy approuve déjà évite cette étape.

## Étape 6 : les trois premiers cycles de collecte

Les données n'arrivent pas d'un seul coup. Avec un intervalle de cinq minutes :

| Cycle | Ce que VCF Operations enregistre |
| --- | --- |
| 1 | Les 65 objets et leurs relations |
| 2 | Les propriétés et les événements ; les clés de métriques sont enregistrées |
| 3 | Les métriques |

Une vérification deux minutes après le premier cycle montre des objets sans métriques, ce qui ressemble à un pack cassé. Les métriques apparaissent environ dix minutes après le démarrage.

## Ce que le pack remonte

65 objets de 14 types, 312 métriques par cycle. La partie 1 prévoyait 12 types. La revue a ajouté la fédération elle-même, le canal de synchronisation et la réalisation d'un objet dans une location, et les règles sont devenues optionnelles.

| Type d'objet | Nombre dans le lab | Exemples de données collectées |
| --- | --- | --- |
| Fédération | 1 | Global Managers actifs, locations collectées sur le total, erreurs d'API, managers au-delà du budget d'appels |
| Global Manager | 1 | État de synchronisation du standby, état de la sauvegarde, alarmes ouvertes, jours avant la première expiration de certificat |
| Nœud de manager | 1 | État en ligne |
| Location | 2 | Connexion au Global Manager, joignabilité depuis le collecteur, nœuds en ligne, échecs de full sync, sessions BGP inter-site, alarmes, version |
| Canal de synchronisation | 6 | État de la connexion entre deux managers |
| Edge cluster | 2 | État inter-site, membres, adresses RTEP |
| Nœud edge | 4 | Tunnels RTEP, sessions BGP inter-site |
| Tunnel RTEP | 8 | État BGP, paquets, paquets perdus et bloqués dans chaque sens |
| Voisin BGP | 16 | État, préfixes reçus et annoncés, messages, durée d'établissement ; 4 sessions d'uplink et 12 sessions internes entre edges |
| Gateway Tier-0 | 1 | Span, locations réalisées, locations en erreur |
| Gateway Tier-1 | 3 | Comme le Tier-0 |
| Segment | 5 | Idem, plus les sous-réseaux et la gateway connectée |
| Politique de sécurité globale | 1 | Span, réalisation, règles au total et désactivées |
| Réalisation (objet dans une location) | 14 | Réalisé ou non, texte d'état |

Les objets règle sont optionnels, politique par politique, et sont restés désactivés pour cette installation.

Le pack rapporte sa propre santé sur l'objet fédération : appels effectués, appels limités, erreurs, et si un manager a épuisé son budget d'appels.

![Inventaire VCF Operations : les types d'objets NSX Federation dans l'arborescence, et le résumé de la location site-a-wld01 avec ses objets liés, ses compteurs d'alarmes, l'âge de la sauvegarde et l'alerte d'écart de version](/images/nsx-federation-mp/ops-inventory-location.webp)

### Les alertes du premier jour

Le pack ne livre aucune définition d'alerte. Un événement envoyé par l'adaptateur apparaît dans VCF Operations comme une alerte « Notification event » sur l'objet.

La version 1.0.0 a montré trois alertes :

- **Warning :** aucun Global Manager standby n'est configuré pour cette fédération.
- **Information, deux fois :** écart de version, chaque location est en 9.1.1.0 alors que le Global Manager est en 9.1.1.0100.

Une quatrième était attendue et manquait : la sauvegarde n'est pas configurée sur le Global Manager. L'adaptateur levait cet événement lors d'une exécution locale, et l'objet Global Manager portait la propriété `backup_enabled = False`, mais aucune alerte n'apparaissait.

La cause se trouvait dans l'adaptateur. L'événement de sauvegarde portait une fault key, et les trois événements visibles n'en portaient pas. VCF Operations n'affiche un événement doté d'une fault key que si le pack livre une définition de symptôme correspondante, et ce pack n'en a aucune. Un tel événement est écarté sans erreur d'un côté comme de l'autre.

La version 1.0.1 envoie tous les événements sans fault key. Après la mise à niveau, l'instance d'adaptateur est restée en `FAILED` jusqu'à un arrêt suivi d'un démarrage. Le cloud proxy avait déjà tiré la nouvelle image. Au premier cycle, la quatrième alerte est apparue :

- **Immediate :** la sauvegarde n'est pas configurée sur le Global Manager `gm-site-a`.

![Alertes VCF Operations filtrées sur « Global Manager » : l'alerte de sauvegarde sur gm-site-a, le standby manquant sur l'objet fédération, et l'écart de version sur les deux locations](/images/nsx-federation-mp/ops-alerts.webp)

Les quatre décrivent correctement le lab.

Les alarmes NSX ouvertes de chaque location arrivent de la même façon. Pendant le remplacement de nœuds edge dans le lab le 18 novembre, les alarmes `rtep_bgp_down` et `rtep_connectivity_lost` sont apparues comme alertes sur les deux objets location. VCF Operations a annulé chacune d'elles environ 15 minutes plus tard, sans aucune action dans VCF Operations.

## Ce qui manque encore

- Les définitions d'alertes et de symptômes, les tableaux de bord et une arborescence d'inventaire. Les alertes ci-dessus sont de simples événements.
- Le nettoyage des objets remplacés. Un nœud edge redéployé reçoit un nouvel ID dans NSX et devient un nouvel objet. Après le remplacement des edges, 26 anciens objets nœud edge et tunnel sont restés dans l'inventaire sans données, à côté des 65 objets actifs.
- La latence entre managers. NSX la mesure et l'affiche dans son interface ; l'API qui la porte n'a pas été trouvée.
- L'état du pare-feu des hôtes, le second point ouvert de la partie 1.
- Les liens vers les objets de l'intégration NSX native.
- Les tests d'un Global Manager standby, d'un switchover et d'un split-brain. Le lab n'a qu'un seul Global Manager.
- Un pack signé. Chaque nouvelle version demande l'interrupteur des packs non signés.

## Sources

- Lab : Holodeck VCF 9.1.1 imbriqué, NSX 9.1.1.0 (Local Managers) et 9.1.1.0100 (Global Manager), VCF Operations 9.1.1.0 build 25795063. Construction et installation les 17 et 18 novembre 2026, version 1.0.1 le 18 novembre.
- [VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction)
- KB Broadcom [415279](https://knowledge.broadcom.com/external/article/415279/error-no-signature-found-on-pak-file-wh.html) : « No signature found on PAK file » lors de l'installation d'un management pack
- [Docker : verify repository client with certificates](https://docs.docker.com/engine/security/certificates/)
