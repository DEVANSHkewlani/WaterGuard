"""Topology-based zones. No sensor values or demo fault truth are used."""
from collections import deque


def zones_for(wn, requested_count=None):
    node_ids = sorted(name for name, _ in wn.nodes())
    if not node_ids:
        raise ValueError("Network has no nodes")

    count = requested_count or (3 if len(node_ids) < 30 else 6)
    count = min(count, len(node_ids))

    adjacency = {node_id: set() for node_id in node_ids}
    for _, link in wn.links():
        a, b = link.start_node_name, link.end_node_name
        adjacency[a].add(b)
        adjacency[b].add(a)

    def distance_from(starts):
        distance = {name: 0 for name in starts}
        queue = deque(starts)
        while queue:
            current = queue.popleft()
            for neighbor in sorted(adjacency[current]):
                if neighbor not in distance:
                    distance[neighbor] = distance[current] + 1
                    queue.append(neighbor)
        return distance

    reservoirs = sorted(wn.reservoir_name_list)
    seeds = [reservoirs[0] if reservoirs else node_ids[0]]

    # Spread zone seeds across the actual graph.
    while len(seeds) < count:
        distances = distance_from(seeds)
        remaining = (n for n in node_ids if n not in seeds)
        seeds.append(
            max(remaining, key=lambda n: (distances.get(n, 10**9), n))
        )

    # Simultaneous BFS: each assigned node is reached through its zone.
    owner = {seed: f"Z{i + 1}" for i, seed in enumerate(seeds)}
    queue = deque(seeds)
    while queue:
        current = queue.popleft()
        for neighbor in sorted(adjacency[current]):
            if neighbor not in owner:
                owner[neighbor] = owner[current]
                queue.append(neighbor)

    if len(owner) != len(node_ids):
        raise ValueError("Disconnected component has no zone seed")

    zones = {
        f"Z{i + 1}": {
            "id": f"Z{i + 1}",
            "name": f"Zone {i + 1}",
            "node_ids": [],
            "junction_ids": [],
            "pipe_ids": [],
            "neighbor_ids": set(),
            "boundary_node_ids": set(),
            "boundary_link_ids": set(),
        }
        for i in range(len(seeds))
    }

    junctions = set(wn.junction_name_list)
    for node_id, zone_id in owner.items():
        zones[zone_id]["node_ids"].append(node_id)
        if node_id in junctions:
            zones[zone_id]["junction_ids"].append(node_id)

    for link_id, link in wn.links():
        a, b = link.start_node_name, link.end_node_name
        za, zb = owner[a], owner[b]

        if link_id in wn.pipe_name_list:
            # A boundary pipe has one primary search zone.
            zones[za]["pipe_ids"].append(link_id)

        if za != zb:
            for zone_id, other_id in ((za, zb), (zb, za)):
                zones[zone_id]["neighbor_ids"].add(other_id)
                zones[zone_id]["boundary_node_ids"].update((a, b))
                zones[zone_id]["boundary_link_ids"].add(link_id)

    return [
        {
            key: sorted(value) if isinstance(value, set) else value
            for key, value in zone.items()
        }
        for zone in zones.values()
    ]


def zone_for_candidate(zones, candidate):
    location = candidate["location_id"]
    field = (
        "junction_ids"
        if candidate["fault_type"] == "demand_surge"
        else "pipe_ids"
    )
    for zone in zones:
        if location in zone[field]:
            return zone["id"]
    raise ValueError(f"No zone for candidate {candidate}")