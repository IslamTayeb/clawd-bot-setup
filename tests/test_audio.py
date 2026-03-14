import asyncio

import pytest

from clawd_ops import audio


def test_normalize_transcript_rewrites_domain_terms():
    transcript = "hi claude save this to markdown md on aws ec two and github"
    assert (
        audio._normalize_transcript(transcript)
        == "Hi Claude save this to markdown.md on AWS EC2 and GitHub"
    )


def test_transcribe_mode_prefers_batch_when_bucket_present(monkeypatch):
    monkeypatch.setenv("TRANSCRIBE_MODE", "auto")
    monkeypatch.setenv("TRANSCRIBE_BUCKET", "bucket")
    monkeypatch.setenv("TRANSCRIBE_AUTO_BATCH_MIN_SECONDS", "90")
    assert audio._transcribe_mode(120) == "job"
    assert audio._transcribe_mode(30) == "stream"


def test_transcribe_mode_keeps_threshold_duration_on_stream(monkeypatch):
    monkeypatch.setenv("TRANSCRIBE_MODE", "auto")
    monkeypatch.setenv("TRANSCRIBE_BUCKET", "bucket")
    monkeypatch.setenv("TRANSCRIBE_AUTO_BATCH_MIN_SECONDS", "90")
    assert audio._transcribe_mode(90) == "stream"
    assert audio._transcribe_mode(91) == "job"


def test_transcribe_timeout_seconds_defaults_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("TRANSCRIBE_TIMEOUT_SECONDS", raising=False)
    assert audio._transcribe_timeout_seconds() == 1800

    monkeypatch.setenv("TRANSCRIBE_TIMEOUT_SECONDS", "2400")
    assert audio._transcribe_timeout_seconds() == 2400


def test_transcribe_voice_preserves_source_when_cleanup_disabled(monkeypatch, tmp_path):
    async def fake_convert(_source, target, _format):
        target.write_bytes(b"converted")

    async def fake_stream(_pcm_path, _region):
        return "hi claude"

    monkeypatch.setenv("TRANSCRIBE_MODE", "stream")
    monkeypatch.setattr(audio, "_convert_audio", fake_convert)
    monkeypatch.setattr(audio, "_stream_transcribe", fake_stream)
    monkeypatch.setattr(audio, "_probe_duration_seconds", lambda _path: 5)

    source = tmp_path / "voice.oga"
    source.write_bytes(b"source")

    transcript = asyncio.run(audio.transcribe_voice(str(source), cleanup_source=False))

    assert transcript == "Hi Claude"
    assert source.exists()


def test_transcribe_voice_auto_falls_back_to_stream_when_job_fails(monkeypatch, tmp_path):
    async def fake_convert(_source, target, _format):
        target.write_bytes(b"converted")

    async def fake_stream(_pcm_path, _region):
        return "hi claude"

    def fake_job(_flac_path, _region):
        raise RuntimeError("job failed")

    monkeypatch.setenv("TRANSCRIBE_MODE", "auto")
    monkeypatch.setenv("TRANSCRIBE_BUCKET", "bucket")
    monkeypatch.setenv("TRANSCRIBE_AUTO_BATCH_MIN_SECONDS", "90")
    monkeypatch.setattr(audio, "_convert_audio", fake_convert)
    monkeypatch.setattr(audio, "_stream_transcribe", fake_stream)
    monkeypatch.setattr(audio, "_transcribe_job_sync", fake_job)
    monkeypatch.setattr(audio, "_probe_duration_seconds", lambda _path: 120)

    source = tmp_path / "voice.oga"
    source.write_bytes(b"source")

    transcript = asyncio.run(audio.transcribe_voice(str(source), cleanup_source=False))

    assert transcript == "Hi Claude"
    assert source.exists()


def test_transcribe_voice_job_mode_raises_when_job_fails(monkeypatch, tmp_path):
    async def fake_convert(_source, target, _format):
        target.write_bytes(b"converted")

    async def fail_stream(_pcm_path, _region):
        raise AssertionError("stream fallback should not run in explicit job mode")

    def fake_job(_flac_path, _region):
        raise RuntimeError("job failed")

    monkeypatch.setenv("TRANSCRIBE_MODE", "job")
    monkeypatch.setenv("TRANSCRIBE_BUCKET", "bucket")
    monkeypatch.setattr(audio, "_convert_audio", fake_convert)
    monkeypatch.setattr(audio, "_stream_transcribe", fail_stream)
    monkeypatch.setattr(audio, "_transcribe_job_sync", fake_job)
    monkeypatch.setattr(audio, "_probe_duration_seconds", lambda _path: 120)

    source = tmp_path / "voice.oga"
    source.write_bytes(b"source")

    with pytest.raises(RuntimeError, match="job failed"):
        asyncio.run(audio.transcribe_voice(str(source), cleanup_source=False))
