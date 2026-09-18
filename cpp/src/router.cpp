#include "busmap/router.hpp"
#include "busmap/dijkstra.hpp"
#include "busmap/dijkstra_baseline.hpp"
#include "busmap/astar.hpp"
#include "busmap/hpa.hpp"
#include <stdexcept>

namespace busmap {
Router::Router(const Graph& graph, Algorithm algorithm, HpaOptions options,
               std::shared_ptr<const HpaIndex> index, std::shared_ptr<const BiHpaIndex> bidirectional_index)
    : graph_(graph), algorithm_(algorithm), hpa_options_(options), hpa_index_(std::move(index)),
      bihpa_index_(std::move(bidirectional_index)) {
    if (algorithm == Algorithm::Hpa || algorithm == Algorithm::BiHpa) {
        options.validate();
        if (algorithm == Algorithm::BiHpa && bihpa_index_) {
            if (hpa_index_ && hpa_index_ != bihpa_index_->base_index())
                throw std::invalid_argument("Bidirectional and base HPA indexes disagree");
            hpa_index_ = bihpa_index_->base_index();
        }
        if (!hpa_index_) hpa_index_ = std::make_shared<HpaIndex>(graph, options.cluster_size_m);
        if (&hpa_index_->graph() != &graph || hpa_index_->cluster_size_m() != options.cluster_size_m)
            throw std::invalid_argument("HPA index and router configuration disagree");
        if (algorithm == Algorithm::BiHpa && !bihpa_index_)
            bihpa_index_ = std::make_shared<BiHpaIndex>(hpa_index_);
    }
}

std::string_view status_name(PathStatus status) {
    switch (status) {
        case PathStatus::Found: return "found";
        case PathStatus::Unreachable: return "unreachable";
        case PathStatus::InvalidVertex: return "invalid_vertex";
        case PathStatus::NotImplemented: return "not_implemented";
    }
    throw std::logic_error("Unknown PathStatus");
}

Algorithm parse_algorithm(std::string_view name) {
    if (name == "dijkstra") return Algorithm::Dijkstra;
    if (name == "dijkstra_baseline") return Algorithm::DijkstraBaseline;
    if (name == "astar") return Algorithm::AStar;
    if (name == "hpa") return Algorithm::Hpa;
    if (name == "bihpa") return Algorithm::BiHpa;
    throw std::invalid_argument("Unknown algorithm: " + std::string(name));
}

PathResult Router::query(const Query& request) {
    if (request.source < 0 || request.target < 0 ||
        static_cast<std::uint64_t>(request.source) >= graph_.node_count() ||
        static_cast<std::uint64_t>(request.target) >= graph_.node_count())
        return {PathStatus::InvalidVertex, std::nullopt, {}};
    switch (algorithm_) {
        case Algorithm::Dijkstra: return dijkstra(graph_, request, workspace_);
        case Algorithm::DijkstraBaseline: return dijkstra_baseline(graph_, request);
        case Algorithm::AStar: return astar(graph_, request, workspace_);
        case Algorithm::Hpa:
            return hpa(graph_, request, *hpa_index_, hpa_workspace_, hpa_options_.heuristic_weight);
        case Algorithm::BiHpa:
            return bihpa(graph_, request, *bihpa_index_, bihpa_workspace_, hpa_options_.heuristic_weight);
    }
    throw std::logic_error("Unknown algorithm enum");
}
}
