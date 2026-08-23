from __future__ import annotations

from . import research_server
from .account_balance_compat import install as install_account_balance_compat


def main() -> int:
    """Start the persistent LIVE server after production compatibility patches."""
    install_account_balance_compat()
    return research_server._persistent.main()


if __name__ == "__main__":
    raise SystemExit(main())
