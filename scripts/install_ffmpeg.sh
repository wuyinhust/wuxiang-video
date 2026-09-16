#!/usr/bin/env bash
# install_ffmpeg.sh —— 为视频流水线装一份独立的 ffmpeg / ffprobe
#
# 为什么需要：乾坤大挪移的 media_tools 会一路找到 ~/.codex/skills/*/node_modules
# 里 ffmpeg-static 之类的捆绑二进制。那条路在没装系统 ffmpeg 的机器上确实能跑通，
# 但那个 skill 一卸载/更新，抽帧与分镜就静默失效，而报错不会指向真正原因。
# 本脚本装的是自己的一份，从此不借别人的东西。
#
# 主源 ffmpeg.martin-riedl.de：macOS 原生 arm64 构建、提供 SHA-256 校验值、
# 免 Homebrew、免 sudo。备选源 evermeet.cx 只有 Intel 版，不在此脚本内。
#
# 用法：
#   bash install_ffmpeg.sh                          # 装到 ~/.local/bin（默认）
#   bash install_ffmpeg.sh --dest /usr/local/bin     # 需该目录可写
#   bash install_ffmpeg.sh --proxy http://127.0.0.1:7890
#   bash install_ffmpeg.sh --check                   # 只体检，不改动任何文件
#   bash install_ffmpeg.sh --force                   # 同版本也重装
#   bash install_ffmpeg.sh --release 1787073674_9.0.1   # 钉死某个构建

set -euo pipefail

SITE="https://ffmpeg.martin-riedl.de"
DEST="$HOME/.local/bin"
PROXY=""
FORCE=0
CHECK_ONLY=0
RELEASE=""

log()  { printf '%s\n' "$*"; }
die()  { printf '错误：%s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dest)    DEST="${2:?--dest 需要一个目录}"; shift 2 ;;
    --proxy)   PROXY="${2:?--proxy 需要一个 URL}"; shift 2 ;;
    --release) RELEASE="${2:?--release 需要 ts_version}"; shift 2 ;;
    --force)   FORCE=1; shift ;;
    --check)   CHECK_ONLY=1; shift ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "未知参数：${1}（用 --help 看用法）" ;;
  esac
done

DEST="${DEST/#\~/$HOME}"

# ---- 依赖检查（全部是 macOS 自带命令，刻意不引入 brew）----
for c in curl unzip shasum; do
  command -v "$c" >/dev/null 2>&1 || die "缺少 ${c}，无法继续"
done

[ "$(uname -s)" = "Darwin" ] || die "本脚本面向 macOS；Linux 请用系统包管理器（apt/dnf）安装 ffmpeg"

case "$(uname -m)" in
  arm64|aarch64) ARCH=arm64 ;;
  x86_64|amd64)  ARCH=amd64 ;;
  *) die "不支持的架构：$(uname -m)" ;;
esac

# ---- 代理：仅在该参数存在时显式指定，否则沿用 curl 环境变量 ----
fetch() {  # fetch URL OUT
  if [ -n "$PROXY" ]; then
    curl -fsSL --max-time 600 -x "$PROXY" -o "$2" "$1"
  else
    curl -fsSL --max-time 600 -o "$2" "$1"
  fi
}

describe_bin() {  # 打印某个二进制的来源与版本，并判断是否独立
  local name="$1" path ver
  path="$(command -v "$name" 2>/dev/null || true)"
  if [ -z "$path" ]; then
    printf '  %-8s 未找到\n' "$name"
    return
  fi
  ver="$("$path" -version 2>/dev/null | head -1 | cut -c1-46 || echo '?')"
  case "$path" in
    *node_modules*) printf '  %-8s %s\n           %s\n           [借用其他 skill 的捆绑件 —— 脆弱]\n' "$name" "$path" "$ver" ;;
    *)              printf '  %-8s %s\n           %s\n           [独立]\n' "$name" "$path" "$ver" ;;
  esac
}

# ---- --check：只体检 ----
if [ "$CHECK_ONLY" -eq 1 ]; then
  log "当前解析结果："
  describe_bin ffmpeg
  describe_bin ffprobe
  log ""
  borrowed=0
  for b in ffmpeg ffprobe; do
    case "$(command -v "$b" 2>/dev/null || echo)" in *node_modules*) borrowed=1 ;; esac
  done
  if [ "$borrowed" -eq 1 ]; then
    log "结论：仍有二进制借用其他 skill 的捆绑件，建议执行不带 --check 的本脚本。"
    exit 1
  fi
  log "结论：ffmpeg / ffprobe 来源独立。"
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ---- 定位构建版本 ----
if [ -n "$RELEASE" ]; then
  REL="$RELEASE"
  log "[1/5] 使用指定构建：$REL"
else
  log "[1/5] 查询 $SITE 上 macOS/$ARCH 的最新稳定构建…"
  fetch "$SITE/" "$TMP/index.html"
  # 目录形如 /download/macos/arm64/1787073674_9.0.1；排除含 N- 的每日构建
  REL="$(grep -oE "/download/macos/$ARCH/[0-9]+_[^/\"']+" "$TMP/index.html" \
        | sed 's|.*/||' | grep -v 'N-' | sort -rn | head -1 || true)"
  [ -n "$REL" ] || die "无法从站点解析出版本号（站点可能改版）。用 --release ts_version 手工指定，或改用其他源"
  log "        最新稳定构建：$REL"
fi

VER="${REL#*_}"
BASE="$SITE/download/macos/$ARCH/$REL"

# ---- 已装同版本则跳过（除非 --force）----
if [ "$FORCE" -eq 0 ] && [ -x "$DEST/ffmpeg" ] && [ -x "$DEST/ffprobe" ]; then
  cur="$("$DEST/ffmpeg" -version 2>/dev/null | head -1 | awk '{print $3}' || true)"
  if [ "${cur%%-*}" = "$VER" ]; then
    log ""
    log "已安装 ${VER}（${DEST}）且与目标版本一致，无需改动。用 --force 可强制重装。"
    exit 0
  fi
fi

# ---- 下载（含校验值）----
log "[2/5] 下载 ffmpeg / ffprobe 及其 SHA-256…"
for f in ffmpeg.zip ffmpeg.zip.sha256 ffprobe.zip ffprobe.zip.sha256; do
  fetch "$BASE/$f" "$TMP/$f" || die "下载失败：$BASE/$f"
  printf '        %-20s %s 字节\n' "$f" "$(wc -c < "$TMP/$f" | tr -d ' ')"
done

# ---- 强制校验：不通过即中止，绝不安装未验证的二进制 ----
log "[3/5] 校验 SHA-256…"
for z in ffmpeg ffprobe; do
  ( cd "$TMP" && shasum -a 256 --status -c "$z.zip.sha256" ) \
    || die "$z.zip 校验失败（下载损坏或被篡改），已中止，未安装任何文件"
  printf '        %-12s ✓\n' "$z.zip"
done

# ---- 解压并安装 ----
log "[4/5] 安装到 ${DEST}…"
mkdir -p "$DEST" || die "无法创建 ${DEST}（权限不足？考虑用 --dest 指向可写目录）"
( cd "$TMP" && unzip -q -o ffmpeg.zip && unzip -q -o ffprobe.zip )

for b in ffmpeg ffprobe; do
  [ -f "$TMP/$b" ] || die "解压后未找到 $b"
  # 先删目标：它可能是软链，cp/install 跟随链接会改写别的 skill 的文件
  rm -f "$DEST/$b"
  install -m 755 "$TMP/$b" "$DEST/$b"
  xattr -c "$DEST/$b" 2>/dev/null || true   # 清 quarantine，防 Gatekeeper 拦截
  printf '        %-8s -> %s\n' "$b" "$DEST/$b"
done

# ---- 验收 ----
log "[5/5] 验证…"
"$DEST/ffmpeg"  -hide_banner -version | head -1 | sed 's/^/        /'
"$DEST/ffprobe" -hide_banner -version | head -1 | sed 's/^/        /'
# 真跑一次滤镜链，避免"能报版本号但不能用"
"$DEST/ffmpeg" -hide_banner -loglevel error -f lavfi -i "sine=duration=0.3" -f null - \
  && log "        滤镜链执行正常 ✓"

log ""
log "完成。ffmpeg / ffprobe $VER 已装到 $DEST"
case ":$PATH:" in
  *":$DEST:"*) log "该目录已在 PATH 中，直接可用。" ;;
  *) log "注意：$DEST 不在当前 PATH 中，请把下面一行加入 ~/.zprofile："
     log "  export PATH=\"$DEST:\$PATH\"" ;;
esac
