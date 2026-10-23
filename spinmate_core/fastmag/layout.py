"""Slots of the G (int) and P (float) arrays the kernels read, and how a film fills them."""
import numpy as np

MU0 = 4e-7 * np.pi
GAMMA0 = 2.211e5
# G: grid and switches
NX, NY, NZ, S, BCX, BCY, BCZ, GHOST, DMI, LOCAL_DEMAG, HAS_MASK, STT, SOT = range(13)
# P: coefficients
C_EX, C_K, MS, C_D, Q_BC, C_J, GAMMA, H_DL, SIG_X, SIG_Y, U0, U1, BETA, SIG_Z = range(14)
BC = {"periodic": 0, "free": 1, "pinned": 2}


def pack(Nx, Ny, Nz, S_, dx, Ms, A, Ku, D, dmi, bc, local_demag, ghost, J=0.0, has_mask=False,
         stt_u=None, stt_beta=0.0, sot_h=0.0, sot_sigma=(0.0, 0.0, 0.0)):
    G = np.zeros(16, np.int64)
    P = np.zeros(16, np.float64)
    G[NX], G[NY], G[NZ], G[S] = Nx, Ny, Nz, S_
    G[BCX], G[BCY], G[BCZ] = (BC[b] for b in bc)
    G[GHOST] = int(ghost)
    G[DMI] = 0 if not D else (1 if dmi == "interfacial" else 2)
    G[LOCAL_DEMAG] = int(local_demag)
    G[HAS_MASK] = int(has_mask)
    P[C_EX] = 2 * A / (MU0 * Ms * dx * dx)
    P[C_K] = 2 * Ku / (MU0 * Ms)
    P[MS] = Ms
    P[C_D] = 2 * D / (MU0 * Ms) / (2 * dx)
    P[Q_BC] = dx * D / (2 * A)
    P[C_J] = J / (MU0 * Ms)
    P[GAMMA] = GAMMA0
    if stt_u is not None and np.any(stt_u):
        G[STT] = 1
        P[U0], P[U1] = stt_u[0] / (2 * dx), stt_u[1] / (2 * dx)
        P[BETA] = stt_beta
    if sot_h:
        G[SOT] = 1
        P[H_DL] = sot_h
        P[SIG_X], P[SIG_Y], P[SIG_Z] = sot_sigma
    return G, P
