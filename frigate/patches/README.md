# Frigate 0.18.2 patch: embeddings memory leak

`compose.yml` mounts two patched files read-only over the pinned Frigate 0.18.2 image:

| File here | Replaces in the image |
|---|---|
| `maintainer.py` | `/opt/frigate/frigate/embeddings/maintainer.py` |
| `object_descriptions.py` | `/opt/frigate/frigate/data_processing/post/object_descriptions.py` |

Both are full copies of the Frigate 0.18.2 files (MIT licence, see `LICENSE.frigate`) with the changes
marked `askcam patch`. `frigate-0.18.2-embeddings-leak.patch` is the same change as a unified diff.

**They match Frigate 0.18.2 only.** If you change `FRIGATE_IMAGE` to another version, remove the two
mount lines from `compose.yml` and check whether that version still needs the fix.

## The problem

With GenAI object descriptions on, `frigate.embeddings_manager` grew about 0.1 GiB per minute until the
container's 5 GB limit killed it (about every 21 minutes).

1. `EmbeddingMaintainer.run()` waits without a timeout for the next detection frame message, so it
   loops once per detection frame: 12 times per second with 3 cameras at 4 fps.
2. It read one object update per loop. The demo clips produce about 34 updates per second, so the
   queue fell further and further behind (84 s after 2 minutes).
3. "Object ended" messages come on a separate queue that is fully drained every loop, so an object's
   cleanup ran long before its queued updates were read.
4. Each late update re-created the object's thumbnail list in `ObjectDescriptionProcessor.tracked_events`
   (up to 10 JPEGs of about 176 kB). Nothing deleted it again.

## The change

- `maintainer.py`: `_process_updates()` reads all pending updates per loop (capped at 200), and runs once
  more just before `_process_finalized()`.
- `object_descriptions.py`: `cleanup_event()` remembers the last 2048 ended event ids, and
  `__handle_frame_update()` ignores updates for them.

## Checking it

```bash
docker exec askcam-frigate grep -c "askcam patch" /opt/frigate/frigate/embeddings/maintainer.py
```

```bash
python3 scripts/soak.py 30 --tag leakcheck
```

The `emb_rss_gib` column of `logs/step6/soak-leakcheck.csv` must stay flat.
