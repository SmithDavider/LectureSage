#!/usr/bin/env python3
"""计算两份转写文本之间的字符错误率 (CER)，支持带时间戳和说话人前缀的标准文本。"""
import argparse
import itertools
import json
import re
from typing import Optional


LINE_RE = re.compile(
    r"^(?P<start>\d{1,2}):(?P<start_s>\d{2})"
    r"-(?P<end>\d{1,2}):(?P<end_s>\d{2})\s+"
    r"说话人(?P<spk>\d+)[：:]\s*(?P<text>.*)$"
)


def to_ms(minutes: str, seconds: str) -> int:
    return int(minutes) * 60000 + int(seconds) * 1000


def normalize(text: str) -> str:
    # 只保留汉字、字母和数字，忽略标点、空白和大小写差异
    text = re.sub(r"\s+", "", text)
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text).lower()


def edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        current = [i]
        for j, right_char in enumerate(right, 1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def load_reference(path: str, limit_ms: int = 0):
    """参考文本可以是普通文本，也可以是 时间戳+说话人 逐行格式。"""
    lines = open(path, encoding="utf-8").read().splitlines()
    segments = []
    text_lines = []
    for line in lines:
        match = LINE_RE.match(line.strip())
        if match:
            start_ms = to_ms(match.group("start"), match.group("start_s"))
            end_ms = to_ms(match.group("end"), match.group("end_s"))
            if limit_ms and start_ms >= limit_ms:
                continue
            segments.append(
                {
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "spk": int(match.group("spk")),
                    "text": match.group("text"),
                }
            )
            text_lines.append(match.group("text"))
        elif line.strip() and not segments:
            text_lines.append(line)
    return segments, "\n".join(text_lines)


def speaker_accuracy(segments, chunks_path: str, limit_ms: int = 0) -> Optional[float]:
    """将标准时间轴与 ASR chunks 时间轴按帧对齐，做说话人标签最大匹配后算准确率。"""
    if not segments:
        return None
    chunks = [
        json.loads(line)
        for line in open(chunks_path, encoding="utf-8")
        if line.strip()
    ]
    chunks = [c for c in chunks if "spk" in c]
    if limit_ms:
        chunks = [c for c in chunks if c["start_ms"] < limit_ms]
    if not chunks:
        return None

    step_ms = 500
    end_ms = max(seg["end_ms"] for seg in segments)
    mins = list(range(0, end_ms, step_ms))
    truth = [None] * len(mins)
    for seg in segments:
        start_i = seg["start_ms"] // step_ms
        end_i = min(len(mins), -(-seg["end_ms"] // step_ms))
        for i in range(start_i, end_i):
            truth[i] = seg["spk"]

    pred = [None] * len(mins)
    for chunk in chunks:
        start_i = chunk["start_ms"] // step_ms
        end_i = min(len(mins), -(-chunk["end_ms"] // step_ms))
        for i in range(start_i, end_i):
            pred[i] = chunk["spk"] + 1

    covered = [i for i in range(len(mins)) if truth[i] is not None]
    if not covered:
        return None
    truth_ids = sorted({truth[i] for i in covered})
    pred_ids = sorted({pred[i] for i in covered if pred[i] is not None})
    if not pred_ids:
        return None

    best_total = 0
    for mapping in itertools.permutations(pred_ids, min(len(pred_ids), len(truth_ids))):
        mapping = dict(zip(mapping, truth_ids))
        total = sum(1 for i in covered if pred[i] is not None and mapping.get(pred[i]) == truth[i])
        best_total = max(best_total, total)
    return best_total / len(covered)


def main():
    parser = argparse.ArgumentParser(description="compare two transcripts")
    parser.add_argument("--reference", required=True, help="standard transcript")
    parser.add_argument("--hypothesis", required=True, help="ASR output")
    parser.add_argument("--chunks", help="optional chunks.jsonl from ASR with speaker labels")
    parser.add_argument(
        "--limit-ms",
        type=int,
        default=0,
        help="仅评测前 N 毫秒（需要 --chunks 提供时间信息）",
    )
    args = parser.parse_args()

    segments, reference_text = load_reference(args.reference, args.limit_ms)
    reference = normalize(reference_text)
    if args.limit_ms:
        if not args.chunks:
            raise SystemExit("--limit-ms 需要同时提供 --chunks")
        hypothesis_lines = []
        for line in open(args.chunks, encoding="utf-8"):
            if not line.strip():
                continue
            chunk = json.loads(line)
            if chunk.get("start_ms", 0) < args.limit_ms:
                hypothesis_lines.append(chunk.get("text", ""))
        hypothesis_text = "\n".join(hypothesis_lines)
    else:
        hypothesis_text = open(args.hypothesis, encoding="utf-8").read()
    hypothesis = normalize(hypothesis_text)
    distance = edit_distance(reference, hypothesis)
    reference_len = max(1, len(reference))
    cer = distance / reference_len

    print(f"reference_chars={len(reference)}")
    print(f"hypothesis_chars={len(hypothesis)}")
    print(f"edits={distance}")
    print(f"cer={cer:.4f}")
    print(f"accuracy={(1 - cer):.4f}")
    if segments:
        print(f"reference_segments={len(segments)}")
        print(f"reference_speakers={sorted(set(s['spk'] for s in segments))}")
        print(f"reference_seconds={max(s['end_ms'] for s in segments) / 1000:.1f}")
    if args.chunks:
        spk_acc = speaker_accuracy(segments, args.chunks, args.limit_ms)
        print(
            f"speaker_accuracy={spk_acc:.4f}"
            if spk_acc is not None
            else "speaker_accuracy=unavailable"
        )


if __name__ == "__main__":
    main()
