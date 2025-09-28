import json
import os
import pickle
import logging
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import tidalapi

TIDAL_SESSION_FILE = "tidal_session.pkl"

def load_config(path):
    with open(path) as f:
        return json.load(f)

def spotify_session(cfg):
    auth = SpotifyOAuth(
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        redirect_uri=cfg["redirect_uri"],
        scope="playlist-read-private"
    )
    return spotipy.Spotify(auth_manager=auth)

def tidal_session():
    if os.path.exists(TIDAL_SESSION_FILE):
        with open(TIDAL_SESSION_FILE, "rb") as f:
            session = pickle.load(f)
        if session.check_login():
            print("Loaded saved Tidal session")
            return session
        print("Saved Tidal session expired, logging in again")

    session = tidalapi.Session()
    login, future = session.login_oauth()
    print("Open this URL in your browser to log in to Tidal:")
    print(login.verification_uri_complete)
    future.result()

    if not session.check_login():
        raise RuntimeError("Failed to log in to Tidal")

    with open(TIDAL_SESSION_FILE, "wb") as f:
        pickle.dump(session, f)
    print("Tidal session saved")
    return session

def find_spotify_playlist_by_name(sp, name):
    results = sp.current_user_playlists(limit=50)
    while results:
        for pl in results["items"]:
            if pl["name"] == name:
                return pl["id"]
        if results["next"]:
            results = sp.next(results)
        else:
            results = None
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
            artists = ", ".join(a["name"] for a in track.get("artists", []))
            iscr = track["external_ids"]["isrc"]
            tracks.append({"name": name, "artists": artists, "isrc":iscr})
        if results["next"]:
            results = sp.next(results)
        else:
            results = None
    return tracks

def ensure_tidal_playlist(session, name, description="Imported from Spotify"):
    user = session.user

    for playlist in user.playlists():
        if playlist.name == name:
            return playlist

    return user.create_playlist(name, description)

def search_tidal_by_name_and_artist(session, track):
    name = track["name"]
    artists = track["artists"]
    isrc = track["isrc"]

    query = f"{name} {artists}"
    results = session.search(query, models=[tidalapi.Track], limit=10)

    for t in results["tracks"]:
        if t.isrc == isrc:
            return t
    print(f"no exact match for query: '{query}'")
    return None

def add_tracks_in_batches(playlist, tracks, batch_size=100):
    track_ids = [t.id for t in tracks]  # extract Tidal track IDs

    for i in range(0, len(track_ids), batch_size):
        playlist.add(track_ids[i:i+batch_size])

def main():
    cfg = load_config("config.json")

    logging.basicConfig(filename=cfg["logging"]["file"],
                        level=logging.INFO,
                        format="%(message)s")

    sp = spotify_session(cfg["spotify"])
    tidal = tidal_session()

    for playlist_name in cfg["spotify"]["playlists"]:
        playlist_id = find_spotify_playlist_by_name(sp, playlist_name)
        if not playlist_id:
            logging.info(f"Spotify playlist not found: {playlist_name}")
            continue

        logging.info(f"Processing playlist: {playlist_name}")
        tracks = fetch_spotify_playlist_tracks(sp, playlist_id)
        tidal_playlist = ensure_tidal_playlist(tidal, playlist_name)

        tidal_tracks = []
        for track in tracks:
            found = search_tidal_by_name_and_artist(tidal, track)

            if found is not None:
                tidal_tracks.append(found)

        if tidal_tracks:
            add_tracks_in_batches(tidal_playlist, tidal_tracks)
            logging.info(f"Added {len(tidal_tracks)} tracks to '{playlist_name}'")

if __name__ == "__main__":
    main()
