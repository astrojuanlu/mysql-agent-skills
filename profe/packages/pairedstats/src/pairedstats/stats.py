"""The profe stats helper: paired experiments on scipy/numpy.

One authoritative implementation of the small statistics the campaigns
need (retro A4 "canonical numbers" / B10 "analysis as committed code"):
exact permutation, paired t, Wilcoxon, McNemar, single-arm upper bounds
(Jeffreys + Clopper–Pearson), and the Miettinen–Nurminen unconditional
score CI. Distributional math is scipy's (beta / t / normal / binomial);
the exact sign-flip permutation keeps a meet-in-the-middle enumerator
because scipy's exact test enumerates all 2^n sign vectors and is
infeasible at the n ≤ 40 the pre-registration guarantees, and the
Miettinen–Nurminen CI keeps its own score root-finder (scipy ships no
MN interval). Each function's docstring cites its reference (paper,
textbook, or standard tables); `tests/test_stats.py` anchors every
function to external ground truth only — published tables, analytic
closed forms and identities, definitional brute-force enumeration, and
textbook defining equations solved with an independent root-finder.

Sign conventions fixed by campaign 3 §5: d = ln(off) − ln(on) per pair
(positive = the treated arm used fewer); the permutation test is
two-sided on mean(d).

Call `selftest()` (or run the pytest suite in `tests/`) to verify the
implementation against textbook values and brute force.
"""

from __future__ import annotations

import math
from itertools import product

import numpy as np
from scipy.special import betainc as _betainc
from scipy.stats import beta as _beta
from scipy.stats import binom as _binom
from scipy.stats import norm as _norm
from scipy.stats import t as _t
from scipy.stats import ttest_1samp, wilcoxon


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b).

    Reference: NIST DLMF §8.17 (incomplete beta function; definition
    and analytic special cases used in the tests).
    """
    return float(_betainc(a, b, x))


def t_two_sided_p(t: float, df: float) -> float:
    """Two-sided p-value of Student t = `t` on `df` degrees of freedom.

    References: Student (1908), "The probable error of a mean",
    Biometrika 6(1), 1–25; standard tables, e.g. t_{0.975,10} = 2.2281
    ⇒ p = 0.05 (used verbatim in the tests).
    """
    return float(2.0 * _t.sf(abs(t), df))


def t_quantile_975(df: float) -> float:
    """Two-sided 95% critical value of Student t on `df` degrees of freedom.

    Reference: standard t tables (published entries asserted in
    tests/test_stats.py, e.g. t_{0.975,1} = 12.7062, t_{0.975,30} =
    2.0423).
    """
    return float(_t.ppf(0.975, df))


def norm_ppf_975() -> float:
    """97.5th percentile of the standard normal (two-sided 95% z).

    Reference: standard normal tables; published constant
    z_0.975 = 1.959963984540054 (asserted verbatim in the tests).
    """
    return float(_norm.ppf(0.975))


def _signed_sums(x: np.ndarray) -> np.ndarray:
    """All 2^len(x) signed sums of `x`, built by doubling."""
    s = np.zeros(1)
    for v in x:
        s = np.concatenate((s - v, s + v))
    return s


def signflip_exact(d: list[float]) -> float:
    """Exact two-sided sign-flip permutation p on mean(d), meet-in-middle.

    p = #{sign vectors s : |mean(s·d)| >= |mean(d)|} / 2^n, exact for any
    n (enumerates both halves at 2^(n/2) and counts pairs by bisection —
    scipy's exact test enumerates all 2^n and cannot honor the n ≤ 40
    guarantee).

    References: the sign-flip (randomization) test — Pitman (1937),
    "Significance tests which may be applied to samples from any
    populations", JRSS Supplement 4, 119–130 and 225–232; Ernst (2004),
    "Permutation methods: a basis for exact inference", Statistical
    Science 19(4), 676–685. The meet-in-the-middle split is the classic
    subset-sum technique (Horowitz & Sahni 1974).
    """
    d_in = np.asarray(d, dtype=float)
    n = d_in.size
    if n == 0:
        return float("nan")
    s_obs = abs(float(d_in.sum()))
    if s_obs == 0.0:
        return 1.0
    h = n // 2
    A = _signed_sums(d_in[:h])
    B = np.sort(_signed_sums(d_in[h:]))
    nb = B.size
    # count b: a + b >= s_obs  or  a + b <= -s_obs
    # (1e-9 slack: counts exact ties as >=, and absorbs float association
    # noise so the split-sum matches a direct summation)
    total = int(
        (nb - np.searchsorted(B, s_obs - A - 1e-9, side="left")).sum()
        + np.searchsorted(B, -s_obs - A + 1e-9, side="right").sum()
    )
    return total / 2.0**n


def wilcoxon_pratt(d: list[float]) -> float:
    """Signed-rank p-value, Pratt zeros handling, normal approximation.

    Pre-registered convention (c3 §5): PRATT — zeros are dropped AFTER
    ranking, i.e. ranks are assigned to all differences and the zero
    block is then excluded from the statistic (scipy
    `zero_method="pratt"`). Ties get average ranks and the normal
    approximation is tie-corrected, without continuity correction
    (`method="approx"`, `correction=False`). Returns nan when every
    difference is zero.

    References: Wilcoxon (1945), "Individual comparisons by ranking
    methods", Biometrics Bulletin 1(6), 80–83; Pratt (1959), "Remarks
    on zeros and ties in the Wilcoxon signed rank procedures", JASA
    54(287), 655–667 (the zeros convention); normal approximation with
    tie correction, e.g. Lehmann (1975), Nonparametrics: Statistical
    Methods Based on Ranks, §3.2.
    """
    if not any(v != 0.0 for v in d):
        return float("nan")
    res = wilcoxon(
        d,
        zero_method="pratt",
        alternative="two-sided",
        method="approx",
        correction=False,
    )
    return float(res.pvalue)


def paired_t(d: list[float]) -> tuple[float, float, float]:
    """Two-sided paired t on differences `d`: (p, mean, sd).

    Reference: the one-sample t applied to pairwise differences —
    Student (1908), Biometrika 6(1), 1–25; standard texts, e.g.
    Casella & Berger (2002), Statistical Inference, 2nd ed., §5.3.
    """
    n = len(d)
    if n < 2:
        return float("nan"), float("nan"), float("nan")
    m = sum(d) / n
    s = math.sqrt(sum((v - m) ** 2 for v in d) / (n - 1))
    if s == 0:
        return (0.0 if m == 0 else float("nan")), m, s
    p = float(ttest_1samp(d, 0.0, alternative="two-sided").pvalue)
    return p, m, s


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value on discordant counts (b, c).

    The doubled one-sided binomial convention: 2·P(X ≤ min(b, c)) under
    Binomial(b + c, 1/2), capped at 1.

    References: McNemar (1947), "Note on the sampling error of the
    difference between correlated proportions or percentages",
    Psychometrika 12(2), 153–157; the exact conditional (doubled
    one-sided) form, e.g. Agresti (2002), Categorical Data Analysis,
    2nd ed., §10.5.
    """
    m = b + c
    if m == 0:
        return 1.0
    k = min(b, c)
    return float(min(1.0, 2.0 * _binom.cdf(k, m, 0.5)))


def jeffreys_upper(x: int, n: int, conf: float = 0.95) -> float:
    """Jeffreys Beta(x+.5, n−x+.5) `conf` upper bound.

    References: Jeffreys (1946), "An invariant form for the prior
    probability in estimation problems", Proc. Royal Soc. A 186,
    453–461 (the Beta(½,½) prior); Brown, Cai & DasGupta (2001),
    "Interval estimation for a binomial proportion", Statistical
    Science 16(2), 101–133 (properties and comparison with CP).
    """
    return float(_beta.ppf(conf, x + 0.5, n - x + 0.5))


def cp_upper(x: int, n: int, conf: float = 0.95) -> float:
    """Clopper–Pearson `conf` upper bound on a binomial proportion.

    Quantile of Beta(x+1, n−x); for x=0 it is 1−(1−conf)^(1/n) (exact
    closed form, asserted in the tests).
    NOTE: DESIGN §5.1.4 quotes 26%/14%/9.5% under the label 'Jeffreys',
    but those are the CP values — both bounds are reported; the label
    gets resolved at pre-registration (operator, 2026-10-16).

    References: Clopper & Pearson (1934), "The use of confidence or
    fiducial limits illustrated in the case of the binomial",
    Biometrika 26(4), 404–413; Brown, Cai & DasGupta (2001),
    Statistical Science 16(2), 101–133 (conservatism of the interval).
    """
    if n - x <= 0:
        return 1.0
    return float(_beta.ppf(conf, x + 1.0, n - x))


def _mn_p2_tilde(x1: int, n1: int, x2: int, n2: int, delta: float) -> float | None:
    """Constrained MLE p2~ under p1 = p2 + delta (score equation, bisection).

    Reference: the score equation for the constrained MLE is equation
    (1) of Miettinen & Nurminen (1985), Statistics in Medicine 4(2),
    213–226. The search bracket is the intersection of BOTH validity
    constraints, p̃1 = p̃2 + δ ∈ (0,1) and p̃2 ∈ (0,1): for δ < 0 that
    means p̃2 > −δ. (A plain (0, 1−δ) bracket admits p̃1 ≤ 0 — the
    score degenerates there and produced false roots; found by the
    defining-equation test in tests/test_stats.py.)
    """

    def score(p2: float) -> float | None:
        p1 = p2 + delta
        if not (1e-12 < p1 < 1 - 1e-12) or not (1e-12 < p2 < 1 - 1e-12):
            return None
        return x1 / p1 - (n1 - x1) / (1 - p1) + x2 / p2 - (n2 - x2) / (1 - p2)

    lo = max(1e-9, -delta + 1e-9)
    hi = min(1.0 - delta, 1.0) - 1e-9
    if hi <= lo:
        return None
    flo, fhi = score(lo), score(hi)
    if flo is None or fhi is None:
        return None
    if flo * fhi > 0:
        # No interior root: the constrained log-likelihood is CONCAVE in
        # p2 (log of affine-positive terms), so its derivative — this
        # score — is monotonically decreasing, and the constrained max
        # sits at an endpoint, chosen by the derivative's sign (a
        # smaller |score| does NOT identify the maximum). Found by the
        # zero-cell cases in the defining-equation test.
        return hi if flo >= 0 else lo
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = score(mid)
        if fm is None:
            return None
        if (fm < 0) == (flo < 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2


def mn_ci_diff(
    x1: int, n1: int, x2: int, n2: int, conf: float = 0.95
) -> tuple[float, float]:
    """Miettinen–Nurminen unconditional score CI for p1 − p2.

    g(delta) = |Z_MN(delta)| − z has exactly two roots (g → +∞ at the
    boundaries, g < 0 at the point estimate); each is found by expanding
    outward from the point estimate until g crosses, then bisecting.

    References: Miettinen & Nurminen (1985), "Comparative analysis of
    two rates", Statistics in Medicine 4(2), 213–226 (the score CI and
    its n/(n−1) variance correction); tests re-solve the defining
    equation with scipy.optimize.brentq as an independent check.
    """
    z = norm_ppf_975()
    p1h, p2h = x1 / n1, x2 / n2

    def zabs(delta: float) -> float:
        p2 = _mn_p2_tilde(x1, n1, x2, n2, delta)
        if p2 is None:
            return float("inf")
        p1 = p2 + delta
        n = n1 + n2
        var = (p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2) * (n / (n - 1.0))
        if var <= 0:
            return float("inf")
        return abs(p1h - p2h - delta) / math.sqrt(var)

    def g(delta: float) -> float:
        return zabs(delta) - z

    def root(direction: int) -> float:
        d0 = p1h - p2h
        step = 0.02
        inside = d0
        while True:
            cand = inside + direction * step
            if not (-0.999999 < cand < 0.999999):
                cand = math.copysign(0.999999, direction)
                if abs(cand - inside) < 1e-12:
                    return cand
            if g(cand) >= 0:
                a, b = min(inside, cand), max(inside, cand)
                for _ in range(200):
                    m = (a + b) / 2
                    if (g(a) <= 0) == (g(m) <= 0):
                        a = m
                    else:
                        b = m
                return (a + b) / 2
            inside = cand
            step *= 1.6

    lo, hi = root(-1), root(+1)
    return (lo, hi) if lo <= hi else (hi, lo)


def selftest() -> bool:
    """Check the implementation against textbook values and brute force.

    Returns True when every check passes. The bounds checks double as
    the resolution of DESIGN §5.1.4's mislabeled 'Jeffreys' values:
    Jeffreys is verified via the F-distribution identity (Beta(a,b)
    quantile q ⟺ (b·q)/(a·(1−q)) = F(2a,2b) quantile), CP against the
    quoted numbers. The full external-ground-truth suite lives in
    `tests/test_stats.py`.
    """
    ok: list[tuple[str, bool]] = []

    def chk(name: str, cond: bool) -> None:
        ok.append((name, bool(cond)))

    chk("t p (2.228, df 10) ~ 0.05", abs(t_two_sided_p(2.228, 10) - 0.0501) < 5e-3)
    # Jeffreys bounds, verified against the F-distribution identity:
    # 0/10 → 0.1708 (F(1,21)=4.3244), 0/20 → 0.0906 (F(1,40)=4.0847),
    # 0/30 → 0.0616 (F(1,61)=4.0012). §5.1.4's quoted 26/14/9.5% are the
    # CLOPPER–PEARSON values — checked here as such.
    chk(
        "jeffreys 0/10 = 0.1708 (F identity)",
        abs(jeffreys_upper(0, 10) - 0.1708) < 2e-3,
    )
    chk(
        "jeffreys 0/20 = 0.0906 (F identity)",
        abs(jeffreys_upper(0, 20) - 0.0906) < 2e-3,
    )
    chk(
        "jeffreys 0/30 = 0.0616 (F identity)",
        abs(jeffreys_upper(0, 30) - 0.0616) < 2e-3,
    )
    chk("cp 0/10 = 0.2589 (DESIGN quotes 26%)", abs(cp_upper(0, 10) - 0.2589) < 1e-3)
    chk("cp 0/20 = 0.1391 (DESIGN quotes 14%)", abs(cp_upper(0, 20) - 0.1391) < 1e-3)
    chk("cp 0/30 = 0.0952 (DESIGN quotes 9.5%)", abs(cp_upper(0, 30) - 0.0952) < 1e-3)
    chk("mcnemar (1,9) ~ 0.0215", abs(mcnemar_exact(1, 9) - 0.02148) < 1e-3)
    chk("perm zero effect p=1", abs(signflip_exact([1, -1, 2, -2]) - 1.0) < 1e-12)
    chk("perm [1,1,1] p=0.25", abs(signflip_exact([1, 1, 1]) - 0.25) < 1e-12)
    d = [0.3, -1.7, 2.2, 0.05, -0.4, 1.1, -2.0, 0.9]
    s_obs = abs(sum(d))
    cnt = sum(
        1
        for signs in product((1, -1), repeat=len(d))
        if abs(sum(s * v for s, v in zip(signs, d))) >= s_obs - 1e-12
    )
    chk("perm matches brute force", abs(signflip_exact(d) - cnt / 2 ** len(d)) < 1e-12)
    for name, good in ok:
        print(f"  {'ok ' if good else 'FAIL'} {name}")
    return all(good for _, good in ok)
