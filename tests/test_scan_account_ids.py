"""The account-id gate must stay narrow enough to be believed and wide enough to fire.

Written 2026-09-09 alongside L67. Committing the run-7 gate evidence, `tools/scan_account_ids.py`
reported two "real AWS account id(s)" that were the trailing field of a Lambda request UUID
(35c572b3-d0da-404e-9377-039106473245). The cheap response was to rewrite those digits - to corrupt
an evidence file so an instrument would go green. The instrument was wrong, so the instrument was
narrowed; and a narrowing of a security gate is worthless unless something asserts that every shape
it is FOR still fires. That is what most of this file is.
"""
import importlib.util
import pathlib
import sys

import pytest

SCANNER = pathlib.Path(__file__).resolve().parents[1] / "tools" / "scan_account_ids.py"
REAL = "864217980669"   # shape only - the id this portfolio redacts


def _load():
    spec = importlib.util.spec_from_file_location("scan_account_ids_under_test", SCANNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def scanner():
    return _load()


def _findings(scanner, content, path="evidence/x.json"):
    return scanner.scan(["%s:1:%s" % (path, content)])


# --- the shapes the gate exists to catch: every one must still fire ---------------------

@pytest.mark.parametrize("content", [
    'arn:aws:lambda:us-east-1:%s:function:ben-fp2-obs' % REAL,
    'arn:aws-us-gov:s3:::%s-bucket' % REAL,
    '%s.dkr.ecr.us-east-1.amazonaws.com/aegis' % REAL,
    '"account": "%s"' % REAL,
    'account=%s' % REAL,
    'Account **%s** - us-east-1' % REAL,
    'account %s / us-east-1' % REAL,
    '"account_id": "%s"' % REAL,            # L67b: the AWS CLI's own spelling
    '"awsAccountId": "%s"' % REAL,
    '"Account": "%s"' % REAL,               # sts get-caller-identity
    'AWS_ACCOUNT_ID=%s' % REAL,
    'codebuild-sources-%s-us-east-1' % REAL,
    'ben-mt2-pha-a-worm-%s' % REAL,
])
def test_a_real_account_id_still_fires(scanner, content):
    assert _findings(scanner, content), "gate went blind on: %s" % content


def test_the_uuid_exclusion_does_not_swallow_an_account_id_later_on_the_same_line(scanner):
    """The exclusion is positional. One UUID on a line must not amnesty the whole line."""
    content = ('request 35c572b3-d0da-404e-9377-039106473245 '
               'arn:aws:sts::%s:assumed-role/x' % REAL)
    found = _findings(scanner, content)
    assert [f[2] for f in found] == [REAL]


# --- the shape that is not an account id -----------------------------------------------

def test_a_lambda_request_uuid_is_not_a_finding(scanner):
    content = ('"excerpt": "2026-09-09T02:32:56.470Z\\t35c572b3-d0da-404e-9377-039106473245'
               '\\tERROR\\t(node:2) [DEP0169] DeprecationWarning"')
    assert _findings(scanner, content) == []


def test_an_all_digit_uuid_tail_in_a_trace_id_is_not_a_finding(scanner):
    assert _findings(scanner, '"session": "aegis-084a2568-0022-f468-2955-897980c082c6"') == []


def test_the_exclusion_requires_a_full_uuid_prefix_not_just_a_hyphen(scanner):
    """`something-<12 digits>` with no UUID in front of it is still a finding."""
    assert _findings(scanner, 'ben-fp2-lineage-%s' % REAL)


def test_placeholders_are_never_findings(scanner):
    for placeholder in sorted(scanner.ALLOWED):
        assert _findings(scanner, 'arn:aws:iam::%s:root' % placeholder) == []


# --- guard on the guard ----------------------------------------------------------------

def test_binary_paths_are_skipped_but_json_is_not(scanner):
    line = 'arn:aws:iam::%s:root' % REAL
    assert _findings(scanner, line, path="docs/deck.pptx") == []
    assert _findings(scanner, line, path="evidence/run.json")