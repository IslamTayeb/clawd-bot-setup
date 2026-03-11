import argparse
import asyncio
import os
import sys

from clawd_ops.audio import transcribe_voice


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m clawd_ops.openclaw_audio_cli")
    parser.add_argument("media_path")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        transcript = asyncio.run(
            transcribe_voice(
                args.media_path,
                region=args.region,
                cleanup_source=False,
            )
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    text = transcript.strip()
    if not text:
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
