#include "busmap/graph_loader.hpp"
#include "busmap/hpa.hpp"
#include "busmap/query_pool.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>

using namespace busmap;
using Matrix = std::vector<std::vector<double>>;
constexpr double INF = std::numeric_limits<double>::infinity();
void require(bool condition, const char* message) { if (!condition) throw std::runtime_error(message); }
bool close(double a, double b) { return std::abs(a-b) <= std::max(1e-6, 1e-9 * std::max(a,b)); }
struct TempFile {
    std::filesystem::path path = std::filesystem::temp_directory_path() /
        ("busmap-hpa-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + ".txt");
    ~TempFile() { std::error_code ec; std::filesystem::remove(path, ec); }
};
Graph make_graph(const std::vector<std::pair<double,double>>& xy, const Matrix& weights) {
    TempFile f;
    std::ofstream out(f.path);
    out << std::setprecision(17) << "BUSMAP_GRAPH 1\nGRAPH_SHA256 " << std::string(64,'a')
        << "\nCOORDINATE_CRS EPSG:4326\nDISTANCE_CRS EPSG:3405\nWEIGHT_UNIT meter\nDIRECTED 1\nNODES " << xy.size() << '\n';
    for (std::size_t i=0;i<xy.size();++i) out << i << ' ' << 10.0+i*.00001 << " 106 " << xy[i].first << ' ' << xy[i].second << '\n';
    std::size_t count=0;
    for (std::size_t u=0;u<xy.size();++u) for (std::size_t v=0;v<xy.size();++v) if (u!=v && std::isfinite(weights[u][v])) ++count;
    out << "EDGES " << count << '\n';
    for (std::size_t u=0;u<xy.size();++u) for (std::size_t v=0;v<xy.size();++v)
        if (u!=v && std::isfinite(weights[u][v])) out << u << ' ' << v << ' ' << weights[u][v] << '\n';
    out.close(); return load_graph(f.path);
}
Matrix floyd(Matrix d) {
    for (std::size_t u=0;u<d.size();++u) d[u][u]=0;
    for (std::size_t k=0;k<d.size();++k) for (std::size_t u=0;u<d.size();++u)
        for (std::size_t v=0;v<d.size();++v) d[u][v]=std::min(d[u][v],d[u][k]+d[k][v]);
    return d;
}
void check_graph(const Graph& graph, const Matrix& edges) {
    const auto oracle=floyd(edges); const auto n=graph.node_count();
    for (double size : {1.0, 10.0, 25.0, 10000.0}) {
        auto index=std::make_shared<HpaIndex>(graph,size);
        auto restricted=edges;
        for (std::size_t u=0;u<n;++u) for (std::size_t v=0;v<n;++v)
            if (index->cluster(u)!=index->cluster(v)) restricted[u][v]=INF;
        restricted=floyd(restricted);
        for (NodeId u=0;u<n;++u) {
            std::size_t expected=0;
            if (index->portal(u)) for (NodeId v=0;v<n;++v)
                if (v!=u && index->portal(v) && index->cluster(u)==index->cluster(v) && std::isfinite(restricted[u][v])) ++expected;
            require(index->shortcuts(u).size()==expected,"Missing/doubled shortcut");
            for (auto e:index->shortcuts(u)) {
                require(close(e.distance_m,restricted[u][e.target]),"Shortcut differs from Floyd-Warshall");
                std::vector<NodeId> path{u};
                const auto cost=index->append_shortcut_path(u,e.target,path);
                require(path.back()==e.target && close(cost,e.distance_m),"Stored shortcut cost/endpoints");
                double sum=0;
                for (std::size_t i=1;i<path.size();++i) {
                    require(index->cluster(path[i])==index->cluster(u),"Stored shortcut leaves cluster");
                    require(std::isfinite(edges[path[i-1]][path[i]]),"Stored shortcut missing edge");
                    sum+=edges[path[i-1]][path[i]];
                }
                require(close(sum,cost),"Stored shortcut weight disagrees with actual edges");
            }
        }
        for (double w : {1.005,1.01,1.05}) {
            HpaWorkspace workspace;
            for (NodeId u=0;u<n;++u) for (NodeId v=0;v<n;++v) {
                HpaDiagnostics diag;
                const auto result=hpa(graph,{0,u,v},*index,workspace,w,&diag);
                require(diag.heuristic_evaluations <= diag.heuristic_requests && diag.heuristic_evaluations <= n,
                        "Heuristic must be evaluated at most once per vertex per query");
                if (!std::isfinite(oracle[u][v])) {
                    require(result.status==PathStatus::Unreachable && result.path.empty() && !result.distance_m,"Wrong unreachable"); continue;
                }
                require(result.status==PathStatus::Found && result.distance_m.has_value(),"Missing reachable path");
                require(result.path.front()==u && result.path.back()==v,"Path endpoints");
                if (u==v) require(result.path.size()==1 && *result.distance_m==0,"Self path");
                double sum=0;
                for (std::size_t i=1;i<result.path.size();++i) {
                    auto cost=edges[result.path[i-1]][result.path[i]];
                    require(std::isfinite(cost),"Missing directed path edge"); sum+=cost;
                }
                require(close(sum,*result.distance_m),"Path cost mismatch");
                require(sum+1e-6>=oracle[u][v] && sum<=w*oracle[u][v]+1e-6,"Optimality/weighted bound");
            }
            require(hpa(graph,{0,-1,0},*index,workspace,w).status==PathStatus::InvalidVertex,"Invalid ID");
            require(hpa(graph,{0,0,static_cast<std::int64_t>(n)},*index,workspace,w).status==PathStatus::InvalidVertex,"High invalid ID");
        }
    }
}
int main() {
    try {
        // Leave and re-enter source cluster, disconnected pieces, directed long jumps,
        // zero cycle, equal alternatives, isolated node and a shortcut in a third cluster.
        std::vector<std::pair<double,double>> xy{{-10,0},{-9,0},{0,0},{1,0},{20,0},{21,0},{40,0},{100,0}};
        Matrix e(8,std::vector<double>(8,INF));
        e[0][1]=50; e[0][2]=1; e[2][3]=0; e[3][2]=0; e[3][1]=1;
        e[3][4]=2; e[4][5]=1; e[5][6]=1; e[6][0]=20; e[2][4]=2;
        auto graph=make_graph(xy,e); check_graph(graph,e);
        auto shared=std::make_shared<HpaIndex>(graph,10);
        Router router(graph,Algorithm::Hpa,{10,1.05},shared);
        QueryPool pool(graph,Algorithm::Hpa,4,1,{10,1.05},shared);
        require(router.hpa_index()==shared.get() && pool.hpa_index()==shared.get(),"Index not shared");
        std::vector<std::future<TimedPathResult>> pending_queries;
        for (int i=0;i<30;++i) {
            auto a=pool.submit({0,0,6}).get(), b=router.query({0,0,6});
            require(a.path==b.path && a.distance_m==b.distance_m,"Parallel differs");
            pending_queries.push_back(pool.submit_timed({0,0,6}));
        }
        for (auto& pending:pending_queries) {
            auto a=pending.get().result, b=router.query({0,0,6});
            require(a.path==b.path && a.distance_m==b.distance_m,"Timed parallel differs");
        }
        require(pool.max_workspace_bytes()>0,"Workspace not measured");
        bool mismatch=false;
        try { Router wrong(graph,Algorithm::Hpa,{25,1.05},shared); }
        catch (const std::invalid_argument&) { mismatch=true; }
        require(mismatch,"Mismatched cluster size accepted");
        for (auto options : {HpaOptions{0,1.05},HpaOptions{1,.9},HpaOptions{1,1},HpaOptions{INF,1.05},HpaOptions{1,NAN}}) {
            bool rejected=false; try { options.validate(); } catch (const std::invalid_argument&) { rejected=true; }
            require(rejected,"Invalid options accepted");
        }
        auto other=make_graph(xy,e); bool rejected=false;
        try { Router wrong(other,Algorithm::Hpa,{10,1.05},shared); } catch (const std::invalid_argument&) { rejected=true; }
        require(rejected,"Foreign index accepted");
        // Weighted search may return a longer valid path; w=1 is no longer public.
        {
            Matrix weights(3,std::vector<double>(3,INF));
            weights[0][1]=1; weights[1][2]=99; weights[0][2]=102;
            auto weighted_graph=make_graph({{0,0},{1,0},{100,0}},weights);
            HpaIndex idx(weighted_graph,1000); HpaWorkspace ws;
            const auto result=hpa(weighted_graph,{0,0,2},idx,ws);
            require(close(*result.distance_m,102),"Default must use Weighted A*");
            bool exact_rejected=false;
            try { (void)hpa(weighted_graph,{0,0,2},idx,ws,1); }
            catch (const std::invalid_argument&) { exact_rejected=true; }
            require(exact_rejected,"Exact mode still accepted");
        }
        // Consistent Euclidean h becomes inconsistent after weighting. A is popped
        // before B, then B improves A; the search must reopen A before reaching T.
        {
            Matrix weights(4,std::vector<double>(4,INF));
            weights[0][1]=9.1; weights[0][2]=1; weights[2][1]=8; weights[1][3]=100;
            auto reopen_graph=make_graph({{0,0},{9,0},{1,0},{10,0}},weights);
            HpaIndex idx(reopen_graph,100); HpaWorkspace ws; HpaDiagnostics d;
            const auto result=hpa(reopen_graph,{0,0,3},idx,ws,1.05,&d);
            require(close(*result.distance_m,109) && d.overlay_expanded==5,"Weighted A* did not reopen an improved node");
            require(d.heuristic_evaluations == 4 && d.heuristic_requests > d.heuristic_evaluations,
                    "Reopening should reuse the cached heuristic");
        }
        {
            Matrix singleton(1,std::vector<double>(1,INF));
            auto one=make_graph({{0,0}},singleton); check_graph(one,singleton);
        }
        // Half metric-weighted graphs, half arbitrary/zero-weight graphs.
        std::mt19937 rng(162164);
        for (int trial=0;trial<100;++trial) {
            const int n=2+rng()%19;
            std::vector<std::pair<double,double>> coords(n);
            for (auto& p:coords) p={static_cast<double>(int(rng()%100)-50),static_cast<double>(int(rng()%100)-50)};
            Matrix weights(n,std::vector<double>(n,INF));
            for (int u=0;u<n;++u) for (int v=0;v<n;++v) if (u!=v && rng()%100<22) {
                const double d=std::hypot(coords[u].first-coords[v].first,coords[u].second-coords[v].second);
                weights[u][v]=trial%2 ? d*(1+(rng()%20)/10.0) : rng()%21;
            }
            auto random_graph=make_graph(coords,weights); check_graph(random_graph,weights);
        }
        std::cout << "HPA weighted: 100 all-pairs random graphs, restricted shortcuts, edge cases and shared pool passed\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
