import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { assertProjectFile, loadProjectSpec, toChineseNumber } from "./project_config.mjs";

const root = process.cwd();
const spec = loadProjectSpec(root);
const storyboard = JSON.parse(fs.readFileSync(path.join(root, "STORYBOARD.json"), "utf8"));
const audio = JSON.parse(fs.readFileSync(path.join(root, "audio_meta.json"), "utf8"));
const style = JSON.parse(fs.readFileSync(path.join(root, "HBG_STYLE.json"), "utf8"));
const totalDuration = Number(audio.totalDuration.toFixed(3));
const narrationDuration = Number(audio.narrationDuration.toFixed(3));
const opening = audio.opening;
const captions = audio.captions ?? [];
const W = style.canvas.width;
const H = style.canvas.height;
const captionStyle = style.captions;
const openingStyle = style.opening;
const openingLayout = openingStyle.layout;
const chapterStyle = style.chapters;
if (!openingLayout || !chapterStyle) throw new Error("HBG_STYLE.json requires opening.layout and chapters orientation-specific values");

const bgmSourceRelative = spec.audio?.bgmSource;
const bgmPathRelative = spec.audio?.bgmLooped;
const narrationPathRelative = audio.body?.path ?? spec.audio?.narrationOutput;
const finalImageRelative = spec.opening?.finalImage;
const flashLives = spec.opening?.flashLives ?? [];
if (flashLives.length === 0) throw new Error("PROJECT_SPEC.opening.flashLives must contain at least one life");
const bgmSource = assertProjectFile(root, bgmSourceRelative, "BGM source");
const finalImage = assertProjectFile(root, finalImageRelative, "selected-life image");
for (const life of flashLives) assertProjectFile(root, life.asset, `flash life ${life.label}`);
assertProjectFile(root, narrationPathRelative, "narration audio");

const fullBgmVolume = style.audio.bgmVolume;
const round3 = (value) => Number(value.toFixed(3));
const leadStart = round3(opening.lead.start);
const flashStart = round3(opening.flash.start);
const revealStart = round3(opening.reveal.start);
const bodyStart = round3(opening.bodyStart);
const leadDuration = flashStart;
const flashDuration = round3(opening.flash.duration);
const finalStart = round3(flashStart + flashDuration - 0.03);
const finalHoldDuration = round3(bodyStart - finalStart);
const compositionsDir = path.join(root, "compositions");
fs.mkdirSync(compositionsDir, { recursive: true });

const bgmOutput = path.resolve(root, bgmPathRelative);
fs.mkdirSync(path.dirname(bgmOutput), { recursive: true });
const bgmResult = spawnSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", "-stream_loop", "-1", "-i", bgmSource, "-t", String(totalDuration), "-vn", "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "192k", bgmOutput], { stdio: "inherit" });
if (bgmResult.status !== 0) throw new Error(`Unable to create full-length BGM: ${bgmResult.status}`);

const escapeHtml = (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const renderScenes = storyboard.map((scene, index) => {
  const nextStart = storyboard[index + 1]?.start ?? totalDuration;
  return { ...scene, duration: round3(nextStart - scene.start), end: round3(nextStart) };
});

const chapterGroups = audio.chapters.map((chapterMeta) => {
  const scenes = renderScenes.filter((scene) => scene.chapter === chapterMeta.chapter);
  if (scenes.length === 0) throw new Error(`No storyboard scenes for chapter ${chapterMeta.chapter}`);
  return { id: chapterMeta.id, chapter: chapterMeta.chapter, title: chapterMeta.title, start: chapterMeta.start, end: chapterMeta.end, duration: round3(chapterMeta.duration), scenes };
});

const motionFrom = {
  "zoom-in": `{ scale: 1.0, x: -8, y: 12 }`,
  "zoom-out": `{ scale: 1.13, x: 14, y: -16 }`,
  "pan-left": `{ scale: 1.09, x: 28, y: 4 }`,
  "pan-right": `{ scale: 1.09, x: -28, y: -4 }`,
  hold: `{ scale: 1.01, x: -4, y: 6 }`,
};
const motionTo = (motion, duration) => ({
  "zoom-in": `{ scale: 1.13, x: 14, y: -16, duration: ${duration}, ease: "none" }`,
  "zoom-out": `{ scale: 1.0, x: -8, y: 12, duration: ${duration}, ease: "none" }`,
  "pan-left": `{ scale: 1.11, x: -28, y: -4, duration: ${duration}, ease: "none" }`,
  "pan-right": `{ scale: 1.11, x: 28, y: 4, duration: ${duration}, ease: "none" }`,
  hold: `{ scale: 1.035, x: 4, y: -6, duration: ${duration}, ease: "none" }`,
}[motion] ?? `{ scale: 1.035, x: 4, y: -6, duration: ${duration}, ease: "none" }`);

for (const group of chapterGroups) {
  const phaseIds = group.scenes.map((scene) => `${group.id}-phase-${scene.id}`);
  const phases = group.scenes.map((scene, index) => {
    const title = index === 0 ? `<div id="${group.id}-chapter" class="chapter-overlay"><div class="chapter-kicker">第${toChineseNumber(group.chapter)}幕</div><div class="chapter-title">${escapeHtml(group.title)}</div></div>` : "";
    return `<div id="${phaseIds[index]}" class="phase${index === 0 ? " first-phase" : ""}"><div id="${group.id}-shell-${scene.id}" class="image-shell" data-layout-allow-overflow><img id="${group.id}-img-${scene.id}" src="${scene.asset}" alt="" /></div>${title}</div>`;
  }).join("\n");
  const timeline = [];
  timeline.push(`tl.set("${phaseIds.map((id) => `#${id}`).join(",")}", { opacity: 0 }, 0);`);
  timeline.push(`tl.set("#${phaseIds[0]}", { opacity: 1 }, 0);`);
  group.scenes.forEach((scene, index) => {
    const localStart = round3(scene.start - group.start);
    const duration = round3(scene.duration);
    if (index > 0) {
      timeline.push(`tl.set("#${phaseIds[index - 1]}", { opacity: 0 }, ${localStart});`);
      timeline.push(`tl.set("#${phaseIds[index]}", { opacity: 1 }, ${localStart});`);
    }
    timeline.push(`tl.fromTo("#${group.id}-img-${scene.id}", ${motionFrom[scene.motion] ?? motionFrom.hold}, ${motionTo(scene.motion, duration)}, ${localStart});`);
    timeline.push(`tl.fromTo("#${group.id}-shell-${scene.id}", { opacity: 0.76 }, { opacity: 1, duration: 0.22, ease: "power1.out" }, ${localStart});`);
  });
  timeline.push(`tl.fromTo("#${group.id}-chapter", { opacity: 0, x: -22 }, { opacity: 1, x: 0, duration: 0.42, ease: "power2.out" }, 0.12);`);
  timeline.push(`tl.to("#${group.id}-chapter", { opacity: 0, x: 16, duration: 0.32, ease: "power2.in" }, ${Math.max(0.8, Math.min(3.0, group.scenes[0].duration - 0.4))});`);

  fs.writeFileSync(path.join(compositionsDir, `${group.id}.html`), `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8" /></head><body><template id="${group.id}-template"><style>
  @font-face { font-family: "HBG Sans"; src: local("${chapterStyle.fontFamily}"), local("Hiragino Sans GB"), local("Arial Unicode MS"); font-weight: 100 900; }
  .phase { position:absolute; inset:0; width:${W}px; height:${H}px; overflow:hidden; background:#171312; opacity:0; }
  .first-phase { opacity:1; }
  .image-shell { position:absolute; inset:-3px; width:${W + 6}px; height:${H + 6}px; overflow:hidden; background:#171312; }
  .image-shell img { display:block; width:100%; height:100%; object-fit:cover; transform-origin:50% 50%; }
  .chapter-overlay { position:absolute; left:${chapterStyle.left}px; top:${chapterStyle.top}px; max-width:${chapterStyle.maxWidth}px; padding:18px 24px 22px; border-left:7px solid ${chapterStyle.accentColor}; background:${chapterStyle.backgroundRgba}; opacity:0; font-family:"HBG Sans",sans-serif; }
  .chapter-kicker { color:${chapterStyle.accentColor}; font-size:${chapterStyle.kickerFontSize}px; font-weight:750; letter-spacing:.18em; }
  .chapter-title { margin-top:8px; color:${chapterStyle.textColor}; font-size:${chapterStyle.titleFontSize}px; font-weight:850; line-height:1.2; letter-spacing:.035em; }
</style><div id="${group.id}-root" data-composition-id="${group.id}" data-width="${W}" data-height="${H}" data-duration="${group.duration}" style="position:absolute;inset:0;width:${W}px;height:${H}px;overflow:hidden;background:#171312;">${phases}</div><script>window.__timelines=window.__timelines||{};const tl=gsap.timeline({paused:true});${timeline.join("\n")}window.__timelines["${group.id}"]=tl;</script></template></body></html>`);
}

const slots = chapterGroups.map((group) => `<div id="host-${group.id}" data-composition-id="${group.id}" data-composition-src="compositions/${group.id}.html" data-start="${group.start}" data-duration="${group.duration}" data-track-index="1" data-width="${W}" data-height="${H}"></div>`).join("\n");
const captionData = captions.map((caption) => ({ id: caption.id, start: round3(bodyStart + caption.start), end: round3(bodyStart + caption.end), text: caption.text }));
const flashFrameDuration = flashDuration / flashLives.length;
const flashFrames = flashLives.map((life, index) => `<div id="flash-${index}" class="flash-frame"><img src="${escapeHtml(life.asset)}" alt=""/><div class="flash-label">${escapeHtml(life.label)}</div></div>`).join("\n");
const titleLines = spec.titleLines?.length ? spec.titleLines : [spec.title];
const titleHtml = titleLines.map((line, index) => `<div class="${index === 0 ? "opening-story-primary" : "opening-story-secondary"}">${escapeHtml(line)}</div>`).join("");

const indexHtml = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"/><meta name="viewport" content="width=${W},height=${H}"/><title>${escapeHtml(spec.title)}</title><script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script><style>
@font-face{font-family:"HBG Project Sans";src:local("${captionStyle.fontFamily}"),local("Hiragino Sans GB"),local("Arial Unicode MS");font-weight:100 900}
*{box-sizing:border-box}html,body{margin:0;width:${W}px;height:${H}px;overflow:hidden;background:#171312}#root{position:relative;width:${W}px;height:${H}px;overflow:hidden}.ground{position:absolute;inset:0;background:#171312}.opening-clip{position:absolute;inset:0;width:${W}px;height:${H}px;overflow:hidden;z-index:10}
#opening-lead{display:grid;place-items:center;background:radial-gradient(circle at 50% 42%,#493a32 0%,#211713 54%,#100c0b 100%)}#opening-lead:before{content:"";position:absolute;inset:0;opacity:.22;background-image:repeating-linear-gradient(0deg,transparent 0 5px,rgba(255,255,255,.06) 6px)}#opening-lead-copy{position:relative;width:${openingLayout.leadWidth}px;color:#fff8ef;font:850 ${openingLayout.leadFontSize}px/1.22 "HBG Project Sans",sans-serif;letter-spacing:.06em;text-align:center;text-shadow:0 6px 28px rgba(0,0,0,.85)}#opening-lead-copy:after{content:"";display:block;width:150px;height:7px;margin:28px auto 0;background:#d8000f}
#opening-flash{background:#110c0b}.flash-frame{position:absolute;inset:0;opacity:0;background:#110c0b;overflow:hidden}.flash-frame img{display:block;width:100%;height:100%;object-fit:cover;transform-origin:50% 48%;filter:saturate(.86) contrast(1.05)}.flash-frame:after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(10,6,4,.1) 0%,rgba(10,6,4,.08) 42%,rgba(10,6,4,.72) 100%)}.flash-label{position:absolute;left:50%;top:54%;width:${openingLayout.flashLabelWidth}px;transform:translate(-50%,-50%);z-index:2;padding:22px 20px;border-top:5px solid #d8000f;border-bottom:5px solid #d8000f;background:rgba(19,12,10,.88);color:#fff;font:900 ${openingLayout.flashLabelFontSize}px/1.25 "HBG Project Sans",sans-serif;letter-spacing:.025em;text-align:center;text-shadow:0 4px 18px #000}
#opening-final img{display:block;width:100%;height:100%;object-fit:cover;transform-origin:50% 50%}#opening-final:after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(12,8,7,.05) 0%,rgba(12,8,7,.08) 42%,rgba(12,8,7,.68) 100%)}#opening-story-title{position:absolute;left:50%;top:${openingLayout.titleTopPercent}%;width:${openingLayout.titleCardWidth}px;transform:translate(-50%,-50%);z-index:2}#opening-story-card{padding:28px 30px 32px;border:5px solid #d8000f;background:rgba(20,12,10,.9);box-shadow:0 20px 52px rgba(0,0,0,.62);text-align:center}.opening-story-primary{color:#fff9f1;font:900 ${openingLayout.primaryTitleFontSize}px/1.16 "HBG Project Sans",sans-serif;letter-spacing:.045em}.opening-story-secondary{margin-top:12px;color:#f3c7bd;font:850 ${openingLayout.secondaryTitleFontSize}px/1.18 "HBG Project Sans",sans-serif;letter-spacing:.035em}
#root>div[data-composition-src]{position:absolute;inset:0;width:${W}px;height:${H}px;z-index:1}#caption-layer{position:absolute;inset:0;width:${W}px;height:${H}px;z-index:40}.caption-item{position:absolute;left:50%;bottom:${captionStyle.bottom}px;max-width:${captionStyle.maxWidth}px;padding:${captionStyle.htmlPadding};border:1px solid rgba(255,255,255,.14);border-radius:${captionStyle.borderRadius}px;background:${captionStyle.backgroundRgba};color:${captionStyle.textColor};font:${captionStyle.fontWeight} ${captionStyle.fontSize}px/${captionStyle.lineHeight} "HBG Project Sans",sans-serif;letter-spacing:${captionStyle.letterSpacingEm}em;text-align:center;text-shadow:0 3px 12px rgba(0,0,0,.9);box-shadow:0 7px 22px rgba(0,0,0,.3);opacity:0;transform:translateX(-50%);white-space:nowrap}
</style></head><body><div id="root" data-composition-id="main" data-start="0" data-width="${W}" data-height="${H}" data-duration="${totalDuration}"><div class="ground"></div>
<div id="opening-lead" class="clip opening-clip" data-start="0" data-duration="${leadDuration}" data-track-index="0"><div id="opening-lead-copy">${escapeHtml(spec.opening.leadDisplayText ?? "今天体验的人生副本是……")}</div></div>
<div id="opening-flash" class="clip opening-clip" data-start="${flashStart}" data-duration="${flashDuration}" data-track-index="0">${flashFrames}</div>
<div id="opening-final" class="clip opening-clip" data-start="${finalStart}" data-duration="${finalHoldDuration}" data-track-index="1"><img id="opening-final-image" src="${escapeHtml(finalImageRelative)}" alt=""/><div id="opening-story-title"><div id="opening-story-card">${titleHtml}</div></div></div>
${slots}<div id="caption-layer" class="clip" data-start="${bodyStart}" data-duration="${narrationDuration}" data-track-index="20" data-layout-allow-caption-zone></div>
<audio id="audio-opening-lead" src="${opening.lead.path}" data-start="${leadStart}" data-duration="${round3(opening.lead.duration)}" data-track-index="10" data-volume="1"></audio><audio id="audio-bgm" src="${bgmPathRelative}" data-start="0" data-duration="${totalDuration}" data-track-index="9" data-volume="${fullBgmVolume}"></audio><audio id="audio-opening-ratchet" src="${opening.flash.audio}" data-start="${flashStart}" data-duration="${flashDuration}" data-track-index="11" data-volume="1"></audio><audio id="audio-opening-reveal" src="${opening.reveal.path}" data-start="${revealStart}" data-duration="${round3(opening.reveal.duration)}" data-track-index="12" data-volume="1"></audio><audio id="audio-story-narration" src="${narrationPathRelative}" data-start="${bodyStart}" data-duration="${narrationDuration}" data-track-index="13" data-volume="1"></audio>
</div><script>window.__timelines=window.__timelines||{};const captionData=${JSON.stringify(captionData)};const captionLayer=document.getElementById("caption-layer");for(const c of captionData){const e=document.createElement("div");e.id=c.id;e.className="caption-item";e.textContent=c.text;captionLayer.appendChild(e)}const mainTl=gsap.timeline({paused:true});mainTl.fromTo("#opening-lead-copy",{opacity:0,scale:.96},{opacity:1,scale:1,duration:.4,ease:"power2.out"},.04);mainTl.set(".flash-frame",{opacity:0},0);${flashLives.map((_, index) => { const at = round3(flashStart + index * flashFrameDuration); const zoomIn = index % 2 === 0; return `mainTl.set("#flash-${index}",{opacity:1},${at});${index > 0 ? `mainTl.set("#flash-${index - 1}",{opacity:0},${at});` : ""}mainTl.fromTo("#flash-${index} img",{scale:${zoomIn ? openingStyle.flashScaleMin : openingStyle.flashScaleMax},x:${zoomIn ? -10 : 12},y:${zoomIn ? 8 : -10}},{scale:${zoomIn ? openingStyle.flashScaleMax : openingStyle.flashScaleMin},x:${zoomIn ? 12 : -10},y:${zoomIn ? -10 : 8},duration:${round3(flashFrameDuration)},ease:"none"},${at});`; }).join("")}mainTl.fromTo("#opening-final-image",{scale:${openingStyle.selectedScaleStart},x:-10,y:10},{scale:${openingStyle.selectedScaleEnd},x:12,y:-14,duration:${finalHoldDuration},ease:"none"},${finalStart});mainTl.fromTo("#opening-story-card",{opacity:0,scale:.96},{opacity:1,scale:1,duration:.24,ease:"power2.out"},${round3(finalStart + .04)});mainTl.set(".caption-item",{opacity:0},0);for(const c of captionData){mainTl.set("#"+c.id,{opacity:1},c.start);mainTl.set("#"+c.id,{opacity:0},c.end)}window.__timelines.main=mainTl;</script></body></html>`;

fs.writeFileSync(path.join(root, "index.html"), indexHtml);
const qaDir = path.join(root, "qa");
fs.mkdirSync(qaDir, { recursive: true });
const qaHtml = indexHtml.replace("<head><meta", "<head><base href=\"/\"><meta");
fs.writeFileSync(path.join(qaDir, "opening-bgm-preview.html"), qaHtml.replace(`data-duration="${totalDuration}"`, `data-duration="${style.opening.previewDuration ?? 20}"`));
fs.writeFileSync(path.join(qaDir, "review-excerpt.html"), qaHtml.replace(`data-duration="${totalDuration}"`, `data-duration="52"`));
console.log(JSON.stringify({ title: spec.title, width: W, height: H, totalDuration, narrationDuration, scenes: renderScenes.length, chapters: chapterGroups.length, flashLives: flashLives.length, openingBgmVolume: fullBgmVolume }, null, 2));
