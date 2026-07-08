"""Tests for the Python DSL parser.

The fixture corpus in dsl/fixtures/ is the sync contract between this
parser and the JavaScript one (src/GraphIaC/web/graphiac.js): each *.giac
source is paired with the exact *.json parse result both implementations
must produce. The JS side runs the same corpus via:

    node --test src/GraphIaC/web/
"""

import json
from pathlib import Path

import pytest

from GraphIaC import dsl
from GraphIaC.dsl_registry import build_registry

FIXTURES = Path(__file__).parent.parent / "dsl" / "fixtures"
REG = build_registry()


def shape(graph):
    """Drop per-run metadata (line numbers, the inferred flag) for round-trip equality."""
    return {
        "nodes": [{k: n[k] for k in ("g_id", "type", "fields")} for n in graph["nodes"]],
        "edges": [{k: e[k] for k in ("type", "fields")} for e in graph["edges"]],
    }


@pytest.mark.parametrize("giac", sorted(FIXTURES.glob("*.giac")), ids=lambda p: p.name)
def test_fixture(giac):
    expected = json.loads(giac.with_suffix(".json").read_text())
    res = dsl.parse(giac.read_text(), REG)

    exp = expected.get("errors", [])
    assert len(res["errors"]) == len(exp), res["errors"]
    for got, e in zip(res["errors"], exp):
        assert got["line"] == e["line"], got
        assert e["includes"] in got["msg"], got

    if "graph" in expected:
        assert res["graph"] == expected["graph"]

    # error-free sources must round-trip: parse(desugar(graph)) == graph
    if not exp:
        again = dsl.parse(dsl.desugar(res["graph"], REG), REG)
        assert again["errors"] == []
        assert shape(again["graph"]) == shape(res["graph"])


def test_hash_inside_string_is_not_a_comment():
    res = dsl.parse('b : S3Bucket(bucket_name: "a#b") # real comment', REG)
    assert res["errors"] == []
    assert res["graph"]["nodes"][0]["fields"]["bucket_name"] == "a#b"


def test_constants_substitute_and_chain():
    res = dsl.parse('a = "x.co"\nb = a\nhz : HostedZone(domain_name: b)', REG)
    assert res["errors"] == []
    assert res["graph"]["nodes"][0]["fields"]["domain_name"] == "x.co"


def test_label_may_not_shadow_constant():
    res = dsl.parse('hz = "oops"\nhz : HostedZone(domain_name: "x.co")', REG)
    assert any("already a constant" in e["msg"] for e in res["errors"])


def test_ref_to_unknown_field_errors():
    src = 'cert : ACMCertificate(domain_name: "x.co")\ncf : CloudFrontDistribution(domain_name: "x.co", cert_arn: cert.nope)'
    res = dsl.parse(src, REG)
    assert any('no field "nope"' in e["msg"] for e in res["errors"])


def test_refs_of_reports_data_dependencies():
    res = dsl.parse((FIXTURES / "static-site.giac").read_text(), REG)
    assert dsl.refs_of(res["graph"]) == [{"from": "cert", "to": "cf", "field": "arn"}]


def test_endpoint_fields_cannot_be_set_as_args():
    src = 'hz : HostedZone(domain_name: "x.co")\ncert : ACMCertificate(domain_name: "x.co")\ncert -> hz : (hz_g_id: hz)'
    res = dsl.parse(src, REG)
    assert any("set by the arrow" in e["msg"] for e in res["errors"])


def test_duplicate_edge_warns_even_reversed():
    src = 'hz : HostedZone(domain_name: "x.co")\ncert : ACMCertificate(domain_name: "x.co")\ncert -> hz\nhz -> cert'
    res = dsl.parse(src, REG)
    assert res["errors"] == []
    assert len(res["warnings"]) == 1
    assert "duplicate edge" in res["warnings"][0]["msg"]


def test_unclosed_paren_errors_with_statement_line():
    res = dsl.parse('hz : HostedZone(domain_name: "x.co"', REG)
    assert res["errors"][0]["line"] == 1
    assert "unclosed" in res["errors"][0]["msg"]
