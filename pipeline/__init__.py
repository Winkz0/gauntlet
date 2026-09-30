import sys


def utf8_output() -> None:
    """
    Posting titles and company names are arbitrary Unicode ("Mondelēz"). On
    Windows a piped stdout falls back to the ANSI code page and print() raises
    on them, so each command-line entry point switches its streams to UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
