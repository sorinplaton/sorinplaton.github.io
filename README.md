# sorinplaton.github.io

Hugo + [PaperMod](https://github.com/adityatelange/hugo-PaperMod) blog, published to GitHub Pages by GitHub Actions on every push to `main`.

## Go live (one time, ~5 minutes)

1. Create a **public** repo named `sorinplaton.github.io` on GitHub (empty — no README).
2. Push this folder:
   ```bash
   cd blog
   git init -b main
   git add -A
   git commit -m "Hugo + PaperMod blog"
   git remote add origin git@github.com:sorinplaton/sorinplaton.github.io.git
   git push -u origin main
   ```
3. In the repo: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
4. Wait for the *Deploy Hugo site to Pages* workflow to finish. The site is at <https://sorinplaton.github.io/>.

That's the whole deployment. No servers, no bill.

### Custom domain (optional)

Buy a domain (Cloudflare Registrar or Namecheap are the cheap ones), then:

- DNS: `CNAME  www  sorinplaton.github.io` and the four GitHub Pages `A` records on the apex
  (185.199.108.153 / 109 / 110 / 111).
- Repo **Settings → Pages → Custom domain** → enter the domain, tick *Enforce HTTPS*.
- Change `baseURL` in `hugo.yaml` to `https://yourdomain/`.

## Write a post

```bash
hugo new posts/my-post/index.md     # creates content/posts/my-post/index.md from the archetype
```

Front matter that matters:

```yaml
title: "..."
date: 2026-10-20
description: "One sentence under the title and in link previews."
summary: "Two sentences on the post list and the home page."
tags: ["NSX", "BGP"]
categories: ["Field notes"]   # or "Runbooks"
draft: true                   # remove when ready
```

Images go next to the post (`content/posts/my-post/diagram.png`, referenced as `diagram.png`) or under `static/images/`.
Code fences get syntax highlighting: ` ```bash `, ` ```yaml `, ` ```text `.

## Preview locally

```bash
brew install hugo        # or: winget install Hugo.Hugo.Extended / apt install hugo
hugo server -D           # http://localhost:1313, live reload, shows drafts
```

## Layout

```
hugo.yaml                      site config, menu, PaperMod params
content/posts/<slug>/index.md  one folder per post
content/about.md, projects/    pages
assets/css/extended/           your CSS on top of PaperMod (site.css, evpn.css)
static/                        served as-is (favicon, images/)
themes/PaperMod/               the theme, vendored (update: copy a newer release over it)
.github/workflows/hugo.yml     build + deploy
```

## Notes on the imported posts

- **evpn-nsx-vyos** is the original hand-built HTML article, kept as an `index.html` content file so its diagrams, cards and callouts render exactly as designed. Its CSS lives in `assets/css/extended/evpn.css`, scoped under `.evpn` so it never leaks into other pages; dark mode follows the site toggle. The 12 embedded images were extracted to `static/images/evpn/` (page went from 768 KB to 76 KB).
- **vcf-91-offline-depot-migration** is the Markdown runbook as-is, with front matter added.
- The old per-post repos can be archived once you've confirmed the new URLs; add `aliases:` to a post's front matter if you want the old paths to redirect.
- Fill in the LinkedIn URL in `hugo.yaml` (`socialIcons`) if you want the icon.
