#pragma once
#include "busmap/query.hpp"
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <exception>
#include <functional>
#include <future>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <utility>

namespace busmap::detail {
// A separate output thread allows stdin to wait for more input while earlier
// results are emitted. Futures stay in input order; buffered results are bounded.
class OrderedResults {
public:
    using Emit = std::function<void(std::uint64_t, PathResult)>;
    OrderedResults(std::size_t capacity, Emit emit)
        : capacity_(capacity), emit_(std::move(emit)) {
        if (capacity == 0) throw std::invalid_argument("Result capacity must be positive");
        writer_ = std::thread([this] { write(); });
    }
    ~OrderedResults() {
        close();
        if (writer_.joinable()) writer_.join();
    }
    OrderedResults(const OrderedResults&) = delete;
    OrderedResults& operator=(const OrderedResults&) = delete;

    void push(std::uint64_t id, std::future<PathResult> result) {
        {
            std::unique_lock lock(mutex_);
            space_available_.wait(lock, [this] { return closed_ || pending_.size() < capacity_; });
            if (error_) std::rethrow_exception(error_);
            if (closed_) throw std::runtime_error("Result stream is closed");
            pending_.push_back({id, std::move(result)});
        }
        ready_.notify_one();
    }

    void finish() {
        close();
        if (writer_.joinable()) writer_.join();
        if (error_) std::rethrow_exception(error_);
    }

private:
    struct Pending {
        std::uint64_t id;
        std::future<PathResult> result;
    };
    void close() {
        {
            std::lock_guard lock(mutex_);
            closed_ = true;
        }
        ready_.notify_all();
        space_available_.notify_all();
    }
    void write() {
        try {
            for (;;) {
                Pending item;
                {
                    std::unique_lock lock(mutex_);
                    ready_.wait(lock, [this] { return closed_ || !pending_.empty(); });
                    if (pending_.empty()) return;
                    item = std::move(pending_.front());
                    pending_.pop_front();
                }
                space_available_.notify_one();
                emit_(item.id, item.result.get());
            }
        } catch (...) {
            {
                std::lock_guard lock(mutex_);
                error_ = std::current_exception();
                closed_ = true;
                pending_.clear();
            }
            space_available_.notify_all();
        }
    }

    const std::size_t capacity_;
    Emit emit_;
    std::mutex mutex_;
    std::condition_variable ready_;
    std::condition_variable space_available_;
    std::deque<Pending> pending_;
    bool closed_ = false;
    std::exception_ptr error_;
    std::thread writer_;
};
}
