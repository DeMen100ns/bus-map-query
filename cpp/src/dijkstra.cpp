#include "busmap/dijkstra.hpp"

#include <algorithm>
#include <functional>
#include <limits>

namespace busmap {
namespace {
constexpr double INF = std::numeric_limits<double>::infinity();
constexpr NodeId NO_PARENT = std::numeric_limits<NodeId>::max();

void prepare_workspace(SearchWorkspace& workspace, std::size_t node_count) {
    if (workspace.distance.size() != node_count || workspace.parent.size() != node_count) {
        workspace.distance.assign(node_count, INF);
        workspace.parent.assign(node_count, NO_PARENT);
    } else {
        for (NodeId node : workspace.touched) {
            workspace.distance[node] = INF;
        }
    }
    workspace.touched.clear();
    // Early exit at the target may leave entries from the previous query.
    workspace.dijkstra_heap.clear();
}
} // namespace

PathResult dijkstra(const Graph& graph, const Query& query, SearchWorkspace& workspace) {
    const auto node_count = graph.node_count();
    if (query.source < 0 || query.target < 0 ||
        static_cast<std::uint64_t>(query.source) >= node_count ||
        static_cast<std::uint64_t>(query.target) >= node_count) {
        return {PathStatus::InvalidVertex, std::nullopt, {}};
    }

    prepare_workspace(workspace, node_count);
    auto& distance = workspace.distance;
    auto& parent = workspace.parent;
    auto& heap = workspace.dijkstra_heap;
    const auto source = static_cast<NodeId>(query.source);
    const auto target = static_cast<NodeId>(query.target);
    const auto compare = std::greater<SearchWorkspace::DijkstraQueueEntry>{};
    auto push = [&](double cost, NodeId node) {
        heap.emplace_back(cost, node);
        std::push_heap(heap.begin(), heap.end(), compare);
    };

    distance[source] = 0;
    parent[source] = NO_PARENT;
    workspace.touched.push_back(source);
    push(0, source);
    while (!heap.empty()) {
        std::pop_heap(heap.begin(), heap.end(), compare);
        const auto [cost, current] = heap.back();
        heap.pop_back();
        if (cost != distance[current]) continue;
        if (current == target) break;

        for (const auto& edge : graph.outgoing(current)) {
            const double candidate = cost + edge.distance_m;
            if (candidate >= distance[edge.target]) continue;
            if (distance[edge.target] == INF) workspace.touched.push_back(edge.target);
            distance[edge.target] = candidate;
            // Every discovered vertex gets a new parent, so old parents need no reset.
            parent[edge.target] = current;
            push(candidate, edge.target);
        }
    }

    if (distance[target] == INF) return {PathStatus::Unreachable, std::nullopt, {}};
    // Results own their paths; subsequent workspace reuse cannot modify them.
    std::vector<NodeId> path{target};
    for (NodeId node = target; node != source;) {
        node = parent[node];
        path.push_back(node);
    }
    std::reverse(path.begin(), path.end());
    return {PathStatus::Found, distance[target], std::move(path)};
}
} // namespace busmap
