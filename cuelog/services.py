"""Higher-level workflows shared by the UI and tests: matching a set, artwork, set import."""
from __future__ import annotations

import json
from typing import Callable

from . import db, musicbrainz, repo, spotify
from .config import Credentials, load_credentials
from .matching import HIGH_CONFIDENCE, Candidate, classify, rank
from .spotify import SpotifyClient, SpotifyError
from .tracklist import TrackEntry


class MatchService:
    """Finds candidates for tracklist entries. Spotify when configured, else MusicBrainz."""

    def __init__(self, creds: Credentials | None = None, spotify_client: SpotifyClient | None = None,
                 mb_search: Callable | None = None):
        self.creds = creds or load_credentials()
        self.spotify = spotify_client or SpotifyClient(self.creds)
        self.mb_search = mb_search or musicbrainz.search

    @property
    def provider(self) -> str:
        return "spotify" if self.spotify.configured else "musicbrainz"

    def search(self, artist: str, title: str) -> list[Candidate]:
        if self.spotify.configured:
            return self.spotify.search(artist, title)
        return self.mb_search(artist, title)

    def song_from_spotify_link(self, track_id: str) -> tuple[int, str, str]:
        """Returns (song_id, status, note) for a pasted Spotify track link."""
        if self.spotify.configured:
            cand = self.spotify.track(track_id)
            return repo.upsert_song(cand), "confirmed", "Pasted Spotify link"
        meta = spotify.oembed(track_id)
        cand = Candidate("spotify", meta.get("title", ""), [], "", spotify_id=track_id,
                         url=f"https://open.spotify.com/track/{track_id}", art_url=meta.get("thumbnail_url", ""),
                         art_source="spotify-oembed")
        return (repo.upsert_song(cand), "ambiguous",
                "Pasted Spotify link. Spotify API not configured, so the artist is unknown: add it manually.")

    def match_track(self, track: dict) -> str:
        """Match one set_tracks row (dict from repo.tracks_for_set). Returns the new match status."""
        cached = repo.cached_match(track["parsed_artist"], track["parsed_title"])
        if cached:
            repo.assign_match(track["id"], cached, "confirmed", 1.0, "Reused a match you verified earlier", remember=False)
            return "confirmed"
        candidates = self.search(track["parsed_artist"], track["parsed_title"])
        ranked = rank(track["parsed_artist"], track["parsed_title"], candidates)
        repo.set_candidates(track["id"], [{"score": r.score, "reasons": r.reasons, "level": r.level,
                                           "candidate": r.candidate.to_dict()} for r in ranked[:8]])
        status, conf = classify(ranked)
        if status == "unmatched":
            note = "No convincing match found" if ranked else "No results"
            repo.assign_match(track["id"], None, "unmatched", conf, note, remember=False)
            return status
        top = ranked[0]
        song_id = repo.upsert_song(top.candidate)
        note = "; ".join(top.reasons) or ("Strong match" if status == "auto" else "Check this match")
        repo.assign_match(track["id"], song_id, status, top.score, note, remember=False)
        return status

    def match_set(self, set_id: int, *, only_open: bool = True, progress: Callable[[int, int, str], None] | None = None,
                  cancelled: Callable[[], bool] = lambda: False) -> dict:
        tracks = repo.tracks_for_set(set_id)
        todo = [t for t in tracks if not only_open or t["match_status"] in ("unmatched", "ambiguous")]
        result = {"auto": 0, "ambiguous": 0, "unmatched": 0, "confirmed": 0, "errors": []}
        for n, t in enumerate(todo, 1):
            if cancelled():
                break
            label = f"{t['parsed_artist']} - {t['parsed_title']}".strip(" -")
            if progress:
                progress(n, len(todo), label)
            try:
                if t.get("spotify_id") and t["match_status"] in ("unmatched", "ambiguous") and not t["parsed_artist"]:
                    continue
                status = self.match_track(t)
                result[status] = result.get(status, 0) + 1
            except (SpotifyError, RuntimeError) as err:
                result["errors"].append(f"{label}: {err}")
                if len(result["errors"]) >= 3 and not result["auto"] and not result["ambiguous"]:
                    break
        return result


def accept_high_confidence(set_id: int) -> int:
    n = 0
    for t in repo.tracks_for_set(set_id):
        if t["match_status"] == "auto" and (t["match_confidence"] or 0) >= HIGH_CONFIDENCE and t["song_id"]:
            repo.assign_match(t["id"], t["song_id"], "confirmed", t["match_confidence"], t["match_note"])
            n += 1
    return n


def choose_candidate(track_id: int, cand: dict | Candidate, score: float | None = None) -> int:
    c = cand if isinstance(cand, Candidate) else Candidate.from_dict(cand)
    song_id = repo.upsert_song(c)
    repo.assign_match(track_id, song_id, "confirmed", score, "Chosen by you")
    return song_id


def build_track_inputs(entries: list[TrackEntry], service: MatchService | None = None) -> list[repo.TrackInput]:
    inputs = []
    for e in entries:
        ti = repo.TrackInput(e.raw_line, e.artist, e.title, e.start_s, "tracklist" if e.start_s is not None else None)
        if e.spotify_id and service is not None:
            try:
                ti.song_id, ti.match_status, ti.match_note = service.song_from_spotify_link(e.spotify_id)
                ti.spotify_id = e.spotify_id
                song = repo.get_song(ti.song_id)
                if song and not ti.title:
                    ti.title = song["title"]
                    ti.artist = song["artists"][0] if song["artists"] else ""
            except Exception as err:  # network problems must not lose the line
                ti.match_note = f"Could not load Spotify link: {err}"
        inputs.append(ti)
    return inputs


def fill_missing_artwork(limit: int = 200, progress: Callable[[int, int], None] | None = None) -> int:
    """Fetch official Spotify artwork via keyless oEmbed for songs that have a Spotify ID but no art."""
    songs = repo.songs_missing_art()[:limit]
    done = 0
    for n, s in enumerate(songs, 1):
        try:
            meta = spotify.oembed(s["spotify_id"])
            if meta.get("thumbnail_url"):
                repo.update_song_art(s["id"], meta["thumbnail_url"], "spotify-oembed")
                done += 1
        except Exception:
            pass
        if progress:
            progress(n, len(songs))
    return done


def save_scanned_metadata(set_id: int, *, duration_s: int | None = None, description: str | None = None,
                          artwork_url: str | None = None) -> None:
    fields = {}
    s = repo.get_set(set_id)
    if duration_s and not s["duration_s"]:
        fields["duration_s"] = int(duration_s)
    if description and not s["description"]:
        fields["description"] = description
    if artwork_url and not s["artwork_url"]:
        fields["artwork_url"] = artwork_url
    if fields:
        repo.update_set(set_id, **fields)


def refresh_missing_set_metadata() -> int:
    """Fill artwork/uploader of saved sets (e.g. migrated ones) from SoundCloud oEmbed. Raw data stays separate."""
    from . import soundcloud
    n = 0
    rows = db.get().execute("SELECT id, source_url FROM sets WHERE source_url IS NOT NULL AND "
                            "(artwork_url IS NULL OR artwork_url = '' OR uploader IS NULL OR uploader = '')").fetchall()
    for row in rows:
        try:
            meta = soundcloud.oembed(row["source_url"])
        except Exception:
            continue
        db.get().execute("UPDATE sc_resources SET uploader = ?, artwork_url = ?, raw_json = ? WHERE url = ?",
                         (meta.uploader, meta.artwork_url, json.dumps(meta.raw, ensure_ascii=False), row["source_url"]))
        db.get().execute("""UPDATE sets SET artwork_url = COALESCE(NULLIF(artwork_url, ''), ?),
                            uploader = COALESCE(NULLIF(uploader, ''), ?) WHERE id = ?""",
                         (meta.artwork_url, meta.uploader, row["id"]))
        n += 1
    return n


def fill_manual_artwork(limit: int = 30) -> int:
    """For songs without a Spotify ID and without art, look up Cover Art Archive artwork via MusicBrainz.

    Only a high-confidence recording match is used, and the artwork is labelled 'coverartarchive'.
    Song metadata itself is never changed.
    """
    from .matching import HIGH_CONFIDENCE
    rows = db.get().execute("""SELECT id, title, artists_json FROM songs WHERE spotify_id IS NULL
                               AND (art_url IS NULL OR art_url = '') LIMIT ?""", (limit,)).fetchall()
    done = 0
    for r in rows:
        artists = json.loads(r["artists_json"] or "[]")
        artist = artists[0] if artists else ""
        try:
            ranked = rank(artist, r["title"], musicbrainz.search(artist, r["title"], limit=5))
        except Exception:
            continue
        top = next((x for x in ranked if x.candidate.art_url and x.score >= HIGH_CONFIDENCE), None)
        if top:
            repo.update_song_art(r["id"], top.candidate.art_url, "coverartarchive")
            done += 1
    return done


def startup_enrichment() -> int:
    return refresh_missing_set_metadata() + fill_missing_artwork() + fill_manual_artwork()
