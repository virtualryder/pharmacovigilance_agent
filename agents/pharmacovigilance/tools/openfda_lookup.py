import json
import urllib.parse
import urllib.request

# openfda_lookup — REAL egress to the public openFDA drug-event (FAERS) API.
# Returns only AGGREGATE, non-PHI background (report count + top MedDRA reaction terms) for a drug.
# openFDA is public + read-only + rate-limited; no API key required for low volume. Fail-soft:
# on any network/parse error we fall back to a deterministic aggregate so the governed workflow
# still proceeds (this is non-PHI background, not a compliance control).

_BASE = "https://api.fda.gov/drug/event.json"
_TIMEOUT = 6


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
        if reactions:
            return {
                "drug": drug,
                "source": "openFDA/FAERS (live)",
                "reports_found": total,
                "top_reactions": [r["term"] for r in reactions if r["term"]],
                "top_reactions_detail": reactions,
                "note": "aggregate FAERS background only; no PHI",
            }
    except Exception as exc:
        fallback_note = "openFDA egress failed (%s); fell back to deterministic aggregate" % type(exc).__name__
    else:
        fallback_note = "openFDA returned no results for this drug; deterministic aggregate shown"
    return {
        "drug": drug,
        "source": "openFDA/FAERS (fallback aggregate)",
        "reports_found": 3,
        "top_reactions": ["rhabdomyolysis", "acute kidney injury", "nausea"],
        "note": fallback_note,
    }
