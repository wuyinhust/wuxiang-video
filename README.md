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
  → 可选数字人出镜（授权肖像 + 最终配音）
  → 词级对齐字幕（原稿文本 + 真实语音时间，绝不用 ASR 原文）
  → Remotion 成片（1080×1920 竖屏，主渲染器）
  → 可选音效混音
  → 技术验收 + AI/人工画面复核
  → 交付
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
python3 scripts/preflight.py --project-root . --route B --avatar  # TTS + 火山数字人
python3 scripts/preflight.py --project-root . --json     # 机器可读
```

它盘四类前置：运行时（Python / Node / npm / git / faster-whisper / Chrome）、媒体工具链
（复用乾坤大挪移的 `check_environment.py`）、依赖仓库、网络可达性；再按路线校验凭据。
未通过时会把「需要用户提供的凭据」单独列出来，便于一次性索取。

### ffmpeg / ffprobe：装一份自己的，别借别人的

乾坤大挪移的媒体工具链会一路找到 `~/.codex/skills/*/node_modules` 里 `ffmpeg-static`
之类的**捆绑二进制**。在没有系统 ffmpeg 的机器上那样确实能跑通，但那个 skill 一卸载或更新，
抽帧与分镜就会静默失效，而报错不会指向真正原因。

预检因此会单独盯一件事：**媒体二进制来源是否稳固**。没过就跑这个脚本，装一份独立的：

```bash
bash scripts/install_ffmpeg.sh              # 默认装到 ~/.local/bin
bash scripts/install_ffmpeg.sh --check      # 只体检，看当前来源是否独立
bash scripts/install_ffmpeg.sh --proxy http://127.0.0.1:7890
bash scripts/install_ffmpeg.sh --force      # 同版本也重装
```

macOS 原生 arm64/amd64 静态构建，强制 SHA-256 校验（不通过即中止、绝不安装），
免 Homebrew、免 sudo，装完自动清 quarantine 扩展属性。幂等：版本一致时直接跳过。

### 流水线用哪个 Python 解释器

预检报告头会给出「流水线解释器」，**后续所有 Python 脚本都用它跑**：

```
流水线解释器：/Users/…/python/envs/default/bin/python
```

faster-whisper 等依赖装在隔离 venv 里，而 PATH 中 `python3` 往往先解析到**没有依赖的
那个**解释器——直接 `python3 scripts/align_subtitles.py` 会 `ImportError`，跑预检也会
误报"缺 faster-whisper"（依赖其实装着）。

预检因此不假定当前解释器，而是按 当前解释器 → `envs/default` → `.venv` → `venv` → `env`
的顺序，找一个**实跑 `import faster_whisper` 成功**的解释器。只看目录存在是不够的——
目录在而依赖没装很常见。

### 凭据清单

| 凭据 | 环境变量 | 何时必需 | 从哪里拿 |
|---|---|---|---|
| 火山引擎 AppID | `VOLC_TTS_APPID` | 路线 B | 火山引擎控制台 → 语音技术 → 语音合成 → 应用管理 |
| 火山引擎 Access Token | `VOLC_TTS_ACCESS_TOKEN` | 路线 B | 同上（与 AppID 同页） |
| 火山引擎音色 voice_type | `VOLC_TTS_VOICE` | 路线 B | 控制台音色列表；**须已下单/授权** |
| 业务集群 | `VOLC_TTS_CLUSTER` | 一般不用 | 默认 `volcano_tts` |
| 火山视觉 API AK/SK | `VOLC_ACCESSKEY` / `VOLC_SECRETKEY` | 选择火山数字人时 | 火山引擎控制台 → 访问控制 → 访问密钥；需有视觉智能 CV 权限 |
| TOS 桶 | `VOLC_TOS_BUCKET` / `VOLC_TOS_REGION` / `VOLC_TOS_ENDPOINT` | 本地肖像/音频自动上传时 | 单独开通对象存储并创建 Bucket；语音合成不会自动分配 |
| GitHub 凭据 | `GITHUB_TOKEN` | 仅私有仓库 / 发布产物 | classic PAT（`repo` scope） |

取值优先级：命令行参数 > `--config creds.json` > 环境变量。模板见 `scripts/creds.example.json`。
**凭据不入源码、不入仓库、不进报告**——`.gitignore` 已预置 `creds.json` / `*.env`。

## 仓库结构

```
SKILL.md                      技能主文档（预检 + 六段流水线 + 决策点 + 避坑清单）
.gitignore                    预置凭据与生成物忽略规则
scripts/
├── preflight.py              安装预检：凭据 / 运行时 / 媒体工具链 / 依赖仓库 / 网络（只读）
├── install_ffmpeg.sh         装独立 ffmpeg/ffprobe（SHA-256 校验，免 sudo / 免 Homebrew）
├── creds.example.json        凭据模板（复制为 creds.json 并填入）
├── align_subtitles.py        字幕对齐器：原稿 + faster-whisper 词级时间戳 → SRT
├── volcano_tts_batch.py      火山引擎 TTS 批量配音 + 主音轨拼接 + 音量归一
├── volcano_digital_human.py 火山 OmniHuman 1.5 数字人片段生成（可选）
├── sfx_mix.py                音效混音、口播侧链压低与峰值限制（可选）
├── media_qc.py               成片技术验收报告
└── render_remotion.sh        Remotion 一键渲染（系统 Chrome，免 Headless 下载）
```

## 依赖

- [qiankun-video-shift](https://github.com/wuyinhust/qiankun-video-shift)（拆解前三式）
- [video-talkcraft](https://github.com/Vincentwei1021/video-talkcraft)（Remotion 剪辑）
- Python 3 + Node 22+
- ffmpeg / ffprobe —— 用 `scripts/install_ffmpeg.sh` 装一份独立的（**不要**依赖其他 skill 的捆绑件）
- faster-whisper（词级转写）
- 路线 B 另需火山引擎语音合成凭据（AppID / Access Token / 音色），纯 HTTPS 调用、无额外 pip 依赖
- 数字人可选：火山 OmniHuman 1.5 需要视觉服务 AK/SK；本地文件上传 TOS 时另装 requirements-digital-human.txt，并使用自有 Bucket

## 声音来源与出镜画面（分开决策）

一条视频的**声音来源**仍二选一：真人实录，或火山引擎 TTS。是否使用数字人是另一个选择：可以不出镜、使用授权真人视频，或用授权肖像和已完成的配音生成火山数字人。数字人可驱动真人录音或 TTS 音频。

### 火山 OmniHuman 1.5 数字人

模型用单张图片和音频生成一段出镜视频。火山语音合成服务不附带 TOS 桶；TOS 是要单独开通、由用户创建的对象存储。接口要求图片和音频可通过 HTTPS 访问。

本地文件模式会由脚本自动上传到私有 TOS，生成限时读取链接；任务完成后尝试删除输入对象。需要设置：

~~~bash
export VOLC_ACCESSKEY=...
export VOLC_SECRETKEY=...
export VOLC_TOS_BUCKET=...
export VOLC_TOS_REGION=cn-beijing
export VOLC_TOS_ENDPOINT=tos-cn-beijing.volces.com

python3 -m pip install -r requirements-digital-human.txt
python3 scripts/preflight.py --route B --avatar
python3 scripts/volcano_digital_human.py \
  --portrait assets/avatar.png \
  --audio tts/output/master_norm.mp3 \
  --timeline timeline.json \
  --out out/digital-human \
  --resolution 1080
~~~

每条 API 音频必须**严格短于 35 秒**。超过 35 秒的本地配音需提供 align_subtitles.py 生成的 timeline.json；脚本按句界切成最长 30 秒的段，并对超过 30 秒的单句或尾部静音报错，需先拆短或裁剪。30–35 秒的单段可以直接提交，但仍必须小于 35 秒。脚本输出 avatar-001.mp4 等**无声画面片段**和 avatar_manifest.json，Remotion 按清单编排；最终配音只在 Remotion 音轨中放一次。已有公网 HTTPS 文件可传 --image-url、--audio-url 和 --duration-s；该模式仅支持小于 35 秒的单段音频。

火山数字人 API 使用 AK/SK，和 TTS 的 AppID/Access Token 是两套凭据。不要把密钥写入脚本或仓库。数字人输入图必须有使用授权。API 服务开通、计费和接口字段以[火山 OmniHuman 1.5 官方 API Explorer](https://api.volcengine.com/api-explorer/debug?action=JimengRealmanAvatarPictureOmniV15SubmitTask&groupName=Jimeng+AI+Public+Beta&serviceCode=cv&version=2024-06-06)为准。

### 音效混音（可选）

Remotion 先用单独的最终配音轨渲染干净成片（数字人画面片段保持静音），再用有使用权的音效素材做后期混音。不要把音效嵌进 Remotion 场景音轨，也不附带来源不明的素材库。

音效计划示例，文件路径相对 JSON 文件：

~~~json
{
  "events": [
    {
      "id": "hook",
      "file": "assets/sfx/hit.wav",
      "start_s": 0.2,
      "gain_db": -16,
      "purpose": "hook"
    }
  ]
}
~~~

~~~bash
python3 scripts/sfx_mix.py \
  --video out/render.mp4 \
  --plan sfx_plan.json \
  --timeline timeline.json \
  --out out/final.mp4
~~~

脚本会把音效压在配音之下，限制混合峰值至约 -3 dBFS，并写出 .sfx.json 混音记录。音效库缺失或没有合适落点时，跳过音效，不造占位音。

### 成片技术验收

~~~bash
python3 scripts/media_qc.py out/final.mp4 --report qc/final.json
~~~

检查画幅、帧率、音视频时长差和音轨峰值。此技术报告不代表视觉验收通过；画面内容需另做 AI/人工抽帧复核。若任务超时中断，上传对象可能仍被数字人任务读取；给私有 TOS 桶配置生命周期清理规则。

## 核心设计

1. **先预检再开工**：新机器装完先跑 `preflight.py`，凭据一次要齐——走到阶段 3 才发现没有
   配音凭据，是最大的返工来源。
2. **第〇步先锁决策**：声音来源（真人实录 / 火山引擎 TTS，**二选一，不混用**）、是否需要数字人出镜、肖像授权及证据边界，开工前定好。
3. **自动切镜不可信**：每 2 秒补抽帧，按叙事人工细分 8–10 段。
4. **字幕铁律**：字幕 = 原稿文本 + 真实语音词级时间，绝不直接用 ASR 原文
   （生造词必被转写错）。
5. **真实原则**：演示素材用本项目真实拆解产物，不虚构功能、数据、评价。
6. **凭据纪律**：凭据只走环境变量 / `--config`，不入源码、不入仓库、不进报告。

## 基准

38s 参考视频 → 77s 成片，约 2.5–4 小时；详见 SKILL.md。

## License

MIT
