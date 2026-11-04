"""Audio -> whitened log-frequency spectrogram."""
import subprocess

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import stft

SR = 8000


def load_audio(path, sr=SR, start=None, duration=None):
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", path]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, np.float32)


class LogSpec:
    """W[t, j]: robust z-score of dB power at log-frequency bin j.

    Stationary tones (hum, fans) are removed by subtracting a running median over time,
    so what survives are sounds whose pitch moves - e.g. Doppler-swept engine tones.
    """

    def __init__(self, x, sr=SR, win_s=1.0, hop_s=0.25, fmin=40.0, fmax=3600.0,
                 dlog=0.003, bg_s=60.0):
        nper = int(win_s * sr)
        f, t, Z = stft(x, sr, nperseg=nper, noverlap=nper - int(hop_s * sr), nfft=2 * nper,
                       boundary=None, padded=False)
        P = (np.abs(Z) ** 2).T + 1e-12           # [T, F]
        self.t = t
        self.dlog = dlog
        self.logf = np.arange(np.log(fmin), np.log(fmax), dlog)
        # max-pool linear bins into each log bin (interpolate where log bins are finer)
        edges = np.exp(np.r_[self.logf - dlog / 2, self.logf[-1] + dlog / 2])
        lo = np.searchsorted(f, edges[:-1])
        hi = np.searchsorted(f, edges[1:])
        L = np.empty((P.shape[0], len(self.logf)))
        for j in range(len(self.logf)):
            if hi[j] > lo[j]:
                L[:, j] = P[:, lo[j]:hi[j]].max(axis=1)
            else:
                k = min(max(lo[j], 1), len(f) - 1)
                L[:, j] = P[:, k]
        D = 10 * np.log10(L)
        nbg = max(3, int(bg_s / hop_s) | 1)
        D = D - median_filter(D, size=(nbg, 1), mode="nearest")
        D = D - median_filter(D, size=(1, 61), mode="nearest")
        mad = np.median(np.abs(D)) * 1.4826 + 1e-9
        self.W = np.clip(D / mad, -3, 6).astype(np.float32)

    @property
    def freqs(self):
        return np.exp(self.logf)
