#include "busmap/graph_loader.hpp"
#include "busmap/query_pool.hpp"
#include "ordered_results.hpp"
#include <future>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
void same(const busmap::PathResult& a, const busmap::PathResult& b) {
    require(a.status == b.status && a.distance_m == b.distance_m && a.path == b.path,
            "Pooled result differs from sequential solver");
}
}

int main(int argc, char** argv) {
    try {
        require(argc == 2, "Need project root");
        const auto graph = busmap::load_graph(std::filesystem::path(argv[1]) / "tests/fixtures/graph.txt");
        const std::vector<busmap::Query> queries{
            {90, 3, 3}, {4, 0, 4}, {2, 4, 0}, {17, -1, 2},
            {8, 2, 4}, {6, 5, 0}, {0, 0, 3}, {45, 6, 0}, {5, 0, 0}};
        for (auto algorithm : {busmap::Algorithm::Dijkstra, busmap::Algorithm::DijkstraBaseline, busmap::Algorithm::AStar, busmap::Algorithm::Hpa, busmap::Algorithm::BiHpa}) {
            // Multiple producers, tiny queue, and repeated requests exercise reuse
            // of each worker's private workspace and synchronization under pressure.
            busmap::QueryPool pool(graph, algorithm, 4, 1);
            std::vector<std::future<void>> producers;
            for (int producer = 0; producer < 3; ++producer) {
                producers.push_back(std::async(std::launch::async, [&] {
                    busmap::Router reference(graph, algorithm);
                    for (int repeat = 0; repeat < 20; ++repeat) {
                        std::vector<std::future<busmap::PathResult>> pending;
                        for (auto query : queries) pending.push_back(pool.submit(query));
                        for (std::size_t i = 0; i < queries.size(); ++i)
                            same(pending[i].get(), reference.query(queries[i]));
                    }
                }));
            }
            for (auto& producer : producers) producer.get();
            auto accepted = pool.submit({1, 0, 4});
            pool.close();
            pool.close(); // Idempotent, including while work remains.
            busmap::Router reference(graph, algorithm);
            same(accepted.get(), reference.query({1, 0, 4}));
            bool rejected = false;
            try { (void)pool.submit({2, 0, 4}); } catch (const std::runtime_error&) { rejected = true; }
            require(rejected, "Closed pool accepted a new job");
        }
        // Timed and untimed jobs share the same workers; timings are published
        // with the result and include queue/submission delay without output I/O.
        {
            busmap::QueryPool pool(graph, busmap::Algorithm::Dijkstra, 4, 1);
            std::vector<std::future<busmap::TimedPathResult>> timed;
            for (int i = 0; i < 100; ++i) {
                timed.push_back(pool.submit_timed({static_cast<std::uint64_t>(i), 0, 4}));
                require(pool.submit({0, 3, 3}).get().distance_m == 0, "Mixed untimed job failed");
            }
            pool.close();
            for (auto& future : timed) {
                const auto value = future.get();
                require(value.result.distance_m == 2, "Timed job result incorrect");
                require(value.timing.duration_ns >= 0 && value.timing.wait_ns >= 0 &&
                        value.timing.latency_ns >= value.timing.duration_ns &&
                        std::abs(value.timing.latency_ns - value.timing.wait_ns - value.timing.duration_ns) <= 1,
                        "Inconsistent query timing intervals");
            }
            bool rejected = false;
            try { (void)pool.submit_timed({0, 0, 4}); } catch (const std::runtime_error&) { rejected = true; }
            require(rejected, "Timed submission accepted after close");
        }
        // Futures outlive the pool: destructor must complete accepted work.
        std::vector<std::future<busmap::PathResult>> pending;
        {
            busmap::QueryPool pool(graph, busmap::Algorithm::Dijkstra, 2, 2);
            for (int i = 0; i < 50; ++i) pending.push_back(pool.submit({static_cast<std::uint64_t>(i), 0, 4}));
        }
        for (auto& result : pending) {
            auto value = result.get();
            require(value.status == busmap::PathStatus::Found && value.distance_m == 2, "Shutdown lost work");
        }
        // Router throws on an invalid enum. The exception must reach the future;
        // the worker must stay alive to service the next request.
        {
            busmap::QueryPool pool(graph, static_cast<busmap::Algorithm>(99), 1, 1);
            for (int i = 0; i < 2; ++i) {
                bool rejected = false;
                try { (void)pool.submit({0, 0, 4}).get(); } catch (const std::logic_error&) { rejected = true; }
                require(rejected, "Worker exception did not reach caller");
            }
            bool timed_rejected = false;
            try { (void)pool.submit_timed({0, 0, 4}).get(); }
            catch (const std::logic_error&) { timed_rejected = true; }
            require(timed_rejected, "Timed worker exception did not reach caller");
            require(pool.submit({0, -1, 4}).get().status == busmap::PathStatus::InvalidVertex,
                    "Worker did not survive exception");
        }
        for (auto sizes : {std::pair<std::size_t, std::size_t>{0, 1}, {1, 0}}) {
            bool rejected = false;
            try { busmap::QueryPool pool(graph, busmap::Algorithm::Dijkstra, sizes.first, sizes.second); }
            catch (const std::invalid_argument&) { rejected = true; }
            require(rejected, "Zero size accepted");
        }
        {
            std::vector<std::uint64_t> ids;
            busmap::detail::OrderedResults output(1, [&](std::uint64_t id, busmap::PathResult) { ids.push_back(id); });
            std::promise<busmap::PathResult> first, second;
            output.push(50, first.get_future());
            second.set_value({busmap::PathStatus::Found, 0, {3}});
            output.push(2, second.get_future()); // Second completes before first.
            first.set_value({busmap::PathStatus::Found, 0, {0}});
            output.finish();
            require(ids == std::vector<std::uint64_t>{50, 2}, "Output reordered ready futures");
        }
        {
            busmap::detail::OrderedResults output(1, [](std::uint64_t, busmap::PathResult) {
                throw std::runtime_error("Injected output failure");
            });
            std::promise<busmap::PathResult> result;
            result.set_value({busmap::PathStatus::Found, 0, {0}});
            output.push(0, result.get_future());
            bool rejected = false;
            try { output.finish(); } catch (const std::runtime_error&) { rejected = true; }
            require(rejected, "Writer failure did not reach caller");
        }
        std::cout << "Worker reuse, multiple producers, shutdown, futures and ordered output verified\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
