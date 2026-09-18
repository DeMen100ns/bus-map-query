#include "busmap/graph_loader.hpp"
#include "busmap/router.hpp"
#include <cmath>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

int main(int argc, char** argv) {
    try {
        require(argc == 2, "Need project root");
        const std::filesystem::path root(argv[1]);
        const auto small = busmap::load_graph(root / "tests/fixtures/graph.txt");
        require(small.node_count() == 6 && small.edge_count() == 6, "Fixture counts");
        require(small.outgoing(0).size() == 3, "CSR degree");
        require(small.outgoing(4).empty() && small.outgoing(5).empty(), "Sink and isolated vertex");
        require(small.outgoing(3)[0].target == 4 && small.outgoing(3)[0].distance_m == 0, "Zero-weight edge");
        require(small.coordinate(0).lat == 10 && small.coordinate(0).lon == 106, "Coordinate order");
        bool rejected = false;
        try { (void)small.outgoing(6); } catch (const std::out_of_range&) { rejected = true; }
        require(rejected, "Invalid vertex must throw");
        for (auto algorithm : {busmap::Algorithm::Dijkstra, busmap::Algorithm::AStar, busmap::Algorithm::Hpa}) {
            busmap::Router router(small, algorithm);
            require(router.query({0, -1, 2}).status == busmap::PathStatus::InvalidVertex, "Invalid query");
            const auto expected = busmap::PathStatus::Found;
            require(router.query({0, 0, 4}).status == expected, "Solver dispatch");
        }
        const auto path = root / "data/processed/graph.txt";
        const auto graph = busmap::load_graph(path);
        require(graph.node_count() == 38148 && graph.edge_count() == 42170, "Full dataset counts");
        // Compare every exported record to the actual in-memory CSR, including empty ranges.
        std::ifstream input(path);
        std::string line;
        for (int i = 0; i < 7; ++i) std::getline(input, line);
        for (std::size_t i = 0; i < graph.node_count(); ++i) {
            std::uint64_t id; double lat, lon, x, y;
            input >> id >> lat >> lon >> x >> y;
            auto c = graph.coordinate(static_cast<busmap::NodeId>(i));
            require(id == i && lat == c.lat && lon == c.lon && x == c.x_m && y == c.y_m, "CSR coordinates differ");
        }
        std::string key; std::size_t count;
        input >> key >> count;
        require(key == "EDGES" && count == graph.edge_count(), "Edge header");
        std::size_t visited = 0;
        for (std::size_t u = 0; u < graph.node_count(); ++u) {
            for (auto edge : graph.outgoing(static_cast<busmap::NodeId>(u))) {
                std::uint64_t source, target; double weight;
                input >> source >> target >> weight;
                require(source == u && target == edge.target && weight == edge.distance_m, "CSR edge differs");
                ++visited;
            }
        }
        require(input.good() && visited == count, "Truncated edge comparison");
        std::cout << "Loader/CSR verified: " << graph.node_count() << " nodes, " << graph.edge_count() << " edges\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
