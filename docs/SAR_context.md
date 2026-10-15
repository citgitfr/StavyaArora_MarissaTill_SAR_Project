# SAR Context:

IAMSAR (International Aeronautical Maritime Search and Rescue) divides an incident into five stages: Awareness → Initial Action → Planning → Operations → Conclusion. 


## Step 1: Awareness and alert
Distress sources: a GMDSS digital selective calling alert, an EPIRB/PLB detected by Cospas-Sarsat satellites, a VHF Mayday, a report of an overdue vessel, or a witness sighting.
Incident phase: The Rescue Coordination Centre (RCC) classifies it as Uncertainty, Alert or Distress (INCERFA / ALERFA / DETRESFA). The phase determines how many resources are committed.
SAR Mission Coordinator (SMC): appointed to run the case.

## Step 2: Initial action and information gathering
The SMC collects the inputs the drift model will need:
Last known position (LKP) and its time, plus how it was obtained (GPS, dead reckoning, radar, a witness). The method sets the initial position error (X).
What is drifting: a person in the water (with or without a lifejacket), a life raft (with or without a drogue), a swamped skiff, and so on. This chooses the leeway category, the most important physical parameter in the model.
Number of people, survival equipment and water temperature. These drive survival-time estimates, which later decide when to stop searching.
Uncertainty in the distress time. If the vessel was simply overdue, the drift start time may be uncertain by hours.

## Step 3: Building scenarios and the initial datum
Datum: the most probable location of the search object at a given time. It can be a point, a line or an area.

## Step 4: Drift prediction
Forcing data: wind forecasts or analyses from numerical weather prediction, and currents from ocean models (HYCOM, GLORYS/CMEMS, or regional and tidal models). HF radar or drifter data are used where available.
Particle seeding: thousands of particles are placed according to the scenario's spatial and temporal uncertainty. Each particle gets its own perturbed leeway coefficients.
Integration: particles are moved forward from the distress time to the commence-search time (CST), and usually through the planned search period.
Backtracking (BAKTRAK): If debris or a body is found and the origin is unknown, backtracking runs the forward model repeatedly to infer the start location.

## Step 5: Probability map and search area
Particles are binned into a POC grid, or summarized with a datum, divergence and probable-error circle in the manual method.

## Step 6: Effort allocation and the Search Action Plan
Available search and rescue units (SRUs): each has a speed, endurance, transit time and sensor.
Planning steps: compute sweep width, choose track spacing, size the search area, allocate areas to SRUs, and choose a pattern for each.
Search Action Plan: a message that gives each SRU its area corners, pattern, commence search point (CSP), track spacing, creep direction, altitude, time on scene and reporting duties.

## Step 7: Execute, update and replan
Report effort: SRUs report the tracks they actually flew or sailed, weather and visibility, and any sightings.
Unsuccessful search as information: a search that finds nothing is treated as data. Probabilities in searched areas are lowered with Bayes' rule.
Re-run: the drift model runs again with newer forcing data. A new plan is issued for the next operational period, such as the next daylight window.
Repeat until: the object is found, the cumulative probability of success (POS) is high enough, or the estimated survival time is exceeded. At that point the SMC recommends suspending the search, which is the Conclusion stage.
