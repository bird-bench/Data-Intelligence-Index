# Data Intelligence Index (DII) v1.0 <img src="materials/hku-logo.jpg" alt="HKU" width="36" style="vertical-align: middle;"/>  <img src="materials/bird_fig_main.png" alt="BIRD" width="36" style="vertical-align: middle; position: relative; top: -3px;"/>

[![Blog](https://img.shields.io/badge/Blog-Data%20Intelligence%20Index-blue)](https://livesqlbench.ai/data-intelligence-index/)
[![Dashboard](https://img.shields.io/badge/Dashboard-Live%20Leaderboard-green)](https://livesqlbench.ai/data-intelligence-index/dashboard)
[![Harbor](https://img.shields.io/badge/Harbor%20Hub-dii--v1--muscle-purple)](https://hub.harborframework.com/datasets/birdsql/dii-v1-muscle)
[![Hugging Face](https://img.shields.io/badge/Hugging%20Face-dii--v1--muscle-orange)](https://huggingface.co/datasets/birdsql/dii-v1-muscle)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

DII v1.0 Muscle is a compact, challenging benchmark for data agents: **500 hard data tasks from 9
benchmarks**, covering database question answering, database analysis and operations, DB application
debugging, user interaction, data science, query optimization and computer use. Each task is a sandboxed
[Harbor](https://harborframework.com) environment with real databases, code and files, and is scored by
the original benchmark's checks. Plug in any agent: mini-swe-agent, Claude Code, Codex CLI or your own.

**200 tasks are public** on [Harbor Hub](https://hub.harborframework.com/datasets/birdsql/dii-v1-muscle)
and [Hugging Face](https://huggingface.co/datasets/birdsql/dii-v1-muscle). **300 tasks are held out** as
a hidden test set, so models can be compared on tasks that are not published. Both halves rank the models almost the same way: of the 12
models scored, only the last two swap places. To have your model scored on the hidden tasks, email
[bird.bench25@gmail.com](mailto:bird.bench25@gmail.com).

Muscle is the hard part of DII v1. A larger, broader **Overall** set will follow; it uses the same code
here, with more tasks.

Presented by <img src="materials/bird_fig_main.png" alt="BIRD" width="20" style="vertical-align: middle;"/> [the BIRD Team](https://bird-bench.github.io/) (HKU).

## What is in DII v1.0 Muscle

| Category | Benchmark | What the agent does | Public tasks |
|---|---|---|---:|
| Database Question Answering | [BIRD Mini-Dev](https://github.com/bird-bench/mini_dev) | Answer a question with SQL on SQLite, PostgreSQL or MySQL | 36 |
| Database Analysis & Operations | [LiveSQLBench](https://github.com/bird-bench/livesqlbench) | Analyze and change business data (SELECT, UPDATE, CREATE, DELETE) using business documents | 20 |
| DB Application Debugging | [BIRD-Critic](https://github.com/bird-bench/BIRD-CRITIC-1) | Find and fix a broken SQL query from a user's issue | 28 |
| | [BIRD-Critic 1.5: SWE](https://bird-critic.github.io/bird-critic-15/) | Fix database-related bugs in real application code bases | 22 |
| Human-centric Interaction | [BIRD-Interact](https://github.com/bird-bench/BIRD-Interact) | Work with a simulated user over several turns: ask, clarify, then query or change the database | 21 |
| Data Science | [DS-CodeTrans](https://arxiv.org/pdf/2609.30749) | Translate a whole data-science project between implementations so its outputs still match | 14 |
| | [DARE-Bench](https://github.com/Snowflake-Labs/dare-bench) | Build a machine-learning model on Kaggle-style data and submit predictions | 17 |
| Query Optimization | [Effi-SQL](https://huggingface.co/datasets/birdsql/Effi-SQL) | Rewrite a slow query so it returns the same rows faster | 17 |
| Computer-Use | [Workspace-Bench](https://github.com/OpenDataBox/Workspace-Bench) | Produce reports, spreadsheets and documents from a large personal workspace of files | 25 |
| | | **Total public** | **200** |

The other **300 tasks** form the hidden test set. They come from the same 9 benchmarks.

## Scores

All 500 tasks (public and hidden), reference agent setup. Index and category scores are out of 100.
Per-benchmark scores are on the [dashboard](https://livesqlbench.ai/data-intelligence-index/dashboard).

| # | Model | Index | DB QA | DB Analysis & Ops | App Debug | Human-centric | Data Science | Query Opt. | Computer-Use |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | Claude Opus 5.5 (max) | **71.2** | 80.0 | 60.8 | 68.4 | 85.2 | 70.4 | 79.8 | 54.1 |
| 2 | GPT-6 Astra (max) | **70.4** | 73.0 | 70.6 | 70.0 | 68.7 | 79.3 | 77.6 | 53.3 |
| 3 | GPT-6.1 Sol (high) | **66.2** | 64.4 | 68.6 | 65.6 | 66.9 | 74.5 | 71.3 | 51.9 |
| 4 | GPT-5.6 Sol (xhigh) | **62.0** | 66.7 | 68.8 | 65.6 | 43.5 | 66.0 | 75.0 | 48.2 |
| 5 | GPT-5.6 Sol (high) | **60.9** | 65.6 | 66.7 | 66.1 | 42.9 | 63.9 | 73.8 | 47.6 |
| 6 | DeepSeek V4.1 Flash (max) | **59.3** | 64.4 | 58.8 | 59.8 | 57.1 | 52.2 | 72.5 | 50.0 |
| 7 | GPT-5.6 Sol (medium) | **58.7** | 64.4 | 68.6 | 60.8 | 41.9 | 60.0 | 70.6 | 44.2 |
| 8 | GLM-5.3 (max) | **55.6** | 62.2 | 49.0 | 52.6 | 48.6 | 59.1 | 67.7 | 49.7 |
| 9 | GPT-5.6 Sol (low) | **51.9** | 65.6 | 52.9 | 52.4 | 40.6 | 52.0 | 62.8 | 36.9 |
| 10 | Qwen 3.8 Max (max) | **50.9** | 58.4 | 47.1 | 51.1 | 41.5 | 49.0 | 64.0 | 45.3 |
| 11 | GPT-5.6 Luna (high) | **49.8** | 50.6 | 54.9 | 52.9 | 32.1 | 53.6 | 61.2 | 43.1 |
| 12 | GLM-5.3 Flash (max) | **49.5** | 57.8 | 49.0 | 53.4 | 44.4 | 41.2 | 48.8 | 51.9 |

**How the index is computed.** Each task gets a score from 0 to 1: pass or fail for most benchmarks,
a partial score for DARE-Bench and Workspace-Bench, and a speed-up tier for Effi-SQL
(1 for at least 2x faster, 0.75 for 1.2x, 0.5 for 1x, 0.25 for 0.5x, and 0 if the rows differ or the
query is slower than 0.5x).
A benchmark's score is the mean over its tasks (x100). A category's score is the average of its
benchmark scores (DB Application Debugging: BIRD-Critic and BIRD-Critic 1.5; Data Science: DS-CodeTrans
and DARE-Bench; the other five categories have one benchmark each). **The index is the plain average
of the 7 category scores.** Tasks that failed for infrastructure reasons (for example Docker errors, or a grader
that could not run) are left out and should be rerun; a run that hits the time limit still counts. See [`dii_harbor/score.py`](dii_harbor/score.py).

## Running the tasks

```bash
git clone https://github.com/bird-bench/Data-Intelligence-Index.git
cd Data-Intelligence-Index
python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install .
cp .env.example .env    # add your API keys

python -m dii_harbor.run --model openai/gpt-6-astra --jobs-dir jobs/astra
python -m dii_harbor.score --run astra=jobs/astra
```

The runner takes the 200 public tasks from Harbor Hub and uses the DII v1.0 Muscle settings by default.
[docs/running.md](docs/running.md) has the details: keys, choosing tasks, settings and other agents.

You will need Docker on an x86 Linux machine (Docker Desktop on Apple silicon runs most tasks under
emulation, with limits listed in [docs/running.md](docs/running.md#macos)), Python 3.12+, enough free disk
for the task images (Workspace-Bench and BIRD-Critic 1.5 images are the largest), and an OpenAI API key: Interact
tasks use it for the simulated user and Workspace tasks use it for the judge (both `openai/gpt-6-luna`),
whatever model you test.

## The reference agent setup

All DII v1.0 Muscle scores use the same simple agent, so the index compares models rather than agent
frameworks:

- [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) 2.4.6 with a bash tool and a short
  system prompt (see [`dii_harbor/run.py`](dii_harbor/run.py)).
- At most 300 model calls and 3600 seconds per task, 180 seconds per command, 16,384 output tokens per
  model call (thinking included). Workspace-Bench tasks use 65,536, because a cut-off reply breaks a
  Workspace task; this is the same for every model.
- The task container has no Internet access. The agent loop runs on the host, calls the model API, and
  sends each command into the container.

**About the reply-length cap.** Today's scores cap each model reply at 16,384 tokens, the same for every
model. The exception is Workspace-Bench: its tasks use 65,536 for every model, because a cut-off reply
breaks a Workspace task. The 16,384 cap is stricter than usual Harbor practice. Because a
model's thinking counts against it, it cuts off Claude's replies far more often than GPT's. The next
version will use 64k or no cap for all models.

## Hidden test set

The 300 hidden tasks are not published. To have a model evaluated on them, or to add your model or
benchmark to the index, write to [bird.bench25@gmail.com](mailto:bird.bench25@gmail.com). As models
improve, we retire tasks that get easy and add harder ones.

Some hidden tasks are adapted from benchmarks whose original data is public (for example BIRD
Mini-Dev), so holding them out lowers, but does not remove, the chance that a model has seen them.

## Earlier version

DII v0 (April 2026) used prompt-only evaluation on the original benchmarks. Its description and
benchmark links are kept in [docs/v0.md](docs/v0.md).

## Citation

```bibtex
@misc{dii2026,
  title        = {Data Intelligence Index (DII) v1.0},
  author       = {{HKU BIRD Team}},
  year         = {2026},
  howpublished = {\url{https://github.com/bird-bench/Data-Intelligence-Index}}
}
```

Please also cite the benchmarks you use; each one's page is linked in the table above.

## License

The code in this repository is MIT licensed. Each task keeps the license of the benchmark it comes
from. DARE-Bench (Shu et al.) and Workspace-Bench (Tang et al.) are by other groups; their tasks list
their authors, and we thank them for their work.
