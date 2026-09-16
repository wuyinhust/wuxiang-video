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
  → 配音（真人实录 / 火山引擎 TTS 二选一）
  → 词级对齐字幕（原稿文本 + 真实语音时间，绝不用 ASR 原文）
  → Remotion 成片（1080×1920 竖屏）
  → 验收交付
```

## 触发方式

在支持 Skill 的 AI Agent（如 WorkBuddy / Claude Code）中安装后，直接说：

- "用小无相功流程做一条 XX 主题的视频"
- "复刻这条参考视频，主题换成 XX"
- "拆解这条爆款并仿拍一条"

**首次使用**会先跑安装预检，并向你一次性索取所需的凭据（见下）。

## 安装后第一件事：跑预检、把凭据一次要齐

**新机器装完 skill 后，先跑预检再开工。** 预检是只读的——不安装任何软件、不写入任何文件：

```bash
python3 scripts/preflight.py --project-root .            # 两条路线都按必需校验
python3 scripts/preflight.py --project-root . --route A  # 已定真人实录：不要求 TTS 凭据
python3 scripts/preflight.py --project-root . --route B  # 已定火山 TTS：凭据缺失即失败
python3 scripts/preflight.py --project-root . --json     # 机器可读
```

它盘四类前置：运行时（Python / Node / npm / git / faster-whisper / Chrome）、媒体工具链
（复用乾坤大挪移的 `check_environment.py`）、依赖仓库、网络可达性；再按路线校验凭据。
未通过时会把「需要用户提供的凭据」单独列出来，便于一次性索取。

### 凭据清单

| 凭据 | 环境变量 | 何时必需 | 从哪里拿 |
|---|---|---|---|
| 火山引擎 AppID | `VOLC_TTS_APPID` | 路线 B | 火山引擎控制台 → 语音技术 → 语音合成 → 应用管理 |
| 火山引擎 Access Token | `VOLC_TTS_ACCESS_TOKEN` | 路线 B | 同上（与 AppID 同页） |
| 火山引擎音色 voice_type | `VOLC_TTS_VOICE` | 路线 B | 控制台音色列表；**须已下单/授权** |
| 业务集群 | `VOLC_TTS_CLUSTER` | 一般不用 | 默认 `volcano_tts` |
| GitHub 凭据 | `GITHUB_TOKEN` | 仅私有仓库 / 发布产物 | classic PAT（`repo` scope） |

取值优先级：命令行参数 > `--config creds.json` > 环境变量。模板见 `scripts/creds.example.json`。
**凭据不入源码、不入仓库、不进报告**——`.gitignore` 已预置 `creds.json` / `*.env`。

## 仓库结构

```
SKILL.md                      技能主文档（预检 + 六段流水线 + 决策点 + 避坑清单）
.gitignore                    预置凭据与生成物忽略规则
scripts/
├── preflight.py              安装预检：凭据 / 运行时 / 媒体工具链 / 依赖仓库 / 网络（只读）
├── creds.example.json        凭据模板（复制为 creds.json 并填入）
├── align_subtitles.py        字幕对齐器：原稿 + faster-whisper 词级时间戳 → SRT
├── volcano_tts_batch.py      火山引擎 TTS 批量配音（路线 B）+ 主音轨拼接 + 音量归一
└── render_remotion.sh        Remotion 一键渲染（系统 Chrome，免 Headless 下载）
```

## 依赖

- [qiankun-video-shift](https://github.com/wuyinhust/qiankun-video-shift)（拆解前三式）
- [video-talkcraft](https://github.com/Vincentwei1021/video-talkcraft)（Remotion 剪辑）
- Python 3 + ffmpeg/ffprobe + Node 22+
- faster-whisper（词级转写）
- 路线 B 另需火山引擎语音合成凭据（AppID / Access Token / 音色），纯 HTTPS 调用、无额外 pip 依赖

## 配音路线（二选一，不混用）

| | 路线 A · 真人实录 | 路线 B · 火山引擎 TTS |
|---|---|---|
| 适用 | 首选，效果天花板 | 无真人出镜时的机器路线 |
| 前置 | 带时间戳逐字稿 | AppID / Access Token / 音色 voice_type |
| 命令 | 用户录制 → ffmpeg 提取音频 | `volcano_tts_batch.py --check` 先自检 |

路线 B 完整流程：

```bash
export VOLC_TTS_APPID=...
export VOLC_TTS_ACCESS_TOKEN=...
export VOLC_TTS_VOICE=zh_male_M392_conversation_wvae_bigtts

python3 scripts/volcano_tts_batch.py --check          # 冒烟自检：一次验证三项凭据

python3 scripts/volcano_tts_batch.py segments.json --out tts/output \
    --speed 1.15 --master tts/output/master.mp3 --normalize
```

凭据只走环境变量或 `--config creds.json`，不写入源码与仓库。

## 核心设计

1. **先预检再开工**：新机器装完先跑 `preflight.py`，凭据一次要齐——走到阶段 3 才发现没有
   配音凭据，是最大的返工来源。
2. **第〇步先锁决策**：配音路线（真人实录 / 火山引擎 TTS，**二选一，不混用**）与合规边界
   （录屏界面与参考片真人肖像不进成片）开工前定死。
3. **自动切镜不可信**：每 2 秒补抽帧，按叙事人工细分 8–10 段。
4. **字幕铁律**：字幕 = 原稿文本 + 真实语音词级时间，绝不直接用 ASR 原文
   （生造词必被转写错）。
5. **真实原则**：演示素材用本项目真实拆解产物，不虚构功能、数据、评价。
6. **凭据纪律**：凭据只走环境变量 / `--config`，不入源码、不入仓库、不进报告。

## 基准

38s 参考视频 → 77s 成片，约 2.5–4 小时；详见 SKILL.md。

## License

MIT
