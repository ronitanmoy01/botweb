"""IMDb Scraper — single-file edition.

This is the whole imdb-scraper codebase consolidated into one self-contained
module with **no internal imports**: every public function works standalone.

Public API (all return the same JSON the HTTP API does):

    get_title_details(title_id, country=None)   -> consolidated title dict
    get_title_cast(title_id, cursor=None, country=None)
    get_title_reviews(title_id, sort=..., include_spoilers=True, cursor=None, country=None)
    get_title_episodes(title_id, season=None, cursor=None, country=None)
    get_person_details(person_id, country=None) -> consolidated person dict
    search_titles(query=None, title_type=None, genre=None, start_year=None,
                  end_year=None, sort=None, cursor=None, country=None)
    autocomplete(query, country=None)
    get_chart(kind, country=None) / get_box_office / get_popular_celebrities
    fetch_graphql(query, variables, country="US") / fetch_suggestions(query)

Run the API server (same as run.py):

    python imdb.py                    # http://localhost:8000
    PORT=9000 python imdb.py

Run one call straight from the CLI (same as each module's smoke test):

    python imdb.py details tt6751668
    python imdb.py cast tt0944947
    python imdb.py reviews tt1375666
    python imdb.py episodes tt0944947 1
    python imdb.py person nm0000138
    python imdb.py search matrix
    python imdb.py autocomplete shawshank
    python imdb.py chart top-movies

Transport: plain HTTP via curl_cffi (no browser, no cookies, no auth).
GraphQL goes to api.graphql.imdb.com; autocomplete rides the cookie-free
v3.sg.media-imdb.com suggestion API. The www.imdb.com HTML pages (AWS WAF)
are never touched. See the README for the hosted-API field docs.
"""
import json
import os
import re
import sys
import threading
import time
from urllib.parse import quote

# ============================================================================
# config
# ============================================================================

PORT = int(os.environ.get("PORT", "8000"))
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number
IMDB_PROXY = os.environ.get("IMDB_PROXY") or None

# ============================================================================
# transport
# ============================================================================

GRAPHQL_URL = "https://api.graphql.imdb.com/"
SUGGESTION_URL = "https://v3.sg.media-imdb.com/suggestion/x/{query}.json"
TIMEOUT = 10          # suggestion API answers in <1s
GRAPHQL_TIMEOUT = 25  # chart queries return ~200KB
IMPERSONATE = "chrome"


class ImdbUpstreamError(Exception):
    """Transport failure or 5xx — retryable."""


class ImdbBlocked(ImdbUpstreamError):
    """WAF challenge/403/429/non-JSON — retryable."""


class ImdbBadRequest(Exception):
    """Upstream 400 / GraphQL validation error (bad params) — never retried."""


class ImdbNotFound(Exception):
    """Entity doesn't exist (null title/name for a well-formed id) — never retried."""


# One session per thread: a curl handle must not be shared across threads.
_local = threading.local()


def _session():
    sess = getattr(_local, "session", None)
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        if IMDB_PROXY:
            sess.proxies = {"http": IMDB_PROXY, "https": IMDB_PROXY}
        _local.session = sess
    return sess


def _drop_session():
    """Close the thread's session so a poisoned keep-alive connection dies."""
    sess = getattr(_local, "session", None)
    _local.session = None
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


def dump_debug(name, text):
    """Write raw response text to $IMDB_DEBUG_DIR/<name>.txt for post-mortems."""
    dbg = os.environ.get("IMDB_DEBUG_DIR", "")
    if dbg and text:
        try:
            os.makedirs(dbg, exist_ok=True)
            with open(os.path.join(dbg, name + ".txt"), "w") as f:
                f.write(text)
        except OSError:
            pass


def _graphql_headers(country):
    return {
        "content-type": "application/json",
        "x-imdb-client-name": "imdb-web-next-localized",
        "x-imdb-user-country": country,
        "x-imdb-user-language": "en-US",
        "origin": "https://www.imdb.com",
        "referer": "https://www.imdb.com/",
    }


def _classify(resp, host):
    """Map an HTTP response to the error taxonomy; return the JSON payload."""
    if resp.status_code == 400:
        raise ImdbBadRequest(resp.text[:200])
    if resp.status_code == 202 and resp.headers.get("x-amzn-waf-action"):
        dump_debug(f"waf_{host}", resp.text)
        raise ImdbBlocked(f"AWS WAF challenge from {host}")
    if resp.status_code in (403, 429, 503):
        dump_debug(f"blocked_{host}", resp.text)
        raise ImdbBlocked(f"HTTP {resp.status_code} from {host}")
    if resp.status_code != 200:
        raise ImdbUpstreamError(f"HTTP {resp.status_code} from {host}")
    try:
        return resp.json()
    except Exception:
        dump_debug(f"nonjson_{host}", resp.text)
        raise ImdbBlocked(f"non-JSON response from {host}")


def _graphql_once(query, variables, country):
    try:
        resp = _session().post(GRAPHQL_URL,
                               json={"query": query, "variables": variables},
                               headers=_graphql_headers(country),
                               timeout=GRAPHQL_TIMEOUT)
    except Exception as e:
        raise ImdbUpstreamError(f"request failed: {type(e).__name__}: {e}")

    payload = _classify(resp, "api.graphql.imdb.com")
    errors = payload.get("errors")
    if errors:
        msg = json.dumps(errors)[:300]
        raise ImdbBadRequest(f"graphql errors: {msg}")
    if not isinstance(payload.get("data"), dict):
        dump_debug("unexpected_graphql", resp.text)
        raise ImdbBlocked("unexpected GraphQL payload shape (no data)")
    return payload["data"]


def fetch_graphql(query, variables, *, country="US"):
    """POST one GraphQL query; return the `data` dict.

    Retries only ImdbUpstreamError (transport/5xx/block); ImdbBadRequest
    surfaces immediately — retrying bad params can never succeed."""
    return _retrying(lambda: _graphql_once(query, variables, country))


def _suggestions_once(query):
    url = SUGGESTION_URL.format(query=quote(query, safe=""))
    try:
        resp = _session().get(url, headers={"Accept": "application/json"},
                              timeout=TIMEOUT)
    except Exception as e:
        raise ImdbUpstreamError(f"request failed: {type(e).__name__}: {e}")

    payload = _classify(resp, "v3.sg.media-imdb.com")
    if not isinstance(payload.get("d"), list):
        if isinstance(payload, dict) and "q" in payload:
            return {"d": [], "q": payload.get("q")}
        dump_debug("unexpected_suggestion", resp.text)
        raise ImdbBlocked("unexpected suggestion payload shape")
    return payload


def fetch_suggestions(query):
    """GET the typeahead suggestion payload for `query` (not GraphQL)."""
    return _retrying(lambda: _suggestions_once(query))


def _retrying(fn):
    """Run fn() under the shared retry policy (ImdbUpstreamError only)."""
    last = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except ImdbUpstreamError as e:
            last = e
            _drop_session()
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
    raise last


# ============================================================================
# id normalization + enum maps
# ============================================================================

_TITLE_ID_RE = re.compile(r"^tt\d{4,10}$")
_TITLE_URL_RE = re.compile(r"imdb\.com/[^ ]*?/?title/(tt\d{4,10})", re.I)
_PERSON_ID_RE = re.compile(r"^nm\d{4,10}$")
_PERSON_URL_RE = re.compile(r"imdb\.com/[^ ]*?/?name/(nm\d{4,10})", re.I)


def normalize_title_id(value):
    """Accept a bare tt-id or any imdb.com /title/ URL; return the bare id."""
    value = (value or "").strip()
    if _TITLE_ID_RE.match(value):
        return value
    m = _TITLE_URL_RE.search(value)
    if m:
        return m.group(1)
    raise ValueError(
        f"title_id must be an IMDB id like tt0111161 or an imdb.com title URL, got: {value!r}")


def normalize_person_id(value):
    """Accept a bare nm-id or any imdb.com /name/ URL; return the bare id."""
    value = (value or "").strip()
    if _PERSON_ID_RE.match(value):
        return value
    m = _PERSON_URL_RE.search(value)
    if m:
        return m.group(1)
    raise ValueError(
        f"person_id must be an IMDB id like nm0000138 or an imdb.com name URL, got: {value!r}")


_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")


def normalize_country(value):
    """Optional 2-letter country -> uppercased (default US); ValueError otherwise."""
    if value in (None, ""):
        return "US"
    value = str(value).strip()
    if not _COUNTRY_RE.match(value):
        raise ValueError(f"country must be a 2-letter code like US or IN, got: {value!r}")
    return value.upper()


# Our `type` param -> TitleTypeSearchConstraint.anyTitleTypeIds
TITLE_TYPE_IDS = {
    "movie": ["movie", "tvMovie"],
    "tv": ["tvSeries", "tvMiniSeries"],
}

# Our `sort` param -> AdvancedTitleSearchSort.
SEARCH_SORTS = {
    "popularity": {"sortBy": "POPULARITY", "sortOrder": "ASC"},
    "rating": {"sortBy": "USER_RATING", "sortOrder": "DESC"},
    "vote_count": {"sortBy": "USER_RATING_COUNT", "sortOrder": "DESC"},
    "release_date": {"sortBy": "RELEASE_DATE", "sortOrder": "DESC"},
    "alphabetical": {"sortBy": "TITLE_REGIONAL", "sortOrder": "ASC"},
}

REVIEW_SORTS = {
    "helpfulness": {"by": "HELPFULNESS_SCORE", "order": "DESC"},
    "newest": {"by": "SUBMISSION_DATE", "order": "DESC"},
    "oldest": {"by": "SUBMISSION_DATE", "order": "ASC"},
    "rating": {"by": "USER_RATING", "order": "DESC"},
    "votes": {"by": "TOTAL_VOTES", "order": "DESC"},
}

TITLE_CHARTS = {
    "top-movies": "TOP_RATED_MOVIES",
    "top-tv": "TOP_RATED_TV_SHOWS",
    "popular-movies": "MOST_POPULAR_MOVIES",
    "popular-tv": "MOST_POPULAR_TV_SHOWS",
}

GENRES = [
    "Action", "Adventure", "Animation", "Biography", "Comedy", "Crime",
    "Documentary", "Drama", "Family", "Fantasy", "Film-Noir", "Game-Show",
    "History", "Horror", "Music", "Musical", "Mystery", "News", "Reality-TV",
    "Romance", "Sci-Fi", "Short", "Sport", "Talk-Show", "Thriller", "War",
    "Western",
]

# ============================================================================
# GraphQL queries
# ============================================================================

TITLE_CARD = """fragment TitleCard on Title {
  id
  titleText { text }
  originalTitleText { text }
  titleType { id text canHaveEpisodes }
  releaseYear { year endYear }
  primaryImage { url width height }
  ratingsSummary { aggregateRating voteCount }
  runtime { seconds }
  plot { plotText { plainText } }
  titleGenres { genres(limit: 3) { genre { text } } }
}"""

TITLE_DETAILS = TITLE_CARD + """
query TitleDetails($id: ID!) {
  title(id: $id) {
    id
    canonicalUrl
    titleText { text }
    originalTitleText { text }
    titleType { id text canHaveEpisodes isSeries isEpisode }
    releaseYear { year endYear }
    releaseDate { day month year }
    runtime { seconds }
    certificate { rating }
    ratingsSummary { aggregateRating voteCount topRanking { rank } }
    metacritic { metascore { score reviewCount } }
    plot { plotText { plainText } }
    plots(first: 10) { edges { node { plotText { plainText } } } }
    taglines(first: 3) { edges { node { text } } }
    prestigiousAwardSummary { wins nominations award { text } }
    keywords(first: 20) { edges { node { text } } }
    titleGenres { genres { genre { text } } }
    primaryImage { url width height caption { plainText } }
    latestTrailer { id name { value } runtime { value unit } thumbnail { url } }
    principalCredits { category { id text } credits(limit: 10) { name { id nameText { text } } } }
    credits(first: 20, filter: { categories: ["cast"] }) {
      total
      edges { node { name { id nameText { text } primaryImage { url } } ... on Cast { characters(limit: 3) { name } } } }
    }
    productionBudget { budget { amount currency } }
    lifetimeGross(boxOfficeArea: WORLDWIDE) { total { amount currency } }
    openingWeekendGross(boxOfficeArea: DOMESTIC) { gross { total { amount currency } } weekendEndDate theaterCount }
    moreLikeThisTitles(first: 12) { edges { node { ...TitleCard } } }
    episodes { isOngoing displayableSeasons(first: 100) { total edges { node { season } } } episodes(first: 1) { total } }
    countriesOfOrigin { countries(limit: 10) { id text } }
    spokenLanguages { spokenLanguages(limit: 10) { id text } }
  }
}"""

TITLE_CAST = """
query TitleCast($id: ID!, $first: Int!, $after: ID) {
  title(id: $id) {
    id
    titleText { text }
    credits(first: $first, after: $after, filter: { categories: ["cast"] }) {
      total
      pageInfo { endCursor hasNextPage }
      edges { node {
        category { id text }
        name { id nameText { text } primaryImage { url width height } }
        ... on Cast { characters(limit: 5) { name } attributes(limit: 3) { text } episodeCredits(first: 1) { total yearRange { year endYear } } }
      } }
    }
  }
}"""

TITLE_REVIEWS = """
query TitleReviews($id: ID!, $first: Int!, $after: ID, $sort: ReviewsSort, $filter: ReviewsFilter) {
  title(id: $id) {
    id
    titleText { text }
    reviews(first: $first, after: $after, sort: $sort, filter: $filter) {
      total
      pageInfo { endCursor hasNextPage }
      edges { node {
        id
        summary { originalText }
        text { originalText { plainText } }
        authorRating
        author { nickName userId }
        submissionDate
        spoiler
        helpfulness { upVotes downVotes }
      } }
    }
  }
}"""

TITLE_SEASONS = """
query TitleSeasons($id: ID!) {
  title(id: $id) {
    id
    titleText { text }
    titleType { id text canHaveEpisodes }
    episodes {
      isOngoing
      displayableSeasons(first: 100) { total edges { node { season text } } }
      episodes(first: 1) { total }
    }
  }
}"""

TITLE_EPISODES = """
query TitleEpisodes($id: ID!, $first: Int!, $after: ID, $filter: EpisodesFilter) {
  title(id: $id) {
    id
    titleText { text }
    episodes {
      isOngoing
      episodes(first: $first, after: $after, filter: $filter) {
        total
        pageInfo { endCursor hasNextPage }
        edges { node {
          id
          titleText { text }
          releaseDate { day month year }
          primaryImage { url }
          ratingsSummary { aggregateRating voteCount }
          plot { plotText { plainText } }
          series { displayableEpisodeNumber { displayableSeason { season } episodeNumber { text } } }
        } }
      }
    }
  }
}"""

PERSON_DETAILS = TITLE_CARD + """
query PersonDetails($id: ID!) {
  name(id: $id) {
    id
    canonicalUrl
    nameText { text }
    primaryImage { url width height }
    bio { text { plainText } }
    birthDate { dateComponents { day month year } }
    birthLocation { text }
    deathDate { dateComponents { day month year } }
    deathLocation { text }
    deathStatus
    height { measurement { value unit } }
    professions(limit: 5) { profession { text } }
    meterRank { currentRank rankChange { changeDirection difference } }
    knownFor(first: 8) { edges { node { title { ...TitleCard } credit { category { text } } } } }
    credits(first: 100, filter: { categories: ["actor", "actress", "director", "writer", "producer"] }) {
      total
      pageInfo { endCursor hasNextPage }
      edges { node { category { id text } title { id titleText { text } titleType { id text } releaseYear { year } } } }
    }
  }
}"""

SEARCH_TITLES = TITLE_CARD + """
query SearchTitles($first: Int!, $after: String, $constraints: AdvancedTitleSearchConstraints!, $sort: AdvancedTitleSearchSort) {
  advancedTitleSearch(first: $first, after: $after, constraints: $constraints, sort: $sort) {
    total
    pageInfo { endCursor hasNextPage }
    edges { node { title { ...TitleCard } } }
  }
}"""

CHART_TITLES = TITLE_CARD + """
query ChartTitles($first: Int!, $after: String, $chartType: ChartTitleType!) {
  chartTitles(first: $first, after: $after, chart: { chartType: $chartType }) {
    total
    pageInfo { endCursor hasNextPage }
    edges { currentRank rankChange { changeDirection difference } node { ...TitleCard } }
  }
}"""

BOX_OFFICE = TITLE_CARD + """
query BoxOfficeChart {
  boxOfficeWeekendChart {
    weekendStartDate
    weekendEndDate
    entries {
      weekendGross { total { amount currency } }
      title { ...TitleCard lifetimeGross(boxOfficeArea: WORLDWIDE) { total { amount currency } } }
    }
  }
}"""

POPULAR_CELEBRITIES = """
query PopularCelebrities($first: Int!, $after: String) {
  topMeterNames(first: $first, after: $after) {
    pageInfo { endCursor hasNextPage }
    edges { node {
      id
      nameText { text }
      primaryImage { url width height }
      professions(limit: 3) { profession { text } }
      meterRank { currentRank rankChange { changeDirection difference } }
      knownFor(first: 1) { edges { node { title { id titleText { text } releaseYear { year } } } } }
    } }
  }
}"""

# ============================================================================
# parsers
# ============================================================================

IMDB = "https://www.imdb.com"


def _dig(obj, *path, default=None):
    for key in path:
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list) and isinstance(key, int) and -len(obj) <= key < len(obj):
            obj = obj[key]
        else:
            return default
        if obj is None:
            return default
    return obj


def _title_link(title_id):
    return f"{IMDB}/title/{title_id}/" if title_id else None


def _person_link(person_id):
    return f"{IMDB}/name/{person_id}/" if person_id else None


def _image(node):
    """Image node -> {link, width, height} (or None)."""
    if not isinstance(node, dict) or not node.get("url"):
        return None
    return {"link": node["url"],
            "width": node.get("width"),
            "height": node.get("height")}


def _money(node):
    """Money node {amount, currency} -> same shape (or None)."""
    amount = _dig(node, "amount")
    if amount is None:
        return None
    return {"amount": amount, "currency": _dig(node, "currency")}


def _iso_date(node):
    """{day, month, year} components -> "YYYY-MM-DD" (partial -> "YYYY-MM"/"YYYY")."""
    if not isinstance(node, dict):
        return None
    year, month, day = node.get("year"), node.get("month"), node.get("day")
    if not year:
        return None
    if not month:
        return f"{year:04d}"
    if not day:
        return f"{year:04d}-{month:02d}"
    return f"{year:04d}-{month:02d}-{day:02d}"


def _minutes(seconds):
    return round(seconds / 60) if seconds else None


def _rating(node):
    """ratingsSummary -> {value, vote_count} (or None when unrated)."""
    if not isinstance(node, dict):
        return None
    if node.get("aggregateRating") is None and not node.get("voteCount"):
        return None
    return {"value": node.get("aggregateRating"),
            "vote_count": node.get("voteCount")}


def _rank_change(node):
    """RankChange/MeterRankChange -> {direction, amount} (or None)."""
    if not isinstance(node, dict):
        return None
    direction = (node.get("changeDirection") or "").lower() or None
    return {"direction": direction, "amount": node.get("difference")}


def _genres(node):
    """titleGenres -> ["Action", ...]."""
    return [g for g in
            (_dig(e, "genre", "text") for e in _dig(node, "genres", default=[]))
            if g]


def _plots(node):
    """plots connection -> {short, summary, all} — short is the single-line
    synopsis, summary is every synopsis joined (the multi-paragraph version)."""
    plots = [_dig(e, "node", "plotText", "plainText")
             for e in _dig(node, "edges", default=[])
             if _dig(e, "node", "plotText", "plainText")]
    if not plots:
        return {"short": None, "summary": None, "all": []}
    return {"short": plots[0], "summary": "\n\n".join(plots), "all": plots}


def _tagline(node):
    """taglines connection -> first tagline text (or None)."""
    return _dig(node, "edges", 0, "node", "text")


def _keywords(node):
    """keywords connection -> ["class differences", ...]."""
    return [k for k in
            (_dig(e, "node", "text") for e in _dig(node, "edges", default=[]))
            if k]


def _title_card(node):
    """TitleCard fragment payload -> compact title dict (search/charts/similar)."""
    if not isinstance(node, dict):
        return None
    title_id = node.get("id")
    original = _dig(node, "originalTitleText", "text")
    title = _dig(node, "titleText", "text")
    return {
        "id": title_id,
        "title": title,
        "original_title": original if original != title else None,
        "link": _title_link(title_id),
        "type": _dig(node, "titleType", "id"),
        "is_series": _dig(node, "titleType", "canHaveEpisodes", default=False),
        "year": _dig(node, "releaseYear", "year"),
        "end_year": _dig(node, "releaseYear", "endYear"),
        "runtime_minutes": _minutes(_dig(node, "runtime", "seconds")),
        "plot": _dig(node, "plot", "plotText", "plainText"),
        "genres": _genres(node.get("titleGenres")),
        "rating": _rating(node.get("ratingsSummary")),
        "image": _image(node.get("primaryImage")),
    }


def _missing_title(node):
    """True when the payload is a nonexistent-id stub: IMDB's GraphQL echoes
    back {id: ..., titleText: null, ...} instead of a null title."""
    return not isinstance(node, dict) or _dig(node, "titleText", "text") is None


def _missing_person(node):
    """Same stub detection for Name payloads (nameText null)."""
    return not isinstance(node, dict) or _dig(node, "nameText", "text") is None


def _pagination(connection):
    """Connection payload -> {total, next_cursor, has_more}."""
    page = _dig(connection, "pageInfo", default={}) or {}
    has_more = bool(page.get("hasNextPage"))
    return {
        "total": _dig(connection, "total"),
        "next_cursor": page.get("endCursor") if has_more else None,
        "has_more": has_more,
    }


def parse_suggestions(payload):
    """v3.sg suggestion payload -> mixed title/person result list."""
    results = []
    for item in payload.get("d") or []:
        item_id = item.get("id") or ""
        image = None
        raw_image = item.get("i")
        if isinstance(raw_image, dict) and raw_image.get("imageUrl"):
            image = {"link": raw_image["imageUrl"],
                     "width": raw_image.get("width"),
                     "height": raw_image.get("height")}
        if item_id.startswith("nm"):
            results.append({
                "id": item_id,
                "type": "person",
                "name": item.get("l"),
                "link": _person_link(item_id),
                "known_for": item.get("s") or None,
                "rank": item.get("rank"),
                "image": image,
            })
        elif item_id.startswith("tt"):
            results.append({
                "id": item_id,
                "type": item.get("qid") or "title",
                "title": item.get("l"),
                "link": _title_link(item_id),
                "year": item.get("y"),
                "year_range": item.get("yr"),
                "stars": item.get("s") or None,
                "rank": item.get("rank"),
                "image": image,
            })
        # anything else (editorial lists etc.) is dropped
    return results


def parse_search(data):
    """advancedTitleSearch data -> (results, pagination)."""
    conn = _dig(data, "advancedTitleSearch", default={}) or {}
    results = [_title_card(_dig(e, "node", "title"))
               for e in conn.get("edges") or []]
    return [r for r in results if r], _pagination(conn)


def _principal_credits(nodes):
    """principalCredits -> {directors, creators, writers, stars} name lists."""
    out = {"directors": [], "creators": [], "writers": [], "stars": []}
    keymap = {"director": "directors", "creator": "creators",
              "writer": "writers", "cast": "stars"}
    for group in nodes or []:
        key = keymap.get(_dig(group, "category", "id"))
        if not key:
            continue
        for credit in group.get("credits") or []:
            name_id = _dig(credit, "name", "id")
            out[key].append({
                "id": name_id,
                "name": _dig(credit, "name", "nameText", "text"),
                "link": _person_link(name_id),
            })
    return out


def _cast_entry(node):
    name_id = _dig(node, "name", "id")
    entry = {
        "id": name_id,
        "name": _dig(node, "name", "nameText", "text"),
        "link": _person_link(name_id),
        "characters": [c["name"] for c in node.get("characters") or []
                       if isinstance(c, dict) and c.get("name")],
        "image": _image(_dig(node, "name", "primaryImage")),
    }
    return entry


def _seasons_summary(node):
    """Title.episodes -> {count, seasons, total_episodes, is_ongoing} (None for movies)."""
    if not isinstance(node, dict):
        return None
    seasons = [_dig(e, "node", "season")
               for e in _dig(node, "displayableSeasons", "edges", default=[])]
    seasons = [s for s in seasons if s is not None]
    return {
        "count": _dig(node, "displayableSeasons", "total"),
        "seasons": seasons,
        "total_episodes": _dig(node, "episodes", "total"),
        "is_ongoing": node.get("isOngoing"),
    }


def parse_title_details(data):
    """TitleDetails data -> consolidated details dict (None when title missing)."""
    node = data.get("title")
    if _missing_title(node):
        return None
    title_id = node.get("id")
    title = _dig(node, "titleText", "text")
    original = _dig(node, "originalTitleText", "text")
    principals = _principal_credits(node.get("principalCredits"))
    trailer_id = _dig(node, "latestTrailer", "id")

    budget = _money(_dig(node, "productionBudget", "budget"))
    opening = _money(_dig(node, "openingWeekendGross", "gross", "total"))
    if opening:
        opening["weekend_end_date"] = _dig(node, "openingWeekendGross", "weekendEndDate")
        opening["theater_count"] = _dig(node, "openingWeekendGross", "theaterCount")
    gross = _money(_dig(node, "lifetimeGross", "total"))
    box_office = None
    if budget or opening or gross:
        box_office = {"budget": budget, "opening_weekend": opening,
                      "gross_worldwide": gross}

    metascore = None
    if _dig(node, "metacritic", "metascore", "score") is not None:
        metascore = {"score": _dig(node, "metacritic", "metascore", "score"),
                     "review_count": _dig(node, "metacritic", "metascore", "reviewCount")}

    rating = _rating(node.get("ratingsSummary"))
    if rating:
        rating["top_rank"] = _dig(node, "ratingsSummary", "topRanking", "rank")

    trailer = None
    if trailer_id:
        trailer = {
            "id": trailer_id,
            "title": _dig(node, "latestTrailer", "name", "value"),
            "link": f"{IMDB}/video/{trailer_id}/",
            "duration_seconds": _dig(node, "latestTrailer", "runtime", "value"),
            "thumbnail_link": _dig(node, "latestTrailer", "thumbnail", "url"),
        }

    is_series = _dig(node, "titleType", "canHaveEpisodes", default=False)
    plot_info = _plots(node.get("plots"))
    awards = None
    if _dig(node, "prestigiousAwardSummary", "wins") is not None:
        awards = {"wins": _dig(node, "prestigiousAwardSummary", "wins"),
                  "nominations": _dig(node, "prestigiousAwardSummary", "nominations"),
                  "name": _dig(node, "prestigiousAwardSummary", "award", "text")}
    return {
        "id": title_id,
        "title": title,
        "original_title": original if original != title else None,
        "link": node.get("canonicalUrl") or _title_link(title_id),
        "type": _dig(node, "titleType", "id"),
        "is_series": is_series,
        "is_episode": _dig(node, "titleType", "isEpisode", default=False),
        "year": _dig(node, "releaseYear", "year"),
        "end_year": _dig(node, "releaseYear", "endYear"),
        "release_date": _iso_date(node.get("releaseDate")),
        "runtime_minutes": _minutes(_dig(node, "runtime", "seconds")),
        "certificate": _dig(node, "certificate", "rating"),
        "plot": _dig(node, "plot", "plotText", "plainText"),
        "plotsummary": plot_info["summary"],
        "tagline": _tagline(node.get("taglines")),
        "awards": awards,
        "keywords": _keywords(node.get("keywords")),
        "genres": _genres(node.get("titleGenres")),
        "rating": rating,
        "metascore": metascore,
        "directors": principals["directors"],
        "creators": principals["creators"],
        "writers": principals["writers"],
        "stars": principals["stars"],
        "top_cast": [_cast_entry(_dig(e, "node", default={}))
                     for e in _dig(node, "credits", "edges", default=[])],
        "total_cast": _dig(node, "credits", "total"),
        "trailer": trailer,
        "image": _image(node.get("primaryImage")),
        "box_office": box_office,
        "seasons": _seasons_summary(node.get("episodes")) if is_series else None,
        "similar_titles": [c for c in
                           (_title_card(_dig(e, "node"))
                            for e in _dig(node, "moreLikeThisTitles", "edges", default=[]))
                           if c],
        "countries_of_origin": [c["text"] for c in
                                _dig(node, "countriesOfOrigin", "countries", default=[])
                                if isinstance(c, dict) and c.get("text")],
        "spoken_languages": [l["text"] for l in
                             _dig(node, "spokenLanguages", "spokenLanguages", default=[])
                             if isinstance(l, dict) and l.get("text")],
    }


def parse_cast(data):
    """TitleCast data -> (title header, cast list, pagination); None when missing."""
    node = data.get("title")
    if _missing_title(node):
        return None
    conn = node.get("credits") or {}
    cast = []
    for edge in conn.get("edges") or []:
        member = edge.get("node") or {}
        entry = _cast_entry(member)
        entry["attributes"] = [a["text"] for a in member.get("attributes") or []
                               if isinstance(a, dict) and a.get("text")]
        episodes = member.get("episodeCredits")
        entry["episodes"] = None
        if isinstance(episodes, dict) and episodes.get("total"):
            entry["episodes"] = {
                "count": episodes.get("total"),
                "start_year": _dig(episodes, "yearRange", "year"),
                "end_year": _dig(episodes, "yearRange", "endYear"),
            }
        cast.append(entry)
    header = {"id": node.get("id"),
              "title": _dig(node, "titleText", "text"),
              "link": _title_link(node.get("id"))}
    return header, cast, _pagination(conn)


def parse_reviews(data):
    """TitleReviews data -> (title header, review list, pagination); None when missing."""
    node = data.get("title")
    if _missing_title(node):
        return None
    conn = node.get("reviews") or {}
    reviews = []
    for edge in conn.get("edges") or []:
        review = edge.get("node") or {}
        reviews.append({
            "id": review.get("id"),
            "title": _dig(review, "summary", "originalText"),
            "text": _dig(review, "text", "originalText", "plainText"),
            "rating": review.get("authorRating"),
            "author": {"id": _dig(review, "author", "userId"),
                       "nickname": _dig(review, "author", "nickName")},
            "date": review.get("submissionDate"),
            "is_spoiler": review.get("spoiler", False),
            "helpful_votes": {"up": _dig(review, "helpfulness", "upVotes"),
                              "down": _dig(review, "helpfulness", "downVotes")},
        })
    header = {"id": node.get("id"),
              "title": _dig(node, "titleText", "text"),
              "link": _title_link(node.get("id"))}
    return header, reviews, _pagination(conn)


def parse_seasons(data):
    """TitleSeasons data -> seasons summary dict; None when title missing."""
    node = data.get("title")
    if _missing_title(node):
        return None
    return {
        "id": node.get("id"),
        "title": _dig(node, "titleText", "text"),
        "link": _title_link(node.get("id")),
        "is_series": _dig(node, "titleType", "canHaveEpisodes", default=False),
        "seasons": _seasons_summary(node.get("episodes")),
    }


def _episode_number(value):
    """Episode number text ("7") -> int where possible, else the raw text."""
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return value


def parse_episodes(data):
    """TitleEpisodes data -> (title header, episode list, pagination); None when missing."""
    node = data.get("title")
    if _missing_title(node):
        return None
    conn = _dig(node, "episodes", "episodes", default={}) or {}
    episodes = []
    for edge in conn.get("edges") or []:
        episode = edge.get("node") or {}
        number = _dig(episode, "series", "displayableEpisodeNumber")
        episodes.append({
            "id": episode.get("id"),
            "title": _dig(episode, "titleText", "text"),
            "link": _title_link(episode.get("id")),
            "season": _episode_number(_dig(number, "displayableSeason", "season")),
            "episode": _episode_number(_dig(number, "episodeNumber", "text")),
            "release_date": _iso_date(episode.get("releaseDate")),
            "plot": _dig(episode, "plot", "plotText", "plainText"),
            "rating": _rating(episode.get("ratingsSummary")),
            "image": _image(episode.get("primaryImage")),
        })
    header = {"id": node.get("id"),
              "title": _dig(node, "titleText", "text"),
              "link": _title_link(node.get("id")),
              "is_ongoing": _dig(node, "episodes", "isOngoing")}
    return header, episodes, _pagination(conn)


def parse_person(data):
    """PersonDetails data -> consolidated person dict (None when missing)."""
    node = data.get("name")
    if _missing_person(node):
        return None
    person_id = node.get("id")

    birth = None
    birth_date = _iso_date(_dig(node, "birthDate", "dateComponents"))
    birth_place = _dig(node, "birthLocation", "text")
    if birth_date or birth_place:
        birth = {"date": birth_date, "place": birth_place}

    death = None
    death_date = _iso_date(_dig(node, "deathDate", "dateComponents"))
    death_place = _dig(node, "deathLocation", "text")
    if death_date or death_place:
        death = {"date": death_date, "place": death_place}

    height_cm = None
    if _dig(node, "height", "measurement", "unit") == "CENTIMETER":
        height_cm = _dig(node, "height", "measurement", "value")

    star_meter = None
    if _dig(node, "meterRank", "currentRank") is not None:
        star_meter = {"rank": _dig(node, "meterRank", "currentRank"),
                      "change": _rank_change(_dig(node, "meterRank", "rankChange"))}

    known_for = []
    for edge in _dig(node, "knownFor", "edges", default=[]):
        card = _title_card(_dig(edge, "node", "title"))
        if card:
            card["role"] = _dig(edge, "node", "credit", "category", "text")
            known_for.append(card)

    filmography_conn = node.get("credits") or {}
    filmography = []
    for edge in filmography_conn.get("edges") or []:
        credit = edge.get("node") or {}
        title_id = _dig(credit, "title", "id")
        filmography.append({
            "id": title_id,
            "title": _dig(credit, "title", "titleText", "text"),
            "link": _title_link(title_id),
            "type": _dig(credit, "title", "titleType", "id"),
            "year": _dig(credit, "title", "releaseYear", "year"),
            "category": _dig(credit, "category", "text"),
        })

    return {
        "id": person_id,
        "name": _dig(node, "nameText", "text"),
        "link": node.get("canonicalUrl") or _person_link(person_id),
        "image": _image(node.get("primaryImage")),
        "bio": _dig(node, "bio", "text", "plainText"),
        "birth": birth,
        "death": death,
        "is_dead": (node.get("deathStatus") or "ALIVE") != "ALIVE",
        "height_cm": height_cm,
        "professions": [p for p in
                        (_dig(item, "profession", "text")
                         for item in node.get("professions") or [])
                        if p],
        "star_meter": star_meter,
        "known_for": known_for,
        "filmography": {
            "pagination": _pagination(filmography_conn),
            "credits": filmography,
        },
    }


def parse_chart_titles(data):
    """ChartTitles data -> (ranked title list, pagination)."""
    conn = data.get("chartTitles") or {}
    results = []
    for edge in conn.get("edges") or []:
        card = _title_card(edge.get("node"))
        if not card:
            continue
        entry = {"rank": edge.get("currentRank"),
                 "rank_change": _rank_change(edge.get("rankChange"))}
        entry.update(card)
        results.append(entry)
    return results, _pagination(conn)


def parse_box_office(data):
    """BoxOfficeChart data -> (weekend dict, ranked entries)."""
    chart = data.get("boxOfficeWeekendChart") or {}
    weekend = {"start_date": chart.get("weekendStartDate"),
               "end_date": chart.get("weekendEndDate")}
    entries = []
    for rank, entry in enumerate(chart.get("entries") or [], start=1):
        card = _title_card(entry.get("title")) or {}
        row = {"rank": rank}
        row.update(card)
        row["weekend_gross"] = _money(_dig(entry, "weekendGross", "total"))
        row["gross_worldwide"] = _money(_dig(entry, "title", "lifetimeGross", "total"))
        entries.append(row)
    return weekend, entries


def parse_chart_names(data):
    """PopularCelebrities data -> (ranked people list, pagination)."""
    conn = data.get("topMeterNames") or {}
    results = []
    for edge in conn.get("edges") or []:
        node = edge.get("node") or {}
        person_id = node.get("id")
        known_for_title = _dig(node, "knownFor", "edges", 0, "node", "title")
        known_for = None
        if isinstance(known_for_title, dict) and known_for_title.get("id"):
            known_for = {
                "id": known_for_title["id"],
                "title": _dig(known_for_title, "titleText", "text"),
                "link": _title_link(known_for_title["id"]),
                "year": _dig(known_for_title, "releaseYear", "year"),
            }
        results.append({
            "id": person_id,
            "name": _dig(node, "nameText", "text"),
            "link": _person_link(person_id),
            "rank": _dig(node, "meterRank", "currentRank"),
            "rank_change": _rank_change(_dig(node, "meterRank", "rankChange")),
            "professions": [p for p in
                            (_dig(item, "profession", "text")
                             for item in node.get("professions") or [])
                            if p],
            "known_for": known_for,
            "image": _image(node.get("primaryImage")),
        })
    return results, _pagination(conn)


# ============================================================================
# domain functions
# ============================================================================

CAST_PAGE_SIZE = 50
REVIEWS_PAGE_SIZE = 25
EPISODES_PAGE_SIZE = 50
MAX_SEASON = 1000

TOP_CHART_SIZE = 250      # TOP_RATED_* charts are exactly 250 titles
POPULAR_CHART_SIZE = 100  # MovieMeter/TVMeter/StarMeter published depth

CHART_KINDS = ["top-movies", "top-tv", "popular-movies", "popular-tv",
               "box-office", "popular-celebrities"]

SEARCH_PAGE_SIZE = 50
MIN_YEAR, MAX_YEAR = 1874, 2100  # IMDB's oldest title is from 1874


def autocomplete(query, country=None):
    """Title/person suggestions for a partial query.

    Raises ValueError for bad input, ImdbUpstreamError for upstream
    failures."""
    query = (query or "").strip()
    if not query:
        raise ValueError("query is required")
    normalize_country(country)  # validate even though unused

    payload = fetch_suggestions(query)
    results = parse_suggestions(payload)
    return {
        "query": query,
        "count": len(results),
        "results": results,
    }


def _year(name, value):
    if value in (None, ""):
        return None
    try:
        value = int(value)
        if not MIN_YEAR <= value <= MAX_YEAR:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer {MIN_YEAR}-{MAX_YEAR}")
    return value


def search_titles(query=None, title_type=None, genre=None, start_year=None,
                  end_year=None, sort=None, cursor=None, country=None):
    """One page of title search results (50 per page, cursor-paginated).

    Raises ValueError for bad input, ImdbUpstreamError for upstream
    failures."""
    query = (query or "").strip()
    country = normalize_country(country)
    sort = (sort or "popularity").strip().lower()
    if sort not in SEARCH_SORTS:
        raise ValueError(
            f"sort must be one of {', '.join(sorted(SEARCH_SORTS))}, got: {sort!r}")

    constraints = {}
    if query:
        constraints["titleTextConstraint"] = {"searchTerm": query}

    if title_type not in (None, ""):
        title_type = str(title_type).strip().lower()
        if title_type not in TITLE_TYPE_IDS:
            raise ValueError(
                f"type must be one of {', '.join(sorted(TITLE_TYPE_IDS))}, got: {title_type!r}")
        constraints["titleTypeConstraint"] = {
            "anyTitleTypeIds": TITLE_TYPE_IDS[title_type]}
    else:
        title_type = None

    if genre not in (None, ""):
        genre = str(genre).strip()
        matched = next((g for g in GENRES if g.lower() == genre.lower()), None)
        if not matched:
            raise ValueError(
                f"genre must be one of {', '.join(GENRES)}, got: {genre!r}")
        genre = matched
        constraints["genreConstraint"] = {"allGenreIds": [matched]}
    else:
        genre = None

    start_year = _year("start_year", start_year)
    end_year = _year("end_year", end_year)
    if start_year and end_year and start_year > end_year:
        raise ValueError("start_year must not be after end_year")
    if start_year or end_year:
        date_range = {}
        if start_year:
            date_range["start"] = f"{start_year}-01-01"
        if end_year:
            date_range["end"] = f"{end_year}-12-31"
        constraints["releaseDateConstraint"] = {"releaseDateRange": date_range}

    if not constraints:
        raise ValueError(
            "at least one of query, type, genre, start_year, end_year is required")

    variables = {"first": SEARCH_PAGE_SIZE, "constraints": constraints,
                 "sort": SEARCH_SORTS[sort]}
    if cursor:
        variables["after"] = cursor
    data = fetch_graphql(SEARCH_TITLES, variables, country=country)
    results, pagination = parse_search(data)
    return {
        "query": query or None,
        "type": title_type,
        "genre": genre,
        "start_year": start_year,
        "end_year": end_year,
        "sort": sort,
        "country": country,
        "count": len(results),
        "pagination": pagination,
        "results": results,
    }


def get_title_details(title_id, country=None):
    """Consolidated one-call details for a movie/show.

    Raises ValueError for bad input, ImdbNotFound for a nonexistent id,
    ImdbUpstreamError for upstream failures."""
    title_id = normalize_title_id(title_id)
    country = normalize_country(country)

    data = fetch_graphql(TITLE_DETAILS, {"id": title_id}, country=country)
    result = parse_title_details(data)
    if result is None:
        raise ImdbNotFound(f"title not found: {title_id}")
    result["country"] = country
    return result


def get_title_cast(title_id, cursor=None, country=None):
    """One page of full cast credits (50 per page, cursor-paginated)."""
    title_id = normalize_title_id(title_id)
    country = normalize_country(country)

    variables = {"id": title_id, "first": CAST_PAGE_SIZE}
    if cursor:
        variables["after"] = cursor
    data = fetch_graphql(TITLE_CAST, variables, country=country)
    parsed = parse_cast(data)
    if parsed is None:
        raise ImdbNotFound(f"title not found: {title_id}")
    header, cast, pagination = parsed
    return {
        **header,
        "country": country,
        "count": len(cast),
        "pagination": pagination,
        "results": cast,
    }


def get_title_reviews(title_id, sort="helpfulness", include_spoilers=True,
                      cursor=None, country=None):
    """One page of user reviews (25 per page, cursor-paginated)."""
    title_id = normalize_title_id(title_id)
    country = normalize_country(country)
    sort = (sort or "helpfulness").strip().lower()
    if sort not in REVIEW_SORTS:
        raise ValueError(
            f"sort must be one of {', '.join(sorted(REVIEW_SORTS))}, got: {sort!r}")

    variables = {"id": title_id, "first": REVIEWS_PAGE_SIZE,
                 "sort": REVIEW_SORTS[sort]}
    if not include_spoilers:
        variables["filter"] = {"spoiler": "EXCLUDE"}
    if cursor:
        variables["after"] = cursor
    data = fetch_graphql(TITLE_REVIEWS, variables, country=country)
    parsed = parse_reviews(data)
    if parsed is None:
        raise ImdbNotFound(f"title not found: {title_id}")
    header, reviews, pagination = parsed
    return {
        **header,
        "country": country,
        "sort": sort,
        "includes_spoilers": bool(include_spoilers),
        "count": len(reviews),
        "pagination": pagination,
        "results": reviews,
    }


def get_title_episodes(title_id, season=None, cursor=None, country=None):
    """Episodes of one season (50 per page), or the seasons list when
    `season` is omitted. Unaired specials ride along as episode 0."""
    title_id = normalize_title_id(title_id)
    country = normalize_country(country)

    if season in (None, ""):
        data = fetch_graphql(TITLE_SEASONS, {"id": title_id}, country=country)
        result = parse_seasons(data)
        if result is None:
            raise ImdbNotFound(f"title not found: {title_id}")
        result["country"] = country
        return result

    try:
        season = int(season)
        if not 1 <= season <= MAX_SEASON:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"season must be an integer 1-{MAX_SEASON}")

    variables = {"id": title_id, "first": EPISODES_PAGE_SIZE,
                 "filter": {"includeSeasons": [str(season)]}}
    if cursor:
        variables["after"] = cursor
    data = fetch_graphql(TITLE_EPISODES, variables, country=country)
    parsed = parse_episodes(data)
    if parsed is None:
        raise ImdbNotFound(f"title not found: {title_id}")
    header, episodes, pagination = parsed
    return {
        **header,
        "country": country,
        "season": season,
        "count": len(episodes),
        "pagination": pagination,
        "results": episodes,
    }


def get_person_details(person_id, country=None):
    """Consolidated details for one person.

    Raises ValueError for bad input, ImdbNotFound for a nonexistent id,
    ImdbUpstreamError for upstream failures."""
    person_id = normalize_person_id(person_id)
    country = normalize_country(country)

    data = fetch_graphql(PERSON_DETAILS, {"id": person_id}, country=country)
    result = parse_person(data)
    if result is None:
        raise ImdbNotFound(f"person not found: {person_id}")
    result["country"] = country
    return result


def get_title_chart(kind, country=None):
    """One ranked title chart: top-movies, top-tv, popular-movies, popular-tv."""
    kind = (kind or "").strip().lower()
    if kind not in TITLE_CHARTS:
        raise ValueError(
            f"chart must be one of {', '.join(sorted(TITLE_CHARTS))}, got: {kind!r}")
    country = normalize_country(country)

    first = TOP_CHART_SIZE if kind.startswith("top-") else POPULAR_CHART_SIZE
    data = fetch_graphql(
        CHART_TITLES,
        {"first": first, "chartType": TITLE_CHARTS[kind]},
        country=country)
    results, pagination = parse_chart_titles(data)
    return {
        "chart": kind,
        "country": country,
        "count": len(results),
        "pagination": pagination,
        "results": results,
    }


def get_box_office(country=None):
    """The weekend box office chart (US theatrical)."""
    country = normalize_country(country)
    data = fetch_graphql(BOX_OFFICE, {}, country=country)
    weekend, entries = parse_box_office(data)
    return {
        "chart": "box-office",
        "country": country,
        "weekend": weekend,
        "count": len(entries),
        "results": entries,
    }


def get_popular_celebrities(country=None):
    """The StarMeter chart: the 100 most popular people this week."""
    country = normalize_country(country)
    data = fetch_graphql(POPULAR_CELEBRITIES,
                         {"first": POPULAR_CHART_SIZE}, country=country)
    results, pagination = parse_chart_names(data)
    return {
        "chart": "popular-celebrities",
        "country": country,
        "count": len(results),
        "pagination": pagination,
        "results": results,
    }


def get_chart(kind, country=None):
    """Dispatch any chart kind."""
    kind_clean = (kind or "").strip().lower()
    if kind_clean == "box-office":
        return get_box_office(country)
    if kind_clean == "popular-celebrities":
        return get_popular_celebrities(country)
    return get_title_chart(kind_clean, country)


# ============================================================================
# optional bottle server (lazy import: the functions above need no bottle)
# ============================================================================

ENDPOINTS = [
    ("/title/details", "title_details_route"),
    ("/search", "search_route"),
    ("/autocomplete", "autocomplete_route"),
    ("/title/cast", "title_cast_route"),
    ("/title/reviews", "title_reviews_route"),
    ("/title/episodes", "title_episodes_route"),
    ("/person/details", "person_details_route"),
] + [(f"/charts/{kind}", "chart_route") for kind in CHART_KINDS]
def _build_handlers(bottle):
    """Define + mount the bottle routes; return the handler name->fn map."""
    def q(name):
        value = bottle.request.query.getunicode(name)
        return value.strip() if value else None

    def json_response(data, status=200):
        bottle.response.status = status
        bottle.response.content_type = "application/json"
        return json.dumps(data, ensure_ascii=False)

    def call(label, fn, not_found=None):
        try:
            return json_response(fn())
        except ValueError as e:
            return json_response({"error": str(e)}, 400)
        except ImdbBadRequest as e:
            return json_response({"error": f"imdb rejected the request: {e}"}, 400)
        except ImdbNotFound:
            return json_response({"error": not_found or "not found"}, 404)
        except Exception as e:
            return json_response({"error": f"imdb {label} failed: {e}"}, 500)

    def required(name):
        value = q(name)
        if not value:
            return None, json_response({"error": f"Missing required parameter: {name}"}, 400)
        return value, None

    def mount(path, handler):
        handler.__name__ = "imdb_" + path.strip("/").replace("/", "_").replace("-", "_")
        bottle.route(path, method="GET")(handler)
        bottle.route("/imdb" + path, method="GET")(handler)

    def autocomplete_route():
        query, err = required("query")
        return err or call("autocomplete", lambda: autocomplete(query, q("country")))

    def search_route():
        return call("search", lambda: search_titles(
            query=q("query"), title_type=q("type"), genre=q("genre"),
            start_year=q("start_year"), end_year=q("end_year"), sort=q("sort"),
            cursor=q("cursor"), country=q("country")))

    def title_details_route():
        title, err = required("title_id")
        return err or call("title/details",
                           lambda: get_title_details(title, q("country")),
                           not_found=f"title not found: {title}")

    def title_cast_route():
        title, err = required("title_id")
        return err or call("title/cast",
                           lambda: get_title_cast(title, cursor=q("cursor"), country=q("country")),
                           not_found=f"title not found: {title}")

    def title_reviews_route():
        title, err = required("title_id")
        if err:
            return err
        include_spoilers = (q("include_spoilers") or "").lower() not in ("false", "0", "no")
        return call("title/reviews", lambda: get_title_reviews(
            title, sort=q("sort"), include_spoilers=include_spoilers,
            cursor=q("cursor"), country=q("country")),
            not_found=f"title not found: {title}")

    def title_episodes_route():
        title, err = required("title_id")
        return err or call("title/episodes", lambda: get_title_episodes(
            title, season=q("season"), cursor=q("cursor"), country=q("country")),
            not_found=f"title not found: {title}")

    def person_details_route():
        person, err = required("person_id")
        return err or call("person/details",
                           lambda: get_person_details(person, q("country")),
                           not_found=f"person not found: {person}")

    def chart_route(kind):
        def handler():
            return call(f"charts/{kind}", lambda: get_chart(kind, q("country")))
        return handler

    handlers = {
        "autocomplete_route": autocomplete_route,
        "search_route": search_route,
        "title_details_route": title_details_route,
        "title_cast_route": title_cast_route,
        "title_reviews_route": title_reviews_route,
        "title_episodes_route": title_episodes_route,
        "person_details_route": person_details_route,
    }

    for path, handler_name in ENDPOINTS:
        if handler_name == "chart_route":
            kind = path.rsplit("/", 1)[1]  # /charts/top-movies -> top-movies
            mount(path, chart_route(kind))
        else:
            mount(path, handlers[handler_name])

    @bottle.route("/", method="GET")
    @bottle.route("/health", method="GET")
    def health():
        return json_response({"status": "ok",
                              "endpoints": [p for p, _ in ENDPOINTS]})

    return handlers


def run_server(port=None):
    """Start the IMDb Scraper API server (lazy bottle/cheroot import)."""
    import bottle
    from cheroot import wsgi

    _build_handlers(bottle)
    port = port or PORT
    app = bottle.default_app()
    print(f"IMDb Scraper listening on http://localhost:{port}/")
    print(f"Try:  curl \"http://localhost:{port}/title/details?title_id=tt4154796\"")
    server = wsgi.Server(("0.0.0.0", port), app,
                         server_name="imdb-scraper", numthreads=16)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()


# ============================================================================
# CLI
# ============================================================================

def _main(argv):
    if not argv:
        run_server()
        return

    cmd, *rest = argv
    arg = rest[0] if rest else None

    if cmd == "details":
        print(json.dumps(get_title_details(arg or "tt0111161"),
                         indent=2, ensure_ascii=False))
    elif cmd == "cast":
        print(json.dumps(get_title_cast(arg or "tt0944947"),
                         indent=2, ensure_ascii=False))
    elif cmd == "reviews":
        print(json.dumps(get_title_reviews(arg or "tt1375666"),
                         indent=2, ensure_ascii=False))
    elif cmd == "episodes":
        season = rest[1] if len(rest) > 1 else None
        print(json.dumps(get_title_episodes(arg or "tt0944947", season=season),
                         indent=2, ensure_ascii=False))
    elif cmd == "person":
        print(json.dumps(get_person_details(arg or "nm0000138"),
                         indent=2, ensure_ascii=False))
    elif cmd == "search":
        print(json.dumps(search_titles(arg),
                         indent=2, ensure_ascii=False))
    elif cmd == "autocomplete":
        print(json.dumps(autocomplete(arg or "shawshank"),
                         indent=2, ensure_ascii=False))
    elif cmd == "chart":
        print(json.dumps(get_chart(arg or "top-movies"),
                         indent=2, ensure_ascii=False))
    else:
        raise SystemExit(
            "usage: python imdb.py [details|cast|reviews|episodes|person|search|autocomplete|chart] ...")


if __name__ == "__main__":
    _main(sys.argv[1:])
