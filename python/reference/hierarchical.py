from preprocessing import build_graph as graph_building
from preprocessing.build_graph import Graph
import math
import heapq
import time, random

mp = {}

def get_graph():
    G = graph_building.graph_building_from_file()
    adj_list = {}
    for v in G._vertices_list:
        adj_list[v._id] = []
        mp[v._id] = v
    for e in G._edges_list:
        if (e._start._id == e._stop._id):
            continue
        adj_list[e._start._id].append((e._stop._id, e._length))
        # print(e._start._id, e._stop._id, e.get_length())
    return G, adj_list

class HierarchicalPathfinding:
    def __init__(self, graph, region_size):
        self.graph = graph  # Graph represented as an adjacency list
        self.region_size = region_size  # Size of each region
        self.regions = self.divide_into_regions()  # Divide the graph into regions
        self.borders = self.identify_borders()  # Identify border nodes in each region
        self.border_paths_cache = self.cache_border_paths()  # Cache paths between borders

    def divide_into_regions(self):
        regions = {}
        region_id = 0

        for node in self.graph:
            region_id = node // self.region_size  # Divide after sort node (To-do: Group by cluster)
            if region_id not in regions:
                regions[region_id] = []
            regions[region_id].append(node)

        for i in range(region_id + 1):
            print(i, regions[i])

        return regions

    def identify_borders(self):
        borders = {}
        mp = {}

        for region_id, nodes in self.regions.items():
            borders[region_id] = []
            for node in nodes:
                for neighbor, len in self.graph[node]:
                    neighbor_region = neighbor // self.region_size
                    if (mp.get((neighbor_region, region_id)) == None):
                        mp[(neighbor_region, region_id)] = 1
                    elif (mp[(neighbor_region, region_id)] <= 9):
                        mp[(neighbor_region, region_id)] += 1
                    else:
                        continue
                    if neighbor_region != region_id:
                        borders[region_id].append(node)
                        # break

        return borders

    def cache_border_paths(self):
        cache = {}
        sum = 0

        l = len(self.borders)
        for i in range(l):
            for j in range(i + 1, l):
                border_nodes_l = self.borders[i]
                border_nodes_r = self.borders[j]
                for border1 in border_nodes_l:
                    for border2 in border_nodes_r:
                        if border1 != border2:
                            print(border1, border2)
                            Len, path = self.a_star_search(border1, border2)
                            cache[(border1, border2)] = (Len, path)

        return cache

    def heuristic(self, start, goal):
        return abs(mp[start]._lat - mp[goal]._lat) + abs(mp[start]._lng - mp[goal]._lng)

    def a_star_search(self, start, goal):
        open_list = []
        heapq.heappush(open_list, (0, start))
        came_from = {}
        g_score = {start: 0}
        f_score = {start: self.heuristic(start, goal)}

        while open_list:
            _, current = heapq.heappop(open_list)

            if current == goal:
                return g_score[goal], self.reconstruct_path(came_from, current)

            for neighbor, len in self.graph[current]:
                tentative_g_score = g_score[current] + len
                if neighbor not in g_score or tentative_g_score < g_score[neighbor]:
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g_score
                    f_score[neighbor] = g_score[neighbor] + self.heuristic(neighbor, goal)
                    heapq.heappush(open_list, (f_score[neighbor], neighbor))

        return math.inf, []

    def reconstruct_path(self, came_from, current):
        path = [current]
        while current in came_from:
            current = came_from[current]
            path.append(current)
        return path[::-1]  # Return reversed path

    def find_path(self, start, goal):
        start_region = start // self.region_size
        goal_region = goal // self.region_size

        if start_region == goal_region:
            return self.a_star_search(start, goal)  # Same region, use A* directly

        # Different regions, find path through borders
        shortest_path = None
        len_shortest_path = math.inf

        for start_border in self.borders[start_region]:
            for goal_border in self.borders[goal_region]:
                print(start_border, goal_border)
                # Combine path from start to start_border, cached path between borders, and goal_border to goal
                len1, start_to_border = self.a_star_search(start, start_border)
                len2, border_to_border = self.border_paths_cache.get((start_border, goal_border))
                len3, goal_border_to_goal = self.a_star_search(goal_border, goal)

                print(len1 + len2 + len3)

                if len1 + len2 + len3 == math.inf:
                    return math.inf, []
                full_path = start_to_border + border_to_border[1:] + goal_border_to_goal[1:]

                if shortest_path is None or len1 + len2 + len3 < len_shortest_path:
                    shortest_path = full_path
                    len_shortest_path = len1 + len2 + len3

        return len_shortest_path, shortest_path


if __name__ == "__main__":
    G, adj_list = get_graph()

    hda = HierarchicalPathfinding(adj_list, 3000)
    print()

    t_start = time.time()

    for i in range(1000):
        print(i)
        len, path = hda.find_path(random.randint(0, 30000), random.randint(0, 30000))

    t_end = time.time()
    print(t_end - t_start)
