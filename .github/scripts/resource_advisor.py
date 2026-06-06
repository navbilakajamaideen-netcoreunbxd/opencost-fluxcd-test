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
        ("G", 1000**3),
        ("M", 1000**2),
        ("K", 1000),
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
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=ACM",
            base,
            head
        ],
        capture_output=True,
        text=True,
        check=True
    )

    files = result.stdout.strip().splitlines()

    return [
        f
        for f in files
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

            current_path = (
                f"{path}.{key}"
                if path
                else key
            )

            if key == "resources" and isinstance(value, dict):
                results.append(
                    (current_path, value)
                )

            results.extend(
                find_resources(value, current_path)
            )

    elif isinstance(data, list):

        for index, item in enumerate(data):
            results.extend(
                find_resources(
                    item,
                    f"{path}[{index}]"
                )
            )

    elif isinstance(data, str) and "\n" in data:

        try:
            embedded = yaml.safe_load(data)

            if embedded:
                results.extend(
                    find_resources(
                        embedded,
                        path
                    )
                )

        except Exception:
            pass

    return results


# --------------------------
# Ratio Validation
# --------------------------

# FIXED: Old code only accepted EXACT ratios (1:2, 1:4, 1:8).
# Any value like 550m limit / 130m request = 4.23 was flagged as ❌ Non-standard.
# New logic:
#   - If limit < request         → 🚨 always invalid (Kubernetes will reject the pod)
#   - If ratio is within 1.5–10  → ✅ Valid (flexible, real-world friendly)
#   - If ratio > 10              → ⚠️  Warn (wasteful but not broken)
#   - If ratio < 1.5             → ❌ Too tight (limit barely above request, risky)
# The suggestion columns still show what the nearest standard ratio would look like.

MIN_VALID_RATIO = 1.5   # limit must be at least 1.5× the request
MAX_VALID_RATIO = 10.0  # beyond 10× is considered wasteful


def validate_ratio(request, limit):
    if request is None or limit is None:
        return None

    if limit < request:
        return "limit-less-than-request"

    ratio = limit / request

    if ratio < MIN_VALID_RATIO:
        return "too-tight"          # e.g. limit is only 1.1× request

    if ratio > MAX_VALID_RATIO:
        return "too-loose"          # e.g. limit is 20× request — wasteful

    return "valid"                  # anything between 1.5× and 10× is fine


# --------------------------
# Markdown Generation
# --------------------------

def build_section(filepath, resources_found):

    lines = []

    lines.append(f"### 📄 `{filepath}`")
    lines.append("")

    lines.append(
        "| Resource | Request | Limit | Ratio | Status | 1:2 suggestion | 1:4 suggestion | 1:8 suggestion |"
    )

    lines.append(
        "|---|---|---|---|---|---|---|---|"
    )

    valid = 0
    invalid = 0

    for path, resource in resources_found:

        requests = resource.get("requests", {}) or {}
        limits = resource.get("limits", {}) or {}

        resources_to_check = [
            ("CPU", "cpu", parse_cpu, format_cpu),
            ("Memory", "memory", parse_memory, format_memory)
        ]

        for label, key, parser, formatter in resources_to_check:

            req = parser(requests.get(key)) \
                if requests.get(key) else None

            lim = parser(limits.get(key)) \
                if limits.get(key) else None

            result = validate_ratio(req, lim)

            # Calculate actual ratio for display
            if req and lim and req > 0:
                actual_ratio = round(lim / req, 2)
                ratio_str = f"1:{actual_ratio}"
            else:
                ratio_str = "—"

            if result == "limit-less-than-request":
                # 🚨 Kubernetes will reject the pod outright
                status = "🚨 Limit < Request"
                invalid += 1

            elif result == "too-tight":
                # ❌ Limit is too close to request — pod will be OOMKilled or throttled easily
                status = "❌ Too tight"
                invalid += 1

            elif result == "too-loose":
                # ⚠️ Limit is way higher than request — wastes cluster resources
                status = "⚠️ Too loose (wasteful)"
                invalid += 1

            elif result == "valid":
                # ✅ Ratio is in a healthy range
                status = "✅ Valid"
                valid += 1

            else:
                status = "—"

            req_str = formatter(req) if req else "—"
            lim_str = formatter(lim) if lim else "—"

            # Suggestion columns: show what standard ratios would look like
            # If limit < request, suggest based on limit (what request should be)
            # Otherwise suggest based on request (what limit could be)
            suggestions = []

            for multiplier in RATIOS.values():
                if result == "limit-less-than-request" and lim is not None:
                    suggestions.append(formatter(lim / multiplier))
                elif req is not None:
                    suggestions.append(formatter(req * multiplier))
                else:
                    suggestions.append("—")

            lines.append(
                f"| `{path}` {label} "
                f"| `{req_str}` "
                f"| `{lim_str}` "
                f"| `{ratio_str}` "
                f"| {status} "
                f"| `{suggestions[0]}` "
                f"| `{suggestions[1]}` "
                f"| `{suggestions[2]}` |"
            )

    return "\n".join(lines), valid, invalid


# --------------------------
# Main
# --------------------------

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", required=True)

    args = parser.parse_args()

    files = changed_yaml_files(
        args.base,
        args.head
    )

    sections = []

    total_valid = 0
    total_invalid = 0

    for file_path in files:

        try:

            with open(file_path) as f:
                docs = list(
                    yaml.safe_load_all(f)
                )

        except Exception as e:
            print(
                f"Cannot parse {file_path}: {e}",
                file=sys.stderr
            )
            continue

        resources_found = []

        for doc in docs:
            resources_found.extend(
                find_resources(doc or {})
            )

        if not resources_found:
            continue

        section, valid, invalid = build_section(
            file_path,
            resources_found
        )

        if invalid > 0:
            sections.append(section)

        total_valid += valid
        total_invalid += invalid

    if total_invalid == 0:
        print("All resource ratios are valid. No comment will be posted.")
        return

    report = []

    report.append("## 🤖 Resource Advisor")
    report.append("")
    report.append(
        f"✅ Valid Ratios: **{total_valid}**"
    )
    report.append(
        f"❌ Invalid Ratios: **{total_invalid}**"
    )
    report.append("")
    report.append(
        "> ℹ️ A ratio is **valid** when `limit` is between **1.5×** and **10×** the `request`."
        " The suggestion columns show what standard 1:2 / 1:4 / 1:8 limits would look like."
    )
    report.append("")

    if sections:
        report.extend(sections)
    else:
        report.append(
            "_No resources blocks found in changed YAML files._"
        )

    with open(args.output, "w") as output:
        output.write(
            "\n".join(report)
        )


if __name__ == "__main__":
    main()