"""Build omarchy/plugin/land.js (the HUD globe's coastlines) from Natural Earth 1:110m land.

Natural Earth is public domain (https://www.naturalearthdata.com/about/terms-of-use/).
Source: https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_110m_land.geojson
  sha256 9e0729ee253ca7d7a5c4ae9395fb1902264c5377c52e224d13dd85010e2835d9

Usage: python -I scripts/make-land-data.py ne_110m_land.geojson omarchy/plugin/land.js
Coordinates are rounded to 0.5° and consecutive duplicates dropped (~25 KB, plenty for a 300 px globe).
"""

import hashlib
import json
import sys

EXPECTED = "9e0729ee253ca7d7a5c4ae9395fb1902264c5377c52e224d13dd85010e2835d9"


def main(src: str, dst: str) -> None:
    raw = open(src, "rb").read()
    if hashlib.sha256(raw).hexdigest() != EXPECTED:
        sys.exit("unexpected source file (sha256 mismatch)")
    data = json.loads(raw)
    rings = []
    for feature in data["features"]:
        geom = feature["geometry"]
        polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        for poly in polys:
            ring = []
            for lon, lat in poly[0]:
                pt = [round(lon * 2) / 2, round(lat * 2) / 2]
                if not ring or ring[-1] != pt:
                    ring.append(pt)
            if len(ring) >= 4:
                rings.append([v for p in ring for v in p])   # flat [lon, lat, lon, lat, …]
    body = json.dumps(rings, separators=(",", ":")).replace(".0,", ",").replace(".0]", "]")
    with open(dst, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(".pragma library\n// Natural Earth 1:110m land (public domain), simplified to 0.5 deg by "
                 "scripts/make-land-data.py.\n// Each ring is a flat [lon, lat, lon, lat, ...] array.\n")
        fh.write("var rings = " + body + ";\n")
    print(f"{len(rings)} rings, {sum(len(r) // 2 for r in rings)} points -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
