"""FastAPI application for RSS feed generation with scheduled scraping."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Query, Response
from fastapi.responses import HTMLResponse

from config import (
    API_DESCRIPTION,
    API_TITLE,
    API_VERSION,
    MAX_PAGES_PER_RUN,
    MAX_RSS_ITEMS,
    MIN_SCRAPE_INTERVAL,
    RSS_CACHE_MAX_AGE,
    SCRAPE_INTERVAL,
    SERVER_HOST,
    SERVER_PORT,
)
from database import (
    current_timestamp_ms,
    get_last_scrape_time,
    get_recent_articles,
    init_db,
    set_last_scrape_time,
)
from rss import generate_rss, validate_category_input
from scraper import ArticleStateEnum, InfoTsinghuaScraper

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Initialize scheduler
scheduler = AsyncIOScheduler()


async def scrape_articles() -> None:
    """Scrape articles and save to database."""
    # Check if we scraped recently
    last_scrape = get_last_scrape_time()
    now = current_timestamp_ms()

    if last_scrape:
        time_since_last_scrape = (
            now - last_scrape
        ) / 1000  # Convert to seconds
        if time_since_last_scrape < MIN_SCRAPE_INTERVAL:
            logger.info(
                f"Skipping scrape: last scrape was {time_since_last_scrape:.1f} seconds ago (minimum: {MIN_SCRAPE_INTERVAL}s)"
            )
            return

    logger.info("Starting scrape...")

    try:
        with InfoTsinghuaScraper() as scraper:
            # Calculate cutoff time: last_scrape - scrape_interval
            # We stop processing when we reach articles older than this
            cutoff_time_ms = (
                last_scrape - (SCRAPE_INTERVAL * 1000) if last_scrape else 0
            )

            new_count = 0
            updated_count = 0
            skipped_count = 0
            error_count = 0
            total_items = 0

            # Fetch and process pages one at a time
            for page in range(1, MAX_PAGES_PER_RUN + 1):
                items = scraper.fetch_list(lmid="all", page=page, page_size=30)
                if not items:
                    logger.info(f"No more items on page {page}, stopping")
                    break

                total_items += len(items)
                logger.info(f"Fetched page {page}: {len(items)} items")

                for item in items:
                    # Check if article publish time is before cutoff
                    publish_time = item.get("fbsj", 0)
                    if publish_time < cutoff_time_ms:
                        logger.info(
                            f"Reached article {item.get('xxid')} with publish_time {publish_time} < cutoff {cutoff_time_ms}, stopping"
                        )
                        break

                    try:
                        # Insert or update article using scraper method
                        state = scraper.upsert_article(item)
                        if state == ArticleStateEnum.NEW:
                            new_count += 1
                        elif state == ArticleStateEnum.UPDATED:
                            updated_count += 1
                        else:
                            skipped_count += 1
                    except (ValueError, KeyError) as e:
                        # Skip items with missing required fields
                        error_count += 1
                        logger.warning(
                            f"Skipping item {item.get('xxid', 'UNKNOWN')} due to error: {e}"
                        )
                        continue
                else:
                    # Continue to next page if inner loop didn't break
                    continue

                # Break outer loop if inner loop broke (reached cutoff)
                break

            logger.info(
                f"Fetched {total_items} items total. Saved {new_count} new articles, updated {updated_count} existing articles, skipped {skipped_count} existing, {error_count} errors"
            )

            # Update last scrape time
            scrape_end_time = current_timestamp_ms()
            set_last_scrape_time(scrape_end_time)
            logger.info("Updated last scrape timestamp")

    except Exception as e:
        logger.error(f"Error during scrape: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan."""
    # Initialize database
    init_db()
    logger.info("Database initialized")

    # Start scheduler
    scheduler.add_job(
        scrape_articles,
        "interval",
        seconds=SCRAPE_INTERVAL,
        id="scrape_articles",
        replace_existing=True,
    )

    # Run initial scrape
    await scrape_articles()

    scheduler.start()
    logger.info(f"Scheduler started, scraping every {SCRAPE_INTERVAL} seconds")

    yield

    # Shutdown
    scheduler.shutdown()
    logger.info("Scheduler shutdown")


app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version=API_VERSION,
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
async def root() -> Response:
    """Root endpoint serving the public RSS subscription page."""
    html_path = Path(__file__).parent / "templates" / "index.html"

    if not html_path.exists():
        return Response(
            content="<h1>Info Tsinghua RSS Feed</h1><p><a href='/rss'>Subscribe to RSS</a></p>",
            media_type="text/html",
        )

    html_content = html_path.read_text(encoding="utf-8")
    return Response(content=html_content, media_type="text/html")


@app.get("/api/status")
async def api_status() -> dict[str, bool]:
    """API status endpoint for frontend checks."""
    return {
        "auth_enabled": False,
        "authenticated": True,
        "public": True,
    }


# =============================================================================
# RSS Feed Endpoint
# =============================================================================


@app.get("/rss")
async def rss_feed(
    category_in: list[str] | None = Query(
        None, description="Categories to filter in (only these categories)"
    ),
    category_not_in: list[str] | None = Query(
        None,
        alias="not_in",
        description="Categories to filter out (exclude these categories)",
    ),
) -> Response:
    """Generate and return the public RSS feed.

    Query Parameters:
    - category_in: Filter to only include articles with these categories (e.g., ?category_in=通知&category_in=公告)
    - not_in: Exclude articles with these categories (e.g., ?not_in=招聘&not_in=讲座)
    """
    # Validate category inputs
    category_in = validate_category_input(category_in)
    category_not_in = validate_category_input(category_not_in)

    rss_xml = generate_rss(
        limit=MAX_RSS_ITEMS,
        categories_in=category_in,
        categories_not_in=category_not_in,
    )

    response_headers = {
        "Cache-Control": f"public, max-age={RSS_CACHE_MAX_AGE}",
    }

    return Response(
        content=rss_xml,
        media_type="application/rss+xml; charset=utf-8",
        headers=response_headers,
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    _ = get_recent_articles(limit=1)
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
