"""Configuration for the Info Tsinghua RSS scraper application."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# Application Settings
# =============================================================================

APP_NAME = "InfoTsinghuaRSS"
APP_VERSION = "0.0.1"
USER_AGENT = f"{APP_NAME}/{APP_VERSION}"


# =============================================================================
# Scraper Settings
# =============================================================================

BASE_URL = "https://info.tsinghua.edu.cn"
LIST_URL = f"{BASE_URL}/f/info/xxfb_fg/xnzx/template/more?lmid=all"
LIST_API = f"{BASE_URL}/b/info/xxfb_fg/xnzx/template/more"
DETAIL_URL_TEMPLATE = (
    f"{BASE_URL}/f/info/xxfb_fg/xnzx/template/detail?xxid={{xxid}}"
)

MIN_REQUEST_INTERVAL = 1.0 / 3.0  # 3 requests per second


# =============================================================================
# Scheduler Settings
# =============================================================================

SCRAPE_INTERVAL = 15 * 60  # 15 minutes
MIN_SCRAPE_INTERVAL = 10 * 60  # 10 minutes
MAX_PAGES_PER_RUN = 4


# =============================================================================
# Database Settings
# =============================================================================

DB_PATH = Path(os.getenv("DB_PATH", "info_rss.db"))


# =============================================================================
# RSS Feed Settings
# =============================================================================

FEED_TITLE = "清华大学信息门户"
FEED_DESCRIPTION = "清华大学信息门户最新通知"
FEED_LINK = BASE_URL
FEED_LANGUAGE = "zh-CN"
MAX_RSS_ITEMS = 100
MAX_RSS_ITEMS_LIMIT = 1000
RSS_CACHE_MAX_AGE = 300  # 5 minutes


# =============================================================================
# API Server Settings
# =============================================================================

API_TITLE = "Tsinghua Info RSS"
API_DESCRIPTION = "RSS feed for Tsinghua University Info Portal"
API_VERSION = "1.0.0"
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8000


# =============================================================================
# Parser Settings
# =============================================================================

LIBRARY_ENCODINGS = ["utf-8-sig", "gbk", "gb2312", "gb18030", "utf-8"]
