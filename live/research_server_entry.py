from __future__ import annotations

from . import research_server
from .account_balance_compat import install as install_account_balance_compat
from .whole_dollar_sizing import install as install_whole_dollar_sizing


def main() -> int:
    """Start the persistent LIVE server after production compatibility patches."""
    install_account_balance_compat()
    install_whole_dollar_sizing()
    return research_server._persistent.main()


if __name__ == "__main__":
    raise SystemExit(main())
