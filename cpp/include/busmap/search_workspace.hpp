#pragma once
#include "busmap/graph.hpp"
#include <utility>
#include <vector>

namespace busmap {
// Owned by a Router and reused across sequential queries. Starts empty.
// Algorithms must resize/reset these arrays before use on each query.
// Keep search state here, not in the read-only Graph. This is not a result cache.
struct SearchWorkspace {
    using DijkstraQueueEntry = std::pair<double, NodeId>;
    std::vector<double> distance;
    std::vector<NodeId> parent;
    // Record each discovered vertex once; reset these vertices before the next query.
    std::vector<NodeId> touched;
    // Ordered by (distance, NodeId); capacity is retained between Dijkstra queries.
    std::vector<DijkstraQueueEntry> dijkstra_heap;
};
}
