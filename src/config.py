import os
from dataclasses import dataclass
from dotenv import load_dotenv
from typing import Optional


load_dotenv()


@dataclass
class Config:
	TELEGRAM_TOKEN: str
	API_BASE_URL: str = "http://localhost:8000"
	API_TIMEOUT: int = 5
	ADMIN_CHAT_ID: Optional[int] = None

	def validate(self):
		if not self.TELEGRAM_TOKEN:
			raise ValueError("TELEGRAM_TOKEN is required")


_CONFIG: Optional[Config] = None


def get_config() -> Config:
	global _CONFIG
	if _CONFIG is None:
		token = os.getenv("TELEGRAM_TOKEN", "")
		base = os.getenv("API_BASE_URL", "http://localhost:8000")
		timeout = int(os.getenv("API_TIMEOUT", "5"))
		admin = os.getenv("ADMIN_CHAT_ID")
		admin_id = int(admin) if admin and admin.isdigit() else None
		_CONFIG = Config(TELEGRAM_TOKEN=token, API_BASE_URL=base, API_TIMEOUT=timeout, ADMIN_CHAT_ID=admin_id)
	return _CONFIG


# Backwards compatible module-level constants (for existing imports)
_cfg = get_config()
TELEGRAM_TOKEN = _cfg.TELEGRAM_TOKEN
API_BASE_URL = _cfg.API_BASE_URL
API_TIMEOUT = _cfg.API_TIMEOUT

