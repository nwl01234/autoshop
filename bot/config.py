import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SHOP_BOT_USERNAME = os.getenv("SHOP_BOT_USERNAME", "").strip() or "NexusDvBot"
SITE_URL = os.getenv("SITE_URL", "").strip()

# GitHub sync остатков (сайт = GitHub Pages)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "").strip() or "nwl01234/portfolio-app"

# Демо-URL прочих продуктов
CRM_DEMO_URL = os.getenv("CRM_DEMO_URL", "").strip()
TTS_LIVE_URL = os.getenv("TTS_LIVE_URL", "").strip()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

DATA_DIR = ROOT / "data"
