import asyncio
import json
import logging
import os
import subprocess
import tempfile
import uuid
import wave
from difflib import SequenceMatcher
from pathlib import Path

from fastapi.concurrency import run_in_threadpool

from app.config import settings

logger = logging.getLogger("keji.sensevoice")


def _wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() * 1000 // wav.getframerate()


def _run_command(command: list[str]) -> None:
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"命令执行失败: {' '.join(command)}\n{proc.stderr[-2000:]}"
        )


def _convert_to_wav(path: Path, output: Path, sample_rate: int = 16000) -> None:
    """转成 16kHz 单声道 PCM WAV，作为 SenseVoice 的输入格式。"""
    _run_command([
        settings.ffmpeg_path,
        "-y",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(output),
    ])


def _merge_audio_files(
    segments: list[tuple[int, Path]],
    output: Path,
    sample_rate: int = 16000,
) -> int:
    """分段转码后按 offset_ms 覆盖写入同一 WAV，返回总时长毫秒。"""
    decoded: list[tuple[int, bytes]] = []
    max_end_ms = 0
    for offset_ms, path in segments:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        try:
            _convert_to_wav(path, wav_path, sample_rate)
            with wave.open(str(wav_path), "rb") as source:
                if source.getframerate() != sample_rate:
                    raise ValueError(f"音频采样率不是 {sample_rate}: {path}")
                frames = source.readframes(source.getnframes())
            if not frames:
                continue
            decoded.append((offset_ms, frames))
            samples = len(frames) // 2
            max_end_ms = max(max_end_ms, offset_ms + samples * 1000 // sample_rate)
        finally:
            wav_path.unlink(missing_ok=True)

    if not decoded:
        return 0

    total_samples = max_end_ms * sample_rate // 1000 + sample_rate
    merged = bytearray(total_samples * 2)
    for offset_ms, frames in decoded:
        start = offset_ms * sample_rate // 1000
        end = start + len(frames) // 2
        if start >= total_samples:
            continue
        merged[start * 2 : end * 2] = frames[: (end - start) * 2]

    with wave.open(str(output), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(bytes(merged))
    return max_end_ms


def _split_wav(path: Path) -> list[tuple[Path, int]]:
    chunk_ms = settings.sensevoice_chunk_seconds * 1000
    overlap_ms = settings.sensevoice_chunk_overlap_seconds * 1000
    with wave.open(str(path), "rb") as source:
        sample_rate = source.getframerate()
        total_ms = source.getnframes() * 1000 // sample_rate

    chunks: list[tuple[Path, int]] = []
    start_ms = 0
    while start_ms < total_ms:
        left_ms = max(0, start_ms - overlap_ms)
        right_ms = min(total_ms, start_ms + chunk_ms + overlap_ms)
        start_sample = left_ms * sample_rate // 1000
        end_sample = right_ms * sample_rate // 1000
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            chunk_path = Path(tmp.name)
        with wave.open(str(path), "rb") as source:
            source.setpos(start_sample)
            frames = source.readframes(end_sample - start_sample)
        with wave.open(str(chunk_path), "wb") as chunk:
            chunk.setnchannels(1)
            chunk.setsampwidth(2)
            chunk.setframerate(sample_rate)
            chunk.writeframes(frames)
        chunks.append((chunk_path, left_ms))
        start_ms += chunk_ms
    return chunks


def _merge_chunk_results(chunk_results: list[tuple[int, list[dict]]]) -> list[dict]:
    result: list[dict] = []
    for _chunk_start_ms, items in sorted(chunk_results, key=lambda entry: entry[0]):
        for item in items:
            if result and item["start_ms"] < result[-1]["end_ms"]:
                last = result[-1]
                same_text = (
                    SequenceMatcher(None, item["text"], last["text"]).ratio() >= 0.8
                )
                if same_text or item["end_ms"] <= last["end_ms"]:
                    continue
                if item["end_ms"] >= last["end_ms"] and len(item["text"]) >= len(last["text"]):
                    result[-1] = item
                continue
            result.append(item)
    return sorted(result, key=lambda item: (item["start_ms"], item["end_ms"]))



class _SenseVoiceWorker:
    def __init__(self, index: int, process: asyncio.subprocess.Process, reader, writer):
        self.index = index
        self.process = process
        self.reader = reader
        self.writer = writer
        self.lock = asyncio.Lock()
        self._stderr_task: asyncio.Task | None = None
        self._closed = False

    @classmethod
    async def start(cls, index: int) -> "_SenseVoiceWorker":
        script = Path(__file__).resolve().parents[2] / "scripts" / "sensevoice_worker.py"
        command = [
            str(settings.sensevoice_python),
            str(script),
            "--model",
            str(settings.sensevoice_model),
            "--threads",
            str(settings.sensevoice_threads),
        ]
        if settings.sensevoice_punc_model:
            command += ["--punc-model", settings.sensevoice_punc_model]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        worker = cls(index, process, process.stdout, process.stdin)
        worker._stderr_task = asyncio.create_task(worker._drain_stderr())
        return worker

    async def _drain_stderr(self) -> None:
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    logger.info("SenseVoice worker %s: %s", self.index, text)
        except asyncio.CancelledError:
            pass

    async def request(self, payload: dict, timeout: float | None = None) -> dict:
        timeout = timeout or settings.sensevoice_timeout_seconds
        async with self.lock:
            request_id = uuid.uuid4().hex
            message = json.dumps(dict(payload, request_id=request_id), ensure_ascii=False) + "\n"
            self.writer.write(message.encode("utf-8"))
            await self.writer.drain()
            response = await self._read_response(timeout)
            if response.get("request_id") != request_id:
                raise RuntimeError("SenseVoice worker 响应不匹配")
            if not response.get("ok"):
                raise RuntimeError(response.get("error") or "SenseVoice worker 转写失败")
            return response

    async def _read_response(self, timeout: float) -> dict:
        while True:
            line = await asyncio.wait_for(self.reader.readline(), timeout)
            if not line:
                raise RuntimeError("SenseVoice worker 已退出")
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                logger.info("SenseVoice worker 输出非 JSON 行: %s", text[:500])
                continue

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._stderr_task is not None:
            self._stderr_task.cancel()
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            self.process.kill()


class SenseVoiceService:
    def __init__(self):
        self._workers: list[_SenseVoiceWorker] = []
        self._start_lock = asyncio.Lock()
        self._index = 0

    @property
    def enabled(self) -> bool:
        return settings.sensevoice_enabled

    async def _ensure_workers(self) -> None:
        if not self.enabled or self._workers:
            return
        async with self._start_lock:
            if self._workers:
                return
            count = max(1, min(settings.sensevoice_workers, 8))
            started: list[_SenseVoiceWorker] = []
            try:
                for index in range(count):
                    worker = await _SenseVoiceWorker.start(index)
                    started.append(worker)
                    await worker.request({"type": "ping"}, timeout=settings.sensevoice_timeout_seconds)
            except Exception:
                for worker in started:
                    await worker.close()
                raise
            self._workers = started
            logger.info("SenseVoice 常驻通道就绪，共 %d 个 worker", len(self._workers))

    async def transcribe_segment(
        self,
        path: Path,
        offset_ms: int,
        segment_id: int | None = None,
    ) -> dict | None:
        if not self.enabled:
            return None
        descriptor, temp_name = tempfile.mkstemp(suffix=".wav", prefix="sense_")
        os.close(descriptor)
        wav_path = Path(temp_name)
        try:
            await run_in_threadpool(_convert_to_wav, path, wav_path)
            duration_ms = await run_in_threadpool(_wav_duration_ms, wav_path)
            await self._ensure_workers()
            if not self._workers:
                raise RuntimeError("SenseVoice worker 未启动")
            worker = self._workers[self._index % len(self._workers)]
            self._index += 1
            try:
                response = await worker.request({"type": "transcribe", "wav": str(wav_path)})
            except Exception:
                self._workers.remove(worker)
                await worker.close()
                raise
        finally:
            wav_path.unlink(missing_ok=True)

        text = str(response.get("text") or "").strip()
        if not text:
            return None
        subtitle_id = f"sensevoice-{segment_id or uuid.uuid4().hex}-{offset_ms}"
        return {
            "id": subtitle_id,
            "start_ms": offset_ms,
            "end_ms": offset_ms + duration_ms,
            "text": text,
            "speaker": None,
            "page": None,
            "confirmed": True,
            "review_state": "confirmed",
            "confidence": None,
        }

    async def _transcribe_chunk(self, path: Path, offset_ms: int) -> list[dict]:
        duration_ms = await run_in_threadpool(_wav_duration_ms, path)
        await self._ensure_workers()
        if not self._workers:
            raise RuntimeError("SenseVoice worker 未启动")
        worker = self._workers[self._index % len(self._workers)]
        self._index += 1
        try:
            response = await worker.request({"type": "transcribe", "wav": str(path)})
        except Exception:
            self._workers.remove(worker)
            await worker.close()
            raise

        text = str(response.get("text") or "").strip()
        if not text:
            return []
        return [
            {
                "id": f"sensevoice-{uuid.uuid4().hex}-{offset_ms}",
                "start_ms": offset_ms,
                "end_ms": offset_ms + duration_ms,
                "text": text,
                "speaker": None,
                "page": None,
                "confirmed": True,
                "review_state": "confirmed",
                "confidence": None,
            }
        ]

    async def transcribe_merged(
        self,
        segments: list[tuple[int, Path]],
    ) -> list[dict]:
        """把课后分段合并成整段音频，再按窗口并发转写并合并结果。"""
        if not self.enabled:
            raise RuntimeError("SenseVoice 未启用，无法执行课后转写")
        await self._ensure_workers()

        def prepare():
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            total_ms = _merge_audio_files(segments, tmp_path)
            if total_ms == 0:
                tmp_path.unlink(missing_ok=True)
                return None, 0
            return tmp_path, total_ms

        merged_path, total_ms = await run_in_threadpool(prepare)
        if merged_path is None:
            return []

        chunk_paths: list[Path] = []
        try:
            chunks = await run_in_threadpool(_split_wav, merged_path)
            chunk_paths = [path for path, _ in chunks]
            workers = max(1, len(self._workers))
            semaphore = asyncio.Semaphore(workers)

            async def transcribe_one(index: int, chunk_path: Path, chunk_start_ms: int):
                del index
                async with semaphore:
                    return await self._transcribe_chunk(chunk_path, chunk_start_ms)

            chunk_results = await asyncio.gather(
                *(
                    transcribe_one(index, chunk_path, chunk_start_ms)
                    for index, (chunk_path, chunk_start_ms) in enumerate(chunks)
                )
            )
            return _merge_chunk_results(
                list(zip((chunk_start_ms for _, chunk_start_ms in chunks), chunk_results))
            )
        finally:
            merged_path.unlink(missing_ok=True)
            for path in chunk_paths:
                path.unlink(missing_ok=True)

    async def close(self) -> None:
        for worker in list(self._workers):
            await worker.close()
        self._workers.clear()


sensevoice = SenseVoiceService()
