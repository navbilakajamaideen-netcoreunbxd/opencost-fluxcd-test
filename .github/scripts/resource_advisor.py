import argparse
import subprocess
import sys
import os
import yaml
 
# --------------------------
# CPU:Memory Ratios
# 1 core CPU : X GB Memory
# --------------------------
RATIOS = {
    "1:2": 2,
    "1:4": 4,
    "1:8": 8
}
 
# GB = 1000^3 bytes
GB = 1000**3
MB = 1000**2
 
# --------------------------
# CPU recommendation config
# --------------------------
# TODO: 
CPU_RECOMMENDATION_FACTOR = 0.90
 

MIN_RECOMMENDED_MILLICORES = 10
 
# --------------------------
# CPU
# --------------------------
 
def parse_cpu(value):
    if value is None:
        return None
    value = str(value).strip()
    if value.endswith("m"):
        return float(value[:-1]) / 1000
    return float(value)
 
def format_cpu(value):
    if value < 1:
        return f"{round(value * 1000)}m"
    if value == int(value):
        return str(int(value))
    return f"{round(value * 1000)}m"
 

 
def recommend_cpu(cpu_cores):
    if cpu_cores is None or cpu_cores <= 0:
        return None
 
    recommended_millicores = round(cpu_cores * CPU_RECOMMENDATION_FACTOR * 1000)
    recommended_millicores = max(recommended_millicores, MIN_RECOMMENDED_MILLICORES)
 
    return f"{recommended_millicores}m"
 
def is_cpu_already_optimized(cpu_cores):
    
    if cpu_cores is None or cpu_cores <= 0:
        return True  # nothing to recommend
 
    return cpu_cores != int(cpu_cores)
 
# --------------------------
# Memory — parse to bytes
# --------------------------
 
def parse_memory(value):
    if value is None:
        return None
    value = str(value).strip()
    units = [
        ("Gi", 1024**3),
        ("Mi", 1024**2),
        ("Ki", 1024),
        ("GB", 1000**3),
        ("MB", 1000**2),
        ("KB", 1000),
        ("G",  1000**3),
        ("M",  1000**2),
        ("K",  1000),
    ]
    for suffix, multiplier in units:
        if value.endswith(suffix):
            return int(float(value[:-len(suffix)]) * multiplier)
    return int(value)
 
# --------------------------
# Memory — format bytes to human readable
# Using MB/GB (1000-based)
# --------------------------
 
def format_memory(value):
    if value >= 1000**3:
        gb = value / 1000**3
        if gb == int(gb):
            return f"{int(gb)}GB"
        return f"{round(gb, 1)}GB"
    if value >= 1000**2:
        return f"{round(value / 1000**2)}MB"
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
 
# --------------------------
# Validate CPU:Memory ratio
#
# ratio = memory_in_GB / cpu_in_cores
#
# 1:2 → 2.0 to 2.99
# 1:4 → 4.0 to 4.99
# 1:8 → 8.0 to 8.99
# --------------------------
 
def validate_cpu_memory_ratio(cpu_cores, memory_bytes):
    if cpu_cores is None or memory_bytes is None:
        return None, None
 
    if cpu_cores <= 0:
        return None, None
 
    memory_gb = memory_bytes / GB
    ratio = memory_gb / cpu_cores
 
    if 2.0 <= ratio < 3.0:
        return "valid", "1:2"
    elif 4.0 <= ratio < 5.0:
        return "valid", "1:4"
    elif 8.0 <= ratio < 9.0:
        return "valid", "1:8"
    elif ratio < 2.0:
        return "too-low", None
    elif ratio >= 9.0:
        return "too-high", None
    else:
        return "not-standard", None
 
# --------------------------
# Suggest memory based on CPU
# Using GB (1000-based)
# --------------------------
 
def suggest_memory(cpu_cores):
    suggestions = {}
    for ratio_name, multiplier in RATIOS.items():
        memory_bytes = cpu_cores * multiplier * GB
        suggestions[ratio_name] = format_memory(int(memory_bytes))
    return suggestions
 
# --------------------------
# Markdown Generation
# --------------------------
 
def build_section(filepath, resources_found):
    lines = []
    lines.append(f"### 📄 {filepath}")
    lines.append("")
 
    valid   = 0
    invalid = 0
    cpu_recommendations = 0
 
    for path, resource in resources_found:
        requests = resource.get("requests", {}) or {}
        limits   = resource.get("limits",   {}) or {}
 
        # Keep original strings for display
        req_cpu_raw = str(requests.get("cpu", "—"))
        req_mem_raw = str(requests.get("memory", "—"))
        lim_cpu_raw = str(limits.get("cpu", "—"))
        lim_mem_raw = str(limits.get("memory", "—"))
 
        # Parse for calculation only
        req_cpu = parse_cpu(requests.get("cpu"))
        req_mem = parse_memory(requests.get("memory"))
        lim_cpu = parse_cpu(limits.get("cpu"))
        lim_mem = parse_memory(limits.get("memory"))
 
        def get_status(result, nearest):
            if result == "valid":
                return f"✅ Near {nearest}"
            elif result == "too-low":
                return "❌ Memory too low for CPU"
            elif result == "too-high":
                return "⚠️ Memory too high for CPU"
            elif result == "not-standard":
                return "❌ Not standard (use 1:2, 1:4 or 1:8)"
            return "—"
 
        def ratio_str(cpu, mem):
            if cpu and mem and cpu > 0:
                return f"1:{round((mem / GB) / cpu, 2)}"
            return "—"
 
        # Requests
        req_result, req_nearest = validate_cpu_memory_ratio(req_cpu, req_mem)
        req_status = get_status(req_result, req_nearest)
        req_suggestions = suggest_memory(req_cpu) if req_cpu else None
        req_cpu_recommendation = recommend_cpu(req_cpu) if req_cpu else None
        req_cpu_flagged = req_cpu_recommendation and not is_cpu_already_optimized(req_cpu)
 
        if req_result == "valid":
            valid += 1
        elif req_result in ("too-low", "too-high", "not-standard"):
            invalid += 1
        if req_cpu_flagged:
            cpu_recommendations += 1
 
        # Limits
        lim_result, lim_nearest = validate_cpu_memory_ratio(lim_cpu, lim_mem)
        lim_status = get_status(lim_result, lim_nearest)
        lim_suggestions = suggest_memory(lim_cpu) if lim_cpu else None
        lim_cpu_recommendation = recommend_cpu(lim_cpu) if lim_cpu else None
        lim_cpu_flagged = lim_cpu_recommendation and not is_cpu_already_optimized(lim_cpu)
 
        if lim_result == "valid":
            valid += 1
        elif lim_result in ("too-low", "too-high", "not-standard"):
            invalid += 1
        if lim_cpu_flagged:
            cpu_recommendations += 1
 
        # Requests table
        lines.append(f"#### {path} — Requests")
        lines.append("")
        lines.append("| CPU | Suggested CPU | Memory | CPU:Memory Ratio | Status | 1:2 memory | 1:4 memory | 1:8 memory |")
        lines.append("|---|---|---|---|---|---|---|---|")
 
        lines.append(
            f"| {req_cpu_raw} "
            f"| {req_cpu_recommendation if req_cpu_recommendation else '—'} "
            f"| {req_mem_raw} "
            f"| {ratio_str(req_cpu, req_mem)} "
            f"| {req_status} "
            f"| {req_suggestions['1:2']} "
            f"| {req_suggestions['1:4']} "
            f"| {req_suggestions['1:8']} |"
            if req_suggestions else
            f"| {req_cpu_raw} | {req_cpu_recommendation if req_cpu_recommendation else '—'} | {req_mem_raw} | — | — | — | — | — |"
        )
 
        lines.append("")
 
        # Limits table
        lines.append(f"#### {path} — Limits")
        lines.append("")
        lines.append("| CPU (fixed) | Suggested CPU | Memory | CPU:Memory Ratio | Status | 1:2 memory | 1:4 memory | 1:8 memory |")
        lines.append("|---|---|---|---|---|---|---|---|")
 
        lines.append(
            f"| {lim_cpu_raw} "
            f"| {lim_cpu_recommendation if lim_cpu_recommendation else '—'} "
            f"| {lim_mem_raw} "
            f"| {ratio_str(lim_cpu, lim_mem)} "
            f"| {lim_status} "
            f"| {lim_suggestions['1:2']} "
            f"| {lim_suggestions['1:4']} "
            f"| {lim_suggestions['1:8']} |"
            if lim_suggestions else
            f"| {lim_cpu_raw} | {lim_cpu_recommendation if lim_cpu_recommendation else '—'} | {lim_mem_raw} | — | — | — | — | — |"
        )
 
        lines.append("")
 
    return "\n".join(lines), valid, invalid, cpu_recommendations
 
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
    total_cpu_recommendations = 0
 
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
 
        section, valid, invalid, cpu_recs = build_section(filepath, resources_found)
        total_valid   += valid
        total_invalid += invalid
        total_cpu_recommendations += cpu_recs
 
        
        if invalid > 0 or cpu_recs > 0:
            sections.append(section)
 
    if total_invalid == 0 and total_cpu_recommendations == 0:
        print("All CPU:Memory ratios are standard and no CPU values found. No comment posted.", file=sys.stderr)
        return
 
    report = [
        "## 🤖 Resource Advisor",
        "",
        f"✅ Standard Ratios: **{total_valid}**",
        f"❌ Non-standard Ratios: **{total_invalid}**",
        f"💡 CPU Recommendations: **{total_cpu_recommendations}**",
        "> Please fix non-standard ratios before merging.",
        "",
    ]
 
    report.extend(sections)
 
    how_to_fix = """
---
<details><summary>ℹ️ How to fix</summary>
 
**Memory / ratio issues:** CPU stays unchanged. Only update **memory** to match a standard CPU:Memory ratio.
 
Memory is calculated using GB (1000-based):
```
cpu = 500m = 0.5 cores
1:2 → 0.5 × 2GB = 1GB
1:4 → 0.5 × 4GB = 2GB
1:8 → 0.5 × 8GB = 4GB
```
 
```yaml
resources:
  requests:
    cpu: 500m      
    memory: 1GB    # 1:2 ratio
  limits:
    cpu: 500m      
    memory: 1GB    # 1:2 ratio
```
 
</details>"""
 
    report.append(how_to_fix)
 
    with open(args.output, "w") as f:
        f.write("\n".join(report))
 
    print("Done.", file=sys.stderr)
 
if __name__ == "__main__":
    main()