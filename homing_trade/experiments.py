"""Two-sample statistics for honest A/B variant evaluation (Phase 7 #4).

Pure stdlib (no numpy/scipy). Given two samples of a per-trade metric (e.g. realized PnL%) for
variant A and variant B, `two_sample_test` runs Welch's t-test (unequal variances) and returns an
EXACT two-sided p-value via the regularized incomplete beta function — not a normal approximation, so
it is honest for the small samples a young experiment has. `min_detectable_effect` reports the
smallest mean difference the current sample sizes could detect at a given significance/power, so an
"inconclusive" result is distinguishable from a "no real difference" one.

This module only COMPUTES; the experiments ledger (db.create_experiment / conclude_experiment) records
the results, and the multiple-comparison correction + promotion gate live in Phase 7 #5.
"""
import math

# Standard-normal quantiles for the common MDE defaults (probit is not in stdlib): alpha=0.05
# two-sided -> z_alpha = 1.959964; power=0.80 -> z_power = 0.841621.
Z_ALPHA_05 = 1.959964
Z_POWER_80 = 0.841621


def _mean(xs):
    return sum(xs) / len(xs)


def _sample_var(xs, mean):
    """Unbiased sample variance (n-1). 0.0 for n<2."""
    return sum((x - mean) ** 2 for x in xs) / (len(xs) - 1) if len(xs) > 1 else 0.0


def _betacf(a, b, x):
    """Continued-fraction expansion for the incomplete beta (Numerical Recipes betacf)."""
    MAXIT, EPS, FPMIN = 300, 3.0e-16, 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < EPS:
            break
    return h


def betai(a, b, x):
    """Regularized incomplete beta I_x(a, b) ∈ [0, 1]."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    bt = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t, df):
    """Two-sided p-value of a t-statistic with `df` degrees of freedom (exact)."""
    if df <= 0:
        return 1.0
    return betai(df / 2.0, 0.5, df / (df + t * t))


def welch_t(a, b):
    """Welch's t-statistic and degrees of freedom for samples a, b. (0.0, df) if both are flat."""
    na, nb = len(a), len(b)
    ma, mb = _mean(a), _mean(b)
    sa = _sample_var(a, ma) / na
    sb = _sample_var(b, mb) / nb
    denom = sa + sb
    if denom == 0.0:
        return 0.0, float(max(na + nb - 2, 1))
    t = (ma - mb) / math.sqrt(denom)
    if na > 1 and nb > 1:
        df = denom ** 2 / (sa ** 2 / (na - 1) + sb ** 2 / (nb - 1))
    else:
        df = float(max(na + nb - 2, 1))
    return t, df


def two_sample_test(a, b):
    """Welch two-sample test of mean(a) vs mean(b). Returns n_a/n_b, the means, their difference,
    t, df, and the exact two-sided p_value. Requires >=2 observations in each sample."""
    if len(a) < 2 or len(b) < 2:
        raise ValueError("two_sample_test needs >= 2 observations per variant")
    ma, mb = _mean(a), _mean(b)
    t, df = welch_t(a, b)
    return {"n_a": len(a), "n_b": len(b), "mean_a": ma, "mean_b": mb, "diff": ma - mb,
            "t": t, "df": df, "p_value": t_two_sided_p(t, df)}


def min_detectable_effect(n_a, n_b, sd, *, z_alpha=Z_ALPHA_05, z_power=Z_POWER_80):
    """Smallest absolute mean difference detectable at the given significance/power for sample sizes
    n_a, n_b and pooled-ish stdev `sd`. inf when undefined (no data / no variance)."""
    if n_a <= 0 or n_b <= 0 or sd <= 0:
        return float("inf")
    return (z_alpha + z_power) * sd * math.sqrt(1.0 / n_a + 1.0 / n_b)
