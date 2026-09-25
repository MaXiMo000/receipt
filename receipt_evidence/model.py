"""Three statuses, same shape as invariant/firedrill/carabiner.

`unverified` here means "no scope was declared" -- the task ran and the
receipt records exactly what it touched, but there was nothing to check that
against, so calling it a pass would claim more than was actually verified.
"""
PASS = "pass"
FAIL = "fail"
UNVERIFIED = "unverified"
