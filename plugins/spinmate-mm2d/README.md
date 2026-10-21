# spinmate-mm2d

SpinMate's own 2D micromagnetic solver as a SpinMate engine: a ferromagnetic film one cell thick, finite
differences on a square grid, NumPy on the CPU. No GPU and no other program needed.

```bash
pip install -e plugins/spinmate-mm2d     # registers the engine "mm2d"
```

## What it models

- Exchange `Aex`, perpendicular uniaxial anisotropy `Ku`, Zeeman field `B_ext`.
- DMI `D`: `dmi=interfacial` (Néel walls and skyrmions) or `dmi=bulk` (Bloch).
- The local thin-film demagnetising field −Ms·mz, so the effective anisotropy is Keff = Ku − ½μ0Ms².
- Periodic film or free edges (`boundary=periodic|free`).

## Modes

- `mode=relax`: steepest descent with Barzilai–Borwein steps until max |m × H| < `tol` (A/m), from a seed
  `texture=skyrmion | skyrmionium | domain_wall | uniform | random`.
- `mode=dynamics`: relaxes first (`relax_first=1`), then solves the LLG equation with an adaptive
  Dormand–Prince 5(4) Runge–Kutta solver for `t` seconds, writing ⟨m⟩ every `table_dt`. A uniform pulse
  `B_pulse` during the first `t_pulse` seconds excites the film; the summary gives the main frequency.

## Files of a run

`texture_3d.png` (tilted view, shaded 3D arrows coloured like the film), `texture_2d.png` (top view with nm
axes), `texture_3d_2d.png` (both), `m_final.npz` (m and the cell centres), `output1.dat` (⟨m⟩),
`energy.dat`, `summary.json` (texture, topological charge, radius, rings, energies, frequency).

## Validation

`tests/test_mm2d.py` checks, against analytic results: the domain-wall energy 4√(A·Keff) − π|D| and width
√(A/Keff) for both DMI types and the wall type DMI selects, the Kittel frequency γ0/2π·(H + 2Keff/μ0Ms),
conservation of energy without damping, |m| = 1, and the topological charge of a skyrmion (−1) and of
skyrmionium (0).

## Limits

One cell thick; the demagnetising field is local, so there is no long-range stray field between distant parts
of the film. No temperature yet. Two sublattices (antiferromagnets) and a GPU version are planned.
