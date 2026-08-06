# Active Dependencies

| 能力 | 依赖 | 阶段 |
|---|---|---|
| 内容合同与门禁 | Python >= 3.11 | planning |
| 图片机器诊断 | Pillow | local-render |
| HBG 脚本 | Node.js | local-render |
| 媒体探测与渲染 | FFmpeg + ffprobe | local-render |
| 连续旁白与 VTT | `edge-tts` 命令或 `edge_tts` Python 模块 | production |
| 图片生成 | 宿主 ImageGen 工具 | production |

HBG 上游代码保存在 `vendor/hbg-life-simulation`，由 `UPSTREAM_LOCK.json` 验证。音频、BGM、生成图片、参考资料和凭据必须保存在项目工作区，不得提交到 Skill Runtime。
