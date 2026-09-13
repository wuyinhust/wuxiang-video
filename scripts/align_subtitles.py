#!/usr/bin/env python3
"""小无相功 · 字幕对齐器

铁律：字幕文本 = 原稿，时间 = 真实语音词级时间戳。绝不用 ASR 原文做字幕。

输入：
  --transcript  faster-whisper 词级转写 JSON（含 segments[].words[]: {w, start, end}，
                或扁平 words[]）
  --script      原稿纯文本（口播稿，含标点；按标点切句）
输出：
  --out-srt       SRT 字幕
  --out-timeline  句级时间线 JSON（可选）

原理：原稿与转写全文做 difflib 字符级对齐 → 把词级时间戳映射回原稿字符 →
按原稿标点切句取时间窗。不按字数均分。
"""
import argparse, difflib, json, re, sys

CJK_PUNCT = "，。、！？：；·''\"\"—…「」"
SENT_END = "。！？；…"


def norm(t: str) -> str:
    return re.sub(r"[%s\s\-\.,!?;:]" % re.escape(CJK_PUNCT), "", t).lower()


def fmt_srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def load_words(path: str):
    d = json.load(open(path))
    if "words" in d:  # 扁平
        return [(w["w"], float(w["start"]), float(w["end"])) for w in d["words"]]
    out = []
    for seg in d.get("segments", []):
        for w in seg.get("words", []) or []:
            out.append((w.get("w") or w.get("word"), float(w["start"]), float(w["end"])))
    if not out:  # 无词级时间戳时退化为段级
        for seg in d.get("segments", []):
            out.append((seg["text"], float(seg["start"]), float(seg["end"])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--script", required=True)
    ap.add_argument("--out-srt", required=True)
    ap.add_argument("--out-timeline")
    args = ap.parse_args()

    words = load_words(args.transcript)
    script = open(args.script, encoding="utf-8").read()

    # 转写全文（去标点）与逐字符时间
    asr_chars, asr_times = [], []
    for w, st, ed in words:
        t = norm(w)
        for ch in t:
            asr_chars.append(ch)
            asr_times.append((st, ed))

    script_norm = norm(script)
    sm = difflib.SequenceMatcher(None, script_norm, "".join(asr_chars), autojunk=False)

    # 原稿规范化字符 -> 时间（仅取 equal 块，错字处用邻近插值）
    char_times = [None] * len(script_norm)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                char_times[i1 + k] = asr_times[j1 + k]
    last = (0.0, 0.0)
    for i in range(len(char_times)):  # 前向填充
        if char_times[i] is None:
            char_times[i] = last
        else:
            last = char_times[i]

    # 原稿按标点切句，映射到规范化字符坐标
    entries, ni, cur, cur_start = [], 0, "", None
    for ch in script:
        if norm(ch):
            if cur_start is None:
                cur_start = ni
            ni += 1
            cur += ch
        elif ch in CJK_PUNCT and cur:
            cur += ch
        if ch in SENT_END and cur.strip():
            st = char_times[cur_start][0] if cur_start is not None else 0.0
            ed = char_times[min(ni - 1, len(char_times) - 1)][1] if ni > 0 else st
            entries.append({"start": round(st, 2), "end": round(ed, 2), "text": cur.strip()})
            cur, cur_start = "", None
    if cur.strip():
        st = char_times[cur_start][0] if cur_start is not None else 0.0
        ed = char_times[-1][1] if char_times else st
        entries.append({"start": round(st, 2), "end": round(ed, 2), "text": cur.strip()})

    with open(args.out_srt, "w", encoding="utf-8") as f:
        for i, e in enumerate(entries, 1):
            f.write(f"{i}\n{fmt_srt(e['start'])} --> {fmt_srt(e['end'])}\n{e['text']}\n\n")
    if args.out_timeline:
        json.dump({"sentences": entries,
                   "total_duration_s": entries[-1]["end"] if entries else 0},
                  open(args.out_timeline, "w"), ensure_ascii=False, indent=2)
    print(f"ok: {len(entries)} sentences -> {args.out_srt}", file=sys.stderr)


if __name__ == "__main__":
    main()
