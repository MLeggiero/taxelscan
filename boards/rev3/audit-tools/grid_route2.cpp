#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <vector>
#include <queue>
#include <limits>
#include <chrono>
struct Q { uint32_t f, g, id; bool operator<(const Q& b) const { return f > b.f || (f == b.f && g < b.g); } };
int main(int argc, char** argv) {
  if (argc != 3) return 2;
  FILE* in = nullptr; fopen_s(&in, argv[1], "rb"); if (!in) return 3;
  uint32_t h, w, L, ns, viacost, tlimit;
  fread(&h, 4, 1, in); fread(&w, 4, 1, in); fread(&L, 4, 1, in); fread(&ns, 4, 1, in); fread(&viacost, 4, 1, in); fread(&tlimit, 4, 1, in);
  uint32_t n = h * w;
  std::vector<uint32_t> starts(ns); fread(starts.data(), 4, ns, in);
  std::vector<uint8_t> fr(n), via(n), goal(n), pen((size_t)L * n);
  std::vector<float> heur(n);
  fread(fr.data(), 1, n, in); fread(via.data(), 1, n, in); fread(goal.data(), 1, n, in);
  fread(heur.data(), 4, n, in); fread(pen.data(), 1, (size_t)L * n, in);
  fclose(in);
  const uint32_t INF = std::numeric_limits<uint32_t>::max();
  std::vector<uint32_t> d((size_t)L * n, INF), prev((size_t)L * n, INF);
  std::vector<uint8_t> seen((size_t)L * n, 0);
  std::priority_queue<Q> q;
  for (auto id : starts) { if (id >= L * n) continue; d[id] = 0; q.push({(uint32_t)heur[id % n], 0, id}); }
  uint32_t end = INF, visited = 0; auto t0 = std::chrono::steady_clock::now();
  while (!q.empty()) {
    auto v = q.top(); q.pop(); if (seen[v.id]) continue; seen[v.id] = 1; ++visited;
    if ((visited & 0xFFFF) == 0 && std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count() > tlimit) break;
    uint32_t l = v.id / n, p = v.id % n, y = p / w, x = p % w; uint8_t bit = (uint8_t)(1u << l);
    if (goal[p] & bit) { end = v.id; break; }
    auto relax = [&](uint32_t id, uint32_t cost) { uint32_t g = v.g + cost; if (g < d[id]) { d[id] = g; prev[id] = v.id; q.push({g + (uint32_t)heur[id % n], g, id}); } };
    for (int dy = -1; dy <= 1; ++dy) for (int dx = -1; dx <= 1; ++dx) {
      if (!dx && !dy) continue; int yy = (int)y + dy, xx = (int)x + dx;
      if (yy < 0 || yy >= (int)h || xx < 0 || xx >= (int)w) continue;
      uint32_t pp = (uint32_t)yy * w + (uint32_t)xx;
      if (!(fr[pp] & bit)) continue;
      if (dx && dy && (!(fr[y * w + (uint32_t)xx] & bit) || !(fr[(uint32_t)yy * w + x] & bit))) continue;
      relax(l * n + pp, (dx && dy ? 14u : 10u) + pen[(size_t)l * n + pp]);
    }
    if (via[p]) for (uint32_t ll = 0; ll < L; ++ll) if (ll != l && (fr[p] & (1u << ll))) relax(ll * n + p, viacost + pen[(size_t)ll * n + p]);
  }
  std::vector<uint32_t> path;
  if (end != INF) { for (auto id = end; id != INF; id = prev[id]) path.push_back(id); std::reverse(path.begin(), path.end()); }
  FILE* out = nullptr; fopen_s(&out, argv[2], "wb"); if (!out) return 4;
  uint32_t sz = (uint32_t)path.size(); fwrite(&sz, 4, 1, out); fwrite(path.data(), 4, sz, out); fclose(out);
  fprintf(stderr, "visited %u path %u\n", visited, sz);
  return 0;
}
