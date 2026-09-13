# 小无相功 · xiaowuxianggong

> 逍遥派小无相功，无形无相，催动天下武学。
> 在视频创作里：以「乾坤大挪移」拆解参考视频的内力（叙事结构、节奏、形式语言），
> 以小无相功催动之，复刻重生为全新主题的成片。

**小无相功**是一个 AI Agent Skill（工作技能包），构建于
[qiankun-video-shift（乾坤大挪移）](https://github.com/wuyinhust/qiankun-video-shift)
的拆解能力之上，覆盖从参考视频到成片交付的完整流水线：

```
拆解参考视频（乾坤大挪移前三式）
  → 原创同构稿件
  → 配音（真人实录 / edge-tts / 云端 IndexTTS 三路线）
  → 词级对齐字幕（原稿文本 + 真实语音时间，绝不用 ASR 原文）
  → Remotion 成片（1080×1920 竖屏）
  → 验收交付
```

## 触发方式

在支持 Skill 的 AI Agent（如 WorkBuddy / Claude Code）中安装后，直接说：

- "用小无相功流程做一条 XX 主题的视频"
- "复刻这条参考视频，主题换成 XX"
- "拆解这条爆款并仿拍一条"

## 仓库结构

```
SKILL.md                      技能主文档（六段流水线 + 决策点 + 避坑清单）
scripts/
├── align_subtitles.py        字幕对齐器：原稿 + faster-whisper 词级时间戳 → SRT
├── edgetts_batch.py          edge-tts 批量配音（路线 B）+ 生成日志
└── render_remotion.sh        Remotion 一键渲染（系统 Chrome，免 Headless 下载）
```

## 依赖

- [qiankun-video-shift](https://github.com/wuyinhust/qiankun-video-shift)（拆解前三式）
- [video-talkcraft](https://github.com/Vincentwei1021/video-talkcraft)（Remotion 剪辑）
- Python 3 + ffmpeg/ffprobe + Node 22+
- faster-whisper（词级转写）；路线 B 另需 edge-tts

## 核心设计

1. **第〇步先锁决策**：配音路线（真人实录 / edge-tts / IndexTTS）与合规边界
   （录屏界面与参考片真人肖像不进成片）开工前定死——最大的返工来源。
2. **自动切镜不可信**：每 2 秒补抽帧，按叙事人工细分 8–10 段。
3. **字幕铁律**：字幕 = 原稿文本 + 真实语音词级时间，绝不直接用 ASR 原文
   （生造词必被转写错）。
4. **真实原则**：演示素材用本项目真实拆解产物，不虚构功能、数据、评价。

## 基准

38s 参考视频 → 77s 成片，约 2.5–4 小时；详见 SKILL.md。

## License

MIT
