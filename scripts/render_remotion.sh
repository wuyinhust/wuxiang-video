#!/bin/zsh
# 小无相功 · Remotion 一键渲染（脚本内置 cd，规避后台任务 cwd 不保留的坑）
# 用法：zsh render_remotion.sh <remotion工程目录> [composition] [输出名] [帧范围]
#   zsh render_remotion.sh /path/to/remotion                  # 全片
#   zsh render_remotion.sh /path/to/remotion Main prev.mp4 0-269  # 代表片段自检
set -e
PROJ="${1:?usage: render_remotion.sh <project_dir> [comp] [out] [frames]}"
COMP="${2:-Main}"
OUT="${3:-out/film.mp4}"
FRAMES="${4:-}"

# 优先托管 Node 22，其次系统 node
NODE22="/Users/wangyao/.workbuddy/binaries/node/versions/22.22.2-3/bin"
[ -d "$NODE22" ] && export PATH="$NODE22:$PATH"

# 系统 Chrome：跳过 ~130MB Headless Shell 下载
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

cd "$PROJ"
ARGS=(remotion render src/entry.ts "$COMP" "$OUT" --log=info)
[ -x "$CHROME" ] && ARGS+=(--browser-executable="$CHROME")
[ -n "$FRAMES" ] && ARGS+=(--frames="$FRAMES")
npx "${ARGS[@]}"
