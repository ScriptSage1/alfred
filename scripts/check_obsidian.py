"""Manual test: can Alfred talk to your real Obsidian vault?

Run it from the project folder with Obsidian open:

    python scripts/check_obsidian.py

It writes ONE note, Alfred/connection-test.md, in your vault. It is safe to
run repeatedly and safe to delete that note afterwards.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime

from alfred.config import ConfigError, load_config
from alfred.obsidian import NoteNotFoundError, ObsidianClient, ObsidianError

TEST_NOTE = "Alfred/connection-test.md"


def step(message: str) -> None:
    print(f"  - {message}")


def main() -> int:
    try:
        settings = load_config().obsidian
    except ConfigError as exc:
        print(f"Config problem: {exc}")
        return 1

    print("Obsidian settings")
    step(f"URL:     {settings.url}")
    step(f"CA cert: {settings.ca_cert or '(none)'}")
    step(f"API key: {'set' if settings.api_key else 'MISSING (set OBSIDIAN_API_KEY)'}")
    print()

    marker = f"alfred-check-{datetime.now():%Y%m%d%H%M%S}"
    content = f"# Alfred connection test\n\nWritten by Alfred. Marker: {marker}\n"
    appended = "\n- Appended by Alfred.\n"

    try:
        with ObsidianClient.from_settings(settings) as obsidian:
            print("Checks")

            info = obsidian.status()
            step(f"connected to {info.get('service')} {info.get('versions', {}).get('self', '')}")
            if not info.get("authenticated"):
                print("FAILED: Obsidian is reachable but did not accept the API key.")
                return 1
            step("API key accepted")

            try:
                obsidian.read_note(TEST_NOTE)
                obsidian.update_note(TEST_NOTE, content)
                step(f"updated existing note {TEST_NOTE}")
            except NoteNotFoundError:
                obsidian.create_note(TEST_NOTE, content)
                step(f"created note {TEST_NOTE}")

            if obsidian.read_note(TEST_NOTE) != content:
                print("FAILED: the note read back with different content.")
                return 1
            step("read it back, content matches")

            obsidian.append_note(TEST_NOTE, appended)
            if not obsidian.read_note(TEST_NOTE).endswith(appended):
                print("FAILED: the appended text is missing.")
                return 1
            step("appended a line, verified")

            # Obsidian indexes new text in the background, so give search a moment.
            for _ in range(10):
                results = obsidian.search_notes(marker)
                if results:
                    break
                time.sleep(0.5)
            found = [r.filename for r in results]
            if TEST_NOTE in found:
                step(f"search for {marker!r} found the note")
            else:
                step(f"search ran but did not find the note yet (results: {found})")
    except ObsidianError as exc:
        print(f"\nFAILED: {exc}")
        return 1

    print(f"\nSuccess! Alfred can talk to Obsidian. Have a look at {TEST_NOTE} in your vault.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
