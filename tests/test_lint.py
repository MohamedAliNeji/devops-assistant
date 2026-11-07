import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import lint_dockerfile, lint_eol, lint_k8s  # noqa: E402


def rules(issues):
    return {i["rule"] for i in issues}


def test_unpinned_image_is_flagged():
    assert "unpinned-image" in rules(lint_dockerfile("FROM node:latest\nUSER node"))


def test_pinned_image_is_accepted():
    assert "unpinned-image" not in rules(lint_dockerfile("FROM node:22-slim\nUSER node"))


def test_missing_user_is_flagged():
    assert "runs-as-root" in rules(lint_dockerfile("FROM node:22-slim\n"))


def test_non_root_user_is_accepted():
    assert "runs-as-root" not in rules(lint_dockerfile("FROM node:22-slim\nUSER node"))


def test_eol_node_is_flagged():
    assert "eol-image" in rules(lint_eol("FROM node:14-slim"))


def test_npm_force_is_flagged():
    assert "npm-force" in rules(lint_eol("RUN npm install --force"))


def test_k8s_without_limits_and_probe():
    found = rules(lint_k8s("kind: Deployment"))
    assert {"no-resource-limits", "no-readiness-probe"} <= found
