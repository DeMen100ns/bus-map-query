#include "busmap/hpa.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <map>
#include <stdexcept>
#include <tuple>

// Start reading at HpaIndex::HpaIndex (preprocessing) and hpa() (one query).
// Both orchestrate the smaller steps defined in this file.

namespace busmap {
namespace {

constexpr double INFINITY_DISTANCE = std::numeric_limits<double>::infinity();
constexpr NodeId NO_PARENT = std::numeric_limits<NodeId>::max();
using Clock = std::chrono::steady_clock;
using QueueEntry = HpaSearchState::QueueEntry;

// Pop the smallest f first; break ties by g, then NodeId for reproducible paths.
struct QueueEntryGreater {
    bool operator()(const QueueEntry& left, const QueueEntry& right) const {
        return std::tie(left.priority, left.distance_from_source, left.node) >
               std::tie(right.priority, right.distance_from_source, right.node);
    }
};

void push_queue(HpaSearchState& search, double priority, double distance, NodeId node) {
    search.heap.push_back({priority, distance, node});
    std::push_heap(search.heap.begin(), search.heap.end(), QueueEntryGreater{});
}

QueueEntry pop_queue(HpaSearchState& search) {
    std::pop_heap(search.heap.begin(), search.heap.end(), QueueEntryGreater{});
    const auto entry = search.heap.back();
    search.heap.pop_back();
    return entry;
}

std::uint64_t elapsed_nanoseconds(Clock::time_point started) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - started).count();
}

struct CoordinateBounds {
    double min_x = INFINITY_DISTANCE;
    double min_y = INFINITY_DISTANCE;
    double max_x = -INFINITY_DISTANCE;
    double max_y = -INFINITY_DISTANCE;
};

CoordinateBounds find_coordinate_bounds(const Graph& graph) {
    CoordinateBounds bounds;
    for (std::size_t node = 0; node < graph.node_count(); ++node) {
        const auto& coordinate = graph.coordinate(static_cast<NodeId>(node));
        bounds.min_x = std::min(bounds.min_x, coordinate.x_m);
        bounds.min_y = std::min(bounds.min_y, coordinate.y_m);
        bounds.max_x = std::max(bounds.max_x, coordinate.x_m);
        bounds.max_y = std::max(bounds.max_y, coordinate.y_m);
    }
    return bounds;
}

std::int64_t cell_coordinate(double position, double origin, double cluster_size_m) {
    const long double cell = std::floor((static_cast<long double>(position) - origin) / cluster_size_m);
    if (!std::isfinite(cell) || cell < 0 || cell >= std::ldexp(1.0L, 63)) {
        throw std::invalid_argument("HPA cluster coordinate exceeds int64 range");
    }
    return static_cast<std::int64_t>(cell);
}

bool valid_query_vertices(const Graph& graph, const Query& query) {
    return query.source >= 0 && query.target >= 0 &&
           static_cast<std::uint64_t>(query.source) < graph.node_count() &&
           static_cast<std::uint64_t>(query.target) < graph.node_count();
}

} // namespace

void HpaOptions::validate() const {
    if (!std::isfinite(cluster_size_m) || cluster_size_m <= 0) {
        throw std::invalid_argument("HPA cluster size must be finite and positive");
    }
    if (!std::isfinite(heuristic_weight) || heuristic_weight <= 1) {
        throw std::invalid_argument("HPA heuristic weight must be finite and > 1");
    }
}

void HpaSearchState::reset(std::size_t node_count) {
    if (distance.size() != node_count) {
        distance.assign(node_count, INFINITY_DISTANCE);
        parent.resize(node_count);
        parent_edge_weight.resize(node_count);
        parent_is_shortcut.resize(node_count);
    } else {
        for (const auto node : touched_nodes) {
            distance[node] = INFINITY_DISTANCE;
        }
    }

    // Parents are overwritten when a node is reached and only read for reached nodes.
    touched_nodes.clear();
    heap.clear();
}

std::size_t HpaSearchState::bytes() const {
    return (distance.capacity() + parent_edge_weight.capacity()) * sizeof(double) +
           (parent.capacity() + touched_nodes.capacity()) * sizeof(NodeId) + parent_is_shortcut.capacity() +
           heap.capacity() * sizeof(QueueEntry);
}

std::size_t HpaWorkspace::bytes() const {
    return search.bytes() + heuristic.capacity() * sizeof(double) + abstract_path.capacity() * sizeof(NodeId);
}

// Preprocess once: partition nodes, find portals, then build shortcuts and their paths.
HpaIndex::HpaIndex(const Graph& graph, double cluster_size_m)
    : graph_(&graph), cluster_size_m_(cluster_size_m) {
    const auto started = Clock::now();
    HpaOptions{cluster_size_m, 1.05}.validate();

    const auto bounds = find_coordinate_bounds(graph);
    const auto cluster_members = partition_nodes(bounds.min_x, bounds.min_y);
    find_portals_and_heuristic_scale(std::hypot(bounds.max_x - bounds.min_x, bounds.max_y - bounds.min_y));
    build_shortcuts(cluster_members);
    finalize_storage_statistics();

    stats_.build_ms = std::chrono::duration<double, std::milli>(Clock::now() - started).count();
}

HpaIndex::ClusterMembers HpaIndex::partition_nodes(double origin_x, double origin_y) {
    using Cell = std::pair<std::int64_t, std::int64_t>;
    std::map<Cell, std::vector<NodeId>> cells;
    node_cluster_.resize(graph_->node_count());

    for (std::size_t node = 0; node < graph_->node_count(); ++node) {
        const auto node_id = static_cast<NodeId>(node);
        const auto& coordinate = graph_->coordinate(node_id);
        const Cell cell{cell_coordinate(coordinate.x_m, origin_x, cluster_size_m_),
                        cell_coordinate(coordinate.y_m, origin_y, cluster_size_m_)};
        cells[cell].push_back(node_id);
    }

    // The map visits cells in coordinate order, giving clusters stable IDs.
    ClusterMembers cluster_members;
    cluster_members.reserve(cells.size());
    for (auto& [cell, nodes] : cells) {
        const auto cluster_id = static_cast<std::uint32_t>(cluster_members.size());
        for (const auto node : nodes) {
            node_cluster_[node] = cluster_id;
        }
        stats_.max_cluster_nodes = std::max(stats_.max_cluster_nodes, nodes.size());
        cluster_members.push_back(std::move(nodes));
    }
    stats_.clusters = cluster_members.size();
    return cluster_members;
}

void HpaIndex::find_portals_and_heuristic_scale(double bounding_box_diagonal) {
    is_portal_.assign(graph_->node_count(), 0);
    double heuristic_scale = std::isfinite(bounding_box_diagonal) ? 1 : 0;

    for (std::size_t node = 0; node < graph_->node_count(); ++node) {
        const auto source = static_cast<NodeId>(node);
        const auto& source_coordinate = graph_->coordinate(source);
        for (const auto& edge : graph_->outgoing(source)) {
            // Mark both endpoints of a cross-cluster edge as portals; preserve its direction.
            if (node_cluster_[source] != node_cluster_[edge.target]) {
                is_portal_[source] = 1;
                is_portal_[edge.target] = 1;
                ++stats_.cross_edges;
            }

            // h = alpha * Euclidean. Every edge must cost at least alpha times its geometric length.
            const auto& target_coordinate = graph_->coordinate(edge.target);
            const double geometric_distance = std::hypot(source_coordinate.x_m - target_coordinate.x_m,
                                                         source_coordinate.y_m - target_coordinate.y_m);
            if (!std::isfinite(geometric_distance)) {
                heuristic_scale = 0;
            } else if (geometric_distance > 0) {
                heuristic_scale = std::min(heuristic_scale, edge.distance_m / geometric_distance);
            }
        }
    }

    // Round down conservatively to keep the heuristic a lower bound.
    stats_.heuristic_scale = heuristic_scale > 0 ? std::nextafter(heuristic_scale * (1 - 1e-12), 0.0) : 0;
}

void HpaIndex::build_shortcuts(const ClusterMembers& cluster_members) {
    std::vector<std::size_t> portal_counts(cluster_members.size(), 0);
    for (std::size_t node = 0; node < graph_->node_count(); ++node) {
        if (is_portal_[node]) {
            ++portal_counts[node_cluster_[node]];
            ++stats_.portals;
        }
    }
    for (const auto count : portal_counts) {
        stats_.max_cluster_portals = std::max(stats_.max_cluster_portals, count);
    }

    HpaSearchState search;
    std::vector<NodeId> reversed_path;
    shortcut_offsets_.resize(graph_->node_count() + 1);
    path_offsets_.push_back(0);

    // Visit sources in NodeId order to append directly to CSR without an intermediate copy.
    for (std::size_t node = 0; node < graph_->node_count(); ++node) {
        const auto source = static_cast<NodeId>(node);
        const auto source_cluster = node_cluster_[source];
        const auto first_shortcut = shortcuts_.size();

        if (is_portal_[source] && portal_counts[source_cluster] >= 2) {
            run_cluster_dijkstra(source, portal_counts[source_cluster] - 1, search);
            std::sort(shortcuts_.begin() + first_shortcut, shortcuts_.end(),
                      [](const Edge& left, const Edge& right) { return left.target < right.target; });
            store_shortcut_paths(source, first_shortcut, cluster_members[source_cluster].size(),
                                 search.parent, reversed_path);
        }
        shortcut_offsets_[node + 1] = shortcuts_.size();
    }
}

void HpaIndex::run_cluster_dijkstra(NodeId source, std::size_t remaining_portals, HpaSearchState& search) {
    const auto source_cluster = node_cluster_[source];
    search.reset(graph_->node_count());
    search.distance[source] = 0;
    search.parent[source] = NO_PARENT;
    search.touched_nodes.push_back(source);
    push_queue(search, 0, 0, source);

    while (!search.heap.empty()) {
        const auto entry = pop_queue(search);
        const auto current = entry.node;
        const double current_distance = entry.distance_from_source;
        if (current_distance != search.distance[current]) {
            continue;
        }

        // Settling a destination portal gives its exact within-cluster shortcut distance.
        if (current != source && is_portal_[current]) {
            shortcuts_.push_back({current, current_distance});
            if (--remaining_portals == 0) {
                break;
            }
        }

        for (const auto& edge : graph_->outgoing(current)) {
            if (node_cluster_[edge.target] != source_cluster) {
                continue;
            }
            const double candidate_distance = current_distance + edge.distance_m;
            if (candidate_distance < search.distance[edge.target]) {
                if (search.distance[edge.target] == INFINITY_DISTANCE) {
                    search.touched_nodes.push_back(edge.target);
                }
                search.distance[edge.target] = candidate_distance;
                search.parent[edge.target] = current;
                push_queue(search, candidate_distance, candidate_distance, edge.target);
            }
        }
    }
}

void HpaIndex::store_shortcut_paths(NodeId source, std::size_t first_shortcut, std::size_t cluster_node_count,
                                    const std::vector<NodeId>& parent, std::vector<NodeId>& reversed_path) {
    for (std::size_t shortcut_id = first_shortcut; shortcut_id < shortcuts_.size(); ++shortcut_id) {
        reversed_path.clear();
        for (auto node = shortcuts_[shortcut_id].target; node != source; node = parent[node]) {
            if (node == NO_PARENT || reversed_path.size() >= cluster_node_count) {
                throw std::logic_error("Invalid Dijkstra parent chain");
            }
            reversed_path.push_back(node);
        }

        // Reverse the parent chain into source-to-target order, excluding source.
        path_nodes_.insert(path_nodes_.end(), reversed_path.rbegin(), reversed_path.rend());
        path_offsets_.push_back(path_nodes_.size());
    }
}

void HpaIndex::finalize_storage_statistics() {
    shortcuts_.shrink_to_fit();
    path_offsets_.shrink_to_fit();
    path_nodes_.shrink_to_fit();

    stats_.shortcuts = shortcuts_.size();
    stats_.path_nodes = path_nodes_.size();
    stats_.path_storage_bytes =
        path_offsets_.capacity() * sizeof(std::size_t) + path_nodes_.capacity() * sizeof(NodeId);
    stats_.index_bytes = sizeof(*this) + node_cluster_.capacity() * sizeof(std::uint32_t) +
                         is_portal_.capacity() + shortcut_offsets_.capacity() * sizeof(std::size_t) +
                         shortcuts_.capacity() * sizeof(Edge) + stats_.path_storage_bytes;
}

std::span<const Edge> HpaIndex::shortcuts(NodeId source) const {
    const auto begin = shortcut_offsets_[source];
    const auto count = shortcut_offsets_[source + 1] - begin;
    return std::span<const Edge>(shortcuts_).subspan(begin, count);
}

double HpaIndex::heuristic(NodeId node, NodeId target) const {
    if (stats_.heuristic_scale == 0) {
        return 0;
    }
    const auto& node_coordinate = graph_->coordinate(node);
    const auto& target_coordinate = graph_->coordinate(target);
    return stats_.heuristic_scale * std::hypot(node_coordinate.x_m - target_coordinate.x_m,
                                               node_coordinate.y_m - target_coordinate.y_m);
}

long double HpaIndex::append_shortcut_path(NodeId source, NodeId target, std::vector<NodeId>& path) const {
    if (source >= graph_->node_count() || target >= graph_->node_count() || path.empty() ||
        path.back() != source) {
        throw std::invalid_argument("Invalid stored shortcut request");
    }

    const auto outgoing_shortcuts = shortcuts(source);
    const auto shortcut = std::lower_bound(outgoing_shortcuts.begin(), outgoing_shortcuts.end(), target,
                                           [](const Edge& edge, NodeId node) { return edge.target < node; });
    if (shortcut == outgoing_shortcuts.end() || shortcut->target != target) {
        throw std::invalid_argument("Shortcut does not exist");
    }

    const auto index_in_source_row = static_cast<std::size_t>(shortcut - outgoing_shortcuts.begin());
    const auto shortcut_id = shortcut_offsets_[source] + index_in_source_row;
    long double segment_distance = 0;
    for (std::size_t position = path_offsets_[shortcut_id]; position < path_offsets_[shortcut_id + 1];
         ++position) {
        const auto next_node = path_nodes_[position];
        const auto outgoing_edges = graph_->outgoing(path.back());
        const auto edge =
            std::find_if(outgoing_edges.begin(), outgoing_edges.end(),
                         [next_node](const Edge& candidate) { return candidate.target == next_node; });
        if (edge == outgoing_edges.end()) {
            throw std::logic_error("Stored path contains a missing edge");
        }
        // Sum actual edge weights to report the cost of the returned path.
        segment_distance += edge->distance_m;
        path.push_back(next_node);
    }

    const double tolerance = std::max(1e-6, 1e-9 * shortcut->distance_m);
    if (path.back() != target || std::abs(segment_distance - shortcut->distance_m) > tolerance) {
        throw std::logic_error("Stored path disagrees with shortcut distance");
    }
    return segment_distance;
}

namespace {

bool search_overlay(const Graph& graph, const HpaIndex& index, NodeId source, NodeId target,
                    double heuristic_weight, HpaWorkspace& workspace, HpaDiagnostics* diagnostics) {
    auto& search = workspace.search;
    auto& heuristic = workspace.heuristic;
    const auto source_cluster = index.cluster(source);
    const auto target_cluster = index.cluster(target);
    search.reset(graph.node_count());
    heuristic.resize(graph.node_count());
    heuristic[source] = index.heuristic(source, target);
    if (diagnostics) {
        ++diagnostics->heuristic_requests;
        ++diagnostics->heuristic_evaluations;
    }
    search.distance[source] = 0;
    search.parent[source] = NO_PARENT;
    search.touched_nodes.push_back(source);
    push_queue(search, heuristic_weight * heuristic[source], 0, source);

    while (!search.heap.empty()) {
        if (diagnostics) {
            diagnostics->peak_heap = std::max<std::uint64_t>(diagnostics->peak_heap, search.heap.size());
        }
        const auto entry = pop_queue(search);
        const auto current = entry.node;
        const double current_distance = entry.distance_from_source;

        // Discard stale entries before testing the target. Do not use a closed set:
        // Weighted A* must reopen a node when a better g is found.
        if (current_distance != search.distance[current]) {
            if (diagnostics) {
                ++diagnostics->stale_entries;
            }
            continue;
        }
        if (diagnostics) {
            ++diagnostics->overlay_expanded;
        }
        if (current == target) {
            return true;
        }

        auto relax_edge = [&](const Edge& edge, bool is_shortcut) {
            if (diagnostics) {
                ++diagnostics->edges_examined;
            }
            const double candidate_distance = current_distance + edge.distance_m;
            if (candidate_distance < search.distance[edge.target]) {
                if (diagnostics) {
                    ++diagnostics->heuristic_requests;
                }
                if (search.distance[edge.target] == INFINITY_DISTANCE) {
                    // Resetting distance also invalidates the old query's cached h.
                    heuristic[edge.target] = index.heuristic(edge.target, target);
                    search.touched_nodes.push_back(edge.target);
                    if (diagnostics) {
                        ++diagnostics->heuristic_evaluations;
                    }
                }
                search.distance[edge.target] = candidate_distance;
                search.parent[edge.target] = current;
                search.parent_edge_weight[edge.target] = edge.distance_m;
                search.parent_is_shortcut[edge.target] = is_shortcut;

                const double priority =
                    candidate_distance + heuristic_weight * heuristic[edge.target];
                push_queue(search, priority, candidate_distance, edge.target);
            }
        };

        const auto current_cluster = index.cluster(current);
        const bool use_original_edges =
            current_cluster == source_cluster || current_cluster == target_cluster;

        // Use original edges in the endpoint clusters; elsewhere keep only cross-cluster edges.
        // Allow leaving and re-entering even when source and target share a cluster.
        for (const auto& edge : graph.outgoing(current)) {
            if (use_original_edges || index.cluster(edge.target) != current_cluster) {
                relax_edge(edge, false);
            }
        }
        if (!use_original_edges) {
            for (const auto& shortcut : index.shortcuts(current)) {
                relax_edge(shortcut, true);
            }
        }
    }
    return false;
}

PathResult reconstruct_path(NodeId source, NodeId target, const HpaIndex& index, HpaWorkspace& workspace,
                            HpaDiagnostics* diagnostics) {
    const auto& search = workspace.search;
    auto& abstract_path = workspace.abstract_path;
    abstract_path.clear();
    for (auto node = target; node != source; node = search.parent[node]) {
        abstract_path.push_back(node);
    }
    abstract_path.push_back(source);
    std::reverse(abstract_path.begin(), abstract_path.end());

    std::vector<NodeId> path{source};
    long double total_distance = 0;
    for (std::size_t step = 1; step < abstract_path.size(); ++step) {
        const auto from = abstract_path[step - 1];
        const auto to = abstract_path[step];
        if (search.parent_is_shortcut[to]) {
            if (diagnostics) {
                ++diagnostics->shortcuts_used;
            }
            total_distance += index.append_shortcut_path(from, to, path);
        } else {
            path.push_back(to);
            total_distance += search.parent_edge_weight[to];
        }
    }
    return {PathStatus::Found, static_cast<double>(total_distance), std::move(path)};
}

} // namespace

// Each query runs Weighted A* on the overlay, then expands stored shortcut paths.
PathResult hpa(const Graph& graph, const Query& query, const HpaIndex& index, HpaWorkspace& workspace,
               double heuristic_weight, HpaDiagnostics* diagnostics) {
    if (&graph != &index.graph()) {
        throw std::invalid_argument("HPA index belongs to another graph instance");
    }
    if (!std::isfinite(heuristic_weight) || heuristic_weight <= 1) {
        throw std::invalid_argument("Invalid HPA weight");
    }
    if (diagnostics) {
        *diagnostics = {};
    }
    if (!valid_query_vertices(graph, query)) {
        return {PathStatus::InvalidVertex, std::nullopt, {}};
    }

    const auto source = static_cast<NodeId>(query.source);
    const auto target = static_cast<NodeId>(query.target);
    if (source == target) {
        return {PathStatus::Found, 0, {source}};
    }

    auto started = diagnostics ? Clock::now() : Clock::time_point{};
    const bool found =
        search_overlay(graph, index, source, target, heuristic_weight, workspace, diagnostics);
    if (diagnostics) {
        diagnostics->search_ns = elapsed_nanoseconds(started);
        started = Clock::now();
    }
    if (!found) {
        return {PathStatus::Unreachable, std::nullopt, {}};
    }

    auto result = reconstruct_path(source, target, index, workspace, diagnostics);
    if (diagnostics) {
        diagnostics->reconstruction_ns = elapsed_nanoseconds(started);
    }
    return result;
}

} // namespace busmap
