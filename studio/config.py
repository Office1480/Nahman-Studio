import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    channel_id: str
    channel_name: str
    public_url: str
    admin_password: str
    anthropic_api_key: str
    claude_model: str
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_channel_id: str
    hebcal_geoname_id: str
    timezone: str
    youtube_daily_quota: int
    live_quota_reserve: int
    archive_per_day: int
    poll_minutes: int
    caption_check_minutes: int
    ask_daily_limit: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.db"

    @property
    def has_claude(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def has_youtube(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.google_refresh_token)

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


def load() -> Settings:
    _load_dotenv()
    env = os.environ.get
    data_dir = Path(env("DATA_DIR", str(ROOT / "data")))
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        data_dir=data_dir,
        channel_id=env("YOUTUBE_CHANNEL_ID", "UCl6DCmZEozwVnxv3MVYbJnA"),
        channel_name=env("CHANNEL_NAME", "Nahman meOuman"),
        public_url=env("PUBLIC_URL", "http://localhost:8000").rstrip("/"),
        admin_password=env("ADMIN_PASSWORD", ""),
        anthropic_api_key=env("ANTHROPIC_API_KEY", ""),
        claude_model=env("CLAUDE_MODEL", "claude-opus-5-5"),
        google_client_id=env("GOOGLE_CLIENT_ID", ""),
        google_client_secret=env("GOOGLE_CLIENT_SECRET", ""),
        google_refresh_token=env("GOOGLE_REFRESH_TOKEN", ""),
        telegram_bot_token=env("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=env("TELEGRAM_CHAT_ID", ""),
        telegram_channel_id=env("TELEGRAM_CHANNEL_ID", ""),
        hebcal_geoname_id=env("HEBCAL_GEONAME_ID", "281184,2988507"),  # Jérusalem et Paris
        timezone=env("TIMEZONE", "Asia/Jerusalem"),
        youtube_daily_quota=int(env("YOUTUBE_DAILY_QUOTA", "10000")),
        live_quota_reserve=int(env("LIVE_QUOTA_RESERVE", "1500")),
        archive_per_day=int(env("ARCHIVE_PER_DAY", "17")),
        poll_minutes=int(env("POLL_MINUTES", "15")),
        caption_check_minutes=int(env("CAPTION_CHECK_MINUTES", "180")),
        ask_daily_limit=int(env("ASK_DAILY_LIMIT", "200")),
    )
