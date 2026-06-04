#!/usr/bin/env python3
"""
Resource Advisor Bot
Scans changed helmvalues.yaml files in a PR.
- If resources block EXISTS  → shows current values + ratio suggestions
- If resources block MISSING → suggests a starter block with 3 ratios
"""

import argparse
import subprocess
import sys
import os
import yaml

RATIOS = {"1:2": 2, "1:4": 4, "1:8": 8}

# ── Default starter values when no resources block exists ──────────────────
STARTER_CPU_REQUEST    = 100   # millicores
STARTER_MEMORY_REQUEST = 128   # MiB


# ── CPU helpers ────────────────────────────────────────────────────────────

def parse_cpu(s):
    if s is None:
        return None
    s = str(s).strip()
    return float(s[:-1]) if s.endswith("m") else float(s) * 1000

def format_cpu(mc):
    mc = round(mc)
    if mc >= 1000 and mc % 1000 == 0:
        return f"{mc // 1000}"
    return f"{mc}m"


# ── Memory helpers ─────────────────────────────────────────────────────────

def parse_memory(s):
    if s is None:
        return None
    s = str(s).strip()
    for suffix, mult in [("Gi", 1024**3), ("Mi", 1024**2), ("Ki", 1024),
                          ("G",  1000**3), ("M",  1000**2), ("K",  1000)]:
        if s.endswith(suffix):
            return int(float(s[:-len(suffix)]) * mult)
    return int(s)

def format_memory(b):
    if b >= 1024**3 and b % 1024**3 == 0:
        return f"{b // 1024**3}Gi"
    if b >= 1024**2:
        return f"{round(b / 1024**2)}Mi"
    if b >= 1024:
        return f"{round(b / 1024)}Ki"
    return str(b)


# ── Git helpers ────────────────────────────────────────────────────────────

def changed_files(base, head):
    out = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACM", base, head],
        capture_output=True, text=True, check=True
    ).stdout.strip().splitlines()
    return [f for f in out if os.path.basename(f) in ("helmvalues.yaml", "values.yaml")
            and os.path.exists(f)]


# ── Find resources block anywhere in a nested dict ─────────────────────────

def find_resources(data, path=""):
    """Recursively find all 'resources' keys. Returns list of (path, value)."""
    results = []
    if not isinstance(data, dict):
        return results
    for k, v in data.items():
        current_path = f"{path}.{k}" if path else k
        if k == "resources" and isinstance(v, dict):
            results.append((current_path, v))
        else:
            results.extend(find_resources(v, current_path))
    return results


# ── Suggestion builders ────────────────────────────────────────────────────

def ratio_row(label, cur_req, cur_lim, parser, formatter):
    req = parser(cur_req) if cur_req else None
    lim = parser(cur_lim) if cur_lim else None
    cur_req_str = f"`{formatter(req)}`" if req else "—"
    cur_lim_str = f"`{formatter(lim)}`" if lim else "—"
    cells = []
    for _, mult in RATIOS.items():
        if req:
            cells.append(f"`{formatter(req)}` → `{formatter(req * mult)}`")
        else:
            cells.append("—")
    return f"| {label} | {cur_req_str} | {cur_lim_str} | {cells[0]} | {cells[1]} | {cells[2]} |"


def suggest_existing(filepath, resources_found):
    lines = [f"#### `{filepath}`\n"]
    lines.append("| Resource | Current Request | Current Limit | 1:2 | 1:4 | 1:8 |")
    lines.append("|---|---|---|---|---|---|")
    for res_path, res in resources_found:
        req = res.get("requests", {}) or {}
        lim = res.get("limits",   {}) or {}
        prefix = f"`{res_path}`"
        lines.append(row := ratio_row(
            f"{prefix} CPU",    req.get("cpu"),    lim.get("cpu"),    parse_cpu,    format_cpu))
        lines.append(ratio_row(
            f"{prefix} Memory", req.get("memory"), lim.get("memory"), parse_memory, format_memory))
    return "\n".join(lines)


def suggest_missing(filepath):
    cpu_r = STARTER_CPU_REQUEST
    mem_r = STARTER_MEMORY_REQUEST * 1024**2   # to bytes

    lines = [
        f"#### `{filepath}`\n",
        f"> ⚠️ No `resources:` block found. Here are starter suggestions:\n",
    ]

    for ratio_name, mult in RATIOS.items():
        cpu_lim = format_cpu(cpu_r * mult)
        mem_lim = format_memory(mem_r * mult)
        lines.append(f"**Ratio {ratio_name}** — request→limit")
        lines.append("```yaml")
        lines.append("resources:")
        lines.append("  requests:")
        lines.append(f"    cpu: {format_cpu(cpu_r)}")
        lines.append(f"    memory: {format_memory(mem_r)}")
        lines.append("  limits:")
        lines.append(f"    cpu: {cpu_lim}")
        lines.append(f"    memory: {mem_lim}")
        lines.append("```\n")

    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base",   required=True)
    ap.add_argument("--head",   required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    files = changed_files(args.base, args.head)
    print(f"Relevant files: {files}", file=sys.stderr)

    sections = []

    for filepath in files:
        try:
            with open(filepath) as f:
                data = yaml.safe_load(f)
        except Exception as e:
            print(f"Warning: cannot parse {filepath}: {e}", file=sys.stderr)
            continue

        resources_found = find_resources(data or {})

        if resources_found:
            sections.append(suggest_existing(filepath, resources_found))
        else:
            sections.append(suggest_missing(filepath))

    output_lines = [
        "## 🤖 Resource Advisor\n",
        "> Triggered by `/suggest-resources` · CPU & Memory only  \n"
        "> Ratios: **1:2** moderate · **1:4** lean · **1:8** minimal\n",
    ]

    if sections:
        output_lines.extend(sections)
    else:
        output_lines.append("_No `helmvalues.yaml` or `values.yaml` files were changed in this PR._")

    output_lines.append(
        "\n---\n"
        "<details><summary>ℹ️ How to apply</summary>\n\n"
        "Pick a ratio and add/update the `resources:` block in your `helmvalues.yaml`, for example:\n"
        "```yaml\n"
        "resources:\n"
        "  requests:\n"
        "    cpu: 100m\n"
        "    memory: 128Mi\n"
        "  limits:\n"
        "    cpu: 400m    # 1:4\n"
        "    memory: 512Mi  # 1:4\n"
        "```\n"
        "Then push and comment `/suggest-resources` again to refresh.\n"
        "</details>"
    )

    with open(args.output, "w") as f:
        f.write("\n".join(output_lines))
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()