import json
import logging
import os
import re
import time

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("MODEL", "llama3.2:3b")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("assistant")

app = FastAPI(title="DevOps Assistant")

SYSTEM = ("You are a DevOps assistant. Return only the file content, "
          "with no explanations and no markdown fences.")


class Request(BaseModel):
    description: str


def strip_fences(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("```")).strip()


def issue(rule: str, severity: str, message: str) -> dict:
    return {"rule": rule, "severity": severity, "message": message}


def lint_dockerfile(text: str) -> list:
    issues = []
    for img in re.findall(r"^FROM\s+(\S+)", text, re.M | re.I):
        if img.lower() != "scratch" and (":" not in img or img.endswith(":latest")):
            issues.append(issue("unpinned-image", "warning",
                                f"Image '{img}' is not pinned to a version"))
    users = re.findall(r"^USER\s+(\S+)", text, re.M | re.I)
    if not users or users[-1] in ("root", "0"):
        issues.append(issue("runs-as-root", "warning",
                            "The container runs as root: add a non-root USER"))
    if not re.search(r"^HEALTHCHECK", text, re.M | re.I):
        issues.append(issue("no-healthcheck", "info", "No HEALTHCHECK instruction"))
    return issues


def lint_k8s(text: str) -> list:
    issues = []
    if "limits:" not in text:
        issues.append(issue("no-resource-limits", "warning", "No resource limits defined"))
    if "readinessProbe" not in text:
        issues.append(issue("no-readiness-probe", "warning", "No readinessProbe defined"))
    if re.search(r"image:\s*\S+:latest", text) or re.search(r"image:\s*[^\s:]+\s*$", text, re.M):
        issues.append(issue("unpinned-image", "warning", "Image is not pinned to a version"))
    return issues


def ask(prompt: str) -> str:
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": MODEL, "system": SYSTEM, "prompt": prompt, "stream": False},
            timeout=300,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        log.error(json.dumps({"event": "ollama_error", "error": str(e)}))
        raise HTTPException(status_code=502, detail=f"Ollama error: {e}")
    return strip_fences(r.json()["response"])


def generate(kind: str, prompt: str, linter) -> dict:
    start = time.perf_counter()
    content = ask(prompt)
    issues = linter(content)
    duration = round(time.perf_counter() - start, 2)
    log.info(json.dumps({"event": "generate", "kind": kind, "model": MODEL,
                         "duration_s": duration, "issues": len(issues),
                         "warnings": sum(i["severity"] == "warning" for i in issues)}))
    return {"content": content, "issues": issues,
            "valid": not any(i["severity"] == "warning" for i in issues),
            "duration_s": duration}


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL}


@app.post("/generate/dockerfile")
def dockerfile(req: Request):
    prompt = (f"Write a production-ready Dockerfile for: {req.description}. "
              "Use a pinned version tag (never latest), run as a non-root user "
              "with a USER instruction, and copy dependency files before the source code.")
    return generate("dockerfile", prompt, lambda t: lint_dockerfile(t) + lint_eol(t))


@app.post("/generate/k8s")
def k8s(req: Request):
    prompt = (f"Write a Kubernetes Deployment and Service manifest (YAML) for: {req.description}. "
              "Include resource requests and limits, a readinessProbe, and a pinned image tag.")
    return generate("k8s", prompt, lint_k8s)


EOL_NODE = {"10", "12", "14", "16", "18", "20"}
EOL_PYTHON = {"2", "3.6", "3.7", "3.8", "3.9"}


def lint_eol(text: str) -> list:
    issues = []
    for img in re.findall(r"^FROM\s+(\S+)", text, re.M | re.I):
        m = re.match(r"node:(\d+)", img)
        if m and m.group(1) in EOL_NODE:
            issues.append(issue("eol-image", "warning",
                                f"Node {m.group(1)} is end-of-life: use a supported LTS release"))
        m = re.match(r"python:(\d+(?:\.\d+)?)", img)
        if m and m.group(1) in EOL_PYTHON:
            issues.append(issue("eol-image", "warning",
                                f"Python {m.group(1)} is end-of-life: use a supported release"))
    if re.search(r"npm (install|ci)\b.*--force", text):
        issues.append(issue("npm-force", "warning",
                            "npm --force hides dependency conflicts: avoid it"))
    return issues
