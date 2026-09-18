#pragma once
#include "busmap/query.hpp"
#include "busmap/search_workspace.hpp"

namespace busmap {
// Workspace persists across queries; the algorithm owns its resize/reset policy.
PathResult astar(const Graph& graph, const Query& query, SearchWorkspace& workspace);
}
