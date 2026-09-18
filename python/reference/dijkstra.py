from preprocessing import build_graph as graph_building
from preprocessing.build_graph import Graph
import math
import heapq
import time, random

def get_graph():
    G = graph_building.graph_building_from_file()
    adj_list = {}
    for v in G._vertices_list:
        adj_list[v._id] = []
    for e in G._edges_list:
        if (e._start._id == e._stop._id):
            continue
        adj_list[e._start._id].append((e._stop._id, e._length))
        # print(e._start._id, e._stop._id, e.get_length())
    return G, adj_list

def Dijkstra_Shortest_Path(U, V, G, adj_list):
    dist = {}
    par = {}
    for v in G._vertices_list:
        dist[v._id] = math.inf
        par[v._id] = v._id
    dist[U] = 0
    pqueue = []
    heapq.heappush(pqueue, (0, U))
    while pqueue:
        du, u = pqueue[0]
        heapq.heappop(pqueue)

        if abs(du - dist[u]) > 1e-4:
            continue

        for v, length in adj_list[u]:
            if dist[u] + length < dist[v]:
                dist[v] = dist[u] + length
                par[v] = u
                heapq.heappush(pqueue, (dist[v], v))
    
    if dist[V] == math.inf:
        return math.inf, []
        
    t = V
    path = [V]
    while t != U:
        t = par[t]
        path.append(t)
    path.reverse()

    return dist[V], path


if __name__ == "__main__":
    G, adj_list = get_graph()

    t_start = time.time()
    for i in range(1000):
        print(i)
        dist, path = Dijkstra_Shortest_Path(random.randint(0, 30000), random.randint(0, 30000), G, adj_list)
    t_end = time.time()
    print(t_end - t_start)
