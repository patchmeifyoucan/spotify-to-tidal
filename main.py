import json
import re

import numpy as np
from rapidfuzz import fuzz
import os
import pickle
import sys
import time

import spotipy
import tidalapi
from loguru import logger
from spotipy.oauth2 import SpotifyOAuth

TIDAL_SESSION_FILE = "tidal_session.pkl"


def configure_logging(filename=None, level="INFO"):
    fmt = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> {elapsed} <level>{level: <4}</level> "

    fmt_chunks = [" <level>", "</level>"]
    fmt += "".join(fmt_chunks)
    fmt += " {message}"
    args = {"format": fmt, "level": level}
    logger.remove(0)
    logger.add(sys.stdout, **args)

    if filename is not None:
        logger.add(filename, **args)


def load_config(path):
    with open(path) as f:
        return json.load(f)


def clean(text: str) -> str:
    replace_list = [
        "&",
        "@",
        "(",
        ")",
        "ft.",
        "feat.",
        "featuring",
        "original mix",
    ]

    text = text.lower()
    pattern = "|".join(re.escape(s) for s in replace_list)
    text = re.sub(pattern, "", text)
    text = re.sub(r"[-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def spotify_session(cfg):
    auth = SpotifyOAuth(
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        redirect_uri=cfg["redirect_uri"],
        scope="playlist-read-private",
    )
    return spotipy.Spotify(auth_manager=auth)


def tidal_session():
    if os.path.exists(TIDAL_SESSION_FILE):
        with open(TIDAL_SESSION_FILE, "rb") as f:
            session = pickle.load(f)
        if session.check_login():
            logger.info("loaded saved tidal session")
            return session
        logger.info("saved tidal session expired, logging in again")

    session = tidalapi.Session()
    login, future = session.login_oauth()

    logger.info("open this url in your browser to log in to tidal:")
    print(login.verification_uri_complete)

    future.result()

    if not session.check_login():
        raise RuntimeError("failed to log in to tidal")

    with open(TIDAL_SESSION_FILE, "wb") as f:
        pickle.dump(session, f)  # noqa
    logger.info("tidal session saved")
    return session


def find_spotify_playlist_by_name(sp, name):
    results = sp.current_user_playlists(limit=50)
    while results:
        for pl in results["items"]:
            if pl["name"] == name:
                return pl["id"]
        results = sp.next(results) if results["next"] else None

    return None


def fetch_spotify_playlist_tracks(sp, playlist_id):
    results = sp.playlist_items(playlist_id, additional_types=("track",))
    tracks = []
    while results:
        for item in results["items"]:
            track = item.get("track")
            if not track:
                continue
            name = track.get("name")

            # TODO define how to search for artists to not confuse search
            artists = track["artists"][0]["name"]

            iscr = track["external_ids"]["isrc"]
            tracks.append({"name": name, "artists": artists, "isrc": iscr})

        results = sp.next(results) if results["next"] else None

    return tracks


def ensure_tidal_playlist(session, prefix, name, description="Imported from Spotify"):
    user = session.user

    name = f"{prefix}{name}"

    for playlist in user.playlists():
        if playlist.name == name:
            logger.info(f"playlist {name} exists")
            return playlist

    logger.info(f"creating playlist {name}")
    return user.create_playlist(name, description)


def search_tidal_by_name_and_artist(session, track, auto):
    name = track["name"]
    artists = track["artists"]
    isrc = track["isrc"]

    query = clean(f"{artists} {name}")
    results = session.search(query, models=[tidalapi.Track], limit=100)
    tracks = results["tracks"]

    if not tracks:
        logger.info(f"nothing found for query: '{query}'")
        return None

    for t in tracks:
        if t.isrc == isrc:
            return t

    fuzzy_scores = (
        np.array([fuzz.token_set_ratio(name, t.name) for t in tracks])
        .round()
        .astype(int)
    )

    n = 9
    top_n = np.argsort(fuzzy_scores)[::-1][:n]

    if auto:
        track = tracks[0]
        track_name = track.name
        track_artist = track.artists[0].name
        logger.info(
            f"auto-matched '{query}' with '{track_artist} - {track_name}' (score: {fuzzy_scores[0]})"
        )
        return track

    logger.info(f"\n\nno exact match for query: '{query}'. best matches:")

    for idx, i in enumerate(top_n):
        track_name = tracks[i].name
        track_artist = tracks[i].artists[0].name
        print(f"({idx+1}): {track_artist} - {track_name} (score: {fuzzy_scores[i]})")

    while True:
        read = input("select the number you want to add. press RETURN to skip:\n")

        if len(read) == 0:
            return None

        try:
            selected = int(read)
        except ValueError:
            logger.error(f"'{read}' is not a number dummy... try again")
            continue

        valid = set(range(1, n + 1))

        if selected not in valid:
            logger.error(f"must select any of {valid}")
            continue

        return tracks[selected - 1]


def add_tracks(playlist, tracks):
    track_ids = [t.id for t in tracks]
    playlist.add(track_ids)


def main():
    configure_logging("sync.log")

    conf = load_config("config.json")

    sp = spotify_session(conf["spotify"])
    tidal = tidal_session()

    prefix = conf["prefix"]
    auto = conf["auto"]

    batch_size = 100

    for playlist_name in conf["spotify"]["playlists"]:
        start = time.time()

        logger.info(f"lookup spotify playlist: {playlist_name}")
        playlist_id = find_spotify_playlist_by_name(sp, playlist_name)

        if not playlist_id:
            logger.info(f"spotify playlist not found: {playlist_name}")
            continue

        logger.info(f"fetch spotify playlist: {playlist_name}")
        tracks = fetch_spotify_playlist_tracks(sp, playlist_id)

        logger.info(f"check tidal playlist: {playlist_name}")
        tidal_playlist = ensure_tidal_playlist(tidal, prefix, playlist_name)

        logger.info(f"search tidal tracks...")

        tidal_tracks = []

        done = 0

        for track in tracks:
            found = search_tidal_by_name_and_artist(tidal, track, auto)

            if found is not None:
                tidal_tracks.append(found)

            if len(tidal_tracks) == batch_size:
                done += len(tidal_tracks)
                add_tracks(tidal_playlist, tidal_tracks)
                tidal_tracks = []
                logger.info(f"{done}/{len(tracks)} done")

        done += len(tidal_tracks)
        add_tracks(tidal_playlist, tidal_tracks)
        logger.info(f"{done}/{len(tracks)} done")

        logger.info(f"search tidal tracks done")

        if not tidal_tracks:
            continue

        logger.info(f"add tidal tracks...")
        add_tracks(tidal_playlist, tidal_tracks)
        logger.info(f"add tidal tracks done")

        end = time.time()
        duration = round(end - start, 2)

        logger.info(
            f"added {done}/{len(tracks)} tracks to '{playlist_name}' in {duration}s"
        )


if __name__ == "__main__":
    main()
