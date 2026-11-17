#!/usr/bin/env bash
# One-shot setup: tools, Databricks login, local .venv, workspace checks, .env, sync.
# Safe to re-run. Usage:
#   ./setup.sh                       # interactive (asks for workspace URL on first run)
#   DATABRICKS_HOST=https://adb-....azuredatabricks.net ./setup.sh
set -euo pipefail
cd "$(dirname "$0")"

PROFILE="azure-ml"          # OAuth profile used by CLI, bundle and VS Code extension
PAT_PROFILE="azure-ml-pat"  # token profile used by local notebook runs
export PATH="$HOME/.local/bin:$PATH"

say()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- 1. tools
say "Checking tools"
if ! command -v databricks >/dev/null; then
  echo "Installing Databricks CLI into ~/.local/bin (no sudo)"
  mkdir -p "$HOME/.local/bin"; tmp=$(mktemp -d)
  V=$(curl -fsSL https://api.github.com/repos/databricks/cli/releases/latest | grep -m1 '"tag_name"' | cut -d'"' -f4 | sed 's/^v//')
  case "$(uname -m)" in x86_64) ARCH=amd64;; aarch64|arm64) ARCH=arm64;; *) warn "unsupported CPU"; exit 1;; esac
  OS=$(uname -s | tr '[:upper:]' '[:lower:]')
  F="databricks_cli_${V}_${OS}_${ARCH}.zip"
  curl -fsSL -o "$tmp/$F" "https://github.com/databricks/cli/releases/download/v${V}/$F"
  curl -fsSL -o "$tmp/sums" "https://github.com/databricks/cli/releases/download/v${V}/databricks_cli_${V}_SHA256SUMS"
  (cd "$tmp" && grep " $F\$" sums | sha256sum -c -)
  unzip -q -o "$tmp/$F" -d "$tmp/out" && install -m 755 "$tmp/out/databricks" "$HOME/.local/bin/databricks"
  rm -rf "$tmp"
fi
if ! command -v uv >/dev/null; then
  echo "Installing uv into ~/.local/bin"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
fi
databricks -v; uv --version

# ---------------------------------------------------------------- 2. login
say "Databricks authentication (profile: $PROFILE)"
if ! databricks current-user me -p "$PROFILE" >/dev/null 2>&1; then
  HOST="${DATABRICKS_HOST:-}"
  [ -z "$HOST" ] && read -rp "Workspace URL (e.g. https://adb-123.4.azuredatabricks.net): " HOST
  databricks auth login --host "$HOST" --profile "$PROFILE"
fi
USER_EMAIL=$(databricks current-user me -p "$PROFILE" -o json | python3 -c 'import json,sys;print(json.load(sys.stdin)["userName"])')
HOST=$(databricks auth describe -p "$PROFILE" -o json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("details",{}).get("host") or d.get("host"))')
echo "Logged in as $USER_EMAIL on $HOST"

# ---------------------------------------------------------------- 3. local venv
say "Local Python environment (.venv, Python 3.12 = serverless env v6)"
[ -d .venv ] || uv venv .venv --python 3.12
uv pip install --python .venv -q pip nbformat ipykernel pyyaml databricks-connect \
  "mlflow==3.16.1" "databricks-sdk==0.145.0" "databricks-agents==1.12.0" "databricks-openai==0.17.1" \
  "databricks-langchain==0.20.0" "databricks-mcp==0.9.2" "databricks-ai-search==0.78" \
  "openai-agents==0.22.3" "langgraph==1.2.12" "dspy==3.4.0" "unitycatalog-ai[databricks]==0.3.2" \
  "psycopg[binary]==3.3.4" nest_asyncio reportlab
echo "Installed: $(.venv/bin/python -c 'from importlib.metadata import version as v;print("databricks-connect",v("databricks-connect"),"| mlflow",v("mlflow"))')"

# ---------------------------------------------------------------- 4. workspace checks
say "Workspace checks"
CATALOGS=$(databricks catalogs list -p "$PROFILE" -o json | python3 -c 'import json,sys;[print(c["name"]) for c in json.load(sys.stdin)]')
grep -qx workspace <<<"$CATALOGS" && echo "  [ok]      catalog 'workspace' (course schemas are created inside it)" \
  || warn "catalog 'workspace' not found: set catalog.name in weekN/Includes/config/*.yaml to an existing catalog"
if grep -qx databricks_airbnb_sample_data <<<"$CATALOGS"; then
  echo "  [ok]      databricks_airbnb_sample_data (Marketplace: 'Airbnb Sample Data' by Databricks)"
else
  warn "missing databricks_airbnb_sample_data: add 'Airbnb Sample Data' (provider: Databricks) from Marketplace (weeks 2 and 4)"
fi
# Foundation models referenced by the course configs must be served in this workspace
MODELS=$(grep -rhoE 'served_by: *"[^"]+"' week*/Includes/config/*.yaml | grep -oE 'databricks-[a-z0-9-]+' | sort -u)
ENDPOINTS=$(databricks serving-endpoints list -p "$PROFILE" -o json | python3 -c 'import json,sys;[print(e["name"]) for e in json.load(sys.stdin)]')
for m in $MODELS; do
  grep -qx "$m" <<<"$ENDPOINTS" && echo "  [ok]      model $m" || warn "model $m is not served in this workspace (see README: Models)"
done

# ---------------------------------------------------------------- 5. PAT profile + .env
say "Local run configuration"
if ! databricks current-user me -p "$PAT_PROFILE" >/dev/null 2>&1; then
  read -rp "Create a 90-day personal access token for local runs? [Y/n] " ans
  if [[ ! $ans =~ ^[Nn] ]]; then
    TOKEN=$(databricks tokens create --comment vscode-genai-course --lifetime-seconds 7776000 -p "$PROFILE" -o json \
      | python3 -c 'import json,sys;print(json.load(sys.stdin)["token_value"])')
    printf '\n[%s]\nhost  = %s\ntoken = %s\n' "$PAT_PROFILE" "$HOST" "$TOKEN" >> "$HOME/.databrickscfg"
    unset TOKEN; chmod 600 "$HOME/.databrickscfg"; echo "Saved profile $PAT_PROFILE"
  fi
fi
LOCAL_PROFILE=$PAT_PROFILE
databricks current-user me -p "$PAT_PROFILE" >/dev/null 2>&1 || LOCAL_PROFILE=$PROFILE
sed -e "s#^DATABRICKS_CONFIG_PROFILE=.*#DATABRICKS_CONFIG_PROFILE=$LOCAL_PROFILE#" \
    -e "s#<your-email>#$USER_EMAIL#" .env.example > .env
[ "$LOCAL_PROFILE" = "$PAT_PROFILE" ] || sed -i '/^DATABRICKS_AUTH_TYPE=/d' .env
echo "Wrote .env (profile $LOCAL_PROFILE)"

# ---------------------------------------------------------------- 6. sync
say "Syncing courses to the workspace"
databricks bundle validate >/dev/null
databricks bundle sync --full | tail -1
echo "Files: /Workspace/Users/$USER_EMAIL/.bundle/genai-course/dev/files"

say "Done. Open this folder in VS Code, select the .venv interpreter, and run notebooks
    with right-click -> 'Run File as Workflow' (or open them in the Databricks UI)."
