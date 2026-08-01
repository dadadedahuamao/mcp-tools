# Unified Nested Environment Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make DB, Loki, K8s, and Redis read one shared `env.yaml` whose tool settings live under `db`, `loki`, `k8s`, and `redis` blocks.

**Architecture:** Keep each MCP’s existing internal configuration dataclasses and clients; change only the shared-environment adapter to read its own nested block. Old flat keys are intentionally ignored and missing nested blocks produce configuration errors.

**Tech Stack:** Python 3.12, PyYAML, pytest, existing MCP dependencies.

## Global Constraints

- Every MCP reads only its own nested environment block.
- Old `db_*`, `loki_*`, and `k8s_*` keys are not compatibility inputs.
- Preserve existing validation, read-only boundaries, aliases, and secret redaction.
- Migrate the actual shared `E:\Project\xcmg\xcmg_uat\mom-backend\env.yaml` without changing values.

### Task 1: Update DB nested parser and tests

**Files:** `db-mcp/db_mcp/config.py`, `db-mcp/tests/test_config.py`

- [ ] Add a test fixture using `env[].db` with `connect_name/type/host/port/name/user/password/schema`; assert one `DatabaseSource` is loaded.
- [ ] Add a test with only legacy `db_connect_name` and assert `DatabaseConfigurationError`.
- [ ] Change `_datasources_from_unified_env` to read `item["db"]`, map nested names to the existing internal names, and retain dialect/password/schema validation.
- [ ] Run `db-mcp` config tests and commit.

### Task 2: Update Loki nested parser and tests

**Files:** `loki-mcp/loki_mcp/config.py`, `loki-mcp/tests/test_config.py`

- [ ] Replace fixtures with `env[].loki` and add a legacy-only rejection test.
- [ ] Read `url/datasource_uid/username/password/query` from `loki`, preserving alias resolution and required-field errors.
- [ ] Run Loki tests and commit.

### Task 3: Update K8s nested parser and tests

**Files:** `k8s-mcp/k8s_mcp/config.py`, `k8s-mcp/tests/test_config.py`

- [ ] Replace environment fixture fields with `env[].k8s.namespace/kubeconfig/context` and add legacy-only rejection coverage.
- [ ] Register only environments containing `k8s.kubeconfig`; validate filename within the configured kubeconfig directory and preserve context behavior.
- [ ] Run K8s tests and commit.

### Task 4: Migrate examples, actual shared config, and documentation

**Files:** `db-mcp/config/env.yaml.example`, `loki-mcp/config/env.yaml.example`, `k8s-mcp/config/env.yaml.example`, corresponding READMEs, and `E:\Project\xcmg\xcmg_uat\mom-backend\env.yaml`.

- [ ] Convert each example to one shared nested document containing all tool blocks.
- [ ] Convert the actual shared YAML with the same values; preserve passwords and paths exactly.
- [ ] Update README configuration snippets and run a safe YAML parse plus all four MCP test suites.
