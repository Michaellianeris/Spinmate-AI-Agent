# spinmate-mumax3

[mumax3](https://mumax.github.io) (GPU-accelerated micromagnetism) as a SpinMate engine. The plug-in turns
SpinMate parameter lines, or a complete `.mx3` script, into a mumax3 run and converts the mumax3 table into
the files SpinMate shows: ⟨m⟩(t), the run card, exports and figures.

```bash
pip install -e plugins/spinmate-mumax3     # registers the engine "mumax3"
export MUMAX3_BIN=/path/to/mumax3          # when mumax3 is not on the PATH
```

mumax3 itself is not included: install it from the mumax3 releases. It needs an NVIDIA GPU and driver.

## License and credit

- **mumax3** is free software under the GNU GPL v3, with an additional permission to combine it with the
  NVIDIA CUDA libraries. This plug-in only starts the mumax3 program as a separate process and does not
  contain or link its code, so the plug-in, like SpinMate, stays under the MIT License. If you redistribute a
  mumax3 binary (for example inside a Docker image), include its license and give access to its source code.
- **CUDA** and the NVIDIA driver are NVIDIA software under NVIDIA's own license terms.
- **Citation.** If you publish results obtained with mumax3, cite: A. Vansteenkiste, J. Leliaert,
  M. Dvornik, M. Helsen, F. Garcia-Sanchez and B. Van Waeyenberge, *The design and verification of MuMax3*,
  AIP Advances 4, 107133 (2014), https://doi.org/10.1063/1.4899186.
- **Logo.** The mumax3 name and logo belong to the mumax3 authors; SpinMate uses them only to show the
  integration.
