# Storyboard music catalog

Soft instrumental beds mixed under dialogue when cooking a storyboard video.

## Source

Use the [YouTube Audio Library](https://www.youtube.com/audiolibrary) (YouTube Studio → Audio library).

Filters that work well for Easy English / family stories:

- **Attribution:** not required (preferred)
- **Genre:** acoustic, ambient, children’s, cinematic (soft)
- **Mood:** calm, happy, inspiring, peaceful (map into our tags below)

Download ~12–20 instrumental MP3s. No live scraping — this folder is the catalog.

## Mood tags

| Tag | When we pick it |
|-----|-----------------|
| `warm` | Default family / home / cozy (main bed for most stories) |
| `playful` | Fun, jokes, games |
| `tension` | Worry, mistakes, trouble |
| `sad` | Tears, sorry, lonely |
| `resolve` | Lesson learned, hug, forgive |

Edit `moods` on each track in `catalog.json` after registering — filenames alone are a weak signal.

## Add tracks

1. Download MP3s from the Audio Library.
2. Drop them into `assets/music/inbox/`.
3. Register:

```bash
cd videofactory
python3 scripts/register_music_tracks.py
# optional: propose moods with LLM
python3 scripts/register_music_tracks.py --suggest-moods
```

Files are copied to `assets/music/tracks/` and listed in `catalog.json`.

## Cook behavior

1. Infer a **main mood** for the whole story (title + beat sheet).
2. Score each scene’s dialogue for a local mood. Strong signals (worry, joke, tears…) can leave the main bed; short digressions stay on main so we don’t thrash.
3. Collapse consecutive same-mood scenes into segments; **crossfade** (~1.4s) between beds, then return to the main track.
4. Mix the finished bed quietly under dialogue (~10% volume).
5. If the catalog is empty, cooking skips music and still succeeds.

## Attribution

If a track requires credit, put the credit string in `catalog.json` → `attribution`. We can later append it to the YouTube description kit.
