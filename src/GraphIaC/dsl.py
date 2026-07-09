"""GraphIaC DSL v0.1 — the Python parser (the engine's side of the language).

A faithful twin of src/GraphIaC/web/graphiac.js: same grammar, same graph
JSON, same error wording, same desugar output. The two implementations are
kept in sync by the shared fixture corpus in dsl/fixtures/ — every *.giac
source must parse to the exact *.json result in both. When changing one
parser, change the other and run both suites:

    pytest tests/test_dsl.py
    node --test src/GraphIaC/web/

See dsl/spec.md for the language. In one line each:

    label : Type(args)     a node — the label IS the g_id, and defaults
                           into the type's name field
    a -> b                 an edge — type inferred from the node-type pair;
                           `: Type(args)` makes it explicit
    name = value           a constant, substituted at parse time
    other.field            an attribute reference ($ref) — a data
                           dependency the planner resolves from live state
    #                      a comment

parse() returns plain JSON (the graph), not model instances: unresolved
$refs mean a node may not validate as a Pydantic model until plan time.
All AWS knowledge comes from the registry (dsl_registry.build_registry).
"""

import re

from pydantic import BaseModel

from GraphIaC.dsl_registry import build_registry

VERSION = "0.1"
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")  # labels may contain dashes
TYPE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)?\s*(\(([\s\S]*)\))?$")  # Type, Type(...), or (...)
IDENT_AT = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
NUM_AT = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)")


_FAIL = object()  # sentinel: a value that failed to resolve (None is a real value)


def _clip(s):
    s = str(s).strip()
    return s[:40] + "…" if len(s) > 40 else s


# ---------------------------------------------------------------------
# Lines -> statements. A '#' outside a string starts a comment; a
# statement continues across lines while ( [ { stay open.
# ---------------------------------------------------------------------
def strip_comment(line):
    in_str = False
    i = 0
    while i < len(line):
        c = line[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "#":
            return line[:i]
        i += 1
    return line


def _depth_delta(text):
    d, in_str, i = 0, False, 0
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "([{":
            d += 1
        elif c in ")]}":
            d -= 1
        i += 1
    return d


def _to_statements(src):
    out = []
    buf, start_ln, depth = None, 0, 0
    for i, raw in enumerate(str(src).split("\n")):
        text = strip_comment(raw)
        if buf is None:
            if text.strip() == "":
                continue
            buf, start_ln, depth = text, i + 1, _depth_delta(text)
        else:
            buf += "\n" + text
            depth += _depth_delta(text)
        if depth <= 0:
            out.append({"ln": start_ln, "text": buf.strip()})
            buf, depth = None, 0
    if buf is not None:
        out.append({"ln": start_ln, "text": buf.strip(), "unclosed": True})
    return out


def _index_top_level(text, tok):
    """Index of `tok` outside strings and outside any brackets, or -1."""
    d, in_str, i = 0, False, 0
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "([{":
            d += 1
        elif c in ")]}":
            d -= 1
        elif d == 0 and text.startswith(tok, i):
            return i
        i += 1
    return -1


# ---------------------------------------------------------------------
# Value scanner — strings, numbers, booleans, lists, maps, identifiers,
# and dotted attribute references (other.field). Returns TAGGED values;
# _resolve() turns them into the plain JSON the graph carries.
# ---------------------------------------------------------------------
class _Scanner:
    def __init__(self, s, ln, err):
        self.s, self.ln, self.err, self.i = s, ln, err, 0

    def ws(self):
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def eof(self):
        self.ws()
        return self.i >= len(self.s)

    def rest(self):
        return self.s[self.i :]

    def at(self):
        return self.s[self.i] if self.i < len(self.s) else ""

    def ident(self):
        self.ws()
        m = IDENT_AT.match(self.s, self.i)
        if not m or m.start() != self.i:
            return None
        self.i = m.end()
        return m.group(0)

    def _string(self):
        self.i += 1  # opening quote
        v = ""
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\" and self.i + 1 < len(self.s):
                v += self.s[self.i + 1]
                self.i += 2
                continue
            if c == '"':
                self.i += 1
                return {"t": "str", "v": v}
            v += c
            self.i += 1
        self.err(self.ln, "unterminated string")
        return None

    def value(self):
        self.ws()
        if self.i >= len(self.s):
            self.err(self.ln, "expected a value")
            return None
        c = self.s[self.i]
        if c == '"':
            return self._string()
        if c == "[":
            self.i += 1
            v = []
            self.ws()
            if self.at() == "]":
                self.i += 1
                return {"t": "list", "v": v}
            while True:
                e = self.value()
                if e is None:
                    return None
                v.append(e)
                self.ws()
                if self.at() == ",":
                    self.i += 1
                    continue
                if self.at() == "]":
                    self.i += 1
                    return {"t": "list", "v": v}
                self.err(self.ln, "expected , or ] in list")
                return None
        if c == "{":
            self.i += 1
            v = {}
            self.ws()
            if self.at() == "}":
                self.i += 1
                return {"t": "map", "v": v}
            while True:
                self.ws()
                if self.at() == '"':
                    ks = self._string()
                    if ks is None:
                        return None
                    k = ks["v"]
                else:
                    k = self.ident()
                if not k:
                    self.err(self.ln, "expected a key in map")
                    return None
                self.ws()
                if self.at() != ":":
                    self.err(self.ln, f'expected : after map key "{k}"')
                    return None
                self.i += 1
                e = self.value()
                if e is None:
                    return None
                v[k] = e
                self.ws()
                if self.at() == ",":
                    self.i += 1
                    continue
                if self.at() == "}":
                    self.i += 1
                    return {"t": "map", "v": v}
                self.err(self.ln, "expected , or } in map")
                return None
        m = NUM_AT.match(self.s, self.i)
        if m and m.start() == self.i:
            self.i = m.end()
            tok = m.group(0)
            return {"t": "num", "v": float(tok) if "." in tok else int(tok)}
        ident = self.ident()
        if ident:
            if ident in ("true", "false"):
                return {"t": "bool", "v": ident == "true"}
            if ident == "file" and self.at() == "(":
                self.i += 1
                self.ws()
                if self.at() != '"':
                    self.err(self.ln, 'file(...) takes a quoted path — e.g. file("handler.js")')
                    return None
                p = self._string()
                if p is None:
                    return None
                self.ws()
                if self.at() != ")":
                    self.err(self.ln, "expected ) after file path")
                    return None
                self.i += 1
                return {"t": "fileval", "path": p["v"]}
            if self.at() == ".":
                self.i += 1
                f = self.ident()
                if not f:
                    self.err(self.ln, f'expected a field name after "{ident}."')
                    return None
                return {"t": "ref", "g_id": ident, "field": f}
            return {"t": "ident", "v": ident}
        self.err(self.ln, f'bad value at "{_clip(self.rest())}"')
        return None


def _parse_args(inner, ln, err):
    """`(args)` body -> (positional, named) of tagged values."""
    named, positional = {}, None
    if inner is None or inner.strip() == "":
        return positional, named
    sc = _Scanner(inner, ln, err)
    first = True
    while True:
        if sc.eof():
            break
        save = sc.i
        ident = sc.ident()
        sc.ws()
        name, v = None, None
        if ident and sc.at() == ":":
            sc.i += 1
            name = ident
            v = sc.value()
        else:
            sc.i = save
            v = sc.value()
        if v is None:
            return positional, named
        if name:
            if name in named:
                err(ln, f'argument "{name}" given twice')
            named[name] = v
        elif first:
            positional = v
        else:
            err(ln, "only one leading positional argument is allowed — name the rest")
        first = False
        sc.ws()
        if sc.eof():
            break
        if sc.at() == ",":
            sc.i += 1
            continue
        err(ln, f'expected , between arguments — at "{_clip(sc.rest())}"')
        return positional, named
    return positional, named


# ---------------------------------------------------------------------
# PARSE — source -> { graph: { nodes, edges }, errors, warnings }.
# Declaration order is free: constants first, then all node labels are
# collected, THEN fields resolve — a node may reference one defined below.
# ---------------------------------------------------------------------
def parse(src, registry=None):
    registry = registry or build_registry()
    errors, warnings = [], []

    def err(ln, msg):
        errors.append({"line": ln, "msg": msg})

    def warn(ln, msg):
        warnings.append({"line": ln, "msg": msg})

    graph = {"nodes": [], "edges": []}

    # ---- classify statements ----
    const_stmts, node_stmts, edge_stmts = [], [], []
    for st in _to_statements(src):
        if st.get("unclosed"):
            err(st["ln"], "unclosed ( [ or { — statement never ends")
            continue
        if _index_top_level(st["text"], "->") >= 0:
            edge_stmts.append(st)
        elif _index_top_level(st["text"], "=") >= 0:
            const_stmts.append(st)
        elif _index_top_level(st["text"], ":") >= 0:
            node_stmts.append(st)
        else:
            err(st["ln"], f'unrecognized statement (expected name = value, label : Type, or a -> b): "{_clip(st["text"])}"')

    # a tagged value -> the plain JSON the graph carries
    def _resolve(v, ln, consts, nodes, refs_allowed):
        t = v["t"]
        if t in ("str", "num", "bool"):
            return v["v"]
        if t == "list":
            out = []
            for e in v["v"]:
                r = _resolve(e, ln, consts, nodes, refs_allowed)
                if r is _FAIL:
                    return _FAIL
                out.append(r)
            return out
        if t == "map":
            out = {}
            for k, e in v["v"].items():
                r = _resolve(e, ln, consts, nodes, refs_allowed)
                if r is _FAIL:
                    return _FAIL
                out[k] = r
            return out
        if t == "fileval":
            # stays symbolic — load_graph reads the file, relative to the
            # source file's directory; parse never touches the disk
            return {"$file": {"path": v["path"]}}
        if t == "ident":
            if v["v"] in consts:
                return consts[v["v"]]
            if nodes is not None and v["v"] in nodes:
                return v["v"]  # a bare label means its g_id
            err(ln, f'unknown name "{v["v"]}" — not a constant{" or node label" if nodes is not None else ""}')
            return _FAIL
        if t == "ref":
            if not refs_allowed:
                err(ln, f'attribute references ({v["g_id"]}.{v["field"]}) are not allowed here')
                return _FAIL
            target = nodes.get(v["g_id"])
            if not target:
                err(ln, f'reference to unknown node "{v["g_id"]}" in {v["g_id"]}.{v["field"]}')
                return _FAIL
            reg = registry["nodes"][target["type"]]
            if v["field"] not in reg["fields"]:
                err(ln, f'{target["type"]} has no field "{v["field"]}" (in {v["g_id"]}.{v["field"]})')
                return _FAIL
            return {"$ref": {"g_id": v["g_id"], "field": v["field"]}}
        return _FAIL

    # ---- constants (parse-time only; may use earlier constants) ----
    consts = {}
    for st in const_stmts:
        eq = _index_top_level(st["text"], "=")
        name = st["text"][:eq].strip()
        if not IDENT.match(name):
            err(st["ln"], f'bad constant name "{_clip(name)}"')
            continue
        sc = _Scanner(st["text"][eq + 1 :], st["ln"], err)
        v = sc.value()
        if v is None:
            continue
        if not sc.eof():
            err(st["ln"], f'unexpected text after constant value: "{_clip(sc.rest())}"')
            continue
        plain = _resolve(v, st["ln"], consts, None, False)
        if plain is _FAIL:
            continue
        if name in consts:
            warn(st["ln"], f'constant "{name}" redefined')
        consts[name] = plain

    # ---- nodes, pass 1: collect every label and type ----
    nodes = {}
    for st in node_stmts:
        ci = _index_top_level(st["text"], ":")
        label = st["text"][:ci].strip()
        rest = st["text"][ci + 1 :].strip()
        if not IDENT.match(label):
            err(st["ln"], f'bad label "{_clip(label)}"')
            continue
        if label in consts:
            err(st["ln"], f'"{label}" is already a constant — labels and constants share one namespace')
            continue
        if label in nodes:
            err(st["ln"], f'duplicate label "{label}" (first defined on line {nodes[label]["line"]})')
            continue
        m = TYPE_RE.match(rest)
        if not m or not m.group(1):
            err(st["ln"], f'expected a node type after "{label} :"')
            continue
        type_name = m.group(1)
        if type_name not in registry["nodes"]:
            if type_name in registry["edges"]:
                err(st["ln"], f'"{type_name}" is an edge type — edges are written a -> b')
            else:
                err(st["ln"], f'unknown node type "{type_name}"')
            continue
        nodes[label] = {"g_id": label, "type": type_name, "fields": {}, "line": st["ln"], "args_raw": m.group(3) or ""}

    # ---- nodes, pass 2: resolve fields, default the name, check required ----
    for node in nodes.values():
        reg = registry["nodes"][node["type"]]
        positional, named = _parse_args(node["args_raw"], node["line"], err)
        fields = {}
        if positional is not None:
            if not reg["nameField"]:
                err(node["line"], f'{node["type"]} has no name field — a positional argument means nothing here; name every field')
            else:
                r = _resolve(positional, node["line"], consts, nodes, True)
                if r is not _FAIL:
                    fields[reg["nameField"]] = r
        for f, v in named.items():
            if f not in reg["fields"]:
                err(node["line"], f'{node["type"]} has no field "{f}"')
                continue
            if f in fields:
                err(node["line"], f'field "{f}" already set by the positional argument')
                continue
            r = _resolve(v, node["line"], consts, nodes, True)
            if r is not _FAIL:
                fields[f] = r
        if reg["nameField"] and reg["nameField"] not in fields:
            fields[reg["nameField"]] = node["g_id"]  # the label names the thing
        for f, info in reg["fields"].items():
            if info["required"] and f not in fields:
                err(node["line"], f'{node["type"]} "{node["g_id"]}" is missing required field "{f}"')
        node["fields"] = fields
        graph["nodes"].append({"g_id": node["g_id"], "type": node["type"], "fields": fields, "line": node["line"]})

    # ---- edges ----
    seen_edges = set()
    for st in edge_stmts:
        ai = _index_top_level(st["text"], "->")
        a_label = st["text"][:ai].strip()
        rhs = st["text"][ai + 2 :].strip()
        if _index_top_level(rhs, "->") >= 0:
            err(st["ln"], "one arrow per statement")
            continue
        ci = _index_top_level(rhs, ":")
        b_label = (rhs if ci < 0 else rhs[:ci]).strip()
        clause = None if ci < 0 else rhs[ci + 1 :].strip()

        ok = True
        for lbl in (a_label, b_label):
            if lbl not in nodes:
                err(st["ln"], f'"{lbl}" is a constant, not a node' if lbl in consts else f'unknown node "{lbl}" in edge')
                ok = False
        if not ok:
            continue
        if a_label == b_label:
            err(st["ln"], f'a node cannot connect to itself ("{a_label}")')
            continue

        # optional `: EdgeType(args)` / `: EdgeType` / `: (args)`
        explicit_type, args_raw = None, ""
        if clause is not None:
            m = TYPE_RE.match(clause)
            if not m or (not m.group(1) and not m.group(2)):
                err(st["ln"], f'bad edge clause ": {_clip(clause)}"')
                continue
            explicit_type = m.group(1) or None
            args_raw = m.group(3) or ""

        # the pair of node types picks the edge; arrow order is normalized
        ta, tb = nodes[a_label]["type"], nodes[b_label]["type"]
        src_label, dst_label = a_label, b_label
        if explicit_type:
            reg = registry["edges"].get(explicit_type)
            if not reg:
                err(st["ln"], f'unknown edge type "{explicit_type}"')
                continue
            if reg["source"]["type"] == ta and reg["dest"]["type"] == tb:
                pass  # as written
            elif reg["source"]["type"] == tb and reg["dest"]["type"] == ta:
                src_label, dst_label = b_label, a_label
            else:
                err(st["ln"], f'{explicit_type} connects {reg["source"]["type"]} -> {reg["dest"]["type"]}, not {ta} -> {tb}')
                continue
            type_name = explicit_type
        else:
            matches = []
            for name, reg in registry["edges"].items():
                if reg["source"]["type"] == ta and reg["dest"]["type"] == tb:
                    matches.append((name, False))
                elif reg["source"]["type"] == tb and reg["dest"]["type"] == ta:
                    matches.append((name, True))
            if not matches:
                err(st["ln"], f"no edge type known between {ta} and {tb} — see the inference table in dsl/spec.md")
                continue
            if len(matches) > 1:
                err(st["ln"], f'ambiguous edge between {ta} and {tb} ({", ".join(m[0] for m in matches)}) — write the type explicitly')
                continue
            type_name, flip = matches[0]
            if flip:
                src_label, dst_label = b_label, a_label

        reg = registry["edges"][type_name]
        fields = {reg["source"]["field"]: src_label, reg["dest"]["field"]: dst_label}

        positional, named = _parse_args(args_raw, st["ln"], err)
        if positional is not None:
            err(st["ln"], "edge arguments must be named")
        for f, v in named.items():
            if f not in reg["fields"]:
                err(st["ln"], f'{type_name} has no field "{f}"')
                continue
            if f in (reg["source"]["field"], reg["dest"]["field"]):
                err(st["ln"], f'"{f}" is set by the arrow itself')
                continue
            r = _resolve(v, st["ln"], consts, nodes, True)
            if r is not _FAIL:
                fields[f] = r
        for f, info in reg["fields"].items():
            if info["required"] and f not in fields:
                err(st["ln"], f'{type_name} {src_label} -> {dst_label} is missing required field "{f}"')

        key = f"{type_name}|{src_label}|{dst_label}"
        if key in seen_edges:
            warn(st["ln"], f"duplicate edge {src_label} -> {dst_label} ({type_name})")
        seen_edges.add(key)
        graph["edges"].append({"type": type_name, "fields": fields, "inferred": explicit_type is None, "line": st["ln"]})

    return {"graph": graph, "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------
# DESUGAR — re-emit a parsed graph as source with every lens resolved.
# The output is valid DSL and re-parses to the identical graph.
# ---------------------------------------------------------------------
def _fmt_value(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_fmt_value(e) for e in v) + "]"
    if isinstance(v, dict) and "$ref" in v:
        return f'{v["$ref"]["g_id"]}.{v["$ref"]["field"]}'
    if isinstance(v, dict) and "$file" in v:
        path = v["$file"]["path"].replace("\\", "\\\\").replace('"', '\\"')
        return f'file("{path}")'
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {_fmt_value(e)}" for k, e in v.items()) + "}"
    return str(v)


def _fmt_fields(fields, skip=()):
    return ", ".join(f"{f}: {_fmt_value(v)}" for f, v in fields.items() if f not in skip)


def desugar(graph, registry=None):
    registry = registry or build_registry()
    out = [
        "# desugared — every lens resolved:",
        "#   constants substituted · labels -> names · inferred edges explicit · arrows canonical",
        "",
    ]
    for n in graph["nodes"]:
        args = _fmt_fields(n["fields"])
        out.append(f'{n["g_id"]} : {n["type"]}' + (f"({args})" if args else ""))
    if graph["edges"]:
        out.append("")
    for e in graph["edges"]:
        reg = registry["edges"][e["type"]]
        src = e["fields"][reg["source"]["field"]]
        dst = e["fields"][reg["dest"]["field"]]
        extras = _fmt_fields(e["fields"], skip=(reg["source"]["field"], reg["dest"]["field"]))
        out.append(f'{src} -> {dst} : {e["type"]}' + (f"({extras})" if extras else ""))
    return "\n".join(out)


# ---------------------------------------------------------------------
# refs_of — every attribute reference in a graph, as data-dependency
# triples: {from, to, field}. These are what the planner turns into
# BLOCKED when unresolvable.
# ---------------------------------------------------------------------
def refs_of(graph):
    refs = []

    def walk(v, add):
        if isinstance(v, dict):
            if "$ref" in v:
                add(v["$ref"])
            else:
                for e in v.values():
                    walk(e, add)
        elif isinstance(v, list):
            for e in v:
                walk(e, add)

    for n in graph["nodes"]:
        walk(n["fields"], lambda r, n=n: refs.append({"from": r["g_id"], "to": n["g_id"], "field": r["field"]}))
    for e in graph["edges"]:
        walk(e["fields"], lambda r, e=e: refs.append({"from": r["g_id"], "to": None, "field": r["field"], "edge": e}))
    return refs


# ---------------------------------------------------------------------
# LOAD — a parsed graph into a GraphIaCState: instantiate the Pydantic
# models and add them to state.G, resolving every $ref from live AWS
# state on the way. This is where the two-phase pattern becomes the
# planner's problem instead of the author's:
#
#   - a $ref resolves only if the referenced node exists live, its
#     ready() is True, and the referenced field has a value
#   - otherwise the referencing node is BLOCKED — it (and every edge
#     touching it) is left out of the graph and reported instead, so
#     plan/run simply act on what's actionable and pick the rest up on
#     a later run
# ---------------------------------------------------------------------
class BlockedItem(BaseModel):
    """A node or edge left out of this run, and why. plan() reports these
    and shields their DB rows from orphan deletion."""

    g_id: str
    type: str
    reason: str


class _Blocked(Exception):
    def __init__(self, reason):
        self.reason = reason


def _collect_refs(v, out):
    if isinstance(v, dict):
        if "$ref" in v:
            out.append(v["$ref"])
        else:
            for e in v.values():
                _collect_refs(e, out)
    elif isinstance(v, list):
        for e in v:
            _collect_refs(e, out)
    return out


def _substitute(v, resolve_ref, resolve_file):
    if isinstance(v, dict):
        if "$ref" in v:
            return resolve_ref(v["$ref"])  # may raise _Blocked
        if "$file" in v:
            return resolve_file(v["$file"])
        return {k: _substitute(e, resolve_ref, resolve_file) for k, e in v.items()}
    if isinstance(v, list):
        return [_substitute(e, resolve_ref, resolve_file) for e in v]
    return v


def load_graph(state, graph, base_dir=None):
    """Instantiate a parsed DSL graph into `state` (a GraphIaCState).

    Adds every resolvable node and edge to state.G via the normal
    add_node/add_edge path, exactly as a Python infra.py would. Returns
    the list of BlockedItem for everything that couldn't be — pass it to
    plan(state, blocked=...) / run(state, blocked=...).

    `base_dir` anchors file("…") values — pass the directory of the .giac
    source (defaults to the current working directory). A missing file
    raises FileNotFoundError: that's an authoring error, not a BLOCKED.
    """
    from pathlib import Path

    from GraphIaC.main import add_edge, add_node

    base = Path(base_dir) if base_dir else Path.cwd()
    blocked = []
    blocked_ids = set()
    models = {}
    live_cache = {}

    def resolve_file(fileref):
        target = base / fileref["path"]
        if not target.is_file():
            raise FileNotFoundError(f'file("{fileref["path"]}") not found (looked in {base})')
        return target.read_text()

    def live_ready(g_id):
        """The live state of a node, or _Blocked if it doesn't exist / isn't ready."""
        target = models.get(g_id)
        if target is None:
            raise _Blocked(f'waiting on "{g_id}" — {"blocked itself" if g_id in blocked_ids else "unknown node"}')
        if g_id not in live_cache:
            live_cache[g_id] = target.read(state.session, state.G, g_id, target.read_id)
        live = live_cache[g_id]
        if live is None:
            raise _Blocked(f'waiting on "{g_id}" — not created yet')
        if not live.ready():
            raise _Blocked(f'waiting on "{g_id}" — exists but not ready')
        return live

    def resolve_ref(ref):
        live = live_ready(ref["g_id"])
        val = getattr(live, ref["field"], None)
        if val is None:
            raise _Blocked(f'waiting on "{ref["g_id"]}.{ref["field"]}" — no value yet')
        return val

    # edges whose class declares gates_destination: the destination cannot be
    # provisioned until the source's live state is ready() — the
    # relationship-shaped twin of an attribute reference
    gated = {}  # dest g_id -> (source g_id, edge type)
    for e in graph["edges"]:
        cls = state.models_map.get(e["type"])
        if not (cls and getattr(cls, "gates_destination", False)):
            continue
        try:
            probe = cls(**{k: v for k, v in e["fields"].items() if isinstance(v, str)})
            gated[probe.destination_g_id] = (probe.source_g_id, e["type"])
        except Exception:
            pass  # malformed edge — it will fail on its own below

    # nodes: fixpoint iteration so ref/gate targets instantiate before their
    # dependents, regardless of declaration order
    pending = {n["g_id"]: n for n in graph["nodes"]}
    while pending:
        progress = False
        for g_id, n in list(pending.items()):
            refs = _collect_refs(n["fields"], [])
            gate = gated.get(g_id)
            if any(r["g_id"] in pending and r["g_id"] != g_id for r in refs):
                continue  # a ref target hasn't been decided yet — come back to this one
            if gate and gate[0] in pending and gate[0] != g_id:
                continue  # the gating source hasn't been decided yet
            del pending[g_id]
            progress = True
            try:
                if any(r["g_id"] == g_id for r in refs):
                    raise _Blocked(f'"{g_id}" references itself')
                if gate:
                    try:
                        live_ready(gate[0])
                    except _Blocked as b:
                        raise _Blocked(f"{b.reason} (required by {gate[1]})") from None
                fields = _substitute(n["fields"], resolve_ref, resolve_file)
                model = state.models_map[n["type"]](g_id=g_id, **fields)
                models[g_id] = model
                add_node(state, model)
            except _Blocked as b:
                blocked.append(BlockedItem(g_id=g_id, type=n["type"], reason=b.reason))
                blocked_ids.add(g_id)
        if not progress:  # only circular dependencies remain
            for g_id, n in pending.items():
                blocked.append(BlockedItem(g_id=g_id, type=n["type"], reason="circular dependencies"))
                blocked_ids.add(g_id)
            break

    # edges: blocked if their fields can't resolve or either endpoint is blocked
    for e in graph["edges"]:
        label = e["type"]
        try:
            fields = _substitute(e["fields"], resolve_ref, resolve_file)
            edge = state.models_map[e["type"]](**fields)
            label = f"{edge.source_g_id} → {edge.destination_g_id}"
            for endpoint in (edge.source_g_id, edge.destination_g_id):
                if endpoint in blocked_ids:
                    raise _Blocked(f'waiting on blocked node "{endpoint}"')
            add_edge(state, edge)
        except _Blocked as b:
            blocked.append(BlockedItem(g_id=label, type=e["type"], reason=b.reason))

    return blocked
