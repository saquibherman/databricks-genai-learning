# _lib/slack_alert.py
#
# Slack alerts for the admin notebooks (Workspace-Setup, Repair-Resources).
# An alert is sent only when something is broken and needs fixing.
#
# Message layout follows the #training-automation-alerts style:
#   header      -> "<Job> Failure Detected"
#   summary     -> one line + "Checked at <UTC time>"
#   failure row -> course | workspace URL | job: `FAILED` | run link
#   details     -> failed step / missing resources / errors
#
# The Slack incoming webhook URL is read from the Databricks secret
# SLACK_SECRET_SCOPE / SLACK_SECRET_KEY. It is never stored in course content.
# send_slack_alert() never raises: a missing secret or a Slack error only
# prints a warning, so alerting can never change the result of a run.

import json
from datetime import datetime, timezone

import requests

SLACK_SECRET_SCOPE = "lab-alerts"
SLACK_SECRET_KEY = "slack-webhook"

MAX_ITEMS_PER_SECTION = 15   # keep messages readable; the job run has the full list
MAX_ITEM_CHARS = 300
MAX_SECTION_CHARS = 2900     # Slack limit for a section block is 3000


def _first_line(e):
    """First line of an exception message (secret errors include a long Java stack trace)."""
    text = str(e).strip()
    return text.splitlines()[0] if text else type(e).__name__


def run_ids(dbutils):
    """(job_id, run_id) of the current job run, or (None, None) if not running as a job."""
    try:
        ctx = json.loads(dbutils.notebook.entry_point.getDbutils().notebook().getContext().safeToJson())["attributes"]
        job_id = ctx.get("jobId")
        run_id = ctx.get("multitaskParentRunId") or ctx.get("currentRunId") or ctx.get("rootRunId")
        if job_id and run_id:
            return str(job_id), str(run_id)
    except Exception:
        pass
    return None, None


def run_url(ws, dbutils):
    """Link to the current job run, or None if not running as a job."""
    job_id, run_id = run_ids(dbutils)
    if job_id and run_id:
        return f"{ws.config.host.rstrip('/')}/jobs/{job_id}/runs/{run_id}"
    return None


def _section_text(heading, items):
    shown = [f"• {str(i)[:MAX_ITEM_CHARS]}" for i in items[:MAX_ITEMS_PER_SECTION]]
    if len(items) > MAX_ITEMS_PER_SECTION:
        shown.append(f"• _…and {len(items) - MAX_ITEMS_PER_SECTION} more (see the job run)_")
    return (f"*{heading}:*\n" + "\n".join(shown))[:MAX_SECTION_CHARS]


def build_message(ws, dbutils, job_name, summary, course, sections):
    """Return (fallback_text, blocks) for a Slack incoming webhook.

    sections is a list of (heading, [item, ...]); empty sections are left out.
    """
    host = ws.config.host.rstrip("/")
    url = run_url(ws, dbutils)
    checked = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    headline = f":rotating_light: {job_name} Failure Detected"
    failure_row = (
        f"• *{course}*  |  <{host}|{host}>  |  {job_name}: `FAILED`"
        + (f"  |  <{url}|Open job run>" if url else "")
    )
    detail_texts = [_section_text(h, items) for h, items in sections if items]

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": headline[:150], "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{summary}\n_Checked at {checked}_"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": failure_row}},
        *({"type": "section", "text": {"type": "mrkdwn", "text": t}} for t in detail_texts),
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"Workspace ID {ws.get_workspace_id()}"}
        ]},
    ]
    # Plain-text version: used for notifications and printed in the job output.
    text = "\n".join([f"*{headline}*", summary, f"_Checked at {checked}_", "", failure_row, *detail_texts])
    return text, blocks


def send_slack_alert(ws, dbutils, job_name, summary, course, sections):
    """Print the alert and post it to Slack. Never raises.

    Returns a delivery status: "sent", "skipped_no_secret", "build_failed", or "failed_<reason>".
    """
    try:
        text, blocks = build_message(ws, dbutils, job_name, summary, course, sections)
    except Exception as e:
        print(f"WARNING: could not build Slack alert: {_first_line(e)}")
        return "build_failed"
    print("\n--- Slack alert message ---\n" + text + "\n---------------------------")

    try:
        webhook_url = dbutils.secrets.get(SLACK_SECRET_SCOPE, SLACK_SECRET_KEY)
    except Exception as e:
        print(f"WARNING: Slack alert skipped, secret {SLACK_SECRET_SCOPE}/{SLACK_SECRET_KEY} not available: {_first_line(e)}")
        return "skipped_no_secret"

    # Report errors without the exception text: requests includes the webhook URL in it.
    try:
        resp = requests.post(webhook_url, json={"text": text, "blocks": blocks}, timeout=15)
    except Exception as e:
        print(f"WARNING: Slack alert failed: {type(e).__name__}")
        return f"failed_{type(e).__name__}"
    if resp.ok:
        print("Slack alert sent.")
        return "sent"
    print(f"WARNING: Slack alert failed: HTTP {resp.status_code} {resp.text[:200]}")
    return f"failed_http_{resp.status_code}"
