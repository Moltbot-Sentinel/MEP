import sys
import subprocess
import re

try:
    result = subprocess.run([sys.executable, 'get_balance.py'], capture_output=True, text=True, check=True)
    output = result.stdout
    match = re.search(r'\d+(\.\d+)?', output)
    if match:
        print(match.group())
    else:
        print("No balance found in output.")
except Exception as e:
    print(f"Error: {e}")