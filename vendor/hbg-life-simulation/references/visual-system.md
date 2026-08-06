# Visual and character system

## Shared HBG look

- Default canvas: 1920×1080, landscape 16:9, 30 fps. Use 1080×1920 portrait 9:16 only when explicitly requested; frame and crop for the target orientation rather than stretching the other orientation.
- Medium: polished Chinese webtoon / grounded seinen manga.
- Rendering: bold black ink contour, realistic anatomy, detailed lived-in environments, subtle halftone and paper grain.
- Base palette: deep brown-black `#1C1410`, tomato red `#D8000F`, warm off-white `#F5F2EF`, plus restrained colors appropriate to the story setting.
- Generated images contain no text. Add all copy in HTML.

Do not carry occupations, ages, relationships, clothing, locations, or mood from a previous story. Derive them from the current text and record them in the project's `CHARACTERS.md`.

## Project identity lock

For every recurring character, record:

- role and relationship;
- age or age stages;
- face shape, eyes, eyebrows, nose, mouth, ears, hair, and one or two subtle recognition marks;
- body type and posture;
- clothing by time period or location;
- emotional register and behaviors;
- traits that may change and traits that must remain invariant.

For a protagonist shown across ages, preserve at least three identity bridges such as eye shape, eyebrow direction, ear shape, mole placement, hair growth pattern, or smile asymmetry. Do not rely on a name alone.

Generate one anchor for a single recurring protagonist, a dual/group anchor only when multiple characters recur together, and separate age anchors when one face changes substantially over time.

## Environment continuity

Record recurring locations, architecture, lighting, weather, season, period details, and important props in the project files. Reuse those facts in every related prompt. Change them only when the narration changes time or place.

## Physical-reality gate

- Treat real-world plausibility as a hard requirement, not an optional polish pass.
- Use standalone generation for anatomy- or orientation-sensitive frames: overlapping hands, close hand-object contact, phones, keyboards, cigarettes, chopsticks, tools, sports equipment, mirrors, and identity-critical portraits. Reserve 2×2 sheets for lower-risk continuity beats. If one grid panel fails, regenerate that beat separately instead of cropping to conceal the problem.
- Count anatomy before evaluating aesthetics: trace each shoulder to one arm and each wrist to one hand; count visible palms and fingers; distinguish intentional occlusion from duplication. Open every hand close-up at 100% and trace every fingertip back through exactly one palm, wrist, and forearm.
- A two-hand contact image must contain exactly two wrist-to-palm chains, with no third palm-shaped mass or duplicated finger fan. Reject extra, fused, detached, mirrored, ownership-ambiguous, or structurally impossible hands and limbs.
- Check the visible front/back, top/bottom, and functional direction of every prop before accepting a frame.
- For phones, the character's eyes must face the screen side; the screen, rear cameras, case, buttons, and charging edge must not contradict one another. Fingers must grip the edges naturally and must not cover or emerge through the device.
- For tools and food props, verify grip, contact point, gravity, heat source, working end, and intended use.
- For mirrors and reflective surfaces, verify that pose, handedness, device direction, gaze, and surrounding geometry agree with the real subject.
- Check doors, chairs, beds, tables, clothing, shadows, and body support for usable geometry and believable contact.
- Reject and regenerate any frame with reversed, impossible, floating, intersecting, or functionally unusable objects, even if character identity and style are correct.
