"""CLI entrypoints (spec T1.1 / S1.1.3): run, run --dry-run, discover-only,
resume-hitl <ticket_id>.

Usage (via Makefile or directly):
    python -m src.cli.main run
    python -m src.cli.main run --dry-run
    python -m src.cli.main discover-only
    python -m src.cli.main resume-hitl <ticket_id>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from src.config.profile import load_profile
from src.config.secrets import load_secrets
from src.config.settings import load_settings
from src.contracts.exceptions import ConfigError, JuneError, SecretsError
from src.observability.logging import configure_logging, log_event
from src.observability.run_id import new_run_id
from src.orchestrator.pipeline_stub import run_empty_pipeline
from src.tracker.repository import InMemoryTrackerRepository

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
DEFAULT_PROFILE_PATH = Path("config/profile.yaml")


@click.group()
def cli() -> None:
    """June - autonomous JobRight auto-apply pipeline CLI."""


def _bootstrap(*, require_secrets: bool):
    """Shared startup sequence: run_id, config, secrets, logger, tracker.

    Fails fast (exit code 1) with a clear message on any ConfigError /
    SecretsError, before any browser/network action - per spec T1.2.3.
    """
    run_id = new_run_id()
    try:
        settings = load_settings(DEFAULT_SETTINGS_PATH)
        profile = load_profile(DEFAULT_PROFILE_PATH)
        secrets = load_secrets(require=require_secrets)
    except (ConfigError, SecretsError) as exc:
        click.echo(f"startup failed: {exc}", err=True)
        sys.exit(1)

    logger = configure_logging(run_id, secret_values=secrets.redaction_values())
    tracker = InMemoryTrackerRepository()
    return run_id, settings, profile, secrets, tracker, logger


@cli.command()
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Run through selection/resume preview; never submit.",
)
def run(dry_run: bool) -> None:
    """Run the full pipeline (or a dry-run preview) end-to-end.

    Epic 1 scaffold: with no discovery/selection/executor implementation
    yet, this runs an *empty* pipeline against fake/in-memory data so
    every other epic can build against a working entrypoint immediately.
    """
    run_id, settings, profile, _secrets, tracker, logger = _bootstrap(require_secrets=not dry_run)
    try:
        summary = run_empty_pipeline(
            run_id=run_id,
            logger=logger,
            settings=settings,
            profile=profile,
            tracker=tracker,
            dry_run=dry_run,
        )
    except JuneError as exc:
        log_event(logger, "pipeline_failed", level=40, error=str(exc))
        click.echo(f"pipeline failed: {exc}", err=True)
        sys.exit(1)

    click.echo(json.dumps(summary, indent=2, default=str))


@cli.command(name="discover-only")
def discover_only() -> None:
    """Run only the discovery stage (Epic 3 not yet implemented - stub)."""
    run_id, _settings, _profile, _secrets, tracker, logger = _bootstrap(require_secrets=False)
    log_event(logger, "stage_skipped", stage="discovery", reason="not_yet_implemented")
    summary = {
        "run_id": run_id,
        "stage": "discovery",
        "status": "not_yet_implemented",
        "job_counts": {"discovered": len(tracker.get_jobs("discovered"))},
    }
    click.echo(json.dumps(summary, indent=2, default=str))


@cli.command(name="resume-hitl")
@click.argument("ticket_id")
def resume_hitl(ticket_id: str) -> None:
    """Resume a paused HITL ticket (Epic 7 not yet implemented - stub).

    Epic 1 ships a minimal ticket lookup against the in-memory tracker fake
    so the CLI surface exists; Epic 7 owns the real ticket store, resolve
    workflow, and pause/resume semantics (spec T7.1-T7.3).
    """
    run_id, _settings, _profile, _secrets, tracker, logger = _bootstrap(require_secrets=False)
    ticket = tracker.get_ticket(ticket_id)
    if ticket is None:
        log_event(logger, "hitl_ticket_not_found", level=30, ticket_id=ticket_id)
        click.echo(
            json.dumps(
                {
                    "run_id": run_id,
                    "ticket_id": ticket_id,
                    "status": "not_found",
                    "note": "Epic 7 HITL ticket store is not yet implemented; "
                    "no tickets exist in this scaffold.",
                },
                indent=2,
            )
        )
        return
    log_event(logger, "hitl_ticket_resume_requested", ticket_id=ticket_id)
    click.echo(json.dumps({"run_id": run_id, "ticket": ticket.model_dump()}, indent=2, default=str))


if __name__ == "__main__":
    cli()
