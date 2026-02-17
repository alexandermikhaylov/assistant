"""Shared utility functions for the assistant scripts."""

import re


def strip_ansi(text: str) -> str:
    """
    Removes ANSI escape sequences and other terminal control characters.
    """
    ansi_escape = re.compile(r'''
        \x1B  # ESC
        (?:   # 7-bit C1 Fe (Exception: ESC O)
            [@-Z\\-_]
        |     # [ Control Sequence Introducer
            \[
            [0-?]*  # Parameter-Bytes
            [ -/]*  # Intermediate-Bytes
            [@-~]   # Final-Byte
        )
    ''', re.VERBOSE)
    text = ansi_escape.sub('', text)
    # Also remove carriage returns which can mess up formatting in Telegram
    return text.replace('\r', '')
