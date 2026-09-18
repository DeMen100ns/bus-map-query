#include "busmap/astar.hpp"
#include <queue>
#include <algorithm>
#include <functional>
#include <limits>
#include <queue>
#include <utility>
#include <vector>
#include <cmath>
#include <tuple>

namespace busmap
{
    using QueueEntry = std::tuple<double, double, NodeId>;
    const double INF = std::numeric_limits<double>::infinity();
    const NodeId NO_PARENT = std::numeric_limits<NodeId>::max();

    namespace
    {
        bool is_valid_query(const Graph &graph, const Query &query)
        {
            std::size_t node_count = graph.node_count();
            return (0 <= query.source && static_cast<std::uint64_t>(query.source) < node_count && 0 <= query.target && static_cast<std::uint64_t>(query.target) < node_count);
        }

        double manhattan_distance(const Coordinate &source, const Coordinate &target)
        {
            return hypot(source.x_m - target.x_m, source.y_m - target.y_m);
        }

        double H(const Graph &graph, const NodeId &source, const NodeId &target)
        {
            return manhattan_distance(graph.coordinate(source), graph.coordinate(target));
        }
    }

    PathResult astar(const Graph &graph, const Query &query, [[maybe_unused]] SearchWorkspace &workspace)
    {
        if (!is_valid_query(graph, query))
        { // Invalid vertex
            return {PathStatus::InvalidVertex, std::nullopt, {}};
        }

        const std::size_t node_count = graph.node_count();
        const auto &source = static_cast<NodeId>(query.source);
        const auto &target = static_cast<NodeId>(query.target);

        auto &distance = workspace.distance;
        auto &parent = workspace.parent;
        auto &touched = workspace.touched;

        if (distance.size() != node_count ||
            parent.size() != node_count)
        {
            distance.assign(node_count, INF);
            parent.assign(node_count, NO_PARENT);
        }
        else
        {
            for (NodeId u : touched)
            {
                distance[u] = INF;
                parent[u] = NO_PARENT;
            }
        }
        touched.clear();

        std::priority_queue<QueueEntry, std::vector<QueueEntry>, std::greater<QueueEntry>> queue;
        touched.push_back(source);
        distance[source] = 0;
        queue.push({H(graph, source, target), distance[source], source});

        while (!queue.empty())
        {
            const auto [priority_value, distance_to_cur_node, cur_node] = queue.top();
            queue.pop();

            if (cur_node == target)
                break;

            if (distance_to_cur_node != distance[cur_node])
                continue;

            for (const auto &edge : graph.outgoing(cur_node))
            {
                if (distance[edge.target] > distance_to_cur_node + edge.distance_m)
                {
                    if (distance[edge.target] == INF)
                    {
                        touched.push_back(edge.target);
                    }

                    distance[edge.target] = distance_to_cur_node + edge.distance_m;
                    parent[edge.target] = cur_node;

                    queue.push({distance[edge.target] + H(graph, edge.target, target), distance[edge.target], edge.target});
                }
            }
        }

        if (distance[query.target] == INF)
            return {PathStatus::Unreachable, std::nullopt, {}};

        std::vector<NodeId> path = {static_cast<NodeId>(target)};
        NodeId trace_node = target;
        while (trace_node != source)
        {
            trace_node = parent[trace_node];
            path.push_back(trace_node);
        }
        std::reverse(path.begin(), path.end());

        return {PathStatus::Found, distance[query.target], std::move(path)};
    }
}
