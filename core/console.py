import os
import sys
from datetime import datetime


BLUE = "\033[94m"
RESET = "\033[0m"


def _build_timestamp():
    return datetime.now().strftime("%m-%d %H:%M:%S")


def _supports_color():
    return sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def format_timestamp(timestamp, color=False):
    formatted = f"[{timestamp}]"
    if color and _supports_color():
        return f"{BLUE}{formatted}{RESET}"
    return formatted


def log_message(message):
    timestamp = format_timestamp(_build_timestamp(), color=True)
    lines = str(message).splitlines() or [""]
    print(f"{timestamp} {lines[0]}")
    for line in lines[1:]:
        print(line)
