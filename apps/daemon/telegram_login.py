r"""One-time interactive login for the optional Telegram user-account bridge.

Usage (from apps/daemon):  .venv\Scripts\python telegram_login.py
"""
from telegram_user_bridge import _interactive_login

if __name__ == "__main__":
    _interactive_login()
