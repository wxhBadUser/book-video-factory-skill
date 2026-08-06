# HBG opening and audio system

## Opening timing

- Start lead narration almost immediately.
- Place the flash/SFX no more than 0.05 seconds after the lead finishes.
- Use about nine alternate lives across 1.4–1.8 seconds.
- Hold the selected life for 0.1–0.2 seconds before the reveal voice starts.
- Keep the selected frame visible until the reveal finishes.
- Start the first body image only after reveal narration ends.

Keep the opening clean. Show centered life labels during the flash and the full selected-life title on the final card. Do not show progress bars, counters, `副本载入完成`, or numeric HUD badges.

Use orientation-native flash art. In landscape, each final candidate image must be 1920×1080 and composed as a wide shot before zoom is applied. A 5×2 sheet on a landscape canvas produces portrait-shaped cells and is therefore forbidden for landscape flash extraction. Use 2×2 sheets of landscape panels or standalone landscape images instead. Portrait projects follow the inverse rule at 1080×1920.

Render each candidate preview to a revisioned filename. After approval, bind that exact file through `audio_meta.opening.previewVideo` or the canonical approved path in `HBG_STYLE.json`. The long renderer must not search old `v2`/`v3` previews or substitute the raw flash/SFX asset.

## HBG camera language

- Alternate aggressive short zoom-in and zoom-out during every flash image. For a 0.14–0.20 second flash frame, use about `scale 1.00 → 1.18–1.22` and `1.18–1.22 → 1.00`, with a small opposing x/y drift. A sequence of static hard cuts does not satisfy the opening motion requirement.
- On the selected card, use a clearly readable slow `scale 1.00 → 1.13–1.15` plus slight horizontal drift. Verify the first and last encoded hold frames; if the crop change is not immediately visible side by side, strengthen it before the full render.
- In the body, alternate zoom and pan; do not repeat the same direction across consecutive images.

Run `scripts/validate_style_system.mjs PROJECT_DIR` after the preview is approved. It samples two frames inside the first flash and two frames across the selected hold; an unmeasurable crop change is a hard failure.

## Fixed BGM workflow

Default to one static BGM baseline. Do not duck under narration unless the user explicitly requests it.

1. Probe source integrated loudness, true peak, and the first 20 seconds.
2. Add BGM as a separate audio element with one fixed `data-volume`.
3. Render a 15–20 second opening preview covering lead, flash, reveal, and the first body sentence.
4. If the BGM is inaudible, raise it by 3–4 dB and rerender; do not make tiny adjustments.
5. Check final true peak and retain at least 3 dB headroom.

Validated project example:

- source BGM integrated loudness: approximately `-15.3 LUFS`;
- `data-volume=0.055`: too quiet;
- `data-volume=0.14`: audible but still conservative;
- `data-volume=0.22`: accepted louder review level;
- no fades, no ducking, no volume keyframes;
- encoded true peak remained approximately `-3.6 dBTP`.

Do not copy `0.22` blindly to a different track. Match perceived level using the source loudness and the 3–4 dB iteration rule.
