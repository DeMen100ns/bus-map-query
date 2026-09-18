"""Independent stdlib Dijkstra oracle. Not called by the C++ application."""
import heapq
import math


def adjacency(document):
    result = [[] for _ in document["nodes"]]
    for edge in document["edges"]:
        result[edge["source"]].append((edge["target"], edge["distance_m"]))
    return result


def shortest_paths(graph, source):
    if not 0 <= source < len(graph):
        raise ValueError("Invalid source")
    distances = [math.inf] * len(graph)
    predecessors = [-1] * len(graph)
    distances[source] = 0.0
    queue = [(0.0, source)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        for target, weight in graph[node]:
            candidate = distance + weight
            if candidate < distances[target]:
                distances[target] = candidate
                predecessors[target] = node
                heapq.heappush(queue, (candidate, target))
    return distances, predecessors


def reconstruct(source, target, distances, predecessors):
    if not math.isfinite(distances[target]):
        return []
    path = [target]
    while path[-1] != source:
        path.append(predecessors[path[-1]])
    return path[::-1]
