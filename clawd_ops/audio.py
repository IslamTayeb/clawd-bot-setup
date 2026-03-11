import asyncio
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import boto3
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent
from botocore.exceptions import ClientError

SAMPLE_RATE_HZ = 16000
CHUNK_SIZE = 8192
POLL_INTERVAL_SECONDS = 3
JOB_TIMEOUT_SECONDS = 300


async def transcribe_voice(
    oga_path: str,
    region: str = "us-east-1",
    duration_seconds: int | None = None,
    cleanup_source: bool = True,
) -> str:
    source_path = Path(oga_path)
    flac_path = source_path.with_suffix(".flac")
    pcm_path = source_path.with_suffix(".pcm")
    if duration_seconds is None:
        duration_seconds = await asyncio.to_thread(_probe_duration_seconds, source_path)
    mode = _transcribe_mode(duration_seconds)

    try:
        await _convert_audio(source_path, flac_path, "flac")
        if mode != "stream":
            try:
                transcript = await asyncio.to_thread(_transcribe_job_sync, flac_path, region)
                return _normalize_transcript(transcript)
            except Exception:
                if mode == "job":
                    raise

        await _convert_audio(source_path, pcm_path, "pcm")
        transcript = await _stream_transcribe(pcm_path, region)
        return _normalize_transcript(transcript)
    finally:
        cleanup_paths = [flac_path, pcm_path]
        if cleanup_source:
            cleanup_paths.insert(0, source_path)
        for path in cleanup_paths:
            path.unlink(missing_ok=True)


def _transcribe_mode(duration_seconds: int | None) -> str:
    mode = os.environ.get("TRANSCRIBE_MODE", "auto").strip().lower() or "auto"
    if mode in {"job", "stream"}:
        return mode

    threshold = int(os.environ.get("TRANSCRIBE_AUTO_BATCH_MIN_SECONDS", "90"))
    if duration_seconds is not None and duration_seconds < threshold:
        return "stream"

    if os.environ.get("TRANSCRIBE_BUCKET"):
        return "job"
    return "stream"


def _normalize_transcript(text: str) -> str:
    normalized = " ".join(text.split()).strip()
    if not normalized:
        return ""

    replacements = (
        (r"(?i)\bhigh Claude\b", "Hi Claude"),
        (r"(?i)\bhi Claude\b", "Hi Claude"),
        (r"\b[Cc]lawd[- ]?[Bb]ot\b", "Clawd Bot"),
        (r"\b[Cc]laude\b", "Claude"),
        (r"\b[Cc]lawd\b", "Clawd"),
        (r"\b[Oo]bsidian\b", "Obsidian"),
        (r"(?i)\bo+b+sidian\b", "Obsidian"),
        (r"\barxiv\b", "arXiv"),
        (r"\b[Aa][.]?[Ww][.]?[Ss][.]?\b", "AWS"),
        (r"\b[Ee][.]?[Cc][.]?[- ]?2\b", "EC2"),
        (r"\bec[- ]two\b", "EC2"),
        (r"\bOEC2\b", "EC2"),
        (r"(?i)\bo+s?e?c2\b", "EC2"),
        (r"\b[Gg]it[Hh]ub\b", "GitHub"),
        (r"\b[Bb]ot[Ff]ather\b", "BotFather"),
        (r"\b[Rr]eadme[- ]dot[- ](?:m[.]?d[.]?|md)\b", "README.md"),
        (r"\bdot[- ](?:m[.]?d[.]?|md)\b", ".md"),
        (r"\bmarkdown[- ]file\b", "markdown file"),
        (r"(?i)\bmarkdown[.\s]+m[.\s]*d\b", "markdown.md"),
        (r"(?i)\.m\.?d\b", ".md"),
        (r"(?i)\baussie[- ]?c2\b", "AWS EC2"),
        (r"\btoto\b", "todo"),
    )

    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized)

    return normalized.strip()


def _probe_duration_seconds(audio_path: Path) -> int | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return None

    output = result.stdout.strip()
    if not output:
        return None

    try:
        return max(1, int(float(output)))
    except ValueError:
        return None


async def _convert_audio(source_path: Path, target_path: Path, target_format: str) -> None:
    if target_format == "flac":
        args = [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE_HZ),
            "-c:a",
            "flac",
            str(target_path),
        ]
    else:
        args = [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE_HZ),
            str(target_path),
        ]

    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        error = stderr.decode().strip() or "unknown ffmpeg error"
        raise RuntimeError(f"ffmpeg conversion failed: {error}")


def _transcribe_job_sync(audio_path: Path, region: str) -> str:
    bucket = os.environ.get("TRANSCRIBE_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("TRANSCRIBE_BUCKET is required for job-based transcription.")

    prefix = os.environ.get("TRANSCRIBE_PREFIX", "clawd-bot").strip() or "clawd-bot"
    language_code = os.environ.get("TRANSCRIBE_LANGUAGE_CODE", "en-US").strip() or "en-US"
    vocabulary_name = _ready_vocabulary_name(region)
    language_model_name = os.environ.get("TRANSCRIBE_LANGUAGE_MODEL_NAME", "").strip()

    s3_client = boto3.client("s3", region_name=region)
    transcribe_client = boto3.client("transcribe", region_name=region)

    job_id = f"clawd-{uuid.uuid4().hex}"
    media_key = f"{prefix}/input/{job_id}.flac"
    output_prefix = f"{prefix}/output"

    settings = {}
    if vocabulary_name:
        settings["VocabularyName"] = vocabulary_name

    model_settings = {}
    if language_model_name:
        model_settings["LanguageModelName"] = language_model_name

    try:
        s3_client.upload_file(str(audio_path), bucket, media_key)
        request = {
            "TranscriptionJobName": job_id,
            "Media": {"MediaFileUri": f"s3://{bucket}/{media_key}"},
            "MediaFormat": "flac",
            "MediaSampleRateHertz": SAMPLE_RATE_HZ,
            "LanguageCode": language_code,
            "OutputBucketName": bucket,
            "OutputKey": output_prefix,
        }
        if settings:
            request["Settings"] = settings
        if model_settings:
            request["ModelSettings"] = model_settings

        try:
            transcribe_client.start_transcription_job(**request)
        except ClientError:
            if settings or model_settings:
                request.pop("Settings", None)
                request.pop("ModelSettings", None)
                transcribe_client.start_transcription_job(**request)
            else:
                raise
        transcript_uri = _wait_for_job(transcribe_client, job_id)
        return _read_transcript_output(s3_client, bucket, transcript_uri)
    finally:
        _cleanup_transcribe_artifacts(
            s3_client,
            transcribe_client,
            bucket,
            media_key,
            _output_key_from_job_uri(bucket, locals().get("transcript_uri", "")),
            job_id,
        )


def _wait_for_job(transcribe_client, job_id: str) -> str:
    deadline = time.time() + JOB_TIMEOUT_SECONDS
    while time.time() < deadline:
        response = transcribe_client.get_transcription_job(TranscriptionJobName=job_id)
        job = response["TranscriptionJob"]
        status = job["TranscriptionJobStatus"]
        if status == "COMPLETED":
            return job.get("Transcript", {}).get("TranscriptFileUri", "")
        if status == "FAILED":
            reason = job.get("FailureReason", "unknown failure")
            raise RuntimeError(f"transcription job failed: {reason}")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(
        f"transcription job {job_id} did not finish within {JOB_TIMEOUT_SECONDS} seconds"
    )


def _cleanup_transcribe_artifacts(
    s3_client,
    transcribe_client,
    bucket: str,
    media_key: str,
    output_key: str,
    job_id: str,
) -> None:
    try:
        transcribe_client.delete_transcription_job(TranscriptionJobName=job_id)
    except Exception:
        pass

    for key in (media_key, output_key):
        if not key:
            continue
        try:
            s3_client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            pass


def _output_key_from_job_uri(bucket: str, transcript_uri: str) -> str:
    if not transcript_uri:
        return ""

    parsed = urlparse(transcript_uri)
    path = parsed.path.lstrip("/")
    bucket_prefix = f"{bucket}/"
    if path.startswith(bucket_prefix):
        return path[len(bucket_prefix) :]
    return path


def _read_transcript_output(s3_client, bucket: str, transcript_uri: str) -> str:
    if not transcript_uri:
        raise RuntimeError("transcription job completed without a transcript URI")

    output_key = _output_key_from_job_uri(bucket, transcript_uri)
    if not output_key:
        raise RuntimeError("transcription job completed without an output key")

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            body = s3_client.get_object(Bucket=bucket, Key=output_key)["Body"].read()
            payload = json.loads(body)
            transcripts = payload.get("results", {}).get("transcripts", [])
            return transcripts[0]["transcript"] if transcripts else ""
        except (ClientError, json.JSONDecodeError):
            time.sleep(1)
    raise TimeoutError("transcript output was not written in time")


class _StreamHandler(TranscriptResultStreamHandler):
    def __init__(self, stream):
        super().__init__(stream)
        self.transcripts: list[str] = []

    async def handle_transcript_event(self, transcript_event: TranscriptEvent) -> None:
        for result in transcript_event.transcript.results:
            if result.is_partial:
                continue
            for alternative in result.alternatives:
                if alternative.transcript:
                    self.transcripts.append(alternative.transcript)


async def _stream_transcribe(pcm_path: Path, region: str) -> str:
    vocabulary_name = _ready_vocabulary_name(region)
    language_model_name = os.environ.get("TRANSCRIBE_LANGUAGE_MODEL_NAME", "").strip() or None
    language_code = os.environ.get("TRANSCRIBE_LANGUAGE_CODE", "en-US").strip() or "en-US"

    client = TranscribeStreamingClient(region=region)
    try:
        stream = await client.start_stream_transcription(
            language_code=language_code,
            media_sample_rate_hz=SAMPLE_RATE_HZ,
            media_encoding="pcm",
            vocabulary_name=vocabulary_name,
            language_model_name=language_model_name,
        )
    except Exception:
        if not vocabulary_name and not language_model_name:
            raise
        stream = await client.start_stream_transcription(
            language_code=language_code,
            media_sample_rate_hz=SAMPLE_RATE_HZ,
            media_encoding="pcm",
        )

    async def write_audio() -> None:
        with pcm_path.open("rb") as audio_file:
            while True:
                chunk = audio_file.read(CHUNK_SIZE)
                if not chunk:
                    break
                await stream.input_stream.send_audio_event(audio_chunk=chunk)
        await stream.input_stream.end_stream()

    handler = _StreamHandler(stream.output_stream)
    await asyncio.gather(write_audio(), handler.handle_events())
    return " ".join(handler.transcripts)


def _ready_vocabulary_name(region: str) -> str | None:
    vocabulary_name = os.environ.get("TRANSCRIBE_VOCABULARY_NAME", "").strip()
    if not vocabulary_name:
        return None

    try:
        response = boto3.client("transcribe", region_name=region).get_vocabulary(
            VocabularyName=vocabulary_name
        )
    except Exception:
        return None

    if response.get("VocabularyState") == "READY":
        return vocabulary_name
    return None
