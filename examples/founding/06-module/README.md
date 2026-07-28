# Chapter 6 — The same stack, as a module

*Chapter 5 showed you every node and every arrow. This chapter names the
pattern so you never write them again.*

Compare the two files. They produce **the same graph** — same nodes, same
edge types, same guards — and there's a test in `tests/test_dsl.py` that
asserts it, so you don't have to take anyone's word for it.

```
define web-service(hz, host, cert, image, port: 8000, cpu: "512", …) {
    lb        : ALB(name: "${name}-lb")
    db        : RDSPostgres(name: "${name}-db")
    …
    lb  -> app : (health_check_path: "/healthz")
    app -> db : (role_g_id: task-role)
    ? db-private(db)
}

web : web-service(hz: hz, host: "app.yourco.com", cert: app-cert,
                  image: images.uri, cpu: "512", memory: "1024", count: 2)
```

---

## A module is a macro, not a type

Instantiating one **replays the body** with its parameters bound and its
labels prefixed. By the time the planner sees anything, no module exists —
just the same flat nodes and edges chapter 5 wrote by hand. Run `desugar`
in the editor and the expansion is right there, as ordinary DSL you could
have typed yourself.

```bash
python -m GraphIaC graphiac --infra_file webapp.giac serve
```

That property is the whole design. It's why `plan`, `run`, `verify`, the
diagram, and the state DB needed no changes to support this chapter.

---

## Why not just a library construct

CDK has `ApplicationLoadBalancedFargateService`; Pulumi has
`awsx.ecs.FargateService`. Both do this wiring, and both exist for the same
reason this module does — the pattern hurt enough times that somebody named
it. The difference is what happens next.

**A construct owns its resources.** Want two services behind one load
balancer? The construct thinks it owns the load balancer. Want a Redis
cache alongside? There's no constructor argument for that, so you drop to
the layer below and rewrite the wiring by hand.

**A module owns nothing.** `web-lb` and `web-db` are real labels,
addressable from this file. Adding something the module never anticipated
is an arrow:

```
cache : ElastiCache(name: "yourco-cache")
web-app -> cache          # the module didn't have to think of this
```

There's no cliff, because there's no altitude. An unusual stack is the same
language as a common one, just a different set of arrows.

The other half is combinatorial. Pairwise wirings grow like N², and edges
cover that space completely. Constructs have to cover *subsets*, which is
2^N — which is why CDK has a dozen L3 patterns and not a thousand, and why
the one you need is so often the one nobody wrote.

---

## The guard travels with the pattern

```
define web-service(...) {
    …
    ? db-private(db)
}
```

This is the part that makes a module different in kind from a bundle of
resources: **it's a pattern plus its guarantees.** Nobody can instantiate
`web-service` and end up with a database open to the internet, because the
invariant is part of the definition, and `verify` checks it against live
AWS for every instance.

Two instances, two guards, checked independently:

```
✓ ? db-private(web-db)      private address, tcp/5432 open to 1 security group(s)
✓ ? db-private(worker-db)   private address, tcp/5432 open to 1 security group(s)
```

That's the shape your org's S3 standard should take, too — a one-node
module with `? private(b)` attached, so "our buckets" means something
checkable instead of something in a wiki.

---

## The rules, briefly

- **Instantiation looks exactly like a node**: `web : web-service(...)`. A
  module should feel like a bigger node, because that's what it is.
- **Labels get prefixed with the instance label**, joined by `-`: body label
  `db` in instance `web` becomes `web-db`. Two instances can't collide, and
  there are no declared outputs — the labels *are* the interface.
- **`name` is the instance label** inside the body, which is how
  `"${name}-lb"` works. It's reserved as a parameter name.
- **Parameters** are `p` or `p: default`, and arguments are always named. A
  parameter can carry a node (`hz: hz`) or an attribute reference
  (`image: images.uri`) — the ref stays symbolic all the way to the field
  it lands on, so `web-app` BLOCKS on the ECR repository exactly as it did
  in chapter 5.

Not in v1: a module can't instantiate another module, can't declare
constants (use a parameter with a default), and can't be `use`d from
another file — see the note in `dsl/spec.md` about why a file import can't
work in the browser sandbox.

---

## When to reach for one

Not yet, probably. Chapter 5 is forty lines you can read, and a module you
write once and use once is worse than the forty lines — it's the same
information behind a name.

The signal is the **third copy**. When you've forked chapter 5 for three
projects and the same six edits show up every time, those edits are the
parameter list, and you'll have designed the signature from evidence
instead of from guessing.

---

**Back to:** [the founding kit](../)
