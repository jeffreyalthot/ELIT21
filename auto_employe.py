#!/usr/bin/env python3
"""AutoEmploye IA Local

CLI pour automatiser une veille de niches rentables et l'identification
 d'emplacements publicitaires basés sur des URLs publiques.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

USER_AGENT = "AutoEmployeResearchBot/1.0 (+local-cli)"

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


def fetch_url(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def duckduckgo_search(query: str, limit: int = 10) -> list[dict[str, str]]:
    encoded = urllib.parse.quote_plus(query)
    url = f"https://duckduckgo.com/html/?q={encoded}"
    page = fetch_url(url)

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


def find_ad_spots(source_urls: Iterable[str], max_links: int) -> list[AdSpot]:
    spots: list[AdSpot] = []
    for source in source_urls:
        html_text = fetch_url(source)
        parser = LinkExtractor()
        parser.feed(html_text)
        count = 0
        for href, text in parser.links:
            normalized = normalize_url(source, href)
            if not normalized:
                continue
            if urllib.parse.urlparse(normalized).netloc == urllib.parse.urlparse(source).netloc:
                continue
            score, notes = ad_score(text, normalized)
            spots.append(
                AdSpot(
                    source_url=source,
                    outbound_url=normalized,
                    anchor_text=text,
                    ad_fit_score=score,
                    notes=notes,
                )
            )
            count += 1
            if count >= max_links:
                break
    return sorted(spots, key=lambda s: s.ad_fit_score, reverse=True)


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
    spots = find_ad_spots(args.urls, args.max_links)
    payload = [
        {
            "source_url": s.source_url,
            "outbound_url": s.outbound_url,
            "anchor_text": s.anchor_text,
            "ad_fit_score": s.ad_fit_score,
            "notes": s.notes,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto-employe",
        description=(
            "Automatise une veille de niches monétisables et détecte des pages "
            "où proposer de la publicité (URL publiques)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_niches = sub.add_parser("niches", help="Recherche des niches rentables")
    p_niches.add_argument("topic", help="Sujet de recherche, ex: 'AI B2B newsletter' ")
    p_niches.add_argument("--limit", type=int, default=15, help="Nombre max de résultats")
    p_niches.add_argument("--output-dir", default="outputs", help="Dossier d'export")
    p_niches.set_defaults(func=cmd_niches)

    p_ad = sub.add_parser("adspots", help="Analyse des URLs pour trouver des spots pub")
    p_ad.add_argument("urls", nargs="+", help="URLs sources à analyser")
    p_ad.add_argument("--max-links", type=int, default=40, help="Liens sortants max par page")
    p_ad.add_argument("--output-dir", default="outputs", help="Dossier d'export")
    p_ad.set_defaults(func=cmd_adspots)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
