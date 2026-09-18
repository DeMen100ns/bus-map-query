#include "busmap/bihpa.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <tuple>

// Start reading at BiHpaIndex::BiHpaIndex (setup) and bihpa() (one query).
namespace busmap {
namespace {

constexpr double INFINITY_DISTANCE = std::numeric_limits<double>::infinity();
constexpr NodeId NO_NODE = std::numeric_limits<NodeId>::max();
using Clock = std::chrono::steady_clock;
using QueueEntry = HpaSearchState::QueueEntry;

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

void discard_stale_entries(HpaSearchState& search, BiHpaDiagnostics* diagnostics) {
    while (!search.heap.empty() &&
           search.heap.front().distance_from_source != search.distance[search.heap.front().node]) {
        pop_queue(search);
        if (diagnostics) {
            ++diagnostics->stale_entries;
        }
    }
}

template <class Outgoing>
void build_incoming(std::size_t node_count, Outgoing outgoing, std::vector<std::size_t>& offsets,
                    std::vector<Edge>& edges) {
    offsets.assign(node_count + 1, 0);
    for (std::size_t source = 0; source < node_count; ++source) {
        for (const auto& edge : outgoing(static_cast<NodeId>(source))) {
            ++offsets[edge.target + 1];
        }
    }
    std::partial_sum(offsets.begin(), offsets.end(), offsets.begin());
    edges.resize(offsets.back());
    auto cursor = offsets;
    for (std::size_t source = 0; source < node_count; ++source) {
        for (const auto& edge : outgoing(static_cast<NodeId>(source))) {
            edges[cursor[edge.target]++] = {static_cast<NodeId>(source), edge.distance_m};
        }
    }
}

std::uint64_t elapsed_nanoseconds(Clock::time_point started) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - started).count();
}

// All mutable query data lives in the supplied workspace. This small object only
// coordinates the two searches; its methods do not allocate per-node storage.
class OverlaySearch {
  public:
    OverlaySearch(const BiHpaIndex& index, BiHpaWorkspace& workspace, NodeId source, NodeId target,
                  double weight, BiHpaDiagnostics* diagnostics)
        : reverse_index_(index), index_(*index.base_index()), workspace_(workspace), source_(source),
          target_(target), weight_(weight), diagnostics_(diagnostics) {}

    NodeId run() {
        workspace_.reset(index_.graph().node_count());
        initialize(workspace_.forward, source_, true);
        initialize(workspace_.backward, target_, false);
        update_peak_heap();
        bool forward_on_tie = true;

        for (;;) {
            // Stale priorities cannot be used as bounds or to choose a direction.
            discard_stale_entries(workspace_.forward, diagnostics_);
            discard_stale_entries(workspace_.backward, diagnostics_);
            if (workspace_.forward.heap.empty() || workspace_.backward.heap.empty()) {
                return meeting_;
            }
            const double forward_min = workspace_.forward.heap.front().priority;
            const double backward_min = workspace_.backward.heap.front().priority;
            // WBAE* uses b = 2*g + w*h - h_opposite. Our keys store b/2.
            // The termination bound requires consistent heuristics in both directions.
            if (meeting_ != NO_NODE && best_distance_ <= forward_min + backward_min) {
                return meeting_;
            }

            bool go_forward = forward_min < backward_min;
            if (forward_min == backward_min) {
                go_forward = forward_on_tie;
                forward_on_tie = !forward_on_tie;
            }
            expand(go_forward);
        }
    }

  private:
    double priority(NodeId node, double distance, bool go_forward) {
        if (diagnostics_) {
            diagnostics_->heuristic_requests += 2;
        }
        if (workspace_.cache_epoch[node] != workspace_.query_epoch) {
            workspace_.h_to_target[node] = index_.heuristic(node, target_);
            workspace_.h_from_source[node] = index_.heuristic(node, source_);
            workspace_.cache_epoch[node] = workspace_.query_epoch;
            if (diagnostics_) {
                diagnostics_->heuristic_evaluations += 2;
            }
        }
        const double toward_goal = go_forward ? workspace_.h_to_target[node] : workspace_.h_from_source[node];
        const double from_root = go_forward ? workspace_.h_from_source[node] : workspace_.h_to_target[node];
        // Multiplying by w/2 first avoids overflowing w*h before division by 2.
        return distance + (weight_ * 0.5) * toward_goal - 0.5 * from_root;
    }

    void initialize(HpaSearchState& search, NodeId root, bool go_forward) {
        search.distance[root] = 0;
        search.parent[root] = NO_NODE;
        search.touched_nodes.push_back(root);
        push_queue(search, priority(root, 0, go_forward), 0, root);
    }

    void update_meeting(NodeId node) {
        const double candidate = workspace_.forward.distance[node] + workspace_.backward.distance[node];
        if (candidate < best_distance_) {
            best_distance_ = candidate;
            meeting_ = node;
            if (diagnostics_) {
                ++diagnostics_->meeting_updates;
            }
        }
    }

    void update_peak_heap() {
        if (diagnostics_) {
            diagnostics_->peak_heap = std::max<std::uint64_t>(diagnostics_->peak_heap,
                workspace_.forward.heap.size() + workspace_.backward.heap.size());
        }
    }

    void expand(bool go_forward) {
        auto& search = go_forward ? workspace_.forward : workspace_.backward;
        const auto entry = pop_queue(search);
        const auto current = entry.node;
        if (diagnostics_) {
            ++diagnostics_->overlay_expanded;
            ++(go_forward ? diagnostics_->forward_expanded : diagnostics_->backward_expanded);
        }

        auto relax = [&](const Edge& edge, bool shortcut) {
            if (diagnostics_) {
                ++diagnostics_->edges_examined;
            }
            const double candidate = entry.distance_from_source + edge.distance_m;
            if (candidate >= search.distance[edge.target]) {
                return;
            }
            if (!std::isfinite(search.distance[edge.target])) {
                search.touched_nodes.push_back(edge.target);
            }
            search.distance[edge.target] = candidate;
            // Backward parents point toward the target along ORIGINAL directed edges.
            search.parent[edge.target] = current;
            search.parent_edge_weight[edge.target] = edge.distance_m;
            search.parent_is_shortcut[edge.target] = shortcut;
            push_queue(search, priority(edge.target, candidate, go_forward), candidate, edge.target);
            update_meeting(edge.target);
            update_peak_heap();
        };

        const auto cluster = index_.cluster(current);
        const bool endpoint_cluster = cluster == index_.cluster(source_) || cluster == index_.cluster(target_);
        const auto edges = go_forward ? index_.graph().outgoing(current) : reverse_index_.incoming_edges(current);
        for (const auto& edge : edges) {
            if (endpoint_cluster || index_.cluster(edge.target) != cluster) {
                relax(edge, false);
            }
        }
        if (!endpoint_cluster) {
            const auto shortcuts = go_forward ? index_.shortcuts(current) : reverse_index_.incoming_shortcuts(current);
            for (const auto& shortcut : shortcuts) {
                relax(shortcut, true);
            }
        }
    }

    const BiHpaIndex& reverse_index_;
    const HpaIndex& index_;
    BiHpaWorkspace& workspace_;
    NodeId source_;
    NodeId target_;
    double weight_;
    BiHpaDiagnostics* diagnostics_;
    double best_distance_ = INFINITY_DISTANCE;
    NodeId meeting_ = NO_NODE;
};

PathResult reconstruct_path(NodeId source, NodeId target, NodeId meeting, const HpaIndex& index,
                            BiHpaWorkspace& workspace, BiHpaDiagnostics* diagnostics) {
    auto& forward_path = workspace.forward_path;
    forward_path.clear();
    for (auto node = meeting; node != source; node = workspace.forward.parent[node]) {
        forward_path.push_back(node);
    }
    forward_path.push_back(source);
    std::reverse(forward_path.begin(), forward_path.end());

    std::vector<NodeId> path{source};
    long double distance = 0;
    auto append_step = [&](NodeId from, NodeId to, const HpaSearchState& search, NodeId child) {
        if (search.parent_is_shortcut[child]) {
            distance += index.append_shortcut_path(from, to, path);
            if (diagnostics) {
                ++diagnostics->shortcuts_used;
            }
        } else {
            path.push_back(to);
            distance += search.parent_edge_weight[child];
        }
    };
    for (std::size_t i = 1; i < forward_path.size(); ++i) {
        append_step(forward_path[i - 1], forward_path[i], workspace.forward, forward_path[i]);
    }
    for (auto node = meeting; node != target;) {
        const auto successor = workspace.backward.parent[node];
        append_step(node, successor, workspace.backward, node);
        node = successor;
    }
    return {PathStatus::Found, static_cast<double>(distance), std::move(path)};
}

} // namespace

BiHpaIndex::BiHpaIndex(std::shared_ptr<const HpaIndex> base_index) : base_index_(std::move(base_index)) {
    const auto started = Clock::now();
    if (!base_index_) {
        throw std::invalid_argument("Bidirectional HPA requires a base HPA index");
    }
    const auto& graph = base_index_->graph();
    build_incoming(graph.node_count(), [&](NodeId node) { return graph.outgoing(node); }, edge_offsets_, edges_);
    build_incoming(graph.node_count(), [&](NodeId node) { return base_index_->shortcuts(node); },
                   shortcut_offsets_, shortcuts_);
    stats_.reverse_index_bytes = (edge_offsets_.capacity() + shortcut_offsets_.capacity()) * sizeof(std::size_t) +
                                 (edges_.capacity() + shortcuts_.capacity()) * sizeof(Edge);
    stats_.reverse_build_ms = std::chrono::duration<double, std::milli>(Clock::now() - started).count();
}

std::span<const Edge> BiHpaIndex::incoming_edges(NodeId node) const {
    return std::span<const Edge>(edges_).subspan(edge_offsets_[node], edge_offsets_[node + 1] - edge_offsets_[node]);
}

std::span<const Edge> BiHpaIndex::incoming_shortcuts(NodeId node) const {
    return std::span<const Edge>(shortcuts_).subspan(shortcut_offsets_[node], shortcut_offsets_[node + 1] - shortcut_offsets_[node]);
}

void BiHpaWorkspace::reset(std::size_t node_count) {
    forward.reset(node_count);
    backward.reset(node_count);
    h_to_target.resize(node_count);
    h_from_source.resize(node_count);
    if (cache_epoch.size() != node_count) {
        cache_epoch.assign(node_count, 0);
        query_epoch = 0;
    }
    // Epoch zero is reserved for invalid entries. Clear stamps once on wraparound.
    if (++query_epoch == 0) {
        std::fill(cache_epoch.begin(), cache_epoch.end(), 0);
        query_epoch = 1;
    }
}

std::size_t BiHpaWorkspace::bytes() const {
    return forward.bytes() + backward.bytes() + forward_path.capacity() * sizeof(NodeId) +
           (h_to_target.capacity() + h_from_source.capacity()) * sizeof(double) +
           cache_epoch.capacity() * sizeof(std::uint32_t);
}

PathResult bihpa(const Graph& graph, const Query& query, const BiHpaIndex& index,
                 BiHpaWorkspace& workspace, double heuristic_weight, BiHpaDiagnostics* diagnostics) {
    if (&graph != &index.base_index()->graph()) {
        throw std::invalid_argument("Bidirectional HPA index belongs to another graph instance");
    }
    if (!std::isfinite(heuristic_weight) || heuristic_weight <= 1) {
        throw std::invalid_argument("Invalid bidirectional HPA weight");
    }
    if (diagnostics) {
        *diagnostics = {};
    }
    if (query.source < 0 || query.target < 0 ||
        static_cast<std::uint64_t>(query.source) >= graph.node_count() ||
        static_cast<std::uint64_t>(query.target) >= graph.node_count()) {
        return {PathStatus::InvalidVertex, std::nullopt, {}};
    }
    const auto source = static_cast<NodeId>(query.source);
    const auto target = static_cast<NodeId>(query.target);
    if (source == target) {
        return {PathStatus::Found, 0, {source}};
    }

    auto started = diagnostics ? Clock::now() : Clock::time_point{};
    const auto meeting = OverlaySearch(index, workspace, source, target, heuristic_weight, diagnostics).run();
    if (diagnostics) {
        diagnostics->search_ns = elapsed_nanoseconds(started);
        started = Clock::now();
    }
    if (meeting == NO_NODE) {
        return {PathStatus::Unreachable, std::nullopt, {}};
    }
    auto result = reconstruct_path(source, target, meeting, *index.base_index(), workspace, diagnostics);
    if (diagnostics) {
        diagnostics->reconstruction_ns = elapsed_nanoseconds(started);
    }
    return result;
}

} // namespace busmap
