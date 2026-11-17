# Generative AI on Databricks

Databricks Academy GenAI courses, set up to be **edited in VS Code and executed on Azure Databricks
serverless compute (environment version 6)**. Clone it, run one script, and the notebooks run in
your own workspace. Companion repo: [databricks-ml-learning](https://github.com/saquibherman/databricks-ml-learning).

| Week | Course | Notes |
|------|--------|-------|
| [week1](week1/) | *Building RAG Agents with Agent Bricks* (v2.0.1): retrieval agents, Lakebase search, PDF parsing, chunking, AI Search | [NOTES](week1/NOTES.md) |
| [week2](week2/) | *Building Agentic Applications on Databricks* (v2.0.0): agent tools, MCP, OpenAI Agents SDK, LangChain, DSPy | [NOTES](week2/NOTES.md) |
| [week3](week3/) | *GenAI Agent Evaluation on Databricks* (v1.2.0): MLflow judges, guideline and custom judges, human feedback | [NOTES](week3/NOTES.md) |
| [week4](week4/) | *Deploying and Monitoring Agents on Databricks* (v1.1.0): deploying observable agents, tracing, online evaluation | [NOTES](week4/NOTES.md) |

---

## Quick start

**Prerequisites**
- Azure Databricks workspace with **Unity Catalog**, **serverless compute** and Foundation Model APIs
- Linux, macOS or WSL with `bash`, `curl`, `unzip`, `python3`
- VS Code with the **Databricks** extension (on WSL: install it *in WSL*)
- From **Databricks Marketplace**, add **Airbnb Sample Data** (provider: Databricks), shared catalog
  `databricks_airbnb_sample_data` (weeks 2, 3, 4)

**Setup**

```bash
git clone https://github.com/saquibherman/databricks-genai-learning.git
cd databricks-genai-learning
./setup.sh
```

`setup.sh` is safe to re-run. It installs the Databricks CLI and `uv` if missing (no sudo), logs in
(OAuth profile `azure-ml`), creates `.venv` (Python 3.12) with the course libraries, checks the
`workspace` catalog, the Airbnb share and the course models, writes `.env` (optional 90-day token
profile `azure-ml-pat` for local runs) and syncs the courses to
`/Workspace/Users/<you>/.bundle/genai-course/dev/files`.

**One-time shared setup for weeks 3 and 4** (creates the course model services, tools and the
shared agent): run `week4/Includes/Workspace-Setup` and `week3/Includes/Workspace-Setup` once, on
serverless. Week 3's also deploys the Databricks App `agent-airbnb-assistant`, which bills while
running; stop it in **Compute → Apps** when you're not using it.

**Run a notebook**: right-click → **Run File as Workflow**, or open it in the Databricks UI
(environment version 6 is pinned in every notebook).

---

## Models

The courses were written for OpenAI GPT models, which on Azure Databricks require **ADI Services**
(account setting *Integration with ADI Services*, plus eligibility via the Azure Databricks account
team). Until that's enabled, the configs use Claude:

| Course model | Role | Used here |
|---|---|---|
| `databricks-gpt-5-2` | agent / tool model (week 2) | `databricks-claude-sonnet-5-5` |
| `databricks-gpt-6-1-sol` | agent model (weeks 3, 4) | `databricks-claude-sonnet-5-5` |
| `databricks-gpt-6-luna` | judge model (weeks 3, 4) | `databricks-claude-haiku-5-5` |
| `databricks-gpt-5-mini` | custom judge (week 3) | `databricks-claude-haiku-5-5` |

Each change is a `served_by:` line in `weekN/Includes/config/*.yaml`, with the original model in a
comment. To switch back, restore those names and re-run the Classroom-Setup (week 2) or
`Workspace-Setup` (weeks 3, 4). Notebooks always call the course's model *services*, so no notebook
code changes; lecture text still mentions the GPT models.

---

## Status (tested as serverless jobs)

| Notebook | Result |
|---|---|
| week1 02 Demo: Creating a Genie Agent for Unstructured Data (setup + data) | ✅ (the Genie agent itself is built in the UI) |
| week1 05 Demo: Transforming PDFs to Structured Data | ✅ |
| week2 02 Demo: Building Agent Tools on Databricks | ✅ |
| week2 05 Demo: Introduction to OpenAI Agents SDK on Databricks | ✅ |
| week2 07 Demo: Introduction to LangChain on Databricks | ✅ |
| week3 1.2 Demo: Agent Setup | ✅ |
| week3 2.2 Demo: Using MLflow Built-In Judges | ✅ |
| week3 2.4 Demo: Guideline Judges with MLflow | ✅ |
| week3 2.6 Demo: Custom Judges with MLflow | ✅ |
| week4 2 Demo: Deploying an Observable Agent (generates the `simple-agent` project) | ✅ (you deploy the app yourself in the lesson) |
| week4 4 Demo: Tracing for Production Agents | ✅ |
| week3 / week4 `Workspace-Setup` (shared model services, tools, agent, grants, week 3 app) | ✅ |
| week1 03 Lab: Lakebase Search | ➖ Needs a Lakebase project `<username>-lb-search` you create first (bills while running) |
| week1 07 Lab: Chunking PDFs and AI Search | ➖ Expects an AI Search endpoint `vs_all_demo` (Academy pre-creates it); create one or set `VS_ENDPOINT` to an existing endpoint |

Labs contain `<FILL_IN>` exercises and are not expected to run unmodified.

---

## Repository layout

```
.
├── setup.sh              # One-shot setup (tools, login, .venv, checks, .env, sync)
├── databricks.yml        # Databricks Asset Bundle: syncs the repo to the workspace
├── .env.example          # Template for .env (setup.sh fills it in)
├── .vscode/settings.json # .venv interpreter, notebook working dir, Databricks cell markers
├── scripts/set_serverless_env.py  # Pin notebooks to a serverless environment version
└── weekN/
    ├── NOTES.md, AGENDA.ipynb, Version_Info.ipynb
    ├── <lesson notebooks>.ipynb
    └── Includes/
        ├── Classroom-Setup-*.py     # %run by each lesson
        ├── Workspace-Setup.ipynb    # one-time shared setup (admin)
        ├── config/*.yaml            # per-lesson setup: catalog, schema, model services, data
        ├── _lib/                    # course setup framework (Python package)
        └── data/, tools/, ...
```

---

## How the courses were adapted to a regular workspace

| Change | Where | Why |
|---|---|---|
| `catalog.name: "workspace"` | all `Includes/config/config-*.yaml` | Outside Academy labs the setup would create a new catalog `labuser_<user>`; each course uses its own schema in the existing `workspace` catalog instead |
| GPT → Claude in `served_by` | `config/*.yaml` | GPT models need ADI Services on Azure (see [Models](#models)) |
| `dbacademy.agent_eval.*` / `dbacademy.agent_deployment.*` → `workspace.…` | week 3, 4 code, configs, app, notebooks | The shared course resources live in the `workspace` catalog |
| Marketplace share name without `dbacademy_` prefix | week 3 `Workspace-Setup`, `Repair-Resources` | Academy labs prefix shares with `dbacademy_` |
| Academy volume `/Volumes/dbacademy/rag_all/vol_all` → course PDFs | week 1 `Includes/data/rag_all/`, configs 02/05/07, notebooks 05/07 | The PDFs ship with the course; setup copies them into your own volumes |
| Scheduled Repair-Resources job optional | week 3 `Workspace-Setup` (`schedule_repair_job: false`) | Only useful for multi-user classrooms; bills each run |
| SQL warehouse `shared_warehouse` → `2x Small Serverless Warehouse` | week 4 configs 04, 06-lab, 07-lab | Academy labs pre-create `shared_warehouse`; when a lesson says *Select shared_warehouse* in the UI, pick your serverless warehouse instead |
| `CREATE CATALOG` only if the catalog is missing | week 3/4 `Workspace-Setup`, week 3 `Repair-Resources` | `CREATE CATALOG IF NOT EXISTS` fails on metastores without a storage root even when the catalog exists |
| `restartPython()` only on Databricks | `Includes/Classroom-Setup-*.py`, `_common.py` | `dbutils.library` doesn't exist over Databricks Connect |
| `.py` source notebooks → `.ipynb`, underscores in names, environment v6 pinned | all lessons, `Workspace-Setup` | Same layout as the ML repo; without a pinned version, serverless jobs fall back to an old environment |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Endpoint ... databricks-gpt-...` not found | GPT models not enabled on Azure | Use the Claude configs (default here) or enable ADI Services |
| `Table or view ... sf_airbnb_listings` / share not found | Airbnb Marketplace listing missing | Add *Airbnb Sample Data* (provider Databricks), re-run the setup cell |
| `Metastore storage root URL does not exist ... CREATE CATALOG` | `CREATE CATALOG IF NOT EXISTS` on a metastore without default storage | Fixed in this repo's `Workspace-Setup`; set `catalog.name` to an existing catalog |
| `No SQL warehouse named 'shared_warehouse'` | Academy-only warehouse | Week 4 configs use `2x Small Serverless Warehouse`; change `sql_warehouse.name` to one of yours |
| `workspace.agent_eval.agent-model` / `judge-model` not found | Shared setup not run | Run `week3/Includes/Workspace-Setup` (and week 4's) once |
| Local run: `metadata-service ... Connection refused` | VS Code extension auth helper restarted | `DATABRICKS_AUTH_TYPE=pat` in `.env` (setup.sh writes it); restart the kernel |
| `ValueError: cluster_id is required` | `restartPython()` over Databricks Connect | Guard in the setup scripts (included) |

---

## Credits

Course notebooks and setup code in `week1/`–`week4/` are from **Databricks Academy**
(© Databricks, Inc. All rights reserved), with the adaptations listed above. `setup.sh`,
configuration, scripts and notes are my own.
