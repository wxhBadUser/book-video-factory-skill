# Image generation channels

## Selection

- Default to the built-in `image_gen` tool.
- Use an API/CLI path only when the user explicitly requests it or provides an OpenAI-compatible endpoint.
- Read the system `imagegen` skill before either path.

## Built-in path

Issue one built-in call per sheet or standalone frame. Recover the returned image into the project, display it in chat, inspect it, then split or adopt it.

## Explicit API batch path

Use the bundled imagegen CLI rather than writing a new API client:

```bash
IMAGE_GEN="${CODEX_HOME:-$HOME/.codex}/skills/.system/imagegen/scripts/image_gen.py"
OPENAI_API_KEY="..." OPENAI_BASE_URL="https://example.invalid/v1" \
  python "$IMAGE_GEN" generate-batch \
  --input tmp/imagegen/prompts.jsonl \
  --out-dir assets/generated \
  --concurrency 5 \
  --no-augment
```

Rules:

- Inject credentials only through the current process environment. Never write them into repository files, scripts, prompt JSONL, skill files, logs, or final reports.
- Use one JSONL job per distinct 2×2 sheet or standalone frame. Default concurrency is 5; do not exceed 10.
- Write `assets/generated/prompts/SHEET_MAP.json` before the call. It must map each job to final scene IDs in top-left, top-right, bottom-left, bottom-right order and cover every scene exactly once.
- Specify the exact output filename, orientation, quality, panel order, anchor traits, exact participant count, allowed characters, and forbidden extra characters in every job.
- For a 2×2 sheet, require four equal landscape panels and uniform black gutters. For a standalone frame, require one 16:9 composition.
- Do not describe the younger brother, parents, coworkers, or other supporting roles in a prompt unless that exact frame needs them.
- After the batch finishes, build and display contact sheets in chat. Inspect identity, anatomy, phone direction, hands, tools, panel count, and unexpected people.
- Generate into a staging directory. Resolve outputs by verified basename because compatible endpoints or CLI versions may flatten nested `out` paths under `--out-dir`; reject duplicate basenames instead of overwriting.
- Probe delivered dimensions. Normalize every final scene to the project's exact canvas before it enters `STORYBOARD.json`; the provider may return a nearby size even when a larger size was requested.
- Regenerate only failed jobs with corrected participant counts or physical constraints; preserve good outputs.
- Record successful jobs, failed attempts, regenerations, model, endpoint class, size, and concurrency in `PROMPTS.md`. Do not record the key.

## Counting

Report both:

- successful generated assets/jobs;
- failed or rejected attempts separately.

A 2×2 sheet is one generation job and four final scene images after splitting.
