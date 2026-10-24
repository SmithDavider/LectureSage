#!/usr/bin/env python3
"""用 funasr_onnx 做 SenseVoice ONNX 量化推理，可选 VAD 切段和标点模型。"""
import argparse
import json
import re
import time
from pathlib import Path

import numpy as np


def iter_windows(path, sample_rate=16000, window_seconds=30, overlap_seconds=2):
    """读取单声道 16k int16 wav，按固定窗口产出 float32 数据。"""
    import wave

    window = int(window_seconds * sample_rate)
    stride = int((window_seconds - overlap_seconds) * sample_rate)
    with wave.open(str(path), "rb") as wav:
        assert wav.getframerate() == sample_rate
        assert wav.getnchannels() == 1
        total = wav.getnframes()
        start = 0
        while start < total:
            length = min(window, total - start)
            wav.setpos(start)
            pcm = np.frombuffer(wav.readframes(length), dtype="<i2").astype(
                np.float32
            ) / 32768.0
            yield start, pcm
            start += stride


def merge_transcripts(texts, min_overlap_chars=4, max_overlap_chars=200):
    merged = ""
    for text in texts:
        current = text.strip()
        if not current:
            continue
        if not merged:
            merged = current
            continue
        limit = min(len(merged), len(current), max_overlap_chars)
        overlap = 0
        for size in range(limit, min_overlap_chars - 1, -1):
            if merged[-size:].casefold() == current[:size].casefold():
                overlap = size
                break
        remainder = current[overlap:]
        if not remainder:
            continue
        separator = "" if merged[-1:].isspace() or remainder[:1].isspace() else " "
        merged += separator + remainder
    return merged


def load_quantized_model(model_dir):
    """优先复用已导出的量化 onnx，否则先从本地 pytorch 权重导出。"""
    model_dir = Path(model_dir)
    quant_path = model_dir / "model_quant.onnx"
    if quant_path.exists():
        print(f"使用已有量化模型: {quant_path}")
        return str(model_dir)

    from funasr import AutoModel

    print("未找到 model_quant.onnx，开始导出并量化...")
    started = time.time()
    model = AutoModel(model=str(model_dir), device="cpu", disable_update=True)
    export_dir = model.export(type="onnx", quantize=True, device="cpu")
    print(f"导出完成，耗时 {time.time() - started:.1f}s，目录: {export_dir}")
    return export_dir


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("onnx_transcript.txt"))
    parser.add_argument("--model", default="iic/SenseVoiceSmall")
    parser.add_argument("--vad-model")
    parser.add_argument("--vad-merge-ms", type=int, default=30000)
    parser.add_argument("--spk-model")
    parser.add_argument("--punc-model")
    parser.add_argument("--window-seconds", type=float, default=30)
    parser.add_argument("--overlap-seconds", type=float, default=2)
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args()

    from funasr_onnx import SenseVoiceSmall
    from funasr_onnx.utils.postprocess_utils import rich_transcription_postprocess

    model_dir = load_quantized_model(args.model)
    started = time.time()
    model = SenseVoiceSmall(
        model_dir=model_dir,
        batch_size=1,
        quantize=True,
        intra_op_num_threads=args.threads,
    )

    if args.vad_model:
        from funasr import AutoModel

        import librosa

        vad = AutoModel(model=args.vad_model, device="cpu", disable_update=True)
        waveform, _ = librosa.load(args.input, sr=16000, mono=True)
        vad_result = vad.generate(input=waveform)
        segments = vad_result[0]["value"]
        print(f"VAD 原始 {len(segments)} 个语音段")
        from funasr.utils.vad_utils import merge_vad

        segments = merge_vad(segments, max_length=args.vad_merge_ms, min_length=0)
        print(f"合并后 {len(segments)} 个语音段")
        audio_chunks = []
        for start_ms, end_ms in segments:
            start_sample = int(start_ms * 16)
            end_sample = int(end_ms * 16)
            audio_chunks.append((start_sample, waveform[start_sample:end_sample]))
        print(f"VAD 检测到 {len(audio_chunks)} 个语音段")
    else:
        audio_chunks = list(
            iter_windows(
                args.input,
                window_seconds=args.window_seconds,
                overlap_seconds=args.overlap_seconds,
            )
        )
        import librosa

        waveform, _ = librosa.load(args.input, sr=16000, mono=True)

    spk_ids = None
    if args.spk_model:
        import torch
        from funasr import AutoModel
        from funasr.models.campplus.cluster_backend import ClusterBackend
        from funasr.models.campplus.utils import distribute_spk, postprocess, sv_chunk

        print("正在加载说话人模型...")
        spk_model = AutoModel(model=args.spk_model, device="cpu", disable_update=True)
        audio_segments = [
            [
                start_sample / 16000,
                (start_sample + len(pcm)) / 16000,
                pcm.astype("float32"),
            ]
            for start_sample, pcm in audio_chunks
        ]
        spk_chunks = sv_chunk(audio_segments)
        print(f"说话人模型处理 {len(spk_chunks)} 个 1.5s 子片段")
        spk_res = spk_model.generate(input=[chunk[2] for chunk in spk_chunks])
        embeddings = torch.cat(
            [res["spk_embedding"] for res in spk_res], dim=0
        ).cpu().numpy()
        labels = ClusterBackend()(embeddings)
        spk_intervals = postprocess(
            [[chunk[0], chunk[1]] for chunk in spk_chunks], None, labels, embeddings
        )
        sentence_list = [
            {"start": start_sample / 16000, "end": (start_sample + len(pcm)) / 16000}
            for start_sample, pcm in audio_chunks
        ]
        sentence_list = distribute_spk(sentence_list, spk_intervals)
        spk_ids = [sentence.get("spk", 0) for sentence in sentence_list]
        print(f"说话人聚类完成：{sorted(set(spk_ids))}")

    chunks = []
    for index, (start_sample, pcm) in enumerate(audio_chunks):
        raw = model(pcm, language="auto", textnorm="withitn")[0]
        text = rich_transcription_postprocess(raw)
        end_ms = round((start_sample + len(pcm)) * 1000 / 16000)
        spk = spk_ids[index] if spk_ids is not None else None
        chunks.append(
            {
                "index": index,
                "start_ms": round(start_sample * 1000 / 16000),
                "end_ms": end_ms,
                "raw_text": raw,
                "text": text,
                "spk": spk,
            }
        )
        prefix = f"[说话人{spk + 1}] " if spk is not None else ""
        print(f"[{index:02d}] {prefix}{end_ms}ms: {text}")

    if args.punc_model:
        from funasr import AutoModel

        punc_model = AutoModel(model=args.punc_model, device="cpu", disable_update=True)
        for chunk in chunks:
            clean = re.sub(r"<\|[^>]+\|>", "", chunk["raw_text"])
            clean = re.sub(r"[\s，。！？、；;,.!?:：]", "", clean)
            chunk["text"] = punc_model.generate(input=clean)[0]["text"]

    display_lines = []
    for chunk in chunks:
        prefix = f"[说话人{chunk['spk'] + 1}] " if chunk["spk"] is not None else ""
        display_lines.append(prefix + chunk["text"])
    merged = "\n".join(display_lines)
    args.output.write_text(merged + "\n", encoding="utf-8")
    plain_path = args.output.with_name(args.output.stem + ".plain.txt")
    plain_path.write_text("\n".join(chunk["text"] for chunk in chunks) + "\n", encoding="utf-8")
    chunks_path = args.output.with_suffix(".chunks.jsonl")
    with chunks_path.open("w", encoding="utf-8") as fp:
        for chunk in chunks:
            fp.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    elapsed = time.time() - started
    print(f"推理完成: {elapsed:.2f}s, rtf={elapsed / 300:.3f}")
    print(f"wrote {args.output}, {plain_path} and {chunks_path}")


if __name__ == "__main__":
    main()
