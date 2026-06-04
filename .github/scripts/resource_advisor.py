#!/usr/bin/env python3
import argparse, subprocess, sys, os
import yaml

RATIOS = {"1:2": 2, "1:4": 4, "1:8": 8}

def parse_cpu(s):
    if s is None: return None
    s = str(s).strip()
    return float(s[:-1]) if s.endswith("m") else float(s) * 1000

def format_cpu(mc):
    mc = round(mc)
    return f"{mc // 1000}" if mc >= 1000 and mc % 1000 == 0 else f"{mc}m"

def parse_memory(s):
    if s is None: return None
    s = str(s).strip()
    for suffix, mult in [("Gi", 1024**3), ("Mi", 1024**2), ("Ki", 1024),
                         ("G",  1000**3), ("M",  1000**2), ("K",  1000)]:
        if s.endswith(suffix):
            return int(float(s[:-len(suffix)]) * mult)
    return int(s)

def format_memory(b):
    if b >= 1024**3 and b % 1024**3 == 0: return f"{b // 1024**3}Gi"
    if b >= 1024**2: return f"{round(b / 1024**2)}Mi"
    if b >= 1024:    return f"{round(b / 1024)}Ki"
    return str(b)

def changed_yaml_files(base, head):
    out = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACM", base, head],
        capture_output=True, text=True, check=True
    ).stdout.strip().splitlines()
    return [f for f in out if f.endswith((".yaml", ".yml")) and os.path.exists(f)]

def find_resources(data, path=""):
    results = []
    if not isinstance(data, dict):
        return results
    for k, v in data.items():
        current_path = f"{path}.{k}" if path else k
        if k == "resources" and isinstance(v, dict):
            results.append((current_path, v))
        elif isinstance(v, str) and "\n" in v:
            # Parse embedded YAML string inside ConfigMap
            try:
                embedded = yaml.safe_load(v)
                if isinstance(embedded, dict):
                    results.extend(find_resources(embedded, current_path))
            except Exception:
                pass
        else:
            results.extend(find_resources(v, current_path))
    return results

def suggest_existing(filepath, resources_found):
    lines = [f"#### `{filepath}`\n"]
    lines.append("| Resource | Current Request | Current Limit | 1:2 | 1:4 | 1:8 |")
    lines.append("|---|---|---|---|---|---|")
    for res_path, res in resources_found:
        req = res.get("requests", {}) or {}
        lim = res.get("limits",   {}) or {}
        prefix = f"`{res_path}`"
        for label, key, parser, formatter in [
            ("CPU",    "cpu",    parse_cpu,    format_cpu),
            ("Memory", "memory", parse_memory, format_memory),
        ]:
            cur_req = parser(req.get(key)) if req.get(key) else None
            cur_lim = parser(lim.get(key)) if lim.get(key) else None
            cur_req_str = f"`{formatter(cur_req)}`" if cur_req else "—"
            cur_lim_str = f"`{formatter(cur_lim)}`" if cur_lim else "—"
            cells = []
            for _, mult in RATIOS.items():
                cells.append(f"`{formatter(cur_req)}` → `{formatter(cur_req * mult)}`" if cur_req else "—")
            lines.append(f"| {prefix} {label} | {cur_req_str} | {cur_lim_str} | {cells[0]} | {cells[1]} | {cells[2]} |")
    return "\n".join(lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base",   required=True)
    ap.add_argument("--head",   required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    files = changed_yaml_files(args.base, args.head)
    print(f"Changed YAML files: {files}", file=sys.stderr)

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

    output_lines = [
        "## 🤖 Resource Advisor\n",
        "> Auto-triggered on PR · CPU & Memory only  \n"
        "> Ratios: **1:2** moderate · **1:4** lean · **1:8** minimal\n",
    ]
    output_lines += sections if sections else [
        "_No `resources:` blocks found in the changed YAML files of this PR._"
    ]
    output_lines.append(
        "\n---\n"
        "<details><summary>ℹ️ How to apply</summary>\n\n"
        "Pick a ratio and update the `resources:` block in your file:\n"
        "```yaml\n"
        "resources:\n"
        "  requests:\n"
        "    cpu: 150m\n"
        "    memory: 256Mi\n"
        "  limits:\n"
        "    cpu: 600m    # 1:4\n"
        "    memory: 1Gi  # 1:4\n"
        "```\n"
        "Then push — the bot will auto-comment updated suggestions.\n"
        "</details>"
    )

    with open(args.output, "w") as f:
        f.write("\n".join(output_lines))
    print("Done.", file=sys.stderr)

if __name__ == "__main__":
    main()