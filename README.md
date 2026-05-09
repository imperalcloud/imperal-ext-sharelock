# imperal-ext-sharelock

Forensic case analysis — manage cases, upload documents, run AI-driven incremental analysis pipelines, generate prosecution and inspection reports, and review forensic findings.

Imperal-owned extension for [Webbee 🐝](https://docs.imperal.io), the agent of [Imperal Cloud](https://imperal.io) — the world's first AI Cloud OS.

| Field | Value |
|---|---|
| **App ID** | `sharelock-v2` |
| **Current version** | v3.0.0 |
| **Status** | Production |
| **License** | Proprietary (Imperal, Inc.) |
| **SDK** | `imperal-sdk >= 4.1.4` |

## Deploy flow

This git repo is the **source of truth**. The deployed copy on `whm-ai-worker:/opt/extensions/sharelock-v2/` is downstream of Dev Portal uploads — do not edit the deployed copy directly.

1. Edit code locally in this folder.
2. Commit + push to `main`.
3. Open <https://panel.imperal.io/developer> and upload a tarball of the current commit.
4. Dev Portal validates against the federal extension contract (V14–V22 + V24) and rolls out to production workers.

## Layout

| Path | Role |
|---|---|
| `app.py`, `main.py` | Extension declaration + entry |
| `chat.py`, `handlers.py`, `handlers_analysis.py` | `@chat.function` surface |
| `panels.py`, `panels_case.py`, `panels_analysis.py`, `panels_gap_review.py`, `panels_graph.py` | UI panels |
| `case_resolver.py`, `intelligence_*.py` | Case resolution + AI analysis pipeline |
| `cache_models.py`, `validation.py` | Pydantic cache models + validators |
| `prompts/`, `system_prompt.txt` | LLM prompts |
| `tests/` | Pytest suite |

## Federal contract

Must satisfy V14–V22 + V24 to publish via Dev Portal. See <https://docs.imperal.io/en/sdk/validators-reference/>.

## Testing

```bash
cd /Users/val-mac/Nextcloud/MCP-Configs/imperal-ext-sharelock
pytest tests/
```
