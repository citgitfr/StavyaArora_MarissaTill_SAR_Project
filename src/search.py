"""Search planning on a particle forecast (Lab 4, Phase C). LLM-free, deterministic.

From a forecast's particles (optionally weighted) this builds a probability grid, allocates a fixed amount of search
effort with one of two planners, and scores the plan:

  probability grid   p_c: a kernel density estimate of the (weighted) particles, integrated over square cells of
                     CELL_KM, normalized to 1 over a grid covering the cloud plus 3 kernel bandwidths. Land is not
                     masked (the object is assumed to be in the water; stated limitation).
  effort             Z = sweep width W (km) x track length L (km) = area "swept" per period, in km2.
                     L = assets x speed x hours on scene. These are ASSUMED planning numbers (see DEFAULTS), not
                     values from IAMSAR tables; change them with arguments.
  detection          exponential (random-search) law: POD_c = 1 - exp(-z_c / A_c), z_c = effort put in cell c.
  planners           greedy   : the allocation that maximizes POS = sum p_c POD_c for this law (the classical
                                 optimal-search solution: z_c = A_c ln(d_c / lambda) where the density d_c = p_c / A_c
                                 exceeds lambda, else 0; lambda set by the effort budget).
                     iamsar   : uniform coverage of a square centred on the datum (the probability-weighted centre),
                                 its side chosen from a fixed set to maximize the predicted POS. A simplified stand-in
                                 for the IAMSAR datum-and-search-area procedure, not the full manual method.
  scoring            predicted POS = sum p_c POD_c (what the planner believes); against reality, POD at the cell
                     holding the TRUE position, i.e. the probability this plan would have found the object.
  after a search     Bayes update of particle weights: w_i <- w_i (1 - POD at particle i), renormalized.

The object's motion during the search period is ignored (the plan is made for one moment).
"""
import numpy as np

KM_PER_DEG = 111.195
CELL_KM = 1.0
DEFAULTS = {"sweep_width_km": 1.0, "speed_kmh": 27.8, "hours_on_scene": 4.0, "assets": 1}   # assumptions
SQUARE_SIDES_KM = tuple(float(s) for s in (2, 3, 4, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 60, 80))


def effort_km2(sweep_width_km=None, speed_kmh=None, hours_on_scene=None, assets=None):
    d = DEFAULTS
    return ((sweep_width_km or d["sweep_width_km"]) * (speed_kmh or d["speed_kmh"])
            * (hours_on_scene or d["hours_on_scene"]) * (assets or d["assets"]))


class Grid:
    """Square cells of CELL_KM in a local km frame around (lon0, lat0)."""

    def __init__(self, lon0, lat0, x_edges, y_edges):
        self.lon0, self.lat0 = lon0, lat0
        self.kx = KM_PER_DEG * np.cos(np.radians(lat0))
        self.x_edges, self.y_edges = x_edges, y_edges
        self.xc = 0.5 * (x_edges[1:] + x_edges[:-1])
        self.yc = 0.5 * (y_edges[1:] + y_edges[:-1])
        self.cell_area = float((x_edges[1] - x_edges[0]) * (y_edges[1] - y_edges[0]))

    def to_xy(self, lon, lat):
        return (np.asarray(lon) - self.lon0) * self.kx, (np.asarray(lat) - self.lat0) * KM_PER_DEG

    def cell_of(self, lon, lat):
        """(iy, ix) of each point, or -1 where outside the grid."""
        x, y = self.to_xy(lon, lat)
        ix = np.searchsorted(self.x_edges, x) - 1
        iy = np.searchsorted(self.y_edges, y) - 1
        out = (ix < 0) | (ix >= self.xc.size) | (iy < 0) | (iy >= self.yc.size) | ~np.isfinite(x) | ~np.isfinite(y)
        return np.where(out, -1, iy), np.where(out, -1, ix)


def probability_grid(lon, lat, weights=None, cell_km=CELL_KM, max_cells=250_000):
    """(Grid, p) with p[iy, ix] summing to 1. Weighted Gaussian KDE (Scott's bandwidth); a degenerate cloud (all
    particles at one point) puts all probability in that cell."""
    from scipy.stats import gaussian_kde
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    ok = np.isfinite(lon) & np.isfinite(lat)
    w = np.ones(lon.size) if weights is None else np.asarray(weights, float)
    lon, lat, w = lon[ok], lat[ok], w[ok]
    if w.sum() <= 0:
        raise ValueError("all particle weights are zero")
    w = w / w.sum()
    lon0, lat0 = float(np.average(lon, weights=w)), float(np.average(lat, weights=w))
    kx = KM_PER_DEG * np.cos(np.radians(lat0))
    x, y = (lon - lon0) * kx, (lat - lat0) * KM_PER_DEG
    kde = None
    if lon.size >= 5 and np.std(x) > 1e-6 and np.std(y) > 1e-6:
        try:
            kde = gaussian_kde(np.vstack([x, y]), weights=w)
        except np.linalg.LinAlgError:
            kde = None
    pad = 3 * np.sqrt(np.diag(kde.covariance)) if kde is not None else np.array([cell_km, cell_km])
    x0, x1 = x.min() - pad[0], x.max() + pad[0]
    y0, y1 = y.min() - pad[1], y.max() + pad[1]
    nx, ny = int(np.ceil((x1 - x0) / cell_km)), int(np.ceil((y1 - y0) / cell_km))
    if nx * ny > max_cells:
        cell_km = float(np.sqrt((x1 - x0) * (y1 - y0) / max_cells))
        nx, ny = int(np.ceil((x1 - x0) / cell_km)), int(np.ceil((y1 - y0) / cell_km))
    xe = x0 + cell_km * np.arange(nx + 1)
    ye = y0 + cell_km * np.arange(ny + 1)
    g = Grid(lon0, lat0, xe, ye)
    if kde is None:
        p = np.zeros((ny, nx))
        iy, ix = g.cell_of(lon, lat)
        np.add.at(p, (iy[iy >= 0], ix[ix >= 0]), w[iy >= 0])
    else:
        GX, GY = np.meshgrid(g.xc, g.yc)
        p = kde(np.vstack([GX.ravel(), GY.ravel()])).reshape(ny, nx) * g.cell_area
    return g, p / p.sum()


def pod(z, cell_area):
    return 1.0 - np.exp(-np.asarray(z) / cell_area)


def plan_greedy(p, cell_area, effort):
    """Optimal allocation for the exponential law: z_c = A ln(d_c / lam)+, with sum z_c = effort."""
    d = p.ravel() / cell_area
    pos = d > 0
    if effort <= 0 or not pos.any():
        return np.zeros_like(p)
    ld = np.log(d[pos])

    def used(loglam):
        return cell_area * np.clip(ld - loglam, 0, None).sum()

    lo, hi = ld.min() - effort / cell_area - 1, ld.max()       # used(lo) >= effort, used(hi) = 0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if used(mid) > effort:
            lo = mid
        else:
            hi = mid
    z = np.zeros(d.size)
    z[pos] = cell_area * np.clip(ld - hi, 0, None)
    z *= effort / z.sum() if z.sum() > 0 else 0
    return z.reshape(p.shape)


def plan_square(grid, p, effort, side_km):
    """Uniform effort over the cells whose centres lie in a square of side_km centred on the datum (0, 0)."""
    inx = np.abs(grid.xc) <= side_km / 2
    iny = np.abs(grid.yc) <= side_km / 2
    mask = np.outer(iny, inx)
    z = np.zeros_like(p)
    if mask.sum():
        z[mask] = effort / mask.sum()
    return z


def plan_iamsar(grid, p, effort, sides=SQUARE_SIDES_KM):
    """The square (from `sides`) with the highest predicted POS. Returns (z, side_km)."""
    best = None
    for s in sides:
        z = plan_square(grid, p, effort, s)
        v = float((p * pod(z, grid.cell_area)).sum())
        if best is None or v > best[0]:
            best = (v, z, s)
    return best[1], best[2]


def plan(lon, lat, weights=None, effort=None, planner="greedy"):
    """Returns a dict: grid, p, z (effort per cell), pod grid, predicted POS, square side (iamsar only)."""
    effort = effort_km2() if effort is None else effort
    g, p = probability_grid(lon, lat, weights)
    side = None
    if planner == "greedy":
        z = plan_greedy(p, g.cell_area, effort)
    elif planner == "iamsar":
        z, side = plan_iamsar(g, p, effort)
    else:
        raise ValueError("planner must be greedy or iamsar")
    pg = pod(z, g.cell_area)
    return {"grid": g, "p": p, "z": z, "pod": pg, "pos_predicted": float((p * pg).sum()), "side_km": side,
            "effort_km2": effort, "area_searched_km2": float((z > 0).sum() * g.cell_area)}


def pod_at(result, lon, lat):
    """POD of the plan at a point (e.g. the true position): the probability the plan would have found it there."""
    iy, ix = result["grid"].cell_of(np.atleast_1d(lon), np.atleast_1d(lat))
    return np.where(iy >= 0, result["pod"][np.clip(iy, 0, None), np.clip(ix, 0, None)], 0.0)


def bayes_update(weights, result, lon, lat):
    """After an unsuccessful search: w_i <- w_i (1 - POD at particle i), renormalized. Returns (w, prob_not_found)."""
    w0 = np.asarray(weights, float)
    w = w0 / w0.sum() * (1.0 - pod_at(result, lon, lat))
    miss = w.sum()                                   # predicted chance the object was missed (particle estimate)
    if miss <= 0:
        raise ValueError("the search covered every particle with POD 1")
    return w / miss, float(miss)
