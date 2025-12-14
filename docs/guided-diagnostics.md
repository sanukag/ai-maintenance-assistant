# Guided maintenance diagnostics

## Purpose

Guided diagnostics turns the assistant from a passive question-answering tool
into a stateful fault-investigation partner. It gathers observations, maintains
competing hypotheses, answers interruptions and follow-up questions, and chooses
one useful next question or evidence-backed check.

The feature supports a maintenance engineer's reasoning process; it does not
claim to be a qualified engineer or replace site procedures, competence rules
or responsibility for the equipment.

## Investigation loop

Each turn performs the following sequence:

1. Load the durable case state and the most recent eight turns.
2. Combine the worker's new message with the case summary, symptoms and leading
   hypotheses to form a bounded retrieval query.
3. Apply the session's manual, brand, machine, site and document-type filters.
4. Retrieve and deduplicate up to six parent evidence sections.
5. Ask the fixed OpenAI model for a typed diagnostic plan.
6. Validate its action, safety state, source IDs and inline citation markers.
7. Preserve established worker facts even if the model omits them from its
   proposed state.
8. Atomically append the worker and assistant turns and update the case state.

Initial case creation is also atomic: a provider failure cannot leave a
user-only diagnostic record.

## Structured state and actions

SQLite schema version 14 adds `diagnostic_sessions` and `diagnostic_turns`. A
session retains:

- equipment and manual scope;
- active, resolved or escalated status;
- explicit equipment-safety status;
- symptoms and worker observations;
- named measurements and units;
- checks already completed;
- low, medium or high ranked hypotheses;
- supporting sources and contrary observations; and
- a bounded current summary.

The model must choose one typed action: ask a question, request an observation,
request a measurement, suggest a check, answer a follow-up, report a diagnosis,
escalate or mark the fault resolved. Hypotheses remain labelled possibilities;
likelihood is deliberately categorical rather than a misleading numerical
probability.

## Evidence and safety controls

Manual-derived statements use the same `[S1]` citation convention as grounded
answers. The API rejects unknown, duplicated or mismatched citations. Suggested
checks, requested measurements and reported diagnoses must cite retrieved
evidence. Citation snapshots are stored with the turn so the investigation
remains auditable if a manual is later superseded or deleted.

The worker explicitly selects one safety state:

- `unknown` — equipment state has not been confirmed;
- `non_intrusive_only` — observation without access, isolation or contact only;
- `confirmed_safe` — the worker confirms the equipment is isolated and safe for
  checks they are authorised and competent to perform; or
- `stop` — stop the investigation and escalate.

The API rejects an intrusive model action unless safety is `confirmed_safe`.
`stop` requires an escalation action, and action/status combinations are
validated. These controls cannot determine whether a real workplace is safe;
workers must follow approved isolation, permit and escalation procedures.

## Conversation and privacy boundary

The application sends the structured case state, latest message, at most eight
recent turns and selected manual evidence to OpenAI. It does not send every old
turn indefinitely or treat an earlier model answer as approved evidence.
Responses use a Pydantic schema and `store: false`; the local SQLite session is
the authoritative conversation record. This follows OpenAI's documented
[structured-output](https://developers.openai.com/api/docs/guides/structured-outputs)
and [manually managed conversation-state](https://developers.openai.com/api/docs/guides/conversation-state)
patterns while retaining local lifecycle control.

## Worker interface

Open **Diagnose a fault** from the main navigation. A worker can:

- describe a symptom and select equipment metadata or one manual;
- explicitly record the current safety boundary;
- answer the assistant's focused questions or ask why a check matters;
- enter observations and measurements in ordinary language;
- inspect the live summary, hypotheses, contrary evidence and completed checks;
- expand cited manual evidence; and
- reopen an earlier investigation after an application restart.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/diagnostic-sessions` | Start and plan the first diagnostic turn |
| `POST` | `/diagnostic-sessions/{id}/turns` | Apply a new observation, reading or question |
| `GET` | `/diagnostic-sessions` | List recent investigations |
| `GET` | `/diagnostic-sessions/{id}` | Reopen complete case state and turn history |
| `DELETE` | `/diagnostic-sessions/{id}` | Permanently delete one investigation |

The feature returns `diagnostics_disabled` until embeddings and the diagnostic
model are available. Model failures use `diagnostic_planning_failed`; partial
turns are not stored.

## Current limitations

- Observations and readings are supplied by the worker; the application does
  not yet connect directly to sensors, a CMMS or machine controller.
- A ranked hypothesis is not proof of the root cause.
- Diagnostic model quality still needs calibration against a reviewed set of
  representative equipment faults before production use.
- The API is local and unauthenticated, so investigations share the workstation
  data boundary and should not contain unnecessary personal information.
