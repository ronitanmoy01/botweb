import json as jsonlib
import logging
import queue
import re
import threading
import time
import uuid

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"

RESULTS_DIR.mkdir(parents=True, exist_ok=True)

MANUAL_VERIFICATION_TIMEOUT = 600

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logger = logging.getLogger("imdb-scraper")

job_queue = queue.Queue()

jobs = {}
jobs_lock = threading.Lock()

driver = None
driver_lock = threading.Lock()


# ============================================================
# REQUEST MODEL
# ============================================================

class ScrapeRequest(BaseModel):
    url: str = Field(
        ...,
        examples=["https://www.imdb.com/title/tt6751668/"],
    )


# ============================================================
# VALIDATE IMDb URL
# ============================================================

def extract_imdb_id(url: str) -> str:
    parsed = urlparse(url)

    if parsed.hostname not in {
        "imdb.com",
        "www.imdb.com",
    }:
        raise ValueError("Only IMDb URLs are supported.")

    match = re.search(r"/title/(tt\d+)", parsed.path)

    if not match:
        raise ValueError("Invalid IMDb title URL.")

    return match.group(1)


# ============================================================
# PERSISTENT CHROME BROWSER
# ============================================================

def create_driver():
    options = Options()

    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")

    options.page_load_strategy = "eager"

    browser = webdriver.Chrome(options=options)
    browser.set_page_load_timeout(60)

    logger.info("Chrome browser started.")
    return browser


def get_driver():
    global driver

    with driver_lock:

        if driver is not None:
            try:
                _ = driver.current_url
                return driver
            except WebDriverException:
                logger.warning("Existing Chrome session is unavailable.")
                driver = None

        driver = create_driver()
        return driver


def close_driver():
    global driver

    with driver_lock:

        if driver is not None:
            try:
                driver.quit()
                logger.info("Chrome browser closed.")
            except WebDriverException:
                pass
            finally:
                driver = None


# ============================================================
# AWS WAF DETECTION
# ============================================================

def is_waf_challenge(browser) -> bool:
    try:
        html = browser.page_source.lower()

        markers = ["awswaf", "aws-waf", "challenge-container", "gokuprops"]
        has_waf_marker = any(m in html for m in markers)

        title_found = browser.find_elements(
            By.CSS_SELECTOR,
            'span.hero__primary-text[data-testid="hero__primary-text"]',
        )

        return has_waf_marker and not title_found

    except WebDriverException:
        return False


# ============================================================
# MANUAL VERIFICATION
# ============================================================

def wait_for_manual_verification(browser, timeout=MANUAL_VERIFICATION_TIMEOUT):
    logger.warning("=" * 60)
    logger.warning("IMDb AWS WAF VERIFICATION DETECTED")
    logger.warning("Complete verification in the Chrome window.")
    logger.warning("DO NOT CLOSE CHROME.")
    logger.warning("Waiting up to %s seconds.", timeout)
    logger.warning("=" * 60)

    start = time.monotonic()

    while time.monotonic() - start < timeout:
        try:
            title_elements = browser.find_elements(
                By.CSS_SELECTOR,
                'span.hero__primary-text[data-testid="hero__primary-text"]',
            )

            for element in title_elements:
                if element.is_displayed() and element.text.strip():
                    logger.info(
                        "IMDb verification completed. Page: %s",
                        element.text.strip(),
                    )
                    return True

        except WebDriverException as exc:
            logger.warning("Browser is navigating: %s", exc)

        time.sleep(2)

    raise TimeoutError("Manual IMDb verification timed out.")


# ============================================================
# JSON-LD HELPERS
# ============================================================

def _iter_jsonld_entities(data):
    if isinstance(data, list):
        for item in data:
            yield item
    elif isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield item


def _select_title_entity(entities):
    preferred = {
        "Movie", "TVSeries", "TVEpisode", "TVMiniSeries",
        "TVSeason", "VideoGame", "CreativeWork",
    }

    for item in entities:
        if not isinstance(item, dict):
            continue
        t = item.get("@type")
        types = t if isinstance(t, list) else [t]
        types = [x for x in types if isinstance(x, str)]
        if any(x in preferred for x in types):
            return item

    return {}


def extract_jsonld_from_soup(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = jsonlib.loads(raw)
        except (ValueError, TypeError):
            continue

        entity = _select_title_entity(_iter_jsonld_entities(data))
        if entity:
            return entity

    return {}


def extract_jsonld_from_browser(browser):
    try:
        scripts = browser.execute_script(
            """
            return Array.from(
                document.querySelectorAll('script[type="application/ld+json"]')
            ).map(s => s.textContent);
            """
        )
    except WebDriverException:
        return {}

    for raw in scripts or []:
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            data = jsonlib.loads(raw)
        except (ValueError, TypeError):
            continue

        entity = _select_title_entity(_iter_jsonld_entities(data))
        if entity:
            return entity

    return {}


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []

    out = []
    seen = set()
    for v in value:
        if not isinstance(v, str):
            continue
        v = v.strip()
        if v and v not in seen:
            out.append(v)
            seen.add(v)
    return out


def _jsonld_names(value):
    if not value:
        return []
    if not isinstance(value, list):
        value = [value]

    out = []
    seen = set()
    for item in value:
        name = None
        if isinstance(item, dict):
            name = item.get("name")
        elif isinstance(item, str):
            name = item
        if isinstance(name, str):
            n = name.strip()
            if n and n not in seen:
                out.append(n)
                seen.add(n)
    return out


def _first_string(value):
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                v = item.strip()
                if v:
                    return v
    return None


def _get_image_url(value):
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for k in ("url", "contentUrl"):
            v = value.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None
    if isinstance(value, list):
        for item in value:
            url = _get_image_url(item)
            if url:
                return url
    return None


# ============================================================
# JSON-LD @type -> "type" FIELD MAPPING
# ============================================================

JSONLD_TYPE_TO_KIND = {
    "Movie": "movie",
    "TVSeries": "series",
    "TVMiniSeries": "series",
    "TVEpisode": "series",
    "TVSeason": "series",
    "VideoGame": "game",
    "PodcastSeries": "podcast",
    "PodcastEpisode": "podcast",
}


def map_jsonld_type_to_kind(ld_type):
    if isinstance(ld_type, list):
        for t in ld_type:
            if isinstance(t, str) and t in JSONLD_TYPE_TO_KIND:
                return JSONLD_TYPE_TO_KIND[t]
        return None
    if isinstance(ld_type, str):
        return JSONLD_TYPE_TO_KIND.get(ld_type)
    return None


def _is_tvepisode(ld_type):
    if isinstance(ld_type, str):
        return ld_type == "TVEpisode"
    if isinstance(ld_type, list):
        return "TVEpisode" in ld_type
    return False


# ============================================================
# SCROLL HELPERS
# ============================================================

def scroll_into_view(browser, selector, wait=1.5):
    try:
        elements = browser.find_elements(By.CSS_SELECTOR, selector)
        if elements:
            browser.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});",
                elements[0],
            )
            time.sleep(wait)
            return True
    except WebDriverException:
        pass
    return False


def scroll_page_to_render_lazy_content(browser):
    for selector in (
        'li[data-testid="title-pc-principal-credit"]',
        'li[data-testid="title-details-origin"]',
        'li[data-testid="title-details-languages"]',
        'li[data-testid="storyline-genres"]',
        'div.ipc-chip-list__scroller',
        'a[href*="/awards/"][href*="tt_awd"]',
        'a[href*="/releaseinfo/"]',
        '.metacritic-score-box',
        'li[data-testid="title-boxoffice"]',
    ):
        scroll_into_view(browser, selector)

    try:
        browser.execute_script(
            "window.scrollTo(0, document.body.scrollHeight);"
        )
        time.sleep(2)
        browser.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)
    except WebDriverException:
        pass


# ============================================================
# LIVE-DOM GENRE FALLBACKS
# ============================================================

def extract_genres_from_browser(browser):
    selectors = (
        'a.ipc-metadata-list-item__list-content-item--link'
        '[href*="tt_stry_gnr"]',
        'a.ipc-metadata-list-item__list-content-item--link'
        '[href*="/search/title/?genres="]',
        'a[href*="tt_stry_gnr"]',
        'a[href*="/search/title/?genres="]',
    )

    for selector in selectors:
        try:
            elements = browser.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue

        genres = []
        seen = set()

        for el in elements:
            try:
                text = el.text.strip()
            except WebDriverException:
                continue

            if not text:
                continue
            if text.lower() in {"see more", "see all", "genres"}:
                continue
            if text not in seen:
                genres.append(text)
                seen.add(text)

        if genres:
            return genres

    return []


# ============================================================
# CREDITS + AWARDS + RATING EXTRACTION
# ============================================================

def _dedup_links(container, selector):
    out = []
    seen = set()
    for a in container.select(selector):
        text = a.get_text(strip=True)
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _extract_credits(soup, jsonld, content_type):
    director_names = []
    creator_names = []
    writers_names = []
    actors_names = []

    for li in soup.select('li[data-testid="title-pc-principal-credit"]'):
        label_el = li.select_one(
            'span.ipc-metadata-list-item__label, '
            'a.ipc-metadata-list-item__label--link'
        )
        if not label_el:
            continue

        label = label_el.get_text(strip=True).lower()

        names = _dedup_links(
            li,
            "a.ipc-metadata-list-item__list-content-item--link",
        )
        if not names:
            continue

        if label == "director":
            director_names = names
        elif label == "creator":
            creator_names = names
        elif label in ("writers", "writer"):
            writers_names = names
        elif label == "stars":
            actors_names = names

    jsonld_creator = _jsonld_names(jsonld.get("creator"))

    if content_type == "series":
        if not creator_names:
            creator_names = jsonld_creator
        director_names = []
        writers_names = []
    else:
        if not director_names:
            director_names = _jsonld_names(jsonld.get("director"))
        if not writers_names:
            writers_names = jsonld_creator
        creator_names = []

    if not actors_names:
        actors_names = _jsonld_names(jsonld.get("actor"))

    return {
        "director": director_names,
        "creator": creator_names,
        "writer": writers_names,
        "actors": actors_names,
    }


def _extract_awards(soup):
    headline = None
    total = None

    for a in soup.select('a[href*="/awards/"][href*="tt_awd"]'):
        if a.select_one("svg"):
            continue
        if "icon-link" in (a.get("class") or []):
            continue

        text = a.get_text(strip=True)
        if not text:
            continue
        if text.lower() in {
            "see more",
            "see more awards and nominations",
        }:
            continue

        headline = text
        break

    for span in soup.select(
        'span.ipc-metadata-list-item__list-content-item'
    ):
        text = span.get_text(strip=True)
        if re.search(r"\d+\s+wins?\b", text, re.IGNORECASE):
            total = text
            break

    if headline and total:
        return f"{headline}. {total}"
    if headline:
        return headline
    if total:
        return total
    return None


def _find_rating_bar_root(soup):
    score_div = soup.select_one(
        '[data-testid="hero-rating-bar__aggregate-rating__score"]'
    )
    if score_div is None:
        return None, None
    return score_div, score_div.parent


def _extract_rating(jsonld, soup):
    imdb_rating = None
    imdb_votes = None
    rating_display = None

    score_display = None
    votes_display = None

    score_div, wrapper = _find_rating_bar_root(soup)

    if score_div is not None:
        score_span = score_div.select_one("span")
        if score_span:
            score_display = score_span.get_text(strip=True)

        if wrapper is not None:
            for sib in wrapper.find_all("div", recursive=False):
                if sib is score_div:
                    continue
                text = sib.get_text(strip=True)
                if not text:
                    continue
                if re.fullmatch(r"[\d.,]+[KMB]?", text):
                    votes_display = text
                    break

    agg = jsonld.get("aggregateRating") or {}
    if isinstance(agg, dict):
        rv = agg.get("ratingValue")
        if rv is not None:
            try:
                imdb_rating = float(rv)
            except (TypeError, ValueError):
                imdb_rating = str(rv)

        rc = agg.get("ratingCount")
        if rc is not None:
            try:
                imdb_votes = int(rc)
            except (TypeError, ValueError):
                imdb_votes = str(rc)

    if imdb_rating is None and score_display:
        try:
            imdb_rating = float(score_display)
        except ValueError:
            imdb_rating = score_display

    if imdb_votes is None and votes_display:
        imdb_votes = votes_display

    score_for_display = score_display
    if score_for_display is None and imdb_rating is not None:
        score_for_display = str(imdb_rating)

    votes_for_display = votes_display
    if votes_for_display is None and imdb_votes is not None:
        votes_for_display = str(imdb_votes)

    if score_for_display and votes_for_display:
        rating_display = (
            f"{score_for_display}/10 "
            f"based on {votes_for_display} user ratings"
        )
    elif score_for_display:
        rating_display = f"{score_for_display}/10"

    return imdb_rating, imdb_votes, rating_display


def _extract_metascore(soup):
    el = soup.select_one('.metacritic-score-box')
    if el:
        text = el.get_text(strip=True)
        return text or None
    return None


def _extract_box_office(soup):
    for label_span in soup.find_all(
        "span",
        class_="ipc-metadata-list-item__label",
    ):
        if "gross worldwide" in label_span.get_text(strip=True).lower():
            value_span = label_span.find_next(
                "span",
                class_="ipc-metadata-list-item__list-content-item",
            )
            if value_span:
                text = value_span.get_text(strip=True)
                return text or None
    return None


# ============================================================
# POSTER + THUMBNAIL HELPERS
# ============================================================

def _derive_sized_poster(poster_url):
    if not poster_url:
        return None

    if re.search(r"_V1_\.jpg$", poster_url):
        return re.sub(
            r"_V1_\.jpg$",
            "_V1_QL75_UY562_CR2,0,380,562_.jpg",
            poster_url,
        )

    if re.search(r"_V1_[^/]*\.jpg$", poster_url):
        return re.sub(
            r"_V1_[^/]*\.jpg$",
            "_V1_QL75_UY562_CR2,0,380,562_.jpg",
            poster_url,
        )

    return poster_url


def _extract_trailer_thumbnail(jsonld):
    trailer = jsonld.get("trailer") or {}
    if not isinstance(trailer, dict):
        return None

    thumb = trailer.get("thumbnailUrl")
    if isinstance(thumb, str) and thumb.strip():
        return thumb.strip()

    thumb_obj = trailer.get("thumbnail") or {}
    if isinstance(thumb_obj, dict):
        content_url = thumb_obj.get("contentUrl")
        if isinstance(content_url, str) and content_url.strip():
            return content_url.strip()

    return None


# ============================================================
# SERIES INFO (episodes, total_seasons, series_id)
# ============================================================

def _extract_series_info(soup, jsonld, content_type, imdb_id):
    if content_type != "series":
        return {
            "episodes": None,
            "total_seasons": None,
            "series_id": None,
        }

    episodes = None
    total_seasons = None
    series_id = None

    ep_el = soup.select_one(
        'span[data-testid="hero-subnav-bar-series-episode-count"]'
    )
    if ep_el:
        text = ep_el.get_text(strip=True)
        if text:
            episodes = text

    if _is_tvepisode(jsonld.get("@type")):
        ps = jsonld.get("partOfSeries")
        if isinstance(ps, dict):
            url = ps.get("url")
            if isinstance(url, str):
                m = re.search(r"(tt\d+)", url)
                if m:
                    series_id = m.group(1)

    if not series_id:
        series_id = imdb_id

    if not total_seasons:
        for el in soup.find_all(
            string=re.compile(r"\b\d+\s+Seasons?\b")
        ):
            match = re.search(r"\b(\d+)\s+Seasons?\b", el)
            if match:
                total_seasons = match.group(1)
                break

    return {
        "episodes": episodes,
        "total_seasons": total_seasons,
        "series_id": series_id,
    }


# ============================================================
# EPISODES PAGE — TOTAL SEASONS
# ============================================================

def extract_total_seasons(browser, imdb_id, timeout=15):
    url = f"https://www.imdb.com/title/{imdb_id}/episodes/"

    try:
        browser.get(url)
    except TimeoutException:
        logger.warning(
            "[episodes] Navigation timed out for %s", imdb_id
        )
        return None
    except WebDriverException as exc:
        logger.warning(
            "[episodes] Navigation failed for %s: %s", imdb_id, exc
        )
        return None

    time.sleep(2)

    try:
        WebDriverWait(browser, timeout).until(
            lambda d: d.find_elements(
                By.CSS_SELECTOR, '[data-testid="tab-season-entry"]'
            )
        )
    except TimeoutException:
        logger.warning(
            "[episodes] No season tabs found for %s", imdb_id
        )
        return None

    try:
        season_tabs = browser.find_elements(
            By.CSS_SELECTOR, '[data-testid="tab-season-entry"]'
        )

        season_numbers = []
        for tab in season_tabs:
            try:
                text = (tab.text or "").strip()
            except WebDriverException:
                continue
            if re.fullmatch(r"\d+", text):
                season_numbers.append(int(text))

        if not season_numbers:
            return None

        return str(max(season_numbers))

    except WebDriverException as exc:
        logger.warning(
            "[episodes] Failed to read season tabs for %s: %s",
            imdb_id,
            exc,
        )
        return None


# ============================================================
# TAGLINES PAGE
# ============================================================

def extract_tagline(browser, imdb_id, timeout=15):
    url = f"https://www.imdb.com/title/{imdb_id}/taglines/"

    try:
        browser.get(url)
    except TimeoutException:
        logger.warning("[tagline] Navigation timed out for %s", imdb_id)
        return None
    except WebDriverException as exc:
        logger.warning("[tagline] Navigation failed for %s: %s", imdb_id, exc)
        return None

    time.sleep(2)

    try:
        WebDriverWait(browser, timeout).until(
            lambda d: d.find_elements(
                By.CSS_SELECTOR, 'li[data-testid="list-item"]'
            )
        )
    except TimeoutException:
        logger.warning("[tagline] No tagline list found for %s", imdb_id)
        return None

    try:
        html = browser.page_source
        soup = BeautifulSoup(html, "html.parser")

        tagline_li = soup.find("li", {"data-testid": "list-item"})
        if tagline_li:
            tagline_div = tagline_li.find(
                "div", class_="ipc-html-content-inner-div"
            )
            if tagline_div:
                text = tagline_div.get_text(strip=True)
                if text:
                    return text
    except Exception as exc:
        logger.warning(
            "[tagline] Extraction failed for %s: %s", imdb_id, exc
        )

    return None


# ============================================================
# PLOT SUMMARY PAGE
# ============================================================

def _clean_paragraph_text(div):
    """
    Extract readable paragraph text from an HTML content div.

    - <br> becomes a newline (hard line break)
    - each block of text becomes a paragraph
    - empty lines are removed
    - multiple consecutive breaks collapse to one blank line
    """
    # Convert <br> tags to newlines so get_text sees them.
    for br in div.find_all("br"):
        br.replace_with("\n")

    # get_text with \n separator and strip=True trims each segment.
    raw = div.get_text("\n", strip=True)

    # Split into lines, strip, drop empties.
    lines = [line.strip() for line in raw.split("\n")]
    lines = [line for line in lines if line]

    # Join lines with "\n\n" so each becomes its own paragraph.
    return "\n\n".join(lines)


def extract_plotsummary(browser, imdb_id, timeout=15):
    """
    Navigate to /title/{imdb_id}/plotsummary/ and return ALL
    plot summaries as ONE string, with proper paragraph breaks.

    Layout on the page:
        <li data-testid="list-item">
          <div class="ipc-html-content-inner-div">SUMMARY</div>
        </li>

    Result layout:
        SUMMARY 1 (paragraph)
        <blank line>
        SUMMARY 2 (paragraph)
        <blank line>
        SUMMARY 3 (paragraph)
        ...

    Each summary is a paragraph; internal <br> / text blocks inside
    a summary also become paragraphs. Whitespace is normalised, so
    every paragraph is separated by exactly one blank line — the
    same visual spacing you see on the IMDb page.
    """
    url = f"https://www.imdb.com/title/{imdb_id}/plotsummary/"

    try:
        browser.get(url)
    except TimeoutException:
        logger.warning("[plotsummary] Navigation timed out for %s", imdb_id)
        return None
    except WebDriverException as exc:
        logger.warning(
            "[plotsummary] Navigation failed for %s: %s", imdb_id, exc
        )
        return None

    time.sleep(2)

    try:
        WebDriverWait(browser, timeout).until(
            lambda d: d.find_elements(
                By.CSS_SELECTOR, 'li[data-testid="list-item"]'
            )
        )
    except TimeoutException:
        logger.warning(
            "[plotsummary] No plot summaries found for %s", imdb_id
        )
        return None

    try:
        html = browser.page_source
        soup = BeautifulSoup(html, "html.parser")

        summaries = []
        seen = set()

        for li in soup.find_all("li", {"data-testid": "list-item"}):
            div = li.find("div", class_="ipc-html-content-inner-div")
            if not div:
                continue

            text = _clean_paragraph_text(div)
            if text and text not in seen:
                summaries.append(text)
                seen.add(text)

        if not summaries:
            return None

        # Each summary becomes its own paragraph.
        # Paragraphs are separated by a blank line ("\n\n"),
        # which reads cleanly in any plain-text viewer / DB column.
        result = "\n\n".join(summaries)

        # Final safety: collapse 3+ newlines to exactly 2.
        result = re.sub(r"\n{3,}", "\n\n", result)

        return result.strip() or None

    except Exception as exc:
        logger.warning(
            "[plotsummary] Extraction failed for %s: %s", imdb_id, exc
        )
        return None


# ============================================================
# EXTRACT TITLE, ORIGINAL TITLE, AND METADATA
# ============================================================

def extract_titles(html: str, imdb_id: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    jsonld = extract_jsonld_from_soup(soup)

    title_element = soup.select_one(
        'span.hero__primary-text[data-testid="hero__primary-text"]'
    )
    title = title_element.get_text(strip=True) if title_element else None

    original_element = soup.select_one("div.baseAlt")
    original_title = None
    if original_element:
        original_text = original_element.get_text(" ", strip=True)
        original_title = re.sub(
            r"^Original title:\s*",
            "",
            original_text,
            flags=re.IGNORECASE,
        ).strip()

    plot = _first_string(jsonld.get("description"))
    raw_poster = _get_image_url(jsonld.get("image"))

    og_poster = raw_poster
    poster = _derive_sized_poster(raw_poster)

    imdb_link = _first_string(jsonld.get("url"))
    thumbnail = _extract_trailer_thumbnail(jsonld)

    release_year = None
    year_element = soup.select_one(
        'a[href*="/releaseinfo/"][href*="tt_ov_rdat"]'
    )
    if year_element:
        release_year = year_element.get_text(strip=True)

    if not release_year and jsonld.get("datePublished"):
        m = re.match(r"(\d{4})", str(jsonld["datePublished"]))
        if m:
            release_year = m.group(1)

    release_date = None
    date_element = soup.select_one(
        'a.ipc-metadata-list-item__list-content-item--link'
        '[href*="tt_dt_rdat"]'
    )
    if date_element:
        release_date = date_element.get_text(strip=True)

    if not release_date:
        for el in soup.select('a[href*="tt_dt_rdat"]'):
            text = el.get_text(strip=True)
            if text and re.search(r"\d{4}", text):
                release_date = text
                break

    content_type = map_jsonld_type_to_kind(jsonld.get("@type"))
    if not content_type:
        episode_count_element = soup.select_one(
            'span[data-testid="hero-subnav-bar-series-episode-count"]'
        )
        content_type = "series" if episode_count_element else "movie"

    content_rating = None
    rating_element = soup.select_one(
        'a[href*="/parentalguide/"][href*="#certificates"]'
    )
    if rating_element:
        content_rating = rating_element.get_text(strip=True)

    if not content_rating and jsonld.get("contentRating"):
        content_rating = str(jsonld["contentRating"]).strip() or None

    runtime = None
    for subtext in soup.find_all(
        "span",
        class_="ipc-metadata-list-item__list-content-item--subText",
    ):
        if re.search(r"\d+\s*min", subtext.get_text()):
            parent_li = subtext.find_parent("li")
            if parent_li:
                main_span = parent_li.find(
                    "span",
                    class_="ipc-metadata-list-item__list-content-item",
                )
                if main_span:
                    runtime = main_span.get_text(strip=True)
                    break

    if not runtime and jsonld.get("duration"):
        m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", str(jsonld["duration"]))
        if m:
            hours = int(m.group(1) or 0)
            mins = int(m.group(2) or 0)
            if hours:
                runtime = f"{hours}h {mins}m" if mins else f"{hours}h"
            elif mins:
                runtime = f"{mins}m"

    genres = _as_list(jsonld.get("genre"))

    if not genres:
        seen = set()
        for container in soup.select('li[data-testid="storyline-genres"]'):
            for a in container.select(
                "a.ipc-metadata-list-item__list-content-item--link"
            ):
                text = a.get_text(strip=True)
                if text and text not in seen:
                    genres.append(text)
                    seen.add(text)
            if genres:
                break

    if not genres:
        seen = set()
        for a in soup.select('a[href*="tt_stry_gnr"]'):
            text = a.get_text(strip=True)
            if text and text not in seen:
                genres.append(text)
                seen.add(text)

    subgenres = []
    for chip in soup.select('a.ipc-chip[href*="tt_ov_in_"]'):
        text_el = chip.select_one(".ipc-chip__text")
        if text_el:
            subgenres.append(text_el.get_text(strip=True))

    country = []
    country_container = soup.select_one(
        'li[data-testid="title-details-origin"]'
    )
    if country_container:
        country = _dedup_links(
            country_container,
            "a.ipc-metadata-list-item__list-content-item--link",
        )

    language = []
    language_container = soup.select_one(
        'li[data-testid="title-details-languages"]'
    )
    if language_container:
        language = _dedup_links(
            language_container,
            "a.ipc-metadata-list-item__list-content-item--link",
        )

    if not language:
        language = _as_list(jsonld.get("inLanguage"))

    credits = _extract_credits(soup, jsonld, content_type)

    awards = _extract_awards(soup)

    imdb_rating, imdb_votes, rating_display = _extract_rating(jsonld, soup)

    metascore = _extract_metascore(soup)

    box_office = _extract_box_office(soup)

    result = {
        "imdb_id": imdb_id,
        "title": title,
        "original_title": original_title,
        "release_year": release_year,
        "release_date": release_date,
        "type": content_type,
        "content_rating": content_rating,
        "runtime": runtime,
        "genres": genres,
        "subgenres": subgenres,
        "country": country,
        "language": language,
        "director": credits["director"],
        "creator": credits["creator"],
        "writer": credits["writer"],
        "actors": credits["actors"],
        "awards": awards,
        "rating": rating_display,
        "imdb_rating": imdb_rating,
        "imdb_votes": imdb_votes,
        "metascore": metascore,
        "box_office": box_office,
    }

    if content_type == "series":
        series_info = _extract_series_info(
            soup, jsonld, content_type, imdb_id
        )
        result["episodes"] = series_info["episodes"]
        result["total_seasons"] = series_info["total_seasons"]
        result["series_id"] = series_info["series_id"]

    result["tagline"] = None
    result["plot"] = plot
    result["plotsummary"] = None      # filled in by scrape_imdb
    result["imdb_link"] = imdb_link
    result["og_poster"] = og_poster
    result["poster"] = poster
    result["thumbnail"] = thumbnail

    return result


# ============================================================
# SCRAPE IMDb
# ============================================================

def scrape_imdb(url: str, job_id: str) -> dict:

    imdb_id = extract_imdb_id(url)

    logger.info("[%s] Starting scrape: %s", job_id, imdb_id)

    browser = get_driver()

    try:
        browser.get(url)
    except TimeoutException:
        logger.warning("[%s] Initial navigation timed out.", job_id)

    time.sleep(3)

    if is_waf_challenge(browser):
        wait_for_manual_verification(
            browser,
            timeout=MANUAL_VERIFICATION_TIMEOUT,
        )

    WebDriverWait(browser, 30).until(
        lambda d: any(
            element.is_displayed()
            and element.text.strip()
            for element in d.find_elements(
                By.CSS_SELECTOR,
                'span.hero__primary-text[data-testid="hero__primary-text"]',
            )
        )
    )

    time.sleep(2)

    scroll_page_to_render_lazy_content(browser)

    html = browser.page_source
    result = extract_titles(html, imdb_id)

    if not result["title"]:
        raise RuntimeError("IMDb display title was not found.")

    # Genre fallbacks (must happen BEFORE tagline navigation).
    if not result["genres"]:
        live_jsonld = extract_jsonld_from_browser(browser)
        live_genres = _as_list(live_jsonld.get("genre"))

        if live_genres:
            logger.info(
                "[%s] Genres recovered from live JSON-LD: %s",
                job_id,
                live_genres,
            )
            result["genres"] = live_genres

    if not result["genres"]:
        live_genres = extract_genres_from_browser(browser)
        if live_genres:
            logger.info(
                "[%s] Genres recovered from live DOM: %s",
                job_id,
                live_genres,
            )
            result["genres"] = live_genres

    if not result.get("imdb_link"):
        result["imdb_link"] = f"https://www.imdb.com/title/{imdb_id}/"

    # --------------------------------------------------------
    # TAGLINE — separate page navigation
    # --------------------------------------------------------
    tagline = extract_tagline(browser, imdb_id)
    result["tagline"] = tagline

    if tagline:
        logger.info("[%s] Tagline: %s", job_id, tagline)

    # --------------------------------------------------------
    # PLOT SUMMARIES — single string, paragraph-separated
    # --------------------------------------------------------
    plotsummary = extract_plotsummary(browser, imdb_id)
    result["plotsummary"] = plotsummary

    if plotsummary:
        paragraph_count = plotsummary.count("\n\n") + 1
        logger.info(
            "[%s] Plot summaries collected: %d paragraphs, %d chars",
            job_id,
            paragraph_count,
            len(plotsummary),
        )
    else:
        logger.info("[%s] No plot summaries found.", job_id)

    # --------------------------------------------------------
    # TOTAL SEASONS — from /episodes/ page (series only)
    # --------------------------------------------------------
    if result.get("type") == "series":
        seasons_from_episodes = extract_total_seasons(browser, imdb_id)
        if seasons_from_episodes:
            result["total_seasons"] = seasons_from_episodes
            logger.info(
                "[%s] Total seasons (from /episodes/): %s",
                job_id,
                seasons_from_episodes,
            )
        else:
            logger.info(
                "[%s] Keeping main-page total_seasons fallback: %s",
                job_id,
                result.get("total_seasons"),
            )

    # Save
    result_path = RESULTS_DIR / f"{job_id}.json"
    result_path.write_text(
        jsonlib.dumps(result, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )

    logger.info(
        "[%s] Completed: %s | Original: %s",
        job_id,
        result["title"],
        result["original_title"],
    )

    logger.info(
        "[%s] Full result:\n%s",
        job_id,
        jsonlib.dumps(result, indent=4, ensure_ascii=False),
    )

    return result


# ============================================================
# BACKGROUND WORKER
# ============================================================

def worker_loop():

    logger.info("Background worker started.")

    while True:

        task = job_queue.get()

        if task is None:
            job_queue.task_done()
            break

        job_id, url = task

        try:

            with jobs_lock:
                jobs[job_id]["status"] = "running"

            result = scrape_imdb(url, job_id)

            with jobs_lock:
                jobs[job_id]["status"] = "completed"
                jobs[job_id]["result"] = result

        except Exception as exc:

            logger.exception(
                "[%s] Scraping failed: %s",
                job_id,
                exc,
            )

            with jobs_lock:
                jobs[job_id]["status"] = "failed"
                jobs[job_id]["error"] = str(exc)

        finally:
            job_queue.task_done()

    logger.info("Background worker stopped.")


# ============================================================
# FASTAPI LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    worker = threading.Thread(
        target=worker_loop,
        daemon=True,
        name="imdb-worker",
    )

    worker.start()

    logger.info("IMDb API started.")
    logger.info("Chrome will remain open between jobs.")

    try:
        yield

    finally:

        job_queue.put(None)
        worker.join()

        close_driver()

        logger.info("IMDb API stopped.")


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="IMDb Title Scraper",
    description=(
        "Persistent Selenium Chrome scraper. "
        "Returns IMDb display title and original title."
    ),
    version="3.0.0",
    lifespan=lifespan,
)


@app.get("/")
def home():
    return {
        "name": "IMDb Title Scraper",
        "version": "3.0.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "queue_size": job_queue.qsize(),
        "total_jobs": len(jobs),
        "persistent_browser": driver is not None,
    }


@app.post("/scrape")
def submit_scrape(request: ScrapeRequest):

    try:
        imdb_id = extract_imdb_id(request.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    job_id = str(uuid.uuid4())

    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "imdb_id": imdb_id,
            "url": request.url,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "result": None,
            "error": None,
        }

    job_queue.put((job_id, request.url))

    return {
        "job_id": job_id,
        "imdb_id": imdb_id,
        "status": "queued",
        "status_url": f"/jobs/{job_id}",
        "result_url": f"/jobs/{job_id}/result",
    }


@app.get("/jobs/{job_id}")
def get_job(job_id: str):

    with jobs_lock:

        job = jobs.get(job_id)

        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")

        return {
            "job_id": job["job_id"],
            "imdb_id": job["imdb_id"],
            "status": job["status"],
            "error": job["error"],
        }


@app.get("/jobs/{job_id}/result")
def get_job_result(job_id: str):

    with jobs_lock:

        job = jobs.get(job_id)

        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")

        if job["status"] in {"queued", "running"}:
            return {
                "job_id": job_id,
                "status": job["status"],
                "message": "Scraping is still in progress.",
            }

        if job["status"] == "failed":
            raise HTTPException(status_code=500, detail=job["error"])

        return {
            "job_id": job_id,
            "status": "completed",
            "data": job["result"],
        }


@app.get("/jobs")
def list_jobs():

    with jobs_lock:

        return {
            "total": len(jobs),
            "jobs": [
                {
                    "job_id": job["job_id"],
                    "imdb_id": job["imdb_id"],
                    "status": job["status"],
                }
                for job in jobs.values()
            ],
        }