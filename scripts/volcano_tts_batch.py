#!/usr/bin/env python3
"""小无相功 · 火山引擎 TTS 批量配音（路线 B）

接口：火山引擎「语音合成」HTTP 非流式
  POST https://openspeech.bytedance.com/api/v1/tts
  Header: Authorization: Bearer;${access_token}     ← 分号分隔，不是空格，写错必鉴权失败
  Body:   app{appid,token,cluster} / user{uid} / audio{...} / request{reqid,text,operation}
  返回  : JSON（音频 base64 在 data），code==3000 为成功

凭据不进源码/仓库，走环境变量或 --config（JSON）：
  VOLC_TTS_APPID          控制台应用的 AppID
  VOLC_TTS_ACCESS_TOKEN   控制台应用的 Access Token
  VOLC_TTS_CLUSTER        业务集群，默认 volcano_tts
  VOLC_TTS_VOICE          音色 voice_type（须已在控制台开通/授权）

用法：
  # 0) 冒烟自检：一次验证 appid / token / 音色 三者是否都对（强烈建议先跑）
  python3 volcano_tts_batch.py --check

  # 1) 逐段合成 mp3
  python3 volcano_tts_batch.py segments.json --out tts/output --speed 1.15

  # 2) 拼主音轨（段间 0.3s）+ 音量归一（mean→-25dB，峰值 < -3dB）
  python3 volcano_tts_batch.py segments.json --out tts/output \
      --master tts/output/master.mp3 --normalize

输入 segments JSON：[{"id": "S01", "text": "..."}, ...]
输出：tts/output/<id>.mp3 + generation_log.json（参数、时长、logid）

限制与坑：
  - request.text 上限 1024 字节（UTF-8），建议 <300 字 —— 按叙事段切分天然满足
  - "豆包语音合成模型2.0"音色（*_uranus_bigtts 等）v1 接口不支持，需改用 v3 接口
  - 错误码 3003/3005/3030/3031/3032/3040 可重试，脚本内置指数退避
  - 音色报 access denied = 该音色未在控制台下单/授权（≠ 鉴权失败）
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

API_URL = "https://openspeech.bytedance.com/api/v1/tts"
DEFAULT_CLUSTER = "volcano_tts"
DEFAULT_VOICE = "zh_male_M392_conversation_wvae_bigtts"  # 官方请求示例音色；须已授权
MAX_TEXT_BYTES = 1024
RETRYABLE = {3003, 3005, 3030, 3031, 3032, 3040}
MAX_ATTEMPTS = 4

CODE_HINT = {
    3001: "参数非法（如 operation 配置错误）→ 检查调用参数",
    3003: "并发超限 → 降低并发或重试",
    3005: "后端服务忙 → 重试",
    3006: "服务中断（同一 reqid 重复请求）→ 换新 reqid",
    3010: "文本长度超限 → 缩短该段文本",
    3011: "无效文本（空/纯标点/语种不匹配）→ 检查该段文本",
    3030: "处理超时 → 重试或缩短文本",
    3031: "后端异常 → 重试",
    3032: "等待音频超时 → 重试",
    3040: "后端链路连接错误 → 重试",
    3050: "音色不存在 → 检查 voice_type 代号拼写",
}

MSG_HINT = (
    ("quota exceeded for types: concurrency", "并发超过限定值 → 降低并发或增购并发"),
    ("quota exceeded", "试用版用量已用尽 → 需在控制台开通正式版"),
    ("Init Engine Instance failed", "voice_type / cluster 传递错误 → 核对两者取值"),
    ("illegal input text", "文本无可合成内容（纯标点/emoji/语种不符）→ 检查该段文本"),
    ("requested grant not found", "鉴权失败 → 核对 appid/access_token，且 header 必须是 Bearer;TOKEN"),
    ("access denied", "当前音色未授权 → 需在控制台购买/下单该音色（免费音色也需 0 元下单）"),
)


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def load_config(args):
    cfg = {}
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
    appid = args.appid or cfg.get("appid") or os.environ.get("VOLC_TTS_APPID")
    token = args.token or cfg.get("access_token") or os.environ.get("VOLC_TTS_ACCESS_TOKEN")
    cluster = args.cluster or cfg.get("cluster") or os.environ.get("VOLC_TTS_CLUSTER") or DEFAULT_CLUSTER
    voice = args.voice or cfg.get("voice") or os.environ.get("VOLC_TTS_VOICE") or ""
    return appid, token, cluster, voice


def require_creds(appid, token, voice):
    missing = []
    if not appid:
        missing.append("VOLC_TTS_APPID")
    if not token:
        missing.append("VOLC_TTS_ACCESS_TOKEN")
    if not voice:
        missing.append("VOLC_TTS_VOICE")
    if missing:
        log("缺少必需凭据：" + "、".join(missing))
        log("设置环境变量，或用 --appid/--token/--voice 传入，或 --config creds.json。")
        log("凭据获取：火山引擎控制台 → 语音技术 → 语音合成 → 应用管理（AppID / Access Token）与音色列表。")
        sys.exit(2)


def synthesize(text, appid, token, cluster, voice, uid, speed, loudness,
               encoding, timestamp, timeout):
    """单次合成。返回 (audio_bytes, duration_ms, logid, raw)。"""
    payload = {
        "app": {"appid": appid, "token": token, "cluster": cluster},
        "user": {"uid": uid},
        "audio": {"voice_type": voice, "encoding": encoding,
                  "speed_ratio": speed, "loudness_ratio": loudness},
        "request": {"reqid": str(uuid.uuid4()), "text": text, "operation": "query"},
    }
    if timestamp:
        payload["request"]["with_timestamp"] = 1

    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            # 分号分隔；写成 "Bearer TOKEN"（空格）会直接鉴权失败
            headers={"Authorization": "Bearer;" + token,
                     "Content-Type": "application/json"},
            method="POST",
        )
        http_status = 200
        logid = ""
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                logid = resp.headers.get("X-Tt-Logid", "")
                body_text = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            # 鉴权/参数失败时火山返回 HTTP 4xx + JSON body，code/message 在 body 里，
            # 必须解析 body，否则既丢诊断信息、又会把不可重试的错误反复重试。
            http_status = e.code
            logid = e.headers.get("X-Tt-Logid", "") if e.headers else ""
            body_text = e.read().decode("utf-8", "replace")
        except Exception as e:  # 网络抖动，可重试
            last_err = "%s: %s" % (type(e).__name__, e)
            body_text = None

        if body_text is not None:
            try:
                body = json.loads(body_text)
            except ValueError:
                body = None

            if body is not None:
                code = body.get("code")
                if code == 3000 and body.get("data"):
                    audio = base64.b64decode(body["data"])
                    dur = (body.get("addition") or {}).get("duration")
                    return audio, dur, logid, body

                msg = str(body.get("message", ""))
                hint = CODE_HINT.get(code, "")
                for pat, h in MSG_HINT:
                    if pat in msg:
                        hint = h
                        break
                last_err = "HTTP %s code=%s message=%s" % (http_status, code, msg)
                if code not in RETRYABLE:
                    log("合成失败（不可重试）：%s" % last_err)
                    if hint:
                        log("建议：" + hint)
                    if logid:
                        log("X-Tt-Logid: " + logid)
                    sys.exit(1)
            else:
                last_err = "HTTP %s 响应非 JSON：%s" % (http_status, body_text[:200])

        if attempt < MAX_ATTEMPTS:
            wait = 2 ** attempt
            log("第 %d 次失败：%s —— %ds 后重试" % (attempt, last_err, wait))
            time.sleep(wait)

    log("重试 %d 次仍失败：%s" % (MAX_ATTEMPTS, last_err))
    sys.exit(1)


def ffprobe_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path], capture_output=True, text=True)
        return round(float(r.stdout.strip()), 2)
    except Exception:
        return -1.0


def volumedetect(path):
    """返回 (mean_dB, max_dB)，失败返回 (None, None)。"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", path,
                        "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    out = r.stderr
    mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", out)
    peak = re.search(r"max_volume:\s*(-?[\d.]+) dB", out)
    return ((float(mean.group(1)) if mean else None),
            (float(peak.group(1)) if peak else None))


def build_master(files, gap, sample_rate, out_path):
    """用 concat demuxer 拼接（段间静音 gap 秒），同参 mp3 可无损 copy。"""
    workdir = os.path.dirname(os.path.abspath(out_path)) or "."
    gapfile = os.path.join(workdir, "_gap.mp3")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "anullsrc=r=%d:cl=mono" % sample_rate,
                    "-t", str(gap), "-c:a", "libmp3lame", "-b:a", "160k", gapfile],
                   check=True)
    listfile = os.path.join(workdir, "_concat.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for i, p in enumerate(files):
            if i:
                f.write("file '%s'\n" % gapfile.replace("'", "'\\''"))
            f.write("file '%s'\n" % os.path.abspath(p).replace("'", "'\\''"))
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", listfile,
                    "-c", "copy", out_path], check=True)
    for tmp in (gapfile, listfile):
        if os.path.exists(tmp):
            os.remove(tmp)


def normalize(in_path, out_path, target_mean, limit_linear):
    mean, peak = volumedetect(in_path)
    if mean is None:
        log("volumedetect 读取失败，跳过归一")
        return None, None
    gain = target_mean - mean
    # level=0 关掉 alimiter 的自动电平，保证结果可复现；limit 0.708 ≈ -3dBFS
    af = "volume=%.2fdB,alimiter=limit=%.3f:level=0" % (gain, limit_linear)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", in_path, "-af", af, "-c:a", "libmp3lame",
                    "-b:a", "192k", out_path], check=True)
    new_mean, new_peak = volumedetect(out_path)
    return (mean, peak), (new_mean, new_peak)


def main():
    ap = argparse.ArgumentParser(description="火山引擎 TTS 批量配音（小无相功 路线 B）")
    ap.add_argument("segments", nargs="?", help="segments.json")
    ap.add_argument("--out", help="输出目录")
    ap.add_argument("--check", action="store_true", help="冒烟自检：合成一句短文本，验证凭据与音色")
    ap.add_argument("--config", help="凭据 JSON（appid/access_token/cluster/voice）")
    ap.add_argument("--appid")
    ap.add_argument("--token")
    ap.add_argument("--cluster")
    ap.add_argument("--voice", help="音色 voice_type，如 zh_male_M392_conversation_wvae_bigtts")
    ap.add_argument("--uid", default="xiaowuxianggong")
    ap.add_argument("--speed", type=float, default=1.0, help="语速 0.1–2；短视频口播约 1.15")
    ap.add_argument("--loudness", type=float, default=1.0, help="音量 0.5–2")
    ap.add_argument("--encoding", default="mp3", choices=["mp3", "wav", "pcm", "ogg_opus"])
    ap.add_argument("--timestamp", action="store_true",
                    help="请求字级时间戳（with_timestamp=1），原始响应另存 tts_raw/<id>.json")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--master", help="拼接主音轨输出路径，如 tts/output/master.mp3")
    ap.add_argument("--normalize", action="store_true", help="对主音轨做音量归一（需 --master）")
    ap.add_argument("--gap", type=float, default=0.3, help="段间静音秒数，默认 0.3")
    ap.add_argument("--sample-rate", type=int, default=24000, help="段间静音采样率，需与音频一致")
    ap.add_argument("--target-mean", type=float, default=-25.0, help="归一目标 mean_volume (dB)")
    args = ap.parse_args()

    appid, token, cluster, voice = load_config(args)
    require_creds(appid, token, voice)

    # 冒烟自检
    if args.check:
        audio, dur, logid, _ = synthesize("小无相功配音自检。", appid, token, cluster,
                                          voice, args.uid, 1.0, 1.0, "mp3", False, args.timeout)
        log("自检通过：音色 %s，返回 %d 字节，declared duration=%s ms，X-Tt-Logid=%s"
            % (voice, len(audio), dur, logid))
        return

    for name, val in (("--speed", args.speed), ("--loudness", args.loudness)):
        if not 0.1 <= val <= 2.0:
            log("%s 超出范围 [0.1, 2.0]：%s" % (name, val))
            sys.exit(2)

    if not args.segments or not args.out:
        ap.error("需要 segments.json 与 --out（或用 --check 做自检）")

    segs = json.load(open(args.segments, encoding="utf-8"))
    os.makedirs(args.out, exist_ok=True)
    raw_dir = os.path.join(os.path.dirname(os.path.abspath(args.out)), "tts_raw")
    if args.timestamp:
        os.makedirs(raw_dir, exist_ok=True)

    log_entry = {
        "backend": "volcengine-tts/v1-http",
        "cluster": cluster, "voice": voice, "speed_ratio": args.speed,
        "loudness_ratio": args.loudness, "encoding": args.encoding,
        "with_timestamp": bool(args.timestamp), "segments": [],
    }

    written = []
    for s in segs:
        sid, text = s["id"], s["text"]
        nbytes = len(text.encode("utf-8"))
        if nbytes > MAX_TEXT_BYTES:
            log("段 %s 文本 %d 字节，超过 %d 字节上限 → 请再切分该段" % (sid, nbytes, MAX_TEXT_BYTES))
            sys.exit(1)
        if nbytes > 900:
            log("警告：段 %s 文本 %d 字节（>300 字易增 badcase），建议再切分" % (sid, nbytes))

        audio, dur, logid, raw = synthesize(text, appid, token, cluster, voice, args.uid,
                                            args.speed, args.loudness, args.encoding,
                                            args.timestamp, args.timeout)
        out_path = os.path.join(args.out, "%s.%s" % (sid, args.encoding))
        with open(out_path, "wb") as f:
            f.write(audio)
        measured = ffprobe_duration(out_path)
        if args.timestamp:
            with open(os.path.join(raw_dir, "%s.json" % sid), "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)

        log_entry["segments"].append({
            "id": sid, "text": text, "file": out_path,
            "duration_s": measured,
            "api_duration_ms": dur, "chars": len(text), "bytes": nbytes, "logid": logid,
        })
        written.append(out_path)
        log("%s: %ss (%d 字)" % (sid, measured, len(text)))

    log_entry["total_duration_s"] = round(
        sum(x["duration_s"] for x in log_entry["segments"]), 2)
    log("逐段合计：%ss" % log_entry["total_duration_s"])

    if args.master:
        build_master(written, args.gap, args.sample_rate, args.master)
        master_dur = ffprobe_duration(args.master)
        log_entry["master"] = {"file": args.master, "gap_s": args.gap,
                               "duration_s": master_dur}
        log("主音轨：%s（%.2fs，含 %d 处 %.1fs 静音）"
            % (args.master, master_dur, len(written) - 1, args.gap))
        if args.normalize:
            norm_path = os.path.splitext(args.master)[0] + "_norm.mp3"
            before, after = normalize(args.master, norm_path,
                                      args.target_mean, 10 ** (-3.0 / 20))
            if before:
                log("归一：mean %s→%s dB，peak %s→%s dB"
                    % (before[0], after[0], before[1], after[1]))
                log_entry["master"]["normalized"] = {
                    "file": norm_path, "target_mean_db": args.target_mean,
                    "before": {"mean_db": before[0], "peak_db": before[1]},
                    "after": {"mean_db": after[0], "peak_db": after[1]},
                }
    elif args.normalize:
        log("--normalize 需与 --master 同时使用，已忽略")

    with open(os.path.join(args.out, "generation_log.json"), "w", encoding="utf-8") as f:
        json.dump(log_entry, f, ensure_ascii=False, indent=2)
    log("已写 %s/generation_log.json" % args.out)


if __name__ == "__main__":
    main()
