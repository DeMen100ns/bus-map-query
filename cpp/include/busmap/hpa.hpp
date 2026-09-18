#pragma once

#include "busmap/query.hpp"

#include <memory>
#include <vector>

namespace busmap {

struct HpaOptions {
    double cluster_size_m = 3500;
    double heuristic_weight = 1.05;

    void validate() const;
};

struct HpaStats {
    std::size_t clusters = 0;
    std::size_t portals = 0;
    std::size_t cross_edges = 0;
    std::size_t shortcuts = 0;
    std::size_t max_cluster_nodes = 0;
    std::size_t max_cluster_portals = 0;
    std::size_t index_bytes = 0;
    std::size_t path_storage_bytes = 0;
    std::size_t path_nodes = 0;
    double build_ms = 0;
    double heuristic_scale = 0;
};

// Reusable search memory, owned separately by each worker.
struct HpaSearchState {
    struct QueueEntry {
        double priority;             // f = g + w * h; Dijkstra uses f = g.
        double distance_from_source; // g when this entry was pushed.
        NodeId node;
    };

    std::vector<double> distance;
    std::vector<NodeId> parent;
    std::vector<double> parent_edge_weight;
    std::vector<unsigned char> parent_is_shortcut;
    std::vector<NodeId> touched_nodes;
    std::vector<QueueEntry> heap;

    // Reset only nodes touched by the previous search; retain vector capacity.
    void reset(std::size_t node_count);
    [[nodiscard]] std::size_t bytes() const;
};

struct HpaWorkspace {
    HpaSearchState search;
    // Valid for a node once its distance becomes finite in the current query.
    std::vector<double> heuristic;
    std::vector<NodeId> abstract_path; // Route before expanding shortcut steps.

    [[nodiscard]] std::size_t bytes() const;
};

// Immutable preprocessed data shared across workers.
// The borrowed Graph must outlive the index; it is neither owned nor copied.
class HpaIndex {
  public:
    explicit HpaIndex(const Graph& graph, double cluster_size_m);

    [[nodiscard]] const Graph& graph() const { return *graph_; }
    [[nodiscard]] double cluster_size_m() const { return cluster_size_m_; }
    [[nodiscard]] std::uint32_t cluster(NodeId node) const { return node_cluster_[node]; }
    [[nodiscard]] bool portal(NodeId node) const { return is_portal_[node] != 0; }
    [[nodiscard]] const HpaStats& stats() const { return stats_; }

    [[nodiscard]] std::span<const Edge> shortcuts(NodeId source) const;
    [[nodiscard]] double heuristic(NodeId node, NodeId target) const;

    // path must end at source. Append the stored source-to-target path, excluding
    // source to avoid a duplicate join vertex; return the sum of appended edge costs.
    long double append_shortcut_path(NodeId source, NodeId target, std::vector<NodeId>& path) const;

  private:
    using ClusterMembers = std::vector<std::vector<NodeId>>;

    ClusterMembers partition_nodes(double origin_x, double origin_y);
    void find_portals_and_heuristic_scale(double bounding_box_diagonal);
    void build_shortcuts(const ClusterMembers& cluster_members);
    void run_cluster_dijkstra(NodeId source, std::size_t remaining_portals, HpaSearchState& search);
    void store_shortcut_paths(NodeId source, std::size_t first_shortcut, std::size_t cluster_node_count,
                              const std::vector<NodeId>& parent, std::vector<NodeId>& reversed_path);
    void finalize_storage_statistics();

    const Graph* graph_;
    double cluster_size_m_;
    std::vector<std::uint32_t> node_cluster_;
    std::vector<unsigned char> is_portal_;

    // First CSR: outgoing shortcuts for source occupy
    // shortcuts_[shortcut_offsets_[source] .. shortcut_offsets_[source + 1]).
    std::vector<std::size_t> shortcut_offsets_;
    std::vector<Edge> shortcuts_;

    // Second CSR: the full path of shortcut i occupies
    // path_nodes_[path_offsets_[i] .. path_offsets_[i + 1]), excluding source.
    std::vector<std::size_t> path_offsets_;
    std::vector<NodeId> path_nodes_;
    HpaStats stats_;
};

// Optional diagnostics; normal latency benchmarks pass nullptr.
struct HpaDiagnostics {
    std::uint64_t overlay_expanded = 0;
    std::uint64_t edges_examined = 0;
    std::uint64_t stale_entries = 0;
    std::uint64_t shortcuts_used = 0;
    std::uint64_t peak_heap = 0;
    std::uint64_t search_ns = 0;
    std::uint64_t reconstruction_ns = 0;
    std::uint64_t heuristic_requests = 0;    // Individual h values requested for priorities.
    std::uint64_t heuristic_evaluations = 0; // Actual HpaIndex::heuristic calls on cache misses.
};

PathResult hpa(const Graph& graph, const Query& query, const HpaIndex& index, HpaWorkspace& workspace,
               double heuristic_weight = 1.05, HpaDiagnostics* diagnostics = nullptr);

} // namespace busmap
