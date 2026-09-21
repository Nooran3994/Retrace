"""Quick test for the PowerShell transcript parser."""
from retrace.ps_transcript import _parse_transcript

sample = r"""Windows PowerShell transcript start
Start time: 20260919193000
PS C:\Users\HP> git status
On branch main
PS C:\Users\HP> echo TOKEN=abc123
abc123
PS C:\Users\HP> dir
"""

recs = _parse_transcript(sample)
for r in recs:
    print(r)
print("total:", len(recs))
assert len(recs) == 3, f"expected 3, got {len(recs)}"
assert recs[0]["cmd"] == "git status"
assert recs[1]["cmd"] == "echo TOKEN=abc123"
assert recs[2]["cmd"] == "dir"
assert recs[0]["cwd"] == "C:\\Users\\HP"
print("PASS")