#include "busmap/dijkstra_baseline.hpp"

#include <algorithm>
#include <functional>
#include <limits>
#include <queue>
#include <utility>
#include <vector>

namespace busmap {
PathResult dijkstra_baseline(const Graph& graph, const Query& query) {
    const auto node_count = graph.node_count();
    if (query.source < 0 || query.target < 0 ||
        static_cast<std::uint64_t>(query.source) >= node_count ||
        static_cast<std::uint64_t>(query.target) >= node_count) {
        return {PathStatus::InvalidVertex, std::nullopt, {}};
    }

    constexpr double INF = std::numeric_limits<double>::infinity();
    constexpr NodeId NO_PARENT = std::numeric_limits<NodeId>::max();
    using QueueEntry = std::pair<double, NodeId>;
    // All search resources belong to this call; no workspace survives a query.
    std::vector<double> distance(node_count, INF);
    std::vector<NodeId> parent(node_count, NO_PARENT);
    std::priority_queue<QueueEntry, std::vector<QueueEntry>, std::greater<QueueEntry>> heap;
    const auto source = static_cast<NodeId>(query.source);
    const auto target = static_cast<NodeId>(query.target);

    distance[source] = 0;
    heap.emplace(0, source);
    while (!heap.empty()) {
        const auto [cost, current] = heap.top();
        heap.pop();
        if (cost != distance[current]) continue;
        if (current == target) break;

        for (const auto& edge : graph.outgoing(current)) {
            const double candidate = cost + edge.distance_m;
            if (candidate >= distance[edge.target]) continue;
            distance[edge.target] = candidate;
            parent[edge.target] = current;
            heap.emplace(candidate, edge.target);
        }
    }

    if (distance[target] == INF) return {PathStatus::Unreachable, std::nullopt, {}};
    std::vector<NodeId> path{target};
    for (NodeId node = target; node != source;) {
        node = parent[node];
        path.push_back(node);
    }
    std::reverse(path.begin(), path.end());
    return {PathStatus::Found, distance[target], std::move(path)};
}
} // namespace busmap
