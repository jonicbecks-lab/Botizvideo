from __future__ import annotations

from . import persistent_server as _persistent
from .research_engine import ResearchCompatibleGalkaLiveEngine


# Reuse the proven persistent HTTP/session/PID server unchanged. Only substitute
# the engine class with a subclass that adds a research-only sidecar.
_persistent.SafeCompatibleGalkaLiveEngine = ResearchCompatibleGalkaLiveEngine


def main() -> int:
    return _persistent.main()


if __name__ == "__main__":
    raise SystemExit(main())
