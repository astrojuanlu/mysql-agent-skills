"""Tests for profe.stats, anchored to EXTERNAL ground truth only.

No reference implementations, no oracle code: every assertion checks
the module against one of

  1. published constant values (t table, normal table, the CP closed
     forms quoted by DESIGN §5.1.4),
  2. analytic closed forms / identities (incomplete-beta special cases,
     the closed-form Wilcoxon variance for untied data, paired-t at
     n = 2, Clopper–Pearson x = 0),
  3. definitional brute force (sign-flip permutation = enumeration over
     all 2^n sign vectors; McNemar = the combinatorial sum),
  4. textbook defining equations solved by an INDEPENDENT root-finder
     (the Miettinen–Nurminen endpoints must satisfy |Z_MN(δ)| = z with
     the score equation transcribed from Miettinen & Nurminen 1985 and
     bracketed by scipy.optimize.brentq — a different finder than the
     module's expanding bisection),
  5. invariance/property checks (symmetry, monotonicity, containment).

Textbook values used below are standard table entries (R `qt`/`qnorm`
and any printed t table) and are written as literals so a transcription
error in the module cannot reproduce them.

Assertions use numpy.testing helpers (NaN-safe by construction).
"""

import math
from itertools import product

import numpy as np
import pytest
from scipy.optimize import brentq
from scipy.stats import f as f_dist
from scipy.stats import norm as norm_dist

from pairedstats import (
    betainc,
    cp_upper,
    jeffreys_upper,
    mcnemar_exact,
    mn_ci_diff,
    norm_ppf_975,
    paired_t,
    selftest,
    signflip_exact,
    t_quantile_975,
    t_two_sided_p,
    wilcoxon_pratt,
)

RNG = np.random.default_rng(20261016)

NAN = float("nan")


def test_selftest():
    """The module selftest must pass in full (textbook + brute force)."""
    assert selftest()


# ---------------------------------------------------------------- t / normal


@pytest.mark.parametrize(
    "df,q975",
    [
        (1.0, 12.7062047362),
        (2.0, 4.3026527299),
        (5.0, 2.5705818366),
        (10.0, 2.2281388520),
        (20.0, 2.0859634473),
        (30.0, 2.0422724563),
    ],
)
def test_t_quantile_975_published(df, q975):
    """Standard t-table entries for the two-sided 95% critical value."""
    np.testing.assert_almost_equal(t_quantile_975(df), q975, decimal=8)


def test_t_two_sided_p_table():
    """p at published quantiles must return the matching tail probability."""
    np.testing.assert_almost_equal(t_two_sided_p(2.2281388520, 10), 0.05, decimal=8)
    np.testing.assert_almost_equal(t_two_sided_p(1.8124611228, 10), 0.10, decimal=8)
    np.testing.assert_almost_equal(t_two_sided_p(3.1692726731, 10), 0.01, decimal=8)
    np.testing.assert_almost_equal(t_two_sided_p(0.0, 10), 1.0, decimal=12)
    # monotone in |t|
    assert t_two_sided_p(2.0, 10) < t_two_sided_p(1.0, 10)


def test_norm_ppf_975_published():
    np.testing.assert_almost_equal(norm_ppf_975(), 1.959963984540054, decimal=12)
    np.testing.assert_almost_equal(norm_ppf_975(), norm_dist.ppf(0.975), decimal=12)


# ---------------------------------------------------------------- betainc


def test_betainc_analytic_identities():
    """Closed forms: I_x(1,b) = 1−(1−x)^b, I_x(a,1) = x^a,
    I_x(2,2) = 3x²−2x³, I_x(½,½) = (2/π)·arcsin(√x)."""
    for x in [1e-4, 0.1, 0.37, 0.5, 0.73, 0.9, 1 - 1e-4]:
        for b in [0.3, 1.0, 2.5, 17.0]:
            np.testing.assert_almost_equal(
                betainc(1.0, b, x), 1.0 - (1.0 - x) ** b, decimal=10
            )
        for a in [0.3, 1.0, 2.5, 17.0]:
            np.testing.assert_almost_equal(betainc(a, 1.0, x), x**a, decimal=10)
        np.testing.assert_almost_equal(
            betainc(2.0, 2.0, x), 3.0 * x * x - 2.0 * x**3, decimal=10
        )
        np.testing.assert_almost_equal(
            betainc(0.5, 0.5, x), 2.0 / math.pi * math.asin(math.sqrt(x)), decimal=10
        )


def test_betainc_complement_identity():
    """I_x(a,b) = 1 − I_{1−x}(b,a) — the complement identity requires the
    SWAPPED parameters (∫ₓ¹ t^{a−1}(1−t)^{b−1}/B(a,b) dt substitutes
    u = 1−t and becomes a beta(b,a) integral)."""
    for _ in range(100):
        a = float(RNG.uniform(0.2, 50))
        b = float(RNG.uniform(0.2, 50))
        x = float(RNG.uniform(0.01, 0.99))
        np.testing.assert_almost_equal(
            betainc(a, b, x), 1.0 - betainc(b, a, 1.0 - x), decimal=9
        )


# ---------------------------------------------------------------- bounds


def test_cp_upper_closed_form():
    """Clopper–Pearson at x=0 has the exact closed form 1−(1−conf)^(1/n)
    (the values DESIGN §5.1.4 quotes as 'Jeffreys': 26%/14%/9.5%); at
    x=n the bound is 1. Every bound must satisfy its DEFINING equation
    Beta-CDF(q) = conf."""
    for n, quoted in [(10, 0.2589), (20, 0.1391), (30, 0.0950)]:
        np.testing.assert_almost_equal(
            cp_upper(0, n), 1.0 - 0.05 ** (1.0 / n), decimal=11
        )
        np.testing.assert_almost_equal(cp_upper(0, n), quoted, decimal=3)
        assert cp_upper(n, n) == 1.0
    for _ in range(100):
        x = int(RNG.integers(0, 16))
        n = int(RNG.integers(x + 1, 61))
        q = cp_upper(x, n)
        np.testing.assert_almost_equal(betainc(x + 1.0, n - x, q), 0.95, decimal=11)


def test_jeffreys_upper_defining_equation_and_f_identity():
    """Jeffreys bound = Beta(x+.5, n−x+.5) quantile; the defining
    equation Beta-CDF(q) = conf must hold exactly, and the value must
    match the F-distribution identity
    (b·q)/(a·(1−q)) = F_{2a,2b}(conf) — independent distribution code."""
    for _ in range(100):
        x = int(RNG.integers(0, 16))
        n = int(RNG.integers(x + 1, 61))
        q = jeffreys_upper(x, n)
        a, b = x + 0.5, n - x + 0.5
        np.testing.assert_almost_equal(betainc(a, b, q), 0.95, decimal=11)
        f = f_dist.ppf(0.95, 2 * a, 2 * b)
        np.testing.assert_almost_equal((b * q) / (a * (1.0 - q)), f, decimal=8)
    # ordering: at x = 0 Jeffreys is strictly shorter than CP
    for n in [10, 20, 30]:
        assert jeffreys_upper(0, n) < cp_upper(0, n)


# ---------------------------------------------------------------- McNemar


def test_mcnemar_definitional_sum():
    """Exact two-sided McNemar = 2·Σ_{i≤min(b,c)} C(b+c, i)/2^(b+c),
    written out from the definition (McNemar 1947); symmetric in (b, c);
    published example (1,9) ≈ 0.02148."""
    for b in range(0, 13):
        for c in range(0, 13):
            m = b + c
            k = min(b, c)
            ref = min(1.0, 2.0 * sum(math.comb(m, i) for i in range(0, k + 1)) / 2.0**m)
            np.testing.assert_almost_equal(mcnemar_exact(b, c), ref, decimal=13)
            assert mcnemar_exact(b, c) == mcnemar_exact(c, b)
    np.testing.assert_almost_equal(mcnemar_exact(1, 9), 0.02148, decimal=4)
    assert mcnemar_exact(0, 0) == 1.0


# ---------------------------------------------------------------- paired t


def test_paired_t_closed_forms():
    """n = 2 closed form: t = (a+b)/(a−b), p = t_two_sided_p(t, 1);
    mean/sd closed forms; degenerate handling (NaN-safe assertions)."""
    a, b = 1.7, -0.9
    p, m, s = paired_t([a, b])
    np.testing.assert_almost_equal(m, (a + b) / 2.0, decimal=14)
    np.testing.assert_almost_equal(s, abs(a - b) / math.sqrt(2.0), decimal=14)
    t_ref = (a + b) / (a - b)  # = m / (s/√2) by algebra
    np.testing.assert_almost_equal(p, t_two_sided_p(t_ref, 1.0), decimal=12)
    # zero-variance differences (NaN in the first element is expected)
    np.testing.assert_almost_equal(paired_t([3.0, 3.0]), (NAN, 3.0, 0.0))
    np.testing.assert_almost_equal(paired_t([0.0, 0.0])[0], 0.0)
    assert all(math.isnan(v) for v in paired_t([1.0]))
    # random data: p must equal the t distribution at the closed-form t
    for _ in range(100):
        n = int(RNG.integers(2, 41))
        d = [float(v) for v in RNG.normal(0, 2, n)]
        p, m, s = paired_t(d)
        if math.isnan(p):
            continue
        m_ref = sum(d) / n
        s_ref = math.sqrt(sum((v - m_ref) ** 2 for v in d) / (n - 1))
        t_ref = m_ref / (s_ref / math.sqrt(n))
        np.testing.assert_almost_equal(p, t_two_sided_p(t_ref, n - 1), decimal=11)


# ---------------------------------------------------------------- Wilcoxon


def test_wilcoxon_untied_closed_form():
    """Zero-free, tie-free data: ranks are 1..n, so Var = Σr² =
    n(n+1)(2n+1)/6 in CLOSED form and S = Σ sign·rank. p = normal
    two-sided at z = S/√Var."""
    for _ in range(50):
        n = int(RNG.integers(6, 41))
        ranks = list(range(1, n + 1))
        signs = [
            1.0 if v > 0 else -1.0 for v in RNG.normal(0, 1, n)
        ]  # random signs, no zeros
        d = [s * r for s, r in zip(signs, ranks)]
        s_stat = sum(signs[i] * ranks[i] for i in range(n))
        var = n * (n + 1) * (2 * n + 1) / 6.0
        z = s_stat / math.sqrt(var)
        p_ref = math.erfc(abs(z) / math.sqrt(2.0))
        np.testing.assert_almost_equal(wilcoxon_pratt(d), p_ref, decimal=9)


def test_wilcoxon_pratt_zeros_first_principles():
    """Pratt convention (c3 §5): ranks over ALL differences (average
    ranks for ties), then the zero block is dropped from the statistic;
    Var = Σr² over the retained ranks.
      d=[0,1,2]   → ranks 1,2,3, retained 2,3 → S=5,  Var=13
      d=[0,0,1]   → ranks 1.5,1.5,3, retained 3 → S=3, Var=9
      d=[-1,1,1,-1,1] → all |d| tied → ranks 3.0 ×5 → S=3, Var=45"""
    cases = [
        ([0.0, 1.0, 2.0], 5.0, 13.0),
        ([0.0, 0.0, 1.0], 3.0, 9.0),
        ([-1.0, 1.0, 1.0, -1.0, 1.0], 3.0, 45.0),
    ]
    for d, s_stat, var in cases:
        z = s_stat / math.sqrt(var)
        p_ref = math.erfc(abs(z) / math.sqrt(2.0))
        np.testing.assert_almost_equal(wilcoxon_pratt(d), p_ref, decimal=9)
    assert math.isnan(wilcoxon_pratt([0.0, 0.0, 0.0]))


# ---------------------------------------------------------------- signflip


def test_signflip_brute_force():
    """The definition, enumerated: p = #{s ∈ {±1}^n : |mean(s·d)| ≥
    |mean(d)|} / 2^n. Also: the result must be an exact multiple of 2^-n
    and sign-symmetric."""

    def brute(d):
        s_obs = abs(sum(d))
        cnt = sum(
            1
            for signs in product((1, -1), repeat=len(d))
            if abs(sum(s * v for s, v in zip(signs, d))) >= s_obs - 1e-9
        )
        return cnt / 2 ** len(d)

    for _ in range(60):
        n = int(RNG.integers(3, 13))
        d = [float(v) for v in RNG.normal(0, 1, n)]
        p = signflip_exact(d)
        np.testing.assert_almost_equal(p, brute(d), decimal=12)
        np.testing.assert_almost_equal(p * 2.0**n, round(p * 2.0**n), decimal=6)
        assert signflip_exact([-v for v in d]) == p
    assert signflip_exact([1.0, -1.0, 2.0, -2.0]) == 1.0  # zero effect
    np.testing.assert_almost_equal(signflip_exact([]), NAN)  # nan == nan
    assert signflip_exact([3.7]) == 1.0  # n=1 always mirrors


def test_signflip_large_n_exact_and_feasible():
    """n = 40 stays exact and fast (the pre-registration guarantee that
    ruled out scipy's exact enumeration)."""
    d = [float(v) for v in RNG.normal(0, 1, 40)]
    p = signflip_exact(d)
    assert 0.0 <= p <= 1.0
    np.testing.assert_almost_equal(p * 2.0**40, round(p * 2.0**40), decimal=3)


# ---------------------------------------------------------------- MN CI


def _z_mn(x1, n1, x2, n2, delta):
    """The Miettinen–Nurminen score statistic, transcribed from the 1985
    paper: constrained MLE p̃2 solves the score equation (independent
    root-finder: brentq), then
    Z = (p̂1 − p̂2 − δ) / √( (p̃1(1−p̃1)/n1 + p̃2(1−p̃2)/n2) · n/(n−1) ).
    The constraint bracket is the intersection p̃2 ∈ (max(0,−δ), min(1,1−δ))
    — the same one the module must respect."""
    p1h, p2h = x1 / n1, x2 / n2

    def score(p2):
        p1 = p2 + delta
        return x1 / p1 - (n1 - x1) / (1 - p1) + x2 / p2 - (n2 - x2) / (1 - p2)

    def loglik(p2):
        p1 = p2 + delta
        return (
            x1 * math.log(p1)
            + (n1 - x1) * math.log(1.0 - p1)
            + x2 * math.log(p2)
            + (n2 - x2) * math.log(1.0 - p2)
        )

    lo = max(1e-9, -delta + 1e-9)
    hi = min(1.0 - delta, 1.0) - 1e-9
    if score(lo) * score(hi) > 0:
        # no interior root (zero-cell cases): the log-likelihood is
        # concave, so its constrained max is at an endpoint — pick it by
        # DIRECT likelihood comparison, independent of the module's
        # derivative-sign rule
        p2 = max((lo, hi), key=loglik)
    else:
        p2 = brentq(score, lo, hi, xtol=1e-14)
    assert isinstance(p2, float)  # scipy's stub union: full_output defaults off
    p1 = p2 + delta
    n = n1 + n2
    var = (p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2) * (n / (n - 1.0))
    return (p1h - p2h - delta) / math.sqrt(var)


def test_mn_ci_defining_equation():
    """Each endpoint δ* must satisfy |Z_MN(δ*)| = z_{0.975} exactly (the
    CI is DEFINED as {δ : |Z_MN(δ)| ≤ z}), with the score solved by
    brentq — a different root-finder than the module's expanding
    bisection. Containment and degenerate handling checked alongside.
    (This test found a real bug: the module's constrained-MLE bracket
    ignored the p̃1 = p̃2 + δ > 0 constraint for δ < 0.)"""
    z = 1.959963984540054
    for _ in range(60):
        x1 = int(RNG.integers(0, 8))
        x2 = int(RNG.integers(0, 8))
        n1 = int(RNG.integers(max(x1 + 1, 4), 41))
        n2 = int(RNG.integers(max(x2 + 1, 4), 41))
        lo, hi = mn_ci_diff(x1, n1, x2, n2)
        d0 = x1 / n1 - x2 / n2
        assert lo <= d0 <= hi  # endpoints bracket the point estimate
        if not (x1 == 0 and x2 == 0):
            # interior endpoints satisfy the defining equation
            np.testing.assert_almost_equal(abs(_z_mn(x1, n1, x2, n2, lo)), z, decimal=5)
            np.testing.assert_almost_equal(abs(_z_mn(x1, n1, x2, n2, hi)), z, decimal=5)
            # just outside the endpoints the statistic must exceed z
            eps = 1e-4
            if hi < 1 - eps:
                assert abs(_z_mn(x1, n1, x2, n2, hi + eps)) > z
            if lo > -1 + eps:
                assert abs(_z_mn(x1, n1, x2, n2, lo - eps)) > z


def test_mn_ci_properties():
    lo, hi = mn_ci_diff(0, 10, 0, 10)
    assert lo <= 0.0 <= hi  # equal zero-event arms → interval covers 0
    for x, n in [(0, 10), (3, 10), (5, 10)]:
        lo, hi = mn_ci_diff(x, n, x, n)
        assert lo <= 0.0 <= hi  # equal proportions → covers 0
    # equal proportions, growing n → interval shrinks toward 0
    w10 = mn_ci_diff(3, 10, 3, 10)[1] - mn_ci_diff(3, 10, 3, 10)[0]
    w40 = mn_ci_diff(12, 40, 12, 40)[1] - mn_ci_diff(12, 40, 12, 40)[0]
    assert w40 < w10
