import numpy as np 
from conf import conFile
from scipy.signal import chirp

class Current:
	def __init__(self):  
		self.param = conFile()

	def j(self, J, t0):
		J = np.float64(J)
		t0 = np.float64(t0)

		if not self.param.flag1:
			return J
		else:
			# TT = np.float64(self.param.T * (self.param.g0 * self.param.Ms))
			omega = np.float64(2 * np.pi *self.param.Fr)
			Jamp = np.float64((4 * self.param.A0_Amp) / (self.param.mu0 * self.param.Ms**2 * self.param.l**2))
			j = np.float64(J + Jamp * np.sin(omega * t0 + self.param.phase*np.pi/180.0))
			return j

	def ku(self, k, t0):
		k = np.float64(k)
		t0 = np.float64(t0)

		# Only the AC part of Ku(t): the static part is already in K (before, K was returned here and added twice).
		if not self.param.flag2:
			return np.float64(0.0)
		else:
			omega = np.float64(2 * np.pi *self.param.Ku_Fr)
			# same normalisation as the static K in solver.py (before: g0 instead of mu0, 1.76e11 times too small)
			kamp = np.float64((4 * self.param.Ku_Amp) / (self.param.mu0 * self.param.Ms**2))
			kk = np.float64(kamp * np.sin(omega * t0 + self.param.Ku_phase*np.pi/180.0))
			return kk

	def H(self, H, t0):
		H = np.float64(H)
		t0 = np.float64(t0)

		if not self.param.flag3:
			return np.float64(H * self.param.Hex_DC)
		else:
			# TT = np.float64(self.param.T * (self.param.g0 * self.param.Ms))
			omega = np.float64(2 * np.pi *self.param.Fr)
			H_Amp = np.float64(self.param.H_Amp / (self.param.mu0 * self.param.Ms))
			HH = np.float64(H * self.param.Hex_DC + self.param.Hex_AC * (H_Amp * np.sin(omega * t0 + self.param.phase*np.pi/180.0)))
			return HH

	def Je_Chirp(self, t0):
		t0 = np.float64(t0)
		Je_Chirp = np.float64(0.0)

		if self.param.flag4 and self.param.t_chirp > t0:
			sigma = 1.0e4 * 2.0 * self.param.muB / (self.param.e * self.param.g0 * self.param.Ms**2 * self.param.Lz)
			Je_Chirp = np.float64(sigma * self.param.Chirp_Amp * chirp(t=t0, f0=self.param.Chirp_min_Fr, t1=self.param.t_chirp, f1=self.param.Chirp_max_Fr, method='linear', phi=self.param.Chirp_phase*np.pi/180.0))

		return Je_Chirp

	def sinc_signal(self, f_cut, t0):
		t0 = np.float64(t0)
		sinc_pulse= np.float64(1.0)
		f_cut = np.float64(f_cut)
		Dx = np.float64(0.0)

		if self.param.flagSinc:
			sinc_pulse = np.sinc(2*f_cut*(t0-Dx))
		return sinc_pulse

	def Je_SOT(self, t0):
		
		Je_SOT = np.float64(0.0)

		if self.param.flag5:
			omega = np.float64(2 * np.pi * self.param.SOT_AC_Fr)

			sigma = 1.0e4 * 2.0 * self.param.muB / (self.param.e * self.param.g0 * self.param.Ms**2 * self.param.Lz)
			Prefactor = np.float64(self.param.SHE_angle * sigma)
			Je_SOT = np.float64(Prefactor * (self.param.SOT_DC_Amp + self.param.SOT_AC_Amp * np.sin(omega * t0 + self.param.SOT_AC_phase*np.pi/180.0)))

		return Je_SOT

	def Temperature_dependence_parameters(self, Msat, Jex, Kani, t0):
		"""Temperature dependence of parameters"""
		if self.param.flagTempVarying:
			# Temperature dependence of parameters 
			omega = np.float64(2 * np.pi *self.param.Fr)
			Tc = 700.0  # Critical temperature
			T_phase = 370.0
			T_Amp = self.param.Temp
			T_of_t_old = T_phase +  T_Amp * np.sin(omega * (t0-self.param.h) + self.param.phase*np.pi/180.0) # Previous time step temperature
			T_of_t = T_phase +  T_Amp * np.sin(omega * t0 + self.param.phase*np.pi/180.0)
			m_exp = 0.5
			meq = np.power( (1.0 - T_of_t/Tc), m_exp ) # Temperature dependent magnetisation
			A0_exp = 1.77
			A0_sign = 1.0
			Ku_exp = 3.0
			DeltaT = np.sign(T_of_t - T_of_t_old) * 5.0
			if T_of_t < T_phase + DeltaT:
				A0_exp = 2.65
				A0_sign = -1.0
				Ku_exp = 3.0
			Jex = A0_sign * Jex * np.power(meq, A0_exp)
			Kani = Kani * np.power(meq, Ku_exp)
			Msat = self.param.Ms * meq  # Temperature dependente saturation magnetisation
			print(f'Time: {t0:.2e} s, Tempold: {T_of_t_old:.2f} K , Temp: {T_of_t:.2f} K, meq: {meq:.2f}, Msat: {Msat:.2e}, Jex: {Jex:.2e}, Kani: {Kani:.2e}')


		return Msat, Jex, Kani