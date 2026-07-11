# Team state

Keep your infrastructure's state (`.db`) in S3: same state from every
machine and every teammate, with locked runs. No DynamoDB — the lock is a
plain lockfile made atomic by S3 conditional writes (the same protocol
Terraform 1.10+ uses).

The workflow:

1. **[`get-started/`](../get-started/)** first — deploy role + profile.
2. **Create the state bucket** (once; bucket names are globally unique, so
   change the label in `state-bucket.giac` first):

   ```bash
   python -m GraphIaC graphiac --infra_file state-bucket.giac run
   ```

3. **Run everything with `--state`:**

   ```bash
   python -m GraphIaC graphiac --infra_file site.giac \
       --state s3://my-graphiac-state/prod run
   ```

## Migrating existing local state

Started local (most people should — permissions come first)? Moving up is
a one-time copy. The key must be `<prefix>/<infra-file-name>.db` — for
`site.giac` under `--state s3://my-graphiac-state/prod`:

```bash
aws s3 cp site.db s3://my-graphiac-state/prod/site.db --profile graphiac
```

Then add `--state s3://my-graphiac-state/prod` to your commands and delete
the local `site.db`. Sanity check: the first `plan` after migrating should
look exactly like your last local one (usually: empty).

## Locking

- `run` takes the lock. A second runner is told **who** holds it, from
  which host, doing what, since when — and stops.
- `plan` and `verify` don't lock; they read the latest state and tolerate
  the tiny staleness window.
- A crashed run's lock is **never stolen automatically**. Release it
  deliberately:

  ```bash
  python -m GraphIaC graphiac --infra_file site.giac \
      --state s3://my-graphiac-state/prod unlock
  ```

The bucket is versioned (see the `.giac`) — every state change is
recoverable, which pairs with the lock's compare-and-swap: even a lost
race loses nothing.
