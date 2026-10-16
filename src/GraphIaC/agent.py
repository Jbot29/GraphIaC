"""The agent: a Claude call that edits `.giac` source on request.

The editor's agent drawer sends the open file plus a request ("add a
Postgres database behind web", "why is cf blocked?", "what should stay
true here?"). This module builds the prompt, calls the model, and checks
the answer with the real parser before anyone sees it.

Why this is safe to put in front of infrastructure, when "AI writes your
Terraform" is not: a `.giac` file has no IAM syntax. The model can name
nodes and draw arrows; the parser rejects anything outside the registry;
the edges write the policies. The language constrains what the model can
express, the parser enforces it, and the human still presses run.

Division of labour (the first blog post, made literal):
  - LLM handles expression        -> this module
  - DSL constrains representation -> the parser check + one automatic retry
  - interpreter handles truth     -> plan/run/verify, never called from here
  - human inspects the bridge     -> the UI shows a diff; Accept edits the
                                     editor only. The agent never saves,
                                     plans, or runs.

Prompt layout (stable first, so the whole language caches; volatile last):
  system  = grammar sheet + type catalog rendered from the registry
            + two worked examples                       [cache_control]
  user    = other files in the workspace, the open file, the last
            plan/verify output if any, the conversation so far, the request

The SDK is optional (`pip install "GraphIaC[agent]"`). Credentials resolve
the way the Anthropic SDK does — ANTHROPIC_API_KEY in the environment is
the usual case. `available()` says whether the drawer should exist.
"""

import json
import os

from GraphIaC import dsl
from GraphIaC.dsl_registry import build_registry
from GraphIaC.logs import setup_logger

logger = setup_logger()

MODEL = os.environ.get("GRAPHIAC_AGENT_MODEL", "claude-opus-5-5")
MAX_TOKENS = 8000  # a .giac file is forty lines; this is room for ten of them

# What the model returns. `source` is the whole file (files are small, and a
# whole file is checkable; a patch is not) or null for a question answered
# in prose. `reply` is for the human, one short paragraph.
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {
            "type": "string",
            "description": "For the person: what changed and why, or the answer to their question. Plain prose, a few sentences.",
        },
        "source": {
            "type": ["string", "null"],
            "description": "The complete updated .giac file when the request changes the infrastructure; null when it does not.",
        },
    },
    "required": ["reply", "source"],
    "additionalProperties": False,
}

GRAMMAR = """\
You are the agent inside the GraphIaC editor. You edit `.giac` files: a small
language where nodes are AWS resources and arrows are the relationships
between them. The arrow is the point — `web -> db` provisions the security
group rule, the IAM grant, and the secret injection. You never write IAM
policies, security group rules, or ARNs; the edges own all of that.

# The language

    # comments run to end of line
    name = "value"                         # constant, substituted at parse time
    domain = "${company}.com"              # "${const}" interpolates a CONSTANT only
                                           # (never a label, never a ref)
    label : TypeName(field: value, ...)    # a node; the label IS its identity
    label : TypeName                       # no args
    label : TypeName("name")               # one positional arg = the name field
    a -> b                                 # an edge; the type is inferred from the pair
    a -> b : (field: value)                # inferred type, with args
    a -> b : EdgeType(field: value)        # explicit type
    other.field                            # attribute reference: a data dependency —
                                           # the node is BLOCKED until it resolves
    file("path.js")                        # a code file next to the source
    ? predicate(label, ...)                # a guard: an invariant checked by verify
    define pattern(p, q: default) { ... }  # a module (macro) over the graph
    inst : pattern(p: x, q: y)             # instantiating one, written like a node

Values: "strings", numbers, true/false, [lists], {maps: "like this"}.
Labels: letters, digits, `_`, `-`. A bare label as a value means that node.

Rules that matter:
- Most node types have a name field (listed below). When omitted it defaults
  to the label, so `signups : DynamoTable(...)` names the table "signups".
- Arrow direction does not matter for inference; one edge exists per pair of
  node types. If a pair is not in the table below, there is no such arrow.
- Edges that need extra participants take them as args
  (`fn -> table : (role_g_id: role)`). Check each edge's fields below.
- Attribute references (`cert.arn`, `db.endpoint`, `images.uri`) are the only
  way to pass live values. There is no waiting logic: the planner BLOCKS the
  node until the upstream exists and is ready, and picks it up on a later run.
- There is no VPC, security group, listener, target group, bucket policy or
  IAM policy node. If the user asks for one, explain which arrow provides it.
- Guards check live AWS with independent code. Add them for the properties the
  person would be scared to get wrong; put them at the end of the file.
- Keep comments: a `.giac` file is meant to be read. Match the file's existing
  style. Do not rename things the user did not ask to rename.

# How to answer

Return JSON with `reply` and `source`.
- When the request changes the infrastructure, `source` is the COMPLETE new
  file and `reply` says what you added and which arrows carry the wiring.
- When it is a question (why is this blocked, what does this arrow do, is this
  safe), `source` is null and `reply` answers it.
- If the request needs a resource or relationship the language does not have,
  say so in `reply` and leave `source` null. Never invent a type or an edge.
- You cannot run anything. Plan, run and verify are the person's buttons; if
  the answer requires live state you do not have, say which button to press.
"""

EXAMPLES = """\
# Worked examples

A static site — Route53 -> CloudFront (HTTPS) -> S3:

    domain = "yourco.com"

    hz     : HostedZone(domain_name: domain)
    cert   : ACMCertificate(domain_name: domain)
    bucket : S3Bucket("yourco-com-site")
    cf     : CloudFrontDistribution(domain_name: domain)

    cert -> hz                          # DNS validation records
    cert -> cf                          # viewer certificate; cf BLOCKED until ISSUED
    cf   -> bucket                      # OAC bucket policy: only this distribution reads
    cf   -> hz : (domain_name: domain)  # the A alias record

    ? private(bucket)
    ? https-only(cf)
    ? locked-to(bucket, cf)

A web server — ALB -> ECS Fargate -> RDS Postgres:

    company  = "yourco"
    domain   = "${company}.com"
    app-host = "app.${domain}"
    region   = "us-east-2"

    hz        : HostedZone(domain_name: domain)
    cert      : ACMCertificate(domain_name: app-host, region: region)
    images    : EcrRepository(name: "${company}-web", region: region)
    lb        : ALB(name: "${company}-lb", region: region)
    db        : RDSPostgres(name: "${company}-db", region: region, db_name: company)
    cluster   : EcsCluster(name: company, region: region)
    task-role : EcsTaskRole(name: "${company}-task-role")
    web       : EcsService(name: "${company}-web", region: region,
                           image: images.uri, container_port: 8000,
                           env: {DATABASE_HOST: db.endpoint, PORT: "8000"})

    cert      -> hz
    cert      -> lb                              # HTTPS listener; lb BLOCKED until ISSUED
    lb        -> hz : (domain_name: app-host)
    cluster   -> web
    task-role -> web                             # pull images, write logs
    lb        -> web : (health_check_path: "/healthz")
    web       -> db : (role_g_id: task-role)     # SG rule + secret, both sides

    ? db-private(db)
"""


def _fmt_default(v):
    return json.dumps(v) if v is not None else "null"


def catalog(registry):
    """Every type the parser accepts, as a reference sheet for the model.
    Rendered from the same registry the parsers use, so the model can
    never see a type the parser would reject."""
    out = [
        "# Node types",
        "",
        "Fields shown `=null` are read back from AWS after creation (ARNs, ids,",
        "status, DNS names) — never set them. Fields with a real default are tunable.",
        "",
    ]
    for name, info in sorted(registry["nodes"].items()):
        head = name
        if info.get("isa"):
            head += f"  (isa {info['isa']}: takes its parent's arrows and guards)"
        out.append(head)
        if info.get("nameField"):
            out.append(f"    name field: {info['nameField']} (defaults to the label)")
        req = [f for f, fi in info["fields"].items() if fi["required"] and f != info.get("nameField")]
        opt = [f"{f}={_fmt_default(fi.get('default'))}" for f, fi in info["fields"].items() if not fi["required"]]
        if req:
            out.append("    required: " + ", ".join(req))
        if opt:
            out.append("    optional: " + ", ".join(opt))
        out.append("")

    out += ["# Edges (one per pair of node types; direction shown is canonical)", ""]
    for name, info in sorted(registry["edges"].items()):
        out.append(f"{info['source']['type']} -> {info['dest']['type']}    {name}")
        extra = {
            f: fi
            for f, fi in info["fields"].items()
            if f not in (info["source"]["field"], info["dest"]["field"])
        }
        req = [f for f, fi in extra.items() if fi["required"]]
        opt = [f"{f}={_fmt_default(fi.get('default'))}" for f, fi in extra.items() if not fi["required"]]
        if req:
            out.append("    required args: " + ", ".join(req))
        if opt:
            out.append("    optional args: " + ", ".join(opt))
    out.append("")

    out += ["# Guards (? predicate(targets))", ""]
    for name, info in sorted(registry["predicates"].items()):
        out.append(f"? {name}({', '.join(info['args'])})    {info['doc']}")
    out.append("")
    return "\n".join(out)


def system_prompt(registry=None):
    registry = registry or build_registry()
    return GRAMMAR + "\n" + catalog(registry) + "\n" + EXAMPLES


def user_prompt(source, request, file=None, files=(), context=None, history=()):
    """The volatile half: everything specific to this request."""
    parts = []
    others = [f for f in files if f != file]
    if others:
        parts.append(
            "Other files in this workspace (you are editing only the open one; "
            "resources declared in these already exist, do not redeclare them):\n"
            + "\n".join(f"  {f}" for f in others)
        )
    parts.append(f"The open file ({file or 'untitled.giac'}):\n```\n{source}\n```")
    if context:
        parts.append(f"The last plan/run/verify output:\n```\n{context}\n```")
    for turn in history:
        who = "Person" if turn.get("role") == "user" else "You"
        parts.append(f"{who} (earlier): {turn.get('text', '')}")
    parts.append(f"Request: {request}")
    return "\n\n".join(parts)


def available():
    """Should the editor show the drawer? True when the SDK is installed and
    credentials are present the way the SDK itself looks for them."""
    try:
        import anthropic
    except ImportError:
        return False
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    try:
        client = anthropic.Anthropic()
        return bool(client.api_key or client.auth_token)
    except Exception:  # noqa: BLE001 — any construction failure means "not configured"
        return False


class Agent:
    """One agent per server. `client` is injectable so tests never touch the
    network; `parse` is the real parser unless a test says otherwise."""

    def __init__(self, client=None, model=MODEL, registry=None, parse=dsl.parse):
        self._client = client
        self.model = model
        self.parse = parse
        self.system = system_prompt(registry)

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def _call(self, messages):
        # Thinking stays on (the model's default); effort medium — these are
        # small edits to small files. The system prompt is the whole language
        # and identical on every call, so it is the cache breakpoint.
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        )
        if response.stop_reason == "refusal":
            return {"reply": "The model declined this request.", "source": None}
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.error(f"agent returned non-JSON: {text[:200]}")
            return {"reply": text or "(empty response)", "source": None}

    def ask(self, source, request, file=None, files=(), context=None, history=()):
        """-> {reply, source|None, errors: [...], attempts}. `source` is only
        non-None when it parses — except after the retry also fails, in
        which case it is returned with `errors` so the person can see what
        the model tried and decide."""
        messages = [{"role": "user", "content": user_prompt(source, request, file, files, context, history)}]
        result = self._call(messages)
        attempts = 1
        errors = self._check(result)
        if errors:
            # The parser is the authority: hand the errors back once, verbatim.
            messages.append({"role": "assistant", "content": json.dumps(result)})
            messages.append(
                {
                    "role": "user",
                    "content": "That file does not parse. Fix exactly these errors and return the complete file again:\n"
                    + "\n".join(f"  line {e['line']}: {e['msg']}" for e in errors),
                }
            )
            result = self._call(messages)
            attempts = 2
            errors = self._check(result)
        return {
            "reply": result.get("reply", ""),
            "source": result.get("source"),
            "errors": errors,
            "attempts": attempts,
        }

    def _check(self, result):
        src = result.get("source")
        if not isinstance(src, str):
            result["source"] = None
            return []
        return self.parse(src)["errors"]
