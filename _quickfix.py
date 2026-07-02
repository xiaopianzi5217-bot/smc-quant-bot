# Quick fix
with open('core/alpha_master_engine.py', 'r', encoding='utf-8') as f:
    c = f.read()

old = 'verdict["summary"] = "\n".join(lines)'
new = 'verdict["summary"] = "\\n".join(lines)'

if old in c:
    c = c.replace(old, new)
    with open('core/alpha_master_engine.py', 'w', encoding='utf-8') as f:
        f.write(c)
    print("Fixed")
else:
    # Find what's really there
    idx = c.find('verdict["summary"]')
    print(repr(c[idx:idx+60]))
