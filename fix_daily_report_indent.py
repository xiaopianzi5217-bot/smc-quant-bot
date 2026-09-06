"""Fix indentation on line 216 in analytics/daily_report.py."""
from pathlib import Path

path = Path("analytics/daily_report.py")
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

# Line 216 (0-based index 215) has 16 spaces instead of 8
idx = 215
old_line = lines[idx]
new_line = "        sym = ev.get('symbol') or 'UNK'\n"

print(f"OLD [{idx+1}]: {old_line!r}")
print(f"NEW [{idx+1}]: {new_line!r}")

# Verify content matches what we expect before replacing
expected_content = "sym = ev.get('symbol') or 'UNK'"
if expected_content in old_line:
    lines[idx] = new_line
    path.write_text("".join(lines), encoding="utf-8")
    print("SUCCESS: Fixed indentation on line 216")
else:
    print(f"ERROR: Line {idx+1} does not contain expected content: {old_line!r}")