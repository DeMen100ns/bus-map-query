#pragma once
#include "busmap/graph.hpp"

namespace busmap {
// Throws std::runtime_error on malformed or unsupported input.
// graph_sha256 is a dataset identity tag; this function does not hash the JSON.
Graph load_graph(const std::filesystem::path& path);
}
