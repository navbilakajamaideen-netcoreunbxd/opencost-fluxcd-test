#!/usr/bin/env python3

import argparse
import subprocess
import sys
import os
import yaml

RATIOS = {
    "1:2": 2,
    "1:4": 4,
    "1:8": 8
}

# --------------------------
# CPU
# --------------------------

def parse_cpu(value):
    if value is None:
        return None
    value = str(value).strip()
    if value.endswith("m"):
        return float(value[:-1])
    return float(value) * 1000

def format_cpu(value):
    value = round(value)
    if value >= 1000 and value % 1000 == 0:
        return str(value // 1000)
    return f"{value}m"

# --------------------------
# Memory
# --------------------------

def parse_memory(value):
    if value is None:
        return None
    value = str(value).strip()
    units = [
        ("Gi", 1024**3),
        ("Mi", 1024**2),
        ("Ki", 1024),
        ("G",  1000**3),
        ("M",  1000**2),
        ("K",  1000),
    ]
    for suffix, multiplier in units:
        if value.endswith(suffix):
            return int(float(value[:-len(suffix)]) * multiplier)
    return int(value)

def format_memory(value):
    if value >= 1024**3 and value % 1024**3 == 0:
        return f"{value // 1024**3}Gi"
    if value >= 1024**2:
        return f"{round(value / 1024**2)}Mi"
    if value >= 1024:
        return f"{round(value / 1024)}Ki"
    return str(value)

# --------------------------
# Git Diff
# --------------------------

def changed_yaml_files(base, head):
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACM", base, head],
        capture_output=True,
        text=True,
        check=True
    )
    files = result.stdout.strip().splitlines()
    return [
        f for f in files
        if f.endswith((".yaml", ".yml"))
        and os.path.exists(f)
        and "kustomization" not in f.lower()
    ]

# --------------------------
# Resource Discovery
# --------------------------

def find_resources(data, path=""):
    results = []

    if isinstance(data, dict):
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key
            if key == "resources" and isinstance(value, dict):
                results.append((current_path, value))
            results.extend(find_resources(value, current_path))

    elif isinstance(data, list):
        for index, item in enumerate(data):
            results.extend(find_resources(item, f"{path}[{index}]"))

    elif isinstance(data, str) and "\n" in data:
        try:
            embedded = yaml.safe_load(data)
            if embedded:
                results.extend(find_resources(embedded, path))
        except Exception:
            pass

    return results



def validate_ratio(request, limit):
    if request is None or limit is None:
        return None, None

    if limit < request:
        return "limit-less-than-request", None

    ratio = limit / request

    if 2.0 <= ratio < 3.0:
        return "valid", "1:2"
    elif 4.0 <= ratio < 5.0:
        return "valid", "1:4"
    elif 8.0 <= ratio < 9.0:
        return "valid", "1:8"
    elif ratio < 2.0:
        return "too-tight", None
    elif ratio >= 9.0:
        return "too-loose", None
    else:
        return "not-standard", None

# --------------------------
# Markdown Generation
# --------------------------

def build_section(filepath, resources_found):
    lines = []
    lines.append(f"### 📄 `{filepath}`")
    lines.append("")
    lines.append("| Resource | Request | Limit | Ratio | Status | 1:2 limit | 1:4 limit | 1:8 limit |")
    lines.append("|---|---|---|---|---|---|---|---|")

    valid = 0
    invalid = 0

    for path, resource in resources_found:
        requests = resource.get("requests", {}) or {}
        limits   = resource.get("limits",   {}) or {}

        for label, key, parser, formatter in [
            ("CPU",    "cpu",    parse_cpu,    format_cpu),
            ("Memory", "memory", parse_memory, format_memory),
        ]:
            req = parser(requests.get(key)) if requests.get(key) else None
            lim = parser(limits.get(key))   if limits.get(key)   else None

            result, nearest = validate_ratio(req, lim)

            # Actual ratio string
            if req and lim and req > 0:
                ratio_str = f"1:{round(lim / req, 2)}"
            else:
                ratio_str = "—"

            # Status
            if result == "limit-less-than-request":
                status = "🚨 Limit < Request"
                invalid += 1
            elif result == "too-tight":
                status = "❌ Too tight (ratio < 1:2)"
                invalid += 1
            elif result == "not-standard":
                status = "❌ Not standard (use 1:2, 1:4 or 1:8)"
                invalid += 1
            elif result == "too-loose":
                status = "⚠️ Too loose (ratio > 1:8)"
                invalid += 1
            elif result == "valid":
                status = f"✅ Near {nearest}"
                valid += 1
            else:
                status = "—"

            req_str = formatter(req) if req else "—"
            lim_str = formatter(lim) if lim else "—"

            # Suggestion columns — always based on request
            suggestions = []
            for mult in RATIOS.values():
                suggestions.append(
                    f"`{formatter(req * mult)}`" if req else "—"
                )

            lines.append(
                f"| `{path}` {label} "
                f"| `{req_str}` "
                f"| `{lim_str}` "
                f"| `{ratio_str}` "
                f"| {status} "
                f"| {suggestions[0]} "
                f"| {suggestions[1]} "
                f"| {suggestions[2]} |"
            )

    return "\n".join(lines), valid, invalid

# --------------------------
# Main
# --------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base",   required=True)
    ap.add_argument("--head",   required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    files = changed_yaml_files(args.base, args.head)
    print(f"Changed YAML files: {files}", file=sys.stderr)

    sections      = []
    total_valid   = 0
    total_invalid = 0

    for filepath in files:
        try:
            with open(filepath) as f:
                docs = list(yaml.safe_load_all(f))
        except Exception as e:
            print(f"Cannot parse {filepath}: {e}", file=sys.stderr)
            continue

        resources_found = []
        for doc in docs:
            resources_found.extend(find_resources(doc or {}))

        if not resources_found:
            continue

        section, valid, invalid = build_section(filepath, resources_found)
        total_valid   += valid
        total_invalid += invalid

        if invalid > 0:
            sections.append(section)

    if total_invalid == 0:
        print("All ratios are standard. No comment posted.", file=sys.stderr)
        return

    report = [
        "## 🤖 Resource Advisor",
        "",
        f"✅ Standard Ratios: **{total_valid}**",
        f"❌ Non-standard Ratios: **{total_invalid}**",
        "",
        "> **Valid ranges:** `1:2` (2.0–2.99×) · `1:4` (4.0–4.99×) · `1:8` (8.0–8.99×)",
        "> Please fix non-standard ratios before merging.",
        "",
    ]

    report.extend(sections)

    report.append(
        "\n---\n"
        "<details><summary>ℹ️ How to fix</summary>\n\n"
        "Pick a standard ratio and update your `resources:` block:\n"
        "```yaml\n"
        "resources:\n"
        "  requests:\n"
        "    cpu: 200m\n"
        "    memory: 256Mi\n"
        "  limits:\n"
        "    cpu: 400m      # 1:2 → 400m | 1:4 → 800m | 1:8 → 1600m\n"
        "    memory: 512Mi  # 1:2 → 512Mi | 1:4 → 1Gi | 1:8 → 2Gi\n"
        "```\n"
        "</details>"
    )

    with open(args.output, "w") as f:
        f.write("\n".join(report))

    print("Done.", file=sys.stderr)

if __name__ == "__main__":
    main()