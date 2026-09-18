#include "busmap/graph_loader.hpp"
#include "busmap/router.hpp"
#include "busmap/query_pool.hpp"
#include "text_reader.hpp"
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <unordered_set>
#include <vector>
#include <sys/resource.h>

namespace {
using Clock = std::chrono::steady_clock;
using Nanoseconds = std::chrono::nanoseconds;

double milliseconds(Clock::time_point start, Clock::time_point end) {
    return std::chrono::duration<double, std::milli>(end - start).count();
}

void validate_result(const busmap::PathResult& result) {
    if (result.status == busmap::PathStatus::Found) {
        if (!result.distance_m || !std::isfinite(*result.distance_m) || *result.distance_m < 0 || result.path.empty())
            throw std::runtime_error("Malformed found result");
    } else if (result.distance_m || !result.path.empty()) {
        throw std::runtime_error("Malformed non-found result");
    }
}

void write_results(const std::filesystem::path& path, const std::string& digest,
                   const std::vector<busmap::Query>& queries, const std::vector<busmap::PathResult>& results) {
    std::ofstream output(path);
    output.imbue(std::locale::classic());
    output << std::setprecision(17) << "BUSMAP_RESULTS 1\nGRAPH_SHA256 " << digest << "\nRESULTS " << queries.size() << '\n';
    for (std::size_t i = 0; i < queries.size(); ++i) {
        const auto& result = results[i];
        output << queries[i].id << ' ' << busmap::status_name(result.status) << ' ';
        if (result.distance_m) output << *result.distance_m;
        else output << '-';
        output << ' ' << result.path.size();
        for (auto node : result.path) output << ' ' << node;
        output << '\n';
    }
    output.close();
    if (!output) throw std::runtime_error("Failed writing benchmark results");
}
}

int main(int argc, char** argv) {
    try {
        std::string graph_path, query_path, output_dir, algorithm_name = "dijkstra";
        std::uint64_t repetitions = 5, warmup = 1;
        std::size_t threads = 1, queue_capacity = 64;
        busmap::HpaOptions hpa_options;
        bool hpa_explicit = false, diagnostics = false;
        std::unordered_set<std::string> seen_options;
        for (int i = 1; i < argc; ++i) {
            std::string option = argv[i];
            if (option == "--hpa-diagnostics") {
                if (diagnostics) throw std::runtime_error("Repeated diagnostics flag");
                diagnostics = true; continue;
            }
            if (option == "--help") {
                std::cout << "busmap-bench --graph FILE --queries FILE --output-dir NEW_DIR\n"
                             "  [--algorithm dijkstra|dijkstra_baseline|astar|hpa|bihpa] [--repetitions 5] [--warmup 1]\n"
                             "  [--threads 1] [--queue-capacity 64]\n"
                             "Weighted HPA / BiHPA: --hpa-cluster-size METERS (default 3500) --hpa-weight W > 1 (default 1.05); --hpa-diagnostics.\n"
                             "Warmup counts full query batches. Measures service, wait, latency and batch wall time.\n";
                return 0;
            }
            if ((option != "--graph" && option != "--queries" && option != "--output-dir" &&
                 option != "--algorithm" && option != "--repetitions" && option != "--warmup" &&
                 option != "--threads" && option != "--queue-capacity" && option != "--hpa-cluster-size" && option != "--hpa-weight") ||
                !seen_options.insert(option).second || i + 1 == argc)
                throw std::runtime_error("Unknown, repeated or incomplete option: " + option);
            std::string value = argv[++i];
            if (option == "--graph") graph_path = value;
            else if (option == "--queries") query_path = value;
            else if (option == "--output-dir") output_dir = value;
            else if (option == "--algorithm") algorithm_name = value;
            else if (option == "--repetitions") repetitions = busmap::detail::integer<std::uint64_t>(value);
            else if (option == "--warmup") warmup = busmap::detail::integer<std::uint64_t>(value);
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
        if (graph_path.empty() || query_path.empty() || output_dir.empty() || repetitions == 0)
            throw std::runtime_error("Graph, queries, new output directory and positive repetitions are required");
        if (threads == 0 || queue_capacity == 0)
            throw std::runtime_error("Threads and queue capacity must be positive");
        const auto algorithm = busmap::parse_algorithm(algorithm_name);
        const auto load_start = Clock::now();
        hpa_options.validate();
        if ((hpa_explicit || diagnostics) && algorithm != busmap::Algorithm::Hpa && algorithm != busmap::Algorithm::BiHpa)
            throw std::runtime_error("HPA options require --algorithm hpa or bihpa");
        const auto graph = busmap::load_graph(graph_path);
        const auto load_end = Clock::now();
        const auto setup_start = Clock::now();
        std::unique_ptr<busmap::Router> router;
        std::unique_ptr<busmap::QueryPool> pool;
        if (threads == 1) router = std::make_unique<busmap::Router>(graph, algorithm, hpa_options);
        else pool = std::make_unique<busmap::QueryPool>(graph, algorithm, threads, queue_capacity, hpa_options);
        const auto setup_end = Clock::now();
        const auto* hpa_index = router ? router->hpa_index() : pool->hpa_index();
        const auto* bihpa_index = router ? router->bihpa_index() : pool->bihpa_index();

        std::ifstream input(query_path);
        if (!input) throw std::runtime_error("Cannot open query file");
        busmap::detail::TextReader reader(input);
        reader.expect("BUSMAP_QUERIES", "1");
        if (reader.field("GRAPH_SHA256") != graph.graph_sha256()) throw std::runtime_error("Query/graph hash mismatch");
        const auto count = busmap::detail::integer<std::uint64_t>(reader.field("QUERIES"));
        if (count == 0) throw std::runtime_error("Benchmark requires at least one query");
        std::vector<busmap::Query> queries;
        std::unordered_set<std::uint64_t> ids;
        for (std::uint64_t i = 0; i < count; ++i) {
            auto fields = reader.row(3);
            busmap::Query query{busmap::detail::integer<std::uint64_t>(fields[0]),
                                busmap::detail::integer<std::int64_t>(fields[1]),
                                busmap::detail::integer<std::int64_t>(fields[2])};
            if (!ids.insert(query.id).second) throw std::runtime_error("Duplicate query ID");
            queries.push_back(query);
        }
        reader.finish();
        const std::filesystem::path destination(output_dir);
        if (std::filesystem::exists(destination)) throw std::runtime_error("Output directory already exists; use a new directory");
        std::filesystem::create_directories(destination);
        struct Batch {
            std::vector<busmap::PathResult> results;
            std::vector<busmap::QueryTiming> samples;
            double wall_ms;
        };
        auto run_batch = [&] {
            Batch batch{std::vector<busmap::PathResult>(queries.size()),
                        std::vector<busmap::QueryTiming>(queries.size()), 0};
            std::vector<std::future<busmap::TimedPathResult>> pending;
            if (pool) pending.reserve(queries.size());
            // Arrays are allocated before the batch clock. The clock includes
            // scheduling, backpressure and collecting results, but no file I/O.
            const auto batch_start = Clock::now();
            if (pool) {
                for (const auto& query : queries) pending.push_back(pool->submit_timed(query));
                for (std::size_t i = 0; i < queries.size(); ++i) {
                    auto timed = pending[i].get();
                    batch.samples[i] = timed.timing;
                    batch.results[i] = std::move(timed.result);
                }
            } else {
                for (std::size_t i = 0; i < queries.size(); ++i) {
                    const auto start = Clock::now();
                    auto result = router->query(queries[i]);
                    const auto end = Clock::now();
                    const auto ns = std::chrono::duration_cast<Nanoseconds>(end - start).count();
                    batch.samples[i] = {ns, 0, ns};
                    batch.results[i] = std::move(result);
                }
            }
            batch.wall_ms = milliseconds(batch_start, Clock::now());
            // Validation and serialization are outside both timing intervals.
            for (const auto& result : batch.results) {
                validate_result(result);
                if (result.status == busmap::PathStatus::NotImplemented)
                    throw std::runtime_error("Cannot benchmark an unimplemented algorithm");
            }
            return batch;
        };
        // One executor persists through warmup and all measured batches.
        for (std::uint64_t r = 0; r < warmup; ++r) (void)run_batch();
        std::ofstream timings(destination / "timings.csv");
        timings.imbue(std::locale::classic());
        timings << "repetition,query_id,duration_ns,status,wait_ns,latency_ns\n";
        std::vector<double> run_totals, run_wall;
        for (std::uint64_t r = 0; r < repetitions; ++r) {
            auto batch = run_batch();
            double total_ms = 0;
            for (std::size_t i = 0; i < queries.size(); ++i) {
                const auto& sample = batch.samples[i];
                total_ms += static_cast<double>(sample.duration_ns) / 1e6;
                timings << r + 1 << ',' << queries[i].id << ',' << sample.duration_ns << ','
                        << busmap::status_name(batch.results[i].status) << ','
                        << sample.wait_ns << ',' << sample.latency_ns << '\n';
            }
            run_totals.push_back(total_ms);
            run_wall.push_back(batch.wall_ms);
            write_results(destination / ("results-" + std::to_string(r + 1) + ".txt"), graph.graph_sha256(), queries, batch.results);
        }
        timings.close();
        if (!timings) throw std::runtime_error("Failed writing timings");
        // Separate instrumented pass: no phase clocks/counters in timed queries.
        if (diagnostics) {
            busmap::HpaWorkspace work;
            busmap::BiHpaWorkspace bidirectional_work;
            std::ofstream detail(destination / "diagnostics.csv");
            detail << "query_id,search_ns,reconstruction_ns,overlay_expanded,edges_examined,stale_entries,shortcuts_used,peak_heap,workspace_bytes,workspace_grew,forward_expanded,backward_expanded,meeting_updates,heuristic_requests,heuristic_evaluations\n";
            for (const auto& query : queries) {
                busmap::BiHpaDiagnostics stats;
                const auto before = bihpa_index ? bidirectional_work.bytes() : work.bytes();
                if (bihpa_index) {
                    (void)busmap::bihpa(graph, query, *bihpa_index, bidirectional_work, hpa_options.heuristic_weight, &stats);
                } else {
                    (void)busmap::hpa(graph, query, *hpa_index, work, hpa_options.heuristic_weight, &stats);
                    stats.forward_expanded = stats.overlay_expanded;
                }
                const auto bytes = bihpa_index ? bidirectional_work.bytes() : work.bytes();
                detail << query.id << ',' << stats.search_ns << ',' << stats.reconstruction_ns << ','
                       << stats.overlay_expanded << ',' << stats.edges_examined << ','
                       << stats.stale_entries << ',' << stats.shortcuts_used << ',' << stats.peak_heap << ','
                       << bytes << ',' << (bytes > before) << ',' << stats.forward_expanded << ','
                       << stats.backward_expanded << ',' << stats.meeting_updates << ','
                       << stats.heuristic_requests << ',' << stats.heuristic_evaluations << '\n';
            }
            detail.close();
            if (!detail) throw std::runtime_error("Failed writing diagnostics");
        }
        struct rusage usage{};
        const bool rss_available = getrusage(RUSAGE_SELF, &usage) == 0;
#if defined(__APPLE__)
        constexpr std::uint64_t rss_unit = 1;
#else
        constexpr std::uint64_t rss_unit = 1024;
#endif
        std::ofstream summary(destination / "timing.json");
        summary.imbue(std::locale::classic());
        summary << std::setprecision(17) << "{\n  \"schema_version\": 2,\n  \"algorithm\": " << std::quoted(algorithm_name)
                << ",\n  \"graph_sha256\": " << std::quoted(graph.graph_sha256())
                << ",\n  \"build_type\": " << std::quoted(BUSMAP_BUILD_TYPE)
                << ",\n  \"compiler\": " << std::quoted(BUSMAP_COMPILER)
                << ",\n  \"lto_enabled\": " << (BUSMAP_LTO_ENABLED ? "true" : "false")
                << ",\n  \"clock\": \"std::chrono::steady_clock\",\n  \"query_count\": " << count
                << ",\n  \"repetitions\": " << repetitions << ",\n  \"warmup_batches\": " << warmup
                << ",\n  \"threads\": " << threads << ",\n  \"queue_capacity\": " << queue_capacity
                << ",\n  \"executor\": " << std::quoted(threads == 1 ? "direct" : "worker_pool")
                << ",\n  \"graph_load_ms\": " << milliseconds(load_start, load_end)
                << ",\n  \"router_setup_ms\": " << milliseconds(setup_start, setup_end)
                << ",\n  \"run_query_total_ms\": [";
        for (std::size_t i = 0; i < run_totals.size(); ++i) {
            if (i) summary << ", ";
            summary << run_totals[i];
        }
        summary << "],\n  \"run_batch_wall_ms\": [";
        for (std::size_t i = 0; i < run_wall.size(); ++i) {
            if (i) summary << ", ";
            summary << run_wall[i];
        }
        summary << "],\n  \"peak_rss_bytes\": ";
        if (rss_available) summary << static_cast<std::uint64_t>(usage.ru_maxrss) * rss_unit;
        else summary << "null";
        summary << ",\n  \"diagnostics_enabled\": " << (diagnostics ? "true" : "false")
                << ",\n  \"max_hpa_workspace_bytes_per_worker\": " << (router ? router->workspace_bytes() : pool->max_workspace_bytes())
                << ",\n  \"dijkstra\": ";
        if (algorithm == busmap::Algorithm::Dijkstra || algorithm == busmap::Algorithm::DijkstraBaseline) {
            const bool baseline = algorithm == busmap::Algorithm::DijkstraBaseline;
            summary << "{\"resource_policy\":" << std::quoted(baseline ? "fresh_per_query" : "reused_per_worker")
                    << ",\"distance_reset\":" << std::quoted(baseline ? "full" : "touched")
                    << ",\"heap_storage\":" << std::quoted(baseline ? "local" : "workspace") << '}';
        } else summary << "null";
        summary << ",\n  \"hpa\": ";
        if (hpa_index) {
            const auto& st = hpa_index->stats();
            const auto reverse_stats = bihpa_index ? bihpa_index->stats() : busmap::BiHpaStats{};
            summary << "{\"cluster_size_m\":" << hpa_options.cluster_size_m
                    << ",\"heuristic_weight\":" << hpa_options.heuristic_weight
                    << ",\"path_storage\":" << std::quoted("paths")
                    << ",\"heuristic_scale\":" << st.heuristic_scale
                    << ",\"search_strategy\":" << std::quoted(bihpa_index ? "wbae" : "weighted_astar")
                    << ",\"heuristic_cache\":\"per_query\""
                    << ",\"heuristic_cache_invalidation\":" << std::quoted(bihpa_index ? "epoch" : "first_touch")
                    << ",\"index_build_ms\":" << st.build_ms + reverse_stats.reverse_build_ms
                    << ",\"index_bytes\":" << st.index_bytes + reverse_stats.reverse_index_bytes
                    << ",\"base_index_build_ms\":" << st.build_ms
                    << ",\"base_index_bytes\":" << st.index_bytes
                    << ",\"reverse_build_ms\":" << reverse_stats.reverse_build_ms
                    << ",\"reverse_index_bytes\":" << reverse_stats.reverse_index_bytes
                    << ",\"path_storage_bytes\":" << st.path_storage_bytes
                    << ",\"path_nodes\":" << st.path_nodes
                    << ",\"clusters\":" << st.clusters << ",\"portals\":" << st.portals
                    << ",\"cross_edges\":" << st.cross_edges << ",\"shortcuts\":" << st.shortcuts
                    << ",\"max_cluster_nodes\":" << st.max_cluster_nodes
                    << ",\"max_cluster_portals\":" << st.max_cluster_portals << '}';
        } else summary << "null";
        summary << "\n}\n";
        summary.close();
        if (!summary) throw std::runtime_error("Failed writing summary");
        std::cout << "Timing saved to " << (destination / "timing.json") << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Error: " << error.what() << '\n';
        return 1;
    }
}
