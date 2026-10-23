"""The physics of one cell, written once in plain scalar Python and compiled twice: by numba for the CPU (parallel
loops) and by numba.cuda for the GPU (one thread per cell).

Arrays: m, hext, out have shape (3, S, Nz, Ny, Nx) (S = 1 or 2 sublattices); alpha (Ny, Nx); mask (Nz, Ny, Nx) uint8.
G (int64) and P (float64) hold the grid and the material; their slots are named in layout.py."""


def make(jit):
    """The cell functions compiled with `jit` (numba.njit or numba.cuda.jit(device=True))."""

    @jit
    def neighbour(m, mask, G, P, s, k, j, i, dk, dj, di, ghost):
        Nx, Ny, Nz = G[0], G[1], G[2]
        kk, jj, ii = k + dk, j + dj, i + di
        miss = False
        if ii < 0 or ii >= Nx:
            if G[4] == 0:
                ii = (ii + Nx) % Nx
            elif G[4] == 2:
                return 0.0, 0.0, 1.0
            else:
                miss = True
        if jj < 0 or jj >= Ny:
            if G[5] == 0:
                jj = (jj + Ny) % Ny
            elif G[5] == 2:
                return 0.0, 0.0, 1.0
            else:
                miss = True
        if kk < 0 or kk >= Nz:
            if G[6] == 0:
                kk = (kk + Nz) % Nz
            elif G[6] == 2:
                return 0.0, 0.0, 1.0
            else:
                miss = True
        if not miss and G[10] == 1:
            if mask[kk, jj, ii] == 0:
                miss = True
        if miss:
            mx, my, mz = m[0, s, k, j, i], m[1, s, k, j, i], m[2, s, k, j, i]
            if ghost and G[7] == 1 and P[4] != 0.0 and dk == 0:
                # free edge with DMI: m + dx dm/dn (2A dm/dn = -D (mz n - (n.m) z) interfacial, D n x m bulk)
                q = P[4]
                nx = float(di)
                ny = float(dj)
                if G[8] == 1:
                    return mx - q * mz * nx, my - q * mz * ny, mz + q * (nx * mx + ny * my)
                return mx + q * ny * mz, my - q * nx * mz, mz + q * (nx * my - ny * mx)
            return mx, my, mz
        return m[0, s, kk, jj, ii], m[1, s, kk, jj, ii], m[2, s, kk, jj, ii]

    @jit
    def field(m, hext, mask, G, P, s, k, j, i):
        """Effective field (A/m): exchange, uniaxial anisotropy along z, local demag, DMI, AFM coupling, external."""
        mx, my, mz = m[0, s, k, j, i], m[1, s, k, j, i], m[2, s, k, j, i]
        if G[10] == 0 and i > 0 and i < G[0] - 1 and j > 0 and j < G[1] - 1:     # inside: plain neighbours
            xp0, xp1, xp2 = m[0, s, k, j, i + 1], m[1, s, k, j, i + 1], m[2, s, k, j, i + 1]
            xm0, xm1, xm2 = m[0, s, k, j, i - 1], m[1, s, k, j, i - 1], m[2, s, k, j, i - 1]
            yp0, yp1, yp2 = m[0, s, k, j + 1, i], m[1, s, k, j + 1, i], m[2, s, k, j + 1, i]
            ym0, ym1, ym2 = m[0, s, k, j - 1, i], m[1, s, k, j - 1, i], m[2, s, k, j - 1, i]
        else:
            xp0, xp1, xp2 = neighbour(m, mask, G, P, s, k, j, i, 0, 0, 1, True)
            xm0, xm1, xm2 = neighbour(m, mask, G, P, s, k, j, i, 0, 0, -1, True)
            yp0, yp1, yp2 = neighbour(m, mask, G, P, s, k, j, i, 0, 1, 0, True)
            ym0, ym1, ym2 = neighbour(m, mask, G, P, s, k, j, i, 0, -1, 0, True)
        if G[2] == 1 and G[6] == 0:                     # one periodic layer: no z neighbours
            zp0, zp1, zp2 = mx, my, mz
            zm0, zm1, zm2 = mx, my, mz
        else:
            zp0, zp1, zp2 = neighbour(m, mask, G, P, s, k, j, i, 1, 0, 0, True)
            zm0, zm1, zm2 = neighbour(m, mask, G, P, s, k, j, i, -1, 0, 0, True)
        c = P[0]
        hx = c * (xp0 + xm0 + yp0 + ym0 + zp0 + zm0 - 6.0 * mx)
        hy = c * (xp1 + xm1 + yp1 + ym1 + zp1 + zm1 - 6.0 * my)
        hz = c * (xp2 + xm2 + yp2 + ym2 + zp2 + zm2 - 6.0 * mz)
        hz += P[1] * mz
        if G[9] == 1:
            hz -= P[2] * mz
        d = P[3]                                        # 2D / (mu0 Ms) / (2 dx): central differences
        if G[8] == 1:                                   # interfacial: E = D [mz div m - (m.grad) mz]
            hx += d * (xp2 - xm2)
            hy += d * (yp2 - ym2)
            hz -= d * ((xp0 - xm0) + (yp1 - ym1))
        elif G[8] == 2:                                 # bulk: E = D m.(curl m)
            hx -= d * ((yp2 - ym2) - (zp1 - zm1))
            hy -= d * ((zp0 - zm0) - (xp2 - xm2))
            hz -= d * ((xp1 - xm1) - (yp0 - ym0))
        if G[3] == 2:                                   # two sublattices: -J m_other / (mu0 Ms)
            o = 1 - s
            hx -= P[5] * m[0, o, k, j, i]
            hy -= P[5] * m[1, o, k, j, i]
            hz -= P[5] * m[2, o, k, j, i]
        return hx + hext[0, s, k, j, i], hy + hext[1, s, k, j, i], hz + hext[2, s, k, j, i]

    @jit
    def rhs(m, hext, alpha, mask, G, P, s, k, j, i):
        """dm/dt of the LLG equation with the spin torques, in Gilbert form."""
        if G[10] == 1:
            if mask[k, j, i] == 0:
                return 0.0, 0.0, 0.0
        mx, my, mz = m[0, s, k, j, i], m[1, s, k, j, i], m[2, s, k, j, i]
        hx, hy, hz = field(m, hext, mask, G, P, s, k, j, i)
        g = P[6]
        tx = -g * (my * hz - mz * hy)
        ty = -g * (mz * hx - mx * hz)
        tz = -g * (mx * hy - my * hx)
        if G[11] == 1:                                  # Zhang-Li STT: -(u.grad)m + beta m x (u.grad)m
            a0, a1, a2 = neighbour(m, mask, G, P, s, k, j, i, 0, 0, 1, False)
            b0, b1, b2 = neighbour(m, mask, G, P, s, k, j, i, 0, 0, -1, False)
            c0, c1, c2 = neighbour(m, mask, G, P, s, k, j, i, 0, 1, 0, False)
            e0, e1, e2 = neighbour(m, mask, G, P, s, k, j, i, 0, -1, 0, False)
            u0, u1, beta = P[10], P[11], P[12]          # u / (2 dx)
            ux = u0 * (a0 - b0) + u1 * (c0 - e0)
            uy = u0 * (a1 - b1) + u1 * (c1 - e1)
            uz = u0 * (a2 - b2) + u1 * (c2 - e2)
            tx += -ux + beta * (my * uz - mz * uy)
            ty += -uy + beta * (mz * ux - mx * uz)
            tz += -uz + beta * (mx * uy - my * ux)
        if G[12] == 1:                                  # damping-like SOT: -gamma0 H_DL m x (m x sigma)
            sx, sy, sz = P[8], P[9], P[13]
            px = my * sz - mz * sy
            py = mz * sx - mx * sz
            pz = mx * sy - my * sx
            hd = g * P[7]
            tx -= hd * (my * pz - mz * py)
            ty -= hd * (mz * px - mx * pz)
            tz -= hd * (mx * py - my * px)
        a = alpha[j, i]
        f = 1.0 / (1.0 + a * a)
        return ((tx + a * (my * tz - mz * ty)) * f, (ty + a * (mz * tx - mx * tz)) * f,
                (tz + a * (mx * ty - my * tx)) * f)

    @jit
    def descent(m, hext, mask, G, P, s, k, j, i):
        """m x (m x H) (the minimiser goes along minus this) and the largest component of |m x H|."""
        if G[10] == 1:
            if mask[k, j, i] == 0:
                return 0.0, 0.0, 0.0, 0.0
        mx, my, mz = m[0, s, k, j, i], m[1, s, k, j, i], m[2, s, k, j, i]
        hx, hy, hz = field(m, hext, mask, G, P, s, k, j, i)
        cx = my * hz - mz * hy
        cy = mz * hx - mx * hz
        cz = mx * hy - my * hx
        t = max(abs(cx), max(abs(cy), abs(cz)))
        return my * cz - mz * cy, mz * cx - mx * cz, mx * cy - my * cx, t

    return neighbour, field, rhs, descent
