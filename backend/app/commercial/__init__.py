"""Customer × Item commercial intelligence.

The analytical grain is one customer's relationship with one item over time.
Customer-level margin says *that* an account is deteriorating; only this grain
says *which item*, *why*, and *how much it is worth*.

Layering, strictly one direction:

    config     thresholds (versioned, env-overridable)
    economics  one invoice line -> normalized unit economics    [pure]
    metrics    lines -> Customer x Item periods and movement    [pure]
    benchmark  same-item peer position                          [pure]
    detectors  metrics -> SignalDrafts for the existing engine  [pure]
    compute    load -> compute -> persist CustomerItemMetric     [I/O]
    backfill   CLI over compute                                  [I/O]

Everything above ``compute`` is a pure function of its inputs, so every number
is reproducible and testable without a database.
"""
