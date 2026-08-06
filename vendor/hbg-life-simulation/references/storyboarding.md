# Storyboarding rules

## Beat selection

Choose a new still when at least one changes:

- location or time of day;
- dominant action;
- emotional power balance;
- focal object or visual metaphor;
- narration moves from observation to memory or decision.

Do not create a new image for connective wording alone. Combine consecutive sentences when they describe the same action and emotional state.

## Duration-density audit

Run this audit only after real narration timing exists:

- 4–8 seconds: normal still;
- 8–12 seconds: acceptable only when the frame carries enough action, detail, or emotion;
- over 12 seconds: split unless the hold is explicitly intentional;
- over 16 seconds: always treat as missing visual coverage and add at least one beat.

For an 8–12 minute HBG story, target roughly 70–110 images. Prioritize extra images in emotional peaks, dialogue exchanges, object memories, and ending reflections. A stronger zoom does not compensate for a 25–55 second still.

## Shot rotation

Avoid repeating the same face-centric composition. Rotate among:

1. wide establishing frame;
2. medium character action;
3. two-shot with visible distance;
4. over-shoulder observation;
5. hand/object close-up;
6. environmental or symbolic insert.

At least one in every four scenes should work without showing either face.

## Chapter selection

Choose chapter boundaries from the current story's irreversible changes: a new age or life stage, location, relationship phase, power shift, decision, gain, loss, departure, or final reinterpretation. Use as many chapters as the story needs; do not force every project into ten acts.

## Storyboard JSON shape

```json
{
  "id": "s001",
  "chapter": 1,
  "narration": "旁白文本",
  "visual": "one concrete visible moment",
  "characters": ["protagonist", "supporting-character-id"],
  "participants": { "count": 2, "allowed": ["protagonist", "hr-manager"] },
  "shot": "wide | medium | close | insert | two-shot",
  "motion": "zoom-in | zoom-out | pan-left | pan-right | hold",
  "duration_hint": 4.2,
  "asset": "assets/generated/scenes/s001.png"
}
```

`participants` is required for API generation. Author it semantically while reading the story; never derive it later only from surface keywords. `count` is the exact visible-person count for this frame, and `allowed` lists only the people permitted to appear. A crowd frame may use a fixed count such as seven with a bounded description such as `protagonist plus six ordinary commuters`.

## Image prompt checklist

- Repeat immutable identity traits from `visual-system.md`.
- Describe one visible moment, not a paragraph of plot.
- State camera distance and who is in focus.
- State the current story's environment continuity.
- Prohibit text and extra lookalike protagonists.
- Keep hands anatomically plausible when soldering, smoking, eating, or applying bandages.
