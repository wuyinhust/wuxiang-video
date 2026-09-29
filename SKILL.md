---
name: xiaowuxianggong
description: 小无相功——基于乾坤大挪移的视频复刻制作流水线。拆解参考视频形式语言 → 原创同构稿件 → 配音（真人实录 / 火山引擎 TTS 二选一）→ 词级字幕与时间线 → 可选火山 OmniHuman 数字人 → Remotion 成片 → 音效后期与验收交付。当用户说"用小无相功流程做 XX 视频"、"复刻/模仿一条参考视频"、"拆解爆款视频并仿拍一条"时触发。
metadata:
  agent_created: true
---

# 小无相功 · 视频复刻制作流水线

以上乘内力催动乾坤大挪移：乾坤大挪移负责"拆解与还原"，小无相功负责"模仿与重生"——把参考视频的叙事结构、节奏、形式语言迁移到全新主题的成片。

**依赖**：乾坤大挪移仓库（`git clone https://github.com/wuyinhust/qiankun-video-shift.git tools/qiankun-video-shift`，按其 SKILL.md 执行前三式）、video-talkcraft（Remotion 剪辑，`git clone https://github.com/Vincentwei1021/video-talkcraft.git tools/video-talkcraft`）、Python 3 + ffmpeg/ffprobe + Node 22+、faster-whisper（词级转写）。路线 B 只需火山引擎语音合成的三项凭据，纯 HTTPS 调用、无额外 pip 依赖。可选数字人需要火山视觉服务 AK/SK；本地肖像/音频上传另需用户自己的 TOS 桶和 TOS Python SDK。

## 安装后第一件事：跑预检、把凭据一次要齐（新机器必做）

**在开工之前先执行预检；凭据没齐，不要进入第〇步。**

```bash
python3 scripts/preflight.py --project-root .            # 两条路线都按必需校验
python3 scripts/preflight.py --project-root . --route A  # 已定真人实录：不要求 TTS 凭据
python3 scripts/preflight.py --project-root . --route B  # 已定火山 TTS：凭据缺失即失败
python3 scripts/preflight.py --project-root . --route B --avatar  # 另检查数字人 AK/SK 与 API 网络
python3 scripts/preflight.py --project-root . --json     # 机器可读（便于程序化判断）
```

预检盘四类前置（**只读**，不安装任何东西、不写入任何文件）：

| 类别 | 内容 |
|---|---|
| 运行时 | Python ≥3.10、Node ≥22、npm、git、faster-whisper、系统 Chrome |
| 媒体工具链 | 复用乾坤大挪移 `scripts/check_environment.py --json`（ffmpeg/ffprobe/yt-dlp） |
| 依赖仓库 | `tools/qiankun-video-shift`、`tools/video-talkcraft` 是否就位及 SHA |
| 网络 | github.com、hf-mirror.com、（路线 B）openspeech.bytedance.com、（--avatar）visual.volcengineapi.com |

### 凭据清单（一次性索取，禁止在流水线中途要）

| 凭据 | 环境变量 | 何时必需 | 从哪里拿 |
|---|---|---|---|
| 火山引擎 AppID | `VOLC_TTS_APPID` | 路线 B | 火山引擎控制台 → 语音技术 → 语音合成 → 应用管理 |
| 火山引擎 Access Token | `VOLC_TTS_ACCESS_TOKEN` | 路线 B | 同上（与 AppID 同页） |
| 火山引擎音色 voice_type | `VOLC_TTS_VOICE` | 路线 B | 控制台音色列表；**须已下单/授权**（免费音色也要 0 元下单） |
| 业务集群 | `VOLC_TTS_CLUSTER` | 一般不用 | 默认 `volcano_tts` |
| 火山视觉 API AK/SK | `VOLC_ACCESSKEY` / `VOLC_SECRETKEY` | 启用数字人时 | 火山引擎控制台 → 访问控制 → 访问密钥；须有 CV 调用权限 |
| TOS 桶 | `VOLC_TOS_BUCKET` / `VOLC_TOS_REGION` / `VOLC_TOS_ENDPOINT` | 本地图片/音频自动上传时 | 单独开通并创建自有 TOS 桶；语音合成不附带桶 |
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

### 流水线用哪个 Python 解释器（别用裸 `python3`）

预检报告头会给出「流水线解释器」，**后续所有 Python 脚本都用它跑**：

```
流水线解释器：/Users/…/python/envs/default/bin/python
```

原因：faster-whisper 等依赖装在隔离 venv 里，而 PATH 中 `python3` 往往先解析到
**没有依赖的那个**解释器。直接 `python3 scripts/align_subtitles.py` 会 `ImportError`，
跑预检也会误报"缺 faster-whisper"（依赖明明装着）。

预检因此不假定当前解释器，而是按 当前解释器 → `envs/default` → `.venv` → `venv` → `env`
的顺序找一个**实跑 `import faster_whisper` 成功**的解释器（只看目录存在不够，
目录在而依赖没装很常见）。
3. 全绿后再向用户确认声音路线、是否生成数字人、肖像授权和证据边界，进入第〇步

## 第〇步：开工前必须锁定的两个决策（最重要，跳过必返工）

1. **声音来源（二选一，先问用户）**——一条片子只用一条声音路线，不混用；**不使用 edge-tts / IndexTTS**：
   - **A. 真人实录**：提供真实配音/口播源片，保留其原始词级时间线
   - **B. 火山引擎 TTS**：需 **AppID / Access Token / 音色 voice_type**；凭据走环境变量或 `--config`，不入源码与仓库
2. **出镜方式单独决策**：不出镜、授权真人实拍，或用有使用授权的肖像 + 最终配音生成火山数字人。数字人可以驱动 A 或 B 的配音，不替代 Remotion。
3. **合规与证据边界**：参考片只迁移结构和形式语言；参考片真人肖像不进成片，除非用户明确提供使用授权。真实产品/网页/数据须用可核验素材，不能伪造界面或证据；敏感信息先打码。上传至 TOS 或数字人服务的图片/音频须经用户授权。

## 六段流水线

### 阶段 1：拆解（乾坤大挪移前三式）
1. `acquire_video.py` 登记来源 → 均匀抽 6–12 帧确认录屏界面覆盖区（状态栏/导航/互动条），记录像素边界
2. `segment_video.py` 自动分镜 + 提取 audio.wav；**自动切镜不可信**，必须每 2 秒补抽帧、按叙事人工细分为 8–10 段
3. faster-whisper（small/int8，词级时间戳）转写 audio.wav，与画面字幕互证，回写 analysis.json 段边界与口播
4. 产出 analysis.json + report.md（结构模板：钩子公式/信息组织/呈现方式），`validate_analysis.py --check-files` 必须通过
5. 登记参考素材来源与采集日期；参考只用于结构分析，不复制原片人脸、界面或带版权的音视频；凡含真实产品/网页/数据的口播句，记录实际证据来源。截图/录屏入片前检查账号、邮箱、电话、令牌与客户信息并打码；不确定是否可公开的内容不上传。

### 阶段 2：原创稿件
沿用参考叙事顺序与表达形态，替换为新主题**真实**内容（演示素材优先用本项目真实拆解产物，不虚构）。每个事实、数字、评价或产品能力都须能回到真实来源；证据不足时删去或明确标成观点。交付：口播稿（仅朗读内容）+ 段落对应表 + 每段画面/屏幕文字/强调/停顿建议。信息密度对标参考（约 4–4.5 字/秒）。

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

3. **可选数字人**：词级时间线确认后再生成，确保驱动音频与最终字幕来自同一条配音。接口：火山 OmniHuman 1.5；每次传入音频必须严格短于 35 秒。短音频可直接传公开 HTTPS URL；本地文件走独立 TOS。
   - 安装本地 TOS 上传依赖：python3 -m pip install -r requirements-digital-human.txt
   - 配置 VOLC_ACCESSKEY、VOLC_SECRETKEY、VOLC_TOS_BUCKET、VOLC_TOS_REGION、VOLC_TOS_ENDPOINT
   - 生成命令：python3 scripts/volcano_digital_human.py --portrait assets/avatar.png --audio tts/output/master_norm.mp3 --timeline timeline.json --out out/digital-human --resolution 1080
   - 长音频由脚本按时间线句界切为默认不超过 30 秒的段；超过时长的单句先拆稿重录/重合成。生成 avatar-*.mp4 与 avatar_manifest.json，Remotion 按清单时间放置片段。人物画面轨可用数字人片段，声音轨仍用唯一一条最终配音，避免双音轨。
   - 公网模式：--image-url https://... --audio-url https://... --duration-s 20；脚本无法切分远端长音频。数字人服务和 TOS 会产生各自费用，只有选用该功能时才调用接口。

3. 真人实录路线：utterance 与原稿短语一一映射定场景窗口；用户自发加的内容编为新场景

### 阶段 5：Remotion 成片
1. 按 video-talkcraft 当前文档初始化（不臆造命令）；1080×1920@30fps；package.json 锁版本
2. Remotion 是唯一主合成和终片渲染器；数字人片段只是可选视频素材，放进既有场景时间轴，不引入 HyperFrames 或剪映工程链路
2. 场景按参考形式语言重绘；字幕固定中下部安全区、大字号、随场景明暗切换
3. 真人画中画：圆形（右下、白边、避开字幕区）；结尾全屏段 `startFrom` 用**绝对起始帧常量**（禁用逐帧相对值）
4. 渲染脚本化：`scripts/render_remotion.sh`（内置 cd + 系统 Chrome）；先渲代表片段自检，再渲全片

### 阶段 6：验收交付
1. 先运行 python3 scripts/media_qc.py final.mp4 --report qc/final.json：硬查画幅、帧率、音视频时长差、峰值；FAIL 就修复后重渲。
2. AI/人工复核开头、中段、结尾、每个场景切换和数字人代表帧：事实是否有证据、截图是否为真、敏感字是否打码、字幕是否可读、人物是否挡住关键内容、口型是否贴合唯一配音。AI 标出的具体问题须打开对应帧再裁定；无法检查的项目标记「未检查」，不得写成通过。
3. 逐项核对：录屏界面零残留｜形式/节奏可对应参考｜无虚构｜口播清晰无削波｜字幕与口播一致｜每场景至少抽 1 帧。
交付：成片 MP4｜QC JSON + 视觉复核记录｜拆解报告 + analysis.json｜稿件 + 对应表｜配音音频｜SRT + timeline｜Remotion 工程｜REPRODUCE.md（仓库 SHA + 依赖版本 + 步骤）。

### 可选音效后期

Remotion 输出干净成片后才做音效混音；音效素材必须有使用权，项目不捆绑来源不明的音效库。声音计划由实际口播节奏和镜头转场决定，开头 0.5 秒内可放 hook 音效，但不为满足模板硬塞声音。

计划文件示例（文件路径相对 JSON 文件）：

~~~json
{"events":[{"id":"hook","file":"assets/sfx/hit.wav","start_s":0.2,"gain_db":-16,"purpose":"hook"}]}
~~~

运行：python3 scripts/sfx_mix.py --video render.mp4 --plan sfx_plan.json --timeline timeline.json --out final.mp4。脚本会在口播期间自动压低音效、把混合峰值限制到约 -3 dBFS，并生成 .sfx.json 记录。无合适素材则保持干净配音，不生成占位音。

## 避坑清单（按收益排序）

1. **新机器先跑 `preflight.py`，凭据一次要齐**——走到阶段 3 才发现没有配音凭据 = 最大返工来源
2. 配音路线与合规边界开工前定死
3. 火山 TTS 鉴权头必须是 `Authorization: Bearer;<token>`（**分号**，不是空格），写成 `Bearer <token>` 直接 401
4. 火山 TTS 先 `--check` 再批量——一次验证 appid/token/音色；鉴权与音色问题是**不可重试**错误，脚本会快速退出并给建议
5. 火山 TTS 单段文本上限 1024 字节（建议 <300 字），按叙事段切分天然满足；"豆包语音合成模型2.0"音色（`*_uranus_bigtts`）需 v3 接口，v1 不支持
6. **ffmpeg/ffprobe 的来源要稳固**：预检会提示是否"借用其他 skill 的 node_modules 捆绑件"——是的话跑 `bash scripts/install_ffmpeg.sh` 装一份独立的（macOS 原生 arm64 静态构建、SHA-256 校验、免 sudo/Homebrew），否则那个 skill 一卸载，抽帧与分镜连带静默失效。
7. **别用裸 `python3` 跑流水线 Python 脚本**：faster-whisper 等依赖装在隔离 venv 里，而 PATH 中 `python3` 常先解析到**没有依赖的那个**解释器——轻则预检误报"缺 faster-whisper"，重则 `ImportError`。以预检报告头的「流水线解释器」为准
8. Remotion 渲染用系统 Chrome（`--browser-executable`），跳过 ~130MB Headless 下载
9. 长命令脚本化：后台任务 shell 不保留 cwd，cd 必须写在脚本内
10. HuggingFace 被代理拦截：`HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1`
11. pip 装 sdist 包报 EEXIST：`env -u PYTHONPATH pip install`
12. GitHub 慢/断流：`git -c http.version=HTTP/1.1 clone --depth 1 --single-branch`，codeload tarball 兜底
13. zsh：`rm -f` 匹配不到的 glob 会中断整条命令链
14. npm/pip/模型下载全部后台并行，等待期做分析、写稿
15. 抽帧成本控制：先低分辨率筛、关键段再全尺寸；验收抽帧每场景 1 帧即可
16. **yt-dlp 的版本探测可能极慢**（PyInstaller 单文件打包，实测 18–33s，与代理/stdin 只是部分相关）：预检给乾坤大挪移的媒体检查设了 8s 上限，超时即退化为自探，不复用它的 yt-dlp 版本号。yt-dlp 只是可选工具，**不要为它加长超时**——那会让预检从 3s 变成 33s

## 基准

38s 参考视频 → 77s 成片：首做约 4 小时 / 40–60 万 tokens（按本清单执行）；拆解 45min、稿件 30min、配音 5–10min（火山 TTS；真人实录按录制时长另计）、对齐 20min、Remotion 90min、验收 15min。
