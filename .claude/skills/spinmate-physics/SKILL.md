---
name: spinmate-physics
description: Design, run and interpret two-sublattice macrospin simulations with the SpinMate MCP tools. Use when the user asks about antiferromagnet, FeRh or spin-orbit torque dynamics, or about SpinMate runs, sweeps, figures and reports.
---

<!-- generated from spinmate_mcp/physics.md by python -m spinmate_mcp.agent_files; edit that file -->

# SpinMate physics guide

You design, run and interpret simulations with the SpinMate tools (MCP server `spinmate`). The engine is the
original two-sublattice macrospin solver: two coupled LLG equations (sublattices m1 and m2) with
inter-sublattice exchange A0, uniaxial anisotropy Ku, Zeeman field, spin-orbit torque, thermal noise and
time-dependent drives, integrated with Heun steps.

## Engines
- macrospin (default): the two-sublattice model above. mumax3, when installed: finite-difference micromagnetics
  of one ferromagnet on a grid (GPU). Call list_engines to see what is installed and the parameters of each, and
  pass engine="mumax3" to validate_parameters and start_simulation. Sweeps and continue_run are macrospin only.

## Rules
- Report only numbers that come from a tool result or from this guide. If something was not computed, say so
  or run it; never estimate a result you could have simulated.
- Parameters are lines such as `--A0=0.248e-12` or `--t=2e-9`, in SI units except currents (A/cm^2). Write only
  the values that differ from the defaults; `spinmate://parameters` lists them all.
- Sanity check the units: A0=0.248 instead of 0.248e-12, t=2 instead of 2e-9, a current in A/m^2 instead of
  A/cm^2 are almost always slips. Correct them only when the intent is clear and say what you changed.
- Large requests (more than 40 runs or about 10 minutes) return `needs_confirmation`. Give the user the
  estimate, and call the same tool again with `confirmed=true` only after they agree.
- Show the parameter lines you used, so the run can be repeated.

## Workflows
- One run: validate_parameters -> start_simulation -> wait_for_run (timeout_s 120; the result has the summary)
  -> get_run with include_plot=true when the shape of the dynamics matters.
- Sweep: start_sweep -> wait_for_sweep -> get_sweep with include_plot=true -> say where the response changes.
- Longer runs in segments: continue_run on the finished run (time and drive phases continue), then
  wait_for_run, then combine_runs with the last segment for one trajectory. Never restart from copied m1, m2.
- Threshold search ("minimum current that switches", "A0 where AFM becomes FM"): a coarse sweep of 6-8 values
  over a plausible range (use the scales below); read order_final, neel_z_switches and mean |n_z| to find
  where the behaviour changes; then sweep inside that bracket. At most 4 rounds of 10 short runs; stop when
  the bracket is narrower than about 10 % of its centre, and report the bracket and the values tried.
- Several runs: compare_runs; figures: make_figure; write-ups: make_report with your interpretation in notes.
- Questions across earlier runs: query_runs with one read-only SQL query, when it is available.

## Reading the results
- order: from m1·m2; AFM below -0.9, FM above +0.9, otherwise canted.
- neel_z_switches counts real reversals of the Néel vector (n_z from below -0.5 to above +0.5 or back);
  neel_z_sign_changes counts every zero crossing, ringing included: never call those switching.
- Néel switching means something when the run starts and ends AFM; switches in a run that ends FM belong to
  the transient rotation of the pair.
- Many switches, no settle time and a clear frequency mean steady rotation driven by the current (an
  auto-oscillation), not one switching event.
- settle_time_s: time after which m1 and m2 stay within 0.02 of their final values; null means still moving.
- Frequencies are kept only with at least 5 periods in the second half of the run; never quote one the
  summary left null, suggest a longer run instead.

## Scales of the default material
Ms 566 kA/m, |A0| 0.248 pJ/m, Ku 28.4 kJ/m^3, lattice constant l 0.35 nm, damping α 0.05.
- Each sublattice carries Ms/2: exchange field μ0H_E = 4|A0|/(Ms l^2) ≈ 14 T, anisotropy field
  μ0H_K = 4Ku/Ms ≈ 0.2 T.
- AFM <-> FM stability boundary: |Ku| = 2|A0|/l^2. Below it only the order set by the sign of A0 is stable;
  above it both AFM and FM are (meta)stable.
- AFM resonance f = (γ/2π) μ0 √(H_K (2H_E + H_K)) ≈ 67 GHz; FM mode (A0 > 0) f = (γ/2π) μ0 (H_K + H) ≈ 5.6 GHz
  at H = 0, +28 GHz per tesla. These formulas are checked by `spinmate://validation`.
- A tilted AFM state settles in ≈ 20 ps; FM -> AFM in ≈ 50 ps; AFM -> FM switches within ≈ 50 ps, then a
  ≈ 5 GHz precession of the pair decays over about a nanosecond.
- DC SOT with polarisation x: no switching up to 70e6 A/cm^2; at 100e6 A/cm^2 steady rotation, n_z at ≈ 63 GHz.
- Choose durations so a run covers at least 5 periods of what you want to see, and h small enough for the
  fastest mode (the default h = 1e-13 s resolves the AFM mode).

## Answer
Lead with the outcome ("AFM -> FM, switched in 50 ps"), then the numbers that answer the question, the
mechanism (which field or torque dominates and why, using the scales), the caveats that apply and one next
step. Units: ps or ns, GHz, T, A/cm^2, pJ/m for A0.
