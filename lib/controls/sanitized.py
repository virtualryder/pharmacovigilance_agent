import hashlib
import json
import os

import tenancy
import time
import uuid

import provenance  # shared HMAC signer/verifier (bundled beside this module at deploy; on sys.path in tests)

# sanitized.py — server-issued SANITIZED-ARTIFACT references (P0-1), ported from the proven
# financial-aid/housing control plane.
#
# THE DEFECT THIS FIXES: downstream tools (assess_seriousness, record_causality, draft_narrative)
# authorized on a `deidentified: true` BOOLEAN that arrives in the tool call body — i.e. a claim the
# model/caller makes about masking, not proof that masking actually occurred.
# `{"case":"<unmasked PHI>","deidentified":true}` satisfied both the Cedar forbid and the in-body
# check. A flag you can type is not a control.
#
# THE FIX (same pattern as the openFDA provenance signer, provenance.py): the ONLY component that
# actually performed the masking (mask_pii, which alone ran Comprehend DetectPiiEntities) MINTS a
# signed sanitized-artifact reference over the EXACT masked content:
#
#     sanitized_ref = { artifact_id, sanitized_sha256, engine, entities_masked, ts, source,
#                       authoritative, sig, alg }
#
# signed with the per-deploy PROVENANCE_SECRET (never in the repo). Downstream tools accept ONLY this
# reference and VERIFY the signature fail-closed before treating the input as de-identified:
#   * signature verification  -> PROOF-OF-MASKING: only mask_pii (holder of the secret) can mint a ref,
#     so a caller cannot fabricate "this was masked."
#   * sanitized_sha256        -> CONTENT BINDING: any tool that consumes the sanitized TEXT must prove
#     the text it holds hashes to the signed digest — the model cannot substitute unmasked content,
#     because unmasked content cannot hash to the digest of the masked artifact.
#   * artifact store          -> CONTENT CHANNEL: when a store is configured (SANITIZED_TABLE), the
#     sanitized payload is persisted server-side at mint and retrieved server-side by consumers, so the
#     masked content need not travel through the model at all. Without a store the hash binding still
#     guarantees integrity.
#
# The Cedar `unless context.input.deidentified == true` forbid REMAINS as a coarse gateway-level gate
# (defense in depth), but it is no longer the authoritative control: the tools authorize on the verified
# reference, never on the boolean. Fail-closed everywhere: no secret, no ref, bad signature, or a hash
# mismatch all refuse — they never degrade to trusting the caller.
#
# NOTE (scope): this port uses the single per-deploy PROVENANCE_SECRET that already signs openFDA
# provenance here. Splitting the masking key from the source-provenance key (GA-2 domain-split, as in
# the financial-aid agent) is a follow-on hardening; it is not required for the boolean->ref fix.

SOURCE = "mask_pii — fail-closed PHI/PII de-identification (server-issued sanitized artifact)"
_TABLE_ENV = "SANITIZED_TABLE"
_TTL_SECONDS = int(os.environ.get("SANITIZED_TTL_SECONDS", "86400"))  # artifacts are transient working data

# Signed field set. Rebuilt by the VERIFIER from the ref it was handed, so tampering with any field
# (including the content digest) breaks the signature.
_SIGNED_FIELDS = ("artifact_id", "sanitized_sha256", "engine", "entities_masked", "ts")


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ── artifact store (server-side content channel) ──────────────────────────────
class MemoryStore:
    """In-process store for tests/offline runs."""
    def __init__(self):
        self.items = {}

    def put(self, artifact_id, text, meta):
        self.items[artifact_id] = {"text": text, "meta": meta}
        return True

    def get(self, artifact_id):
        it = self.items.get(artifact_id)
        return it["text"] if it else None


class DynamoStore:
    """DynamoDB-backed store (table from SANITIZED_TABLE; PutItem/GetItem least privilege, TTL attr)."""
    def __init__(self, table_name):
        import boto3
        self._t = boto3.resource("dynamodb").Table(table_name)

    def put(self, artifact_id, text, meta):
        item = {"artifact_id": artifact_id, "text": text,
                "expires_at": int(time.time()) + _TTL_SECONDS}
        item.update({k: v for k, v in (meta or {}).items() if k not in item})
        self._t.put_item(Item=item)
        return True

    def get(self, artifact_id):
        it = self._t.get_item(Key={"artifact_id": artifact_id}).get("Item")
        return it.get("text") if it else None


def default_store():
    """DDB store when SANITIZED_TABLE is configured; else None (hash-binding still enforces integrity)."""
    name = tenancy.route_store(os.environ.get(_TABLE_ENV, ""), "sanitized-artifacts")
    if not name:
        return None
    try:
        return DynamoStore(name)
    except Exception:
        return None  # fail-open ONLY on the storage channel; the signature/hash controls stay fail-closed


# ── mint / parse / verify / load ─────────────────────────────────────────────
def mint_ref(masked_text, engine, entities_masked=0, store=None):
    """Called ONLY by mask_pii after masking succeeded. Signs the exact masked content and (when a
    store is available) persists it server-side. authoritative=False when no secret is configured — a
    mask running without the secret self-reports unproven rather than pretending."""
    fields = {
        "artifact_id": uuid.uuid4().hex,
        "sanitized_sha256": sha256_text(masked_text),
        "engine": engine,
        "entities_masked": int(entities_masked or 0),
        "ts": int(time.time()),
    }
    tok = provenance.sign(SOURCE, fields)
    ref = dict(fields)
    ref.update({"source": SOURCE, "authoritative": tok["authoritative"],
                "sig": tok["sig"], "alg": tok["alg"]})
    st = store if store is not None else default_store()
    stored = False
    if st is not None:
        try:
            stored = bool(st.put(fields["artifact_id"], masked_text,
                                 {"sanitized_sha256": fields["sanitized_sha256"], "engine": engine}))
        except Exception:
            stored = False
    ref["stored"] = stored
    return ref


def parse_ref(raw):
    """Accept a dict or a JSON string (refs cross the gateway as JSON). Anything else is not a ref."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else None
        except Exception:
            return None
    return None


def verify_ref(ref):
    """True ONLY if `ref` carries a signature minted by mask_pii over exactly these fields (proof of
    masking). Missing/None/tampered/forged/unsigned -> False. Never trusts a bare boolean."""
    ref = parse_ref(ref)
    if not isinstance(ref, dict):
        return False
    fields = {k: ref.get(k) for k in _SIGNED_FIELDS}
    if not fields.get("artifact_id") or not fields.get("sanitized_sha256"):
        return False
    try:
        fields["entities_masked"] = int(fields.get("entities_masked") or 0)
        fields["ts"] = int(fields.get("ts") or 0)
    except Exception:
        return False
    return provenance.verify(ref.get("source", SOURCE), fields, ref)


def load_text(ref, candidate_text=None, store=None):
    """Return the sanitized content for a VERIFIED ref, or None (fail-closed).
    Resolution order:
      1. server-side store fetch by artifact_id, content re-hashed against the signed digest;
      2. else `candidate_text` (content the caller already holds) ONLY if it hashes to the signed
         digest — the hash binding makes it impossible to substitute unmasked content.
    """
    ref = parse_ref(ref)
    if not verify_ref(ref):
        return None
    want = ref.get("sanitized_sha256")
    st = store if store is not None else default_store()
    if st is not None:
        try:
            text = st.get(ref.get("artifact_id"))
        except Exception:
            text = None
        if text is not None and sha256_text(text) == want:
            return text
    if isinstance(candidate_text, str) and sha256_text(candidate_text) == want:
        return candidate_text
    return None
