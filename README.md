# StavyaArora_MarissaTill_SAR_Project

AM/ME/CE 295: Agentic AI in Scientific Discovery, Fall 2026. 

Status: setup phase.

### DRAFT Project Proposal
Maritime search-and-rescue drift forecasts increasingly fuse sparse real-time sensor data with model output, but recent reviews find that these prediction improvements are rarely tested beyond trajectory error, so it remains unknown whether they lead to better search decisions. This project develops an LLM-based agent that ingests and reconciles messy ocean, atmospheric and sensor feeds, blends sparse HF-radar surface currents with ocean-model currents, runs Monte Carlo particle simulations in OpenDrift, and converts the results into probability-of-containment maps for allocating search effort. Using held-out drifter trajectories as ground truth, the agent runs a 2×2 experiment (model-only vs. HF-radar-fused currents, crossed with a standard IAMSAR-style planner vs. a planner that targets the highest-probability areas) and scores each combination on trajectory error, probability-map calibration, search-plan quality and simulated detection outcomes. The agent is compared with a conventional scripted pipeline on task completion, silent-error rate under injected data faults, and runtime and cost, and the project delivers a reproducible benchmark along with evidence on whether sensor fusion leads to better search plans.

## Tools and data
- OpenDrift (Leeway model) for drift simulation.
- HYCOM ocean currents (OPeNDAP/THREDDS) and a wind source [TBD].
- Global Drifter Program hourly tracks as real-world ground truth.
- Python 3.12 in a uv-managed .venv; dependencies in requirements.txt.