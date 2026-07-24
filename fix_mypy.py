import re
import os

with open("mypy_errors.txt", "r") as f:
    lines = f.readlines()

unused_ignores = []
no_return_annotations = []
import_not_founds = []
missing_type_annotations = []

for line in lines:
    if "Unused \"type: ignore\" comment" in line:
        parts = line.split(":")
        unused_ignores.append((parts[0], int(parts[1])))
    elif 'Use "-> None" if function does not return a value' in line:
        parts = line.split(":")
        no_return_annotations.append((parts[0], int(parts[1])))
    elif 'Function is missing a type annotation' in line:
        parts = line.split(":")
        missing_type_annotations.append((parts[0], int(parts[1])))
    elif "Cannot find implementation or library stub for module" in line:
        parts = line.split(":")
        import_not_founds.append((parts[0], int(parts[1])))

# Fix unused ignores (sort by line descending to not mess up offsets)
unused_ignores = sorted(unused_ignores, key=lambda x: x[1], reverse=True)
files = {}
for path, line in unused_ignores:
    if path not in files:
        with open(path, "r") as f:
            files[path] = f.readlines()
    l = files[path][line-1]
    files[path][line-1] = re.sub(r'# type: ignore.*', '', l).rstrip() + "\n"

# Fix no return annotations
no_return_annotations = sorted(no_return_annotations, key=lambda x: x[1], reverse=True)
for path, line in no_return_annotations:
    if path not in files:
        with open(path, "r") as f:
            files[path] = f.readlines()
    l = files[path][line-1]
    if "def " in l and "->" not in l:
        files[path][line-1] = l.replace("):", ") -> None:")

# Fix missing type annotations in test files (default to `-> None`)
missing_type_annotations = sorted(missing_type_annotations, key=lambda x: x[1], reverse=True)
for path, line in missing_type_annotations:
    if not path.startswith("tests/"):
        continue
    if path not in files:
        with open(path, "r") as f:
            files[path] = f.readlines()
    l = files[path][line-1]
    if "def " in l and "->" not in l:
        files[path][line-1] = l.replace("):", ") -> None:")

for path, content in files.items():
    with open(path, "w") as f:
        f.writelines(content)
print(f"Fixed {len(unused_ignores)} unused ignores and {len(no_return_annotations)} missing returns, {len(missing_type_annotations)} missing test annotations.")
