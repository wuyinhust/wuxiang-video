#!/usr/bin/env python3
"""小无相功 · edge-tts 批量配音（路线 B）

输入 segments JSON：[{"id": "S01", "text": "..."}, ...]
逐段生成 mp3，写 generation_log.json（参数 + 实测时长，需 soundfile 或 ffprobe 测时长）。

用法：
  pip install -i https://pypi.org/simple edge-tts
  python3 align_subtitles.py 不需要；本脚本：
  python3 edgetts_batch.py segments.json --out tts/output [--voice zh-CN-YunxiNeural] [--rate +15%]

主音轨拼接（段间 0.3s，需 ffmpeg）：
  ffmpeg -i S01.mp3 -i S02.mp3 ... -f lavfi -t 0.3 -i anullsrc=r=44100:cl=stereo \
    -filter_complex "[0:a][N:a][1:a]...concat=n=..:v=0:a=1[out]" -map "[out]" master.mp3
音量归一：ffmpeg -i master.mp3 -af "volume=6.5dB,alimiter=limit=0.95" master_norm.mp3
"""
import argparse, asyncio, json, os, subprocess, sys

DEFAULT_VOICE = "zh-CN-YunxiNeural"  # 男声；女声备选 zh-CN-XiaoxiaoNeural
DEFAULT_RATE = "+15%"                # ≈4 字/秒，贴近短视频口播密度


async def gen(text: str, out: str, voice: str, rate: str):
    import edge_tts
    await edge_tts.Communicate(text, voice=voice, rate=rate).save(out)


def duration_of(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path], capture_output=True, text=True)
        return round(float(r.stdout.strip()), 2)
    except Exception:
        return -1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("segments")
    ap.add_argument("--out", required=True)
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--rate", default=DEFAULT_RATE)
    args = ap.parse_args()

    segs = json.load(open(args.segments, encoding="utf-8"))
    os.makedirs(args.out, exist_ok=True)
    log = {"voice": args.voice, "rate": args.rate, "segments": []}
    for s in segs:
        out = os.path.join(args.out, f"{s['id']}.mp3")
        asyncio.run(gen(s["text"], out, args.voice, args.rate))
        dur = duration_of(out)
        log["segments"].append({"id": s["id"], "text": s["text"],
                                "file": out, "duration_s": dur})
        print(f"{s['id']}: {dur}s", file=sys.stderr)
    log["total_duration_s"] = round(sum(x["duration_s"] for x in log["segments"]), 2)
    json.dump(log, open(os.path.join(args.out, "generation_log.json"), "w"),
              ensure_ascii=False, indent=2)
    print(f"total: {log['total_duration_s']}s", file=sys.stderr)


if __name__ == "__main__":
    main()
