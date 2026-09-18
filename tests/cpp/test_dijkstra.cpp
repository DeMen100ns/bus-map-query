#include "busmap/astar.hpp"
#include "busmap/dijkstra.hpp"
#include "busmap/dijkstra_baseline.hpp"
#include "busmap/graph_loader.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>

namespace {
using namespace busmap;
using Matrix = std::vector<std::vector<double>>;
constexpr double INF = std::numeric_limits<double>::infinity();

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

struct TemporaryFile {
    std::filesystem::path path = std::filesystem::temp_directory_path() /
        ("busmap-dijkstra-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + ".txt");
    ~TemporaryFile() { std::error_code error; std::filesystem::remove(path, error); }
};

Graph make_graph(const Matrix& edges) {
    TemporaryFile file;
    std::ofstream out(file.path);
    out << std::setprecision(17) << "BUSMAP_GRAPH 1\nGRAPH_SHA256 " << std::string(64, 'a')
        << "\nCOORDINATE_CRS EPSG:4326\nDISTANCE_CRS EPSG:3405\nWEIGHT_UNIT meter\nDIRECTED 1\nNODES "
        << edges.size() << '\n';
    // Coincident projected coordinates make A* admissible for arbitrary test weights.
    for (std::size_t node = 0; node < edges.size(); ++node)
        out << node << ' ' << 10.0 + node * .00001 << " 106 0 0\n";
    std::size_t edge_count = 0;
    for (const auto& row : edges) for (double weight : row) edge_count += std::isfinite(weight);
    out << "EDGES " << edge_count << '\n';
    for (std::size_t u = 0; u < edges.size(); ++u)
        for (std::size_t v = 0; v < edges.size(); ++v)
            if (std::isfinite(edges[u][v])) out << u << ' ' << v << ' ' << edges[u][v] << '\n';
    out.close();
    return load_graph(file.path);
}

Matrix floyd_warshall(Matrix distance) {
    for (std::size_t u = 0; u < distance.size(); ++u) distance[u][u] = 0;
    for (std::size_t k = 0; k < distance.size(); ++k)
        for (std::size_t u = 0; u < distance.size(); ++u)
            for (std::size_t v = 0; v < distance.size(); ++v)
                distance[u][v] = std::min(distance[u][v], distance[u][k] + distance[k][v]);
    return distance;
}

void same_result(const PathResult& actual, const PathResult& expected) {
    require(actual.status == expected.status && actual.distance_m == expected.distance_m &&
            actual.path == expected.path, "Baseline and reused search disagree");
}

void check_path(const PathResult& result, const Query& query, const Matrix& edges, const Matrix& oracle) {
    const auto expected = oracle[query.source][query.target];
    if (!std::isfinite(expected)) {
        require(result.status == PathStatus::Unreachable && !result.distance_m && result.path.empty(),
                "Incorrect unreachable result");
        return;
    }
    require(result.status == PathStatus::Found && result.distance_m == expected, "Distance differs from Floyd-Warshall");
    require(!result.path.empty() && result.path.front() == query.source && result.path.back() == query.target,
            "Incorrect path endpoints");
    require(result.path.size() <= edges.size(), "Path contains a cycle");
    double cost = 0;
    for (std::size_t i = 1; i < result.path.size(); ++i) {
        require(result.path[i - 1] < edges.size() && result.path[i] < edges.size(), "Invalid path vertex");
        const double weight = edges[result.path[i - 1]][result.path[i]];
        require(std::isfinite(weight), "Path uses a missing directed edge");
        cost += weight;
    }
    require(cost == expected, "Path cost differs from reported cost");
}

void check_graph(const Matrix& edges, SearchWorkspace& workspace, std::mt19937& random) {
    const auto graph = make_graph(edges);
    const auto oracle = floyd_warshall(edges);
    std::vector<Query> queries;
    for (NodeId u = 0; u < edges.size(); ++u)
        for (NodeId v = 0; v < edges.size(); ++v) queries.push_back({queries.size(), u, v});
    for (int repetition = 0; repetition < 2; ++repetition) {
        std::shuffle(queries.begin(), queries.end(), random);
        for (const auto& query : queries) {
            const auto baseline = dijkstra_baseline(graph, query);
            const auto optimized = dijkstra(graph, query, workspace);
            same_result(optimized, baseline);
            check_path(optimized, query, edges, oracle);
            // Sharing SearchWorkspace sequentially with A* must not leak search state.
            if (query.id % 7 == 0) same_result(astar(graph, query, workspace), baseline);
            if (query.id % 11 == 0) {
                for (Query invalid : {Query{0, -1, 0}, Query{0, 0, static_cast<std::int64_t>(edges.size())},
                                      Query{0, std::numeric_limits<std::int64_t>::max(), 0}}) {
                    const auto result = dijkstra(graph, invalid, workspace);
                    require(result.status == PathStatus::InvalidVertex && result.path.empty() && !result.distance_m,
                            "Invalid vertex accepted");
                    same_result(result, dijkstra_baseline(graph, invalid));
                }
            }
        }
    }
}
} // namespace

int main() {
    try {
        std::mt19937 random(162164);
        SearchWorkspace workspace;
        Matrix edges(9, std::vector<double>(9, INF));
        // Early target exit leaves frontier entries; later relaxations produce stale entries.
        edges[0][1] = 1; edges[0][2] = 50; edges[0][3] = 80;
        edges[1][2] = 1; edges[2][3] = 0; edges[3][2] = 0;
        edges[1][4] = 1; edges[4][5] = 1; edges[3][5] = 1;
        const auto graph = make_graph(edges);
        const auto saved = dijkstra(graph, {0, 0, 1}, workspace);
        same_result(saved, dijkstra_baseline(graph, {0, 0, 1}));
        check_graph(edges, workspace, random);
        require(saved.path == std::vector<NodeId>({0, 1}) && saved.distance_m == 1,
                "Later queries modified a retained result");

        // Change graph identity at equal size, then shrink and grow the shared workspace.
        edges[0][1] = INF; edges[1][0] = 7; edges[8][0] = 0;
        check_graph(edges, workspace, random);
        check_graph(Matrix(1, std::vector<double>(1, INF)), workspace, random);
        for (int trial = 0; trial < 100; ++trial) {
            const std::size_t size = 2 + random() % 21;
            Matrix weights(size, std::vector<double>(size, INF));
            for (std::size_t u = 0; u < size; ++u)
                for (std::size_t v = 0; v < size; ++v)
                    if (u != v && random() % 100 < 25) weights[u][v] = (random() % 81) / 4.0;
            check_graph(weights, workspace, random);
        }
        std::cout << "Dijkstra variants: Floyd-Warshall, 100 random graphs, workspace reuse and edge cases passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
