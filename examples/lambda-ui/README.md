# lambda-ui

A serverless web app with login, in one Lambda: a public function URL,
Cognito auth, static assets baked into the zip, and authenticated JSON
APIs. The base for internal tools — a feature-flag console, an admin
panel, a CRM helper. Edit `lambda/app.py`, rebuild, run.

## Steps

1. **[`get-started/`](../get-started/)** and **[`team-state/`](../team-state/)**
   if you haven't (deploy role; optional shared state).
2. `./build.sh`
3. `python -m GraphIaC graphiac --infra_file ui.giac run`
4. Create your user (the `.giac` header has the two `admin-*` commands).

Open the printed function URL, sign in, and `/api/now` + `/api/echo`
respond as you. Add a function to `APIS` in `app.py`, rebuild, run — a
new authenticated endpoint at `POST /api/<name>`.

## The security model (`lambda/miniui.py`)

This is a base to fork, so be clear-eyed about what it does:

- **Sessions** — the Cognito access token rides in an HttpOnly, Secure,
  `__Host-`, `SameSite=Strict` cookie, validated against Cognito
  (`get_user`) on every request. No local crypto.
- **CSRF** — state-changing APIs are POST-only; `SameSite=Strict` means a
  cross-site page can't ride the cookie. No tokens needed.
- **Headers** — every response carries CSP, `nosniff`, `X-Frame-Options:
  DENY`, HSTS, and `no-store`. The CSP allows inline scripts/styles
  because the bundled pages use them; tighten `script-src` if yours don't.
- **Logout** revokes the token at Cognito, not just the cookie.
- **Authorization is all-or-nothing** — *any* signed-in user can call
  *every* API. Per-user or per-action authorization is the job of the
  functions in `APIS`; they receive `user` (the email) for exactly that.
- **Brute force** relies on Cognito's own throttling for
  `USER_PASSWORD_AUTH` — turn on the pool's advanced security features.

## First-login note

A user created with a *temporary* password is in a `NEW_PASSWORD_REQUIRED`
challenge state; miniui's simple login doesn't drive challenges, so it
reports "wrong email or password". Set a **permanent** password
(`admin-set-user-password --permanent`) and login works — the `.giac`
header walks through it.
