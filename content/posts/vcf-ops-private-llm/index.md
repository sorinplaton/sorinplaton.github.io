---
title: "Your own LLM for the VCF Operations AI Assistant: Ollama on a Mac mini"
date: 2026-10-27
description: "VCF Operations 9.1.1 can use any OpenAI-compatible endpoint as a VCF Private AI provider. Ollama plus a small Caddy gives it the HTTPS, FQDN and bearer token it insists on. The build on a Mac mini M4, tested the way VCF Ops calls it, and the three things that broke."
summary: "VCF Operations 9.1.1 lets its AI Assistant use any OpenAI-compatible endpoint you run yourself. Ollama on a Mac mini M4 behind a 30-line Caddy gives it the HTTPS, the FQDN and the bearer token it insists on. The full build, the provider wiring in VCF Ops, and the three things that broke: the /v1/v1 404, the 60-second gateway limit, and a 36k-token tool result."
tags: ["VCF 9.1.1", "VCF Operations", "AI Assistant", "Ollama", "Caddy", "Private AI", "Holodeck"]
categories: ["Field notes"]
showToc: true
tocOpen: false
params:
  seoDescription: "Run the VCF Operations 9.1.1 AI Assistant on your own LLM: Ollama on a Mac mini behind Caddy for HTTPS, FQDN and bearer token, plus the three things that broke."
---

VCF Operations 9.1.1 can send its AI Assistant to a **VCF Private AI** provider: any OpenAI-compatible endpoint you run yourself. Ollama speaks that API already. What it lacks is HTTPS, an FQDN and authentication, which VCF Ops insists on. A small Caddy in front of it adds all three. Here is the build on a Mac mini M4, tested the way VCF Ops calls it, and then wired into the AI Assistant for real, including the three things that broke on the way.

*October 2026 · Holodeck VCF 9.1.1 lab · Ollama 0.40 · Caddy 2.11.7*

## What VCF Operations expects

The provider form looks simple, but the client behind it is strict. It rejects a base URL with an IP address, it always sends `Authorization: Bearer …`, it lists models with `GET /v1/models`, and every chat request carries the assistant's 25 tool definitions. A model that answers in prose instead of returning a `tool_call` is useless to it.

![Requirements map: HTTPS on an FQDN is met by Caddy with a self-signed certificate and an A record; the bearer token by a Caddy header matcher; the OpenAI API by Ollama's /v1 endpoint; the 25-tool prompts by a 16k context; and no cold starts by keep-alive forever.](/images/vcf-ops-llm/requirements.svg)

*Five requirements, five small answers. Only the first two need anything beyond Ollama.*

## The design

![Architecture: VCF Operations at 10.1.1.30 resolves llm-mac through the lab DNS, goes out through the holorouter, which masquerades lab traffic, and reaches Caddy on port 443 on the Mac mini. Caddy checks TLS and the bearer token and forwards to Ollama on port 11434.](/images/vcf-ops-llm/architecture.svg)

*One name, one Caddy. Ollama stays on port 11434; only Caddy is meant to be used from outside.*

## Step 1 · Ollama: listen, stay loaded, 16k context

Three settings matter: listen on all interfaces, never unload the model (`OLLAMA_KEEP_ALIVE=-1`), and a context of at least 16k tokens, because the tool definitions alone fill 3–4k and Ollama's default is 4k.

On the Mac, Ollama runs as **Ollama.app**. "Expose Ollama to the network" in its settings gives `0.0.0.0`, but the app has no keep-alive setting. It does pass the GUI session's environment to its server, so a small login agent sets `OLLAMA_KEEP_ALIVE=-1` and restarts Ollama once if it started first. The 16k context comes from a Modelfile variant, so the other models on the Mac keep their memory footprint.

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

## Step 2 · Caddy: TLS and the bearer token

Ollama has no authentication, so Caddy is the gate. A header matcher that answers **401** unless the bearer token matches exactly, and long timeouts because a small model can take a while before the first token. The token is a random 32-character string; Caddy also strips it before passing the request on.

![Path of one request: VCF Operations at 10.1.1.30 sends HTTPS through the holorouter, which masquerades it onto 10.0.4.0/22, to Caddy on port 443 at 10.0.4.45; Caddy checks TLS and the bearer token and forwards plain HTTP to Ollama on 127.0.0.1:11434.](/images/vcf-ops-llm/caddy-path.svg)

*One request, four hops.*

| Hop | Address | Port | Role |
|---|---|---|---|
| VCF Operations | 10.1.1.30 · ops-a.site-a.vcf.lab | — | client; sends `Authorization: Bearer` on every call |
| holorouter | 10.1.1.1 lab side; masquerades onto 10.0.4.0/22 | — | lab DNS for llm-mac; no routes needed |
| Caddy | 10.0.4.45 · llm-mac.site-a.vcf.lab | 443/tcp | TLS, token check, reverse proxy, access log |
| Ollama | 127.0.0.1 (listens on 0.0.0.0, used only by Caddy) | 11434/tcp | OpenAI-compatible API, model |

### Install Caddy

Caddy is one binary from Homebrew. It runs as a **LaunchDaemon** rather than through `brew services`: a LaunchDaemon starts at boot, before anyone logs in, while `brew services` as a normal user makes a LaunchAgent that only runs while you are logged in. The daemon still runs as the normal user (`UserName` in the plist), because macOS lets any user bind ports below 1024 on all interfaces, so Caddy never needs root.

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

### The Caddyfile

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

**What Caddy does here.** It is a reverse proxy: VCF Operations only ever talks to Caddy on 443, and Caddy talks to Ollama on 11434 over localhost. Reading the Caddyfile top to bottom:

- **Terminates TLS.** The `tls` line loads the certificate and key for the FQDN. Ollama only speaks plain HTTP; Caddy decrypts the request and forwards it in the clear on the loopback interface.
- **Checks the token.** The `@unauthorized` matcher catches any request whose `Authorization` header is not exactly `Bearer <TOKEN>` and answers 401 with an OpenAI-style error body. That request never reaches Ollama. `header_up -Authorization` then removes the header before forwarding, so the token stops at Caddy.
- **Forwards to Ollama.** `reverse_proxy 127.0.0.1:11434` passes everything that got through. `header_up Host` rewrites the Host header so Ollama does not refuse an unknown host, and `flush_interval -1` streams the answer token by token instead of buffering it. The 15-minute transport timeouts mean Caddy never hangs up on a slow model; the cut you will meet in Step 6 is VCF Operations' own.
- **Logs every request.** The JSON access log records method, path, status, duration and bytes in. Every failure in this post (the `/v1/v1` 404, the 499 at 60 s, the 36k-token prompt) was found there, not in the VCF Operations UI.

The global block is housekeeping: `admin off` closes Caddy's management API, `auto_https disable_redirects` keeps it off port 80, and `default_sni` picks the certificate for a client that connects without SNI.

### Get the logs

Two files, both under your user, so no `sudo`. The access log is the one named in the Caddyfile's `log` block: one JSON line per request. Caddy's own messages (start-up, certificate loaded, config errors) go to `caddy.log`, the `StandardOutPath` in the LaunchDaemon plist.

```bash
tail -n 20 /opt/homebrew/var/log/caddy/access.log     # last 20 requests, raw JSON
tail -f    /opt/homebrew/var/log/caddy/access.log     # follow while you ask the assistant a question
tail -n 50 /opt/homebrew/var/log/caddy/caddy.log      # Caddy itself: started, cert loaded, errors
scp mac:/opt/homebrew/var/log/caddy/access.log .      # pull a copy to work on
```

One access-log line, trimmed to the fields that matter. `duration` is seconds, `bytes_read` is the prompt size, `remote_ip` is the holorouter because of the masquerade, and `python-httpx` is VCF Operations:

```json
{"ts":1761580001.4,"msg":"handled request",
 "request":{"remote_ip":"<holorouter>","method":"POST","host":"llm-mac.site-a.vcf.lab","uri":"/v1/chat/completions",
            "headers":{"User-Agent":["python-httpx/0.27.0"],"Authorization":["REDACTED"]}},
 "bytes_read":15711,"duration":55.512,"size":1830,"status":200}
```

**Why not `tls internal`?** Caddy's internal CA issues leaf certificates that live 12 hours. VCF Operations asks you to accept the certificate once, so the certificate must not change afterwards. A 10-year self-signed certificate for the FQDN, made once with `openssl`, stays the same:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -subj "/CN=llm-mac.site-a.vcf.lab" \
  -addext "subjectAltName=DNS:llm-mac.site-a.vcf.lab,IP:10.0.4.45" \
  -addext "extendedKeyUsage=serverAuth" \
  -keyout llm-mac.key -out llm-mac.crt
```

## Step 3 · Names, not IPs

VCF Operations refuses IP-based URLs, so the Mac needs an A record in the DNS that VCF Operations uses, here the lab's DNS on the holorouter:

```text
llm-mac.site-a.vcf.lab      A   10.0.4.45
```

No routes were needed: the holorouter masquerades lab traffic onto the home network, so the Mac sees requests coming from the router and answers it directly.

## Step 4 · Test it the way VCF Operations will

A short script does what VCF Operations does: list the models with and without the token, then send a chat request with one tool and check that a `tool_call` comes back. I ran it from a machine on the same subnet and gateway as VCF Operations.

**Provider test · Mac mini**

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

Then the realistic case: 25 tool definitions in one request, like the AI Assistant sends. The model chose the right tool with the right arguments:

| Model | Prompt | Time | Tool call |
|---|---|---|---|
| qwen3:8b-16k | 3,308 tokens | 45 s | `get_vm_rightsizing` memory, cluster-wld01-01a, last_7d |

45 seconds is slow because qwen3:8b thinks before it answers (504 completion tokens for one tool call). For an assistant that runs a few tool calls per question, that is the number to watch.

## Step 5 · Connect the provider in VCF Operations

In VCF Operations 9.1.1 the provider settings live in an extension of their own, and on a fresh appliance it is a chain of prerequisites:

1. **Install the Advanced Configuration extension** (Extensions → Extensions Management → Install). It was only *Available* on mine; the install itself takes seconds.
2. **Install Log Management 9.1.1 or later** (VCF Operations for Logs). The "Configure your LLM" page will not enable its services without it. In my lab that was a Build → Lifecycle → VCF Management → Add Component job of 1 h 22 min, and it added a fourth worker to the VCF services runtime.
3. **Enable Services**, then connect the provider under LLM Providers and pick the model under Models.

![VCF Operations Extensions Management: Advanced Configuration Available with an Install button; Content Builder, Platform Observability and VMware Intelligent Assist installed](/images/vcf-ops-llm/o1-extensions.webp)

*Extensions → **Extensions Management**: Advanced Configuration starts out **Available**, not installed.*

![VMware Intelligent Assist page: to use this feature you need 1. Activate Advanced Configuration, not activated; 2. Configure a Model in Advanced Configuration, not configured](/images/vcf-ops-llm/o2-intelligent-assist.webp)

*Extensions → **VMware Intelligent Assist** lists what it needs: Advanced Configuration activated and a model configured there.*

![Advanced Configuration, Configure your LLM: to use this feature you need 1. Install Log Management version 9.1.1 or later, not installed; 2. Enable Services, button disabled](/images/vcf-ops-llm/o3-configure-llm.webp)

*After installing Advanced Configuration: **Configure your LLM** asks for **Log Management 9.1.1+** before **Enable Services** becomes available.*

> **VCF Operations** · Extensions → Advanced Configuration → **LLM Providers** → Add Provider → Provider **VCF Private AI**. API Endpoint `https://llm-mac.site-a.vcf.lab` (**no `/v1`**), API Key = the bearer token → Connect → check the thumbprint and Accept.

![Connect Provider dialog: Provider VCF Private AI, API Endpoint https://llm-mac.site-a.vcf.lab, API key masked, note that it requires PAIS 9.1.1 (Supervisor service 3.0) or later](/images/vcf-ops-llm/o4-connect-provider.webp)

*The **VCF Private AI** form has two fields. The note talks about Private AI Services (PAIS), but any OpenAI-compatible endpoint behind HTTPS and a token passes.*

**Leave `/v1` off the endpoint.** My first attempt used `https://llm-mac.site-a.vcf.lab/v1` and VCF Operations answered "The provider endpoint is unreachable or returned an unexpected error". The Caddy access log showed why: it appends `/v1/models` itself, so it asked for `/v1/v1/models` and got a 404.

![Review and Accept Certificate: certificate is unknown, SHA-256 thumbprint 49f6fab9…, issued to and by CN=llm-mac.site-a.vcf.lab, expires October 23, 2036](/images/vcf-ops-llm/o5-certificate.webp)

*The self-signed certificate is shown once. Compare the thumbprint with `openssl x509 -in llm-mac.crt -noout -fingerprint -sha256` before you accept it.*

![LLM Providers list: VCF Private AI, Health Healthy](/images/vcf-ops-llm/o6-provider-healthy.webp)

*Connected and **Healthy**. VCF Operations allows **one provider per type**: once VCF Private AI is used, it disappears from the drop-down. A second endpoint means editing this provider, not adding another.*

The **Models** tab then lists everything the endpoint's `/v1/models` returns. The list is read when the provider is saved: after adding a model to Ollama, edit the provider (re-enter the key, Connect) to see it.

![Models tab: selected model qwen3:4b-instruct-16k, selected provider VCF Private AI; ten models listed from the Mac](/images/vcf-ops-llm/o7-model-selected.webp)

*The selected model is global: every VCF Operations agent that needs an LLM uses it.*

## Step 6 · Ask it something, and fix what breaks

My first choice was `qwen3:8b-16k`, the model that passed the 25-tool test. In the assistant every question ended the same way:

![VMware Intelligent Assist: question List my vCenters, answer Error: LLM gateway returned status 504](/images/vcf-ops-llm/o8-error-504.webp)

***LLM gateway returned status 504**. The 504 comes from VCF Operations' own gateway, not from Caddy (whose timeouts are 15 minutes).*

Caddy's access log told the whole story. One question is several model calls (pick a tool, read the result, maybe pick another, write the answer), each with a 13–19 KB prompt, and VCF Operations hangs up on any call after **exactly 60 seconds**:

```text
POST /v1/chat/completions  200  dur=55.5s  in=15711   python-httpx
POST /v1/chat/completions  499  dur=60.0s  in=18835   python-httpx   ← client gave up
POST /v1/chat/completions  200  dur=48.2s  in=15729   python-httpx
POST /v1/chat/completions  499  dur=60.0s  in=16484   python-httpx
```

qwen3:8b on a 16 GB Mac needed 37–55 s per call, mostly prompt processing plus its thinking, and the later calls in a chain went over. The fix was a smaller model that does not think: `qwen3:4b-instruct` with the same 16k context.

```bash
curl http://localhost:11434/api/pull   -d '{"model":"qwen3:4b-instruct"}'
curl http://localhost:11434/api/create -d '{"model":"qwen3:4b-instruct-16k","from":"qwen3:4b-instruct","parameters":{"num_ctx":16384}}'
```

| Model on the Mac | Per call in the assistant | Result |
|---|---|---|
| qwen3:8b-16k (thinking) | 37–60 s, some cut at 60 s | **504** |
| qwen3:4b-instruct-16k | 5–20 s (1.4 s on a repeated prefix) | **answers with live data** |

![VMware Intelligent Assist answer: checked the first 20 hosts, 0 critical, 0 warning, 4 healthy; table of esx-01a to esx-04a in cluster-mgmt-01a, all connected; drill-down suggestions](/images/vcf-ops-llm/o9-assistant-answer.webp)

*"Show me hosts with critical issues", answered by a 4B model on the Mac mini with live data from the lab: four management hosts, all connected, none critical.*

One more limit showed up on a broader question ("Summarize VCF Management Services issues on vsp-01a"): the tool result VCF Operations sent back was 117 KB, about **36k tokens**. Ollama refused it against the 16k context (`request (36235 tokens) exceeds the available context size`) and the assistant showed a 502. A bigger context would not save a small box here, because processing 36k tokens takes longer than the 60-second limit. Narrow questions about hosts, alerts and VMs work; sweeping summaries need a bigger GPU.

## What to watch for

| Symptom | What it is | Fix |
|---|---|---|
| prompt silently truncated | **The 4k default context.** 25 tools are 3–4k tokens before the question. | Use a 16k variant (or `OLLAMA_CONTEXT_LENGTH`) and pick that model in VCF Operations, not the plain one. |
| certificate changed since you accepted it | **`tls internal` rotates every 12 h.** | Use one long-lived self-signed certificate for the FQDN instead. |
| `OLLAMA_KEEP_ALIVE` not set on the server | **Ollama.app ignores your shell.** | Set it in the GUI launchd domain (`launchctl setenv` at login) and restart the app; from SSH that needs `sudo launchctl asuser <uid>`. |
| `curl: 000` after 30 s | **Testing from a vCenter shell.** The appliance routes curl through a local proxy (`https_proxy=localhost:1082`). | Add `--noproxy '*'`; the path itself is fine. |
| Install Log Management (version 9.1.1 or later required) | **The provider page has prerequisites.** LLM Providers live in the Advanced Configuration extension, and its services need VCF Operations for Logs. | Plan that deployment before the model work. |
| LLM gateway returned status 504 | **VCF Operations waits 60 s per call.** | Each model call must finish in 60 seconds. Thinking models on small hardware miss it; a 4B instruct model answers in 5–20 s. |
| The provider endpoint is unreachable or returned an unexpected error | **The endpoint is the base URL.** VCF Operations adds `/v1` itself. | Enter `https://host`, not `https://host/v1`; the access log shows `/v1/v1/models 404` otherwise. |
| no second VCF Private AI in the drop-down | **One VCF Private AI provider.** Each provider type can be added once. | To point VCF Operations at another endpoint, edit the existing provider. |
| LLM gateway returned status 502 | **Big tool results overflow 16k.** Some tools return 100+ KB; Ollama refuses a prompt larger than the context. | Keep questions specific on small models. |

## Takeaways

- **The API is not the hard part.** Ollama already speaks OpenAI; TLS, a name and a token are what VCF Operations really asks for, and Caddy adds them in about 30 lines.
- **Size the context for the tools, not for the question.** The 25 tool definitions come with every request.
- **Small boxes work, if they are fast.** The 60-second limit per call matters more than model size: a 4B instruct model answers the assistant's questions where a thinking 8B model times out.
- **Read the gateway log.** Every failure in this build (the `/v1/v1` 404, the 60-second cut, the 36k-token prompt) was obvious in Caddy's access log and Ollama's server log, and invisible in the VCF Operations UI.

### How to read the gateway log

The log lives where Step 2 put it. One `jq` line turns the stream into the one-line-per-call view used in Step 6:

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

Ollama's own log is the second half: `~/.ollama/logs/server.log`. It names the loaded model and its context, and prints the `exceeds the available context size` line behind a 502.

---

*Lab: Mac mini M4 16 GB (Ollama.app 0.40.11, qwen3:4b-instruct-16k in the assistant), Caddy 2.11.7, VCF Operations 9.1.1 in a Holodeck lab. The real token never appears in this post.*
