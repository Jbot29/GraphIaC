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


def test_file_value_stays_symbolic():
    res = dsl.parse('fn : CloudFrontFunction(function_code: file("f.js"))', REG)
    assert res["errors"] == []
    assert res["graph"]["nodes"][0]["fields"]["function_code"] == {"$file": {"path": "f.js"}}

    bad = dsl.parse("fn : CloudFrontFunction(function_code: file(f.js))", REG)
    assert any("quoted path" in e["msg"] for e in bad["errors"])


def test_file_is_still_a_usable_name_without_paren():
    res = dsl.parse('file = "x.co"\nhz : HostedZone(domain_name: file)', REG)
    assert res["errors"] == []
    assert res["graph"]["nodes"][0]["fields"]["domain_name"] == "x.co"


def test_unclosed_paren_errors_with_statement_line():
    res = dsl.parse('hz : HostedZone(domain_name: "x.co"', REG)
    assert res["errors"][0]["line"] == 1
    assert "unclosed" in res["errors"][0]["msg"]


def test_interpolation_takes_constants_not_node_labels():
    """Deliberate: interpolation is parse-time, and a label's only value is
    its own name — allowing it would read like a reference and not be one."""
    src = 'bucket : S3Bucket\nfn : LambdaZipFile(name: "${bucket}-fn", runtime: "python3.13", handler: "h", zip_file_path: "z")'
    res = dsl.parse(src, REG)
    assert any("only constants can be interpolated" in e["msg"] for e in res["errors"])


def test_interpolation_of_a_literal_dollar_brace_round_trips():
    src = 'b : S3Bucket(bucket_name: "\\${x}")'
    res = dsl.parse(src, REG)
    assert res["errors"] == []
    assert res["graph"]["nodes"][0]["fields"]["bucket_name"] == "${x}"
    again = dsl.parse(dsl.desugar(res["graph"], REG), REG)
    assert again["errors"] == []
    assert again["graph"]["nodes"][0]["fields"]["bucket_name"] == "${x}"


# --- modules ---------------------------------------------------------------

EXAMPLES = Path(__file__).parent.parent / "examples" / "founding"


def test_module_chapter_matches_the_hand_written_one():
    """Chapter 6 is chapter 5 with the pattern named. The point of a module
    is that it is *the same graph* — so assert exactly that, allowing only
    the label prefix a module necessarily adds."""
    hand = dsl.parse((EXAMPLES / "05-webapp" / "webapp.giac").read_text(), REG)
    mod = dsl.parse((EXAMPLES / "06-module" / "webapp.giac").read_text(), REG)
    assert hand["errors"] == [] and mod["errors"] == []

    # 05 calls them lb/db/cluster/task-role/web; 06's module prefixes with
    # the instance label. Nothing else may differ.
    rename = {"lb": "web-lb", "db": "web-db", "cluster": "web-cluster",
              "task-role": "web-task-role", "web": "web-app", "cert": "app-cert"}

    def normalize(graph):
        def g(x):
            return rename.get(x, x)

        def value(v):
            if isinstance(v, dict) and "$ref" in v:
                return {"$ref": {"g_id": g(v["$ref"]["g_id"]), "field": v["$ref"]["field"]}}
            if isinstance(v, dict):
                return {k: value(e) for k, e in v.items()}
            if isinstance(v, list):
                return [value(e) for e in v]
            return v

        nodes = sorted(
            (g(n["g_id"]), n["type"], tuple(sorted((f, str(value(x))) for f, x in n["fields"].items())))
            for n in graph["nodes"]
        )
        edges = sorted(
            (e["type"], tuple(sorted((f, str(value(g(x)) if isinstance(x, str) else value(x)))
                                     for f, x in e["fields"].items())))
            for e in graph["edges"]
        )
        guards = sorted((x["predicate"], tuple(g(a) for a in x["args"])) for x in graph["guards"])
        return nodes, edges, guards

    h_nodes, h_edges, h_guards = normalize(hand["graph"])
    m_nodes, m_edges, m_guards = normalize(mod["graph"])

    # 05 names the instance's fields directly; 06 derives db_name/name from
    # the instance label, so compare structure rather than every string
    assert [n[0] for n in m_nodes] == [n[0] for n in h_nodes]
    assert [n[1] for n in m_nodes] == [n[1] for n in h_nodes]
    assert m_edges == h_edges
    assert m_guards == h_guards


def test_module_expansion_round_trips_through_desugar():
    src = (EXAMPLES / "06-module" / "webapp.giac").read_text()
    res = dsl.parse(src, REG)
    assert res["errors"] == []
    again = dsl.parse(dsl.desugar(res["graph"], REG), REG)
    assert again["errors"] == []
    assert shape(again["graph"]) == shape(res["graph"])


def test_two_instances_do_not_collide():
    src = (
        'define pair(bucket-name) {\n'
        '    b : S3Bucket(bucket_name: bucket-name)\n'
        '    ? private(b)\n'
        '}\n'
        'one : pair(bucket-name: "one-bucket")\n'
        'two : pair(bucket-name: "two-bucket")\n'
    )
    res = dsl.parse(src, REG)
    assert res["errors"] == []
    assert [n["g_id"] for n in res["graph"]["nodes"]] == ["one-b", "two-b"]
    assert [g["args"] for g in res["graph"]["guards"]] == [["one-b"], ["two-b"]]
