"""Tiny SMTP sink that stores every received mail as .eml (dev/testing only).

    pip install aiosmtpd
    python scripts/smtp_sink.py ./mails   # listens on 127.0.0.1:2525
"""

import sys
import time
from pathlib import Path

from aiosmtpd.controller import Controller


class Save:
    def __init__(self, out: Path):
        self.out = out
        out.mkdir(parents=True, exist_ok=True)

    async def handle_DATA(self, server, session, envelope):  # noqa: N802
        path = self.out / f"{time.time_ns()}.eml"
        path.write_bytes(envelope.content)
        print(f"saved {path} ({len(envelope.content) // 1024} KiB) for {envelope.rcpt_tos}", flush=True)
        return "250 OK"


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "mails")
    Controller(Save(out), hostname="127.0.0.1", port=2525).start()
    print("SMTP sink on 127.0.0.1:2525", flush=True)
    while True:
        time.sleep(3600)
