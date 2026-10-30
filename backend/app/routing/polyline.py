"""Decoder for Google encoded polylines. Valhalla uses precision 6."""

from __future__ import annotations


def decode(encoded: str, precision: int = 6) -> list[tuple[float, float]]:
    """Return [(lon, lat), ...] (GeoJSON order) from an encoded polyline."""
    factor = 10**precision
    coords: list[tuple[float, float]] = []
    index = lat = lon = 0
    while index < len(encoded):
        deltas = []
        for _ in range(2):
            result = shift = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        coords.append((lon / factor, lat / factor))
    return coords
