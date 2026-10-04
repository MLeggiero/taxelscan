// grid_route3 - octilinear A* with bend costs, for straight 0/45/90-degree routing.
//
// grid_route2 (rev-3) searches cells only, so any path of equal length wins and
// the line-of-sight simplification afterwards turns its staircases into
// segments at whatever angle the free space allowed. Here the search state is
// (layer, cell, direction of arrival): a move costs 10 straight or 14
// diagonal, plus a bend cost when it changes direction - bend45 for a 45-degree
// turn, bend90 for a right angle - and turns sharper than 90 degrees are not
// allowed at all. A via resets the direction. The cheapest path is then made of
// long straight runs at 0, 45 and 90 degrees with as few corners as the space
// allows, which is what a person routing by hand would draw.
//
// Input (binary, little endian):
//   uint32 h, w, L, ns, viacost, tlimit_s, bend45, bend90
//   uint32 seeds[ns]          start states as layer*n + cell (direction: none)
//   uint8  free[n]            bit l = cell free on layer l
//   uint8  via_ok[n]
//   uint8  goal[n]            bit l = goal cell on layer l
//   float  heur[n]            admissible distance-to-goal estimate, cost units
//   uint8  pen[L*n]           extra cost of entering a cell, per layer
// Output: uint32 count, then count cell ids (layer*n + cell), start to goal.
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <queue>
#include <vector>

struct Q {
  uint32_t f, g, id;
  bool operator<(const Q& b) const { return f > b.f || (f == b.f && g < b.g); }
};

static const int DX[8] = {1, 1, 0, -1, -1, -1, 0, 1};
static const int DY[8] = {0, 1, 1, 1, 0, -1, -1, -1};

int main(int argc, char** argv) {
  if (argc != 3) return 2;
  FILE* in = fopen(argv[1], "rb");
  if (!in) return 3;
  uint32_t hd[8];
  if (fread(hd, 4, 8, in) != 8) return 5;
  const uint32_t h = hd[0], w = hd[1], L = hd[2], ns = hd[3], viacost = hd[4], tlimit = hd[5];
  const uint32_t b45 = hd[6], b90 = hd[7];
  const uint32_t n = h * w;
  std::vector<uint32_t> seeds(ns);
  std::vector<uint8_t> fr(n), via(n), goal(n), pen((size_t)L * n);
  std::vector<float> heur(n);
  size_t ok = fread(seeds.data(), 4, ns, in);
  ok += fread(fr.data(), 1, n, in) + fread(via.data(), 1, n, in) + fread(goal.data(), 1, n, in);
  ok += fread(heur.data(), 4, n, in) + fread(pen.data(), 1, (size_t)L * n, in);
  fclose(in);
  if (ok != (size_t)ns + 3 * (size_t)n + n + (size_t)L * n) return 6;

  const uint32_t D = 9;                       // 8 directions + "none"
  const size_t S = (size_t)L * n * D;
  const uint32_t INF = std::numeric_limits<uint32_t>::max();
  std::vector<uint32_t> dist(S, INF), prev(S, INF);
  std::vector<uint8_t> seen(S, 0);
  std::priority_queue<Q> q;
  for (uint32_t c : seeds) {
    if (c >= L * n) continue;
    uint32_t s = c * D + 8;
    dist[s] = 0;
    q.push({(uint32_t)heur[c % n], 0, s});
  }
  auto t0 = std::chrono::steady_clock::now();
  uint32_t end = INF, visited = 0;
  while (!q.empty()) {
    Q v = q.top();
    q.pop();
    if (seen[v.id]) continue;
    seen[v.id] = 1;
    if ((++visited & 0xFFFF) == 0 &&
        std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count() > tlimit)
      break;
    const uint32_t c = v.id / D, d = v.id % D;
    const uint32_t l = c / n, p = c % n, y = p / w, x = p % w;
    const uint8_t bit = (uint8_t)(1u << l);
    if (goal[p] & bit) { end = v.id; break; }
    auto relax = [&](uint32_t s, uint32_t cost) {
      uint32_t g = v.g + cost;
      if (g < dist[s]) {
        dist[s] = g;
        prev[s] = v.id;
        q.push({g + (uint32_t)heur[(s / D) % n], g, s});
      }
    };
    for (uint32_t k = 0; k < 8; ++k) {
      uint32_t turn = 0;
      if (d != 8) {
        uint32_t diff = d > k ? d - k : k - d;
        diff = std::min(diff, 8 - diff);
        if (diff > 2) continue;                 // no acute corners, no reversals
        turn = diff == 1 ? b45 : diff == 2 ? b90 : 0;
      }
      int xx = (int)x + DX[k], yy = (int)y + DY[k];
      if (xx < 0 || yy < 0 || xx >= (int)w || yy >= (int)h) continue;
      uint32_t pp = (uint32_t)yy * w + (uint32_t)xx;
      if (!(fr[pp] & bit)) continue;
      bool diag = DX[k] && DY[k];
      if (diag && (!(fr[y * w + (uint32_t)xx] & bit) || !(fr[(uint32_t)yy * w + x] & bit))) continue;
      relax(((l * n) + pp) * D + k, (diag ? 14u : 10u) + turn + pen[(size_t)l * n + pp]);
    }
    if (via[p])
      for (uint32_t ll = 0; ll < L; ++ll)
        if (ll != l && (fr[p] & (1u << ll))) relax(((ll * n) + p) * D + 8, viacost + pen[(size_t)ll * n + p]);
  }
  std::vector<uint32_t> path;
  if (end != INF)
    for (uint32_t s = end; s != INF; s = prev[s]) path.push_back(s / D);
  std::reverse(path.begin(), path.end());
  // collapse consecutive duplicates (a via keeps the cell, changes the layer)
  FILE* out = fopen(argv[2], "wb");
  if (!out) return 4;
  uint32_t sz = (uint32_t)path.size();
  fwrite(&sz, 4, 1, out);
  fwrite(path.data(), 4, sz, out);
  fclose(out);
  fprintf(stderr, "visited %u path %u\n", visited, sz);
  return 0;
}
