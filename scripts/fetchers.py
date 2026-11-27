"""
Tech Radar - Multi-source Data Fetchers.

Thu thập ứng viên công nghệ từ GitHub, Reddit (JSON + RSS fallback),
Hacker News (Algolia), Dev.to và Custom RSS / Telegram công khai (t.me/s/...).
Mỗi nguồn được bọc try/except độc lập với timeout=15s để đảm bảo an toàn pipeline.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("tech_radar.fetchers")

REQUEST_TIMEOUT = 15
DEFAULT_USER_AGENT = "TechRadarBot/1.0 (+https://github.com/tech-radar; automated-curator)"
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "ref_src",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


def utc_now_iso() -> str:
    """Trả về thời gian UTC hiện tại theo chuẩn ISO-8601."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonicalize_url(url: str) -> str:
    """Chuẩn hóa URL để chống trùng lặp chính xác."""
    if not url:
        return ""
    raw = url.strip()
    try:
        parsed = urlparse(raw)
        scheme = (parsed.scheme or "https").lower()
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = re.sub(r"/+$", "", parsed.path or "")
        # Lọc bỏ các tham số tracking
        query_items = [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=False)
            if k.lower() not in TRACKING_PARAMS
        ]
        query = urlencode(sorted(query_items))
        return urlunparse((scheme, netloc, path, "", query, ""))
    except Exception:
        return raw.rstrip("/")


def generate_item_id(url: str) -> str:
    """Sinh ID ngắn, cố định theo URL chuẩn hóa."""
    canon = canonicalize_url(url)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


def strip_html(raw_text: str | None, max_len: int = 420) -> str:
    """Loại bỏ thẻ HTML và chuẩn hóa khoảng trắng."""
    if not raw_text:
        return ""
    try:
        soup = BeautifulSoup(raw_text, "html.parser")
        text = soup.get_text(" ", strip=True)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", str(raw_text))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def parse_to_iso_utc(value: Any, fallback: str) -> str:
    """Chuyển đổi nhiều định dạng thời gian về ISO-8601 UTC."""
    if not value:
        return fallback
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
            return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if isinstance(value, time.struct_time):
            dt = datetime.fromtimestamp(time.mktime(value), tz=timezone.utc)
            return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        s = str(value).strip()
        if not s:
            return fallback
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except ValueError:
            dt = parsedate_to_datetime(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except Exception:
        return fallback


def fetch_github(cfg: dict[str, Any], fetched_at: str) -> list[dict[str, Any]]:
    """Thu thập repo GitHub nổi bật theo danh sách topics."""
    if not cfg.get("enabled", True):
        return []

    topics: list[str] = cfg.get("topics", [])
    days_back = int(cfg.get("days_back", 14))
    min_stars = int(cfg.get("min_stars", 20))
    per_topic_limit = int(cfg.get("per_topic_limit", 8))

    since_date = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": DEFAULT_USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    results: list[dict[str, Any]] = []
    seen_local: set[str] = set()

    for topic in topics:
        try:
            query = f"topic:{topic} stars:>={min_stars} pushed:>={since_date} archived:false"
            params = {
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": per_topic_limit,
            }
            resp = requests.get(
                "https://api.github.com/search/repositories",
                headers=headers,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 403:
                logger.warning("GitHub API rate limit hit on topic '%s'. Stopping GitHub fetch.", topic)
                break
            resp.raise_for_status()
            payload = resp.json()

            for repo in payload.get("items", []):
                url = repo.get("html_url", "")
                canon = canonicalize_url(url)
                if not canon or canon in seen_local:
                    continue
                seen_local.add(canon)

                repo_topics = repo.get("topics") or []
                lang = repo.get("language")
                tags = list(dict.fromkeys([t for t in ([topic, lang] + repo_topics[:3]) if t]))[:5]
                desc = strip_html(repo.get("description") or "", 400)
                stars = int(repo.get("stargazers_count") or 0)
                published = parse_to_iso_utc(repo.get("pushed_at") or repo.get("created_at"), fetched_at)

                results.append(
                    {
                        "id": generate_item_id(canon),
                        "title": repo.get("full_name") or repo.get("name") or "Untitled Repo",
                        "url": url,
                        "canonical_url": canon,
                        "source": "GitHub",
                        "stars_or_votes": stars,
                        "description": desc or f"GitHub repository tagged with {topic}.",
                        "tags": tags,
                        "published_at": published,
                        "fetched_at": fetched_at,
                    }
                )
        except Exception as exc:
            logger.warning("GitHub fetch failed for topic '%s': %s", topic, exc)

    logger.info("GitHub fetched %d items across %d topics.", len(results), len(topics))
    return results


def _fetch_reddit_rss_fallback(sub: str, sort: str, limit: int, fetched_at: str) -> list[dict[str, Any]]:
    """Fallback đọc Reddit qua RSS khi endpoint JSON bị giới hạn."""
    rss_url = f"https://www.reddit.com/r/{sub}/{sort}/.rss"
    resp = requests.get(
        rss_url,
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    items: list[dict[str, Any]] = []
    for entry in feed.entries[:limit]:
        url = entry.get("link", "")
        canon = canonicalize_url(url)
        if not canon:
            continue
        published = parse_to_iso_utc(
            entry.get("updated") or entry.get("published") or entry.get("published_parsed"),
            fetched_at,
        )
        desc = strip_html(entry.get("summary") or "", 400)
        items.append(
            {
                "id": generate_item_id(canon),
                "title": strip_html(entry.get("title") or "Reddit Post", 200),
                "url": url,
                "canonical_url": canon,
                "source": "Reddit",
                "stars_or_votes": 20,
                "description": desc,
                "tags": [sub],
                "published_at": published,
                "fetched_at": fetched_at,
            }
        )
    return items


def fetch_reddit(cfg: dict[str, Any], fetched_at: str) -> list[dict[str, Any]]:
    """Thu thập bài viết chất lượng từ các subreddit (JSON + RSS fallback)."""
    if not cfg.get("enabled", True):
        return []

    subreddits: list[str] = cfg.get("subreddits", [])
    sort = cfg.get("sort", "hot")
    limit = int(cfg.get("limit_per_subreddit", 10))
    min_upvotes = int(cfg.get("min_upvotes", 15))

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json",
    }

    results: list[dict[str, Any]] = []
    seen_local: set[str] = set()

    for sub in subreddits:
        sub_items: list[dict[str, Any]] = []
        try:
            url_api = f"https://www.reddit.com/r/{sub}/{sort}.json"
            resp = requests.get(
                url_api,
                headers=headers,
                params={"limit": limit + 5},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code in (403, 429):
                logger.info("Reddit JSON blocked (%d) for r/%s; trying RSS fallback.", resp.status_code, sub)
                sub_items = _fetch_reddit_rss_fallback(sub, sort, limit, fetched_at)
            else:
                resp.raise_for_status()
                children = resp.json().get("data", {}).get("children", [])
                for child in children:
                    post = child.get("data", {})
                    if post.get("stickied") or post.get("over_18"):
                        continue
                    score = int(post.get("ups") or post.get("score") or 0)
                    if score < min_upvotes:
                        continue

                    permalink = f"https://www.reddit.com{post.get('permalink', '')}"
                    ext_url = post.get("url_overridden_by_dest") or ""
                    # Ưu tiên link công cụ/repo bên ngoài nếu không phải ảnh/video Reddit
                    if (
                        ext_url
                        and not any(
                            host in ext_url
                            for host in ("i.redd.it", "v.redd.it", "reddit.com/gallery")
                        )
                    ):
                        target_url = ext_url
                    else:
                        target_url = permalink

                    canon = canonicalize_url(target_url)
                    if not canon:
                        continue

                    flair = post.get("link_flair_text")
                    tags = [t for t in (sub, flair) if t]
                    selftext = strip_html(post.get("selftext") or "", 400)
                    desc = selftext or f"Discussion on r/{sub} ({post.get('num_comments', 0)} comments)."
                    published = parse_to_iso_utc(post.get("created_utc"), fetched_at)

                    sub_items.append(
                        {
                            "id": generate_item_id(canon),
                            "title": strip_html(post.get("title") or "Reddit Post", 220),
                            "url": target_url,
                            "canonical_url": canon,
                            "source": "Reddit",
                            "stars_or_votes": score,
                            "description": desc,
                            "tags": tags[:4],
                            "published_at": published,
                            "fetched_at": fetched_at,
                        }
                    )
                    if len(sub_items) >= limit:
                        break
        except Exception as exc:
            logger.warning("Reddit JSON failed for r/%s (%s); trying RSS fallback.", sub, exc)
            try:
                sub_items = _fetch_reddit_rss_fallback(sub, sort, limit, fetched_at)
            except Exception as rss_exc:
                logger.warning("Reddit RSS fallback also failed for r/%s: %s", sub, rss_exc)

        for item in sub_items:
            if item["canonical_url"] not in seen_local:
                seen_local.add(item["canonical_url"])
                results.append(item)

    logger.info("Reddit fetched %d items across %d subreddits.", len(results), len(subreddits))
    return results


def fetch_hacker_news(cfg: dict[str, Any], fetched_at: str) -> list[dict[str, Any]]:
    """Thu thập bài viết nổi bật trên Hacker News qua Algolia API."""
    if not cfg.get("enabled", True):
        return []

    min_points = int(cfg.get("min_points", 50))
    hours_back = int(cfg.get("hours_back", 48))
    limit = int(cfg.get("limit", 25))
    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours_back)).timestamp())

    results: list[dict[str, Any]] = []
    seen_local: set[str] = set()

    try:
        params = {
            "tags": "story",
            "numericFilters": f"points>={min_points},created_at_i>{cutoff_ts}",
            "hitsPerPage": limit,
        }
        resp = requests.get(
            "https://hn.algolia.com/api/v1/search",
            headers={"User-Agent": DEFAULT_USER_AGENT},
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])

        for hit in hits:
            object_id = hit.get("objectID", "")
            hn_url = f"https://news.ycombinator.com/item?id={object_id}" if object_id else ""
            url = hit.get("url") or hn_url
            canon = canonicalize_url(url)
            if not canon or canon in seen_local:
                continue
            seen_local.add(canon)

            points = int(hit.get("points") or 0)
            comments = int(hit.get("num_comments") or 0)
            title = strip_html(hit.get("title") or "HN Story", 220)
            story_text = strip_html(hit.get("story_text") or "", 350)
            desc = story_text or f"Hacker News story with {points} points and {comments} comments."
            published = parse_to_iso_utc(hit.get("created_at") or hit.get("created_at_i"), fetched_at)

            tags = ["hackernews"]
            lower_title = title.lower()
            if lower_title.startswith("show hn"):
                tags.append("show-hn")
            elif lower_title.startswith("ask hn"):
                tags.append("ask-hn")

            results.append(
                {
                    "id": generate_item_id(canon),
                    "title": title,
                    "url": url,
                    "canonical_url": canon,
                    "source": "HN",
                    "stars_or_votes": points,
                    "description": desc,
                    "tags": tags,
                    "published_at": published,
                    "fetched_at": fetched_at,
                }
            )
    except Exception as exc:
        logger.warning("Hacker News fetch failed: %s", exc)

    logger.info("Hacker News fetched %d items.", len(results))
    return results


def fetch_devto(cfg: dict[str, Any], fetched_at: str) -> list[dict[str, Any]]:
    """Thu thập bài viết kỹ thuật & thủ thuật từ Dev.to API."""
    if not cfg.get("enabled", True):
        return []

    tags_cfg: list[str] = cfg.get("tags", [])
    top_days = int(cfg.get("top_days", 3))
    per_tag_limit = int(cfg.get("per_tag_limit", 8))
    min_reactions = int(cfg.get("min_reactions", 5))

    results: list[dict[str, Any]] = []
    seen_local: set[str] = set()

    for tag in tags_cfg:
        try:
            params = {
                "tag": tag,
                "top": top_days,
                "per_page": per_tag_limit,
            }
            resp = requests.get(
                "https://dev.to/api/articles",
                headers={"User-Agent": DEFAULT_USER_AGENT},
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            articles = resp.json()

            for art in articles:
                reactions = int(art.get("public_reactions_count") or art.get("positive_reactions_count") or 0)
                if reactions < min_reactions:
                    continue
                url = art.get("url") or art.get("canonical_url") or ""
                canon = canonicalize_url(url)
                if not canon or canon in seen_local:
                    continue
                seen_local.add(canon)

                art_tags = art.get("tag_list") or []
                if isinstance(art_tags, str):
                    art_tags = [t.strip() for t in art_tags.split(",") if t.strip()]
                combined_tags = list(dict.fromkeys([tag] + list(art_tags)))[:4]
                published = parse_to_iso_utc(art.get("published_at") or art.get("published_timestamp"), fetched_at)

                results.append(
                    {
                        "id": generate_item_id(canon),
                        "title": strip_html(art.get("title") or "Dev.to Article", 220),
                        "url": url,
                        "canonical_url": canon,
                        "source": "Dev.to",
                        "stars_or_votes": reactions,
                        "description": strip_html(art.get("description") or "", 400),
                        "tags": combined_tags,
                        "published_at": published,
                        "fetched_at": fetched_at,
                    }
                )
        except Exception as exc:
            logger.warning("Dev.to fetch failed for tag '%s': %s", tag, exc)

    logger.info("Dev.to fetched %d items across %d tags.", len(results), len(tags_cfg))
    return results


def _fetch_telegram_public_channel(name: str, url: str, limit: int, fetched_at: str) -> list[dict[str, Any]]:
    """Thu thập bài đăng từ kênh Telegram công khai dạng https://t.me/s/<channel>."""
    resp = requests.get(
        url,
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    messages = soup.select(".tgme_widget_message_wrap")
    items: list[dict[str, Any]] = []

    for wrap in reversed(messages):
        text_el = wrap.select_one(".tgme_widget_message_text")
        if not text_el:
            continue
        text = strip_html(str(text_el), 420)
        if len(text) < 25:
            continue

        link_el = wrap.select_one("a.tgme_widget_message_date")
        post_url = link_el["href"] if link_el and link_el.has_attr("href") else url
        canon = canonicalize_url(post_url)
        if not canon:
            continue

        time_el = wrap.select_one("time[datetime]")
        published = parse_to_iso_utc(time_el["datetime"] if time_el else None, fetched_at)

        views = 0
        views_el = wrap.select_one(".tgme_widget_message_views")
        if views_el:
            v_txt = views_el.get_text(strip=True).upper()
            try:
                if v_txt.endswith("K"):
                    views = int(float(v_txt[:-1]) * 1000)
                elif v_txt.endswith("M"):
                    views = int(float(v_txt[:-1]) * 1_000_000)
                else:
                    views = int(re.sub(r"[^\d]", "", v_txt) or "0")
            except ValueError:
                views = 0

        title = text.split("\n")[0][:120]
        items.append(
            {
                "id": generate_item_id(canon),
                "title": title,
                "url": post_url,
                "canonical_url": canon,
                "source": "RSS",
                "stars_or_votes": views,
                "description": text,
                "tags": ["telegram", name[:20].lower().replace(" ", "-")],
                "published_at": published,
                "fetched_at": fetched_at,
            }
        )
        if len(items) >= limit:
            break

    return items


def fetch_custom_rss(feeds: list[dict[str, Any]], fetched_at: str) -> list[dict[str, Any]]:
    """Thu thập từ danh sách custom RSS/Atom feeds và kênh Telegram công khai (t.me/s/...)."""
    if not feeds:
        return []

    results: list[dict[str, Any]] = []
    seen_local: set[str] = set()

    for feed_cfg in feeds:
        if not feed_cfg.get("enabled", True):
            continue
        name = feed_cfg.get("name") or "Custom Feed"
        url = (feed_cfg.get("url") or "").strip()
        limit = int(feed_cfg.get("limit", 6))
        if not url:
            continue

        try:
            if "t.me/s/" in url:
                feed_items = _fetch_telegram_public_channel(name, url, limit, fetched_at)
            else:
                resp = requests.get(
                    url,
                    headers={"User-Agent": DEFAULT_USER_AGENT},
                    timeout=REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                parsed = feedparser.parse(resp.content)
                feed_items = []
                for entry in parsed.entries[:limit]:
                    entry_url = entry.get("link", "")
                    canon = canonicalize_url(entry_url)
                    if not canon:
                        continue
                    published = parse_to_iso_utc(
                        entry.get("published")
                        or entry.get("updated")
                        or entry.get("published_parsed")
                        or entry.get("updated_parsed"),
                        fetched_at,
                    )
                    summary_raw = entry.get("summary") or entry.get("description") or ""
                    if not summary_raw and entry.get("content"):
                        summary_raw = entry["content"][0].get("value", "")
                    entry_tags = [
                        t.get("term") for t in (entry.get("tags") or []) if isinstance(t, dict) and t.get("term")
                    ]
                    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:20]
                    tags = list(dict.fromkeys([slug] + entry_tags[:3]))[:4]
                    feed_items.append(
                        {
                            "id": generate_item_id(canon),
                            "title": strip_html(entry.get("title") or name, 220),
                            "url": entry_url,
                            "canonical_url": canon,
                            "source": "RSS",
                            "stars_or_votes": 0,
                            "description": strip_html(summary_raw, 420),
                            "tags": tags,
                            "published_at": published,
                            "fetched_at": fetched_at,
                        }
                    )

            for item in feed_items:
                if item["canonical_url"] not in seen_local:
                    seen_local.add(item["canonical_url"])
                    results.append(item)
        except Exception as exc:
            logger.warning("Custom RSS/Telegram fetch failed for '%s' (%s): %s", name, url, exc)

    logger.info("Custom RSS/Telegram fetched %d items.", len(results))
    return results


def fetch_all_sources(config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Chạy toàn bộ các fetcher độc lập và tổng hợp danh sách ứng viên thô."""
    fetched_at = utc_now_iso()
    all_items: list[dict[str, Any]] = []
    stats: dict[str, int] = {}

    source_runners = [
        ("GitHub", lambda: fetch_github(config.get("github", {}), fetched_at)),
        ("Reddit", lambda: fetch_reddit(config.get("reddit", {}), fetched_at)),
        ("HN", lambda: fetch_hacker_news(config.get("hacker_news", {}), fetched_at)),
        ("Dev.to", lambda: fetch_devto(config.get("devto", {}), fetched_at)),
        ("RSS", lambda: fetch_custom_rss(config.get("custom_rss", []), fetched_at)),
    ]

    for source_name, runner in source_runners:
        try:
            items = runner()
            stats[source_name] = len(items)
            all_items.extend(items)
        except Exception as exc:
            logger.error("Unexpected failure in %s fetcher: %s", source_name, exc)
            stats[source_name] = 0

    return all_items, stats
