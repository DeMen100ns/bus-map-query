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

class AStarPathCaching:
    def __init__(self, graph):
        self.graph = graph  # graph represented as an adjacency list
        self.cache = {}  # cache to store computed paths

    def heuristic(self, start, goal):
        return abs(mp[start]._lat - mp[goal]._lat) + abs(mp[start]._lng - mp[goal]._lng)

    def a_star_search(self, start, goal):
        if (start, goal) in self.cache:
            return self.cache[(start, goal)]  # Return cached path if exists
        
        open_list = []
        heapq.heappush(open_list, (0, start))
        came_from = {}
        g_score = {start: 0}
        f_score = {start: self.heuristic(start, goal)}

        while open_list:
            _, current = heapq.heappop(open_list)

            if current == goal:
                path = self.reconstruct_path(came_from, current)
                self.cache[(start, goal)] = path  # Cache the computed path
                return g_score[goal], path

            for neighbor, len in self.graph[current]:
                tentative_g_score = g_score[current] + len
                if neighbor not in g_score or tentative_g_score < g_score[neighbor]:
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g_score
                    f_score[neighbor] = g_score[neighbor] + self.heuristic(neighbor, goal)
                    heapq.heappush(open_list, (f_score[neighbor], neighbor))

        return -1, []  # Return None if no path is found

    def reconstruct_path(self, came_from, current):
        path = [current]
        while current in came_from:
            current = came_from[current]
            path.append(current)
        return path[::-1]  # Return reversed path


if __name__ == "__main__":
    G, adj_list = get_graph()
    astar = AStarPathCaching(adj_list)

    t_start = time.time()
    for i in range(1000):
        print(i)
        dist, path = astar.a_star_search(random.randint(0, 30000), random.randint(0, 30000))
    t_end = time.time()
    print(t_end - t_start)
