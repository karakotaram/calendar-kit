"""Base agent class for all monitoring/quality agents"""
import json
import logging
import os
import subprocess
import time
from abc import ABC, abstractmethod
from typing import Optional


logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract base class for all agents, mirroring BaseScraper pattern"""

    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"agent.{name}")
        self._anthropic_client = None

    @property
    def anthropic_client(self):
        """Lazy-load the Anthropic client; None when ANTHROPIC_API_KEY is unset.

        Every LLM step in the agents is optional. Without a key they log that
        they were skipped and the run carries on, so treat "skipped" in a log
        as a finding, not as success.
        """
        if self._anthropic_client is None and os.environ.get("ANTHROPIC_API_KEY"):
            try:
                import anthropic
                self._anthropic_client = anthropic.Anthropic(timeout=120.0)
            except ImportError:
                self.logger.warning("anthropic package not installed")
        return self._anthropic_client

    def llm_complete(self, prompt: str, system: str = "") -> Optional[str]:
        """One Claude call. Returns the reply text, or None if unavailable,
        declined, or failed - callers must work without it."""
        client = self.anthropic_client
        if not client:
            self.logger.info("Claude not available (ANTHROPIC_API_KEY unset); skipping LLM step")
            return None
        import anthropic

        kwargs = {
            "model": os.environ.get("AGENT_MODEL", "claude-opus-5-5"),
            "max_tokens": 16000,
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
            "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        try:
            response = client.beta.messages.create(**kwargs)
        except anthropic.RateLimitError:
            self.logger.warning("Claude rate limited; skipping LLM step")
            return None
        except anthropic.APIStatusError as e:
            self.logger.error(f"Claude request failed ({e.status_code}): {e.message}")
            return None
        except anthropic.APIConnectionError as e:
            self.logger.warning(f"Could not reach Claude: {e}")
            return None
        if response.stop_reason == "refusal":
            self.logger.warning("Claude declined the request; skipping LLM step")
            return None
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text or None

    def load_events(self) -> list:
        """Load events from data/events.json"""
        events_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data", "events.json"
        )
        try:
            with open(events_path, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            self.logger.warning(f"Events file not found: {events_path}")
            return []

    def save_report(self, data: dict, filename: str):
        """Save report to data/agent_reports/"""
        reports_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data", "agent_reports"
        )
        os.makedirs(reports_dir, exist_ok=True)
        filepath = os.path.join(reports_dir, filename)
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)
        self.logger.info(f"Report saved to {filepath}")

    def create_github_issue(self, title: str, body: str, assignee: str = None) -> bool:
        """Create GitHub issue via gh CLI, deduping by title prefix"""
        # Check for gh CLI
        try:
            subprocess.run(["gh", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.logger.warning("gh CLI not available, skipping issue creation")
            return False

        # Check for existing open issue with same title prefix
        title_prefix = title.split(" - ")[0] if " - " in title else title[:50]
        try:
            result = subprocess.run(
                ["gh", "issue", "list", "--state", "open", "--search", title_prefix, "--json", "title"],
                capture_output=True, text=True, check=True
            )
            existing = json.loads(result.stdout) if result.stdout.strip() else []
            for issue in existing:
                if issue.get("title", "").startswith(title_prefix):
                    self.logger.info(f"Open issue already exists: {issue['title']}")
                    return False
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            pass  # Proceed with creation if check fails

        # Create issue
        try:
            cmd = ["gh", "issue", "create", "--title", title, "--body", body]
            if assignee:
                cmd.extend(["--assignee", assignee])
            subprocess.run(
                cmd,
                capture_output=True, text=True, check=True
            )
            self.logger.info(f"Created GitHub issue: {title}")
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to create issue: {e.stderr}")
            return False

    @abstractmethod
    def execute(self) -> dict:
        """Run the agent logic. Must return a dict with at least a 'status' key."""
        pass

    def run(self) -> dict:
        """Execute agent with timing, logging, and error handling"""
        self.logger.info(f"Starting agent: {self.name}")
        start = time.time()
        try:
            result = self.execute()
            elapsed = time.time() - start
            self.logger.info(f"Agent {self.name} completed in {elapsed:.1f}s")
            result["elapsed_seconds"] = round(elapsed, 1)
            return result
        except Exception as e:
            elapsed = time.time() - start
            self.logger.error(f"Agent {self.name} failed after {elapsed:.1f}s: {e}")
            return {"status": "error", "error": str(e), "elapsed_seconds": round(elapsed, 1)}
