"""Requirement: no LLM calls anywhere in the engine runtime.

Three independent checks: (1) no LLM SDK is imported anywhere in engine/ source, (2) the whole package
imports and a full retrieval run completes in a fresh interpreter where LLM SDKs cannot be imported,
(3) the engine's declared dependencies contain no LLM SDK.
"""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = ("anthropic", "openai", "google.generativeai", "google.genai", "vertexai", "cohere", "mistralai",
             "litellm", "langchain", "llama_index", "transformers", "ollama", "groq", "together", "replicate")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_engine_source_imports_no_llm_sdk():
    offenders = {}
    for py in (ROOT / "engine").rglob("*.py"):
        bad = {n for n in _imports(py) if any(n == f or n.startswith(f + ".") for f in FORBIDDEN)}
        if bad:
            offenders[str(py.relative_to(ROOT))] = sorted(bad)
    assert offenders == {}


def test_engine_does_not_import_the_prototype():
    for py in (ROOT / "engine").rglob("*.py"):
        assert not any(n == "personalizer" or n.startswith("personalizer.") for n in _imports(py)), py


def test_engine_requirements_contain_no_llm_sdk():
    lines = (ROOT / "requirements-engine.txt").read_text(encoding="utf-8").lower().splitlines()
    pkgs = {ln.split("==")[0].split(">=")[0].strip() for ln in lines if ln.strip() and not ln.startswith("#")}
    assert not pkgs & {f.split(".")[0] for f in FORBIDDEN}


BLOCKED_RUN = r"""
import sys, importlib.abc, pkgutil, importlib, asyncio
FORBIDDEN = %r
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if any(name == f or name.startswith(f + ".") for f in FORBIDDEN):
            raise ImportError("LLM SDK import blocked: " + name)
sys.meta_path.insert(0, Block())
sys.path.insert(0, %r)
import engine
for m in pkgutil.walk_packages(engine.__path__, "engine."):
    if m.name != "engine.__main__":
        importlib.import_module(m.name)
import httpx
from datetime import date
from engine.config import EngineConfig
from engine.store.db import connect
from engine.retrieval import Retriever, Lead
from engine.fetch.client import StaticResolver
hosts = []
def handler(req):
    hosts.append(req.url.host)
    if req.url.path == "/robots.txt":
        return httpx.Response(404)
    return httpx.Response(200, html="<html><body><article><p>" + "Plain public text. " * 30 + "</p></article></body></html>")
cfg = EngineConfig(contact="t@example.org"); cfg.fetch.min_host_interval = 0
conn = connect(":memory:")
rid = asyncio.run(Retriever(conn, cfg, transport=httpx.MockTransport(handler), resolver=StaticResolver(),
                            today=date(2026, 10, 17)).run([Lead("https://a.example/x")]))
status = conn.execute("select status from runs").fetchone()[0]
loaded = [m for m in sys.modules if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)]
llm_hosts = [h for h in hosts if any(k in h for k in ("anthropic", "openai", "googleapis", "cohere", "mistral"))]
print(status, loaded, llm_hosts, sorted(set(hosts)))
assert status == "completed" and not loaded and not llm_hosts
"""


def test_full_run_with_llm_sdks_blocked():
    proc = subprocess.run([sys.executable, "-c", BLOCKED_RUN % (FORBIDDEN, str(ROOT))],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip() == "completed [] [] ['a.example']"
