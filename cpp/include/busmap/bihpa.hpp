#pragma once

#include "busmap/hpa.hpp"

namespace busmap {

struct BiHpaStats {
    double reverse_build_ms = 0;
    std::size_t reverse_index_bytes = 0;
};

// Adds incoming adjacency to an existing HPA index. Stored shortcut paths are
// shared, never reversed or duplicated. The borrowed Graph must outlive both indexes.
class BiHpaIndex {
  public:
    explicit BiHpaIndex(std::shared_ptr<const HpaIndex> base_index);

    [[nodiscard]] const std::shared_ptr<const HpaIndex>& base_index() const { return base_index_; }
    [[nodiscard]] const BiHpaStats& stats() const { return stats_; }

    // In incoming adjacency, Edge::target denotes the predecessor in the original graph.
    [[nodiscard]] std::span<const Edge> incoming_edges(NodeId node) const;
    [[nodiscard]] std::span<const Edge> incoming_shortcuts(NodeId node) const;

  private:
    std::shared_ptr<const HpaIndex> base_index_;
    std::vector<std::size_t> edge_offsets_;
    std::vector<Edge> edges_;
    std::vector<std::size_t> shortcut_offsets_;
    std::vector<Edge> shortcuts_;
    BiHpaStats stats_;
};

struct BiHpaWorkspace {
    HpaSearchState forward;
    HpaSearchState backward;
    std::vector<NodeId> forward_path;

    // Both search directions share these values, valid only for query_epoch.
    std::vector<double> h_to_target;
    std::vector<double> h_from_source;
    std::vector<std::uint32_t> cache_epoch;
    std::uint32_t query_epoch = 0;

    void reset(std::size_t node_count);
    [[nodiscard]] std::size_t bytes() const;
};

struct BiHpaDiagnostics : HpaDiagnostics {
    std::uint64_t forward_expanded = 0;
    std::uint64_t backward_expanded = 0;
    std::uint64_t meeting_updates = 0;
};

// Weighted bidirectional search (WBAE*, lambda = 1) on the HPA overlay.
// Reopens improved nodes; stops at the frontier bound, not at the first meeting.
PathResult bihpa(const Graph& graph, const Query& query, const BiHpaIndex& index,
                 BiHpaWorkspace& workspace, double heuristic_weight = 1.05,
                 BiHpaDiagnostics* diagnostics = nullptr);

} // namespace busmap
