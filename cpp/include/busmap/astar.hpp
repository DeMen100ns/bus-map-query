#pragma once
#include "busmap/query.hpp"
#include "busmap/search_workspace.hpp"

namespace busmap {
// Workspace persists across queries; the algorithm owns its resize/reset policy.
// See docs/ARCHITECTURE.md for the handoff steps.
PathResult astar(const Graph& graph, const Query& query, SearchWorkspace& workspace);
}
