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
