# demo_extra.sh (pharmacovigilance) — agent-specific payloads + content checks.
# Sourced by lib/engine/demo.sh; shares: REV, OUT, REV_U, call(), check(), pass, fail.
T_INTAKE="intake-icsr___intake_icsr"
T_FDA="openfda-lookup___openfda_lookup"
T_MASK="mask-pii___mask_pii"
T_ASSESS="assess-seriousness___assess_seriousness"
T_DRAFT="pv-core___draft_narrative"
T_AUDIT="write-audit___write_audit"
T_FINAL="pv-core___finalize_submission"

echo "  -- deny-by-default (identity -> Cedar) --"
check "pv_reviewer intake_icsr" ALLOW "$(call "$REV" "$T_INTAKE" '{"source":"Adverse event report. Suspect product: atorvastatin. Patient hospitalized with rhabdomyolysis. Unexpected."}')"
check "outsider    intake_icsr" DENY  "$(call "$OUT" "$T_INTAKE" '{"source":"Suspect product atorvastatin, rhabdomyolysis."}')"

echo "  -- aggregate FAERS background via openFDA (LIVE federal API, governed) --"
FDA_OUT="$(call "$REV" "$T_FDA" '{"drug":"atorvastatin"}')"
check "pv_reviewer openfda_lookup" ALLOW "$FDA_OUT"
if echo "$FDA_OUT" | grep -qi 'openFDA' && echo "$FDA_OUT" | grep -q '"top_reactions"'; then echo "  PASS | openfda_lookup returned AGGREGATE non-PHI FAERS background"; pass=$((pass+1)); else echo "  FAIL | openfda_lookup -> $FDA_OUT"; fail=$((fail+1)); fi

echo "  -- fail-closed PHI de-identification (mask_pii) --"
MASK_OUT="$(call "$REV" "$T_MASK" '{"case":"Patient John Doe, DOB 1970-02-15, SSN 123-45-6789, 42 Main St. Took atorvastatin; hospitalized with rhabdomyolysis."}')"
check "pv_reviewer mask_pii" ALLOW "$MASK_OUT"
if echo "$MASK_OUT" | grep -q 'REDACTED' && ! echo "$MASK_OUT" | grep -q 'John Doe'; then echo "  PASS | mask_pii redacted PHI (name/SSN removed)"; pass=$((pass+1)); else echo "  FAIL | mask_pii did NOT redact -> $MASK_OUT"; fail=$((fail+1)); fi

echo "  -- forbid: mask-before-assess (seriousness) --"
check "pv_reviewer assess (UN-masked)" DENY "$(call "$REV" "$T_ASSESS" '{"case":"Patient hospitalized with rhabdomyolysis","deidentified":false}')"
ASSESS_OUT="$(call "$REV" "$T_ASSESS" '{"case":"De-identified patient [REDACTED:NAME] hospitalized with rhabdomyolysis after atorvastatin","expectedness":"unlisted","deidentified":true}')"
check "pv_reviewer assess (de-identified)" ALLOW "$ASSESS_OUT"
if echo "$ASSESS_OUT" | grep -q '"serious": *true' && echo "$ASSESS_OUT" | grep -q 'EXPEDITED'; then echo "  PASS | assess_seriousness -> serious + EXPEDITED 15-day reporting clock (ICH E2B)"; pass=$((pass+1)); else echo "  FAIL | assess -> $ASSESS_OUT"; fail=$((fail+1)); fi

echo "  -- forbid: mask-before-model (ICSR narrative) --"
check "pv_reviewer draft (UN-masked)" DENY "$(call "$REV" "$T_DRAFT" '{"case":"x","deidentified":false}')"
DRAFT_OUT="$(call "$REV" "$T_DRAFT" '{"case":"De-identified patient [REDACTED:NAME], took atorvastatin, hospitalized with rhabdomyolysis, recovering. Serious, unexpected.","deidentified":true}')"
check "pv_reviewer draft (de-identified)" ALLOW "$DRAFT_OUT"
if echo "$DRAFT_OUT" | grep -qE '"chars": *[1-9]' && ! echo "$DRAFT_OUT" | grep -q '"error"'; then echo "  PASS | draft_narrative produced a real Bedrock CIOMS narrative"; pass=$((pass+1)); else echo "  FAIL | draft -> $DRAFT_OUT"; fail=$((fail+1)); fi
if echo "$DRAFT_OUT" | grep -q '"guardrail_applied": *true'; then echo "  PASS | narrative passed the fail-closed guardrail"; pass=$((pass+1)); else echo "  FAIL | guardrail not applied -> $DRAFT_OUT"; fail=$((fail+1)); fi

echo "  -- immutable WORM audit --"
NONCE="$RANDOM$RANDOM"
AUDIT_IN="{\"icsr_id\":\"ICSR-2026-0002\",\"action\":\"seriousness_assessment\",\"phase\":\"INTENT\",\"actor\":\"$REV_U\",\"payload\":\"run-$NONCE\"}"
A1="$(call "$REV" "$T_AUDIT" "$AUDIT_IN")"
check "pv_reviewer write_audit (1st)" ALLOW "$A1"
if echo "$A1" | grep -q '"stored": *true' && echo "$A1" | grep -q '"worm": *true'; then echo "  PASS | audit -> append-only ledger + WORM"; pass=$((pass+1)); else echo "  FAIL | audit not stored/worm -> $A1"; fail=$((fail+1)); fi
A2="$(call "$REV" "$T_AUDIT" "$AUDIT_IN")"
if echo "$A2" | grep -q '"stored": *false' && echo "$A2" | grep -qi 'append-only'; then echo "  PASS | duplicate rejected (immutable)"; pass=$((pass+1)); else echo "  FAIL | dup not rejected -> $A2"; fail=$((fail+1)); fi

echo "  -- forbid: no self-submit --"
check "pv_reviewer finalize_submission" DENY "$(call "$REV" "$T_FINAL" '{"icsr_id":"ICSR-2026-0002"}')"

echo "  == STEP TWO: deeper caseload workflows =="
T_DUP="detect-duplicate___detect_duplicate"
T_CAUS="record-causality___record_causality"
T_CAUSCOMMIT="pv-core___commit_causality"

echo "  -- duplicate-ICSR detection (don't double-report) --"
DUP_OUT="$(call "$REV" "$T_DUP" '{"case_key":"atorvastatin|rhabdomyolysis|2026-06|hcp","known_keys":"atorvastatin|rhabdomyolysis|2026-06|hcp; metformin|nausea|2026-05|patient"}')"
check "pv_reviewer detect_duplicate" ALLOW "$DUP_OUT"
if echo "$DUP_OUT" | grep -q '"duplicate_status": *"DUPLICATE"' && echo "$DUP_OUT" | grep -q '"hold": *true'; then echo "  PASS | suspected duplicate -> HELD (not reported twice)"; pass=$((pass+1)); else echo "  FAIL | detect_duplicate -> $DUP_OUT"; fail=$((fail+1)); fi

echo "  -- causality/reportability: documented, human-approved (GVP) --"
check "pv_reviewer record_causality (UN-masked)" DENY "$(call "$REV" "$T_CAUS" '{"assessment":"related","rationale":"positive dechallenge","deidentified":false}')"
CAUS_OUT="$(call "$REV" "$T_CAUS" '{"assessment":"probably related; expedited-reportable","rationale":"Positive dechallenge on statin withdrawal; temporal association and known class effect (rhabdomyolysis)","deidentified":true}')"
check "pv_reviewer record_causality (de-identified)" ALLOW "$CAUS_OUT"
if echo "$CAUS_OUT" | grep -q '"status": *"PREPARED"' && echo "$CAUS_OUT" | grep -q '"requires_senior_approval": *true'; then echo "  PASS | causality prepared + documented -> a DIFFERENT senior physician must approve"; pass=$((pass+1)); else echo "  FAIL | record_causality -> $CAUS_OUT"; fail=$((fail+1)); fi
CAUS_NORAT="$(call "$REV" "$T_CAUS" '{"assessment":"related","deidentified":true}')"
if echo "$CAUS_NORAT" | grep -qi 'requires a documented'; then echo "  PASS | causality refused without a documented rationale (mandatory)"; pass=$((pass+1)); else echo "  FAIL | causality no-rationale not refused -> $CAUS_NORAT"; fail=$((fail+1)); fi

echo "  -- forbid: no self causality-commit (senior-human-only) --"
check "pv_reviewer commit_causality" DENY "$(call "$REV" "$T_CAUSCOMMIT" '{"causality_id":"CAUS-2026-0002"}')"
