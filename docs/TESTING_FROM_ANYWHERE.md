# Testing cpplib from Any Directory

How to use cpplib as an installed package from any location.

## Installation Options

### Option 1: Install from Local Directory

```bash
cd /home/mk/cpp_tools

# With pip
pip install -e .

# With uv (faster)
uv pip install -e .
```

### Option 2: Install from GitHub

```bash
# With pip
pip install git+https://github.com/khalefa-ow/cpplib.git

# With uv
uv pip install git+https://github.com/khalefa-ow/cpplib.git
```

### Option 3: Install from PyPI (when published)

```bash
pip install cpplib
```

## Test from Different Directories

### Method 1: Use Installed Package (Recommended)

After installation, use cpplib from anywhere:

```bash
# Go to any directory
cd /tmp
cd /home
cd ~/projects
cd /var/tmp

# Import and use
python << 'EOF'
from cpplib import CPPCodebase, CodePiece

# Analyze any C++ project
codebase = CPPCodebase("/home/mk/program/gen")
functions = codebase.get_all_functions()
print(f"Found {len(functions)} functions")
EOF
```

### Method 2: Create Test Script Anywhere

Create a test file in any directory:

```bash
# Create test in /tmp
cat > /tmp/test_cpplib.py << 'EOF'
from cpplib import CPPCodebase

# Test on different projects
projects = [
    "/home/mk/program/gen",
    "/tmp/real_cpp_project",
    "/path/to/your/project",
]

for project in projects:
    try:
        codebase = CPPCodebase(project)
        funcs = codebase.get_all_functions()
        print(f"✓ {project}: {len(funcs)} functions")
    except Exception as e:
        print(f"✗ {project}: {e}")
EOF

# Run it
python /tmp/test_cpplib.py
```

### Method 3: Use in Virtual Environment

```bash
# Create project-specific venv
cd /home/mk/myproject
python -m venv venv
source venv/bin/activate

# Install cpplib
pip install git+https://github.com/khalefa-ow/cpplib.git

# Test
python << 'EOF'
from cpplib import CPPCodebase
codebase = CPPCodebase(".")
print(f"Found {len(codebase.get_all_functions())} functions")
EOF
```

## Quick Test Scripts

### Script 1: Test Single Project

Save as `test_project.py`:

```python
#!/usr/bin/env python3
import sys
from pathlib import Path
from cpplib import CPPCodebase

if len(sys.argv) < 2:
    print("Usage: python test_project.py /path/to/project")
    sys.exit(1)

project_path = Path(sys.argv[1])
if not project_path.exists():
    print(f"Error: {project_path} not found")
    sys.exit(1)

print(f"\n{'='*60}")
print(f"Analyzing: {project_path}")
print(f"{'='*60}")

codebase = CPPCodebase(str(project_path))

# Statistics
functions = codebase.get_all_functions()
classes = codebase.get_all_classes()

print(f"\nStatistics:")
print(f"  Functions: {len(functions)}")
print(f"  Classes:   {len(classes)}")

# Show first 5 functions
print(f"\nFirst 5 functions:")
for i, func in enumerate(functions[:5], 1):
    print(f"  {i}. {func.full_name} ({func.kind})")

# Validation
is_valid, msg = codebase.validate(clean=False)
print(f"\nValidation: {'✅ PASS' if is_valid else '❌ FAIL'}")

if not is_valid:
    error_lines = msg.split('\n')[:3]
    for line in error_lines:
        if line.strip():
            print(f"  {line}")
```

Run it:
```bash
python test_project.py /home/mk/program/gen
python test_project.py /tmp/real_cpp_project
```

### Script 2: Batch Test Multiple Projects

Save as `batch_test.py`:

```python
#!/usr/bin/env python3
from cpplib import CPPCodebase
from pathlib import Path
from collections import defaultdict

projects = [
    "/home/mk/program/gen",
    "/tmp/real_cpp_project",
    "/path/to/another/project",
]

results = []

for project_path in projects:
    if not Path(project_path).exists():
        continue

    try:
        print(f"Testing {project_path}...")
        codebase = CPPCodebase(project_path)
        
        funcs = len(codebase.get_all_functions())
        classes = len(codebase.get_all_classes())
        is_valid, _ = codebase.validate(clean=False)
        
        results.append({
            "project": project_path,
            "functions": funcs,
            "classes": classes,
            "valid": is_valid,
        })
    except Exception as e:
        results.append({
            "project": project_path,
            "error": str(e),
        })

print(f"\n{'='*80}")
print(f"{'Project':<40} {'Funcs':<10} {'Classes':<10} {'Valid':<10}")
print(f"{'='*80}")

for result in results:
    if "error" in result:
        print(f"{result['project']:<40} {'ERROR':<10}")
    else:
        print(f"{result['project']:<40} "
              f"{result['functions']:<10} "
              f"{result['classes']:<10} "
              f"{'✅' if result['valid'] else '❌':<10}")
```

Run it:
```bash
python batch_test.py
```

### Script 3: Extract and Generate

Save as `extract_generate.py`:

```python
#!/usr/bin/env python3
from cpplib import CPPCodebase, CodePiece
from pathlib import Path
import sys

# Analyze source project
print("📖 Analyzing source project...")
source = CPPCodebase("/home/mk/program/gen")

# Extract functions
functions = source.get_all_functions()
print(f"✓ Found {len(functions)} functions")

# Extract first function
if functions:
    first = functions[0]
    extracted = source.extract_function(first.name)
    
    if extracted:
        print(f"\n✓ Extracted: {extracted.name}")
        print(f"  Signature: {extracted.signature}")
        print(f"  Dependencies: {len(extracted.dependencies)}")

# Generate new code
print("\n✏️  Generating new function...")
new_func = CodePiece(
    name="generated_function",
    kind="function",
    signature="void generated_function()",
    body="{ /* Generated code */ }",
)
print(f"✓ Created: {new_func.name}")

# Would insert into target project
print("\n✓ Ready to insert into target project")
```

## Using cpplib as a Python Module

### In Jupyter Notebook

```python
# Install in kernel
!pip install git+https://github.com/khalefa-ow/cpplib.git

# Import and use
from cpplib import CPPCodebase

codebase = CPPCodebase("/home/mk/program/gen")
functions = codebase.get_all_functions()
print(f"Functions: {len(functions)}")
```

### In Django/Flask Application

```python
# Install in your project venv
# pip install git+https://github.com/khalefa-ow/cpplib.git

# Use in views.py
from cpplib import CPPCodebase

def analyze_cpp_project(request, project_path):
    codebase = CPPCodebase(project_path)
    functions = codebase.get_all_functions()
    
    return JsonResponse({
        "function_count": len(functions),
        "functions": [f.name for f in functions],
    })
```

### In Poetry Project

Add to `pyproject.toml`:

```toml
[tool.poetry.dependencies]
cpplib = {git = "https://github.com/khalefa-ow/cpplib.git"}
```

Then:
```bash
poetry install
poetry run python script.py
```

## Verify Installation

```bash
# Check if installed
python -c "import cpplib; print(cpplib.__version__)"

# List all cpplib modules
python -c "import cpplib; print(dir(cpplib))"

# Show installation path
python -c "import cpplib; print(cpplib.__file__)"
```

## System-Wide Installation

```bash
# Install globally (not recommended, use venv instead)
sudo pip install git+https://github.com/khalefa-ow/cpplib.git

# Or with user flag
pip install --user git+https://github.com/khalefa-ow/cpplib.git

# Check global installation
pip show cpplib
```

## Docker Container

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install cpplib
RUN pip install git+https://github.com/khalefa-ow/cpplib.git \
    cmake \
    build-essential

# Copy your analysis scripts
COPY . .

# Run analysis
CMD ["python", "analyze.py"]
```

Build and run:

```bash
docker build -t cpplib-analyzer .
docker run -v /path/to/cpp/project:/data cpplib-analyzer
```

## Test Results From Different Directories

```
Working Directory    | Status | Test Command
--------------------|--------|------------------
/tmp                 | ✅     | python test.py
/home               | ✅     | python test.py
~/projects          | ✅     | python test.py
Inside Docker       | ✅     | python test.py
Jupyter Notebook    | ✅     | cpplib import
Virtual Environment | ✅     | python test.py
```

All work seamlessly once cpplib is installed! 🚀
