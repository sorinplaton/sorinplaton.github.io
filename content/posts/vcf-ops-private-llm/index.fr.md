---
title: "Votre propre LLM pour l'AI Assistant de VCF Operations : Ollama sur un Mac mini"
date: 2026-10-27
description: "VCF Operations 9.1.1 peut utiliser n'importe quel endpoint compatible OpenAI comme fournisseur VCF Private AI. Ollama et un petit Caddy lui donnent le HTTPS, le FQDN et le bearer token qu'il exige. Le montage sur un Mac mini M4, testé de la façon dont VCF Ops l'appelle, et les trois choses qui ont cassé."
summary: "VCF Operations 9.1.1 permet à son AI Assistant d'utiliser n'importe quel endpoint compatible OpenAI que vous hébergez vous-même. Ollama sur un Mac mini M4, derrière un Caddy de 30 lignes, lui donne le HTTPS, le FQDN et le bearer token qu'il exige. Le montage complet, le branchement du fournisseur dans VCF Ops, et les trois choses qui ont cassé : le 404 sur /v1/v1, la limite de 60 secondes de la gateway et un résultat d'outil de 36k tokens."
tags: ["VCF 9.1.1", "VCF Operations", "AI Assistant", "Ollama", "Caddy", "Private AI", "Holodeck"]
categories: ["Notes"]
showToc: true
tocOpen: false
---

VCF Operations 9.1.1 peut confier son AI Assistant à un fournisseur **VCF Private AI** : n'importe quel endpoint compatible OpenAI que vous hébergez vous-même. Ollama parle déjà cette API. Ce qui lui manque, c'est le HTTPS, un FQDN et l'authentification, que VCF Ops exige. Un petit Caddy placé devant ajoute les trois. Voici le montage sur un Mac mini M4, testé de la façon dont VCF Ops l'appelle, puis branché pour de vrai dans l'AI Assistant, y compris les trois choses qui ont cassé en chemin.

*Octobre 2026 · Labo Holodeck VCF 9.1.1 · Ollama 0.40 · Caddy 2.11.7*

## Ce que VCF Operations attend

Le formulaire du fournisseur a l'air simple, mais le client derrière est strict. Il refuse une URL de base contenant une adresse IP, il envoie toujours `Authorization: Bearer …`, il liste les modèles avec `GET /v1/models`, et chaque requête de chat transporte les 25 définitions d'outils de l'assistant. Un modèle qui répond en prose au lieu de renvoyer un `tool_call` ne lui sert à rien.

![Carte des exigences : le HTTPS sur un FQDN est assuré par Caddy avec un certificat autosigné et un enregistrement A ; le bearer token par un header matcher Caddy ; l'API OpenAI par l'endpoint /v1 d'Ollama ; les prompts à 25 outils par un contexte de 16k ; et l'absence de démarrage à froid par un keep-alive permanent.](/images/vcf-ops-llm/requirements.svg)

*Cinq exigences, cinq petites réponses. Seules les deux premières demandent autre chose qu'Ollama.*

## L'architecture

![Architecture : VCF Operations, à 10.1.1.30, résout llm-mac via le DNS du labo, sort par le holorouter, qui masquerade le trafic du labo, et atteint Caddy sur le port 443 du Mac mini. Caddy vérifie le TLS et le bearer token, puis transmet à Ollama sur le port 11434.](/images/vcf-ops-llm/architecture.svg)

*Un nom, un Caddy. Ollama reste sur le port 11434 ; seul Caddy est censé être utilisé de l'extérieur.*

## Étape 1 · Ollama : écouter, rester chargé, contexte de 16k

Trois réglages comptent : écouter sur toutes les interfaces, ne jamais décharger le modèle (`OLLAMA_KEEP_ALIVE=-1`) et un contexte d'au moins 16k tokens, parce que les définitions d'outils occupent à elles seules 3 à 4k et que la valeur par défaut d'Ollama est de 4k.

Sur le Mac, Ollama tourne sous la forme d'**Ollama.app**. L'option « Expose Ollama to the network » de ses réglages donne `0.0.0.0`, mais l'application n'a aucun réglage de keep-alive. Elle transmet toutefois l'environnement de la session graphique à son serveur : un petit agent de connexion définit donc `OLLAMA_KEEP_ALIVE=-1` et redémarre Ollama une fois s'il a démarré avant lui. Le contexte de 16k vient d'une variante créée avec un Modelfile, ce qui permet aux autres modèles du Mac de garder leur empreinte mémoire.

```zsh
# ~/bin/ollama-env.sh  (LaunchAgent com.sorin.ollama-env, RunAtLoad)
#!/bin/zsh
# Ollama.app has no setting for keep-alive, but passes the GUI session environment to its server.
# Run at login (LaunchAgent com.sorin.ollama-env): set OLLAMA_KEEP_ALIVE=-1 and, if the server
# already started without it (login-item race), restart Ollama once so it picks it up.
launchctl setenv OLLAMA_KEEP_ALIVE -1
for i in {1..90}; do P=$(pgrep -n -f "Ollama.app/Contents/Resources/ollama serve") && break; sleep 2; done
if [ -z "$P" ]; then echo "$(date) no ollama serve found"; exit 0; fi
if ps eww -p $P | grep -q "OLLAMA_KEEP_ALIVE=-1"; then echo "$(date) ollama serve $P already has OLLAMA_KEEP_ALIVE=-1"; exit 0; fi
echo "$(date) restarting Ollama to apply OLLAMA_KEEP_ALIVE=-1"
pkill -f "Ollama.app/Contents/MacOS/Ollama"; pkill -f "Ollama.app/Contents/Resources/ollama serve"; sleep 3; open -a Ollama
```

```dockerfile
# ~/ollama-modelfiles/qwen3-8b-16k.Modelfile   →  ollama create qwen3:8b-16k -f …
FROM qwen3:8b
PARAMETER num_ctx 16384
```

## Étape 2 · Caddy : le TLS et le bearer token

Ollama n'a aucune authentification, donc Caddy sert de portier. Un header matcher qui répond **401** à moins que le bearer token ne corresponde exactement, et de longs délais d'expiration, parce qu'un petit modèle peut prendre un moment avant le premier token. Le token est une chaîne aléatoire de 32 caractères ; Caddy le retire aussi avant de transmettre la requête.

![Parcours d'une requête : VCF Operations, à 10.1.1.30, envoie du HTTPS par le holorouter, qui le masquerade sur 10.0.4.0/22, vers Caddy sur le port 443 à 10.0.4.45 ; Caddy vérifie le TLS et le bearer token, puis transmet en HTTP simple à Ollama sur 127.0.0.1:11434.](/images/vcf-ops-llm/caddy-path.svg)

*Une requête, quatre sauts.*

| Saut | Adresse | Port | Rôle |
|---|---|---|---|
| VCF Operations | 10.1.1.30 · ops-a.site-a.vcf.lab | — | client ; envoie `Authorization: Bearer` à chaque appel |
| holorouter | 10.1.1.1 côté labo ; masquerade vers 10.0.4.0/22 | — | DNS du labo pour llm-mac ; aucune route nécessaire |
| Caddy | 10.0.4.45 · llm-mac.site-a.vcf.lab | 443/tcp | TLS, vérification du token, reverse proxy, access log |
| Ollama | 127.0.0.1 (écoute sur 0.0.0.0, utilisé seulement par Caddy) | 11434/tcp | API compatible OpenAI, modèle |

### Installer Caddy

Caddy est un seul binaire, installé avec Homebrew. Il tourne comme **LaunchDaemon** plutôt que par `brew services` : un LaunchDaemon démarre au boot, avant que quiconque se connecte, alors que `brew services` lancé par un utilisateur normal crée un LaunchAgent qui ne tourne que pendant votre session. Le daemon tourne quand même sous l'utilisateur normal (`UserName` dans le plist), parce que macOS permet à n'importe quel utilisateur d'écouter sur les ports inférieurs à 1024 sur toutes les interfaces ; Caddy n'a donc jamais besoin de root.

```bash
brew install caddy jq
mkdir -p /opt/homebrew/etc/caddy /opt/homebrew/var/log/caddy
# put llm-mac.crt and llm-mac.key (made below) in /opt/homebrew/etc/caddy/
# put the Caddyfile (below) at /opt/homebrew/etc/Caddyfile
caddy validate --config /opt/homebrew/etc/Caddyfile
```

```xml
<!-- /Library/LaunchDaemons/com.sorin.caddy.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.sorin.caddy</string>
  <key>UserName</key><string><your macOS user></string>
  <key>ProgramArguments</key><array>
    <string>/opt/homebrew/bin/caddy</string><string>run</string>
    <string>--config</string><string>/opt/homebrew/etc/Caddyfile</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/opt/homebrew/var/log/caddy/caddy.log</string>
  <key>StandardErrorPath</key><string>/opt/homebrew/var/log/caddy/caddy.log</string>
</dict></plist>
```

```bash
sudo chown root:wheel /Library/LaunchDaemons/com.sorin.caddy.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.sorin.caddy.plist
sudo launchctl print system/com.sorin.caddy | grep -E 'state|pid'      # running
curl -s -o /dev/null -w '%{http_code}\n' --resolve llm-mac.site-a.vcf.lab:443:127.0.0.1 \
  --cacert /opt/homebrew/etc/caddy/llm-mac.crt https://llm-mac.site-a.vcf.lab/v1/models   # 401 = up and guarding

# after a Caddyfile change
caddy validate --config /opt/homebrew/etc/Caddyfile && sudo launchctl kickstart -k system/com.sorin.caddy
```

### Le Caddyfile

```caddyfile
# OpenAI-compatible gateway in front of the local Ollama (VCF Operations "VCF Private AI" provider).
# Ollama has no auth of its own, so Caddy is the gate: a static bearer token, checked on every request.
{
	admin off
	auto_https disable_redirects
	default_sni llm-mac.site-a.vcf.lab
	servers {
		timeouts {
			read_body   5m
			read_header 30s
			idle        10m
		}
	}
}

https://llm-mac.site-a.vcf.lab:443 {
	# 10-year self-signed certificate (tls internal would rotate the leaf every 12 h)
	tls /opt/homebrew/etc/caddy/llm-mac.crt /opt/homebrew/etc/caddy/llm-mac.key

	@unauthorized not header Authorization "Bearer <TOKEN>"
	handle @unauthorized {
		header WWW-Authenticate "Bearer"
		respond `{"error":{"message":"invalid or missing bearer token","type":"invalid_request_error","code":"invalid_api_key"}}` 401
	}

	reverse_proxy 127.0.0.1:11434 {
		header_up Host 127.0.0.1:11434
		header_up -Authorization
		flush_interval -1
		transport http {
			dial_timeout            10s
			response_header_timeout 15m
			read_timeout            15m
			write_timeout           15m
		}
	}

	log {
		output file /opt/homebrew/var/log/caddy/access.log {
			roll_size 10MiB
			roll_keep 5
		}
		format json
	}
}
```

**Le rôle de Caddy ici.** C'est un reverse proxy : VCF Operations ne parle jamais qu'à Caddy sur le 443, et Caddy parle à Ollama sur le 11434 par localhost. En lisant le Caddyfile de haut en bas :

- **Il assure la terminaison TLS.** La ligne `tls` charge le certificat et la clé du FQDN. Ollama ne parle que le HTTP simple ; Caddy déchiffre la requête et la transmet en clair sur l'interface de loopback.
- **Il vérifie le token.** Le matcher `@unauthorized` intercepte toute requête dont l'en-tête `Authorization` n'est pas exactement `Bearer <TOKEN>` et répond 401 avec un corps d'erreur au format OpenAI. Cette requête n'atteint jamais Ollama. `header_up -Authorization` retire ensuite l'en-tête avant de transmettre : le token s'arrête à Caddy.
- **Il transmet à Ollama.** `reverse_proxy 127.0.0.1:11434` relaie tout ce qui a passé le contrôle. `header_up Host` réécrit l'en-tête Host pour qu'Ollama ne refuse pas un hôte inconnu, et `flush_interval -1` diffuse la réponse token par token au lieu de la mettre en tampon. Avec des délais de transport de 15 minutes, Caddy ne raccroche jamais au nez d'un modèle lent ; la coupure que vous rencontrerez à l'étape 6 vient de VCF Operations lui-même.
- **Il garde un log de chaque requête.** L'access log JSON enregistre la méthode, le chemin, le statut, la durée et les octets reçus. Chaque échec de ce billet (le 404 sur `/v1/v1`, le 499 à 60 s, le prompt de 36k tokens) a été trouvé là, pas dans l'interface de VCF Operations.

Le bloc global relève de l'intendance : `admin off` ferme l'API de gestion de Caddy, `auto_https disable_redirects` le tient à l'écart du port 80, et `default_sni` choisit le certificat pour un client qui se connecte sans SNI.

### Récupérer les logs

Deux fichiers, appartenant tous deux à votre utilisateur, donc pas de `sudo`. L'access log est celui que nomme le bloc `log` du Caddyfile : une ligne JSON par requête. Les messages propres à Caddy (démarrage, certificat chargé, erreurs de configuration) vont dans `caddy.log`, le `StandardOutPath` du plist du LaunchDaemon.

```bash
tail -n 20 /opt/homebrew/var/log/caddy/access.log     # last 20 requests, raw JSON
tail -f    /opt/homebrew/var/log/caddy/access.log     # follow while you ask the assistant a question
tail -n 50 /opt/homebrew/var/log/caddy/caddy.log      # Caddy itself: started, cert loaded, errors
scp mac:/opt/homebrew/var/log/caddy/access.log .      # pull a copy to work on
```

Une ligne de l'access log, réduite aux champs qui comptent. `duration` est en secondes, `bytes_read` correspond à la taille du prompt, `remote_ip` est le holorouter à cause du masquerade, et `python-httpx`, c'est VCF Operations :

```json
{"ts":1761580001.4,"msg":"handled request",
 "request":{"remote_ip":"<holorouter>","method":"POST","host":"llm-mac.site-a.vcf.lab","uri":"/v1/chat/completions",
            "headers":{"User-Agent":["python-httpx/0.27.0"],"Authorization":["REDACTED"]}},
 "bytes_read":15711,"duration":55.512,"size":1830,"status":200}
```

**Pourquoi pas `tls internal` ?** L'autorité de certification interne de Caddy émet des certificats feuilles dont la durée de vie est de 12 heures. VCF Operations vous demande d'accepter le certificat une seule fois, donc le certificat ne doit plus changer ensuite. Un certificat autosigné de 10 ans pour le FQDN, créé une fois avec `openssl`, reste le même :

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -subj "/CN=llm-mac.site-a.vcf.lab" \
  -addext "subjectAltName=DNS:llm-mac.site-a.vcf.lab,IP:10.0.4.45" \
  -addext "extendedKeyUsage=serverAuth" \
  -keyout llm-mac.key -out llm-mac.crt
```

## Étape 3 · Des noms, pas des IP

VCF Operations refuse les URL basées sur une IP, donc le Mac a besoin d'un enregistrement A dans le DNS qu'utilise VCF Operations, ici le DNS du labo sur le holorouter :

```text
llm-mac.site-a.vcf.lab      A   10.0.4.45
```

Aucune route n'a été nécessaire : le holorouter masquerade le trafic du labo vers le réseau domestique, donc le Mac voit les requêtes arriver du routeur et lui répond directement.

## Étape 4 · Tester comme VCF Operations le fera

Un court script fait ce que fait VCF Operations : lister les modèles avec et sans le token, puis envoyer une requête de chat avec un outil et vérifier qu'un `tool_call` revient. Je l'ai lancé depuis une machine sur le même subnet et la même gateway que VCF Operations.

**Test du fournisseur · Mac mini**

```text
### https://llm-mac.site-a.vcf.lab/v1   model qwen3:8b-16k
GET /v1/models with token    -> 200  models: qwen3:8b-16k, qwen3:8b, hf.co/empero-ai/Qwythos-9B-v2-GGUF:Q4_K_M, gemma3:12b, phi4:latest, deepseek-r1:8b, llama3:latest, deepseek-r1:1.5b
GET /v1/models without token -> 401
GET /v1/models wrong token   -> 401
POST /v1/chat/completions    -> 200  16.867676s
  finish_reason: tool_calls
  tool_call: get_host_metrics {"host":"esx-01a.site-a.vcf.lab","metric":"cpu"}
  tokens: prompt 200 completion 318
```

Puis le cas réaliste : 25 définitions d'outils dans une seule requête, comme les envoie l'AI Assistant. Le modèle a choisi le bon outil avec les bons arguments :

| Modèle | Prompt | Durée | Appel d'outil |
|---|---|---|---|
| qwen3:8b-16k | 3 308 tokens | 45 s | `get_vm_rightsizing` memory, cluster-wld01-01a, last_7d |

45 secondes, c'est lent, parce que qwen3:8b réfléchit avant de répondre (504 tokens de complétion pour un seul appel d'outil). Pour un assistant qui enchaîne quelques appels d'outils par question, c'est le chiffre à surveiller.

## Étape 5 · Brancher le fournisseur dans VCF Operations

Dans VCF Operations 9.1.1, les réglages du fournisseur se trouvent dans une extension à part et, sur une appliance fraîchement installée, c'est une chaîne de prérequis :

1. **Installer l'extension Advanced Configuration** (Extensions → Extensions Management → Install). Elle était seulement *Available* chez moi ; l'installation elle-même prend quelques secondes.
2. **Installer Log Management 9.1.1 ou plus récent** (VCF Operations for Logs). La page « Configure your LLM » n'activera pas ses services sans lui. Dans mon labo, c'était une tâche Build → Lifecycle → VCF Management → Add Component de 1 h 22 min, qui a ajouté un quatrième worker au runtime des services VCF.
3. **Enable Services**, puis brancher le fournisseur sous LLM Providers et choisir le modèle sous Models.

![VCF Operations, Extensions Management : Advanced Configuration à l'état Available avec un bouton Install ; Content Builder, Platform Observability et VMware Intelligent Assist installés](/images/vcf-ops-llm/o1-extensions.webp)

*Extensions → **Extensions Management** : Advanced Configuration est d'abord **Available**, pas installée.*

![Page VMware Intelligent Assist : pour utiliser cette fonction, il faut 1. activer Advanced Configuration, non activée ; 2. configurer un modèle dans Advanced Configuration, non configuré](/images/vcf-ops-llm/o2-intelligent-assist.webp)

*Extensions → **VMware Intelligent Assist** liste ce dont il a besoin : Advanced Configuration activée et un modèle configuré à cet endroit.*

![Advanced Configuration, Configure your LLM : pour utiliser cette fonction, il faut 1. installer Log Management en version 9.1.1 ou plus récente, non installé ; 2. Enable Services, bouton désactivé](/images/vcf-ops-llm/o3-configure-llm.webp)

*Après l'installation d'Advanced Configuration : **Configure your LLM** réclame **Log Management 9.1.1+** avant que **Enable Services** devienne disponible.*

> **VCF Operations** · Extensions → Advanced Configuration → **LLM Providers** → Add Provider → Provider **VCF Private AI**. API Endpoint `https://llm-mac.site-a.vcf.lab` (**sans `/v1`**), API Key = le bearer token → Connect → vérifier l'empreinte et Accept.

![Boîte de dialogue Connect Provider : Provider VCF Private AI, API Endpoint https://llm-mac.site-a.vcf.lab, clé d'API masquée, note indiquant qu'il faut PAIS 9.1.1 (Supervisor service 3.0) ou plus récent](/images/vcf-ops-llm/o4-connect-provider.webp)

*Le formulaire **VCF Private AI** a deux champs. La note parle de Private AI Services (PAIS), mais n'importe quel endpoint compatible OpenAI derrière du HTTPS et un token passe.*

**Ne mettez pas `/v1` dans l'endpoint.** Ma première tentative utilisait `https://llm-mac.site-a.vcf.lab/v1` et VCF Operations a répondu « The provider endpoint is unreachable or returned an unexpected error ». L'access log de Caddy a montré pourquoi : il ajoute lui-même `/v1/models`, donc il a demandé `/v1/v1/models` et reçu un 404.

![Review and Accept Certificate : le certificat est inconnu, empreinte SHA-256 49f6fab9…, émis pour et par CN=llm-mac.site-a.vcf.lab, expire le 23 octobre 2036](/images/vcf-ops-llm/o5-certificate.webp)

*Le certificat autosigné n'est affiché qu'une fois. Comparez l'empreinte avec `openssl x509 -in llm-mac.crt -noout -fingerprint -sha256` avant de l'accepter.*

![Liste LLM Providers : VCF Private AI, Health Healthy](/images/vcf-ops-llm/o6-provider-healthy.webp)

*Connecté et **Healthy**. VCF Operations permet **un fournisseur par type** : une fois VCF Private AI utilisé, il disparaît de la liste déroulante. Un deuxième endpoint implique de modifier ce fournisseur, pas d'en ajouter un autre.*

L'onglet **Models** liste ensuite tout ce que renvoie le `/v1/models` de l'endpoint. La liste est lue à l'enregistrement du fournisseur : après avoir ajouté un modèle à Ollama, modifiez le fournisseur (ressaisir la clé, Connect) pour le voir.

![Onglet Models : modèle sélectionné qwen3:4b-instruct-16k, fournisseur sélectionné VCF Private AI ; dix modèles du Mac listés](/images/vcf-ops-llm/o7-model-selected.webp)

*Le modèle sélectionné est global : chaque agent de VCF Operations qui a besoin d'un LLM l'utilise.*

## Étape 6 · Lui poser une question et réparer ce qui casse

Mon premier choix était `qwen3:8b-16k`, le modèle qui avait réussi le test à 25 outils. Dans l'assistant, chaque question finissait de la même façon :

![VMware Intelligent Assist : question List my vCenters, réponse Error: LLM gateway returned status 504](/images/vcf-ops-llm/o8-error-504.webp)

***LLM gateway returned status 504**. Le 504 vient de la gateway propre à VCF Operations, pas de Caddy (dont les délais sont de 15 minutes).*

L'access log de Caddy racontait toute l'histoire. Une question, ce sont plusieurs appels au modèle (choisir un outil, lire le résultat, peut-être en choisir un autre, rédiger la réponse), chacun avec un prompt de 13 à 19 Ko, et VCF Operations raccroche sur tout appel au bout d'**exactement 60 secondes** :

```text
POST /v1/chat/completions  200  dur=55.5s  in=15711   python-httpx
POST /v1/chat/completions  499  dur=60.0s  in=18835   python-httpx   ← client gave up
POST /v1/chat/completions  200  dur=48.2s  in=15729   python-httpx
POST /v1/chat/completions  499  dur=60.0s  in=16484   python-httpx
```

qwen3:8b sur un Mac de 16 Go avait besoin de 37 à 55 s par appel, surtout pour traiter le prompt et pour sa réflexion, et les derniers appels d'une chaîne dépassaient la limite. La solution a été un modèle plus petit qui ne réfléchit pas : `qwen3:4b-instruct` avec le même contexte de 16k.

```bash
curl http://localhost:11434/api/pull   -d '{"model":"qwen3:4b-instruct"}'
curl http://localhost:11434/api/create -d '{"model":"qwen3:4b-instruct-16k","from":"qwen3:4b-instruct","parameters":{"num_ctx":16384}}'
```

| Modèle sur le Mac | Par appel dans l'assistant | Résultat |
|---|---|---|
| qwen3:8b-16k (avec réflexion) | 37 à 60 s, certains coupés à 60 s | **504** |
| qwen3:4b-instruct-16k | 5 à 20 s (1,4 s sur un préfixe répété) | **répond avec des données en direct** |

![Réponse de VMware Intelligent Assist : 20 premiers hôtes vérifiés, 0 critique, 0 avertissement, 4 sains ; tableau de esx-01a à esx-04a dans cluster-mgmt-01a, tous connectés ; suggestions d'approfondissement](/images/vcf-ops-llm/o9-assistant-answer.webp)

*« Show me hosts with critical issues », répondu par un modèle 4B sur le Mac mini avec des données en direct du labo : quatre hôtes de gestion, tous connectés, aucun critique.*

Une autre limite est apparue sur une question plus large (« Summarize VCF Management Services issues on vsp-01a ») : le résultat d'outil renvoyé par VCF Operations faisait 117 Ko, environ **36k tokens**. Ollama l'a refusé, car il dépassait le contexte de 16k (`request (36235 tokens) exceeds the available context size`) et l'assistant a affiché un 502. Un contexte plus grand ne sauverait pas une petite machine ici, parce que traiter 36k tokens prend plus de temps que la limite de 60 secondes. Les questions ciblées sur les hôtes, les alertes et les VM fonctionnent ; les synthèses tous azimuts demandent un plus gros GPU.

## Points à surveiller

| Symptôme | Ce que c'est | Correctif |
|---|---|---|
| prompt tronqué sans avertissement | **Le contexte de 4k par défaut.** 25 outils, ce sont 3 à 4k tokens avant même la question. | Utiliser une variante 16k (ou `OLLAMA_CONTEXT_LENGTH`) et choisir ce modèle dans VCF Operations, pas le modèle de base. |
| certificat modifié depuis son acceptation | **`tls internal` fait une rotation toutes les 12 h.** | Utiliser plutôt un seul certificat autosigné de longue durée pour le FQDN. |
| `OLLAMA_KEEP_ALIVE` non défini côté serveur | **Ollama.app ignore votre shell.** | Le définir dans le domaine launchd de la session graphique (`launchctl setenv` à la connexion) et redémarrer l'application ; depuis SSH, il faut `sudo launchctl asuser <uid>`. |
| `curl: 000` après 30 s | **Test depuis un shell vCenter.** L'appliance fait passer curl par un proxy local (`https_proxy=localhost:1082`). | Ajouter `--noproxy '*'` ; le chemin lui-même est correct. |
| Install Log Management (version 9.1.1 or later required) | **La page du fournisseur a des prérequis.** Les LLM Providers se trouvent dans l'extension Advanced Configuration, et ses services ont besoin de VCF Operations for Logs. | Planifier ce déploiement avant le travail sur le modèle. |
| LLM gateway returned status 504 | **VCF Operations attend 60 s par appel.** | Chaque appel au modèle doit se terminer en 60 secondes. Les modèles à réflexion sur du petit matériel n'y arrivent pas ; un modèle instruct 4B répond en 5 à 20 s. |
| The provider endpoint is unreachable or returned an unexpected error | **L'endpoint est l'URL de base.** VCF Operations ajoute `/v1` lui-même. | Saisir `https://host`, pas `https://host/v1` ; sinon l'access log montre `/v1/v1/models 404`. |
| pas de deuxième VCF Private AI dans la liste déroulante | **Un seul fournisseur VCF Private AI.** Chaque type de fournisseur ne peut être ajouté qu'une fois. | Pour pointer VCF Operations vers un autre endpoint, modifier le fournisseur existant. |
| LLM gateway returned status 502 | **Les gros résultats d'outils dépassent les 16k.** Certains outils renvoient plus de 100 Ko ; Ollama refuse un prompt plus grand que le contexte. | Garder des questions précises avec les petits modèles. |

## À retenir

- **L'API n'est pas la partie difficile.** Ollama parle déjà OpenAI ; le TLS, un nom et un token sont ce que VCF Operations demande vraiment, et Caddy les ajoute en une trentaine de lignes.
- **Dimensionnez le contexte pour les outils, pas pour la question.** Les 25 définitions d'outils accompagnent chaque requête.
- **Les petites machines fonctionnent, si elles sont rapides.** La limite de 60 secondes par appel compte plus que la taille du modèle : un modèle instruct 4B répond aux questions de l'assistant là où un 8B à réflexion dépasse le délai.
- **Lisez le log de la gateway.** Chaque échec de ce montage (le 404 sur `/v1/v1`, la coupure à 60 secondes, le prompt de 36k tokens) sautait aux yeux dans l'access log de Caddy et le log serveur d'Ollama, et restait invisible dans l'interface de VCF Operations.

### Comment lire le log de la gateway

Le log se trouve là où l'étape 2 l'a placé. Une seule ligne `jq` transforme le flux en une vue d'une ligne par appel, celle utilisée à l'étape 6 :

```bash
tail -f /opt/homebrew/var/log/caddy/access.log | jq -r '[
  (.ts|floor|todate), .request.method, .request.uri, .status,
  "dur=" + ((.duration*10|round)/10|tostring) + "s",
  "in=" + (.bytes_read|tostring),
  (.request.headers["User-Agent"][0] // "-")
] | join("  ")'
```

```text
# what each pattern means
401  any URI                     wrong or missing token; check the API key in the provider
404  /v1/v1/models               endpoint entered with /v1; VCF Operations adds it itself
200  dur=55s in=15711            a call that made it, but close to the 60 s limit
499  dur=60.0s                   VCF Operations hung up at 60 s (499 = client went away); shows as 504 in the UI
502  /v1/chat/completions        Ollama refused the request; the reason is in Ollama's log (prompt over the context)
200  dur=1.4s                    repeated prompt prefix, served from the KV cache
```

Le log propre à Ollama est l'autre moitié : `~/.ollama/logs/server.log`. Il nomme le modèle chargé et son contexte, et affiche la ligne `exceeds the available context size` qui se cache derrière un 502.

---

*Labo : Mac mini M4 16 Go (Ollama.app 0.40.11, qwen3:4b-instruct-16k dans l'assistant), Caddy 2.11.7, VCF Operations 9.1.1 dans un labo Holodeck. Le vrai token n'apparaît jamais dans ce billet.*
