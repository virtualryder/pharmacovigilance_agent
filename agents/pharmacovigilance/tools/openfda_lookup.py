import json
import urllib.parse
import urllib.request

# openfda_lookup — REAL egress to the public openFDA drug-event (FAERS) API.
# Returns only AGGREGATE, non-PHI background (report count + top MedDRA reaction terms) for a drug.
# openFDA is public + read-only + rate-limited; no API key required for low volume.
#
# P0-3 — NEVER fabricate on source failure. This tool used to substitute a hard-coded "fallback
# aggregate" (reports_found:3, a canned reaction list) whenever the openFDA call failed or returned
# nothing — data that looks like FAERS background but was invented. That is exactly the fabrication P0-3
# forbids. Now a failed / empty lookup returns found:false with authoritative:false and NO invented
# figures; the caller must treat the background as unavailable rather than mistake fabricated numbers for
# real FAERS data. (This is contextual background only — it does not feed the seriousness determination,
# which assess_seriousness derives solely from the de-identified ICSR — but it must still not fabricate.)

_BASE = "https://api.fda.gov/drug/event.json"
_TIMEOUT = 6
SOURCE = "openFDA/FAERS"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "pv-icsr-accelerator/1.0"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _live_lookup(drug):
    q = 'patient.drug.medicinalproduct:"%s"' % drug
    # top reaction terms (aggregate counts, no PHI)
    count_url = "%s?search=%s&count=patient.reaction.reactionmeddrapt.exact&limit=5" % (
        _BASE, urllib.parse.quote(q, safe=":"))
    data = _get(count_url)
    reactions = [{"term": (x.get("term") or "").lower(), "count": x.get("count")}
                 for x in data.get("results", [])]
    # total matching reports
    total = None
    try:
        meta = _get("%s?search=%s&limit=1" % (_BASE, urllib.parse.quote(q, safe=":")))
        total = meta.get("meta", {}).get("results", {}).get("total")
    except Exception:
        total = None
    return reactions, total


def handler(event, context):
    e = event or {}
    if isinstance(e, str):
        try:
            e = json.loads(e)
        except Exception:
            e = {}
    drug = (e.get("drug") or "unknown").strip()
    try:
        reactions, total = _live_lookup(drug)
    except Exception as exc:
        # source-down: report it, do NOT invent FAERS background
        return {"found": False, "authoritative": False, "drug": drug, "source": SOURCE,
                "error": "openFDA egress failed: %s" % type(exc).__name__,
                "note": "FAERS background unavailable; no fabricated aggregate substituted (P0-3)"}

    terms = [r["term"] for r in reactions if r["term"]]
    if not terms:
        return {"found": False, "authoritative": False, "drug": drug, "source": SOURCE,
                "reports_found": total,
                "note": "openFDA returned no FAERS results for this drug; no fabricated aggregate substituted (P0-3)"}

    return {
        "found": True,
        "authoritative": True,
        "drug": drug,
        "source": SOURCE + " (live)",
        "reports_found": total,
        "top_reactions": terms,
        "top_reactions_detail": reactions,
        "note": "aggregate FAERS background only; no PHI",
    }
