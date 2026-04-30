#!/usr/bin/env python3
"""Scraper for info.tsinghua.edu.cn."""

from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

import requests

from config import (
    BASE_URL,
    DETAIL_URL_TEMPLATE,
    LIST_API,
    LIST_URL,
    MIN_REQUEST_INTERVAL,
    USER_AGENT,
)
from parsers import get_parser

logger = logging.getLogger(__name__)


class ArticleStateEnum(IntEnum):
    NEW = 0
    UPDATED = 1
    SKIPPED = 2


class InfoTsinghuaScraper:
    """Scraper for Tsinghua University Info Portal."""

    # URL endpoints (class-level for convenience)
    BASE_URL = BASE_URL
    LIST_URL = LIST_URL
    LIST_API = LIST_API
    DETAIL_URL_TEMPLATE = DETAIL_URL_TEMPLATE
    MIN_REQUEST_INTERVAL = MIN_REQUEST_INTERVAL

    def __init__(self) -> None:
        """Initialize the scraper."""
        self._session: requests.Session | None = None
        self._csrf_token: str = ""
        self._last_request_time: float = 0.0

    def __enter__(self) -> InfoTsinghuaScraper:
        """Enter context manager."""
        self._init_session()
        return self

    def __exit__(self, *args: Any) -> None:
        """Exit context manager."""
        if self._session:
            self._session.close()

    def _rate_limit(self) -> None:
        """Apply rate limiting by sleeping if necessary."""
        now = time.time()
        time_since_last_request = now - self._last_request_time

        if time_since_last_request < self.MIN_REQUEST_INTERVAL:
            sleep_time = self.MIN_REQUEST_INTERVAL - time_since_last_request
            logger.debug(f"Rate limiting: sleeping for {sleep_time:.2f}s")
            time.sleep(sleep_time)

        self._last_request_time = time.time()

    def _init_session(self) -> None:
        """Initialize session by visiting the page to get cookies and CSRF token."""
        logger.info("Initializing session...")

        self._session = requests.Session()

        # Set user agent
        self._session.headers.update({"User-Agent": USER_AGENT})

        # Visit the list page to get cookies and CSRF token
        response = self._session.get(self.LIST_URL)
        response.raise_for_status()

        # Extract CSRF token from meta tag
        content = response.text
        csrf_match = re.search(
            r'<meta\s+name=["\']_csrf["\']\s+content=["\']([a-z0-9\-]+)',
            content,
        )
        if csrf_match:
            self._csrf_token = csrf_match.group(1)
        else:
            # Try to find in script tags
            script_match = re.search(
                r'_csrf\s*[:=]\s*["\']([a-z0-9\-]+)', content
            )
            if script_match:
                self._csrf_token = script_match.group(1)
            else:
                # Last resort: check for XSRF-TOKEN in cookies
                for cookie in self._session.cookies:
                    if cookie.name in ["XSRF-TOKEN", "X-CSRF-TOKEN"]:
                        self._csrf_token = cookie.value
                        break

        logger.info(f"Got {len(self._session.cookies)} cookies and CSRF token")

    def fetch_list(
        self,
        lmid: str = "all",
        page: int = 1,
        page_size: int = 30,
    ) -> list[dict[str, Any]]:
        """Fetch the list of information items.

        Args:
            lmid: Column ID (default "all" for all columns)
            page: Page number (1-indexed)
            page_size: Number of items per page (default 30)

        Returns:
            List of information items.
        """
        if not self._session or not self._csrf_token:
            raise RuntimeError("Scraper must be used as context manager")

        params = {
            "oType": "xs",
            "lmid": lmid,
            "lydw": "",
            "currentPage": page,
            "length": page_size,
            "xxflid": "",
            "_csrf": self._csrf_token,
        }

        headers = {
            "Referer": self.LIST_URL,
            "Origin": self.BASE_URL,
        }

        self._rate_limit()
        response = self._session.post(
            self.LIST_API, params=params, headers=headers
        )
        response.raise_for_status()
        data = response.json()

        if data.get("result") != "success":
            raise RuntimeError(f"API error: {data.get('msg', 'Unknown error')}")

        return data.get("object", {}).get("dataList", [])

    def fetch_detail(self, xxid: str) -> dict[str, Any]:
        """Fetch the detail page for an information item and parse full content.

        Args:
            xxid: Information ID

        Returns:
            Dictionary containing detail information with keys:
                - title: Title of the information
                - content: HTML content of the information
                - department: Publishing department
                - publish_time: Publish timestamp string
                - category: Category name
        """
        if not self._session:
            raise RuntimeError("Scraper must be used as context manager")

        url = self.DETAIL_URL_TEMPLATE.format(xxid=xxid)

        headers = {
            "Referer": self.LIST_URL,
        }

        self._rate_limit()
        response = self._session.get(url, headers=headers, allow_redirects=True)
        response.raise_for_status()
        html = response.text

        # Get the final URL (after any redirects)
        final_url = response.url

        # Use the appropriate parser for this URL/HTML
        parser = get_parser(final_url, html)

        # Use the parser to extract content, passing session and CSRF token
        parsed = parser.parse(
            final_url, html, session=self._session, csrf_token=self._csrf_token
        )
        return {
            "title": parsed.get("title", ""),
            "content": parsed.get("content", ""),
            "department": parsed.get("department", ""),
            "publish_time": parsed.get("publish_time", ""),
            "category": "",  # Category not available in detail view
        }

    def fetch_items(
        self,
        lmid: str = "all",
        max_pages: int | None = None,
        page_size: int = 30,
    ) -> list[dict[str, Any]]:
        """Fetch multiple pages of information items.

        Args:
            lmid: Column ID (default "all")
            max_pages: Maximum number of pages to fetch (None for unlimited)
            page_size: Number of items per page

        Returns:
            List of information items with details.
        """
        all_items = []
        page = 1

        while True:
            items = self.fetch_list(lmid=lmid, page=page, page_size=page_size)

            if not items:
                break

            all_items.extend(items)

            if max_pages and page >= max_pages:
                break

            page += 1

        return all_items

    def upsert_article(
        self, item: dict[str, Any], fetch_content: bool = True
    ) -> ArticleStateEnum:
        """Insert or update an article from a list item.

        Args:
            item: List item dictionary from the API
            fetch_content: Whether to fetch full article content (default: True)

        Returns:
            ArticleStateEnum indicating if article was new, updated, or skipped

        Raises:
            ValueError: If required fields are missing from the item
        """
        from database import upsert_article as db_upsert

        # Validate required fields
        required_fields = ["xxid", "bt", "fbsj", "url"]
        missing_fields = [
            field
            for field in required_fields
            if field not in item or not item[field]
        ]
        if missing_fields:
            raise ValueError(f"Missing required fields: {missing_fields}")

        # Validate URL path to prevent path traversal
        url_path = item.get("url", "")
        if not isinstance(url_path, str):
            raise ValueError(f"URL must be string, got {type(url_path)}")

        # Check for path traversal attempts (allow absolute paths starting with /)
        if ".." in url_path:
            raise ValueError(f"Invalid URL path: {url_path}")

        # Validate field lengths
        if len(str(item.get("xxid", ""))) > 100:
            raise ValueError("Article ID too long")
        if len(str(item.get("bt", ""))) > 500:
            raise ValueError("Title too long")

        # Build basic article from list item
        article = {
            "xxid": item["xxid"],
            "title": html.unescape(item["bt"]),
            "content": "",
            "department": html.unescape(item.get("dwmc", "")),
            "category": html.unescape(item.get("lmmc", "")),
            "publish_time": item["fbsj"],
            "url": f"{self.BASE_URL}{item['url']}",
        }

        # Fetch full content if requested
        if fetch_content:
            try:
                detail = self.fetch_detail(item["xxid"])
                # Override with detailed content
                article.update(
                    {
                        "content": detail.get("content", ""),
                    }
                )
                logger.debug(f"Fetched full content for {item['xxid']}")
            except Exception as e:
                logger.warning(
                    f"Failed to fetch full content for {item['xxid']}: {e}"
                )
                # Continue with basic article info

        state = db_upsert(article)
        return ArticleStateEnum(state)

    @staticmethod
    def parse_timestamp(timestamp_ms: int) -> datetime:
        """Parse millisecond timestamp to datetime.

        Args:
            timestamp_ms: Timestamp in milliseconds

        Returns:
            UTC datetime object
        """
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def main() -> None:
    """Test the scraper."""
    with InfoTsinghuaScraper() as scraper:
        # Fetch first page
        items = scraper.fetch_list(page=1)
        print(f"Fetched {len(items)} items")

        # Print first 3 items
        for item in items[:3]:
            print(f"\nTitle: {item['bt']}")
            print(f"Category: {item['lmmc']}")
            print(f"Department: {item['dwmc']}")
            print(f"Time: {item['time']}")
            print(f"URL: {scraper.BASE_URL}{item['url']}")

            # Parse timestamp
            dt = scraper.parse_timestamp(item["fbsj"])
            print(f"Publish Time: {dt.isoformat()}")


if __name__ == "__main__":
    main()
