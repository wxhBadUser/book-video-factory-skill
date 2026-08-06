# HBG orientation and style system

Use one project-level `HBG_STYLE.json` as the single source of truth for canvas, captions, opening motion/layout, chapter-label layout, and the accepted BGM baseline. Initialize it before writing composition CSS or rendering:

```bash
# Default: landscape 16:9
node scripts/init_project_style.mjs PROJECT_DIR

# Explicit portrait request only
node scripts/init_project_style.mjs PROJECT_DIR --orientation portrait
```

Do not independently hard-code caption or opening values in HTML and FFmpeg. Composition builders must read `HBG_STYLE.json`; the bundled streaming renderer already does.

## Orientation selection rule

- If the user does not specify orientation, use landscape 16:9 at 1920×1080.
- Use portrait 9:16 at 1080×1920 only when the user explicitly asks for portrait, vertical, 9:16, or an equivalent format.
- Do not infer portrait from the platform, story genre, short-video duration, or a prior project.
- Treat an existing project's `HBG_STYLE.json` as locked. Do not overwrite or rotate it implicitly.
- Landscape and portrait share visual identity and motion language, but canvas, caption placement, maximum text width, crop framing, and safe areas are orientation-specific. Never copy raw coordinates between them.
- Keep story title, chapter names, voice, BGM paths, flash-life labels, and image paths in `PROJECT_SPEC.json`; keep only orientation-dependent pixels and colors here.

## Accepted landscape baseline (default)

For 1920×1080:

- caption font: PingFang SC semibold/bold, 48 px;
- caption box: warm black at about 78% opacity;
- HTML padding: `13px 28px 16px`, 12 px radius;
- ASS `BorderStyle=3` with `Outline=12`;
- caption bottom margin: 58 px, or about 5.4% of frame height;
- caption maximum width: 1540 px;
- semantic caption length: normally 8–18 Chinese characters; prefer a complete phrase over a shorter arbitrary cut;
- flash motion: alternate `1.00 → 1.20` and `1.20 → 1.00` inside each short frame;
- selected-life hold: `1.00 → 1.15` with slight drift.
- opening and chapter-label widths, positions, and font sizes come from the landscape template.

## Accepted portrait baseline

For 1080×1920:

- caption font: PingFang SC semibold/bold, 48 px;
- caption box: warm black at about 78% opacity;
- HTML padding: `13px 28px 16px`, 12 px radius;
- ASS `BorderStyle=3` with `Outline=12`; never use zero outline because the background box effectively disappears;
- caption bottom margin: 280 px, or 14.6% of frame height;
- caption maximum width: 940 px;
- semantic caption length: normally 8–14 Chinese characters;
- flash motion: alternate `1.00 → 1.20` and `1.20 → 1.00` inside each short frame;
- selected-life hold: `1.00 → 1.15` with slight drift.
- opening and chapter-label widths, positions, and font sizes come from the portrait template.

When a reference video has a different aspect ratio, transfer visual appearance, not raw coordinates. Measure font size, box opacity, and padding from the reference, but derive placement and cropping from the target orientation's safe-zone ratios. Never copy `bottom`, `MarginV`, `maxWidth`, pan distance, or crop coordinates directly across orientations.

Every opening flash source must already match the selected canvas. Do not treat `object-fit: cover` as an orientation converter: a portrait source inside a landscape frame discards most of its vertical composition and creates an unintended close-up. The style validator probes every configured flash image and rejects dimensions other than the project's exact canvas.

## HTML and ASS parity gate

The short HyperFrames preview and the long streaming render use different caption engines. A correct HTML preview does not prove the final ASS captions are correct.

Before a long render:

1. Render the 15–20 second HyperFrames preview.
2. Set `audio_meta.opening.previewVideo` to the exact approved preview file, or adopt it to the canonical path in `HBG_STYLE.json`.
3. Run `node scripts/validate_style_system.mjs PROJECT_DIR`.
4. Confirm the validator reports the requested orientation and dimensions, safe orientation-specific caption ratios, measurable flash motion, and measurable selected-life motion.
5. Run the streaming renderer with `HBG_VALIDATE_ONLY=1`; confirm it resolves the same approved opening path.

The full renderer must fail if the approved opening is missing or has the wrong orientation. Do not fall back to an older preview, the raw ratchet video, or an asset made for the other orientation.

## Reference extraction rule

When the user names an accepted prior video, extract encoded frames from that final MP4 rather than relying only on its source CSS. Compare at least:

- two caption frames on light and dark backgrounds;
- two frames from within one flash image;
- selected-life hold near its start and end.

Only freeze parameters after the encoded reference and encoded target have both been inspected.
