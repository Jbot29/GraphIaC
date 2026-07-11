# GraphIaC, hosted by GraphIaC

The editor UI you get locally from `serve` — deployed as a Lambda with a
public URL, Cognito login, and your infra source + state in S3. Edit,
plan, run, and verify your infrastructure from any browser, with nothing
running on your machine.

There is no special server build: the payload is the **GraphIaC package
itself** (the `Api` class is transport-agnostic), miniui from
[`lambda-ui/`](../lambda-ui/) provides login, and `handler.py` is ~100
lines of user-space glue. The infrastructure is the lambda-ui pattern
you've already run.

## Steps

1. **[`team-state/`](../team-state/) first** — the hosted UI keeps state
   *and* source in that bucket. Set `state-url` in `ui.giac`.
2. **Grab + build** — pulls GraphIaC out of PyPI as Lambda-compatible
   wheels and zips it with the handler:

   ```bash
   ./build.sh                    # latest release
   ./build.sh 'GraphIaC==0.0.42' # or pin one
   ```

3. **Deploy:**

   ```bash
   python -m GraphIaC graphiac --infra_file ui.giac run
   ```

4. **Create your user** (same two commands as lambda-ui: `admin-create-user`
   with `--message-action SUPPRESS`, then `admin-set-user-password --permanent`).

Open the printed function URL, sign in — that's your infrastructure
control panel, hosted in the account it controls.

## How the pieces line up

- **The execution role is `graphiac-deploy`** — the same role your CLI
  assumes. The `graphiac-deploy -> graphiac-ui` edge attaches basic
  execution and adds Lambda to the role's trust; one deploy identity,
  laptop or cloud.
- **Runs take the same S3 lock as CLI runs.** You on your laptop and a
  teammate in the hosted UI cannot collide; the loser sees who holds the
  lock and since when.
- **Upgrading GraphIaC** = `./build.sh && ... run` (the zip freezes a
  version). Then re-run `get-started`'s setup if the new version added
  AWS actions.

## Limits (for now)

- `file("...")` values don't resolve in hosted sources — there are no
  local files next to the source in S3. Keep hosted sources
  self-contained (or manage file-using stacks from the CLI).
- One infra file per deployment (`GRAPHIAC_SOURCE`, default `infra.giac`).
