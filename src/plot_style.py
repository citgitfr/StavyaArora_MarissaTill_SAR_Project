"""One naming and colour scheme for every figure, so plots read the same way across the report."""

# policy keys used in the code -> what a reader sees
NAMES = {
    "always_fused": "No QC (radar as delivered)",
    "baseline": "Scripted baseline",
    "agent": "Claude agent",
    "always_model_only": "Model only (radar ignored)",
    "clean_radar": "Perfect repair (clean radar)",
    # forcings in the clean reality check
    "model_only": "HYCOM model only",
    "radar+model": "HF radar + HYCOM",
}
COLORS = {
    "always_fused": "#d62728",       # red
    "baseline": "#1f77b4",           # blue
    "agent": "#9467bd",              # purple
    "always_model_only": "#ff7f0e",  # orange
    "clean_radar": "#2ca02c",        # green
    "model_only": "#ff7f0e",
    "radar+model": "#2ca02c",
    "drifter": "black",
}
TITLE_SIZE, SUBTITLE_SIZE, LABEL_SIZE, LEGEND_SIZE = 12, 10, 9, 8


def key(policy):
    """'agent_oct_rep1' -> 'agent'; other keys unchanged."""
    return "agent" if str(policy).startswith("agent") else str(policy)


def name(policy):
    return NAMES.get(key(policy), str(policy))


def color(policy):
    return COLORS.get(key(policy), "0.4")


def case_label(case):
    """'case14' -> 'Case 14'."""
    return f"Case {int(str(case).replace('case', ''))}" if str(case).startswith("case") else str(case)


def decision_text(dec):
    """A decision as a short phrase: 'fused', 'repair: rescale x0.01', 'model only'."""
    if not dec:
        return "no decision"
    ch = dec.get("choice")
    if ch != "repair":
        return {"model_only": "model only", "fused": "use radar as is"}.get(ch, str(ch))
    r = dec.get("repair")
    steps = r if isinstance(r, list) else ([] if r is None else [r])
    parts = []
    for s in steps:
        a, p = s.get("action"), s.get("param")
        if a == "rescale":
            parts.append(f"rescale x{float(p):g}")
        elif a == "shift_time":
            parts.append(f"shift time {float(p):+g} h")
        elif a == "flip_sign":
            parts.append(f"flip sign of {p}")
        elif a == "swap_components":
            parts.append("swap u and v")
        elif a == "mask_fill":
            parts.append(f"mask values >= {float(p):g}")
        else:
            parts.append(str(a))
    return "repair: " + " then ".join(parts)
