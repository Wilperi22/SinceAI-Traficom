#!/usr/bin/env python3
"""Build Finnish evaluation boundaries for terrestrial.py from Natural Earth 1:10m data.

Inputs (public domain, https://www.naturalearthdata.com, GitHub nvkelso/natural-earth-vector):
  ne_10m_admin_0_boundary_lines_land.geojson, ne_10m_admin_0_countries.geojson

Outputs (geodata/):
  finland_boundaries.csv  lat, lon, type  - 'land border' points every ~1 km on the
                          FIN-RUS/NOR/SWE boundary lines, and 'territorial sea' points
                          (0.02 deg grid) of the sea area within 12 NM of the Finnish
                          and Aland coast, cut at the median line with neighbours and
                          3 NM short of the median toward Estonia (Gulf of Finland
                          high-seas corridor).
  land_mask.npz           boolean land raster (0.02 deg) used to split P.1546 paths into
                          Land / Cold-sea zones.

Approximation: distances are measured from the Natural Earth coastline, not from the
official straight baselines, so in the archipelago the true territorial sea reaches
further offshore than modelled. Replace with the Traficom / avoindata.suomi.fi
"Aluevesien rajat" dataset when available (terrestrial.py --border-file).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

EARTH_RADIUS_KM = 6371.0
NM_KM = 1.852
BBOX = (53.0, 71.5, 14.0, 36.0)   # lat_min, lat_max, lon_min, lon_max
STEP = 0.02
TS_BBOX = (59.0, 66.2, 18.5, 30.5)
NEIGHBOURS = ("SWE", "EST", "RUS", "NOR")


def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def rings(geometry):
    coords = geometry["coordinates"]
    polys = [coords] if geometry["type"] == "Polygon" else coords
    for poly in polys:
        for ring in poly:
            yield np.asarray(ring, dtype=float)  # (lon, lat)


def rasterize(all_rings, lat_c, lon_c) -> np.ndarray:
    """Even-odd scanline fill of polygon rings onto a lat/lon cell-centre grid."""
    mask = np.zeros((lat_c.size, lon_c.size), dtype=bool)
    lat0, step = lat_c[0], lat_c[1] - lat_c[0]
    for ring in all_rings:
        x1, y1 = ring[:-1, 0], ring[:-1, 1]
        x2, y2 = ring[1:, 0], ring[1:, 1]
        lo = np.ceil((np.minimum(y1, y2) - lat0) / step).astype(int)
        hi = np.floor((np.maximum(y1, y2) - lat0) / step).astype(int)
        lo, hi = np.clip(lo, 0, lat_c.size), np.clip(hi, -1, lat_c.size - 1)
        n = np.maximum(hi - lo + 1, 0)
        if n.sum() == 0:
            continue
        edge = np.repeat(np.arange(x1.size), n)
        row = lo[edge] + (np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n))
        y = lat_c[row]
        keep = (y1[edge] > y) != (y2[edge] > y)
        edge, row, y = edge[keep], row[keep], y[keep]
        x = x1[edge] + (y - y1[edge]) * (x2[edge] - x1[edge]) / (y2[edge] - y1[edge])
        col = np.searchsorted(lon_c, x)           # cells with centre >= crossing
        order = np.lexsort((col, row))
        row, col = row[order], col[order]
        for r in np.unique(row):
            cs = col[row == r]
            for a, b in zip(cs[0::2], cs[1::2]):
                mask[r, a:b] ^= True
    return mask


def densify(line: np.ndarray, step_km: float = 1.0) -> np.ndarray:
    out = []
    for (lon1, lat1), (lon2, lat2) in zip(line[:-1], line[1:]):
        n = max(1, int(np.ceil(haversine(lat1, lon1, lat2, lon2) / step_km)))
        t = np.linspace(0, 1, n, endpoint=False)
        out.append(np.column_stack([lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t]))
    out.append([[line[-1, 1], line[-1, 0]]])
    return np.vstack(out)


def coast_points(country_rings, bbox) -> np.ndarray:
    lat_min, lat_max, lon_min, lon_max = bbox
    pts = []
    for ring in country_rings:
        inside = (ring[:, 1] > lat_min - 1) & (ring[:, 1] < lat_max + 1) & \
                 (ring[:, 0] > lon_min - 2) & (ring[:, 0] < lon_max + 2)
        if inside.any():
            pts.append(densify(ring[inside], 2.0) if inside.sum() > 1 else ring[inside][:, ::-1])
    return np.vstack(pts) if pts else np.empty((0, 2))


def nearest_km(lat, lon, pts) -> np.ndarray:
    out = np.full(lat.size, np.inf)
    for chunk in np.array_split(np.arange(pts.shape[0]), max(1, pts.shape[0] // 2000)):
        d = haversine(lat[:, None], lon[:, None], pts[chunk, 0][None, :], pts[chunk, 1][None, :])
        out = np.minimum(out, d.min(axis=1))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ne-dir", type=Path, required=True, help="Directory with the Natural Earth GeoJSON files")
    parser.add_argument("--out-dir", type=Path, default=Path("geodata"))
    args = parser.parse_args()
    args.out_dir.mkdir(exist_ok=True)

    lines = json.loads((args.ne_dir / "ne_10m_admin_0_boundary_lines_land.geojson").read_text())
    countries = json.loads((args.ne_dir / "ne_10m_admin_0_countries.geojson").read_text())
    by_a3: dict[str, list[np.ndarray]] = {}
    for f in countries["features"]:
        by_a3.setdefault(f["properties"]["ADM0_A3"], []).extend(rings(f["geometry"]))

    # Land border.
    border = []
    for f in lines["features"]:
        p = f["properties"]
        if "FIN" in (p["ADM0_A3_L"], p["ADM0_A3_R"]):
            parts = f["geometry"]["coordinates"]
            for part in (parts if f["geometry"]["type"] == "MultiLineString" else [parts]):
                border.append(densify(np.asarray(part, dtype=float), 1.0))
    border = np.vstack(border)

    # Land mask (all countries clipped to the bounding box by the raster extent).
    lat_c = np.arange(BBOX[0] + STEP / 2, BBOX[1], STEP)
    lon_c = np.arange(BBOX[2] + STEP / 2, BBOX[3], STEP)
    regional = [r for rs in by_a3.values() for r in rs
                if r[:, 1].max() > BBOX[0] and r[:, 1].min() < BBOX[1]
                and r[:, 0].max() > BBOX[2] and r[:, 0].min() < BBOX[3]]
    land = rasterize(regional, lat_c, lon_c)
    np.savez_compressed(args.out_dir / "land_mask.npz", land=np.packbits(land, axis=1), shape=land.shape,
                        lat0=lat_c[0], lon0=lon_c[0], step=STEP)

    # Territorial sea: sea cells within 12 NM of the Finnish/Aland coast, nearer to
    # Finland than to any neighbour, and 3 NM short of the median toward Estonia.
    fin_coast = coast_points(by_a3["FIN"] + by_a3.get("ALD", []), TS_BBOX)
    foreign = {a3: coast_points(by_a3[a3], TS_BBOX) for a3 in NEIGHBOURS}
    ilat = (lat_c >= TS_BBOX[0]) & (lat_c <= TS_BBOX[1])
    ilon = (lon_c >= TS_BBOX[2]) & (lon_c <= TS_BBOX[3])
    glat, glon = np.meshgrid(lat_c[ilat], lon_c[ilon], indexing="ij")
    sea = ~land[np.ix_(ilat, ilon)]
    lat_s, lon_s = glat[sea], glon[sea]
    d_fin = nearest_km(lat_s, lon_s, fin_coast)
    near = d_fin <= 12 * NM_KM
    lat_s, lon_s, d_fin = lat_s[near], lon_s[near], d_fin[near]
    keep = np.ones(lat_s.size, dtype=bool)
    for a3, pts in foreign.items():
        if pts.size:
            d_other = nearest_km(lat_s, lon_s, pts)
            keep &= d_fin <= (d_other - 2 * 3 * NM_KM if a3 == "EST" else d_other)
    ts = np.column_stack([lat_s[keep], lon_s[keep]])

    with (args.out_dir / "finland_boundaries.csv").open("w", encoding="utf-8") as handle:
        handle.write("lat,lon,type\n")
        for lat, lon in border:
            handle.write(f"{lat:.5f},{lon:.5f},land border\n")
        for lat, lon in ts:
            handle.write(f"{lat:.4f},{lon:.4f},territorial sea\n")
    print(f"land border points: {len(border)}, territorial-sea points: {len(ts)}, "
          f"land mask {land.shape} ({land.mean():.0%} land)")


if __name__ == "__main__":
    main()
