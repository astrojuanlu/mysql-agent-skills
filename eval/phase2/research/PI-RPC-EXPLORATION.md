# Pi RPC as the yield surface — exploration

Date: 2026-10-06. Question: should profe drive attempts over pi's RPC
mode (`pi --mode rpc`) instead of `--mode json` + external group kills —
what would that buy the yield problem, what would it cost, and what
happens to `profe/packages/pi-agent-json`? Status: **explored and
experimentally validated on the pinned instrument; not adopted** —
adoption is recommended to ride with the driver/orchestration
exploration (deferred), not before. Companion to the 2026-10-06 design
notes in SPEC.md (exploration 3 there points here).

Instrument: pinned pi 0.85.1 (`~/.local/profe-t3`), bare host, model
`z-ai/glm-5.3-flash` (pi default), scratch-HOME discipline as `profe
run` (pi config copied in, `packages` stripped), neutral cwd /tmp,
skills loaded via `--skill <path>` (the six of this repo), serial.

## 1. The problem it addresses

The unified lifecycle's trigger cell needs a **yield**: the attempt
must stop at the trigger decision (the first SKILL.md read) and hand
the evidence over. Today that is an external kill — SIGTERM to pi's
process group the moment `StreamAggregator` sees the read — and the
2026-10-06 flush verification showed what that costs:

- pi's session file appends **complete messages only**, and a kill
  lands faster than the flush: the yielded attempt's session file ended
  at the last thinking-only assistant message; the tool-call event (the
  trigger datum) was never written.
- profe compensated with the **stream tee** (`run` tees pi's stdout
  event stream into `stream.jsonl`, flushed line by line; `grade`
  replays the tee for killed attempts, the session file otherwise).
  That works, but it left two live replay formats in grade and a
  bundle artifact that is profe-formatted rather than pi's.
- kill classification is **reconstructed** (which of my stop conditions
  fired, exit-code taxonomy, inline markers), and the continuation
  semantic (yield, then observe/steer instead of tear down) is
  structurally impossible from outside.

## 2. What RPC mode is

`pi --mode rpc` is a long-lived child driven by strict JSONL on
stdin/stdout (`docs/rpc.md`, `rpc-commands.md`): commands (`prompt`,
`steer`, `follow_up`, `abort`, `get_state`, `set_auto_retry`,
`new_session`, …), `response` records correlated by id, and **session
events that are the same shapes as the `--mode json` stream** (json.md:
"RPC mode emits the same session-event shapes" — no session header
record, and a few RPC-only events). Facts verified for this
exploration:

- The usual resource/session flags apply (`--skill`, `--no-skills`,
  `--no-extensions`, `-nc`, `--session-dir`, `--model`, `--thinking`);
  skills loaded over RPC demonstrably worked (the trigger fired).
- Prompts go through the `prompt` **command**, not positional args
  (RPC rejects positional prompt arguments); `abort` waits for the
  session to become idle before its response arrives; closing stdin is
  the orderly shutdown (exit 0 observed).
- Auto-retry is a command (`set_auto_retry`) with the same
  `auto_retry_*` events json mode emits — set it explicitly to keep the
  json-mode instrument semantics.

## 3. What was verified empirically (the important part)

Two round trips, client = ~60 lines of Python over `subprocess` pipes
(the experiment script doubles as the feasibility proof for a profe
transport). Session files inspected line by line afterwards.

**Yielded attempt** — abort sent at the first `tool_execution_start`
whose args contain a `SKILL.md` path (the trigger-cell condition):

- The `abort` response (`success: true`) arrived ~10 ms after it was
  sent; `agent_settled` immediately after; pi exited 0 on stdin close.
- The session file then contained **everything the json-mode kill
  lost**: the assistant message (stopReason `toolUse`) with the
  thinking block and the **complete toolCall block** — `read` with
  `{"path": ".../skills/charmed-mysql-nightly-triage/SKILL.md"}`, full
  args, toolCallId, usage with pi's nested cost breakdown — plus a
  `toolResult` entry correlated by toolCallId (`"Operation aborted"`)
  and one trailing empty assistant message with stopReason `error`
  ("This operation was aborted") and **zero usage** (no API call; the
  abort turn is local — effort accounting unaffected).
- The model text survived identically (`api`/`provider`/`model` fields
  on the message payloads — grade's model extraction unchanged).

**Full attempt** (no yield; task-cell shape, prompt → settled):

- Complete transcript in the session file: user → assistant(toolCall) →
  toolResult → assistant(stop, final text with the answer). Usage
  present (including the `reasoning` field — `PiUsage` already models
  it); `get_last_assistant_text` returned the answer; clean settle,
  exit 0.

Incidental data point (not this exploration's question): the Test
Observer query misrouted to nightly-triage again — third consecutive
observation; feeds the t5 misroute thread, recorded there.

## 4. Pros

1. **Lossless yield without the tee.** pi itself finalizes the aborted
   turn (toolCall, toolResult, classification message) before `abort`
   responds. The replay surface collapses back to **the session file
   only** — grade's dual-format branch (added 2026-10-06 for the flush
   gap) deletes outright, and the bundle keeps pi's own vocabulary as
   the single evidence format.
2. **Classification becomes client-known.** The client *sent* the
   abort and knows why (trigger yield / cap / watchdog); no exit-code
   taxonomy, no marker reconstruction. The trailing
   `stopReason: error` message is attributable to our own abort — the
   sidecar's `early_exit` already carries exactly this (grade already
   trusts `early_exit` over replay classification; nothing new to
   build).
3. **A cleaner watchdog.** Today the deadline is an immediate group
   kill. Under RPC: `abort` at the deadline → grace → `killpg` only if
   pi stopped answering. The kill stays as last resort but stops being
   the designed path.
4. **Evidence parity is documented, not hoped.** json.md defines the
   event vocabulary as shared between modes; the experiments confirm
   the shapes (and `pi-agent-json` parsed the yielded session file with
   the existing session replay path — trigger detection from toolCall
   blocks worked unmodified).
5. **The continuation seam.** After an abort the process is alive and
   idle: steer/follow_up/new prompts are one command away. Yield-then-
   observe ("log the trigger, let it continue" — the thing the 2026-10-06
   notes explicitly deferred to a future in-process driver) becomes a
   protocol call instead of a process-teardown redesign. This is the
   seam the driver/orchestration exploration wants.
6. **Boundary-neutral.** Same child-of-profe relationship as json mode
   (pipes, no pty, no new syscalls); the PoC boundary recipes need no
   new mounts for it (in-container rerun is part of the parity gate
   below, not assumed).
7. **Cleaner terminal state.** Exit 0 after a protocol stop, vs profe's
   own SIGTERM (-15) today; `pi_exit_code` in the sidecar keeps its
   meaning (classification no longer needs the "our own kill" special
   cases in the taxonomy).

## 5. Cons / costs / open verification items

1. **Adoption is an instrument change.** All graded evidence so far
   (t2/t3/t4, PoC-1, the boundary parity result) was collected over
   `--mode json`. Switching the transport requires the same discipline
   as any instrument change: dual-mode parity run over the known-clean
   smoke set (PoC-1 queries — trigger decisions, records, usage) before
   the json path is deleted. Not negotiable, and it is the main reason
   adoption is not "now".
2. **Protocol edge cases to model and handle**: `response` records must
   be routed **before** event parsing (`parse_pi_event` raises on
   unmodeled records — `response` is currently unmodeled); malformed
   commands produce `parse` responses without ids; `prompt`
   dispositions (`started`/`queued`/`handled`) must be checked (a
   `handled` disposition means no run will follow — do not wait for
   `agent_settled`); startup failures surface as process exits, not
   responses.
3. **Hang behavior unverified.** Does `abort` interrupt a
   provider-stalled run (the watchdog case)? The TUI's Esc aborts
   streaming, so probably; but the last-resort `killpg` path must stay,
   and **whether the session tail survives the last resort decides
   whether the tee is fully retired or demoted to a debug artifact** —
   an adoption-time experiment (stall a run, abort it, inspect).
4. **The long-lived process is a measurement trap.** One pi serving
   many attempts through `new_session` would amortize startup but
   reuse process state, and any session-history leakage biases the
   next attempt's trigger decision. Until the driver exploration says
   otherwise: **one RPC process per attempt** (same isolation json mode
   gives today), `new_session` reserved for the driver layer's
   explicit, recorded decision.
5. **Abort attribution depends on the sidecar.** A hand-assembled
   bundle replayed without `early_exit` sees the trailing
   `stopReason: error` message and misclassifies a protocol stop as a
   pi error. Existing design already routes this through the sidecar;
   the rule just gets sharper: RPC-aborted bundles are only
   classifiable with their sidecar.
6. **Small unused surface.** `bash_execution_update` and
   `extension_error` are RPC-only events profe would model but never
   see (no RPC `bash` commands; `--no-extensions`). Cost: two trivial
   models, kept for unmodeled-event loudness.
7. **Stdout interleaving.** Responses and events share stdout — the
   client reads continuously (backpressure rule, same as json mode)
   and writes commands mid-stream. Single-threaded reading is fine
   (the experiments did it); the discipline must be written down.

## 6. The packages question

`profe/packages/pi-agent-json` (typed Pydantic models of the `--mode
json` stream and session files, pinned to pi 0.85.1 semantics) —
**retain and extend; do not create a new package from scratch.**

- The overlap is structural: json.md defines the event vocabulary as
  shared across modes, and the experiments parsed RPC-produced session
  files with the existing session-entry path unmodified. Message,
  content-block, usage and tool-result models serve both transports.
- What RPC adoption would add, all additive: an `RpcResponse` record
  model (`type: response`, `command`, `success`, `error`, `data`,
  optional `id`) and the two RPC-only events; optionally a typed
  `get_state` data model. Order of ~60–90 lines in the existing
  package, absorbing instrument debt exactly as the package's README
  prescribes.
- A from-scratch `pi-agent-rpc` package would have to re-model
  messages/content/usage (the 80% of the surface) for zero benefit —
  the event vocabulary is the shared part, not the RPC-specific part.
- The TS alternatives (`RpcClient`, SDK) are out: profe is Python, and
  the hand-rolled client over stdin/stdout is ~60 lines — the
  experiments are that client, validated.

## 7. If adopted — the shape of the change

(Not a plan; the blast radius, so the decision is informed.)

- `_pi.py` gains an RPC transport behind the same `run_attempt`
  signature: spawn (same flags, no positional query), handshake
  (`get_state` → record session file), `set_auto_retry(true)`,
  `prompt` command, event loop through the same `StreamAggregator`
  (stop conditions call `abort` instead of `_terminate_group`;
  watchdog = abort → grace → killpg), idle wait via the abort/idle
  response, stdin close. `StreamAggregator` itself is reused verbatim —
  the events are identical.
- `run`: bundle unchanged except `stream.jsonl` disappears if the
  last-resort experiment (§5.3) says the tee is redundant; sidecar
  unchanged (`early_exit` now means "we sent abort for this").
- `grade`: replay returns to session-file-only; the stream-mode branch
  from the flush-gap fix is deleted (or kept solely for the
  last-resort-killed edge, per §5.3).
- Re-validation gate (§5.1): PoC-1 dual-mode parity on the smoke set;
  then the json transport is deleted, not kept in parallel.

## 8. Conclusion

- **The yield problem RPC solves is real and verified**: lossless
  in-protocol stop, session file complete at the yield, classification
  by client action, watchdog demoted from kill-first to abort-first.
  The experiments cost ~$0.03 total and produced the evidence above.
- **It is not adopted now, on purpose.** The measurement pipeline is
  not blocked (the tee solves the flush gap); the decisive payoff —
  the continuation seam — only matters when the driver/orchestration
  layer is designed, and adoption must go through the dual-mode parity
  gate regardless. Recommendation: **decide RPC adoption together with
  the driver exploration**, as one instrument change with one
  re-validation campaign.
- **The packages answer is firm now**: retain `pi-agent-json`, extend
  it additively (response records + RPC-only events) when the transport
  lands; nothing needs rewriting, nothing needs a second package.
