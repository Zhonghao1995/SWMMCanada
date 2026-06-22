"""Per-city real-network adapters + the shared `base` assembler (ADR 0004/0005/0006).

Each city module turns its municipal open data into canonical `RawPipe`s + outfall/ground
points; `base.assemble_network` does the city-agnostic SWMM assembly (coordinate-snapping
topology, node inverts, single-link outfalls), so a new city is mostly a thin field mapping.
"""
