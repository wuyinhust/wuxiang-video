#!/usr/bin/env python3
"""Create short Volcano Engine OmniHuman 1.5 presenter clips for Remotion."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API_HOST = "visual.volcengineapi.com"
API_REGION = "cn-beijing"
API_VERSION = "2024-06-06"
REQ_KEY = "jimeng_realman_avatar_picture_omni_v15"
MAX_AUDIO_SECONDS = 35.0
DEFAULT_CHUNK_SECONDS = 30.0
TOS_URL_TTL_SECONDS = 21600


def fail(message: str) -> None:
    print("错误：" + message, file=sys.stderr)
    raise SystemExit(2)


def require_https(url: str, label: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        fail("%s 必须是可公开读取的 HTTPS URL。" % label)
    return url


def run_capture(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        fail("找不到命令：%s（需安装 ffmpeg / ffprobe）。" % argv[0])
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()[-1200:]
        fail("%s 执行失败：%s" % (argv[0], detail))
    return result.stdout.strip()


def media_duration(path: Path) -> float:
    raw = run_capture([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    try:
        return float(raw)
    except ValueError:
        fail("无法读取音频时长：%s" % path)
    return 0.0


def read_timeline(path: Path, duration: float) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("无法读取字幕时间线：%s" % exc)
    sentences = data.get("sentences")
    if not isinstance(sentences, list) or not sentences:
        fail("时间线必须含非空 sentences 数组。")
    result = []
    previous_start = -1.0
    for index, item in enumerate(sentences, 1):
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            fail("时间线第 %d 句缺少有效 start/end。" % index)
        if start < 0 or end <= start or start < previous_start or end > duration + 0.25:
            fail("时间线第 %d 句的起止时间无效或超出音频时长。" % index)
        previous_start = start
        result.append({"start": start, "end": end, "text": str(item.get("text", ""))})
    return result


def make_chunks(duration: float, timeline: list[dict] | None,
                max_chunk: float) -> list[tuple[float, float]]:
    if max_chunk <= 0 or max_chunk >= MAX_AUDIO_SECONDS:
        fail("--max-chunk-seconds 必须大于 0 且小于 35。")
    if duration < MAX_AUDIO_SECONDS:
        return [(0.0, duration)]
    if not timeline:
        fail("音频达到 35 秒，需要 --timeline；请按句界分段，不能在句中硬切。")

    for sentence in timeline:
        if sentence["end"] - sentence["start"] > max_chunk:
            fail("存在超过 %.1f 秒的单句，请先拆短口播句子。" % max_chunk)

    chunks = []
    chunk_start = 0.0
    last_sentence = timeline[-1]
    while duration - chunk_start > max_chunk:
        boundary = chunk_start + max_chunk
        # 若上限落在一句内部，提前移到该句开头，不切断口播。
        containing = next(
            (sentence for sentence in timeline
             if sentence["start"] < boundary < sentence["end"]),
            None,
        )
        if containing:
            boundary = containing["start"]
            if boundary <= chunk_start:
                fail("当前片段没有可用的句界；请先拆短口播句子。")
        # 避免最后一段只剩长静音：把最后一句整体留在末段。
        elif boundary > last_sentence["end"]:
            boundary = last_sentence["start"]
            if boundary <= chunk_start:
                fail("末尾静音导致最后一段超过 %.1f 秒；请先裁掉尾部静音。" % max_chunk)
        if boundary - chunk_start <= 0 or boundary - chunk_start > max_chunk:
            fail("无法在不切断口播的情况下分段；请先拆短口播句子或裁掉静音。")
        chunks.append((chunk_start, boundary))
        chunk_start = boundary

    chunks.append((chunk_start, duration))
    if any(end <= start or end - start > max_chunk or end - start >= MAX_AUDIO_SECONDS
           for start, end in chunks):
        fail("切分后仍有音频段超过 %.1f 秒；请检查时间线或裁掉尾部静音。" % max_chunk)
    return chunks


def hmac_sha256(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def sign_request(body: bytes, action: str, access_key: str, secret_key: str) -> urllib.request.Request:
    query = urllib.parse.urlencode(
        sorted([("Action", action), ("Version", API_VERSION)]),
        quote_via=urllib.parse.quote,
        safe="~",
    )
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    date = now[:8]
    payload_hash = hashlib.sha256(body).hexdigest()
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_headers = (
        "content-type:application/json\n"
        "host:%s\n" % API_HOST
    ) + "x-content-sha256:%s\nx-date:%s\n" % (payload_hash, now)
    canonical_request = (
        "POST\n/\n%s\n%s\n%s\n%s"
        % (query, canonical_headers, signed_headers, payload_hash)
    )
    scope = "%s/%s/cv/request" % (date, API_REGION)
    string_to_sign = "HMAC-SHA256\n%s\n%s\n%s" % (
        now, scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    )
    signing_key = hmac_sha256(
        hmac_sha256(hmac_sha256(hmac_sha256(secret_key.encode("utf-8"), date),
                                API_REGION), "cv"), "request"
    )
    signature = hmac_sha256(signing_key, string_to_sign).hex()
    authorization = (
        "HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
        % (access_key, scope, signed_headers, signature)
    )
    url = "https://%s/?%s" % (API_HOST, query)
    return urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Host": API_HOST,
            "X-Date": now,
            "X-Content-Sha256": payload_hash,
            "Authorization": authorization,
        },
    )


def cv_call(action: str, payload: dict, access_key: str, secret_key: str,
            timeout: int = 60) -> dict:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = sign_request(body, action, access_key, secret_key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1200]
        fail("火山 API 返回 HTTP %s：%s" % (exc.code, detail))
    except (urllib.error.URLError, TimeoutError) as exc:
        fail("无法连接火山视觉 API：%s" % type(exc).__name__)
    except json.JSONDecodeError:
        fail("火山视觉 API 返回内容不是 JSON。")
    code = result.get("code")
    if code not in (None, 0, 10000, "0", "10000"):
        fail("火山 API 错误 code=%s message=%s" % (code, result.get("message", "")))
    return result


def make_tos():
    names = ("VOLC_ACCESSKEY", "VOLC_SECRETKEY", "VOLC_TOS_BUCKET",
             "VOLC_TOS_REGION", "VOLC_TOS_ENDPOINT")
    cfg = {name: os.environ.get(name, "").strip() for name in names}
    missing = [name for name, value in cfg.items() if not value]
    if missing:
        fail("本地文件自动上传需要独立 TOS 配置：" + "、".join(missing))
    try:
        import tos
        from tos.enum import HttpMethodType
    except ImportError:
        fail("本地文件上传需先安装 requirements-digital-human.txt 中的 TOS Python SDK。")
    client = tos.TosClientV2(
        cfg["VOLC_ACCESSKEY"], cfg["VOLC_SECRETKEY"],
        cfg["VOLC_TOS_ENDPOINT"], cfg["VOLC_TOS_REGION"],
    )
    return client, HttpMethodType, cfg["VOLC_TOS_BUCKET"]


def tos_upload(client, method_type, bucket: str, path: Path, prefix: str) -> tuple[str, str]:
    key = "%s/%s/%s" % (
        prefix, uuid.uuid4().hex, path.name,
    )
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as stream:
        client.put_object(bucket, key, content=stream.read(), content_type=content_type)
    signed = client.pre_signed_url(
        method_type.Http_Method_Get, bucket=bucket, key=key,
        expires=TOS_URL_TTL_SECONDS,
    )
    return signed.signed_url, key


def task_video_url(result: dict) -> str:
    data = result.get("data") or {}
    raw = data.get("resp_data")
    nested = {}
    if isinstance(raw, str):
        try:
            nested = json.loads(raw)
        except json.JSONDecodeError:
            nested = {}
    elif isinstance(raw, dict):
        nested = raw
    code = nested.get("code")
    if code not in (None, 0, "0", 10000, "10000"):
        fail("数字人任务失败 code=%s message=%s" % (code, nested.get("msg", "")))
    urls = [
        nested.get("video_url"),
        data.get("video_url"),
        nested.get("url"),
        data.get("url"),
    ]
    for candidate in urls:
        if isinstance(candidate, str) and candidate.startswith("https://"):
            return candidate
    previews = nested.get("preview_url") or data.get("preview_url") or []
    if isinstance(previews, str):
        previews = [previews]
    for candidate in previews:
        if isinstance(candidate, str) and candidate.startswith("https://"):
            return candidate
    urls = nested.get("urls") or data.get("urls") or []
    for candidate in urls:
        if isinstance(candidate, str) and candidate.startswith("https://"):
            return candidate
    fail("任务完成，但响应中没有可下载的 HTTPS 视频地址。")
    return ""


def submit_and_wait(image_url: str, audio_url: str, access_key: str, secret_key: str,
                    resolution: int, prompt: str, seed: int, wait_seconds: int) -> str:
    payload = {
        "req_key": REQ_KEY,
        "image_url": image_url,
        "audio_url": audio_url,
        "output_resolution": resolution,
        "pe_fast_mode": resolution == 720,
        "seed": seed,
    }
    if prompt:
        payload["prompt"] = prompt
    submitted = cv_call("CVSubmitTask", payload, access_key, secret_key)
    task_id = (submitted.get("data") or {}).get("task_id")
    if not task_id:
        fail("提交成功响应中没有 task_id。")
    print("已提交数字人任务：%s" % task_id, flush=True)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(5)
        result = cv_call(
            "CVGetResult", {"req_key": REQ_KEY, "task_id": task_id},
            access_key, secret_key,
        )
        data = result.get("data") or {}
        status = str(data.get("status", "")).lower()
        if status in ("in_queue", "generating", "queued", "running"):
            print("数字人任务状态：%s" % status, flush=True)
            continue
        if status == "done":
            return task_video_url(result)
        if status in ("failed", "expired", "not_found", "cancelled"):
            fail("数字人任务状态为 %s；task_id=%s" % (status, task_id))
        if status:
            print("数字人任务状态：%s" % status, flush=True)
    fail("等待数字人任务超时；如需排查，保留 task_id 和 TOS 上传对象，task_id=%s" % task_id)
    return ""


def download_video(url: str, target: Path) -> None:
    require_https(url, "生成结果")
    request = urllib.request.Request(url, headers={"User-Agent": "xiaowuxianggong-avatar/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
    except Exception as exc:
        fail("下载数字人视频失败：%s" % type(exc).__name__)


def strip_audio(source: Path, target: Path) -> None:
    run_capture([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-map", "0:v:0", "-c:v", "copy",
        "-an", "-movflags", "+faststart", str(target),
    ])
    source.unlink(missing_ok=True)


def write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="火山引擎 OmniHuman 1.5：把已授权肖像与最终配音生成数字人片段。"
    )
    parser.add_argument("--portrait", help="本地肖像图片；本地文件会上传到私有 TOS")
    parser.add_argument("--image-url", help="已有公网 HTTPS 肖像图 URL")
    parser.add_argument("--audio", help="本地最终配音；超过 35 秒须提供 --timeline")
    parser.add_argument("--audio-url", help="已有公网 HTTPS 音频 URL；限单段小于 35 秒")
    parser.add_argument("--duration-s", type=float, help="仅 audio-url 模式必填，用于校验 35 秒上限")
    parser.add_argument("--timeline", help="align_subtitles.py 生成的 timeline.json")
    parser.add_argument("--out", default="out/digital-human", help="输出目录")
    parser.add_argument("--resolution", type=int, choices=(720, 1080), default=1080)
    parser.add_argument("--max-chunk-seconds", type=float, default=DEFAULT_CHUNK_SECONDS)
    parser.add_argument("--prompt", default="", help="可选中文提示词")
    parser.add_argument("--seed", type=int, default=-1)
    parser.add_argument("--wait-seconds", type=int, default=1800)
    args = parser.parse_args()

    if bool(args.portrait) == bool(args.image_url):
        fail("必须且只能提供 --portrait 或 --image-url。")
    if bool(args.audio) == bool(args.audio_url):
        fail("必须且只能提供 --audio 或 --audio-url。")
    if args.image_url:
        image_url = require_https(args.image_url, "肖像图")
    if args.audio_url:
        audio_url = require_https(args.audio_url, "音频")
        if args.duration_s is None or args.duration_s <= 0 or args.duration_s >= MAX_AUDIO_SECONDS:
            fail("audio-url 模式必须用 --duration-s 提供小于 35 秒的音频时长。")
        if args.timeline:
            fail("audio-url 模式不支持本地句界切分；长音频请用 --audio 本地文件。")

    access_key = os.environ.get("VOLC_ACCESSKEY", "").strip()
    secret_key = os.environ.get("VOLC_SECRETKEY", "").strip()
    if not access_key or not secret_key:
        fail("数字人 API 需要火山引擎 AK/SK：设置 VOLC_ACCESSKEY 和 VOLC_SECRETKEY。")

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = "xiaowuxianggong/avatar/%s" % uuid.uuid4().hex
    tos_client = None
    method_type = None
    bucket = None
    uploaded_keys = []
    if args.portrait or args.audio:
        tos_client, method_type, bucket = make_tos()

    if args.portrait:
        portrait = Path(args.portrait).expanduser().resolve()
        if not portrait.is_file():
            fail("肖像文件不存在：%s" % portrait)
        image_url, key = tos_upload(tos_client, method_type, bucket, portrait, prefix)
        uploaded_keys.append(key)

    chunks: list[tuple[float, float, Path | None, str | None]] = []
    temp_context = tempfile.TemporaryDirectory(prefix="wuxiang-avatar-")
    temp_dir = Path(temp_context.name)
    try:
        if args.audio:
            audio_path = Path(args.audio).expanduser().resolve()
            if not audio_path.is_file():
                fail("音频文件不存在：%s" % audio_path)
            duration = media_duration(audio_path)
            timeline = read_timeline(Path(args.timeline), duration) if args.timeline else None
            spans = make_chunks(duration, timeline, args.max_chunk_seconds)
            for index, (start, end) in enumerate(spans, 1):
                if len(spans) == 1:
                    local_audio = audio_path
                else:
                    local_audio = temp_dir / ("audio-%03d.wav" % index)
                    run_capture([
                        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(audio_path), "-ss", "%.3f" % start,
                        "-t", "%.3f" % (end - start), "-vn",
                        "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le",
                        str(local_audio),
                    ])
                actual = media_duration(local_audio)
                if actual >= MAX_AUDIO_SECONDS:
                    fail("第 %d 段实测时长 %.2f 秒，达到接口 35 秒上限。" % (index, actual))
                url, key = tos_upload(tos_client, method_type, bucket, local_audio, prefix)
                uploaded_keys.append(key)
                chunks.append((start, end, local_audio, url))
        else:
            chunks.append((0.0, float(args.duration_s), None, args.audio_url))

        manifest = {
            "provider": "volcengine",
            "model": "OmniHuman 1.5",
            "req_key": REQ_KEY,
            "region": API_REGION,
            "api_version": API_VERSION,
            "resolution": args.resolution,
            "segments": [],
        }
        manifest_path = out_dir / "avatar_manifest.json"
        write_manifest(manifest_path, manifest)

        for index, (start, end, _local_audio, current_audio_url) in enumerate(chunks, 1):
            if current_audio_url is None:
                raise RuntimeError("音频 URL 未准备")
            result_url = submit_and_wait(
                image_url, current_audio_url, access_key, secret_key,
                args.resolution, args.prompt, args.seed, args.wait_seconds,
            )
            target = out_dir / ("avatar-%03d.mp4" % index)
            raw_target = out_dir / ("avatar-%03d.generated.mp4" % index)
            download_video(result_url, raw_target)
            strip_audio(raw_target, target)
            manifest["segments"].append({
                "id": "avatar-%03d" % index,
                "start_s": round(start, 3),
                "end_s": round(end, 3),
                "video_file": target.name,
            })
            write_manifest(manifest_path, manifest)
            print("已保存：%s" % target)
        print("清单：%s" % manifest_path)

        for key in uploaded_keys:
            try:
                tos_client.delete_object(bucket, key)
            except Exception:
                print("提示：输入对象暂未删除，请按 TOS 生命周期规则清理：%s" % key,
                      file=sys.stderr)
        return 0
    finally:
        temp_context.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
