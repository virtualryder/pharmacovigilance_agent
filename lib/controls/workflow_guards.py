import json

import sanitized

# workflow_guards — the machine-verifiable transition evidence for a DETERMINISTIC pharmacovigilance
# workflow controller (P0-2), ported from the financial-aid/housing control plane.
#
# THE DEFECT THIS FIXES: the ICSR pipeline (intake -> openfda -> mask -> assess -> draft -> audit ->
# signoff) was sequenced by the MODEL (workflow.entrypoint: agent.py). A prompt-injection or model error
# could skip masking or the seriousness gate, or advance a case on asserted (unverified) state. A model
# should not be the thing that guarantees regulated transitions happened.
#
# THE FIX: a deterministic controller (a Step Functions state machine, wired in the CDK/deploy layer)
# invokes this single guard Lambda BETWEEN pipeline stages; each guard returns {"guard","ok","reason"}
# and the state machine BRANCHES on `ok`. A stage cannot be skipped, reordered, or passed on unverified
# state, because the transition itself demands structural or cryptographic proof:
#
#   extracted     -> intake actually produced the load-bearing decision field (suspect product)
#   deidentified  -> a VERIFIED mask_pii-signed sanitized_ref exists (P0-1; a boolean is never accepted)
#   background    -> openFDA FAERS background is honest (never a fabricated aggregate on source failure)
#   rules_executed-> the deterministic seriousness engine ran and returned a legal reporting category
#   duplicate     -> a case detected as a duplicate HOLDS (no double-reporting to the regulator)
#
# Fail-closed: any missing/forged/tampered/malformed evidence -> ok:false; the controller routes to
# ManualReview / a HOLD, never onward. Pure logic + the shared verifiers, fully unit-testable offline.
# (This module is the portable heart of P0-2; wiring the Step Functions controller is the CDK/deploy
# follow-on — the guards it will branch on are proven here.)

_LEGAL_CATEGORIES = {"EXPEDITED", "PERIODIC", "ROUTINE"}


def _coerce(e):
    e = e or {}
    if isinstance(e, str):
        try:
            e = json.loads(e)
        except Exception:
            e = {}
    return e


def _as_dict(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {}
    return v if isinstance(v, dict) else {}


def guard_extracted(e):
    """intake_icsr must yield the suspect product — the load-bearing field for the whole case. Event
    terms and seriousness flags may be absent (assessed from the masked narrative / explicit flags)."""
    f = _as_dict(e.get("fields")) or {}
    ok = bool(str(f.get("suspect_product") or "").strip())
    return ok, ("suspect_product present" if ok else "intake did not yield a suspect_product")


def guard_deidentified(e):
    """PHI de-identification must be PROVEN by a mask_pii-signed sanitized_ref (P0-1), never a boolean."""
    ok = sanitized.verify_ref(e.get("sanitized_ref"))
    return ok, ("masking proven by a verified mask_pii-signed sanitized_ref" if ok else
                "de-identification not proven (no valid sanitized_ref; a boolean is not proof)")


def guard_background(e):
    """openFDA FAERS background is CONTEXT ONLY (it never feeds the seriousness determination), so a
    missing/unavailable background does NOT block. But it must be HONEST: a lookup that claims found +
    authoritative MUST carry real aggregate terms — anti-fabrication (openFDA never substitutes a canned
    aggregate on failure). A malformed 'authoritative' background fails closed."""
    lk = _as_dict(e.get("lookup")) or _as_dict(e.get("background"))
    if not lk:
        return True, "no openFDA background supplied (optional context) — allowed"
    if lk.get("found") is False:
        return True, "openFDA background unavailable (honest found:false, no fabricated aggregate) — allowed"
    if lk.get("authoritative") is True and not (lk.get("top_reactions") or lk.get("top_reactions_detail")):
        return False, "openFDA background claims authoritative but carries no aggregate terms (possible fabrication)"
    return True, "openFDA background present and well-formed (aggregate, non-PHI)"


def guard_rules_executed(e):
    """The deterministic seriousness engine must have run and returned a legal reporting category."""
    a = _as_dict(e.get("assessment")) or e
    ok = a.get("assessed") is True and a.get("reporting_category") in _LEGAL_CATEGORIES
    return ok, ("deterministic seriousness engine produced a legal reporting category" if ok else
                "seriousness engine did not run or returned no legal reporting category")


def guard_duplicate(e):
    """Pilot-core safety gate: a case detected as a DUPLICATE must HOLD — it must not be submitted a
    second time to the regulator. Returns ok=False for a duplicate, which the controller routes to a
    DuplicateHold terminal state (a work queue for the safety team, not an error)."""
    d = _as_dict(e.get("duplicate")) or e
    held = d.get("duplicate_status") == "DUPLICATE" or bool(d.get("hold"))
    return (not held), ("no duplicate hold" if not held else
                        "case held: detected as a duplicate ICSR — must not be double-reported")


_GUARDS = {
    "extracted": guard_extracted,
    "deidentified": guard_deidentified,
    "background": guard_background,
    "rules_executed": guard_rules_executed,
    "duplicate": guard_duplicate,
}


def _emit_metric(guard, ok):
    """Security telemetry: every guard evaluation emits a CloudWatch EMF metric
    (Pharmacovigilance/Governance :: GuardFailed{Guard}). A failed guard is a SECURITY SIGNAL — forged
    sanitized_ref, fabricated background — not just an ops event. Metric only (no payload content)."""
    import json as _json
    import time as _time
    try:
        print(_json.dumps({
            "_aws": {"Timestamp": int(_time.time() * 1000),
                     "CloudWatchMetrics": [{"Namespace": "Pharmacovigilance/Governance",
                                            "Dimensions": [["Guard"]],
                                            "Metrics": [{"Name": "GuardFailed", "Unit": "Count"}]}]},
            "Guard": guard, "GuardFailed": 0 if ok else 1}))
    except Exception:
        pass   # metrics must never affect the control decision


def handler(event, context):
    e = _coerce(event)
    name = str(e.get("guard", ""))
    fn = _GUARDS.get(name)
    if fn is None:
        _emit_metric(name or "unknown", False)
        return {"guard": name, "ok": False, "reason": "unknown guard (fail-closed)"}
    try:
        ok, reason = fn(e)
    except Exception as exc:  # any guard error is a fail-closed deny, never a pass
        ok, reason = False, "guard error (fail-closed): %s" % type(exc).__name__
    _emit_metric(name, ok)
    return {"guard": name, "ok": bool(ok), "reason": reason}
