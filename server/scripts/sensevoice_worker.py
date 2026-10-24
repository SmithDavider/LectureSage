#!/usr/bin/env python3
"""SenseVoice 常驻转写 worker：从 stdin 读取 JSON 请求，向 stdout 写 JSON 结果。"""
import argparse
import json
import os
import re
import sys
import time
import traceback
import wave

os.environ.setdefault("TQDM_DISABLE", "1")

import numpy as np


def load_models(model_dir: str, threads: int, punc_model: str | None):
    from funasr_onnx import SenseVoiceSmall
    from funasr_onnx.utils.postprocess_utils import rich_transcription_postprocess

    print("加载 SenseVoice ONNX 量化模型...", file=sys.stderr, flush=True)
    started = time.time()
    sense = SenseVoiceSmall(
        model_dir=model_dir,
        batch_size=1,
        quantize=True,
        intra_op_num_threads=threads,
    )
    print(f"SenseVoice 加载完成: {time.time() - started:.1f}s", file=sys.stderr, flush=True)

    punc = None
    if punc_model:
        try:
            from funasr import AutoModel

            print("加载标点模型...", file=sys.stderr, flush=True)
            punc = AutoModel(model=punc_model, device="cpu", disable_update=True)
            print("标点模型加载完成", file=sys.stderr, flush=True)
        except Exception as error:
            print(f"标点模型加载失败，本次不启用标点: {error}", file=sys.stderr, flush=True)
    return sense, rich_transcription_postprocess, punc


def read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as wav:
        if wav.getframerate() != 16000:
            raise ValueError(f"采样率不是 16000: {wav.getframerate()}")
        if wav.getnchannels() != 1:
            raise ValueError(f"声道数不是单声道: {wav.getnchannels()}")
        pcm = wav.readframes(wav.getnframes())
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def transcribe(request: dict, model, postprocess, punc):
    path = str(request.get("wav") or "")
    if not path:
        raise ValueError("缺少 wav 参数")
    waveform = read_wav(path)
    if waveform.size == 0:
        raise ValueError("音频内容为空")

    raw = model(waveform, language="auto", textnorm="withitn")[0]
    text = postprocess(raw)
    if punc is not None:
        clean = re.sub(r"<\|[^>]+\|>", "", raw)
        clean = re.sub(r"[\s，。！？、；;,.!?:：]", "", clean)
        if clean:
            text = punc.generate(input=clean)[0]["text"]
    return text.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--punc-model")
    args = parser.parse_args()

    model, postprocess, punc = None, None, None
    try:
        model, postprocess, punc = load_models(args.model, args.threads, args.punc_model)
    except Exception:
        payload = json.dumps(
            {"ok": False, "fatal": True, "error": traceback.format_exc()},
            ensure_ascii=False,
        )
        print(payload, flush=True)
        sys.exit(1)

    stdout = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("request_id")
            if request.get("type") == "ping":
                response = {"request_id": request_id, "ok": True, "type": "pong"}
            else:
                text = transcribe(request, model, postprocess, punc)
                response = {
                    "request_id": request_id,
                    "ok": True,
                    "text": text,
                }
        except Exception as error:
            response = {
                "request_id": request_id,
                "ok": False,
                "error": str(error),
            }
        print(json.dumps(response, ensure_ascii=False), file=stdout, flush=True)


if __name__ == "__main__":
    main()
