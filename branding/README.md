# AgentShip branding

The canonical copy of the AgentShip brand kit, vendored here so the README, the docs site and
Studio all draw on one set of files. The upstream kit (`agentship-brand-kit`) carries the full
guidelines in `BRAND.md`; this file covers what lives here and which file to reach for.

## Layout

```
logo/svg/        vector logos — use these first
logo/png/        raster exports, app icons, apple-touch-icon
logo/favicon.ico 16/32/48
banner/          1280x400 README banner (@2x) and 1280x640 social preview
fonts/           self-hosted woff2/woff + fonts.css (SIL OFL — see Licensing)
tokens/          tokens.json, tokens.css, tailwind.preset.js, mkdocs-agentship.css
```

Two architecture illustrations used to live here, `hero/image.png` and `Architecture.png`.
Both are gone. They predated the rebuild and showed a system that does not exist — Google ADK,
OpenAI SDK and CrewAI as engines, a memory layer with vector stores, guardrails, eval datasets,
LLM-as-judge — and each carried a different old wordmark, one of them with a glow. The README's
architecture picture is now the Mermaid diagram in the README itself, which is generated from
the same facts the rest of the page is held to. If a raster diagram is needed again, draw it
from `tokens/` and keep it in step with that one.

## Which logo

| Place | File |
|---|---|
| Dark background — README banner, Studio, docs dark mode | `logo/svg/agentship-lockup-horizontal-on-dark.svg`, `agentship-mark-on-dark.svg` |
| Light background — PyPI, slides, print | `logo/svg/agentship-lockup-horizontal-on-light.svg`, `agentship-mark-on-light.svg` |
| One-colour contexts — stamps, small badges | `agentship-mark-mono-black.svg`, `agentship-mark-mono-white.svg` |
| Square or centred layouts | `logo/svg/agentship-lockup-stacked-*.svg` |
| App icon, PWA, store listings | `logo/png/agentship-app-icon-{1024,512,192}.png` |
| iOS home screen | `logo/png/apple-touch-icon-180.png` |
| Browser tab | `logo/svg/favicon.svg`, with `logo/favicon.ico` as fallback |

The wordmark in the SVG lockups is outlined, so a lockup renders correctly without the display
font installed.

## Where these are consumed

- `README.md` — `branding/banner/github-readme-banner@2x.png`
- Docs site — `docs/assets/` (mark, favicon), `docs/assets/fonts/`, `docs/stylesheets/agentship.css`,
  wired up in `mkdocs.yml`
- Studio — palette and favicon are inlined in
  `packages/agentship-service/src/agentship_service/static/studio.html`, which is deliberately a
  single no-build file, so it carries the token *values* rather than importing `tokens/tokens.css`

Copies under `docs/` are copies on purpose: MkDocs only publishes what is inside `docs_dir`. If a
token value changes here, update `docs/stylesheets/agentship.css` and Studio's `:root` block too.

## Rules worth repeating

- The accent `#A685EE` is one colour, for emphasis: the mark's top block, links, focus rings,
  active states. Never a large background fill. On light backgrounds use `#6A47C2` instead — the
  dark-mode accent does not carry enough contrast as text on Paper.
- No pure white text (use Fog `#E8EEF0`), no pure black ground (use Ink `#0D1317`).
- No gradients, glows, or emoji.
- Do not recolour, rotate, stretch, outline or shadow the mark, rearrange its blocks, or set the
  wordmark in another typeface. Clearspace: one container block (~16% of mark height) all round.
  Minimum size: mark 16px, horizontal lockup 96px wide.

## Licensing

The AgentShip source is Apache 2.0 (see [`../LICENSE`](../LICENSE)). The fonts vendored in
`fonts/` are **not** ours and are redistributed under the SIL Open Font License 1.1, with each
licence kept beside them:

| Font | Licence |
|---|---|
| Bricolage Grotesque | [`fonts/LICENSE-bricolage-grotesque.txt`](fonts/LICENSE-bricolage-grotesque.txt) |
| Instrument Sans | [`fonts/LICENSE-instrument-sans.txt`](fonts/LICENSE-instrument-sans.txt) |
| JetBrains Mono | [`fonts/LICENSE-jetbrains-mono.txt`](fonts/LICENSE-jetbrains-mono.txt) |

The same three licence files sit in `docs/assets/fonts/`, next to the copies the docs site serves.
Keep a licence file with any copy of the fonts you make — the OFL requires it.

The AgentShip name, wordmark, mark and logo files are project trademarks rather than code. Apache
2.0 grants no trademark rights (section 6). Use them to refer to or link to AgentShip; do not use
them as the identity of a fork, or modified, for a derivative product.
