import asyncio

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
