# Brand assets for `hisense_vrf`

Source SVGs and exported PNGs for the icon/logo of the integration. The chosen design (concept **C3**) is packaged in [`final/custom_integrations/hisense_vrf/`](final/custom_integrations/hisense_vrf/) ready to be submitted as a PR to [home-assistant/brands](https://github.com/home-assistant/brands).

## Chosen design — Concept C3

Gateway-centric composition: the i-Modkit gateway is the focal point of the icon, with a small outdoor unit on the upper-left feeding a refrigerant arc into the gateway, and a Modbus bus dropping to three indoor cassette-style units at the bottom. The gateway shows a simulated 4-digit display, status LEDs and an RJ45 port — visually communicating "smart Modbus controller for a VRF system".

The landscape logo uses the icon on the left and a sans-serif "Hisense / VRF" wordmark on the right (the same human-readable name used in `manifest.json` — not the Hisense corporate logo).

| File | Size | Use |
|------|------|-----|
| `icon.png` | 256×256 | Standard icon shown in HA config flow, device cards, etc. |
| `icon@2x.png` | 512×512 | Hi-DPI version. |
| `logo.png` | 256×128 | Landscape logo for the brand banner. |
| `logo@2x.png` | 512×256 | Hi-DPI version. |

All four are PNG / 8-bit RGBA, non-interlaced (re-export with `rsvg-convert` if needed — `--keep-aspect-ratio` is irrelevant since the SVG viewBox is already locked).

## Submitting to home-assistant/brands

1. Fork https://github.com/home-assistant/brands
2. From this repo, copy the contents of [`brand/final/custom_integrations/hisense_vrf/`](final/custom_integrations/hisense_vrf/) into your fork at the same path:
   ```
   custom_integrations/hisense_vrf/icon.png
   custom_integrations/hisense_vrf/icon@2x.png
   custom_integrations/hisense_vrf/logo.png
   custom_integrations/hisense_vrf/logo@2x.png
   ```
3. Commit + open the PR. A bot validates dimensions, transparency and aspect-ratio automatically — if anything fails the PR comment will spell it out and the SVG can be tweaked + re-exported.
4. Once merged, HA fetches the assets from the official CDN; **no code change in this repo** is needed.

When the integration eventually lands in HA core, the path moves from `custom_integrations/` to `core_integrations/` — same files, just relocated.

## Other concepts explored

The earlier candidates remain in `concept_a/`, `concept_b/`, `concept_c/` and `concept_c2/` for reference. Each has its own SVG + PNG exports. They were not chosen — C3 won because:

- Concept A was readable but didn't represent the controller explicitly.
- Concept B was too abstract — could pass for any "network of nodes".
- Concept C was the right narrative but used an outdoor-unit-centred composition; the gateway was implicit in the bus line.
- Concept C2 (intermediate) modernised the style but the gateway was still small relative to the outdoor unit.
- **C3** makes the gateway the largest element and adds the RJ45 + display affordances that read instantly as "controller".

## Re-generating from source

```bash
brew install librsvg
cd brand/concept_c3
rsvg-convert -w 256 -h 256 icon.svg -o icon.png
rsvg-convert -w 512 -h 512 icon.svg -o icon@2x.png
rsvg-convert -w 256 -h 128 logo.svg -o logo.png
rsvg-convert -w 512 -h 256 logo.svg -o logo@2x.png
cp icon.png icon@2x.png logo.png logo@2x.png ../final/custom_integrations/hisense_vrf/
```

## On trademarks

The icon uses no Hisense imagery, official logos, fonts or registered glyphs — every element is original geometric design. The wordmark on the logo uses the same human-readable name ("Hisense VRF") already declared in `manifest.json`, in a generic system font, which is consistent with how other custom integrations (e.g. those controlling third-party brand devices) label themselves on the HA brands repo. Hisense never objected to similar conventions used by other community projects.

If the integration is ever moved to HA core, the maintainers may request trademark clearance; until then this design sidesteps the issue.
