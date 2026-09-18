#include "busmap/graph_loader.hpp"
#include "busmap/router.hpp"
#include "busmap/query_pool.hpp"
#include "ordered_results.hpp"
#include "text_reader.hpp"
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <unordered_set>

int main(int argc, char** argv) {
    try {
        std::string graph_path, query_path, algorithm_name = "dijkstra";
        std::size_t threads = 1, queue_capacity = 64;
        busmap::HpaOptions hpa_options;
        bool hpa_explicit = false;
        std::unordered_set<std::string> options;
        for (int i = 1; i < argc; ++i) {
            std::string option = argv[i];
            if (option == "--help") {
                std::cout << "busmap-query --graph FILE [--algorithm dijkstra|dijkstra_baseline|astar|hpa|bihpa] [--queries FILE]\n"
                             "             [--threads N] [--queue-capacity N]\n"
                             "Weighted HPA / BiHPA: --hpa-cluster-size METERS (default 3500) --hpa-weight W > 1 (default 1.05).\n"
                             "Defaults: 1 thread, 64 queued jobs/results. Output stays in input order.\n"
                             "Without --queries, read BUSMAP_QUERIES from stdin. Graph loads once.\n";
                return 0;
            }
            if ((option != "--graph" && option != "--queries" && option != "--algorithm" &&
                 option != "--threads" && option != "--queue-capacity" && option != "--hpa-cluster-size" && option != "--hpa-weight") ||
                !options.insert(option).second || i + 1 == argc)
                throw std::runtime_error("Unknown, repeated or incomplete option: " + option);
            std::string value = argv[++i];
            if (option == "--graph") graph_path = value;
            else if (option == "--queries") query_path = value;
            else if (option == "--algorithm") algorithm_name = value;
            else if (option == "--hpa-cluster-size" || option == "--hpa-weight") {
                std::size_t used = 0;
                const double number = std::stod(value, &used);
                if (used != value.size()) throw std::runtime_error("Invalid HPA numeric option");
                if (option == "--hpa-cluster-size") hpa_options.cluster_size_m = number;
                else hpa_options.heuristic_weight = number;
                hpa_explicit = true;
            }
            else if (option == "--threads") threads = busmap::detail::integer<std::size_t>(value);
            else queue_capacity = busmap::detail::integer<std::size_t>(value);
        }
        if (graph_path.empty()) throw std::runtime_error("--graph is required");
        if (threads == 0 || queue_capacity == 0)
            throw std::runtime_error("Threads and queue capacity must be positive");
        auto algorithm = busmap::parse_algorithm(algorithm_name);
        hpa_options.validate();
        if (hpa_explicit && algorithm != busmap::Algorithm::Hpa && algorithm != busmap::Algorithm::BiHpa)
            throw std::runtime_error("HPA options require --algorithm hpa or bihpa");
        const auto graph = busmap::load_graph(graph_path);
        std::ifstream query_file;
        std::istream* input = &std::cin;
        if (!query_path.empty()) {
            query_file.open(query_path);
            if (!query_file) throw std::runtime_error("Cannot open queries: " + query_path);
            input = &query_file;
        }
        // Input must not flush cout while the parallel output thread is writing.
        if (threads > 1) std::cin.tie(nullptr);
        busmap::detail::TextReader reader(*input);
        reader.expect("BUSMAP_QUERIES", "1");
        if (reader.field("GRAPH_SHA256") != graph.graph_sha256()) throw std::runtime_error("Query/graph hash mismatch");
        auto count = busmap::detail::integer<std::uint64_t>(reader.field("QUERIES"));
        std::cout.imbue(std::locale::classic());
        std::cout << std::setprecision(17) << "BUSMAP_RESULTS 1\nGRAPH_SHA256 " << graph.graph_sha256()
                  << "\nRESULTS " << count << '\n' << std::flush;
        if (!std::cout) throw std::runtime_error("Output write failed");
        std::unordered_set<std::uint64_t> seen;
        bool unfinished = false, invalid = false;
        auto read_query = [&] {
            auto fields = reader.row(3);
            busmap::Query query{busmap::detail::integer<std::uint64_t>(fields[0]),
                                busmap::detail::integer<std::int64_t>(fields[1]),
                                busmap::detail::integer<std::int64_t>(fields[2])};
            if (!seen.insert(query.id).second) throw std::runtime_error("Duplicate query ID");
            return query;
        };
        auto emit = [&](std::uint64_t id, busmap::PathResult result) {
            if (result.status == busmap::PathStatus::Found) {
                if (!result.distance_m || !std::isfinite(*result.distance_m) || *result.distance_m < 0 || result.path.empty())
                    throw std::runtime_error("Algorithm returned a malformed found result");
            } else if (result.distance_m || !result.path.empty()) {
                throw std::runtime_error("Algorithm returned malformed non-found result");
            }
            std::cout << id << ' ' << busmap::status_name(result.status) << ' ';
            if (result.distance_m) std::cout << *result.distance_m;
            else std::cout << '-';
            std::cout << ' ' << result.path.size();
            for (auto node : result.path) std::cout << ' ' << node;
            std::cout << '\n' << std::flush;
            if (!std::cout) throw std::runtime_error("Output write failed");
            unfinished |= result.status == busmap::PathStatus::NotImplemented;
            invalid |= result.status == busmap::PathStatus::InvalidVertex;
        };
        if (threads == 1) {
            busmap::Router router(graph, algorithm, hpa_options);
            for (std::uint64_t i = 0; i < count; ++i) {
                auto query = read_query();
                emit(query.id, router.query(query));
            }
            reader.finish();
        } else {
            busmap::QueryPool pool(graph, algorithm, threads, queue_capacity, hpa_options);
            busmap::detail::OrderedResults output(queue_capacity, emit);
            for (std::uint64_t i = 0; i < count; ++i) {
                auto query = read_query();
                output.push(query.id, pool.submit(query));
            }
            pool.close();
            output.finish();
            reader.finish();
        }
        if (unfinished) {
            std::cerr << "Selected shortest-path algorithm is not implemented.\n";
            return 2;
        }
        return invalid ? 1 : 0;
    } catch (const std::exception& error) {
        std::cerr << "Error: " << error.what() << '\n';
        return 1;
    }
}
