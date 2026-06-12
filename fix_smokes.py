import glob
import re


def fix_script(filepath):
    with open(filepath, encoding='utf-8') as f:
        content = f.read()

    # Append random suffix to names in json payloads
    content = re.sub(r'json=\{"name": "([^"]+)"', r'json={"name": f"\1 {uuid.uuid4().hex[:6]}"', content)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

for script in glob.glob("C:\\Users\\Lytrium\\Documents\\Projects\\Nexum\\backend\\scripts\\smoke_*.py"):
    fix_script(script)

print("Fixed smokes.")
