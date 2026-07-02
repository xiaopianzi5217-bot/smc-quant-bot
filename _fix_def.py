# Fix the def _build_verdict signature and all indentation after it
with open('core/alpha_master_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the def _build_verdict
for i, line in enumerate(lines):
    if '_build_verdict' in line and 'def' in line:
        # Fix the signature
        # Current:
        # L484:        def _build_verdict(self, allow, reason, regime, vol_state, signal,
        # L485:
        # L486:                   book="", size=0.0, long_sig=None, short_sig=None):
        # 
        # Should be:
        #     def _build_verdict(
        #         self,
        #         allow: bool,
        #         ...
        #     ) -> Dict[str, Any]:
        
        # Simplified: just put everything on one line
        lines[i] = '    def _build_verdict(self, allow, reason, regime, vol_state, signal, book="", size=0.0, long_sig=None, short_sig=None):\n'
        # Remove the 2nd line of signature
        lines[i+2] = ''  # blank out the hanging line
        # The existing body (lines after i+2) is already at 8 spaces, keep it
        break

# Fix _build_verdict return type - remove "-> Dict[str, Any]:" since we simplified
# Actually it's fine without type hints on the one-liner

with open('core/alpha_master_engine.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

c = ''.join(lines)
try:
    compile(c, 'core/alpha_master_engine.py', 'exec')
    print("Syntax OK!")
except SyntaxError as e:
    print(f"Line {e.lineno}: {e.msg}")
    print(repr(lines[e.lineno-1] if e.lineno and e.lineno <= len(lines) else '???'))
