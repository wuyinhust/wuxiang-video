#!/usr/bin/env python3
"""小无相功 · 新机器首次安装预检（凭据 / 运行时 / 依赖仓库 / 网络）

用途：**开工前**一次性盘清"还缺什么、由谁提供"，把该向用户索要的 key/token/id
一次要齐，避免流水线跑到阶段 3 才发现没配音凭据。

与乾坤大挪移的分工：媒体工具链（ffmpeg/ffprobe/yt-dlp/OpenCV）复用其
`scripts/check_environment.py --json`；本脚本只补它管不到的部分——
凭据、Node/Remotion 运行时、转写栈、依赖仓库、网络可达性。

用法：
  python3 preflight.py                      # 全量检查（两条配音路线都按必需校验）
  python3 preflight.py --route A            # 已定真人实录，则不要求 TTS 凭据
  python3 preflight.py --route B            # 已定火山 TTS，凭据缺失即失败
  python3 preflight.py --json               # 机器可读
  python3 preflight.py --offline            # 跳过网络探测
  python3 preflight.py --project-root .     # 指定项目根（找 tools/ 下的依赖仓库）

退出码：0 = 必需项齐备；2 = 有必需项缺失（脚本会列出待用户提供清单）
本脚本只读：不安装任何软件、不写入任何文件、不改动环境。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

# ── 凭据清单：env 名 / 说明 / 哪条路线需要（[] = 可选）/ 是否可给默认值 ──────────
CREDS = [
    {"env": "VOLC_TTS_APPID", "label": "火山引擎 AppID", "for": ["B"],
     "where": "火山引擎控制台 → 语音技术 → 语音合成 → 应用管理"},
    {"env": "VOLC_TTS_ACCESS_TOKEN", "label": "火山引擎 Access Token", "for": ["B"],
     "where": "同上（应用管理页，与 AppID 同处）"},
    {"env": "VOLC_TTS_VOICE", "label": "火山引擎音色 voice_type", "for": ["B"],
     "where": "控制台音色列表；须已下单/授权（免费音色也需 0 元下单）"},
    {"env": "VOLC_TTS_CLUSTER", "label": "火山引擎业务集群", "for": [], "default": "volcano_tts",
     "where": "一般无需配置，默认 volcano_tts"},
    {"env": "GITHUB_TOKEN", "label": "GitHub 凭据（cloning 私有仓库/发布产物时才需要）", "for": [],
     "where": "公开仓库 clone 不需要；需要时用 classic PAT（repo scope）"},
]

DEP_REPOS = [
    {"path": "tools/qiankun-video-shift",
     "url": "https://github.com/wuyinhust/qiankun-video-shift.git",
     "note": "拆解前三式"},
    {"path": "tools/video-talkcraft",
     "url": "https://github.com/Vincentwei1021/video-talkcraft.git",
     "note": "Remotion 剪辑"},
]

PROBES = [
    {"name": "github.com", "url": "https://api.github.com/rate_limit", "for": [],
     "why": "克隆依赖仓库 / 发布产物"},
    {"name": "hf-mirror.com", "url": "https://hf-mirror.com", "for": [],
     "why": "faster-whisper 模型下载（HF 被拦时的镜像）"},
    {"name": "openspeech.bytedance.com", "url": "https://openspeech.bytedance.com/api/v1/tts", "for": ["B"],
     "why": "火山引擎 TTS 合成"},
]

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

# 流水线解释器候选。PATH 里 versions/<ver>/bin 往往排在 envs/default/bin 之前，
# 于是裸 `python3 scripts/align_subtitles.py` 会解析到没装依赖的那个解释器。
# 所以这里不能假定 sys.executable，得实际去找一个 import 得到 faster_whisper 的。
VENV_PYTHONS = [
    "~/.workbuddy/binaries/python/envs/default/bin/python",
    ".venv/bin/python",
    "venv/bin/python",
    "env/bin/python",
]

# 由 probe_pipeline_python() 填充；报告里会明确提示后续脚本用哪个解释器
PIPELINE_PYTHON = None


def mask(value: str) -> str:
    """只显示可核对的片段，不泄露完整凭据。"""
    if not value:
        return "(空)"
    if len(value) <= 8:
        return "%s…（长 %d）" % (value[:1], len(value))
    return "%s…%s（长 %d）" % (value[:4], value[-2:], len(value))


def run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return (r.stdout or r.stderr or "").strip()
    except Exception:
        return ""


def probe_pipeline_python():
    """挑一个真能 import faster_whisper 的解释器，返回 (ok, path, detail)。

    先信当前解释器；不行就去已知 venv 里逐个实跑 import 验证——只看目录
    存在是不够的，目录在而依赖没装的情况很常见。结果写入 PIPELINE_PYTHON。
    """
    global PIPELINE_PYTHON

    if importlib.util.find_spec("faster_whisper") is not None:
        PIPELINE_PYTHON = sys.executable
        return True, sys.executable, "importable（解释器：%s）" % sys.executable

    for raw in VENV_PYTHONS:
        cand = os.path.expanduser(raw)
        if not (os.path.isfile(cand) and os.access(cand, os.X_OK)):
            continue
        try:
            r = subprocess.run([cand, "-c", "import faster_whisper"],
                               capture_output=True, text=True, timeout=90, check=False)
        except Exception:
            continue
        if r.returncode == 0:
            PIPELINE_PYTHON = cand
            return True, cand, "在 %s 中可用；当前解释器没有，脚本请用这个跑" % cand

    return False, None, "missing"


def first_line(text):
    return text.splitlines()[0] if text else ""


def parse_version(text):
    import re
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return tuple(int(x) for x in m.groups(default="0")) if m else None


def probe(url, timeout=8):
    """只探可达性；HTTP 任何状态码都算"能连通"，网络异常才算不可达。"""
    req = urllib.request.Request(url, headers={"User-Agent": "xiaowuxianggong-preflight"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, "HTTP %s" % resp.status
    except urllib.error.HTTPError as e:
        return True, "HTTP %s" % e.code  # 401/403/404 说明网络通
    except Exception as e:
        return False, "%s" % type(e).__name__


def check_runtime():
    items = []
    py = sys.version_info
    items.append({
        "id": "python", "name": "Python ≥ 3.10", "required": True,
        "ok": py >= (3, 10),
        "detail": "%d.%d.%d" % (py[:3]),
        "fix": "安装 Python 3.10+",
    })

    node = shutil.which("node")
    node_v = parse_version(run([node, "-v"])) if node else None
    items.append({
        "id": "node", "name": "Node ≥ 22（Remotion 要求）", "required": True,
        "ok": bool(node_v and node_v >= (22, 0)),
        "detail": (first_line(run([node, "-v"])) if node else "missing"),
        "fix": "安装 Node 22+（Remotion 4.x 需要）",
    })

    npm = shutil.which("npm")
    items.append({
        "id": "npm", "name": "npm", "required": True, "ok": bool(npm),
        "detail": npm or "missing", "fix": "随 Node 一起安装",
    })

    git = shutil.which("git")
    items.append({
        "id": "git", "name": "git", "required": True, "ok": bool(git),
        "detail": (first_line(run([git, "--version"])) if git else "missing"),
        "fix": "安装 git",
    })

    fw_ok, _fw_py, fw_detail = probe_pipeline_python()
    items.append({
        "id": "faster-whisper", "name": "faster-whisper（词级转写）", "required": True, "ok": fw_ok,
        "detail": fw_detail,
        "fix": "env -u PYTHONPATH pip install faster-whisper（避免 sdist 解包 EEXIST）",
    })

    chrome = os.environ.get("CHROME_PATH") or next(
        (p for p in CHROME_CANDIDATES if os.path.exists(p)), None)
    items.append({
        "id": "chrome", "name": "系统 Chrome（Remotion 渲染用，免下 Headless）", "required": False,
        "ok": bool(chrome), "detail": chrome or "missing",
        "fix": "装 Chrome/Chromium，或用 render_remotion.sh 的 --browser-executable 指定路径",
    })
    return items


BUNDLED_GLOBS = {
    "ffmpeg": [
        "*/node_modules/ffmpeg-static/ffmpeg",
        "*/node_modules/.pnpm/ffmpeg-static@*/node_modules/ffmpeg-static/ffmpeg",
    ],
    "ffprobe": [
        "*/node_modules/@ffprobe-installer/ffprobe",
        "*/node_modules/.pnpm/@derhuerst+ffprobe-static@*/node_modules/@derhuerst/ffprobe-static/ffprobe",
    ],
}


def resolve_media_binary(name):
    """四级解析，与乾坤大挪移 media_tools.resolve_binary 对齐：
    env 覆盖 → PATH → 常见安装目录 → 其他 skill 捆绑的 node_modules 二进制。
    最后一级很关键：本机就没有系统 ffmpeg，只有捆绑件。"""
    configured = os.environ.get("%s_BIN" % name.upper())
    if configured and os.path.isfile(os.path.expanduser(configured)):
        return os.path.expanduser(configured)

    found = shutil.which(name)
    if found:
        return found

    for candidate in (os.path.expanduser("~/.local/bin/%s" % name),
                      "/opt/homebrew/bin/%s" % name,
                      "/usr/local/bin/%s" % name):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    from pathlib import Path
    for root in (Path.home() / ".codex" / "skills", Path.home() / ".agents" / "skills"):
        if not root.is_dir():
            continue
        for pattern in BUNDLED_GLOBS.get(name, []):
            for hit in root.glob(pattern):
                if hit.is_file() and os.access(hit, os.X_OK):
                    return str(hit)
    return None


MEDIA_CHECK_TIMEOUT = 8    # 秒。该脚本内部会探 yt-dlp 版本，个别机器上很慢


def _self_probe_media(reason):
    """乾坤大挪移的检查脚本不可用/超时时的退化路径：自己探，并顺带判来源是否稳固。"""
    ff, fp = resolve_media_binary("ffmpeg"), resolve_media_binary("ffprobe")
    yt = resolve_media_binary("yt-dlp")
    borrowed = [n for n, p in (("ffmpeg", ff), ("ffprobe", fp))
                if p and "node_modules" in p]
    return [
        {
            "id": "media-tools", "name": "ffmpeg / ffprobe（%s）" % reason,
            "required": True, "ok": bool(ff and fp),
            "detail": "ffmpeg=%s ffprobe=%s" % (ff or "missing", fp or "missing"),
            "fix": "跑 bash scripts/install_ffmpeg.sh 装一份独立的",
        },
        {
            "id": "yt-dlp", "name": "yt-dlp（平台页链接下载，可选）", "required": False,
            "ok": bool(yt), "detail": (yt + "（未探版本）") if yt else "missing",
            "fix": "仅当参考视频来自平台页面链接时需要",
        },
        {
            "id": "media-binary-provenance",
            "name": "媒体二进制来源稳固（非借用其他 skill 的捆绑件）",
            "required": False, "ok": not borrowed,
            "detail": ("借用 node_modules 捆绑件：%s" % "、".join(borrowed)) if borrowed
                      else "系统级安装或 standalone",
            "fix": "跑 bash scripts/install_ffmpeg.sh 装一份独立的",
        },
    ]


def check_media_tools(project_root):
    """媒体工具链交给乾坤大挪移自己的检查脚本，避免两套标准。"""
    script = os.path.join(project_root, "tools", "qiankun-video-shift",
                          "scripts", "check_environment.py")
    if not os.path.exists(script):
        return _self_probe_media("乾坤大挪移未就位，退化为直探")
    out = run([sys.executable, script, "--json"], timeout=MEDIA_CHECK_TIMEOUT)
    try:
        rep = json.loads(out)
    except ValueError:
        # 该脚本会顺带探 yt-dlp 版本，而 yt-dlp 在部分机器上启动极慢（实测 18–33s，
        # 只因它是 PyInstaller 单文件打包）。yt-dlp 是可选项，不值得为它拖住整个预检，
        # 因此超时即退化为自探 —— ffmpeg/ffprobe 这两个核心结论不受影响。
        return _self_probe_media("乾坤大挪移检查脚本 %ds 内未返回，已退化为直探"
                                 % MEDIA_CHECK_TIMEOUT)
    t = rep.get("tools", {})
    items = []
    for key, label in (("ffmpeg", "ffmpeg"), ("ffprobe", "ffprobe")):
        items.append({
            "id": key, "name": label, "required": True,
            "ok": bool(t.get(key, {}).get("path")),
            "detail": (t.get(key, {}).get("version") or "missing"),
            "fix": "跑 bash scripts/install_ffmpeg.sh 装一份独立的 %s" % label,
        })
    items.append({
        "id": "yt-dlp", "name": "yt-dlp（平台页链接下载，可选）", "required": False,
        "ok": bool(t.get("yt-dlp", {}).get("path")),
        "detail": t.get("yt-dlp", {}).get("version") or "missing",
        "fix": "仅当参考视频来自平台页面链接时需要",
    })

    # 脆弱性提示：媒体二进制来自其他 skill 的 node_modules 时，卸载那个 skill 就会连锁失效
    borrowed = [k for k in ("ffmpeg", "ffprobe")
                if "node_modules" in (t.get(k, {}).get("path") or "")]
    items.append({
        "id": "media-binary-provenance",
        "name": "媒体二进制来源稳固（非借用其他 skill 的捆绑件）",
        "required": False, "ok": not borrowed,
        "detail": ("借用 node_modules 捆绑件：%s" % "、".join(borrowed)) if borrowed
                  else "系统级安装或 standalone",
        "fix": "跑 bash scripts/install_ffmpeg.sh 装一份独立的（免 sudo / 免 Homebrew，含 SHA-256 校验）",
    })
    return items


def check_repos(project_root):
    items = []
    for repo in DEP_REPOS:
        full = os.path.join(project_root, repo["path"])
        ok = os.path.isdir(full)
        detail = "missing"
        if ok:
            sha = first_line(run(["git", "-C", full, "rev-parse", "--short", "HEAD"]))
            detail = os.path.join(repo["path"]) + (" @%s" % sha if sha else "")
        items.append({
            "id": repo["path"], "name": "%s（%s）" % (repo["path"], repo["note"]),
            "required": True, "ok": ok, "detail": detail,
            "fix": "git -c http.version=HTTP/1.1 clone --depth 1 --single-branch %s %s"
                   % (repo["url"], repo["path"]),
        })
    return items


def check_creds(route):
    routes = {"A": ["A"], "B": ["B"], "both": ["A", "B"]}[route]
    items = []
    for c in CREDS:
        val = os.environ.get(c["env"], "")
        needed = bool(set(c["for"]) & set(routes))
        ok = bool(val) or "default" in c
        items.append({
            "env": c["env"], "name": c["label"], "for": c["for"],
            "required": needed, "ok": ok,
            "detail": mask(val) if val else
                      ("未设置，取默认 %s" % c["default"] if "default" in c else "未设置"),
            "where": c["where"],
        })
    return items


def check_network(route, offline):
    if offline:
        return [{"name": p["name"], "required": False, "ok": None,
                 "detail": "已跳过（--offline）", "why": p["why"]} for p in PROBES]
    items = []
    for p in PROBES:
        ok, detail = probe(p["url"])
        items.append({
            "name": p["name"],
            "required": bool("B" in p["for"] and route in ("B", "both")),
            "ok": ok, "detail": detail, "why": p["why"],
            "fix": "检查网络/代理：如需代理，设置 HTTPS_PROXY（如 http://127.0.0.1:7890）",
        })
    return items


MARK = {True: "✓", False: "✗", None: "-"}


def mark(item):
    """必需缺失=✗ 阻断；可选未配=○ 仅提示；已就位=✓；跳过=-。"""
    ok = item.get("ok")
    if not ok and not item.get("required"):
        return "○"
    return MARK[ok]


def main():
    ap = argparse.ArgumentParser(description="小无相功 · 新机器首次安装预检")
    ap.add_argument("--project-root", default=".", help="项目根目录（找 tools/ 下的依赖仓库）")
    ap.add_argument("--route", choices=["A", "B", "both"], default="both",
                    help="配音路线：A 真人实录 / B 火山引擎 TTS / both（默认，按两条都必需校验）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--offline", action="store_true", help="跳过网络探测")
    args = ap.parse_args()

    root = os.path.abspath(args.project_root)
    report = {
        "project_root": root,
        "route": args.route,
        "runtime": check_runtime(),
        "media_tools": check_media_tools(root),
        "repos": check_repos(root),
        "creds": check_creds(args.route),
        "network": check_network(args.route, args.offline),
    }
    # 由 check_runtime() 内部探测得出，故在其后补写，别在字典字面量里提前读
    report["pipeline_python"] = PIPELINE_PYTHON

    def missing(items, key_required="required"):
        return [i for i in items if i.get(key_required) and not i.get("ok")]

    blockers = (missing(report["runtime"]) + missing(report["media_tools"])
                + missing(report["repos"]) + missing(report["creds"])
                + missing(report["network"]))
    report["ready"] = not blockers
    report["blockers"] = [i.get("name") or i.get("env") for i in blockers]

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not blockers else 2

    print("小无相功 · 安装预检")
    print("项目根：%s    配音路线：%s" % (root, args.route))
    if PIPELINE_PYTHON:
        print("流水线解释器：%s" % PIPELINE_PYTHON)
    print()
    for title, items, req_key in (
        ("运行时", report["runtime"], "required"),
        ("媒体工具链（复用乾坤大挪移 check_environment.py）", report["media_tools"], "required"),
        ("依赖仓库", report["repos"], "required"),
        ("网络", report["network"], "required"),
    ):
        print("[%s]" % title)
        for i in items:
            tag = "必需" if i.get(req_key) else "可选"
            print("  %s %-38s %s  %s" % (mark(i), i["name"][:38], tag, i.get("detail", "")))
            if not i.get("ok") and i.get("fix"):
                print("      → %s" % i["fix"])
        print()

    print("[凭据]  路线 %s；值不落盘、不进仓库" % args.route)
    for c in report["creds"]:
        tag = "必需" if c["required"] else "可选"
        rng = "/".join(c["for"]) if c["for"] else "—"
        print("  %s %-26s %s  适用路线 %-3s %s" % (mark(c), c["env"], tag, rng, c["detail"]))
        if not c["ok"]:
            print("      → 获取：%s" % c["where"])
    print()

    if blockers:
        print("✗ 预检未通过：%d 项必需缺失 —— 请不要开工，先补齐。" % len(blockers))
        print("\n需要用户提供的内容（一次性索取，勿在流水线中途索要）：")
        need = [c for c in report["creds"] if c["required"] and not c["ok"]]
        if need:
            for c in need:
                print("  · %s —— %s" % (c["env"], c["name"]))
                print("      获取：%s" % c["where"])
        else:
            print("  （无 —— 本路线不需要任何凭据）")
        print("\n其余缺失项（装软件 / 克隆仓库 / 修网络）：")
        for i in blockers:
            if i.get("name") not in [c["name"] for c in report["creds"]]:
                print("  · %s —— %s" % (i.get("name"), i.get("fix", "")))
        return 2

    print("✓ 预检通过，可以开工。路线 B 建议先跑：")
    print("  python3 scripts/volcano_tts_batch.py --check   # 一句话验证 appid/token/音色")
    if PIPELINE_PYTHON and os.path.realpath(PIPELINE_PYTHON) != os.path.realpath(sys.executable):
        print("\n注意：流水线里的 Python 脚本请用下面这个解释器跑（当前解释器没装依赖）：")
        print("  %s scripts/align_subtitles.py …" % PIPELINE_PYTHON)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
