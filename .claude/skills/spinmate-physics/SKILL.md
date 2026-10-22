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
- mm2d (built in): SpinMate's own 2D micromagnetics of a ferromagnetic film one cell thick (CPU). Exchange
  Aex, perpendicular anisotropy Ku, DMI D (dmi=interfacial gives Neel, dmi=bulk Bloch textures), Zeeman B_ext and
  the local thin-film demagnetising field -Ms mz, so the effective anisotropy is Keff = Ku - mu0 Ms^2/2.
  demag=full (lattice=ferro) replaces it by the long-range field: the Newell tensor of a cell `thickness` thick,
  convolved by FFT (free edges zero-padded; periodic films sum images and use the exact infinite-film k = 0 term).
  Use it for films thicker than about two exchange lengths, for bubbles (D = 0, the walls turn Bloch; the summary
  calls them bubble) and for stripe or maze domains from texture=random, which the local field cannot give. It makes
  skyrmions larger (1 nm Co-like film: 14.5 -> about 17 nm). Checked against the Aharoni prism factor and the extra
  stray-field energy of a Neel wall, mu0 Ms^2 d ln2 / pi. Still one cell through the thickness: m does not vary along z.
  mode=relax lowers the energy from a seed (texture=skyrmion, skyrmionium, domain_wall, uniform, random) to the
  nearest stable state; mode=dynamics then solves LLG in time, with an optional uniform pulse B_pulse for t_pulse.
  The summary gives texture, topological_charge Q (skyrmion: |Q| = 1, skyrmionium: 0), radius_nm, rings_nm
  (where mz = 0 along the centre line), energies and, in dynamics, peak_frequency_mx_GHz. Scales: wall width
  sqrt(A/Keff), wall energy 4 sqrt(A Keff) - pi |D|; above D_c = 4 sqrt(A Keff)/pi the uniform state breaks into
  stripes. The pictures texture_3d.png and texture_2d.png show the final state.
  Spin waves: mode=dynamics with excite=stripe or dot, pulse=sine at f_rf (summary: wavelength_nm and
  wavelength_theory_nm) or pulse=sinc up to f_rf (dispersion.png, dispersion_mismatch_pct); set absorb to about
  two wavelengths. For a film along +z: f(k) = gamma0/2pi (H + 2Keff/(mu0 Ms) + 2A k^2/(mu0 Ms)), no waves below
  the gap spin_wave_gap_GHz.
  Skyrmion motion: mode=dynamics with a current j (A/m^2, in the plane) and either stt_P (Zhang-Li STT, drift
  u = P muB j/(e Ms), non-adiabaticity stt_beta) or theta_SH (damping-like SOT from a heavy metal, H_DL =
  hbar theta_SH |j|/(2 e mu0 Ms d), polarisation z x j). The summary gives velocity_mps, speed_mps, hall_angle_deg
  (angle between v and j) and thiele_velocity_mps, the Thiele prediction for the relaxed texture. With STT and
  stt_beta = alpha the texture moves with u exactly; the skyrmion Hall angle (deflection of the path from the
  current axis) grows as the damping falls.
  Antiferromagnetic skyrmions: lattice=afm (intrinsic compensated antiferromagnet: two sublattices coupled by
  J_afm m1.m2 in J/m^3, no demagnetising field) or lattice=saf (synthetic antiferromagnet: two ferromagnetic layers
  coupled through a spacer by RKKY, energy -J_rkky m1.m2 per area with J_rkky < 0, each layer with its thin-film
  field). The skyrmions in m1 and m2 have opposite charges (sublattice_charges -1, +1), so their Magnus forces cancel:
  under a current they move along the current axis without a skyrmion Hall effect, and faster than in a single
  ferromagnetic layer. The pictures show the Neel vector n = (m1 - m2)/2 with m1 (red) and m2 (blue) arrows.
  Temperature: Temp (K) in mode=dynamics adds the stochastic thermal field (Brown), integrated with fixed Heun steps
  (thermal_dt, default 0.15 / the fastest precession), so it is slower than at 0 K: keep thermal runs to about
  64 x 64 cells and 1 ns (some minutes). For a localised texture without current the summary gives
  diffusion_m2ps from the mean squared displacement and diffusion_thiele_m2ps from the stochastic Thiele
  equation; one trajectory of 1 ns has a statistical error of roughly 25 %. mean_mz_thermal is the time average of
  <m_z> after the first fifth of the run.
  Relaxation (mode=relax) is an energy minimisation and is always at 0 K; Temp has no effect there. When the user
  asks for a temperature, use mode=dynamics with Temp (it relaxes at 0 K first, then evolves with the thermal field)
  and say so. start_from=rXXXX starts a run from the final texture of an earlier mm2d run (same Nx, Ny and lattice):
  use it to heat, push or excite a texture that is already relaxed instead of seeding a new one.
  Experimentally, room-temperature skyrmions with compensated moments are seen in synthetic antiferromagnets
  (e.g. Pt/Co/Ru multilayers) and ferrimagnets; in intrinsic antiferromagnets only special cases are known.
- mm3d (built in): SpinMate's own 3D micromagnetics of a chiral magnet slab or nanodisk (CPU): exchange, bulk or
  interfacial DMI, anisotropy, Zeeman, no demagnetising field; the top and bottom surfaces pinned along +z
  (surfaces=pinned). It relaxes texture=hopfion or skyrmion_tube and reports hopf_index H (|H| near 1 for a hopfion;
  the grid gives about 0.85-0.9), core_ring_radius_nm and rings_nm in the middle layer. Hopfions are stable in a
  nanodisk (geometry=disk) about 2p across and about p thick, p = 4πA/D, at small fields (0-0.3 T); in a periodic
  slab they unwind or grow into helices. texture_3d_2d.png shows the m_z = 0 torus and the cuts y = 0 and z = 0.
  More 3D textures: texture=chiral_bobber (surfaces=free, geometry=slab, B_z between about 0.35 and 0.5 B_D,
  B_D = D^2/(A Ms), 5.5 T for the defaults): a tube from the top surface ending about p/2 down at a Bloch point.
  texture=toron (surfaces=pinned): a tube closed by Bloch points +1 and -1; it shrinks with field (1-2 T for the
  defaults). texture=hopfion2: two hopfions stacked in a disk about 2p thick (Nz=48), H = 2 (-1.72 on the grid);
  a single torus with a double twist unwinds to H = 1 in this model. The summary gives bloch_points (x, y, z in
  nm and charge, from the solid angle of m over each cube of 8 cells), bloch_point_count and Q_layers, the
  skyrmion number of every layer; texture names: hopfion, hopfion2, skyrmion_tube, chiral_bobber, toron.

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
