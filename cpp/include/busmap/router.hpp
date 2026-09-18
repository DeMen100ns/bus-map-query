#pragma once
#include "busmap/query.hpp"
#include "busmap/search_workspace.hpp"
#include "busmap/bihpa.hpp"

namespace busmap {
enum class Algorithm { Dijkstra, AStar, Hpa, BiHpa, DijkstraBaseline };
Algorithm parse_algorithm(std::string_view name);

// Borrows the graph: its owner must outlive this router. No graph copies per query.
// Owns mutable search workspace. Use one Router per sequential query stream.
class Router {
public:
    Router(const Graph& graph, Algorithm algorithm, HpaOptions options = {},
           std::shared_ptr<const HpaIndex> index = {},
           std::shared_ptr<const BiHpaIndex> bidirectional_index = {});
    [[nodiscard]] const HpaIndex* hpa_index() const { return hpa_index_.get(); }
    [[nodiscard]] const BiHpaIndex* bihpa_index() const { return bihpa_index_.get(); }
    [[nodiscard]] std::size_t workspace_bytes() const {
        return algorithm_ == Algorithm::BiHpa ? bihpa_workspace_.bytes() : hpa_workspace_.bytes();
    }
    [[nodiscard]] PathResult query(const Query& request);
private:
    const Graph& graph_;
    Algorithm algorithm_;
    SearchWorkspace workspace_;
    HpaOptions hpa_options_;
    std::shared_ptr<const HpaIndex> hpa_index_;
    HpaWorkspace hpa_workspace_;
    std::shared_ptr<const BiHpaIndex> bihpa_index_;
    BiHpaWorkspace bihpa_workspace_;
};
}
