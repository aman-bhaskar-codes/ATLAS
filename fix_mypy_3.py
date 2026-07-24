import re

with open("mypy_errors.txt", "r") as f:
    lines = f.readlines()

files = {}
for line in lines:
    if "error:" not in line:
        continue
    
    parts = line.split(":")
    if len(parts) < 3:
        continue
        
    path = parts[0]
    try:
        lineno = int(parts[1])
    except ValueError:
        continue
        
    if path not in files:
        with open(path, "r") as f:
            files[path] = f.readlines()
            
    content = files[path]
    l = content[lineno-1]
    
    if "Unused \"type: ignore\" comment" in line:
        l = re.sub(r'# type: ignore.*', '', l).rstrip() + "\n"
            
    files[path][lineno-1] = l

for path, content in files.items():
    with open(path, "w") as f:
        f.writelines(content)
