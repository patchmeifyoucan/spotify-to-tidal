# spotify-to-tidal

Uses [uv](https://docs.astral.sh/uv/getting-started/installation/) as dependency manager.
Install it, then from root, run:


```
uv venv --python=3.12
uv sync
source .venv/bin/activate
```

Then, edit credentials and playlists in [config.json](config.json).

Sync with ``python main.py``. Interactive sync (`auto`) is disabled by default.
Optionally, set ``prefix`` for the Tidal playlist.
Writes progress and missing tracks into [state.json](state.json).
When syncing an existing playlist for the first time, you may create the
state manually.
```
{
    "Playlist 1": {
        "idx": 5,
        "missing": []
    },
    "Playlist 2": {
        "idx": 25,
        "missing": []
    },
    ...    
}
```

`idx` is the position of the last synced track in the Spotify playlist.
