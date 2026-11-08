"""Central configuration, loaded from environment / apps/daemon/.env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Settings live in ~/.orion/.env so they survive app updates (the installed app's files are
# replaced on every update). Precedence: real env vars > ORION_ENV_FILE > ~/.orion/.env > apps/daemon/.env.
PACKAGED = os.getenv("ORION_PACKAGED", "").strip() == "1"
USER_ENV_FILE = Path(os.path.expanduser(os.getenv("ORION_DATA_DIR", "~/.orion"))) / ".env"


def _load_env_files() -> None:
    explicit = os.getenv("ORION_ENV_FILE")
    if explicit:
        load_dotenv(explicit)
    if PACKAGED and not USER_ENV_FILE.exists():
        try:  # first run of the installed app: start the user from the documented template
            USER_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
            example = Path(__file__).with_name(".env.example")
            if example.exists():
                USER_ENV_FILE.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
    load_dotenv(USER_ENV_FILE)
    load_dotenv(Path(__file__).with_name(".env"))


_load_env_files()


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    host: str = _str("ORION_HOST", "127.0.0.1")
    port: int = _int("ORION_PORT", 9120)
    # OrionOS boardroom (V3 §2). Empty bind = the remote boardroom endpoint is off.
    boardroom_bind: str = _str("ORION_BOARDROOM_BIND", "")
    boardroom_port: int = _int("ORION_BOARDROOM_PORT", 9121)
    boardroom_sim_minutes: float = _float("ORION_BOARDROOM_SIM_MINUTES", 0.0)

    # Screen reading is opt-in (B7): off unless the user turns it on (first-run asks; Settings > Privacy).
    capture_enabled: bool = _bool("ORION_CAPTURE_ENABLED", False)
    capture_interval: float = _float("ORION_CAPTURE_INTERVAL", 5.0)
    phash_threshold: int = _int("ORION_PHASH_THRESHOLD", 5)
    monitor_index: int = _int("ORION_MONITOR_INDEX", 1)
    tesseract_cmd: str = _str("TESSERACT_CMD")
    ocr_max_chars: int = _int("ORION_OCR_MAX_CHARS", 6000)
    image_token_estimate: int = _int("ORION_IMAGE_TOKEN_ESTIMATE", 1100)

    data_dir: Path = Path(os.path.expanduser(_str("ORION_DATA_DIR", "~/.orion")))
    embedding_backend: str = _str("EMBEDDING_BACKEND", "auto").lower()
    embedding_dim: int = 1536
    ollama_embed_model: str = _str("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    st_model: str = _str("ST_MODEL", "all-MiniLM-L6-v2")
    ocr_retention_days: int = _int("OCR_RETENTION_DAYS", 7)
    consolidation_hour: int = _int("CONSOLIDATION_HOUR", 3)

    ollama_url: str = _str("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    ollama_model: str = _str("OLLAMA_MODEL", "llama3.1:8b")
    slm_router: bool = _bool("ORION_SLM_ROUTER", True)
    cloud_provider: str = _str("CLOUD_PROVIDER", "auto").lower()
    anthropic_api_key: str = _str("ANTHROPIC_API_KEY")
    anthropic_model: str = _str("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    openai_api_key: str = _str("OPENAI_API_KEY")
    openai_model: str = _str("OPENAI_MODEL", "gpt-4o")
    openai_embed_model: str = _str("OPENAI_EMBED_MODEL", "text-embedding-3-small")

    auto_analyze: bool = _bool("ORION_AUTO_ANALYZE", False)
    auto_analyze_cooldown: float = _float("ORION_AUTO_ANALYZE_COOLDOWN", 90.0)

    # --- Discord Monitor & Game Bug Tracker ---
    discord_token: str = _str("DISCORD_TOKEN")
    discord_channel_ids: tuple[int, ...] = field(
        default_factory=lambda: tuple(
            int(x) for x in _str("DISCORD_CHANNEL_IDS").split(",") if x.strip().isdigit()
        )
    )
    discord_game_name: str = _str("DISCORD_GAME_NAME", "Starfall Odyssey")
    discord_bug_channel_ids: tuple[int, ...] = field(
        default_factory=lambda: tuple(
            int(x) for x in _str("DISCORD_BUG_CHANNEL_IDS").split(",") if x.strip().isdigit()
        )
    )

    # --- Telegram Remote Bridge ---
    telegram_bot_token: str = _str("TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = _str("TELEGRAM_CHAT_ID")
    telegram_enabled: bool = _bool("TELEGRAM_ENABLED", True)

    # --- Gemini API ---
    gemini_api_key: str = _str("GEMINI_API_KEY", _str("GOOGLE_API_KEY"))
    gemini_model: str = _str("GEMINI_MODEL", "gemini-1.5-flash")

    # --- Voice & Personality ---
    personality_mode: str = _str("ORION_PERSONALITY", "concise_companion")  # concise_companion | detailed_researcher
    tts_voice_id: str = _str("TTS_VOICE_ID")
    tts_rate: int = _int("TTS_RATE", 185)
    tts_volume: float = _float("TTS_VOLUME", 0.9)

    # --- Proactive Context Observer ---
    proactive_observer_enabled: bool = _bool("PROACTIVE_OBSERVER_ENABLED", True)
    proactive_cooldown: float = _float("PROACTIVE_COOLDOWN", 60.0)

    # --- V2: Autonomous Discord Prompt Detector & Approval Queue ---
    discord_triage_enabled: bool = _bool("DISCORD_TRIAGE_ENABLED", True)
    discord_triage_channels: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            x.strip().lstrip("#").lower()
            for x in _str("DISCORD_TRIAGE_CHANNELS", "general,realm-pitches,bug-reports,support").split(",")
            if x.strip()
        )
    )
    discord_triage_channel_ids: tuple[int, ...] = field(
        default_factory=lambda: tuple(
            int(x) for x in _str("DISCORD_TRIAGE_CHANNEL_IDS").split(",") if x.strip().isdigit()
        )
    )
    triage_model: str = _str("TRIAGE_MODEL")  # Tier 1 Ollama model; defaults to OLLAMA_MODEL
    draft_model: str = _str("DRAFT_MODEL")    # Tier 2 OpenAI model; defaults to OPENAI_MODEL
    alignment_examples: int = _int("ALIGNMENT_EXAMPLES", 5)

    # --- V2: Unified Comms Hub ---
    comms_history_per_thread: int = _int("COMMS_HISTORY_PER_THREAD", 200)
    # Optional Telethon user-account bridge for true personal Telegram DMs
    telegram_api_id: int = _int("TELEGRAM_API_ID", 0)
    telegram_api_hash: str = _str("TELEGRAM_API_HASH")

    # --- V2: Ambient Pacing Tracker ---
    pacing_enabled: bool = _bool("PACING_ENABLED", True)
    pacing_limit_minutes: float = _float("PACING_LIMIT_MINUTES", 90.0)
    pacing_break_reset_minutes: float = _float("PACING_BREAK_RESET_MINUTES", 5.0)
    pacing_snooze_minutes: float = _float("PACING_SNOOZE_MINUTES", 15.0)

    # --- V2 hardening: extra browser origins allowed to reach the daemon (comma-separated) ---
    extra_allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(x for x in _str("ORION_ALLOWED_ORIGINS").split(",") if x.strip())
    )

    # --- Feature Bible ---
    clipboard_watch: bool = _bool("CLIPBOARD_WATCH", True)
    devlog_auto: bool = _bool("DEVLOG_AUTO", True)

    # --- V2: Voice-to-Patch Self-Coding ---
    # The installed app has no source tree of its own: self-evolve edits the repo named by
    # ORION_REPO_ROOT (your dev checkout) and ships through `npm run release`. Without it, it's off.
    self_evolve_enabled: bool = _bool("SELF_EVOLVE_ENABLED", True) and (not PACKAGED or bool(_str("ORION_REPO_ROOT")))
    repo_root: Path = Path(_str("ORION_REPO_ROOT") or Path(__file__).resolve().parents[2])

    @property
    def memory_db(self) -> Path:
        return self.data_dir / "memory_db"

    @property
    def training_dir(self) -> Path:
        return self.data_dir / "training"

    @property
    def finetune_path(self) -> Path:
        return self.training_dir / "orion_finetune.jsonl"


settings = Settings()

