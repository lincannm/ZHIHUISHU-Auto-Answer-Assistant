import os
import sys
from datetime import datetime


BLUE = "\033[94m"
RESET = "\033[0m"
_ACTIVE_STREAM = None


def _build_timestamp():
    return datetime.now().strftime("%m-%d %H:%M:%S")


def _supports_color():
    return sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def format_timestamp(timestamp, color=False):
    formatted = f"[{timestamp}]"
    if color and _supports_color():
        return f"{BLUE}{formatted}{RESET}"
    return formatted


class LiveTextStream:
    def __init__(self, title):
        self.title = str(title)
        self.started = False
        self.line_open = False
        self.closed = False

    def _start(self):
        global _ACTIVE_STREAM
        if self.started or self.closed:
            return

        _flush_active_stream_line()
        timestamp = format_timestamp(_build_timestamp(), color=True)
        print(f"{timestamp} {self.title}")
        self.started = True
        _ACTIVE_STREAM = self

    def write(self, text):
        if self.closed:
            return

        text = str(text or "")
        if not text:
            return

        self._start()
        sys.stdout.write(text)
        sys.stdout.flush()
        self.line_open = not text.endswith("\n")

    def finish(self):
        global _ACTIVE_STREAM
        if self.closed:
            return

        if self.started and self.line_open:
            print()
        self.line_open = False
        self.closed = True
        if _ACTIVE_STREAM is self:
            _ACTIVE_STREAM = None


def _flush_active_stream_line():
    if _ACTIVE_STREAM and _ACTIVE_STREAM.line_open:
        print()
        _ACTIVE_STREAM.line_open = False


def create_live_stream(title):
    return LiveTextStream(title)


def log_message(message):
    _flush_active_stream_line()
    timestamp = format_timestamp(_build_timestamp(), color=True)
    lines = str(message).splitlines() or [""]
    print(f"{timestamp} {lines[0]}")
    for line in lines[1:]:
        print(line)
