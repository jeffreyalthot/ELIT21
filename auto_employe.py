#!/usr/bin/env python3
"""AutoEmploye IA Local

CLI pour automatiser une veille de niches rentables et l'identification
 d'emplacements publicitaires basés sur des URLs publiques.
"""

from __future__ import annotations

import argparse
import csv
import html
import itertools
import json
import logging
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

USER_AGENT = "AutoEmployeResearchBot/1.0 (+local-cli)"
DEFAULT_AD_LIBRARY_PATH = Path("data/ad_library.json")
DEFAULT_SEED_URLS = [
    "https://news.ycombinator.com/",
    "https://techcrunch.com/",
    "https://www.producthunt.com/",
    "https://www.blogdumoderateur.com/",
    "https://www.maddyness.com/",
    "https://thenextweb.com/",
    "https://venturebeat.com/",
    "https://www.wired.com/",
    "https://www.theverge.com/",
    "https://arstechnica.com/",
    "https://www.entrepreneur.com/",
    "https://www.fastcompany.com/",
    "https://www.marketingdive.com/",
    "https://www.adweek.com/",
    "https://www.searchenginejournal.com/",
    "https://www.searchenginewatch.com/",
    "https://www.socialmediatoday.com/",
    "https://www.contentmarketinginstitute.com/",
    "https://moz.com/blog",
    "https://backlinko.com/blog",
    "https://www.convinceandconvert.com/",
    "https://www.cmswire.com/",
    "https://www.zdnet.com/",
    "https://www.cnet.com/",
    "https://www.forbes.com/innovation/",
    "https://www.inc.com/",
    "https://www.shopify.com/blog",
    "https://buffer.com/resources/",
    "https://ahrefs.com/blog/",
]
LOGGER = logging.getLogger("auto_employe")

RETRYABLE_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}

FULL_AUTO_PROFILE = {
    "max_links": 80,
    "interval": 120,
    "forever": True,
    "discover_urls": True,
    "discover_limit": 40,
    "min_authorization_score": 0,
    "auto_embed": True,
    "use_local_ai": True,
    "local_ai_model": "llama3.2",
    "log_level": "DEBUG",
}

PROFITABLE_KEYWORDS = {
    "saas": 8,
    "assurance": 10,
    "finance": 10,
    "crypto": 9,
    "b2b": 8,
    "formation": 7,
    "coaching": 7,
    "immobilier": 9,
    "santé": 8,
    "ai": 9,
    "ia": 9,
    "legal": 8,
    "avocat": 8,
    "cyber": 8,
    "vpn": 7,
    "cloud": 8,
}


@dataclass
class NicheResult:
    title: str
    url: str
    snippet: str
    score: int
    matched_keywords: list[str]


@dataclass
class AdSpot:
    source_url: str
    outbound_url: str
    anchor_text: str
    ad_fit_score: int
    notes: str
    authorization_score: int
    authorization_notes: str
    insertion_points: list[str]


@dataclass
class AdCreative:
    name: str
    target_niche: str
    embed_code: str


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = None
        for key, value in attrs:
            if key.lower() == "href" and value:
                href = value.strip()
                break
        self._current_href = href
        self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current_href is None:
            return
        text = " ".join(" ".join(self._current_text).split())
        self.links.append((self._current_href, text))
        self._current_href = None
        self._current_text = []


class SlotExtractor(HTMLParser):
    """Extrait des sélecteurs candidats pour insertion publicitaire."""

    def __init__(self) -> None:
        super().__init__()
        self.points: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        allowed_tags = {"div", "aside", "section", "header", "footer", "main", "article", "nav"}
        if tag.lower() not in allowed_tags:
            return
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}
        blob = f"{attrs_dict.get('id', '')} {attrs_dict.get('class', '')}".lower()
        markers = ["ad", "sponsor", "partner", "sidebar", "banner", "widget", "promo"]
        if any(marker in blob for marker in markers):
            if attrs_dict.get("id"):
                self.points.append(f"#{attrs_dict['id']}")
            classes = [c for c in attrs_dict.get("class", "").split() if c]
            if classes:
                self.points.append("." + ".".join(classes[:2]))


def extract_seed_links(source_url: str, html_text: str, max_candidates: int = 20) -> list[str]:
    parser = LinkExtractor()
    parser.feed(html_text)
    base_domain = urllib.parse.urlparse(source_url).netloc
    candidates: list[str] = []
    seen: set[str] = set()
    for href, _ in parser.links:
        normalized = normalize_url(source_url, href)
        if not normalized:
            continue
        parsed = urllib.parse.urlparse(normalized)
        if parsed.netloc != base_domain:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
        if len(candidates) >= max_candidates:
            break
    return candidates


def discover_urls(seed_urls: Iterable[str], max_discovered_per_seed: int) -> list[str]:
    """Découvre automatiquement de nouvelles URLs pertinentes depuis les URLs sources."""
    discovered: list[str] = []
    seen: set[str] = set()
    for seed in seed_urls:
        if seed not in seen:
            discovered.append(seed)
            seen.add(seed)
        try:
            page = fetch_url(seed)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("[DISCOVER] Échec URL %s: %s", seed, exc)
            continue
        if not page:
            LOGGER.warning("[DISCOVER] Réponse vide depuis %s, aucune URL découverte.", seed)
            continue
        fresh_links = extract_seed_links(seed, page, max_candidates=max_discovered_per_seed)
        for item in fresh_links:
            if item in seen:
                continue
            discovered.append(item)
            seen.add(item)
    return discovered


def fetch_url(url: str, timeout: int = 15, retries: int = 2, retry_delay: float = 1.0) -> str:
    """Télécharge une URL avec tolérance aux erreurs (incluant HTTP 403)."""
    for attempt in range(1, retries + 2):
        LOGGER.debug("[HTTP] Navigation vers %s (tentative %s/%s)", url, attempt, retries + 1)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                payload = response.read().decode(charset, errors="replace")
            LOGGER.debug("[HTTP] Navigation terminée %s | %s caractères", url, len(payload))
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                LOGGER.warning(
                    "[HTTP] 403 Forbidden sur %s (tentative %s/%s) -> URL ignorée, traitement continue.",
                    url,
                    attempt,
                    retries + 1,
                )
                return ""
            should_retry = exc.code in RETRYABLE_HTTP_CODES and attempt <= retries
            LOGGER.warning(
                "[HTTP] Erreur HTTP %s sur %s (tentative %s/%s)",
                exc.code,
                url,
                attempt,
                retries + 1,
            )
            if not should_retry:
                raise
        except urllib.error.URLError as exc:
            should_retry = attempt <= retries
            LOGGER.warning(
                "[HTTP] Erreur réseau sur %s (tentative %s/%s): %s",
                url,
                attempt,
                retries + 1,
                exc,
            )
            if not should_retry:
                raise
        time.sleep(retry_delay * attempt)
    return ""


def duckduckgo_search(query: str, limit: int = 10) -> list[dict[str, str]]:
    encoded = urllib.parse.quote_plus(query)
    url = f"https://duckduckgo.com/html/?q={encoded}"
    page = fetch_url(url)
    if not page:
        LOGGER.warning("[SEARCH] Réponse vide pour la requête DuckDuckGo: %s", query)
        return []

    blocks = re.findall(
        r'<a rel="nofollow" class="result__a" href="(?P<url>.*?)">(?P<title>.*?)</a>.*?'
        r'<a class="result__snippet".*?>(?P<snippet>.*?)</a>|'
        r'<div class="result__snippet">(?P<snippet2>.*?)</div>',
        page,
        flags=re.DOTALL,
    )

    results: list[dict[str, str]] = []
    for match in blocks:
        raw_url, raw_title, snippet_1, snippet_2 = match
        clean_title = clean_html(raw_title)
        clean_snippet = clean_html(snippet_1 or snippet_2 or "")
        clean_url = html.unescape(raw_url)
        if clean_url.startswith("//duckduckgo.com/l/"):
            parsed = urllib.parse.urlparse(clean_url)
            params = urllib.parse.parse_qs(parsed.query)
            if "uddg" in params:
                clean_url = urllib.parse.unquote(params["uddg"][0])
        results.append({"title": clean_title, "url": clean_url, "snippet": clean_snippet})
        if len(results) >= limit:
            break
    return results


def clean_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return " ".join(value.split())


def score_text(text: str) -> tuple[int, list[str]]:
    score = 0
    matched: list[str] = []
    lowered = text.lower()
    for keyword, weight in PROFITABLE_KEYWORDS.items():
        if keyword in lowered:
            score += weight
            matched.append(keyword)
    return score, matched


def research_niches(topic: str, limit: int) -> list[NicheResult]:
    raw_results = duckduckgo_search(topic, limit=limit)
    ranked: list[NicheResult] = []
    for entry in raw_results:
        combo = f"{entry['title']} {entry['snippet']}"
        score, matched = score_text(combo)
        ranked.append(
            NicheResult(
                title=entry["title"],
                url=entry["url"],
                snippet=entry["snippet"],
                score=score,
                matched_keywords=matched,
            )
        )
    return sorted(ranked, key=lambda r: r.score, reverse=True)


def normalize_url(base_url: str, href: str) -> str | None:
    href = href.strip()
    if not href or href.startswith("#"):
        return None
    if href.startswith("mailto:") or href.startswith("javascript:"):
        return None
    absolute = urllib.parse.urljoin(base_url, href)
    parsed = urllib.parse.urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return None
    return absolute


def ad_score(anchor_text: str, target_url: str) -> tuple[int, str]:
    text = f"{anchor_text} {target_url}".lower()
    score = 0
    reasons: list[str] = []
    keywords = {
        "sponsor": 4,
        "advert": 5,
        "partner": 4,
        "media kit": 6,
        "pricing": 3,
        "newsletter": 2,
        "contact": 3,
        "submit": 2,
        "guest post": 4,
    }
    for key, pts in keywords.items():
        if key in text:
            score += pts
            reasons.append(key)
    if any(domain in target_url.lower() for domain in ("/contact", "/advertise", "/sponsor")):
        score += 5
        reasons.append("landing page pub/contact")
    return score, ", ".join(reasons) or "lien sortant général"


def analyze_authorization(target_url: str) -> tuple[int, str, list[str]]:
    """Évalue si la page semble autoriser une insertion publicitaire."""
    try:
        page = fetch_url(target_url)
    except Exception as exc:  # noqa: BLE001
        return 0, f"page inaccessible: {exc}", []

    if not page:
        return 0, "page vide ou inaccessible", []

    text = page.lower()
    score = 0
    notes: list[str] = []
    allow_markers = {
        "advertise": 5,
        "advertising": 5,
        "sponsor": 4,
        "sponsored": 4,
        "media kit": 5,
        "partner": 3,
        "guest post": 2,
        "contact": 1,
    }
    for marker, points in allow_markers.items():
        if marker in text:
            score += points
            notes.append(marker)

    parser = SlotExtractor()
    parser.feed(page)
    insertion_points = list(dict.fromkeys(parser.points))[:10]
    if insertion_points:
        score += min(4, len(insertion_points))
        notes.append(f"{len(insertion_points)} slot(s) détecté(s)")

    return score, ", ".join(notes) or "aucun signal d'autorisation clair", insertion_points


def find_ad_spots(source_urls: Iterable[str], max_links: int, min_authorization_score: int = 0) -> list[AdSpot]:
    from concurrent.futures import ThreadPoolExecutor

    def scan_source(source: str) -> list[AdSpot]:
        LOGGER.info("[SCAN] Analyse source: %s", source)
        local_spots: list[AdSpot] = []
        try:
            html_text = fetch_url(source)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("[SCAN] Échec source %s: %s", source, exc)
            return local_spots
        if not html_text:
            LOGGER.warning("[SCAN] Source ignorée (réponse vide): %s", source)
            return local_spots
        parser = LinkExtractor()
        parser.feed(html_text)
        LOGGER.debug("[SCAN] %s liens détectés sur %s", len(parser.links), source)
        count = 0
        for href, text in parser.links:
            normalized = normalize_url(source, href)
            if not normalized:
                continue
            if urllib.parse.urlparse(normalized).netloc == urllib.parse.urlparse(source).netloc:
                continue
            score, notes = ad_score(text, normalized)
            authorization_score, authorization_notes, insertion_points = analyze_authorization(normalized)
            if authorization_score < min_authorization_score:
                LOGGER.debug(
                    "[AUTH] Ignoré %s (score %s < min %s)",
                    normalized,
                    authorization_score,
                    min_authorization_score,
                )
                continue
            local_spots.append(
                AdSpot(
                    source_url=source,
                    outbound_url=normalized,
                    anchor_text=text,
                    ad_fit_score=score,
                    notes=notes,
                    authorization_score=authorization_score,
                    authorization_notes=authorization_notes,
                    insertion_points=insertion_points,
                )
            )
            LOGGER.debug(
                "[NAV] %s -> %s | score=%s | notes=%s", source, normalized, score, notes
            )
            count += 1
            if count >= max_links:
                break
        LOGGER.info("[SCAN] %s: %s opportunités collectées", source, len(local_spots))
        return local_spots

    spots: list[AdSpot] = []
    sources = list(source_urls)
    workers = min(8, max(1, len(sources)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for res in pool.map(scan_source, sources):
            spots.extend(res)
    return sorted(spots, key=lambda s: s.ad_fit_score, reverse=True)


def local_ai_rank(spot: AdSpot, ads: list[AdCreative], model: str, timeout: int = 25) -> AdCreative | None:
    """Essaie d'utiliser une IA locale (Ollama) pour sélectionner une publicité."""
    if not ads:
        return None
    if not shutil.which("ollama"):
        return None

    catalog = "\n".join(f"- {ad.name} | niche={ad.target_niche}" for ad in ads)
    prompt = (
        "Tu es un assistant de matching pub. Retourne uniquement le nom exact de la meilleure pub.\n"
        f"Contexte opportunité: source={spot.source_url}, lien={spot.outbound_url}, "
        f"ancre={spot.anchor_text}, notes={spot.notes}.\n"
        f"Catalogue:\n{catalog}\n"
        "Réponds avec exactement un nom présent dans le catalogue, sans texte additionnel."
    )

    cmd = ["ollama", "run", model, prompt]
    try:
        raw = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        LOGGER.warning("[IA-LOCAL] Échec sélection (%s): %s", model, exc)
        return None

    picked_name = raw.stdout.strip()
    for ad in ads:
        if ad.name == picked_name:
            LOGGER.info("[IA-LOCAL] Sélection via %s: %s", model, ad.name)
            return ad
    LOGGER.warning("[IA-LOCAL] Réponse non reconnue: %s", picked_name)
    return None


def save_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def load_ad_library(path: Path = DEFAULT_AD_LIBRARY_PATH) -> list[AdCreative]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    ads: list[AdCreative] = []
    for item in raw:
        ads.append(
            AdCreative(
                name=str(item.get("name", "Sans nom")),
                target_niche=str(item.get("target_niche", "general")),
                embed_code=str(item.get("embed_code", "")).strip(),
            )
        )
    return ads


def load_or_bootstrap_ads(path: Path = DEFAULT_AD_LIBRARY_PATH) -> list[AdCreative]:
    ads = load_ad_library(path)
    if ads:
        return ads
    fallback = [
        AdCreative(
            name="AutoEmploye Starter Banner",
            target_niche="general",
            embed_code=(
                "<div class=\"auto-employe-ad\" style=\"padding:12px;border:1px solid #ddd;\">"
                "<strong>Votre publicité</strong><p>Remplacez ce bloc avec votre code officiel.</p></div>"
            ),
        )
    ]
    save_ad_library(fallback, path)
    LOGGER.warning("[AUTO] Bibliothèque vide: création d'une publicité par défaut dans %s", path)
    return fallback


def resolve_source_urls(urls: Iterable[str] | None) -> list[str]:
    cleaned = [item.strip() for item in (urls or []) if item.strip()]
    return cleaned or list(DEFAULT_SEED_URLS)


def save_ad_library(ads: list[AdCreative], path: Path = DEFAULT_AD_LIBRARY_PATH) -> None:
    payload = [
        {"name": ad.name, "target_niche": ad.target_niche, "embed_code": ad.embed_code} for ad in ads
    ]
    save_json(path, payload)


def suggest_ad_placement(
    spots: list[AdSpot],
    ads: list[AdCreative],
    use_local_ai: bool = False,
    local_ai_model: str = "llama3.2",
    auto_embed: bool = False,
) -> list[dict[str, object]]:
    suggestions: list[dict[str, object]] = []
    if not ads:
        return suggestions

    for spot in spots:
        spot_text = f"{spot.anchor_text} {spot.notes} {spot.outbound_url}".lower()
        ranked_ads = sorted(ads, key=lambda ad: int(ad.target_niche.lower() in spot_text), reverse=True)
        picked = ranked_ads[0]
        decision_engine = "heuristique"
        if use_local_ai:
            ai_pick = local_ai_rank(spot, ads, model=local_ai_model)
            if ai_pick is not None:
                picked = ai_pick
                decision_engine = f"ia-locale:{local_ai_model}"
        suggestions.append(
            {
                "source_url": spot.source_url,
                "outbound_url": spot.outbound_url,
                "ad_fit_score": spot.ad_fit_score,
                "selected_ad": picked.name,
                "target_niche": picked.target_niche,
                "embed_code": picked.embed_code,
                "decision_engine": decision_engine,
                "notes": (
                    "Suggestion: vérifier les CGU/contrat avant publication réelle."
                ),
                "authorization_score": spot.authorization_score,
                "authorization_notes": spot.authorization_notes,
                "insertion_points": spot.insertion_points,
                "auto_embed_ready": bool(auto_embed and spot.authorization_score > 0 and spot.insertion_points),
                "automation_payload": (
                    {
                        "mode": "dom-injection-template",
                        "target_url": spot.outbound_url,
                        "selector": spot.insertion_points[0],
                        "embed_code": picked.embed_code,
                    }
                    if auto_embed and spot.authorization_score > 0 and spot.insertion_points
                    else None
                ),
            }
        )
    return suggestions


def cmd_niches(args: argparse.Namespace) -> int:
    results = research_niches(args.topic, args.limit)
    payload = [
        {
            "title": r.title,
            "url": r.url,
            "snippet": r.snippet,
            "score": r.score,
            "matched_keywords": r.matched_keywords,
        }
        for r in results
    ]
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = Path(args.output_dir) / f"niches-{timestamp}"
    save_json(base.with_suffix(".json"), payload)
    save_csv(base.with_suffix(".csv"), payload)

    print(f"[OK] {len(payload)} niches analysées.")
    for row in payload[: min(5, len(payload))]:
        print(f"- ({row['score']:>2}) {row['title']} -> {row['url']}")
    print(f"Exports: {base.with_suffix('.json')} et {base.with_suffix('.csv')}")
    return 0


def cmd_adspots(args: argparse.Namespace) -> int:
    base_urls = resolve_source_urls(getattr(args, "urls", []))
    source_urls = discover_urls(base_urls, args.discover_limit) if args.discover_urls else base_urls
    spots = find_ad_spots(
        source_urls,
        args.max_links,
        min_authorization_score=args.min_authorization_score,
    )
    payload = [
        {
            "source_url": s.source_url,
            "outbound_url": s.outbound_url,
            "anchor_text": s.anchor_text,
            "ad_fit_score": s.ad_fit_score,
            "notes": s.notes,
            "authorization_score": s.authorization_score,
            "authorization_notes": s.authorization_notes,
            "insertion_points": s.insertion_points,
        }
        for s in spots
    ]
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = Path(args.output_dir) / f"adspots-{timestamp}"
    save_json(base.with_suffix(".json"), payload)
    save_csv(base.with_suffix(".csv"), payload)

    print(f"[OK] {len(payload)} opportunités extraites.")
    for row in payload[: min(10, len(payload))]:
        print(f"- ({row['ad_fit_score']:>2}) {row['outbound_url']} [{row['notes']}]")
    print(f"Exports: {base.with_suffix('.json')} et {base.with_suffix('.csv')}")
    return 0


def cmd_ads_add(args: argparse.Namespace) -> int:
    ads = load_ad_library(Path(args.library))
    ads.append(AdCreative(name=args.name, target_niche=args.niche, embed_code=args.embed_code))
    save_ad_library(ads, Path(args.library))
    print(f"[OK] Publicité ajoutée: {args.name}")
    return 0


def cmd_ads_list(args: argparse.Namespace) -> int:
    ads = load_ad_library(Path(args.library))
    if not ads:
        print("[INFO] Aucune publicité enregistrée.")
        return 0
    print(f"[OK] {len(ads)} publicité(s) disponibles:")
    for idx, ad in enumerate(ads, start=1):
        print(f"{idx:>2}. {ad.name} | niche={ad.target_niche}")
    return 0


def cmd_auto_run(args: argparse.Namespace) -> int:
    ads = load_or_bootstrap_ads(Path(args.library))

    cycle = itertools.count(1)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = Path(args.output_dir) / f"auto-placements-{timestamp}"

    print("[INFO] Automatisation lancée.")
    print("[INFO] Mode infini actif: Ctrl+C pour arrêter proprement.")
    print("[INFO] Le programme ne publie rien automatiquement: il propose des emplacements.")
    print(
        "[INFO] Moteur de décision: "
        + (f"IA locale ({args.local_ai_model})" if args.use_local_ai else "Heuristique locale")
    )

    try:
        for turn in cycle:
            cycle_started = time.perf_counter()
            LOGGER.info("[CYCLE %s] Début du cycle.", turn)
            base_urls = resolve_source_urls(getattr(args, "urls", []))
            source_urls = discover_urls(base_urls, args.discover_limit) if args.discover_urls else base_urls
            LOGGER.debug("[CYCLE %s] URLs source de base: %s", turn, base_urls)
            LOGGER.debug("[CYCLE %s] URLs effectivement analysées: %s", turn, source_urls)
            spots = find_ad_spots(
                source_urls,
                args.max_links,
                min_authorization_score=args.min_authorization_score,
            )
            suggestions = suggest_ad_placement(
                spots,
                ads,
                use_local_ai=args.use_local_ai,
                local_ai_model=args.local_ai_model,
                auto_embed=args.auto_embed,
            )
            auto_embed_ready = sum(1 for item in suggestions if item.get("auto_embed_ready"))
            LOGGER.info(
                "[CYCLE %s] spots=%s | suggestions=%s | auto_embed_ready=%s",
                turn,
                len(spots),
                len(suggestions),
                auto_embed_ready,
            )
            save_json(base.with_suffix(".json"), suggestions)
            save_csv(base.with_suffix(".csv"), suggestions)
            cycle_elapsed = time.perf_counter() - cycle_started
            print(
                f"[CYCLE {turn}] {len(suggestions)} suggestions générées. "
                f"Fichiers: {base.with_suffix('.json')} / {base.with_suffix('.csv')}"
                f" | durée={cycle_elapsed:.2f}s"
            )
            if not args.forever:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[INFO] Arrêt demandé par l'utilisateur.")
    return 0


def apply_full_auto_profile(args: argparse.Namespace) -> argparse.Namespace:
    """Active un profil agressif qui exploite toutes les options de `auto-run`."""
    if getattr(args, "command", None) != "auto-run":
        return args
    for key, value in FULL_AUTO_PROFILE.items():
        setattr(args, key, value)
    if not getattr(args, "urls", None):
        args.urls = list(DEFAULT_SEED_URLS)
    print(
        "[INFO] Profil full-auto activé: "
        "--discover-urls --discover-limit 40 --use-local-ai --auto-embed "
        "--min-authorization-score 0 --max-links 80 --interval 120 --forever --log-level DEBUG"
    )
    return args


def ask_choice(prompt: str, choices: dict[str, str]) -> str:
    print(prompt)
    for key, label in choices.items():
        print(f"{key}. {label}")
    while True:
        selected = input("Choix: ").strip()
        if selected in choices:
            return selected
        print("Choix invalide, recommencez.")


def cmd_menu(_: argparse.Namespace) -> int:
    while True:
        selection = ask_choice(
            "\n=== Menu AutoEmploye ===",
            {
                "1": "Recherche de niches",
                "2": "Recherche d'emplacements publicitaires",
                "3": "Ajouter une publicité",
                "4": "Lister les publicités",
                "5": "Lancer l'automatisation (mode infini)",
                "0": "Quitter",
            },
        )

        if selection == "0":
            print("Au revoir.")
            return 0

        if selection == "1":
            topic = input("Sujet: ").strip()
            limit = int(input("Nombre max de résultats (défaut 15): ").strip() or "15")
            cmd_niches(argparse.Namespace(topic=topic, limit=limit, output_dir="outputs"))
        elif selection == "2":
            urls = input("URLs (séparées par espace): ").split()
            max_links = int(input("Liens max par page (défaut 40): ").strip() or "40")
            cmd_adspots(
                argparse.Namespace(
                    urls=urls,
                    max_links=max_links,
                    output_dir="outputs",
                    discover_urls=True,
                    discover_limit=20,
                    min_authorization_score=0,
                )
            )
        elif selection == "3":
            name = input("Nom de la publicité: ").strip()
            niche = input("Niche cible: ").strip() or "general"
            print("Collez le code embed puis tapez une ligne contenant uniquement END")
            lines: list[str] = []
            while True:
                line = input()
                if line.strip() == "END":
                    break
                lines.append(line)
            cmd_ads_add(
                argparse.Namespace(
                    name=name,
                    niche=niche,
                    embed_code="\n".join(lines),
                    library=str(DEFAULT_AD_LIBRARY_PATH),
                )
            )
        elif selection == "4":
            cmd_ads_list(argparse.Namespace(library=str(DEFAULT_AD_LIBRARY_PATH)))
        elif selection == "5":
            urls = input("URLs (séparées par espace): ").split()
            max_links = int(input("Liens max par page (défaut 40): ").strip() or "40")
            interval = int(input("Intervalle entre cycles en secondes (défaut 300): ").strip() or "300")
            cmd_auto_run(
                argparse.Namespace(
                    urls=urls,
                    max_links=max_links,
                    output_dir="outputs",
                    library=str(DEFAULT_AD_LIBRARY_PATH),
                    interval=interval,
                    forever=True,
                    discover_urls=True,
                    discover_limit=20,
                    min_authorization_score=3,
                    auto_embed=True,
                    use_local_ai=False,
                    local_ai_model="llama3.2",
                )
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto-employe",
        description=(
            "Automatise une veille de niches monétisables et détecte des pages "
            "où proposer de la publicité (URL publiques)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=False)

    p_niches = sub.add_parser("niches", help="Recherche des niches rentables")
    p_niches.add_argument("topic", help="Sujet de recherche, ex: 'AI B2B newsletter' ")
    p_niches.add_argument("--limit", type=int, default=15, help="Nombre max de résultats")
    p_niches.add_argument("--output-dir", default="outputs", help="Dossier d'export")
    p_niches.set_defaults(func=cmd_niches)

    p_ad = sub.add_parser("adspots", help="Analyse des URLs pour trouver des spots pub")
    p_ad.add_argument("urls", nargs="*", help="URLs sources à analyser")
    p_ad.add_argument("--max-links", type=int, default=40, help="Liens sortants max par page")
    p_ad.add_argument(
        "--discover-urls",
        action="store_true",
        help="Découvre automatiquement de nouvelles URLs internes depuis les URLs sources",
    )
    p_ad.add_argument(
        "--discover-limit",
        type=int,
        default=20,
        help="Nombre max de nouvelles URLs internes par source",
    )
    p_ad.add_argument(
        "--min-authorization-score",
        type=int,
        default=0,
        help="Filtre les emplacements en dessous d'un score minimal d'autorisation",
    )
    p_ad.add_argument("--output-dir", default="outputs", help="Dossier d'export")
    p_ad.set_defaults(func=cmd_adspots)

    p_ads_add = sub.add_parser("ads-add", help="Ajoute une publicité dans la bibliothèque locale")
    p_ads_add.add_argument("--name", required=True, help="Nom interne de la publicité")
    p_ads_add.add_argument("--niche", default="general", help="Niche cible")
    p_ads_add.add_argument("--embed-code", required=True, help="Code d'intégration publicitaire")
    p_ads_add.add_argument("--library", default=str(DEFAULT_AD_LIBRARY_PATH), help="Chemin bibliothèque")
    p_ads_add.set_defaults(func=cmd_ads_add)

    p_ads_list = sub.add_parser("ads-list", help="Liste les publicités de la bibliothèque")
    p_ads_list.add_argument("--library", default=str(DEFAULT_AD_LIBRARY_PATH), help="Chemin bibliothèque")
    p_ads_list.set_defaults(func=cmd_ads_list)

    p_auto = sub.add_parser("auto-run", help="Lance l'automatisation et propose des placements")
    p_auto.add_argument("urls", nargs="*", help="URLs sources à analyser")
    p_auto.add_argument("--max-links", type=int, default=40, help="Liens sortants max par page")
    p_auto.add_argument("--output-dir", default="outputs", help="Dossier d'export")
    p_auto.add_argument("--library", default=str(DEFAULT_AD_LIBRARY_PATH), help="Chemin bibliothèque")
    p_auto.add_argument("--interval", type=int, default=300, help="Intervalle entre cycles")
    p_auto.add_argument(
        "--full-auto",
        action="store_true",
        help=(
            "Force un profil maximal qui exploite toutes les options auto-run "
            "(découverte URL, IA locale, payload auto-embed, logs DEBUG, boucle infinie, etc.)"
        ),
    )
    p_auto.add_argument(
        "--use-local-ai",
        action="store_true",
        help="Utilise une IA locale (Ollama) pour choisir la meilleure pub quand disponible",
    )
    p_auto.add_argument(
        "--local-ai-model",
        default="llama3.2",
        help="Modèle Ollama à utiliser pour le matching pub (défaut: llama3.2)",
    )
    p_auto.add_argument(
        "--discover-urls",
        action="store_true",
        help="Découvre automatiquement de nouvelles URLs internes à chaque cycle",
    )
    p_auto.add_argument(
        "--discover-limit",
        type=int,
        default=20,
        help="Nombre max de nouvelles URLs internes par source",
    )
    p_auto.add_argument(
        "--min-authorization-score",
        type=int,
        default=3,
        help="Score minimal d'autorisation pour proposer un emplacement",
    )
    p_auto.add_argument(
        "--auto-embed",
        action="store_true",
        help="Ajoute un payload d'automatisation DOM pour l'employé sur les emplacements autorisés",
    )
    p_auto.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Niveau de logs détaillés dans le terminal",
    )
    p_auto.add_argument(
        "--forever",
        action="store_true",
        help="Boucle infinie (sinon un seul cycle)",
    )
    p_auto.set_defaults(func=cmd_auto_run)

    p_menu = sub.add_parser("menu", help="Interface interactive avec options numérotées")
    p_menu.set_defaults(func=cmd_menu)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args = argparse.Namespace(
            command="auto-run",
            func=cmd_auto_run,
            full_auto=True,
            urls=list(DEFAULT_SEED_URLS),
            max_links=FULL_AUTO_PROFILE["max_links"],
            output_dir="outputs",
            library=str(DEFAULT_AD_LIBRARY_PATH),
            interval=FULL_AUTO_PROFILE["interval"],
            forever=FULL_AUTO_PROFILE["forever"],
            discover_urls=FULL_AUTO_PROFILE["discover_urls"],
            discover_limit=FULL_AUTO_PROFILE["discover_limit"],
            min_authorization_score=FULL_AUTO_PROFILE["min_authorization_score"],
            auto_embed=FULL_AUTO_PROFILE["auto_embed"],
            use_local_ai=FULL_AUTO_PROFILE["use_local_ai"],
            local_ai_model=FULL_AUTO_PROFILE["local_ai_model"],
            log_level=FULL_AUTO_PROFILE["log_level"],
        )
        print("[INFO] Aucun argument détecté: démarrage en mode full auto infini.")
    if getattr(args, "command", None) == "auto-run" and getattr(args, "full_auto", False):
        args = apply_full_auto_profile(args)
    logging.basicConfig(
        level=getattr(logging, str(getattr(args, "log_level", "INFO")).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
