#include "busmap/bihpa.hpp"
#include "busmap/graph_loader.hpp"
#include "busmap/query_pool.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>

using namespace busmap;
namespace {
using Matrix = std::vector<std::vector<double>>;
using Coordinates = std::vector<std::pair<double, double>>;
constexpr double INF = std::numeric_limits<double>::infinity();

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

bool close(double left, double right) {
    return std::abs(left - right) <= std::max(1e-6, 1e-9 * std::max(left, right));
}

template <class Function>
void require_invalid(Function function) {
    bool rejected = false;
    try { function(); } catch (const std::invalid_argument&) { rejected = true; }
    require(rejected, "Invalid configuration accepted");
}

Graph make_graph(const Coordinates& coordinates, const Matrix& weights) {
    struct TemporaryFile {
        std::filesystem::path path = std::filesystem::temp_directory_path() /
            ("busmap-bihpa-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + ".txt");
        ~TemporaryFile() { std::error_code error; std::filesystem::remove(path, error); }
    } file;
    std::ofstream output(file.path);
    output << std::setprecision(17) << "BUSMAP_GRAPH 1\nGRAPH_SHA256 " << std::string(64, 'a')
           << "\nCOORDINATE_CRS EPSG:4326\nDISTANCE_CRS EPSG:3405\nWEIGHT_UNIT meter\nDIRECTED 1\nNODES "
           << coordinates.size() << '\n';
    for (std::size_t node = 0; node < coordinates.size(); ++node) {
        output << node << ' ' << 10.0 + node * .00001 << " 106 "
               << coordinates[node].first << ' ' << coordinates[node].second << '\n';
    }
    std::size_t edge_count = 0;
    for (std::size_t u = 0; u < weights.size(); ++u) {
        for (std::size_t v = 0; v < weights.size(); ++v) {
            if (u != v && std::isfinite(weights[u][v])) ++edge_count;
        }
    }
    output << "EDGES " << edge_count << '\n';
    for (std::size_t u = 0; u < weights.size(); ++u) {
        for (std::size_t v = 0; v < weights.size(); ++v) {
            if (u != v && std::isfinite(weights[u][v])) output << u << ' ' << v << ' ' << weights[u][v] << '\n';
        }
    }
    output.close();
    return load_graph(file.path);
}

Matrix floyd_warshall(Matrix distance) {
    for (std::size_t u = 0; u < distance.size(); ++u) distance[u][u] = 0;
    for (std::size_t k = 0; k < distance.size(); ++k) {
        for (std::size_t u = 0; u < distance.size(); ++u) {
            for (std::size_t v = 0; v < distance.size(); ++v) {
                distance[u][v] = std::min(distance[u][v], distance[u][k] + distance[k][v]);
            }
        }
    }
    return distance;
}

void check_transpose(const BiHpaIndex& reverse, const Matrix& weights) {
    const auto& base = *reverse.base_index();
    for (NodeId target = 0; target < weights.size(); ++target) {
        std::size_t edge_count = 0;
        std::size_t shortcut_count = 0;
        for (NodeId source = 0; source < weights.size(); ++source) {
            if (source != target && std::isfinite(weights[source][target])) ++edge_count;
            for (const auto& edge : base.shortcuts(source)) {
                if (edge.target == target) ++shortcut_count;
            }
        }
        require(reverse.incoming_edges(target).size() == edge_count, "Missing incoming edge");
        require(reverse.incoming_shortcuts(target).size() == shortcut_count, "Missing incoming shortcut");
        for (const auto& edge : reverse.incoming_edges(target)) {
            require(close(weights[edge.target][target], edge.distance_m), "Reversed edge has wrong cost/direction");
        }
        for (const auto& edge : reverse.incoming_shortcuts(target)) {
            const auto outgoing = base.shortcuts(edge.target);
            require(std::any_of(outgoing.begin(), outgoing.end(), [&](const auto& original) {
                return original.target == target && original.distance_m == edge.distance_m;
            }), "Reversed shortcut has wrong cost/direction");
        }
    }
}

void check_all_pairs(const Graph& graph, const Matrix& weights) {
    const auto oracle = floyd_warshall(weights);
    const auto count = graph.node_count();
    for (double cluster_size : {1.0, 10.0, 25.0, 10000.0}) {
        auto base = std::make_shared<HpaIndex>(graph, cluster_size);
        BiHpaIndex index(base);
        check_transpose(index, weights);
        for (double weight : {1.005, 1.01, 1.05}) {
            BiHpaWorkspace workspace;
            for (NodeId source = 0; source < count; ++source) {
                for (NodeId target = 0; target < count; ++target) {
                    BiHpaDiagnostics diagnostics;
                    const auto result = bihpa(graph, {0, source, target}, index, workspace, weight, &diagnostics);
                    require(diagnostics.heuristic_evaluations <= diagnostics.heuristic_requests &&
                            diagnostics.heuristic_evaluations <= 2 * count,
                            "Heuristic pair must be evaluated at most once per vertex per query");
                    require(diagnostics.overlay_expanded == diagnostics.forward_expanded + diagnostics.backward_expanded,
                            "Directional counters disagree");
                    if (!std::isfinite(oracle[source][target])) {
                        require(result.status == PathStatus::Unreachable && !result.distance_m && result.path.empty(),
                                "Unreachable query returned a path");
                        continue;
                    }
                    require(result.status == PathStatus::Found && result.distance_m.has_value(), "Reachable query failed");
                    require(result.path.front() == source && result.path.back() == target, "Wrong path endpoints");
                    if (source == target) require(result.path.size() == 1 && *result.distance_m == 0, "Invalid self path");
                    double cost = 0;
                    for (std::size_t step = 1; step < result.path.size(); ++step) {
                        const auto edge_cost = weights[result.path[step - 1]][result.path[step]];
                        require(std::isfinite(edge_cost), "Path uses a missing directed edge");
                        cost += edge_cost;
                    }
                    require(close(cost, *result.distance_m), "Path cost differs from returned distance");
                    require(cost + 1e-6 >= oracle[source][target] && cost <= weight * oracle[source][target] + 1e-6,
                            "Weighted bound violated");
                }
            }
            require(bihpa(graph, {0, -1, 0}, index, workspace, weight).status == PathStatus::InvalidVertex, "Negative ID");
            require(bihpa(graph, {0, 0, static_cast<std::int64_t>(count)}, index, workspace, weight).status == PathStatus::InvalidVertex,
                    "Out-of-range ID");
            require(bihpa(graph, {0, 0, 0}, index, workspace, weight).status == PathStatus::Found, "Reuse after invalid query");
        }
    }
}

void check_stopping_and_reopening() {
    // The direct path is the first incumbent, but stopping there would be wrong.
    Matrix edges(3, std::vector<double>(3, INF));
    edges[0][2] = 100; edges[0][1] = 1; edges[1][2] = 1;
    auto graph = make_graph({{0, 0}, {0, 0}, {0, 0}}, edges);
    BiHpaIndex index(std::make_shared<HpaIndex>(graph, 10));
    BiHpaWorkspace workspace;
    BiHpaDiagnostics diagnostics;
    auto result = bihpa(graph, {0, 0, 2}, index, workspace, 1.05, &diagnostics);
    require(close(*result.distance_m, 2) && diagnostics.meeting_updates >= 2, "Stopped at first meeting");
    require(diagnostics.forward_expanded > 0 && diagnostics.backward_expanded > 0, "Both frontiers must run");
    require(diagnostics.heuristic_evaluations == 6 && diagnostics.heuristic_requests > 6,
            "Both frontiers must share cached heuristic pairs");

    // Inflating a consistent heuristic can make keys decrease. A is expanded,
    // then improved through B and expanded again before the stopping bound holds.
    Matrix reopen_edges(4, std::vector<double>(4, INF));
    reopen_edges[0][1] = 9.1; reopen_edges[0][2] = 1;
    reopen_edges[2][1] = 8; reopen_edges[1][3] = 100;
    auto reopen_graph = make_graph({{0, 0}, {9, 0}, {1, 0}, {10, 0}}, reopen_edges);
    BiHpaIndex reopen_index(std::make_shared<HpaIndex>(reopen_graph, 100));
    result = bihpa(reopen_graph, {0, 0, 3}, reopen_index, workspace, 1.05, &diagnostics);
    require(close(*result.distance_m, 109) && diagnostics.forward_expanded == 4, "Improved node was not reopened");

    // The public algorithm remains weighted, rather than silently reverting to exact search.
    edges[0][1] = 1; edges[1][2] = 99; edges[0][2] = 102;
    auto weighted_graph = make_graph({{0, 0}, {1, 0}, {100, 0}}, edges);
    BiHpaIndex weighted_index(std::make_shared<HpaIndex>(weighted_graph, 1000));
    result = bihpa(weighted_graph, {0, 0, 2}, weighted_index, workspace);
    require(close(*result.distance_m, 102), "Expected weighted path");
    for (double weight : {1.0, .9, INF, static_cast<double>(NAN)}) {
        require_invalid([&] { (void)bihpa(weighted_graph, {0, 0, 2}, weighted_index, workspace, weight); });
    }
    require_invalid([&] { (void)bihpa(graph, {0, 0, 2}, weighted_index, workspace); });
    require_invalid([] { BiHpaIndex invalid(nullptr); });
}

void check_backward_shortcut_path() {
    Coordinates points{{0, 0}, {10, 0}, {11, 0}, {12, 0}, {20, 0}, {21, 0}, {22, 0}, {30, 0}};
    Matrix weights(8, std::vector<double>(8, INF));
    for (std::size_t node = 0; node < 7; ++node) weights[node][node + 1] = 1;
    weights[2][1] = 0; // Forces h = 0; the two frontiers advance symmetrically.
    const auto graph = make_graph(points, weights);
    BiHpaIndex index(std::make_shared<HpaIndex>(graph, 10));
    BiHpaWorkspace workspace;
    BiHpaDiagnostics diagnostics;
    const auto result = bihpa(graph, {0, 0, 7}, index, workspace, 1.05, &diagnostics);
    require(result.path == std::vector<NodeId>({0, 1, 2, 3, 4, 5, 6, 7}), "Backward shortcut path reversed incorrectly");
    require(*result.distance_m == 7 && diagnostics.shortcuts_used == 2, "Both shortcut segments must be expanded");
    require(diagnostics.forward_expanded > 0 && diagnostics.backward_expanded > 0, "Missing search direction");
    require(bihpa(graph, {0, 7, 0}, index, workspace).status == PathStatus::Unreachable, "Invented reverse route");
}

void check_sharing(const Graph& graph) {
    auto base = std::make_shared<HpaIndex>(graph, 10);
    auto reverse = std::make_shared<BiHpaIndex>(base);
    Router router(graph, Algorithm::BiHpa, {10, 1.05}, base, reverse);
    QueryPool pool(graph, Algorithm::BiHpa, 4, 1, {10, 1.05}, {}, reverse);
    require(router.hpa_index() == base.get() && pool.hpa_index() == base.get(), "Base index not shared");
    require(router.bihpa_index() == reverse.get() && pool.bihpa_index() == reverse.get(), "Reverse index not shared");
    std::vector<std::future<TimedPathResult>> futures;
    for (int iteration = 0; iteration < 100; ++iteration) {
        futures.push_back(pool.submit_timed({0, iteration % 8, (iteration * 3) % 8}));
    }
    for (int iteration = 0; iteration < 100; ++iteration) {
        const auto actual = futures[iteration].get().result;
        const auto expected = router.query({0, iteration % 8, (iteration * 3) % 8});
        require(actual.status == expected.status && actual.path == expected.path && actual.distance_m == expected.distance_m,
                "Pool result differs from serial result");
    }
    require(pool.max_workspace_bytes() > 0, "Workspace not measured");
    require_invalid([&] { Router wrong(graph, Algorithm::BiHpa, {25, 1.05}, {}, reverse); });
    require_invalid([&] { QueryPool wrong(graph, Algorithm::BiHpa, 2, 1, {25, 1.05}, {}, reverse); });
    auto other_base = std::make_shared<HpaIndex>(graph, 10);
    require_invalid([&] { Router wrong(graph, Algorithm::BiHpa, {10, 1.05}, other_base, reverse); });
    require_invalid([&] { QueryPool wrong(graph, Algorithm::BiHpa, 2, 1, {10, 1.05}, other_base, reverse); });
}

void check_heuristic_cache_reuse() {
    HpaWorkspace forward_workspace;
    BiHpaWorkspace bidirectional_workspace;
    // Reuse workspaces across endpoint changes, different graphs of equal size,
    // resizing, trivial/invalid queries, and the uint32 epoch wraparound.
    for (const auto& coordinates : std::vector<Coordinates>{
             {{0, 0}, {9, 0}, {1, 0}, {10, 0}, {20, 0}},
             {{0, 0}, {1, 3}, {-2, 0}, {4, 1}, {20, 0}},
             {{0, 0}, {9, 0}, {1, 0}, {10, 0}, {20, 0}, {30, 0}},
             {{0, 0}, {9, 0}, {1, 0}, {10, 0}, {20, 0}}}) {
        Matrix weights(coordinates.size(), std::vector<double>(coordinates.size(), INF));
        weights[0][1] = 9.1; weights[0][2] = 1; weights[2][1] = 8;
        weights[1][3] = 100; weights[3][0] = 15; weights[2][3] = 150;
        const auto graph = make_graph(coordinates, weights);
        auto base = std::make_shared<HpaIndex>(graph, 10);
        BiHpaIndex index(base);
        const std::vector<Query> queries{{0, 0, 3}, {1, 3, 1}, {2, 1, 0}, {3, 4, 0},
                                         {4, -1, 0}, {5, 2, 2}, {6, 0, 2}, {7, 2, 3}};
        for (const auto& query : queries) {
            const double weight = query.id % 2 ? 1.01 : 1.05;
            HpaWorkspace fresh_forward;
            BiHpaWorkspace fresh_bidirectional;
            const auto actual_forward = hpa(graph, query, *base, forward_workspace, weight);
            const auto expected_forward = hpa(graph, query, *base, fresh_forward, weight);
            const auto actual_bidirectional = bihpa(graph, query, index, bidirectional_workspace, weight);
            const auto expected_bidirectional = bihpa(graph, query, index, fresh_bidirectional, weight);
            require(actual_forward.status == expected_forward.status && actual_forward.path == expected_forward.path &&
                    actual_forward.distance_m == expected_forward.distance_m, "HPA reused a stale heuristic");
            require(actual_bidirectional.status == expected_bidirectional.status &&
                    actual_bidirectional.path == expected_bidirectional.path &&
                    actual_bidirectional.distance_m == expected_bidirectional.distance_m, "BiHPA reused a stale heuristic");
            if (query.source < 0 || query.source == query.target) continue;
            for (const auto node : forward_workspace.search.touched_nodes) {
                require(forward_workspace.heuristic[node] == base->heuristic(node, query.target), "HPA cached wrong endpoint");
            }
            for (NodeId node = 0; node < graph.node_count(); ++node) {
                if (bidirectional_workspace.cache_epoch[node] != bidirectional_workspace.query_epoch) continue;
                require(bidirectional_workspace.h_to_target[node] == base->heuristic(node, query.target) &&
                        bidirectional_workspace.h_from_source[node] == base->heuristic(node, query.source),
                        "BiHPA cached wrong endpoints");
            }
        }

        // Poison stamps that would collide after wrapping back to epoch one.
        std::fill(bidirectional_workspace.cache_epoch.begin(), bidirectional_workspace.cache_epoch.end(), 1);
        std::fill(bidirectional_workspace.h_to_target.begin(), bidirectional_workspace.h_to_target.end(), NAN);
        std::fill(bidirectional_workspace.h_from_source.begin(), bidirectional_workspace.h_from_source.end(), NAN);
        bidirectional_workspace.query_epoch = std::numeric_limits<std::uint32_t>::max();
        BiHpaWorkspace fresh;
        BiHpaDiagnostics diagnostics;
        const auto actual = bihpa(graph, {0, 0, 3}, index, bidirectional_workspace, 1.05, &diagnostics);
        const auto expected = bihpa(graph, {0, 0, 3}, index, fresh);
        require(bidirectional_workspace.query_epoch == 1 && diagnostics.heuristic_evaluations > 0,
                "Epoch wraparound did not invalidate the old cache");
        require(actual.path == expected.path && actual.distance_m == expected.distance_m, "Epoch wraparound changed the route");
        require(bidirectional_workspace.cache_epoch[4] == 0, "Wraparound left an untouched stale stamp valid");
    }
}
} // namespace

int main() {
    try {
        // Same-cluster exits/re-entry, disconnected pieces, negative coordinates,
        // exact cell boundaries, nonadjacent cells, zero cycles and an isolated node.
        Coordinates coordinates{{-10, 0}, {-9, 0}, {0, 0}, {1, 0}, {20, 0}, {21, 0}, {40, 0}, {100, 0}};
        Matrix edges(8, std::vector<double>(8, INF));
        edges[0][1] = 50; edges[0][2] = 1; edges[2][3] = 0; edges[3][2] = 0;
        edges[3][1] = 1; edges[3][4] = 2; edges[4][5] = 1; edges[5][6] = 1;
        edges[6][0] = 20; edges[2][4] = 2;
        auto graph = make_graph(coordinates, edges);
        check_all_pairs(graph, edges);
        check_sharing(graph);
        check_stopping_and_reopening();
        check_backward_shortcut_path();
        check_heuristic_cache_reuse();
        Matrix singleton(1, std::vector<double>(1, INF));
        const auto single_graph = make_graph({{0, 0}}, singleton);
        check_all_pairs(single_graph, singleton);

        std::mt19937 random(162164);
        for (int trial = 0; trial < 100; ++trial) {
            const auto count = 2 + random() % 19;
            Coordinates points(count);
            for (auto& point : points) {
                point = {static_cast<double>(int(random() % 100) - 50), static_cast<double>(int(random() % 100) - 50)};
            }
            Matrix weights(count, std::vector<double>(count, INF));
            for (std::size_t u = 0; u < count; ++u) {
                for (std::size_t v = 0; v < count; ++v) {
                    if (u == v || random() % 100 >= 22) continue;
                    const double distance = std::hypot(points[u].first - points[v].first, points[u].second - points[v].second);
                    weights[u][v] = trial % 2 ? distance * (1 + (random() % 20) / 10.0) : random() % 21;
                }
            }
            const auto random_graph = make_graph(points, weights);
            check_all_pairs(random_graph, weights);
        }
        std::cout << "BiHPA: 100 all-pairs random graphs, reverse edges, weighted bound, reopening and shared pool passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
