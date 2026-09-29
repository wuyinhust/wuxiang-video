#!/usr/bin/env python3
"""Mix licensed sound effects under the rendered voice track for a final MP4."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


def fail(message: str) -> None:
    print("错误：" + message, file=sys.stderr)
    raise SystemExit(2)


def capture(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        fail("找不到 %s；需要安装 ffmpeg / ffprobe。" % argv[0])
    except subprocess.CalledProcessError as exc:
        fail("%s 执行失败：%s" % (argv[0], (exc.stderr or "").strip()[-1200:]))
    return result.stdout.strip()


def media_info(path: Path) -> tuple[float, bool]:
    raw = capture([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type", "-of", "json", str(path),
    ])
    data = json.loads(raw)
    duration = float((data.get("format") or {}).get("duration") or 0)
    has_audio = any(stream.get("codec_type") == "audio" for stream in data.get("streams", []))
    return duration, has_audio


def load_plan(plan_path: Path, video_duration: float, timeline_path: Path | None) -> tuple[list[dict], list[str]]:
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("无法读取音效计划：%s" % exc)
    events = plan.get("events")
    if not isinstance(events, list) or not events:
        fail("计划必须包含非空 events 数组。")

    speech = []
    if timeline_path:
        try:
            timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
            speech = timeline.get("sentences") or []
        except (OSError, json.JSONDecodeError) as exc:
            fail("无法读取字幕时间线：%s" % exc)

    warnings = []
    prepared = []
    for index, event in enumerate(events, 1):
        if not isinstance(event, dict):
            fail("音效事件 %d 必须是对象。" % index)
        try:
            start = float(event["start_s"])
            gain = float(event.get("gain_db", -16.0))
        except (KeyError, TypeError, ValueError):
            fail("音效事件 %d 缺少有效 start_s / gain_db。" % index)
        if start < 0 or start >= video_duration:
            fail("音效事件 %d 的 start_s 超出视频时长。" % index)
        if gain > 0 or gain < -48:
            fail("音效事件 %d 的 gain_db 必须在 -48 到 0 dB 之间。" % index)
        raw_path = Path(str(event.get("file", ""))).expanduser()
        path = raw_path if raw_path.is_absolute() else plan_path.parent / raw_path
        if not path.is_file():
            fail("音效文件不存在：%s" % path)
        duration, has_audio = media_info(path)
        if not has_audio or duration <= 0:
            fail("音效文件没有有效音轨：%s" % path)
        overlap = False
        for sentence in speech:
            try:
                if start < float(sentence["end"]) and start + min(duration, 1.0) > float(sentence["start"]):
                    overlap = True
                    break
            except (KeyError, TypeError, ValueError):
                continue
        purpose = str(event.get("purpose", ""))
        if purpose == "hook" and start > 0.5:
            warnings.append("hook 音效落点 %.2fs，超过建议的开头 0.5 秒。" % start)
        prepared.append({
            "id": str(event.get("id") or "sfx-%02d" % index),
            "path": path.resolve(),
            "start": start,
            "gain": gain,
            "duration": duration,
            "speech_overlap": overlap,
        })
    if speech and any(item["speech_overlap"] for item in prepared):
        warnings.append("部分音效与口播句时间重叠；混音会自动对音效做侧链压低。")
    return prepared, warnings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把有使用权的音效混入 Remotion 渲染成片；口播会触发音效侧链压低。"
    )
    parser.add_argument("--video", required=True, help="Remotion 渲染的 MP4（须含配音音轨）")
    parser.add_argument("--plan", required=True, help="JSON 音效计划，文件路径相对计划文件")
    parser.add_argument("--out", required=True, help="带音效的 MP4 输出路径")
    parser.add_argument("--timeline", help="可选 timeline.json，用于报告口播重叠事件")
    args = parser.parse_args()

    video = Path(args.video).expanduser().resolve()
    plan_path = Path(args.plan).expanduser().resolve()
    output = Path(args.out).expanduser().resolve()
    if not video.is_file():
        fail("成片不存在：%s" % video)
    duration, has_audio = media_info(video)
    if duration <= 0 or not has_audio:
        fail("成片必须包含有效视频时长与配音音轨。")
    timeline_path = Path(args.timeline).expanduser().resolve() if args.timeline else None
    events, warnings = load_plan(plan_path, duration, timeline_path)

    inputs = ["-i", str(video)]
    filters = [
        "[0:a:0]apad=whole_dur=%.3f,atrim=duration=%.3f[voice]"
        % (duration, duration)
    ]
    labels = []
    for index, event in enumerate(events, 1):
        inputs.extend(["-i", str(event["path"])])
        delay_ms = int(round(event["start"] * 1000))
        label = "fx%d" % index
        filters.append(
            "[%d:a:0]volume=%.2fdB,adelay=%d:all=1,apad=whole_dur=%.3f,"
            "atrim=duration=%.3f[%s]"
            % (index, event["gain"], delay_ms, duration, duration, label)
        )
        labels.append("[%s]" % label)

    filters.append(
        "%samix=inputs=%d:duration=longest:normalize=0,atrim=duration=%.3f[sfx]"
        % ("".join(labels), len(labels), duration)
    )
    filters.append(
        "[sfx][voice]sidechaincompress=threshold=0.05:ratio=6:attack=15:release=350[ducked]"
    )
    filters.append(
        "[voice][ducked]amix=inputs=2:duration=first:normalize=0,"
        "alimiter=limit=0.708:level=0[mix]"
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *inputs, "-filter_complex", ";".join(filters),
        "-map", "0:v:0", "-map", "[mix]", "-map_metadata", "0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-t", "%.3f" % duration, "-movflags", "+faststart", str(output),
    ]
    capture(command)

    report = {
        "source_video": str(video),
        "output_video": str(output),
        "duration_s": round(duration, 3),
        "sfx": [{
            "id": event["id"],
            "file": str(event["path"]),
            "start_s": round(event["start"], 3),
            "gain_db": event["gain"],
            "speech_overlap": event["speech_overlap"],
        } for event in events],
        "warnings": warnings,
        "voice_ducking": "sidechaincompress",
        "peak_limiter_dbfs": -3.0,
    }
    report_path = output.with_suffix(output.suffix + ".sfx.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print("已输出：%s" % output)
    print("音效记录：%s" % report_path)
    for warning in warnings:
        print("提示：" + warning, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
