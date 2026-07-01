"""Status der lokalen KI-Bots (Bot 1 Release-Analyse, Bot 2 Fix-Vorschlag) via GitHub-API.

Die Bots laufen als GitHub-Actions-Workflows auf einem self-hosted Windows-Runner
mit lokalem Ollama — nicht auf diesem Server. Der Status wird daher über die
GitHub-API abgefragt, nicht per direktem Netzwerkzugriff.
"""
import json
import urllib.error
import urllib.request

from constants import GITHUB_REPO, GITHUB_STATUS_TOKEN

_API = "https://api.github.com"
_WORKFLOWS = {
    "bot1": "bot1-release-analysis.yml",
    "bot2": "bot2-fix-suggestion.yml",
}


def _get(path: str):
    req = urllib.request.Request(
        f"{_API}{path}",
        headers={
            "Authorization": f"Bearer {GITHUB_STATUS_TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "AC-Server-Dashboard",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def get_bot_status() -> dict:
    """Runner-Status + letzter Lauf pro Bot-Workflow. {"configured": False} ohne Token."""
    if not GITHUB_STATUS_TOKEN:
        return {"configured": False}

    result = {"configured": True, "runner": None, "runs": {}}

    # Erfordert Admin-Rechte auf dem Repo — schlägt mit reinem repo-Scope evtl. fehl,
    # dann bleibt runner=None und die UI zeigt "unbekannt".
    runners = _get(f"/repos/{GITHUB_REPO}/actions/runners")
    if runners and runners.get("runners"):
        r = runners["runners"][0]
        result["runner"] = {"name": r.get("name"), "status": r.get("status"), "busy": r.get("busy", False)}

    for key, workflow_file in _WORKFLOWS.items():
        data = _get(f"/repos/{GITHUB_REPO}/actions/workflows/{workflow_file}/runs?per_page=1")
        runs = data.get("workflow_runs") if data else None
        result["runs"][key] = {
            "status":     runs[0].get("status"),
            "conclusion": runs[0].get("conclusion"),
            "created_at": runs[0].get("created_at"),
            "url":        runs[0].get("html_url"),
        } if runs else None

    return result
