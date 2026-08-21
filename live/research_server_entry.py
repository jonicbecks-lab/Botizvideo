from __future__ import annotations

from . import research_server


def main() -> int:
    """Start the persistent LIVE server after research_server installs its patches."""
    return research_server._persistent.main()


if __name__ == "__main__":
    raise SystemExit(main())
