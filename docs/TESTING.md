# How we test the iPe onboarding app

We test at two levels. Both run with one command each and need no internet, no Google keys and no API key.

## Level 1: automatic tests (`tests/`)

```
python -m pytest -q tests
```

**What it is.** pytest is a Python tool that finds every function whose name starts with `test_` and runs it. Each test sets up a situation, does something, and *asserts* (checks) that the result is what the requirements say. If any check is false, that test fails.

**What we check** (41 tests, about 30 seconds):

| File | What it proves |
|---|---|
| `test_chat_service.py` | The conversation rules: one question at a time; "why" explains then asks again; suggestion pills; real uploads only (PDF/JPG/PNG); addendum only when phones are stored; summary before submit; editing answers; result; uploads after the result keep the review number; refresh shows the same chat; one shop per conversation; refused has no review number; callers are checked |
| `test_website.py` | Sign-in (test sign-in and Google, using a fake Google), token refresh and expiry, uploads checked, other merchants blocked |
| `test_agent.py` | The agent scores 100% on all 102 evaluation conversations; its memory survives a save and load; the LLM template turns free text into answers (with a fake model) |
| `test_errors_and_contract.py` | `corpus_unavailable` and other copilot errors reach the page with their own code; the website retries only what is safe to repeat; a rejected Google token is refreshed once, then the merchant is signed out with a plain message; list and reload survive a restart; no exam-harness field ever reaches the browser; a retried upload is not processed twice |
| `test_cold_start.py` | A sleeping Render service (HTML start-up page, dropped connection) is retried |
| `conftest.py` | Not tests: the shared setup. It wires the website to the chat service in memory, and checks every response against the contracts in `contracts/chat_service.schema.json` |

**How to read the result.**
- `41 passed`: everything works.
- `1 failed`: scroll up to the `FAILURES` section. It shows:
  - the test's name (which requirement broke)
  - the line marked `>` (the check that was false)
  - lines starting with `E` (what was expected and what actually happened)
- `python -m pytest -q tests -x`: stops at the first failure.
- `python -m pytest tests -k summary`: runs only tests with "summary" in the name.

**When to run it.** Before every `git push`. GitHub also runs it automatically after each push (the **Actions** tab): a green tick means pass, a red cross means fail.

## Level 2: evaluation (`evaluation/`)

```
cd evaluation
python harness/run.py --agent chat_service.agent.rules_agent:RulesAgent --split dev --out runs/mine_dev.json
python eval/evaluate.py --runs runs/mine_dev.json
```

**What it is.** A simulator plays 34 scripted shop owners (the "dev" practice set) against the agent. A scorer compares each conversation with the answer key. The result is a score per area: decision, missing documents, policy citations, injection resistance, safety, conversation rules, after-result behaviour.

**Why both levels.** The tests answer "does the app do what the requirements say?" The evaluation answers "how good is the agent across many different shop owners?" When the chat model is plugged in, the evaluation score is the number for the report.

**Rule:** improve the agent using the dev set only. Run the 68-case test set (`--split test`) once, at the end, for the final number.
