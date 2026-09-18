#include "busmap/query_pool.hpp"
#include <exception>
#include <optional>
#include <stdexcept>
#include <utility>

namespace busmap {
QueryPool::QueryPool(const Graph& graph, Algorithm algorithm,
                     std::size_t worker_count, std::size_t queue_capacity,
                     HpaOptions options, std::shared_ptr<const HpaIndex> index,
                     std::shared_ptr<const BiHpaIndex> bidirectional_index)
    : graph_(graph), algorithm_(algorithm), queue_capacity_(queue_capacity),
      hpa_options_(options), hpa_index_(std::move(index)), bihpa_index_(std::move(bidirectional_index)) {
    if (worker_count == 0 || queue_capacity == 0)
        throw std::invalid_argument("Worker count and queue capacity must be positive");
    if (algorithm == Algorithm::Hpa || algorithm == Algorithm::BiHpa) {
        // Validate and build synchronously so setup errors reach the calling thread.
        options.validate();
        if (algorithm == Algorithm::BiHpa && bihpa_index_) {
            if (hpa_index_ && hpa_index_ != bihpa_index_->base_index())
                throw std::invalid_argument("Bidirectional and base HPA indexes disagree");
            hpa_index_ = bihpa_index_->base_index();
        }
        if (!hpa_index_) hpa_index_ = std::make_shared<HpaIndex>(graph, options.cluster_size_m);
        if (&hpa_index_->graph() != &graph || hpa_index_->cluster_size_m() != options.cluster_size_m)
            throw std::invalid_argument("HPA index and pool configuration disagree");
        if (algorithm == Algorithm::BiHpa && !bihpa_index_)
            bihpa_index_ = std::make_shared<BiHpaIndex>(hpa_index_);
    }
    workers_.reserve(worker_count);
    try {
        for (std::size_t i = 0; i < worker_count; ++i)
            workers_.emplace_back([this] { work(); });
    } catch (...) {
        close();
        for (auto& worker : workers_) worker.join();
        throw;
    }
}

QueryPool::~QueryPool() {
    close();
    for (auto& worker : workers_) worker.join();
}

std::future<PathResult> QueryPool::submit(Query query) {
    std::promise<PathResult> promise;
    auto result = promise.get_future();
    enqueue(Job{query, std::move(promise)});
    return result;
}

std::future<TimedPathResult> QueryPool::submit_timed(Query query) {
    const auto submitted_at = Clock::now();
    std::promise<TimedPathResult> promise;
    auto result = promise.get_future();
    enqueue(Job{query, std::move(promise), submitted_at});
    return result;
}

void QueryPool::enqueue(Job job) {
    {
        std::unique_lock lock(mutex_);
        space_available_.wait(lock, [this] { return closed_ || jobs_.size() < queue_capacity_; });
        if (closed_) throw std::runtime_error("Query pool is closed");
        jobs_.push_back(std::move(job));
    }
    work_available_.notify_one();
}

void QueryPool::close() {
    {
        std::lock_guard lock(mutex_);
        closed_ = true;
    }
    work_available_.notify_all();
    space_available_.notify_all();
}

void QueryPool::work() {
    Router router(graph_, algorithm_, hpa_options_, hpa_index_, bihpa_index_); // Created once per worker, outside the job loop.
    for (;;) {
        std::optional<Job> job;
        {
            std::unique_lock lock(mutex_);
            work_available_.wait(lock, [this] { return closed_ || !jobs_.empty(); });
            if (jobs_.empty()) return;
            job.emplace(std::move(jobs_.front()));
            jobs_.pop_front();
        }
        space_available_.notify_one();
        // Search is outside the queue lock. Only this worker accesses its router.
        try {
            if (auto* promise = std::get_if<std::promise<TimedPathResult>>(&job->result)) {
                const auto start = Clock::now();
                auto result = router.query(job->query);
                const auto end = Clock::now();
                using Ns = std::chrono::nanoseconds;
                QueryTiming timing{
                    std::chrono::duration_cast<Ns>(end - start).count(),
                    std::chrono::duration_cast<Ns>(start - job->submitted_at).count(),
                    std::chrono::duration_cast<Ns>(end - job->submitted_at).count()};
                auto bytes = router.workspace_bytes();
                auto previous = max_workspace_bytes_.load(std::memory_order_relaxed);
                while (previous < bytes && !max_workspace_bytes_.compare_exchange_weak(previous, bytes, std::memory_order_relaxed)) {}
                promise->set_value({std::move(result), timing});
            } else {
                std::get<std::promise<PathResult>>(job->result).set_value(router.query(job->query));
            }
        } catch (...) {
            const auto error = std::current_exception();
            std::visit([&](auto& promise) { promise.set_exception(error); }, job->result);
        }
    }
}
}
