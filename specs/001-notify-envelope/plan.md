# Plan — 001 notify envelope

## Load-bearing decisions

- **Wire format, not a shared library.** Producers are Python, Node, Go
  templates and the OpenClaw runtime; only a JSON contract can span them.
- **The renderer is a separate module** (`compose/notify-relay/render.js`), not
  inline in `server.js` — it is the piece with rules worth testing, and
  `server.js` is I/O. `server.js` keeps its "no npm dependencies" property; the
  tests run on Node's built-in runner (`node --test`), so the service image
  stays dependency-free.
- **Clip before escaping, measure after.** Truncating already-escaped HTML can
  cut `&amp;` or a tag in half and Telegram then rejects the whole message. Every
  cap is expressed as a *rendered* length and enforced by shrinking the raw
  string until its escaped form fits.
- **Strict validation, `400` on violation.** Producers here are our own code; a
  loud `400` naming the field beats a silently mangled 3am alert. Non-`http(s)`
  link URLs are a validation failure, not a silent drop.
- **Legacy `/text` and `/devclaw` keep working** while producers migrate. They
  are live on the VPS; retiring them in the same change would break devclaw
  between deploys. They stay plain-text (no `parse_mode`) until US2/US3 move
  them — sending their unescaped strings as HTML would break rendering.
- **The endpoint is tested over real HTTP, not through a mocked handler.**
  `server.js` exports its listener and honours `NOTIFY_RELAY_PORT=0`, so the
  test binds an ephemeral port and makes actual requests; startup stays a single
  unconditional code path. An `import.meta`-style "only listen when I am the
  entrypoint" guard was rejected: CI builds no images, so a guard that silently
  stopped matching would ship a relay that never binds.
- **The Node suite asserts the Dockerfile copies every runtime module.** CI
  builds no images (the runner is the production VPS), so a module added beside
  `server.js` and left out of the `COPY` crashes the container at deploy with
  nothing catching it first. The check costs one test and closes the gap the
  moment the service stopped being a single file.
- **New verify layer is declared in `devclaw.json`**, not in `ci.yml`. The
  repo's CI test job runs the memory-audit pytest suite only; the relay's Node
  suite is a second layer and `verifyCmd` names both.

## Story slices — the surfaces each one touches

- **US1 (this slice)** — `compose/notify-relay/render.js` (new: validation +
  rendering), `render.test.js` and `server.test.js` (new: Node test suites),
  `server.js` (add `POST /notify`, thread `parse_mode` through `sendTelegram`,
  export the listener so the endpoint is testable), `Dockerfile`
  (copy the new module into the image), `devclaw.json` (new: declare the Node
  test layer), `docs/notify-envelope.md` (new: the producer-facing contract),
  `README.md` (service-table row). Constraint: the image has no npm deps and
  must keep none — use `node:test` and `node:assert`, nothing else.
- **US2 (done)** — `compose/notify-relay/task-row.js` (new: the mapping) +
  `task-row.test.js`, `server.js` (`/devclaw` renders and sends HTML;
  `formatMessage` deleted), `server.test.js`, `Dockerfile`,
  `docs/notify-envelope.md`. The mapping went to its own module rather than
  staying in `server.js` as first planned: it is pure logic with real edge cases
  and `server.js` is I/O — the same split `render.js` already has. Constraints
  met: `status` `done`/`failed`/other → `good`/`act`/`info` via a
  `hasOwnProperty` lookup (`status` is producer-controlled, so a bare index
  would resolve `constructor` off the prototype); `result_json` is read as a
  JSON string *or* an already-parsed object. Message shape: goal in the
  headline, the error's first line in `body`, the rest of the traceback in the
  collapsed `detail` — replacing the legacy 600-char inline paste.
- **US3** — the devclaw repo (`goal_notify.py` and the task-callback poster),
  not this one. Retire `/text` here only after devclaw's side has deployed.
- **US4** — `compose/observability/grafana/provisioning/alerting/contact-points.yml.tmpl`
  only. Constraint: it is a template rendered by `scripts/deploy.sh`
  (`__TELEGRAM_CHAT_ID__`); Grafana's Go template escaping is not the same as
  the relay's, and `disableResolveMessage: false` must stay.
- **US5** — `defaults/` (the OpenClaw agent workspace contract) and the
  `channels.telegram.accounts.devclaw` wiring.

## Rendered-length caps (US1)

Line 1 and `action` are bounded so the message always fits Telegram's 4096
(`MAX_MSG_CHARS` = 3500, the existing headroom constant): source 48, subject 96,
headline 200, action 256, at most 3 links of 96 (text) + 300 (url). `body` is
capped at 1200. Those caps sum to ~3100, so `detail` always has a positive
budget (what remains after everything else) and is simply clipped to it — no
field-shrinking loop, and no "detail did not fit, drop it" branch, is needed.
