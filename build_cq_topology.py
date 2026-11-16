"""Derive the Central Queensland 275 kV topology and line lengths from OpenStreetMap.

OpenStreetMap (OSM, (c) OpenStreetMap contributors, ODbL) maps Powerlink's
275 kV lines as chains of tower-to-tower segments. This script:

1. downloads (once) the 275 kV lines and substations in the study area via the
   Overpass API and keeps the raw extract in inputs/cq_network/osm_cq_275kV.json;
2. builds a graph of line segments (haversine lengths) and marks the tower
   nodes within a radius of each study substation as that substation's portals;
3. from each substation, walks outward and records every OTHER substation
   reached without passing through a third one = the direct connections,
   with their route length;
4. writes inputs/cq_network/osm_topology.csv for review.

Circuit counts are not reliable in OSM, so they come from Powerlink's TAPR
grid-section definitions (Appendix G) and Chapter 6, recorded in
inputs/cq_network/branches.csv. This script only supplies verified lengths and
confirms which substations are directly connected.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request

import networkx as nx
import pandas as pd

from studylib.config import INPUTS


NETWORK_DIR = INPUTS / "cq_network"
EXTRACT = NETWORK_DIR / "osm_cq_275kV.json"
BBOX = (-25.6, 147.5, -21.0, 152.6)
PORTAL_RADIUS_KM = 1.5
# Study bus -> OSM substation name (as tagged in OSM).
OSM_NAMES = {
    "STAN": "Stanwell",
    "BOUL": "Bouldercombe Substation",
    "RAGL": "Raglan Substation",
    "LARC": "Larcom Creek Transmission Substation",
    "CALR": "Calliope River Substation",
    "GLPS": "Gladstone Substation",
    "WURD": "Wurdong Substation",
    "CALV": "Calvale Substation",
    "BROD": "Broadsound Substation",
    "NEBO": "Nebo Substation",
    "LILY": "Lilyvale Substation",
    "GING": "Gin Gin Substation",
}


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def download() -> dict:
    if EXTRACT.exists():
        return json.loads(EXTRACT.read_text(encoding="utf-8"))
    s, w, n, e = BBOX
    query = (f'[out:json][timeout:180];(way["power"="line"]["voltage"~"275000"]({s},{w},{n},{e});'
             f'way["power"="substation"]({s},{w},{n},{e});node["power"="substation"]({s},{w},{n},{e}););out body geom;')
    request = urllib.request.Request("https://overpass-api.de/api/interpreter",
                                     data=urllib.parse.urlencode({"data": query}).encode(),
                                     headers={"User-Agent": "regional-equivalents-study/1.0"})
    with urllib.request.urlopen(request, timeout=300) as response:
        data = json.loads(response.read().decode("utf-8"))
    NETWORK_DIR.mkdir(parents=True, exist_ok=True)
    EXTRACT.write_text(json.dumps(data), encoding="utf-8")
    return data


def substation_centres(elements) -> dict[str, tuple[float, float]]:
    centres = {}
    for element in elements:
        tags = element.get("tags", {})
        if tags.get("power") != "substation":
            continue
        name = tags.get("name")
        if element["type"] == "node":
            point = (element["lat"], element["lon"])
        else:
            geometry = element.get("geometry", [])
            if not geometry:
                continue
            point = (sum(g["lat"] for g in geometry) / len(geometry), sum(g["lon"] for g in geometry) / len(geometry))
        centres.setdefault(name, point)
    missing = [bus for bus, name in OSM_NAMES.items() if name not in centres]
    if missing:
        raise KeyError(f"Substations not found in OSM extract: {missing}")
    return {bus: centres[name] for bus, name in OSM_NAMES.items()}


def build_graph(elements) -> nx.Graph:
    graph = nx.Graph()
    for element in elements:
        if element.get("tags", {}).get("power") != "line":
            continue
        nodes, geometry = element["nodes"], element["geometry"]
        for (a, ga), (b, gb) in zip(zip(nodes, geometry), zip(nodes[1:], geometry[1:])):
            graph.add_node(a, lat=ga["lat"], lon=ga["lon"])
            graph.add_node(b, lat=gb["lat"], lon=gb["lon"])
            graph.add_edge(a, b, km=haversine_km(ga["lat"], ga["lon"], gb["lat"], gb["lon"]),
                           name=element["tags"].get("name", ""), circuits=element["tags"].get("circuits", ""))
    return graph


def direct_connections(graph: nx.Graph, centres) -> pd.DataFrame:
    # Each tower node within the radius belongs to its NEAREST study substation.
    owner = {}
    for n, d in graph.nodes(data=True):
        nearest = min(centres, key=lambda bus: haversine_km(d["lat"], d["lon"], *centres[bus]))
        if haversine_km(d["lat"], d["lon"], *centres[nearest]) <= PORTAL_RADIUS_KM:
            owner[n] = nearest
    portals = {bus: {n for n, b in owner.items() if b == bus} for bus in centres}
    rows = []
    for bus, start in portals.items():
        if not start:
            continue
        # Dijkstra from this substation; do not expand through other substations' portals.
        blocked = graph.subgraph([n for n in graph.nodes if owner.get(n) in (None, bus)]).copy()
        reach = {}
        dist, paths = nx.multi_source_dijkstra(blocked, start, weight="km")
        for node, km in dist.items():
            for neighbour in graph.neighbors(node):
                other = owner.get(neighbour)
                if other and other != bus:
                    total = km + graph[node][neighbour]["km"]
                    if total < reach.get(other, (float("inf"), None))[0]:
                        reach[other] = (total, paths[node] + [neighbour])
        for other, (km, path) in reach.items():
            if bus < other:
                tags = {}
                for a, b in zip(path, path[1:]):
                    edge = graph[a][b]
                    key = (edge["name"] or "unnamed", edge["circuits"] or "?")
                    tags[key] = tags.get(key, 0.0) + edge["km"]
                main = sorted(tags.items(), key=lambda item: -item[1])[:2]
                rows.append({"from_bus": bus, "to_bus": other, "route_length_km": round(km, 1),
                             "osm_tags_along_route": "; ".join(f"{n} (circuits {c}, {k:.0f} km)" for (n, c), k in main)})
    frame = pd.DataFrame(rows).drop_duplicates(["from_bus", "to_bus"])
    return frame.sort_values(["from_bus", "to_bus"]).reset_index(drop=True)


def main() -> None:
    data = download()
    elements = data["elements"]
    centres = substation_centres(elements)
    graph = build_graph(elements)
    connections = direct_connections(graph, centres)
    connections.to_csv(NETWORK_DIR / "osm_topology.csv", index=False)
    print(f"{graph.number_of_edges()} line segments, {len(connections)} direct substation connections")
    print(connections.to_string(index=False))


if __name__ == "__main__":
    main()
