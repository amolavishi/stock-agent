"""Bridge a long prompt from stdin into Hermes one-shot mode.

Windows CreateProcess has a small command-line limit.  Hermes 0.20 only accepts
the one-shot prompt as an argparse value, so this helper injects it into
``sys.argv`` after the child process has started.  The prompt never appears in
the OS command line.
"""
from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning", required=True)
    parser.add_argument("--usage-file", required=True)
    args = parser.parse_args()
    prompt = sys.stdin.read()
    if not prompt:
        raise SystemExit("empty Hermes prompt on stdin")
    sys.argv = ["hermes", "--provider", args.provider, "-m", args.model,
                "--reasoning", args.reasoning, "--safe-mode",
                "--usage-file", args.usage_file, "-z", prompt]
    from hermes_cli.main import main as hermes_main
    hermes_main()


if __name__ == "__main__":
    main()
