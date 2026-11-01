"""Candidate ranking for tracklist line -> recording matches.

Scores combine title similarity, artist overlap and *version compatibility*.
A different remix / radio edit / extended mix is penalised hard so a similar
title alone never wins.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

HIGH_CONFIDENCE = 0.85
MEDIUM_CONFIDENCE = 0.6


@dataclass
class Candidate:
    source: str                    # spotify | musicbrainz | manual
    title: str
    artists: list[str]
    album: str = ""
    duration_ms: int | None = None
    release_date: str = ""
    url: str = ""
    art_url: str = ""
    art_source: str = ""
    spotify_id: str | None = None
    mbid: str | None = None
    isrc: str | None = None
    popularity: int | None = None
    raw: dict = field(default_factory=dict)

    @property
    def main_artist(self) -> str:
        """Last.fm receives ONLY the first credited artist."""
        return self.artists[0] if self.artists else ""

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d.pop("raw", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Candidate":
        known = {k: d.get(k) for k in cls.__dataclass_fields__ if k in d}
        known.setdefault("artists", [])
        return cls(**known)


@dataclass
class Ranked:
    candidate: Candidate
    score: float
    reasons: list[str]

    @property
    def level(self) -> str:
        return confidence_level(self.score)


def confidence_level(score: float | None) -> str:
    if score is None:
        return "none"
    if score >= HIGH_CONFIDENCE:
        return "high"
    if score >= MEDIUM_CONFIDENCE:
        return "medium"
    return "low"


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = strip_accents(s).casefold()
    s = s.replace("&", " and ").replace("+", " and ")
    s = re.sub(r"[’'`´]", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = s.replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def query_key(artist: str, title: str) -> str:
    return f"{norm(artist)}|{norm(title)}"


_FEAT = re.compile(r"\s*(?:\(|\[|-\s*)?\b(?:feat\.?|ft\.?|featuring|with)\s+([^()\[\]]+?)(?:\)|\]|$)", re.I)
_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|\band\b|\bx\b|\bvs\.?\b|\bversus\b|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bmeets\b|/|\+)\s*", re.I)
_BRACKETS = re.compile(r"[\(\[]([^\)\]]+)[\)\]]")

REMIX_WORDS = r"remix|rmx|bootleg|rework|re-?work|flip|vip|remake|refix|reboot|edit by|mashup"
_KIND_PATTERNS = [
    ("remix", re.compile(rf"\b({REMIX_WORDS})\b", re.I)),
    ("radio", re.compile(r"\bradio\s*(edit|mix|version|cut)?\b", re.I)),
    ("extended", re.compile(r"\b(extended|club)\s*(mix|version|edit)?\b", re.I)),
    ("live", re.compile(r"\blive\b(?!\s+the\b)", re.I)),
    ("remaster", re.compile(r"\bremaster(ed)?\b", re.I)),
    ("edit", re.compile(r"\b(edit|cut|short\s*mix)\b", re.I)),
    ("original", re.compile(r"\boriginal(\s*(mix|version|edit))?\b", re.I)),
]
_DESCRIPTOR = re.compile(r"\b(anthem|ost|theme|official|qlimax|defqon|q-?base|mysteryland|intro|outro)\b", re.I)


@dataclass
class TitleInfo:
    base: str            # normalised title without version/feature descriptors
    kind: str | None     # remix | radio | extended | live | remaster | edit | original | None
    version_text: str    # normalised descriptor text (e.g. remixer name)
    featured: list[str]


def title_info(title: str) -> TitleInfo:
    t = unicodedata.normalize("NFKC", title or "")
    featured: list[str] = []
    for m in _FEAT.finditer(t):
        featured += [norm(a) for a in _ARTIST_SPLIT.split(m.group(1)) if norm(a)]
    t = _FEAT.sub(" ", t)
    descriptors = [m.group(1) for m in _BRACKETS.finditer(t)]
    t = _BRACKETS.sub(" ", t)
    if " - " in t:
        head, tail = t.split(" - ", 1)
        descriptors.append(tail)
        t = head
    kind = None
    version_text = ""
    for d in descriptors:
        for k, pat in _KIND_PATTERNS:
            if pat.search(d):
                if kind is None or kind == "original":
                    kind = k
                    version_text = norm(pat.sub(" ", d))
                    version_text = re.sub(r"\b(mix|version)\b", " ", version_text).strip()
                    version_text = re.sub(r"\s+", " ", version_text)
                break
    if kind == "original":
        version_text = ""
    return TitleInfo(norm(t), kind, version_text, featured)


def artist_tokens(text: str) -> set[str]:
    parts = [norm(a) for a in _ARTIST_SPLIT.split(text or "")]
    tokens = set()
    for p in parts:
        tokens.update(p.split())
    return tokens - {"and", "the", "dj", "mc"}


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    jacc = len(ta & tb) / len(ta | tb) if ta | tb else 0
    return max(ratio, jacc)


def version_adjustment(q: TitleInfo, c: TitleInfo) -> tuple[float, str | None]:
    qk = None if q.kind == "original" else q.kind
    ck = None if c.kind == "original" else c.kind
    if qk == ck:
        if qk in ("remix", "extended", "edit") and q.version_text and c.version_text:
            s = _sim(q.version_text, c.version_text)
            if s < 0.6:
                return -0.4, f"Different {qk}: '{c.version_text}' vs '{q.version_text}'"
        return 0.0, None
    if qk == "remix" or ck == "remix":
        return -0.4, "Remix mismatch" if ck else "Tracklist names a remix this release lacks"
    if ck == "radio" and qk is None:
        return -0.1, "Spotify version is a radio edit"
    if ck == "extended" and qk is None:
        return -0.06, "Spotify version is an extended mix"
    if ck == "edit" and qk is None:
        return -0.08, "Spotify version is an edit"
    if qk in ("radio", "extended", "edit") and ck is None:
        return -0.12, f"Tracklist asks for a {qk} version"
    if qk in ("radio", "extended", "edit") and ck in ("radio", "extended", "edit"):
        return -0.2, f"Tracklist asks for {qk}, Spotify has {ck}"
    return -0.15, f"Version differs ({ck or 'standard'} vs {qk or 'standard'})"


def score(query_artist: str, query_title: str, cand: Candidate, *, isrc: str | None = None) -> Ranked:
    reasons: list[str] = []
    if isrc and cand.isrc and isrc.upper() == cand.isrc.upper():
        return Ranked(cand, 1.0, ["ISRC match"])
    q = title_info(query_title)
    c = title_info(cand.title)
    title_s = _sim(q.base, c.base)
    q_art = artist_tokens(query_artist) | set(" ".join(q.featured).split())
    c_art = set()
    for a in cand.artists:
        c_art |= artist_tokens(a)
    c_art |= set(" ".join(c.featured).split())
    if q_art:
        artist_s = len(q_art & c_art) / len(q_art)
        main = artist_tokens(cand.main_artist)
        if main and not (main & q_art):
            artist_s *= 0.8
            reasons.append("Spotify's first artist is not in the tracklist line")
    else:
        artist_s = 0.5
    adj, why = version_adjustment(q, c)
    if why:
        reasons.append(why)
    if title_s < 0.75:
        reasons.append("Title differs")
    if artist_s < 0.5:
        reasons.append("Artists differ")
    total = max(0.0, min(1.0, 0.55 * title_s + 0.45 * artist_s + adj))
    return Ranked(cand, round(total, 3), reasons)


def rank(query_artist: str, query_title: str, candidates: list[Candidate], *, isrc: str | None = None) -> list[Ranked]:
    seen: set = set()
    ranked = []
    for cand in candidates:
        key = cand.spotify_id or cand.mbid or (norm(cand.title), tuple(norm(a) for a in cand.artists), norm(cand.album))
        if key in seen:
            continue
        seen.add(key)
        ranked.append(score(query_artist, query_title, cand, isrc=isrc))
    # Highest score first; ties prefer earlier releases (originals), then popularity.
    ranked.sort(key=lambda r: (-r.score, r.candidate.release_date or "9999", -(r.candidate.popularity or 0)))
    return ranked


def is_ambiguous(ranked: list[Ranked]) -> bool:
    if not ranked:
        return False
    top = ranked[0]
    if top.score < HIGH_CONFIDENCE:
        return True
    for other in ranked[1:4]:
        if top.score - other.score > 0.04:
            break
        ti, oi = title_info(top.candidate.title), title_info(other.candidate.title)
        if ti.base != oi.base or ti.kind != oi.kind or ti.version_text != oi.version_text:
            return True
    return False


def classify(ranked: list[Ranked]) -> tuple[str, float | None]:
    """Return (match_status, confidence) for an automatic match result."""
    if not ranked:
        return "unmatched", None
    top = ranked[0]
    if top.score < MEDIUM_CONFIDENCE:
        return "unmatched", top.score
    return ("ambiguous" if is_ambiguous(ranked) else "auto"), top.score
