"""Ready material parameters for the micromagnetic engines: --material=FeGe sets Msat, Aex, Ku, D and the kind of
DMI, and suggests a cell size, a grid, a field and a seed radius that give a skyrmion; any line the user writes
wins over the material."""

MU0 = 4e-7 * 3.141592653589793

MATERIALS = {
    "Pt/Co": {
        "title": "Pt/Co ultrathin film (interfacial DMI, Néel skyrmions)",
        "params": {"Msat": 580e3, "Aex": 15e-12, "Ku": 0.8e6, "D": 3.0e-3, "dmi": "interfacial", "alpha": 0.3},
        "suggest": {"thickness": 0.4e-9, "dx": 1e-9, "Nx": 128, "Ny": 128, "R0": 10e-9},
        "note": "0.4 nm of Co on Pt; skyrmions at zero field",
        "ref": "Sampaio et al., Nature Nanotechnology 8, 839 (2013)"},
    "Co/Pd": {
        "title": "[Co/Pd] multilayer (interfacial DMI, Néel skyrmions)",
        "params": {"Msat": 880e3, "Aex": 10e-12, "Ku": 0.24e6 + 0.5 * MU0 * 880e3 ** 2, "D": 2.0e-3,
                   "dmi": "interfacial", "alpha": 0.5},
        "suggest": {"thickness": 6.7e-9, "dx": 2e-9, "Nx": 128, "Ny": 128, "R0": 30e-9, "demag": "full",
                    "B_ext": (0.0, 0.0, 0.1)},
        "note": "Pt/Co/[Pd/Co]x5, 6.7 nm magnetic thickness, K_eff = 0.24 MJ/m^3; with the long-range demag a "
                "skyrmion of R = 28 nm at 0.1 T",
        "ref": "Pollard et al., Nature Communications 8, 14761 (2017)"},
    "FeGe": {
        "title": "FeGe, B20 chiral magnet (bulk DMI, Bloch skyrmions)",
        "params": {"Msat": 384e3, "Aex": 8.78e-12, "Ku": 0.0, "D": 1.58e-3, "dmi": "bulk", "alpha": 0.3},
        "suggest": {"thickness": 2e-9, "dx": 2e-9, "Nx": 128, "Ny": 128, "R0": 25e-9, "B_ext": (0.0, 0.0, 0.37)},
        "suggest3d": {"dx": 3e-9, "Nx": 50, "Ny": 50, "Nz": 24, "disk_diameter": 144e-9, "B_ext": (0.0, 0.0, 0.04)},
        "note": "helical period 4 pi A / D = 70 nm; a skyrmion of R = 37 nm at 0.37 T (half of B_D = D^2 / A Ms) "
                "in a thin film; mm3d: hopfions and bobbers need about one period of thickness",
        "ref": "Beg et al., Scientific Reports 5, 17137 (2015)"},
    "MnSi": {
        "title": "MnSi, B20 chiral magnet at low temperature (bulk DMI)",
        "params": {"Msat": 163e3, "Aex": 0.40e-12, "Ku": 0.0, "D": 0.28e-3, "dmi": "bulk", "alpha": 0.1},
        "suggest": {"thickness": 0.75e-9, "dx": 0.75e-9, "Nx": 128, "Ny": 128, "R0": 6e-9, "B_ext": (0.0, 0.0, 0.36)},
        "suggest3d": {"dx": 0.75e-9, "Nx": 52, "Ny": 52, "Nz": 24, "disk_diameter": 36e-9, "B_ext": (0.0, 0.0, 0.065)},
        "note": "A and D follow from the helical period of 18 nm and the critical field of about 0.6 T of MnSi at "
                "low temperature; a skyrmion of R = 7.7 nm at 0.36 T",
        "ref": "Mühlbauer et al., Science 323, 915 (2009) (period, phase diagram); values derived here"},
    "YIG": {
        "title": "YIG, yttrium iron garnet (low damping, spin waves)",
        "params": {"Msat": 140e3, "Aex": 3.7e-12, "Ku": 0.0, "D": 0.0, "alpha": 2e-4},
        "suggest": {"thickness": 20e-9, "dx": 5e-9, "texture": "uniform", "B_ext": (0.0, 0.0, 0.3)},
        "note": "no DMI, no skyrmions; for spin waves magnetise the film out of plane with B_z above "
                "mu0 Ms = 0.18 T (forward volume waves)",
        "ref": "Klingler et al., J. Phys. D 48, 015001 (2015) (A = 3.7 pJ/m)"},
}
NAMES = tuple(MATERIALS)
ALIASES = {k.lower().replace("/", "").replace("-", ""): k for k in MATERIALS}


def find(name):
    """The material for a user's spelling (pt/co, PtCo, fege ...), or None."""
    return MATERIALS.get(name) and name or ALIASES.get(name.lower().replace("/", "").replace("-", ""))


def lines(name, given, allowed, engine="mm2d"):
    """Parameter lines for `name`, leaving out the names in `given` and those an engine does not know."""
    mat = MATERIALS[find(name)]
    out = []
    suggest = mat["suggest"] if engine == "mm2d" else mat.get("suggest3d", {})
    for k, v in {**suggest, **mat["params"]}.items():
        if k in given or k not in allowed:
            continue
        out.append(f"--{k}=" + (",".join(repr(float(c)) for c in v) if isinstance(v, tuple) else
                                (v if isinstance(v, str) else repr(v))))
    return out


def table():
    """For list_engines and the guide: name, title, values, note, reference."""
    return [{"name": k, **{x: v[x] for x in ("title", "params", "suggest", "note", "ref")}} for k, v in MATERIALS.items()]
