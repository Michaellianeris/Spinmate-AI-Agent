import numpy as np
from current import Current

class solver:
	def __init__(self, param): 
		self.param = param
		self.Current = Current()

	def mxx(self, m1, m2, t0): 
		param = self.param
	   
		# Ensure m1, m2 are double precision
		m1 = np.array(m1, dtype=np.float64)
		m2 = np.array(m2, dtype=np.float64)

		# Compute temperature varying parameters
		M_T, J_T, K_T = Current.Temperature_dependence_parameters(self, param.Ms, param.A0, param.Ku, t0)

		# Compute fields
		# Each sublattice carries Ms/2, so H_K = 4 Ku / (mu0 Ms) and H_E = 4 A0 / (mu0 Ms l^2) in units of Ms.
		# Before, the 4 came from adding K twice (ku() returned K when flag2 was off); the field is the same.
		K = np.float64((4 * K_T) / (param.mu0 * M_T**2))
		J = np.float64((4 * J_T) / (param.mu0 * M_T**2 * param.l**2))
		H = np.float64(param.H / (param.mu0 * M_T))

		Sinc_signal = Current.sinc_signal(self, param.Fr, t0)
		H = H + np.float64(param.H_Amp / (param.mu0 * M_T)) * Sinc_signal * param.Hex_AC

		# Compute time-dependent fields
		JJ = np.float64(Current.j(self, J, t0))
		kk = np.float64(Current.ku(self, K, t0))  # only the AC part of Ku(t); 0 when flag2 is off
		HH = np.float64(Current.H(self, H, t0))

		h_ani = K * np.dot(param.u_ani,m1) * param.u_ani + kk * np.dot(param.u_ani_AC,m1) * param.u_ani_AC

		# Compute currents
		Je_Chirp = Current.Je_Chirp(self, t0)
		Je_SOT = Current.Je_SOT(self, t0) 
		Je_SOT += Je_Chirp
		param.SOT_pol = np.array(param.SOT_pol, dtype=np.float64)
		
		# SOT effective field
		h_SOT = Je_SOT* (np.cross(m1,param.SOT_pol) + param.SOT_FL_q*param.SOT_pol)

		# Temperature field
		# Brown thermal field: each component Gaussian with standard deviation Temp_Ampl (isotropic).
		# Before: a unit vector from np.random.rand(3), only positive components.
		h_Temp = param.Temp_Ampl * np.random.normal(size=3)

		# Effective field
		n = JJ * m2 + h_ani + HH - param.Demag * m1 + h_SOT + h_Temp
		# Precessional and damped dynamics
		pp = np.cross(m1, n)
		bb = np.cross(m1, pp)

		# LLG equation 
		s = np.float64(-1.0*((param.g0 * M_T) / (1 + param.a**2)) * (pp + param.a * bb)) 
		return s

	def Heun(self, m1, m2, t0):  # Heun Method
		param = self.param
		h = np.float64(param.h)

		m1 = np.array(m1, dtype=np.float64)
		m2 = np.array(m2, dtype=np.float64)

		xx = self.mxx(m1, m2, t0)
		yy = self.mxx(m2, m1, t0)

		mp1 = m1 + h * xx  # Predictor
		mp2 = m2 + h * yy
		mp1 /= np.linalg.norm(mp1)  # Normalize
		mp2 /= np.linalg.norm(mp2)

		m1 = m1 + 0.5 * h * (xx + self.mxx(mp1, mp2, t0))  
		m2 = m2 + 0.5 * h * (yy + self.mxx(mp2, mp1, t0))  # Corrector
		m1 /= np.linalg.norm(m1)
		m2 /= np.linalg.norm(m2)

		return m1, m2

	def RK4(self, m1, m2, t0):  # Runge-Kutta 4th order, both sublattices advanced together
		param = self.param
		h = np.float64(param.h)

		m1 = np.array(m1, dtype=np.float64)
		m2 = np.array(m2, dtype=np.float64)

		k1 = self.mxx(m1, m2, t0)
		p1 = self.mxx(m2, m1, t0)
		k2 = self.mxx(m1 + 0.5 * h * k1, m2 + 0.5 * h * p1, t0 + 0.5 * h)
		p2 = self.mxx(m2 + 0.5 * h * p1, m1 + 0.5 * h * k1, t0 + 0.5 * h)
		k3 = self.mxx(m1 + 0.5 * h * k2, m2 + 0.5 * h * p2, t0 + 0.5 * h)
		p3 = self.mxx(m2 + 0.5 * h * p2, m1 + 0.5 * h * k2, t0 + 0.5 * h)
		k4 = self.mxx(m1 + h * k3, m2 + h * p3, t0 + h)
		p4 = self.mxx(m2 + h * p3, m1 + h * k3, t0 + h)

		# weights 1/6 (before: 0.166, and the m2 stages used the m1 slopes)
		m1 = m1 + (h / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
		m2 = m2 + (h / 6.0) * (p1 + 2 * p2 + 2 * p3 + p4)
		m1 /= np.linalg.norm(m1)
		m2 /= np.linalg.norm(m2)

		return m1, m2
