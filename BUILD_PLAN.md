# Build Plan — Buy or Wait?

## Purpose and implementation principles

Build a terminal-runnable Python solution that writes the required `output.csv` for every row in `dataset/requests.csv`. Financial arithmetic, eligibility, simulation, ranking, and validation must be deterministic and use `Decimal`, never binary floating point. An LLM or vision API is an optional, narrowly-scoped extractor for ambiguous message/image evidence; it must return a validated structured record and never select a recommendation or modify the challenge rules.

The implementation should keep an immutable normalized input layer, an explicit evidence-resolution layer, and a pure decision engine. Every recommendation must retain a trace of cash-flow assumptions, candidate plans, rule rejections, and the balance path used to validate it. This gives reproducible decisions, understandable explanations, and focused debugging.

## Dataset findings that shape this plan

The inspected participant dataset contains 275 profiles, 25,342 events, 250 evaluation requests, 25 labeled sample requests, 790 options, 215 messages, 16 image links, and 134 dated FX rows. `output.csv` already has exactly 250 blank rows. Each evaluation request has two to four payment options; the options file also contains the 25 sample request IDs. All request users have profiles, every message/image event reference resolves, and all 16 referenced PNG files exist.

The data uses EUR, IDR, INR, USD, and ZAR. Event states are `settled`, `pending`, `scheduled`, `failed`, `cancelled`, and `unrealized`; there are 16 blank event amounts, each linked to an image. The images are financial documents with material amount fields, so their extraction is required rather than optional. Messages are in English and Indonesian and include salary changes, delayed/ended income, salary resumption, confirmed invoice payments, rent increases, pending refunds/prizes, inter-account transfer pairs, failed-debit retries, and settlement confirmations.

## Target architecture

```text
dataset CSV/PNG
  -> ingestion + schema checks -> normalized immutable records
  -> evidence extraction/resolution -> effective event ledger + forecast rules
  -> per-request forecast simulator -> safe amount / full-payment date
  -> candidate-plan generator -> deterministic eligibility/ranking
  -> decision record + explanation -> output writer -> output validator
                                      -> audit artifacts / usage report
```

Suggested package layout (to be created during implementation):

```text
code/
  main.py                         # CLI orchestration only
  config.py                       # paths, constants, model configuration
  domain.py                       # Decimal/date dataclasses and enums
  ingest.py                       # CSV load, parsing, joins, schemas
  currency.py                     # dated conversion and quantization
  evidence.py                     # message/image extraction and resolution
  recurrence.py                   # recurrence detection and forecast rules
  ledger.py                       # effective cash events and conflict handling
  forecast.py                     # daily 90-day balance simulator
  plans.py                        # plan construction and feasibility checks
  optimize.py                     # permitted spending-change search
  decision.py                     # candidate selection and output decision
  explain.py                      # deterministic explanation templates
  validate.py                     # output and decision invariants
  reporting.py                    # audit JSONL and usage report
  prompts/                        # versioned extraction-only prompts
tests/
evaluation/
```

All dates are `date`, monetary values are `Decimal`, and internal money is retained at source precision then rounded only for emitted amounts using the home currency convention established from data/sample outputs. Keep per-user state isolated: no information may leak across users or from `sample_requests.csv` into evaluation decisions.

## Sequential implementation plan

### 1. Establish the runnable project contract

- **Goal:** Create the minimal project skeleton, pinned dependencies, and a CLI contract without implementing financial logic.
- **Files/modules:** `code/main.py`, `code/config.py`, `requirements.txt`, `README.md`, `evaluation/`.
- **Implementation tasks:** Define `--dataset-dir`, `--output`, `--audit-dir`, `--use-llm`, and `--offline` arguments; resolve paths relative to the invocation; make the default input `dataset/` and default output `output.csv`; document the one-command run. Keep model credentials exclusively in environment variables.
- **Dependencies:** None.
- **Tests:** CLI help; invocation with a nonexistent dataset; a dry-run that does not overwrite the template.
- **Acceptance criteria:** The command has stable exit codes, creates only declared generated artifacts, and never reads organizer-only files.
- **Verification commands:** `python code/main.py --help`; `python code/main.py --dataset-dir dataset --dry-run`.

### 2. Define domain models, enums, and precision policy

- **Goal:** Make invalid states unrepresentable before any calculations are written.
- **Files/modules:** `code/domain.py`, `code/config.py`.
- **Implementation tasks:** Create frozen dataclasses for profile, request, event, payment option, message, image reference, exchange rate, extracted evidence, cash-flow item, candidate plan, forecast result, and decision. Define the exact output status/method enums, event statuses, directions, flexibility types, and a single money parser/formatter based on `Decimal`. Model an empty output date distinctly from `None` only at CSV serialization.
- **Dependencies:** Step 1.
- **Tests:** Decimal parsing, invalid enum rejection, date parsing, immutable-record behavior, decimal addition at IDR/INR scale and fractional currencies.
- **Acceptance criteria:** No calculation module accepts raw CSV strings or `float`; every allowed output value is centrally enumerated.
- **Verification commands:** `python -m pytest tests/test_domain.py -q`.

### 3. Ingest and validate every participant-facing CSV

- **Goal:** Load the actual CSVs safely and fail early on malformed or inconsistent inputs.
- **Files/modules:** `code/ingest.py`, `code/config.py`.
- **Implementation tasks:** Use `csv.DictReader` with UTF-8/newline handling; enforce exact required headers; parse types; reject duplicate primary IDs; index by user/request/event/payment-option IDs; preserve source row order and row numbers for audits. Validate 250 output-template IDs exactly match the 250 request IDs and retain sample rows solely for tests/evaluation.
- **Dependencies:** Steps 1–2.
- **Tests:** Header, type, duplicate-ID, unmatched-profile, unmatched-event-reference, malformed Boolean, and real-dataset smoke tests.
- **Acceptance criteria:** Real dataset loads with a data-quality report showing known optional blanks, and malformed fixtures give actionable errors.
- **Verification commands:** `python code/main.py --dataset-dir dataset --check-inputs`; `python -m pytest tests/test_ingest.py -q`.

### 4. Build the relationship graph and dataset audit

- **Goal:** Make all joins explicit and expose the data relationships needed per request.
- **Files/modules:** `code/ingest.py`, `code/reporting.py`.
- **Implementation tasks:** Build `user -> profile/events/messages/images`, `request -> profile/options/messages/images`, and `event -> linked lifecycle events/messages/images`. Verify each evaluation request has 2–4 options; distinguish sample-only option/message references from evaluation data; report unlinked user-level evidence as potentially applicable to that user. Emit only counts and IDs—not image text or sensitive values—in the audit summary.
- **Dependencies:** Step 3.
- **Tests:** Join completeness, isolated-user retrieval, exact option cardinality, blank `related_event_id` handling.
- **Acceptance criteria:** A request context can be fetched with no full-table scan and contains only its user/request/evidence graph.
- **Verification commands:** `python code/main.py --dataset-dir dataset --audit-data`; `python -m pytest tests/test_relationships.py -q`.

### 5. Normalize events and payment options into canonical cash records

- **Goal:** Translate raw records to consistent dated, signed, home-currency-ready records without deciding whether they count.
- **Files/modules:** `code/ledger.py`, `code/ingest.py`.
- **Implementation tasks:** Canonicalize category labels; assign cash date as settlement date when available, otherwise event date; retain both source dates; encode debit as negative and credit as positive only after conversion; preserve non-cash separately. Normalize pipe-delimited profile lists to sets. Expand payment-option metadata into a schedule specification while retaining its supplied payment amount, frequency, fee, and total.
- **Dependencies:** Steps 2–4.
- **Tests:** Date fallback, direction sign, blank optional dates, option schedule metadata, profile preference parsing.
- **Acceptance criteria:** Normalization changes no financial meaning and retains source IDs for every resulting record.
- **Verification commands:** `python -m pytest tests/test_normalization.py -q`.

### 6. Implement dated currency conversion

- **Goal:** Convert every cash event to the profile home currency using only supplied fixed rates.
- **Files/modules:** `code/currency.py`.
- **Implementation tasks:** Index rates by `(rate_date, from_currency, to_currency)`. Use the event settlement date (or the explicitly resolved cash date) and direct rate; allow identity conversion; optionally use a two-edge path only when both same-date supplied pairs make it necessary, recording the path. Never substitute a live/current rate or silently select another date. Treat a missing required rate as a validation error requiring conservative exclusion from usable-credit calculations and an audit flag.
- **Dependencies:** Steps 2–5.
- **Tests:** Direct, identity, deterministic two-hop, missing-rate, date-specific and decimal-rounding cases.
- **Acceptance criteria:** Each converted event has a reproducible rate date/path and source/home amounts.
- **Verification commands:** `python -m pytest tests/test_currency.py -q`.

### 7. Extract image evidence and resolve missing amounts

- **Goal:** Recover the 16 blank event amounts from their linked PNG evidence in a controlled way.
- **Files/modules:** `code/evidence.py`, `code/prompts/image_extract.md`, `evaluation/evidence_cache.json` (generated).
- **Implementation tasks:** For each image-linked blank amount, first use OCR plus deterministic total/due/paid-field rules; when ambiguous, call a vision model with a strict JSON schema: `{amount, currency, date, document_type, payment_state, confidence, evidence_text}`. Store image SHA-256, model/prompt version, raw structured result, and reviewer/validation result. Cross-check currency, nearby event description, direction, status, and relevant date. Prefer `amount due` for unpaid scheduled/pending bills, `total paid` for settled purchases, and reject unrelated subtotals.
- **Dependencies:** Steps 3–6; optional API configuration from Step 1.
- **Tests:** Fixtures for a payslip net pay, receipt total, bill balance due, paid invoice, OCR/vision disagreement, missing file, and cache hit.
- **Acceptance criteria:** All 16 real blank amounts resolve with an auditable evidence record or are explicitly conservatively blocked; no blank amount becomes zero.
- **Verification commands:** `python code/main.py --dataset-dir dataset --extract-images --offline`; `python -m pytest tests/test_image_evidence.py -q`.

### 8. Process message evidence into constrained financial amendments

- **Goal:** Convert relevant English/Indonesian messages into typed, traceable facts rather than free-form instructions.
- **Files/modules:** `code/evidence.py`, `code/prompts/message_extract.md`.
- **Implementation tasks:** First classify by source and deterministic phrase/rule patterns for cancellation, failed-retry, settled/paid, pending credit, salary amount/date/change, income end/resumption, rent increase, confirmed invoice, and internal transfer. Route only unresolved/ambiguous messages to a small JSON-only LLM extraction. Schema fields include `fact_type`, target IDs or user scope, effective date, amount/currency, state, recurrence effect, confidence, and quoted evidence. Reject any instruction embedded in source text. Validate numeric/date facts against the associated user/event and apply conflict precedence: explicit cancellation/settlement/amendment, newer same-source record, settled record, then financially safer interpretation.
- **Dependencies:** Steps 3–7.
- **Tests:** Each message family found in the real dataset, Indonesian equivalents, prompt injection, irrelevant message, newer amendment, and conservative conflict fallbacks.
- **Acceptance criteria:** Every applied amendment has a source message ID and rationale; untrusted messages cannot override rules; uncertain credits never increase safe capacity.
- **Verification commands:** `python code/main.py --dataset-dir dataset --extract-messages --offline`; `python -m pytest tests/test_message_evidence.py -q`.

### 9. Reconstruct the effective financial state at each request date

- **Goal:** Produce a clean cash ledger and explicit forecast assumptions for one user/request date.
- **Files/modules:** `code/ledger.py`, `code/evidence.py`.
- **Implementation tasks:** Start from `current_available_balance`; apply resolved event lifecycles without double-counting linked original/reversal/refund records; reserve pending debits; ignore pending credits, failed/cancelled transactions, unconfirmed bonuses/commissions/refunds/prizes, and unrealized investment valuations. Include confirmed salary/invoice income only on the resolved settlement date. Identify same-account debit/credit transfers and exclude their net-zero pair from income/expense recurrence. Apply evidence-derived revisions before classifying cash state.
- **Dependencies:** Steps 5–8.
- **Tests:** Pending debit versus credit, failed debit with retry, cancellation/reversal, refund settlement, linked investment lifecycle, internal-transfer pair, and evidence-overrides-event fixtures.
- **Acceptance criteria:** The ledger provides a dated net cash flow, a reserved-obligation list, and an audit trace for every include/exclude decision.
- **Verification commands:** `python -m pytest tests/test_ledger.py -q`; `python code/main.py --dataset-dir dataset --inspect-request request_26`.

### 10. Detect recurrence and construct conservative baseline forecast rules

- **Goal:** Infer only supported recurring commitments/income and essential variable-spend assumptions.
- **Files/modules:** `code/recurrence.py`.
- **Implementation tasks:** Cluster same-user events by category/description/direction and recognize stable weekly, monthly, quarterly, or fixed-period cadence only when at least three compatible historical instances support it (or a confirmed future event/message supplies a specific next occurrence). Derive cadence, next date, and conservative amount: fixed commitments use the latest applicable amount; protected essential variable categories use a high recent robust amount (for example max of latest three or upper quantile); flexible categories remain baseline spending unless deliberately changed. Do not infer recurrence from isolated purchases/transfers/refunds. Apply salary/rent/end-of-income amendments to future rule instances.
- **Dependencies:** Steps 6, 8–9.
- **Tests:** Monthly/weekly/quarterly patterns, outlier exclusion, one-off rejection, rent-increase amendment, salary reduction, ended employment, and future confirmed payment.
- **Acceptance criteria:** Each forecast rule documents supporting event IDs, cadence, amount selection, and confidence; no unsupported future cash flow is invented.
- **Verification commands:** `python -m pytest tests/test_recurrence.py -q`; `python code/main.py --dataset-dir dataset --inspect-request request_27 --show-rules`.

### 11. Implement the 90-day daily balance simulator

- **Goal:** Simulate a conservative daily balance path for a request and optional candidate plan.
- **Files/modules:** `code/forecast.py`.
- **Implementation tasks:** Generate inclusive dates from request date through request date + 90 days; merge effective scheduled/pending obligations, confirmed credits, recurring rules, and candidate payments by date; use a deterministic same-day ordering that reserves all debits before relying on credits (conservative safety). Record opening balance, cash items, closing balance, running minimum, and first breach. A plan is feasible only when all dates remain at or above the profile minimum balance; optionally continue through the later of day 90 and last plan payment to ensure every listed payment is checked, while still keeping the mandatory 90-day path.
- **Dependencies:** Steps 9–10.
- **Tests:** Same-day salary/debit, pending debit, minimum equality, breach detection, 90th-day boundary, plan payment beyond deadline, and deterministic ordering.
- **Acceptance criteria:** The simulator is pure, produces the same ledger path on repeated calls, and identifies the exact blocking date/item.
- **Verification commands:** `python -m pytest tests/test_forecast.py -q`.

### 12. Calculate `amount_safe_to_pay` and full-payment capacity dates

- **Goal:** Derive the maximum safe payment today before spending changes and the earliest safe one-time full payment date.
- **Files/modules:** `code/forecast.py`, `code/decision.py`.
- **Implementation tasks:** Calculate today’s safe cap as `min(requested_amount, max(0, baseline_running_minimum_headroom_after_inserting_a_request_date_debit))`, then confirm it in the simulator. Search dates from request date through the 90-day forecast for the first date a full requested-amount one-time payment is feasible without changes; report request date if immediately feasible, otherwise the first safe date, otherwise empty. This capacity calculation is independent of payment-method preferences.
- **Dependencies:** Step 11.
- **Tests:** Zero, partial, full, next-income-date, later breach, cap boundary/rounding, and no-date-within-horizon cases.
- **Acceptance criteria:** `0 <= amount_safe_to_pay <= requested_amount`; every reported safe amount/date passes an independent simulation.
- **Verification commands:** `python -m pytest tests/test_capacity.py -q`; `python code/main.py --dataset-dir dataset --inspect-request request_27 --show-capacity`.

### 13. Generate all payment-plan candidates exactly from allowed sources

- **Goal:** Enumerate feasible-plan shapes without prematurely choosing one.
- **Files/modules:** `code/plans.py`.
- **Implementation tasks:** Generate full-payment candidates only from a matching supplied option; expand installment dates/amounts exactly from each supplied installment option, checking that number, frequency, summed amounts, fee, and total payable agree after decimal quantization. Generate one partial-payment candidate only when request and profile permit it, the cap is strictly between zero and requested amount, and the previously calculated full-payment date is by the desired completion date; it has exactly two payments. Generate a wait candidate only if full payment becomes safe later, is by deadline, and the user accepts `full_payment`. Attach payment-option ID, total paid, first payment date, count, completion date, and a simulator result.
- **Dependencies:** Steps 3, 11–12.
- **Tests:** Each valid method, disallowed preference, blank maximum installment months, term above maximum, mismatched option schedule, partial amount sum, late completion, and wait eligibility.
- **Acceptance criteria:** Every candidate either exactly follows a supplied option or the strictly specified partial-plan rule; all ineligible candidates record a reason.
- **Verification commands:** `python -m pytest tests/test_plans.py -q`.

### 14. Optimize permitted spending changes

- **Goal:** Find only valid, minimal changes that turn an otherwise infeasible deadline-completing candidate into a feasible one.
- **Files/modules:** `code/optimize.py`, `code/forecast.py`.
- **Implementation tasks:** Identify recurring flexible debit events only when `flexibility` permits the action and category appears in the profile’s reduce/stop sets; never select protected categories. Enumerate bounded combinations of at most three distinct event IDs, with mutually exclusive stop/reduce actions per event. For reductions, search legal amounts from `minimum_allowed_amount` through original amount at currency-appropriate increments; apply the change prospectively to forecasted instances and rerun simulation. Rank feasible changes by fewest actions, least total reduction, earliest relief, stable event-ID order.
- **Dependencies:** Steps 9–13.
- **Tests:** Protected exclusion, fixed exclusion, allowed reduction floor, allowed stop, no duplicated event action, maximum-three rule, and no unnecessary changes.
- **Acceptance criteria:** Returned actions are syntactically valid, permitted, necessary for the selected plan, and independently make its forecast safe.
- **Verification commands:** `python -m pytest tests/test_optimize.py -q`; `python code/main.py --dataset-dir dataset --inspect-request request_26 --show-changes`.

### 15. Select the final decision deterministically

- **Goal:** Convert candidates and optional optimized candidates into one valid output decision.
- **Files/modules:** `code/decision.py`.
- **Implementation tasks:** Filter candidates for safety, profile method acceptance, maximum installment months, and completion by desired date. Rank strictly by: completion by deadline; no changes; lower total payable; earlier first payment; fewer payments; lower numeric payment-option ID. Map the selected candidate to status/method: safe immediate full payment is `affordable_now`; safe partial/installments/changes is `affordable_with_plan`; later safe full payment with eligible wait is `affordable_later`/`wait`; otherwise `not_affordable`/`not_recommended` with plan `none`. Preserve an explicit reason when capacity exists but preferences make all actions ineligible.
- **Dependencies:** Steps 12–14.
- **Tests:** Ranking tie breakers in order, full-payment preference rejection, wait preference, affordable later/no eligible wait, selected spending changes, and fallback behavior.
- **Acceptance criteria:** Exactly one decision exists per request and all output fields remain mutually consistent with the challenge contract.
- **Verification commands:** `python -m pytest tests/test_decision.py -q`.

### 16. Produce concise, grounded decision explanations

- **Goal:** Explain the selected decision using deterministic, non-speculative facts.
- **Files/modules:** `code/explain.py`.
- **Implementation tasks:** Use templates keyed by decision method/status. Include home currency and formatted recommendation amount/schedule, required minimum balance, meaningful constraint (for example reserved pending debit, next confirmed salary date, or deadline), and any selected change. Never claim pending credit, projected investment gain, or invented income; never reveal sensitive document details. Optionally run an LLM only as an offline, schema-constrained proofreading pass, then validate it contains no changed numbers/dates/methods; deterministic template text remains the default and fallback.
- **Dependencies:** Step 15.
- **Tests:** Each method/status template, exact amounts/date inclusion, no unsupported claim, explanation length, and optional-prose validation fallback.
- **Acceptance criteria:** Each explanation is concise, factually derivable from the trace, and agrees with every other output field.
- **Verification commands:** `python -m pytest tests/test_explain.py -q`.

### 17. Generate `output.csv` and audit artifacts

- **Goal:** Serialize all 250 decisions in the exact required form without corrupting the input template.
- **Files/modules:** `code/main.py`, `code/reporting.py`.
- **Implementation tasks:** Iterate in `requests.csv` order and write the eight required columns in the mandated order. Format dates `YYYY-MM-DD`, plans in chronological `date:amount|...` form, and `none` exactly where required. Write a separate JSONL audit record per request containing inputs by ID, evidence IDs, forecast minimum/breach, candidate comparisons, selected rule path, and no secrets. Do not put audit data into the submission output.
- **Dependencies:** Steps 15–16.
- **Tests:** Header/order, row count/order, decimal formatting, CSV quoting, output path override, and determinism over two runs.
- **Acceptance criteria:** The file has exactly one row per evaluation request and is byte-stable when inputs/configuration do not change.
- **Verification commands:** `python code/main.py --dataset-dir dataset --output /tmp/output.csv --audit-dir /tmp/buy-or-wait-audit`; `wc -l /tmp/output.csv`.

### 18. Implement output validation and adversarial checks

- **Goal:** Independently reject invalid submissions and invalid internal decisions before packaging.
- **Files/modules:** `code/validate.py`, `code/main.py`.
- **Implementation tasks:** Validate schema/order, unique/comprehensive request IDs, allowed enums, decimal bounds, date format, plan chronology, amount totals, option-schedule exactness, partial-payment two-payment requirement, `affordable_now` date equality, empty no-full-payment date, changes syntax/permissions/count, and explanation consistency. Re-simulate every proposed plan and change set from serialized data. Add safety checks for image/message prompt injection, missing FX, missing image amount, duplicate lifecycle counting, unconfirmed credit, and protected-spend modification.
- **Dependencies:** Steps 6–17.
- **Tests:** One negative fixture per invariant plus full real-output validation.
- **Acceptance criteria:** A single invalid row causes a nonzero exit with row/field-specific diagnostics; the produced real output validates cleanly.
- **Verification commands:** `python code/main.py --validate-output /tmp/output.csv --dataset-dir dataset`; `python -m pytest tests/test_validate.py -q`.

### 19. Test and evaluate against samples, scenarios, and determinism

- **Goal:** Measure correctness before hidden evaluation without treating samples as hardcoded labels.
- **Files/modules:** `tests/`, `evaluation/evaluate_samples.py`, `evaluation/README.md`.
- **Implementation tasks:** Add unit tests from the previous steps, integration tests for representative event/message/image cases, and golden tests for the 25 sample rows. Use samples to tune documented conservative recurrence and extraction policy, not to add request/user-specific branches. Build a score report comparing every output field, highlighting disagreement type and simulator trace. Add property tests for no-below-minimum and amount/plan invariants; rerun full data twice to assert deterministic bytes and audit stability.
- **Dependencies:** Steps 1–18.
- **Tests:** Unit, integration, sample evaluation, property/fuzz malformed inputs, reproducibility, and a full 250-request smoke test.
- **Acceptance criteria:** All validation/property tests pass; sample results have a documented score and each residual is reviewed with an evidence trace.
- **Verification commands:** `python -m pytest -q`; `python evaluation/evaluate_samples.py --dataset-dir dataset`; `shasum -a 256 /tmp/output.csv` after two runs.

### 20. Control LLM/API use, tokens, and cost

- **Goal:** Use paid models only where they add evidence-extraction value and create the required transparent usage report.
- **Files/modules:** `code/evidence.py`, `code/reporting.py`, `evaluation/usage_report.md`.
- **Implementation tasks:** Default to offline deterministic extraction/parsing; invoke an LLM only for ambiguous messages/images, once per content hash and prompt version. Batch messages by language/type when a model supports structured batched output; cache successes and failures; set low output-token limits and JSON schemas; never send unrelated whole histories or financial datasets. Record provider/model, calls, cache hits, input/output tokens, pricing assumptions, total/average per request, and zero-cost offline paths. Require an explicit `--use-llm` opt-in and make a cache-only run reproducible without credentials.
- **Dependencies:** Steps 7–8, 17.
- **Tests:** Cache hit avoids an API call, malformed model JSON falls back safely, token counter aggregation, no secret in report, and offline full run.
- **Acceptance criteria:** `evaluation/usage_report.md` describes the exact final run and meets the competition fields; financial output does not depend on nondeterministic prose generation.
- **Verification commands:** `python code/main.py --dataset-dir dataset --offline --output /tmp/output.csv`; `test -f evaluation/usage_report.md`; `rg -n 'API[_-]?KEY|sk-' evaluation/usage_report.md`.

### 21. Package, reproduce, and submit

- **Goal:** Produce the three required submission artifacts with a verified clean-room run.
- **Files/modules:** `README.md`, `code.zip`, root `output.csv`, `evaluation/usage_report.md`, chat transcript export.
- **Implementation tasks:** Document prerequisites, environment variables, setup, offline and optional-LLM run commands, validation command, design assumptions, and reproducibility limits. Build `code.zip` containing source, prompts/configuration, tests if desired, README, and `evaluation/usage_report.md`, while excluding `.env`, caches with sensitive text, API keys, organizer-only files, and `log.txt`. In a clean temporary directory, unzip, install dependencies, run against `dataset/`, validate the output, compare row counts/checksum, and inspect the archive manifest. Export the required development chat transcript separately.
- **Dependencies:** Steps 1–20.
- **Tests:** Archive manifest, clean-environment run, output validation, and secret scan.
- **Acceptance criteria:** `code.zip`, completed root `output.csv`, and chat transcript are ready; the zip includes `evaluation/usage_report.md` and a reviewer can reproduce the generated output using README instructions.
- **Verification commands:** `zipinfo -1 code.zip`; `unzip -t code.zip`; `python code/main.py --dataset-dir dataset --output output.csv`; `python code/main.py --validate-output output.csv --dataset-dir dataset`.

## Decision-engine invariants to enforce throughout

1. Never use organizer-only files, live financial data, or labels from sample requests for evaluation predictions.
2. Reserve pending debits; do not spend unconfirmed/pending credits, bonuses, commissions, refunds, prizes, or unrealized investment values.
3. Apply currency conversion using the supplied rate on the resolved settlement/cash date only.
4. No recommended balance path may dip below `minimum_balance_to_keep` on any simulated day.
5. Do not invent recurrence, income, expenses, evidence, payment options, or FX rates.
6. A partial plan has exactly two payments and an installment plan exactly reproduces its supplied option.
7. Spending changes are prospective, permitted, non-protected, at most three, and never stop and reduce the same event.
8. Every emitted field must be revalidated from its serialized representation.

## Completion gates

Proceed to final packaging only after: input/evidence audits are clean or explicitly resolved; all 16 image-backed blank amounts have a verified interpretation; test suite and output validation pass; the full evaluation run is deterministic; `usage_report.md` is generated from that final run; and the clean-room archive reproduction succeeds.
