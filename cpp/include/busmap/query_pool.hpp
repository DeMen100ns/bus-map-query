#pragma once
#include "busmap/router.hpp"
#include <chrono>
#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <future>
#include <mutex>
#include <thread>
#include <variant>
#include <vector>

namespace busmap {
struct QueryTiming {
    std::int64_t duration_ns = 0; // Wall time inside router.query(), including path reconstruction.
    std::int64_t wait_ns = 0;     // Submission/backpressure/queue/scheduling before search starts.
    std::int64_t latency_ns = 0;  // Submission start to search completion; excludes result delivery.
};
struct TimedPathResult {
    PathResult result;
    QueryTiming timing;
};

// Fixed workers, each with its own persistent Router/workspace; graph is borrowed.
// The graph must outlive the pool. submit() supports multiple producers and blocks
// when the waiting queue is full. Bound retained futures/results in the caller too.
// close() rejects further submissions and drains accepted jobs. Destruction joins
// all workers; callers must stop using the pool before destroying it.
class QueryPool {
public:
    QueryPool(const Graph& graph, Algorithm algorithm,
              std::size_t worker_count, std::size_t queue_capacity,
              HpaOptions options = {}, std::shared_ptr<const HpaIndex> index = {},
              std::shared_ptr<const BiHpaIndex> bidirectional_index = {});
    ~QueryPool();
    QueryPool(const QueryPool&) = delete;
    QueryPool& operator=(const QueryPool&) = delete;

    [[nodiscard]] std::future<PathResult> submit(Query query);
    // Same worker pool and queue. Untimed submit() does not read the clock.
    [[nodiscard]] std::future<TimedPathResult> submit_timed(Query query);
    void close();
    [[nodiscard]] const HpaIndex* hpa_index() const { return hpa_index_.get(); }
    [[nodiscard]] const BiHpaIndex* bihpa_index() const { return bihpa_index_.get(); }
    [[nodiscard]] std::size_t max_workspace_bytes() const { return max_workspace_bytes_.load(); }

private:
    using Clock = std::chrono::steady_clock;
    using ResultPromise = std::variant<std::promise<PathResult>, std::promise<TimedPathResult>>;
    struct Job {
        Query query;
        ResultPromise result;
        Clock::time_point submitted_at{};
    };
    void enqueue(Job job);
    void work();

    const Graph& graph_;
    const Algorithm algorithm_;
    const std::size_t queue_capacity_;
    HpaOptions hpa_options_;
    std::shared_ptr<const HpaIndex> hpa_index_;
    std::shared_ptr<const BiHpaIndex> bihpa_index_;
    std::atomic<std::size_t> max_workspace_bytes_{0};
    std::mutex mutex_;
    std::condition_variable work_available_;
    std::condition_variable space_available_;
    std::deque<Job> jobs_;
    bool closed_ = false;
    std::vector<std::thread> workers_;
};
}
