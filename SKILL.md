---
name: xiaowuxianggong
description: 小无相功——基于乾坤大挪移（qiankun-video-shift）的视频复刻制作流水线。拆解参考视频的形式语言 → 原创同构稿件 → 配音（真人实录 / 火山引擎 TTS，二选一）→ 词级对齐字幕 → Remotion 成片 → 验收交付。当用户说"用小无相功流程做 XX 视频"、"复刻/模仿一条参考视频"、"拆解爆款视频并仿拍一条"时触发。
metadata:
  agent_created: true
---

# 小无相功 · 视频复刻制作流水线

以上乘内力催动乾坤大挪移：乾坤大挪移负责"拆解与还原"，小无相功负责"模仿与重生"——把参考视频的叙事结构、节奏、形式语言迁移到全新主题的成片。

**依赖**：乾坤大挪移仓库（`git clone https://github.com/wuyinhust/qiankun-video-shift.git tools/qiankun-video-shift`，按其 SKILL.md 执行前三式）、video-talkcraft（Remotion 剪辑，`git clone https://github.com/Vincentwei1021/video-talkcraft.git tools/video-talkcraft`）、Python 3 + ffmpeg/ffprobe + Node 22+、faster-whisper（词级转写）。路线 B 只需火山引擎语音合成的三项凭据，纯 HTTPS 调用、无额外 pip 依赖。

## 安装后第一件事：跑预检、把凭据一次要齐（新机器必做）

**在开工之前先执行预检；凭据没齐，不要进入第〇步。**

```bash
python3 scripts/preflight.py --project-root .            # 两条路线都按必需校验
python3 scripts/preflight.py --project-root . --route A  # 已定真人实录：不要求 TTS 凭据
python3 scripts/preflight.py --project-root . --route B  # 已定火山 TTS：凭据缺失即失败
python3 scripts/preflight.py --project-root . --json     # 机器可读（便于程序化判断）
```

预检盘四类前置（**只读**，不安装任何东西、不写入任何文件）：

| 类别 | 内容 |
|---|---|
| 运行时 | Python ≥3.10、Node ≥22、npm、git、faster-whisper、系统 Chrome |
| 媒体工具链 | 复用乾坤大挪移 `scripts/check_environment.py --json`（ffmpeg/ffprobe/yt-dlp） |
| 依赖仓库 | `tools/qiankun-video-shift`、`tools/video-talkcraft` 是否就位及 SHA |
| 网络 | github.com、hf-mirror.com、（路线 B）openspeech.bytedance.com |

### 凭据清单（一次性索取，禁止在流水线中途要）

| 凭据 | 环境变量 | 何时必需 | 从哪里拿 |
|---|---|---|---|
| 火山引擎 AppID | `VOLC_TTS_APPID` | 路线 B | 火山引擎控制台 → 语音技术 → 语音合成 → 应用管理 |
| 火山引擎 Access Token | `VOLC_TTS_ACCESS_TOKEN` | 路线 B | 同上（与 AppID 同页） |
| 火山引擎音色 voice_type | `VOLC_TTS_VOICE` | 路线 B | 控制台音色列表；**须已下单/授权**（免费音色也要 0 元下单） |
| 业务集群 | `VOLC_TTS_CLUSTER` | 一般不用 | 默认 `volcano_tts` |
| GitHub 凭据 | `GITHUB_TOKEN` | 仅私有仓库 clone / 发布产物 | classic PAT（`repo` scope） |

取值优先级：命令行参数 > `--config creds.json` > 环境变量。模板见 `scripts/creds.example.json`。

**凭据纪律**：不入源码、不入仓库、不写进报告与日志；对外只出现脱敏形式（预检默认已脱敏）。

**预检未通过时怎么办**：
1. 若缺的是**凭据** → 把预检末尾「需要用户提供的内容」整段发给用户，一次要齐
2. 若缺的是**软件/仓库/网络** → 说明缺什么、影响哪一阶段，**取得用户同意后**再装（不静默安装系统软件）
3. 若缺的是 **ffmpeg/ffprobe**，或预检报「媒体二进制来源稳固」未过 → 跑 `bash scripts/install_ffmpeg.sh`
   装一份独立的（自带 SHA-256 校验，免 sudo、免 Homebrew，默认落 `~/.local/bin`）。
   **不要**让流水线长期依赖其他 skill 的 node_modules 捆绑件——那个 skill 一卸载，
   抽帧与分镜会连带失效，而报错不会指向真正原因
3. 全绿后再向用户确认配音路线与合规边界，进入第〇步

## 第〇步：开工前必须锁定的两个决策（最重要，跳过必返工）

1. **配音路线（二选一，先问用户）**——一条片子只走一条路线，不混用；**不使用 edge-tts / IndexTTS**：
   - **A. 真人实录**（首选，效果天花板）：按"带时间戳逐字稿"录视频；音画同源，可做圆形画中画 + 结尾全屏真人
   - **B. 火山引擎 TTS**（无真人时的机器路线）：需用户提供 **AppID / Access Token / 音色 voice_type** 三项；凭据走环境变量或 `--config`，**不入源码、不入仓库**
2. **合规边界**：录屏界面与参考片真人肖像**不进成片**；演示段用真实拆解数据重绘。此条在拆解阶段定调。

## 六段流水线

### 阶段 1：拆解（乾坤大挪移前三式）
1. `acquire_video.py` 登记来源 → 均匀抽 6–12 帧确认录屏界面覆盖区（状态栏/导航/互动条），记录像素边界
2. `segment_video.py` 自动分镜 + 提取 audio.wav；**自动切镜不可信**，必须每 2 秒补抽帧、按叙事人工细分为 8–10 段
3. faster-whisper（small/int8，词级时间戳）转写 audio.wav，与画面字幕互证，回写 analysis.json 段边界与口播
4. 产出 analysis.json + report.md（结构模板：钩子公式/信息组织/呈现方式），`validate_analysis.py --check-files` 必须通过

### 阶段 2：原创稿件
沿用参考叙事顺序与表达形态，替换为新主题**真实**内容（演示素材优先用本项目真实拆解产物，不虚构）。交付：口播稿（仅朗读内容）+ 段落对应表 + 每段画面/屏幕文字/强调/停顿建议。信息密度对标参考（约 4–4.5 字/秒）。

### 阶段 3：配音（按第〇步决策执行，二选一）
- **路线 A（真人实录）**：
  1. 生成带时间戳逐字稿（分段起止 + 连读版）交用户录制
  2. 收到实录后 `ffmpeg -i 实录.mp4 -vn -ac 1 -ar 24000 voice.wav` 提取音频
  3. 音频到手后进阶段 4 做转写回检与对齐；用户即兴加的内容编为新场景
- **路线 B（火山引擎 TTS）**：
  1. **先冒烟自检**：`python3 scripts/volcano_tts_batch.py --check` —— 用一句话一次验证 appid / access_token / 音色 三者是否都对，避免批量跑到一半才失败
  2. 逐段合成 + 拼主音轨 + 归一（一条命令）：
     `python3 scripts/volcano_tts_batch.py segments.json --out tts/output --speed 1.15 --master tts/output/master.mp3 --normalize`
  3. 凭据走 `VOLC_TTS_APPID` / `VOLC_TTS_ACCESS_TOKEN` / `VOLC_TTS_VOICE`（或 `--config creds.json`）
- 路线无关的统一产出：分段音频 + 主音轨 + `generation_log.json`（参数、实测时长、X-Tt-Logid）；音量归一目标 mean ≈ -25dB、峰值 < -3dB

### 阶段 4：对齐与字幕（铁律：字幕 = 原稿文本 + 真实语音词级时间，绝不用 ASR 原文）
1. 对真实配音转写回检：与原稿比对字数，确认无漏字/重复（ASR 对生造词的同音误识不算错）
2. `scripts/align_subtitles.py --transcript transcript.json --script script.txt --out-srt subtitles.srt --out-timeline timeline.json`（difflib 字符对齐 → 词级时间映射回原稿 → 按原稿标点切句，不按字数均分）
3. 真人实录路线：utterance 与原稿短语一一映射定场景窗口；用户自发加的内容编为新场景

### 阶段 5：Remotion 成片
1. 按 video-talkcraft 当前文档初始化（不臆造命令）；1080×1920@30fps；package.json 锁版本
2. 场景按参考形式语言重绘；字幕固定中下部安全区、大字号、随场景明暗切换
3. 真人画中画：圆形（右下、白边、避开字幕区）；结尾全屏段 `startFrom` 用**绝对起始帧常量**（禁用逐帧相对值）
4. 渲染脚本化：`scripts/render_remotion.sh`（内置 cd + 系统 Chrome）；先渲代表片段自检，再渲全片

### 阶段 6：验收交付
逐项：录屏界面零残留｜形式/节奏逐段可对应参考｜无虚构｜volumedetect 无削波无静音｜字幕与口播一致｜每场景至少抽 1 帧验收。
交付：成片 MP4｜拆解报告 + analysis.json｜稿件 + 对应表｜配音音频｜SRT + timeline｜Remotion 工程｜REPRODUCE.md（仓库 SHA + 依赖版本 + 步骤）。

## 避坑清单（按收益排序）

1. **新机器先跑 `preflight.py`，凭据一次要齐**——走到阶段 3 才发现没有配音凭据 = 最大返工来源
2. 配音路线与合规边界开工前定死
3. 火山 TTS 鉴权头必须是 `Authorization: Bearer;<token>`（**分号**，不是空格），写成 `Bearer <token>` 直接 401
4. 火山 TTS 先 `--check` 再批量——一次验证 appid/token/音色；鉴权与音色问题是**不可重试**错误，脚本会快速退出并给建议
5. 火山 TTS 单段文本上限 1024 字节（建议 <300 字），按叙事段切分天然满足；"豆包语音合成模型2.0"音色（`*_uranus_bigtts`）需 v3 接口，v1 不支持
6. **ffmpeg/ffprobe 的来源要稳固**：预检会提示是否"借用其他 skill 的 node_modules 捆绑件"——是的话跑 `bash scripts/install_ffmpeg.sh` 装一份独立的（macOS 原生 arm64 静态构建、SHA-256 校验、免 sudo/Homebrew），否则那个 skill 一卸载，抽帧与分镜连带静默失效。
7. Remotion 渲染用系统 Chrome（`--browser-executable`），跳过 ~130MB Headless 下载
8. 长命令脚本化：后台任务 shell 不保留 cwd，cd 必须写在脚本内
9. HuggingFace 被代理拦截：`HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1`
10. pip 装 sdist 包报 EEXIST：`env -u PYTHONPATH pip install`
11. GitHub 慢/断流：`git -c http.version=HTTP/1.1 clone --depth 1 --single-branch`，codeload tarball 兜底
12. zsh：`rm -f` 匹配不到的 glob 会中断整条命令链
13. npm/pip/模型下载全部后台并行，等待期做分析、写稿
14. 抽帧成本控制：先低分辨率筛、关键段再全尺寸；验收抽帧每场景 1 帧即可

## 基准

38s 参考视频 → 77s 成片：首做约 4 小时 / 40–60 万 tokens（按本清单执行）；拆解 45min、稿件 30min、配音 5–10min（火山 TTS；真人实录按录制时长另计）、对齐 20min、Remotion 90min、验收 15min。
