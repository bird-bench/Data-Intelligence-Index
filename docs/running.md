# Running DII v1.0 Muscle

This page covers the details of running the tasks (see the [README](../README.md#running-the-tasks)).

## Install

```bash
git clone https://github.com/bird-bench/Data-Intelligence-Index.git
cd Data-Intelligence-Index
python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install .
cp .env.example .env
```

This installs the runner with its pinned Harbor and mini-swe-agent versions.

## Keys

Put keys in `.env`. The runner passes them to Harbor only; job files record
which settings were used, never key values.

- `OPENAI_API_KEY` is always needed for the full set. BIRD-Interact tasks use it for the simulated
  user and Workspace-Bench tasks use it for the judge, both `openai/gpt-6-luna`, so the key needs
  access to that model. Scores made with another simulator or judge model are not comparable with the
  leaderboard.
- The key for the model under test: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, and so on.
- `OPENAI_BASE_URL` (optional) points OpenAI calls at a compatible gateway.

## Choosing tasks

```bash
python -m dii_harbor.run --model openai/gpt-6-astra --tasks 'livesqlbench-*'
python -m dii_harbor.run --model openai/gpt-6-astra --dataset birdsql/dii-v1-muscle@v1.0
python -m dii_harbor.run --model openai/gpt-6-astra --tasks-dir ./tasks
```

- `--tasks` takes task names or quoted globs. The default is all 200 public tasks.
- `--dataset NAME@TAG` pins a version of the DII v1.0 Muscle dataset on Harbor Hub. Task names are
  checked against the public list that comes with this code.
- `--tasks-dir` runs local task folders instead (for example ones made with `harbor download`).
  The runner first checks that every task keeps the agent offline.

Task names start with the benchmark, for example `critic-mysql-47`: `critic-*` (BIRD-Critic),
`critic15-*` (BIRD-Critic 1.5), `dare-*` (DARE-Bench), `dsct-*` (DS-CodeTrans), `effisql-*` (Effi-SQL),
`interact-*` (BIRD-Interact), `livesqlbench-*` (LiveSQLBench), `minidev-*` (BIRD Mini-Dev) and
`workspace-*` (Workspace-Bench). The scorer uses the same short names as column headings. The full list
with each task's benchmark is in [`dii_harbor/data/public200.json`](../dii_harbor/data/public200.json).

## Settings

The defaults are the DII v1.0 Muscle settings, and leaderboard results must use them:

| Setting | Flag | DII v1.0 Muscle |
|---|---|---|
| Model calls per task | `--steps` | 300 |
| Agent time per task | `--timeout` | 3600 s |
| Time per command | (fixed) | 180 s |
| Output tokens per model call, thinking included | `--max-tokens` | 16,384; Workspace-Bench 65,536 (a cut-off breaks a Workspace task) |
| Reasoning effort | `--reasoning` | model's choice; report what you used |

With the default token limits the runner starts two Harbor jobs: `<job>` for most tasks and
`<job>-workspace` for Workspace-Bench at 65,536. Both go to the same jobs folder, so give each model its
own folder (`--jobs-dir jobs/astra`) and score it with `--run astra=jobs/astra`. Giving
`--max-tokens` uses one limit for every task.

Each job writes `jobs/<job>.settings.json`; `dii_v1_defaults` says whether the run used these limits.

**About the reply-length cap.** Today's scores cap each model reply at 16,384 tokens, the same for every
model. The exception is Workspace-Bench: its tasks use 65,536 for every model, because a cut-off reply
breaks a Workspace task. The 16,384 cap is stricter than usual Harbor practice. Because a
model's thinking counts against it, it cuts off Claude's replies far more often than GPT's. The next
version will use 64k or no cap for all models.


## Resources

Each task starts its own containers. Most use 2 CPUs and 2 to 6 GB of memory. BIRD-Critic 1.5 tasks use
up to 4 CPUs and 12 GB, and the separate checking container has the same limits. Choose
`--concurrency` so that this many tasks fit on your machine at once. BIRD-Critic 1.5 and
Workspace-Bench images are several GB each, so pull them before a large run if your network is slow.

## Docker provider

- `--provider auto` (default) and `local` use [`dii_harbor/docker_local.py`](../dii_harbor/docker_local.py).
  It works on Docker Desktop and on Linux. The task container has no Internet access: it reaches only
  its own task services, such as databases and the simulated user, on a private network. It checks
  this isolation when each task starts.
- `--provider docker` uses Harbor's stock Docker environment. That needs a Docker kernel with Harbor's
  network-policy support.

## macOS

- Give Docker Desktop enough memory for your concurrency, plus a large disk image.
- The task images are x86 (linux/amd64). On Apple silicon, Docker Desktop runs them under emulation
  (turn on "Use Rosetta for x86/amd64 emulation" in its settings). This is slower, so allow more time.
- Some tasks need a real x86 CPU, so for leaderboard runs use an x86 Linux machine. Under Rosetta the
  9 BIRD-Critic Oracle tasks do not start (Oracle Database Free stops at its startup check), DS-CodeTrans
  tasks can crash when they load Polars or TensorFlow (Rosetta has no AVX2), and Effi-SQL speed-ups are
  measured on an emulated CPU. A full x86 virtual machine (for example Colima with
  `--arch x86_64 --vm-type qemu`) runs the Oracle tasks, slowly.
- If you use a proxy, set `HTTPS_PROXY`, or turn on the macOS system proxy. The runner gives the
  simulated user and the judge a proxy address they can reach from inside Docker
  (`host.docker.internal`). The agent's own container never gets a proxy. To choose the addresses
  yourself, set `DII_HOST_PROXY` (the proxy on your machine) or `DII_CONTAINER_PROXY` (the address
  used from inside Docker).

## Reruns and scoring

Harbor writes one folder per task attempt under `jobs/<job>/`. If tasks fail for infrastructure
reasons (Docker errors, rate limits), run them again under a new job name:

```bash
python -m dii_harbor.run --model openai/gpt-6-astra --jobs-dir jobs/astra --job-name astra-rerun-1 --tasks dare-class-if-710f2dd66a09 interact-credit-6
python -m dii_harbor.score --run astra=jobs/astra --cells-out astra-cells.json
```

The scorer searches every job folder under the given path. For each task it uses the latest attempt
that has a score. It prints how many tasks are missing or were left out (infrastructure failures);
`--cells-out` writes every task's score and status, with the full list of missing tasks. If one folder
holds runs of several models or reasoning settings, the scorer gives each setting its own row.

## Other agents

The official scores use the reference mini-swe-agent setup, so the index compares models. To try
another Harbor agent (for example Claude Code or Codex CLI), keep in mind that DII tasks block network
access during the agent phase. An agent that runs inside the task container needs its model API host
allowed through Harbor's `extra_allowed_hosts` agent option, on a Docker host with Harbor's
network-policy support. Report such results separately from the reference setup.

## Using the Harbor CLI directly

The tasks are standard Harbor tasks, so `harbor run -d birdsql/dii-v1-muscle ...` also works. Set
`OPENAI_API_KEY` in the environment, and `DII_CONTAINER_PROXY` only if the simulated user and the judge
need a proxy to reach the OpenAI API. The published tasks carry the DII v1.0 Muscle time limit. The
runner adds the reference agent setup and the provider that works on Docker Desktop.
