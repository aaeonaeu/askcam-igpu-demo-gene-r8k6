# Demo video sources

The demo cameras loop three short clips. The clips are **not stored in git**: `scripts/fetch-media.sh`
downloads the originals from Wikimedia Commons and re-encodes them once to `media/cam1.mp4`..`cam3.mp4`
(H.264, 1920x1080, 15 fps, keyframe every 2 s, no audio). All three are fixed-camera footage.

| Camera | Scene | Original file (Wikimedia Commons) | Author | License |
|---|---|---|---|---|
| cam1 | Street crossing with shops (people, cars, buses) | [DiagonalCrosswalkYongeDundas.webm](https://commons.wikimedia.org/wiki/File:DiagonalCrosswalkYongeDundas.webm) (Toronto, 2011-08-18, 57 s) | Raysonho @ Open Grid Scheduler / Grid Engine | [CC0 1.0](http://creativecommons.org/publicdomain/zero/1.0/deed.en) |
| cam2 | Warehouse floor, overhead, workers in hi-vis vests | [20200716-OSEC-LSC-1159-CLIP-B-HighRes.webm](https://commons.wikimedia.org/wiki/File:20200716-OSEC-LSC-1159-CLIP-B-HighRes.webm) (2020-07-17, 23 s) | USDAgov (U.S. Dept. of Agriculture) | Public domain (U.S. Government work) |
| cam3 | Road junction, vehicles passing (loading-bay / gate stand-in) | [Gau-Bischofsheim - Eine Minute an der L 415.webm](https://commons.wikimedia.org/wiki/File:Gau-Bischofsheim_-_Eine_Minute_an_der_L_415.webm) (2025-12-03, 61 s) | ManuelB701 | [CC0 1.0](http://creativecommons.org/publicdomain/zero/1.0/deed.en) |

Notes
- CC0 and U.S. Government public-domain works need no attribution; it is listed here anyway.
- cam2 shows real, identifiable workers (and, in the full source clip, a government visit). It is public domain,
  but check it is acceptable for your audience before showing it at an event.
- The clips are short, so the loop point is visible as a jump. That is expected for a demo.
- Wikimedia rate-limits anonymous downloads (HTTP 429). The fetch script sends a descriptive User-Agent and backs off.
