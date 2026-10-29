"""Forcing tools for the SAR agent: qc, compare, repair and check HF-radar and model surface currents.

Every command prints one JSON object that includes its input file names. numpy/xarray only
(xarray's interp uses scipy). Thresholds come only from configs/thresholds.yaml; there is no
command-line override.

  python .claude/skills/forcing-tools/forcing_tools.py qc --file F --source hfr|model
  python .claude/skills/forcing-tools/forcing_tools.py compare --hfr F --model F
  python .claude/skills/forcing-tools/forcing_tools.py repair --file F --action A [--param X] --out runs/<case>/F
  python .claude/skills/forcing-tools/forcing_tools.py check --hfr F --model F
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import xarray as xr

REPO = Path(__file__).resolve().parents[3]
THRESHOLDS = REPO / "configs" / "thresholds.yaml"
RUNS_DIR = REPO / "runs"
MAX_REPAIRS = 2                      # per case directory; the 3rd is refused
LAGS_H = range(-6, 7)
ACTIONS = ("rescale", "shift_time", "flip_sign", "swap_components", "mask_fill")
TODO = "TODO"
REQUIRED = ("date", "speed.max_m_s", "time.max_gap_s", "qc.fill_values",
            "coverage.min_hour_fraction", "coverage.min_good_hours_fraction",
            "speed_ratio.min", "speed_ratio.max", "correlation.min", "lag.max_abs_hours")
STD_NAMES = {"hfr": ("surface_eastward_sea_water_velocity", "surface_northward_sea_water_velocity"),
             "model": ("eastward_sea_water_velocity", "northward_sea_water_velocity")}
_MS = ("m s-1", "m/s", "m s^-1", "m s**-1", "m.s-1", "ms-1", "meter second-1", "meters second-1",
       "metre second-1", "metres second-1", "meters/second", "meters per second")
_CMS = ("cm s-1", "cm/s", "cm s^-1", "cm s**-1", "cm.s-1", "cms-1", "centimeter second-1",
        "centimeters second-1", "centimetre second-1", "centimetres second-1", "centimeters/second",
        "centimeters per second")
UNITS = {**dict.fromkeys(_MS, ("m s-1", 1.0)), **dict.fromkeys(_CMS, ("cm s-1", 0.01))}


class ToolError(Exception):
    pass


# ---- thresholds ------------------------------------------------------------------------------
def _parse_value(s):
    s = s.strip()
    if s == TODO:
        return TODO
    if s.startswith("[") and s.endswith("]"):
        return [_parse_value(x) for x in s[1:-1].split(",") if x.strip()]
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            pass
    return s.strip("'\"")


def load_thresholds(path=None):
    """Read the two-level YAML subset used by configs/thresholds.yaml into {"section.key": value}."""
    path = Path(path or THRESHOLDS)
    if not path.exists():
        raise ToolError(f"{path} not found")
    out, section = {}, None
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        key, sep, val = line.strip().partition(":")
        if not sep:
            raise ToolError(f"{path}:{n}: expected 'key: value'")
        if raw[0] in " \t":
            if section is None:
                raise ToolError(f"{path}:{n}: indented key outside a section")
            out[f"{section}.{key.strip()}"] = _parse_value(val)
        elif val.strip() == "":
            section = key.strip()
        else:
            section = None
            out[key.strip()] = _parse_value(val)
    return out


def require(thr, keys=REQUIRED):
    missing = [k for k in keys if k not in thr or thr[k] == TODO]
    if missing:
        raise ToolError("thresholds not set (TODO or missing) in configs/thresholds.yaml: " + ", ".join(missing))


def _fill_values_from_config():
    try:
        v = load_thresholds().get("qc.fill_values", TODO)
    except ToolError:
        return None
    return None if v == TODO else (v if isinstance(v, list) else [v])


# ---- loading ---------------------------------------------------------------------------------
def normalize_units(s):
    """Return (canonical, factor_to_m_s) for a velocity unit string, or None if not recognized."""
    if s is None:
        return None
    return UNITS.get(" ".join(str(s).strip().lower().split()))


def _open(path):
    with xr.open_dataset(path) as ds:
        missing = [v for v in ("u", "v", "time", "lat", "lon") if v not in ds.variables]
        if missing:
            raise ToolError(f"{path}: missing variables {missing}")
        return ds[["u", "v"]].transpose("time", "lat", "lon").load()


def _units(ds):
    """(units_attr, units_normalized, factor_to_m_s, flags) from u; v must agree."""
    ua, va = ds["u"].attrs.get("units"), ds["v"].attrs.get("units")
    nu, nv = normalize_units(ua), normalize_units(va)
    flags = []
    if nu is None:
        flags.append("units_unrecognized")
    if nu != nv:
        flags.append("units_u_v_differ")
    return ua, (nu[0] if nu else None), (nu[1] if nu else None), flags


def _steps_s(ds):
    return np.diff(ds["time"].values).astype("timedelta64[s]").astype(np.int64)


def _corr(a, b):
    if a.size < 2 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _mean(*xs):
    return None if any(x is None for x in xs) else float(np.mean(xs))


def _clean(x):
    """JSON-safe: numpy -> python, NaN/inf -> null, floats to 4 significant figures."""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(f"{x:.4g}") if math.isfinite(x) else None
    return x


# ---- qc --------------------------------------------------------------------------------------
def qc(path, source, fill_values=None):
    """Describe one file. Speeds are in m/s when units are recognized, otherwise in file units."""
    if source not in STD_NAMES:
        raise ToolError(f"source must be one of {list(STD_NAMES)}")
    ds = _open(path)
    units_attr, units_norm, factor, flags = _units(ds)
    if (ds["u"].attrs.get("standard_name"), ds["v"].attrs.get("standard_name")) != STD_NAMES[source]:
        flags.append("standard_name_unexpected")

    dt = _steps_s(ds)
    step = int(np.median(dt)) if dt.size else None
    gap = int(dt.max()) if dt.size else None
    if (dt <= 0).any():
        flags.append("time_not_increasing")
    if dt.size and gap > step:
        flags.append("time_step_irregular")

    u, v = ds["u"].values, ds["v"].values
    ok = np.isfinite(u) & np.isfinite(v)
    sp = np.hypot(u, v)[ok] * (factor or 1.0)
    if not sp.size:
        flags.append("no_valid_data")
    if fill_values is None:
        hits = None
        flags.append("fill_values_not_configured")
    else:
        hits = int((np.isin(u, fill_values) | np.isin(v, fill_values)).sum())
        if hits:
            flags.append("fill_values_present")

    return {"tool": "qc", "file": str(path), "source": source,
            "units_attr": units_attr, "units_normalized": units_norm,
            "n_time": int(ds.sizes["time"]), "time_step_s": step, "largest_gap_s": gap,
            "valid_fraction": float(ok.mean()),
            "speed_median": float(np.median(sp)) if sp.size else None,
            "speed_p95": float(np.percentile(sp, 95)) if sp.size else None,
            "speed_max": float(sp.max()) if sp.size else None,
            "fill_value_hits": hits, "flags": flags}


# ---- compare ---------------------------------------------------------------------------------
def _model_at(m, h, lag_h):
    """Model u, v (m/s) interpolated to the HFR grid at HFR times minus lag_h hours."""
    t = h["time"].values - np.timedelta64(lag_h, "h")
    mi = m.interp(time=t, lat=h["lat"].values, lon=h["lon"].values)
    return mi["u"].values, mi["v"].values


def compare(hfr_path, model_path):
    """HFR vs model on the HFR grid and hours. bias = HFR - model.

    best_lag_hours = L that maximizes mean(corr_u, corr_v) of HFR(t) against model(t - L h):
    a positive L means HFR timestamps look L hours late (undo with shift_time --param -L).
    corr_swapped = mean(corr(HFR u, model v), corr(HFR v, model u)) at lag 0.
    speed_ratio_median = median HFR speed / median model speed over the paired points.
    """
    h, m = _open(hfr_path), _open(model_path)
    _, _, fh, hflags = _units(h)
    _, _, fm, mflags = _units(m)
    flags = [f"hfr_{f}" for f in hflags] + [f"model_{f}" for f in mflags]
    hu, hv = h["u"].values * (fh or 1.0), h["v"].values * (fh or 1.0)
    m = m * (fm or 1.0)

    by_lag, lag0 = {}, None
    for lag in LAGS_H:
        mu, mv = _model_at(m, h, lag)
        ok = np.isfinite(hu) & np.isfinite(hv) & np.isfinite(mu) & np.isfinite(mv)
        by_lag[lag] = _mean(_corr(hu[ok], mu[ok]), _corr(hv[ok], mv[ok]))
        if lag == 0:
            lag0 = (mu, mv, ok)
    scored = [lag for lag in by_lag if by_lag[lag] is not None]
    best = max(scored, key=by_lag.get) if scored else None

    mu, mv, ok = lag0
    res = {"tool": "compare", "hfr": str(hfr_path), "model": str(model_path), "n_pairs": int(ok.sum())}
    if not ok.any():
        flags.append("no_overlap")
        res.update(dict.fromkeys(("rms_u", "rms_v", "corr_u", "corr_v", "bias_u", "bias_v",
                                  "speed_ratio_median", "corr_swapped"), None))
        res.update(best_lag_hours=best, corr_by_lag=by_lag, flags=flags)
        return res

    a_u, a_v, b_u, b_v = hu[ok], hv[ok], mu[ok], mv[ok]
    corr_u, corr_v = _corr(a_u, b_u), _corr(a_v, b_v)
    corr_swapped = _mean(_corr(a_u, b_v), _corr(a_v, b_u))
    med_m = np.median(np.hypot(b_u, b_v))
    ratio = float(np.median(np.hypot(a_u, a_v)) / med_m) if med_m > 0 else None
    for name, c in (("corr_u", corr_u), ("corr_v", corr_v)):
        if c is not None and c < 0:
            flags.append(f"negative_{name}")
    direct = _mean(corr_u, corr_v)
    if corr_swapped is not None and direct is not None and corr_swapped > direct:
        flags.append("swapped_corr_exceeds_direct")
    if best not in (0, None):
        flags.append("best_lag_nonzero")

    res.update(rms_u=float(np.sqrt(np.mean((a_u - b_u) ** 2))), rms_v=float(np.sqrt(np.mean((a_v - b_v) ** 2))),
               corr_u=corr_u, corr_v=corr_v,
               bias_u=float(np.mean(a_u - b_u)), bias_v=float(np.mean(a_v - b_v)),
               speed_ratio_median=ratio, best_lag_hours=best, corr_swapped=corr_swapped,
               corr_by_lag=by_lag, flags=flags)
    return res


# ---- repair ----------------------------------------------------------------------------------
def _param(action, param):
    if action not in ACTIONS:
        raise ToolError(f"action must be one of {list(ACTIONS)}")
    if action == "swap_components":
        if param is not None:
            raise ToolError("swap_components takes no --param")
        return None
    if param is None:
        raise ToolError(f"{action} needs --param")
    if action == "flip_sign":
        if param not in ("u", "v"):
            raise ToolError("flip_sign --param must be u or v")
        return param
    try:
        x = float(param)
    except ValueError:
        raise ToolError(f"{action} --param must be a number") from None
    if not math.isfinite(x) or (action == "rescale" and x == 0) or (action == "mask_fill" and x <= 0):
        raise ToolError(f"{action} --param {param} not allowed")
    return x


def repair(path, action, param, out):
    """Write a repaired copy under runs/. The case is out's directory; at most MAX_REPAIRS per case.

    rescale X: u, v *= X. shift_time H: time += H hours. flip_sign u|v: negate that component.
    swap_components: exchange u and v data (names and attributes stay). mask_fill X: set u and v
    to NaN wherever |u| >= X or |v| >= X (file units).
    """
    p = _param(action, param)
    out = Path(out).resolve()
    if RUNS_DIR.resolve() not in out.parents:
        raise ToolError(f"--out must be under {RUNS_DIR}")
    if out.exists():
        raise ToolError(f"{out} exists; not overwriting")
    log = out.parent / "repairs.json"
    done = json.loads(log.read_text(encoding="utf-8")) if log.exists() else []
    if len(done) >= MAX_REPAIRS:
        raise ToolError(f"case {out.parent.name} already has {len(done)} repairs; refusing another")

    with xr.open_dataset(path) as src:
        ds = src.load()
    if "u" not in ds or "v" not in ds:
        raise ToolError(f"{path}: missing u or v")
    extra = {}
    if action == "rescale":
        for c in ("u", "v"):
            ds[c] = ds[c].copy(data=ds[c].values * p)
    elif action == "shift_time":
        shifted = ds["time"].values + np.timedelta64(round(p * 3600), "s")
        ds = ds.assign_coords(time=("time", shifted, ds["time"].attrs))
    elif action == "flip_sign":
        ds[p] = ds[p].copy(data=-ds[p].values)
    elif action == "swap_components":
        u = ds["u"].values.copy()
        ds["u"] = ds["u"].copy(data=ds["v"].values)
        ds["v"] = ds["v"].copy(data=u)
    elif action == "mask_fill":
        with np.errstate(invalid="ignore"):
            bad = (np.abs(ds["u"].values) >= p) | (np.abs(ds["v"].values) >= p)
        for c in ("u", "v"):
            ds[c] = ds[c].copy(data=np.where(bad, np.nan, ds[c].values))
        extra["n_masked"] = int(bad.sum())
    for c in ("u", "v"):
        ds[c].encoding = {}
    step = f"forcing_tools repair {action} {'' if p is None else p}".strip()
    ds.attrs["history"] = (ds.attrs.get("history", "") + "\n" + step).strip()

    out.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(out)
    done.append({"action": action, "param": p, "file": str(path), "out": str(out)})
    log.write_text(json.dumps(done, indent=2), encoding="utf-8")
    return {"tool": "repair", "file": str(path), "out": str(out), "action": action, "param": p,
            "repairs_used": len(done), "repairs_left": MAX_REPAIRS - len(done), **extra}


# ---- check -----------------------------------------------------------------------------------
def _le(a, b):
    return a is not None and a <= b


def check(hfr_path, model_path, thr):
    """Pass/fail per test against the frozen thresholds. Refuses if any threshold is TODO."""
    require(thr)
    fill = thr["qc.fill_values"] if isinstance(thr["qc.fill_values"], list) else [thr["qc.fill_values"]]
    qh, qm = qc(hfr_path, "hfr", fill), qc(model_path, "model", fill)
    c = compare(hfr_path, model_path)
    h, m = _open(hfr_path), _open(model_path)
    tests = {}

    def add(name, ok, **detail):
        tests[name] = {"pass": bool(ok), **detail}

    bad_units = ("units_unrecognized", "units_u_v_differ")
    add("units", not any(f in q["flags"] for q in (qh, qm) for f in bad_units),
        hfr=qh["units_attr"], model=qm["units_attr"])

    add("speed_bound", _le(qh["speed_max"], thr["speed.max_m_s"]),
        speed_max=qh["speed_max"], max_m_s=thr["speed.max_m_s"])

    th, tm = h["time"].values, m["time"].values
    inside = bool(th[0] >= tm[0] and th[-1] <= tm[-1])
    add("time_axis", inside and "time_not_increasing" not in qh["flags"]
        and _le(qh["largest_gap_s"], thr["time.max_gap_s"]),
        largest_gap_s=qh["largest_gap_s"], max_gap_s=thr["time.max_gap_s"], inside_model_times=inside,
        hfr_times=[str(np.datetime_as_string(t, unit="m")) for t in (th[0], th[-1])],
        model_times=[str(np.datetime_as_string(t, unit="m")) for t in (tm[0], tm[-1])])

    add("fill_values", qh["fill_value_hits"] == 0 and qm["fill_value_hits"] == 0,
        hfr_hits=qh["fill_value_hits"], model_hits=qm["fill_value_hits"])

    ocean = xr.DataArray(np.isfinite(m["u"].values).any(axis=0), dims=("lat", "lon"),
                         coords={"lat": m["lat"].values, "lon": m["lon"].values})
    ocean_h = ocean.sel(lat=h["lat"].values, lon=h["lon"].values, method="nearest").values
    n_ocean = int(ocean_h.sum())
    if n_ocean:
        valid = np.isfinite(h["u"].values) & np.isfinite(h["v"].values) & ocean_h[None]
        per_hour = valid.sum(axis=(1, 2)) / n_ocean
        good = float(np.mean(per_hour >= thr["coverage.min_hour_fraction"]))
        cov_mean, cov_min = float(per_hour.mean()), float(per_hour.min())
    else:
        good = cov_mean = cov_min = None
    add("coverage", n_ocean > 0 and good >= thr["coverage.min_good_hours_fraction"],
        ocean_cells=n_ocean, hour_fraction_mean=cov_mean, hour_fraction_min=cov_min,
        good_hours_fraction=good, min_hour_fraction=thr["coverage.min_hour_fraction"],
        min_good_hours_fraction=thr["coverage.min_good_hours_fraction"])

    r = c["speed_ratio_median"]
    add("speed_ratio", r is not None and thr["speed_ratio.min"] <= r <= thr["speed_ratio.max"],
        speed_ratio_median=r, min=thr["speed_ratio.min"], max=thr["speed_ratio.max"])

    cu, cv, cs = c["corr_u"], c["corr_v"], c["corr_swapped"]
    direct = _mean(cu, cv)
    add("correlation_sign", cu is not None and cv is not None and cu >= thr["correlation.min"]
        and cv >= thr["correlation.min"] and cs is not None and cs <= direct,
        corr_u=cu, corr_v=cv, corr_swapped=cs, min=thr["correlation.min"])

    lag = c["best_lag_hours"]
    add("lag", lag is not None and abs(lag) <= thr["lag.max_abs_hours"],
        best_lag_hours=lag, max_abs_hours=thr["lag.max_abs_hours"])

    return {"tool": "check", "hfr": str(hfr_path), "model": str(model_path),
            "thresholds_date": thr["date"], "n_pairs": c["n_pairs"],
            "result": "pass" if all(t["pass"] for t in tests.values()) else "fail", "tests": tests}


# ---- cli -------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("qc")
    a.add_argument("--file", required=True)
    a.add_argument("--source", required=True, choices=list(STD_NAMES))
    for name in ("compare", "check"):
        a = sub.add_parser(name)
        a.add_argument("--hfr", required=True)
        a.add_argument("--model", required=True)
    a = sub.add_parser("repair")
    a.add_argument("--file", required=True)
    a.add_argument("--action", required=True, choices=ACTIONS)
    a.add_argument("--param")
    a.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    code = 0
    try:
        if args.cmd == "qc":
            res = qc(args.file, args.source, _fill_values_from_config())
        elif args.cmd == "compare":
            res = compare(args.hfr, args.model)
        elif args.cmd == "repair":
            res = repair(args.file, args.action, args.param, args.out)
        else:
            res = check(args.hfr, args.model, load_thresholds())
    except (ToolError, OSError, ValueError, KeyError) as e:
        res = {"tool": args.cmd, **{k: v for k, v in vars(args).items() if k != "cmd"}, "error": str(e)}
        code = 2
    print(json.dumps(_clean(res), indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
