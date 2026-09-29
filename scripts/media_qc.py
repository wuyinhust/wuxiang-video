#!/usr/bin/env python3
"""Technical acceptance gate for rendered MP4 files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from fractions import Fraction


def fail(message: str) -> None:
    print("错误：" + message, file=sys.stderr)
    raise SystemExit(2)


def capture(argv: list[str], allow_failure: bool = False) -> tuple[str, str, int]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError:
        fail("找不到 %s；需要安装 ffmpeg / ffprobe。" % argv[0])
    if result.returncode and not allow_failure:
        fail("%s 执行失败：%s" % (argv[0], (result.stderr or "").strip()[-1200:]))
    return result.stdout, result.stderr, result.returncode


def check(name: str, passed: bool, observed, expected: str) -> dict:
    return {
        "name": name,
        "status": "pass" if passed else "fail",
        "observed": observed,
        "expected": expected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="检查成片画幅、帧率、音视频时长差和音轨峰值；不代替画面内容审查。"
    )
    parser.add_argument("video", help="待验收 MP4")
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--max-av-drift", type=float, default=0.1)
    parser.add_argument("--peak-limit-db", type=float, default=-1.0)
    parser.add_argument("--report", help="JSON 报告输出路径")
    args = parser.parse_args()

    path = Path(args.video).expanduser().resolve()
    if not path.is_file():
        fail("视频文件不存在：%s" % path)
    raw, _, _ = capture([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate,duration",
        "-of", "json", str(path),
    ])
    try:
        metadata = json.loads(raw)
    except json.JSONDecodeError:
        fail("ffprobe 输出无法解析。")
    streams = metadata.get("streams", [])
    video_streams = [item for item in streams if item.get("codec_type") == "video"]
    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    if not video_streams:
        fail("文件没有视频流。")
    if not audio_streams:
        fail("文件没有音轨；交付成片应含配音。")

    video = video_streams[0]
    audio = audio_streams[0]
    format_duration = float((metadata.get("format") or {}).get("duration") or 0)
    def stream_duration(stream: dict) -> float:
        try:
            return float(stream.get("duration"))
        except (TypeError, ValueError):
            return format_duration
    video_duration = stream_duration(video)
    audio_duration = stream_duration(audio)
    try:
        frame_rate = float(Fraction(video.get("avg_frame_rate") or video.get("r_frame_rate")))
    except (ValueError, ZeroDivisionError):
        frame_rate = 0.0

    checks = [
        check("画幅", video.get("width") == args.width and video.get("height") == args.height,
              {"width": video.get("width"), "height": video.get("height")},
              "%d×%d" % (args.width, args.height)),
        check("帧率", abs(frame_rate - args.fps) <= 0.02, round(frame_rate, 3),
              "%.3f fps ±0.02" % args.fps),
        check("音视频时长差", abs(video_duration - audio_duration) <= args.max_av_drift,
              round(abs(video_duration - audio_duration), 3),
              "≤ %.3f 秒" % args.max_av_drift),
    ]

    _, stderr, _ = capture([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-",
    ], allow_failure=True)
    mean_match = re.search(r"mean_volume:\s*(-?[\d.]+|-[iI]nf) dB", stderr)
    peak_match = re.search(r"max_volume:\s*(-?[\d.]+|-[iI]nf) dB", stderr)
    peak = float(peak_match.group(1)) if peak_match and "inf" not in peak_match.group(1).lower() else None
    mean = float(mean_match.group(1)) if mean_match and "inf" not in mean_match.group(1).lower() else None
    checks.append(check(
        "音轨峰值",
        peak is not None and peak <= args.peak_limit_db,
        peak,
        "≤ %.1f dBFS" % args.peak_limit_db,
    ))

    failures = [item for item in checks if item["status"] == "fail"]
    report = {
        "video": str(path),
        "status": "fail" if failures else "pass",
        "duration_s": round(format_duration, 3),
        "audio": {"mean_volume_db": mean, "peak_volume_db": peak},
        "checks": checks,
        "visual_review": "not_checked",
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report:
        report_path = Path(args.report).expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
