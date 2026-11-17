# _lib/slack_alert.py
#
# Slack alerts for the admin notebooks (Workspace-Setup, Repair-Resources).
# An alert is sent only when something is broken and needs fixing.
#
# The Slack incoming webhook URL is read from the Databricks secret
# SLACK_SECRET_SCOPE / SLACK_SECRET_KEY. It is never stored in course content.
# send_slack_alert() never raises: a missing secret or a Slack error only
# prints a warning, so alerting can never change the result of a run.

import json

import requests

SLACK_SECRET_SCOPE = "lab-alerts"
SLACK_SECRET_KEY = "slack-webhook"


def _first_line(e):
    """First line of an exception message (secret errors include a long Java stack trace)."""
    text = str(e).strip()
    return text.splitlines()[0] if text else type(e).__name__


def run_url(ws, dbutils):
    """Link to the current job run, or the workspace URL if not running as a job."""
    host = ws.config.host.rstrip("/")
    try:
        ctx = json.loads(dbutils.notebook.entry_point.getDbutils().notebook().getContext().safeToJson())["attributes"]
        job_id = ctx.get("jobId")
        run_id = ctx.get("multitaskParentRunId") or ctx.get("currentRunId") or ctx.get("rootRunId")
        if job_id and run_id:
            return f"{host}/jobs/{job_id}/runs/{run_id}"
    except Exception:
        pass
    return host


def build_message(ws, dbutils, title, course, sections):
    """Slack mrkdwn message. sections is a list of (heading, [item, ...]); empty sections are left out."""
    lines = [
        f":x: *{title}*",
        f"*Course:* {course}",
        f"*Workspace:* {ws.get_workspace_id()} ({ws.config.host})",
    ]
    for heading, items in sections:
        if items:
            lines.append(f"*{heading}:*")
            lines += [f"• {str(item)[:300]}" for item in items]
    lines.append(f"<{run_url(ws, dbutils)}|Open job run>")
    return "\n".join(lines)


def send_slack_alert(ws, dbutils, title, course, sections):
    """Print the alert and post it to Slack. Never raises."""
    try:
        text = build_message(ws, dbutils, title, course, sections)
    except Exception as e:
        print(f"WARNING: could not build Slack alert: {_first_line(e)}")
        return
    print("\n--- Slack alert message ---\n" + text + "\n---------------------------")

    try:
        webhook_url = dbutils.secrets.get(SLACK_SECRET_SCOPE, SLACK_SECRET_KEY)
    except Exception as e:
        print(f"WARNING: Slack alert skipped, secret {SLACK_SECRET_SCOPE}/{SLACK_SECRET_KEY} not available: {_first_line(e)}")
        return
    # Report errors without the exception text: requests includes the webhook URL in it.
    try:
        resp = requests.post(webhook_url, json={"text": text}, timeout=15)
    except Exception as e:
        print(f"WARNING: Slack alert failed: {type(e).__name__}")
        return
    if resp.ok:
        print("Slack alert sent.")
    else:
        print(f"WARNING: Slack alert failed: HTTP {resp.status_code} {resp.text[:200]}")
