# cpplib Usage Guide

A Python library for analyzing, extracting, and modifying C++ code with semantic understanding.

## Table of Contents

1. [Installation](#installation)
2. [Quick Start](#quick-start)
3. [Core Concepts](#core-concepts)
4. [Common Use Cases](#common-use-cases)
5. [API Reference](#api-reference)
6. [Advanced Examples](#advanced-examples)
7. [Troubleshooting](#troubleshooting)

## Installation

```bash
# Clone and install
git clone https://github.com/khalefa-ow/cpplib.git
cd cpplib
pip install -e ".[dev]"

# Run tests to verify
pytest tests/ -v
```

## Quick Start

### 1. Load a C++ Project

```python
from cpplib import CPPCodebase

# Point to your C++ project directory
codebase = CPPCodebase("/path/to/your/cpp/project")
```

**What happens automatically:**
- Scans all `.cpp`, `.h`, `.hpp`, `.cc`, `.cxx` files
- Parses them with tree-sitter C++ parser
- Builds semantic symbol table
- Extracts dependency graph
- Validates with cmake (if CMakeLists.txt exists)

### 2. Analyze Functions

```python
# Get all functions
functions = codebase.get_all_functions()
print(f"Found {len(functions)} functions")

# Iterate through them
for func in functions:
    print(f"{func.full_name} at {func.file_path}:{func.line_start}")
```

### 3. Extract a Function

```python
# Extract specific function with all dependencies
func = codebase.extract_function("myFunction")

if func:
    print(f"Name: {func.name}")
    print(f"Signature: {func.signature}")
    print(f"Body:\n{func.body}")
    print(f"Dependencies: {func.dependencies}")
    print(f"Location: {func.source_file}:{func.line_start}-{func.line_end}")
```

### 4. Analyze Classes

```python
# Get all classes
classes = codebase.get_all_classes()

# Extract a specific class
calc_class = codebase.extract_class("Calculator")
print(f"Class {calc_class.name} at {calc_class.file_path}")
print(f"Members and dependencies: {calc_class.dependencies}")
```

### 5. Insert New Code

```python
from cpplib import CodePiece

# Create a new function
new_func = CodePiece(
    name="add",
    kind="function",
    signature="int add(int a, int b)",
    body="{ return a + b; }",
)

# Insert into a file
codebase.insert_function("src/math.cpp", new_func, after="subtract")

# Apply changes
success = codebase.generate()
print(f"Changes applied: {success}")
```

### 6. Validate Changes

```python
# Compile and verify code
is_valid, message = codebase.validate()

if is_valid:
    print("✅ Code compiles successfully!")
else:
    print(f"❌ Build error:\n{message}")
```

---

## Core Concepts

### CPPCodebase

The main entry point for analyzing a C++ project.

```python
codebase = CPPCodebase(
    root_dir="/path/to/project",
    cmake_dir="/path/to/project"  # Optional, defaults to root_dir
)
```

**Properties:**
- `root_dir`: Project root directory
- `cmake_dir`: CMakeLists.txt location
- `parser`: Tree-sitter C++ parser
- `file_index`: Index of all C++ files
- `semantic_analyzer`: Symbol and dependency analyzer
- `modifier`: File modification tracker
- `validator`: CMake build validator

### CodePiece

Represents an extractable code unit (function or class).

```python
piece = CodePiece(
    name="myFunction",
    kind="function",  # or "class", "struct"
    signature="void myFunction(int x)",
    body="{ /* implementation */ }",
    source_file="main.cpp",           # Optional
    line_start=10,                     # Optional
    line_end=15,                       # Optional
    dependencies=["std::vector"],      # Optional
)
```

### Symbol

Represents a C++ symbol (function, class, variable).

```python
symbol = codebase.get_symbol("MyClass")
if symbol:
    print(f"Name: {symbol.name}")
    print(f"Kind: {symbol.kind}")        # function, class, struct, variable
    print(f"Scope: {symbol.scope}")      # global, class, function
    print(f"File: {symbol.file_path}")
    print(f"Lines: {symbol.line_start}-{symbol.line_end}")
```

---

## Common Use Cases

### Use Case 1: Code Analysis & Documentation

Extract all functions in a module to generate documentation.

```python
from cpplib import CPPCodebase
from pathlib import Path

codebase = CPPCodebase("/path/to/project")

# Extract all functions
for func in codebase.get_all_functions():
    print(f"## {func.name}\n")
    print(f"**Signature:** `{func.signature}`\n")
    print(f"**File:** {Path(func.file_path).name}:{func.line_start}\n")
    print(f"**Implementation:**\n```cpp\n{func.body}\n```\n")
```

### Use Case 2: Refactoring - Extract Method

Move a function from one file to another.

```python
# Extract the function
old_func = codebase.extract_function("complexOperation")

if old_func:
    # Create new version (possibly simplified)
    new_func = old_func  # Or modify it
    
    # Insert into new file
    codebase.insert_function("src/utils.cpp", new_func)
    
    # Verify it compiles
    is_valid, msg = codebase.validate()
    
    if is_valid:
        # Delete from old location (future enhancement)
        print("✅ Refactoring complete")
```

### Use Case 3: Dependency Analysis

Understand what a function depends on.

```python
deps = codebase.get_dependencies("processData")

print(f"Function 'processData' depends on:")
for dep_type, dep_list in deps.items():
    if dep_list:
        print(f"  {dep_type}: {dep_list}")

# Get transitive dependencies
transitive = codebase.semantic_analyzer.get_transitive_dependencies("processData")
print(f"Transitive dependencies: {transitive}")
```

### Use Case 4: Code Generation

Generate boilerplate or utility functions.

```python
from cpplib import CodePiece

# Generate a simple getter/setter
def generate_getter(class_name, member_name, member_type):
    return CodePiece(
        name=f"get{member_name.capitalize()}",
        kind="function",
        signature=f"{member_type} {class_name}::get{member_name.capitalize()}() const",
        body="{ return " + member_name + "; }",
    )

# Create getter for 'count' member
getter = generate_getter("MyClass", "count", "int")
codebase.insert_function("src/myclass.cpp", getter)
```

### Use Case 5: Code Validation Pipeline

Check multiple projects for compilation issues.

```python
from pathlib import Path

projects = [
    "/path/to/project1",
    "/path/to/project2",
    "/path/to/project3",
]

for project_path in projects:
    try:
        codebase = CPPCodebase(project_path)
        is_valid, msg = codebase.validate(clean=True)
        
        status = "✅ PASS" if is_valid else "❌ FAIL"
        print(f"{project_path}: {status}")
        
        if not is_valid:
            print(f"  Error: {msg.split(chr(10))[0]}")
    except Exception as e:
        print(f"{project_path}: ⚠️  ERROR - {e}")
```

---

## API Reference

### CPPCodebase Methods

#### Extraction

```python
# Extract function by name
func = codebase.extract_function("functionName")

# Extract class by name
cls = codebase.extract_class("ClassName")

# Get all functions
functions = codebase.get_all_functions()

# Get all classes
classes = codebase.get_all_classes()

# Look up a symbol
symbol = codebase.get_symbol("symbolName")
```

#### Modification

```python
# Insert function into file
success = codebase.insert_function(
    "path/to/file.cpp",
    code_piece,
    after="afterFunction",      # Optional: insert after this marker
    before="beforeFunction"      # Optional: insert before this marker
)

# Insert class into file
success = codebase.insert_class(
    "path/to/file.cpp",
    code_piece,
    after="afterClass",
    before="beforeClass"
)

# Apply all pending modifications
success = codebase.generate()
```

#### Analysis

```python
# Get dependencies of a function
deps = codebase.get_dependencies("functionName")
# Returns: {"types": set(), "functions": set(), "calls": set(), "uses": set()}

# Get all transitive dependencies
transitive = codebase.semantic_analyzer.get_transitive_dependencies("functionName")
```

#### Validation

```python
# Validate code compiles
is_valid, message = codebase.validate(clean=True)

# Check syntax without full compilation
is_valid, message = codebase.validator.check_syntax(cpp_code_string)

# Get build errors
errors = codebase.validator.get_build_errors()

# Get build output
output = codebase.validator.get_build_output()
```

---

## Advanced Examples

### Example 1: Migrate Code Between Versions

Move functions from old API to new API while maintaining compatibility.

```python
from cpplib import CPPCodebase, CodePiece

old_codebase = CPPCodebase("/path/to/old/version")
new_codebase = CPPCodebase("/path/to/new/version")

# Extract old functions
old_functions = old_codebase.get_all_functions()

for old_func in old_functions:
    # Create wrapper in new codebase
    wrapper = CodePiece(
        name=f"{old_func.name}_deprecated",
        kind="function",
        signature=f"{old_func.signature.replace(old_func.name, old_func.name + '_deprecated')}",
        body=f"{{ return {old_func.name}_new(); }}",
        dependencies=old_func.dependencies,
    )
    
    # Insert into compatibility layer
    new_codebase.insert_function("src/compat.cpp", wrapper)

new_codebase.generate()
new_codebase.validate()
```

### Example 2: Find Dead Code

Identify functions that are never called.

```python
from cpplib import CPPCodebase

codebase = CPPCodebase("/path/to/project")

all_functions = codebase.get_all_functions()
dead_code = []

for func in all_functions:
    dependents = codebase.semantic_analyzer.dependency_graph.get_dependents_of(func.name)
    
    if not dependents and func.name != "main":
        dead_code.append(func)

print("Potentially dead code:")
for func in dead_code:
    print(f"  - {func.full_name} at {func.file_path}:{func.line_start}")
```

### Example 3: Auto-Generate Tests

Generate test stubs for all functions.

```python
from cpplib import CPPCodebase, CodePiece

codebase = CPPCodebase("/path/to/project")

test_file_content = "#include <gtest/gtest.h>\n\n"

for func in codebase.get_all_functions():
    if func.name == "main":
        continue
    
    test_func = CodePiece(
        name=f"Test_{func.name}",
        kind="function",
        signature=f"TEST(FunctionTests, {func.name})",
        body="{\n    // TODO: Implement test\n    FAIL() << \"Test not implemented\";\n}",
    )
    
    test_file_content += f"\n{test_func.signature} {test_func.body}\n"

with open("tests/generated_tests.cpp", "w") as f:
    f.write(test_file_content)

print(f"Generated {len(codebase.get_all_functions())} test stubs")
```

### Example 4: Code Statistics & Metrics

Analyze code complexity and metrics.

```python
from cpplib import CPPCodebase
from collections import defaultdict

codebase = CPPCodebase("/path/to/project")

metrics = {
    "total_functions": 0,
    "total_classes": 0,
    "files": set(),
    "avg_lines_per_function": 0,
    "functions_by_file": defaultdict(int),
}

functions = codebase.get_all_functions()
metrics["total_functions"] = len(functions)
metrics["total_classes"] = len(codebase.get_all_classes())

total_lines = 0
for func in functions:
    lines = func.line_end - func.line_start + 1
    total_lines += lines
    metrics["functions_by_file"][func.file_path] += 1
    metrics["files"].add(func.file_path)

metrics["avg_lines_per_function"] = total_lines / len(functions) if functions else 0

print(f"Total Functions: {metrics['total_functions']}")
print(f"Total Classes: {metrics['total_classes']}")
print(f"Source Files: {len(metrics['files'])}")
print(f"Avg Lines per Function: {metrics['avg_lines_per_function']:.1f}")

# Functions per file
print("\nTop 5 files by function count:")
for file, count in sorted(metrics["functions_by_file"].items(), 
                          key=lambda x: x[1], reverse=True)[:5]:
    print(f"  {file}: {count} functions")
```

---

## Troubleshooting

### Issue: "CMakeLists.txt not found"

**Cause:** Validator can't find CMakeLists.txt

**Solution:**
```python
# Specify cmake directory explicitly
codebase = CPPCodebase(
    "/path/to/project",
    cmake_dir="/path/to/project/build"  # If CMakeLists.txt is in build dir
)

# Or disable validation if not needed
# Just don't call codebase.validate()
```

### Issue: "Failed to parse" errors

**Cause:** Syntax errors or unsupported C++ features

**Solution:**
```python
# Check if the file has syntax errors
result = codebase.validator.check_syntax(code)

# Try parsing just the content
try:
    func = codebase.extract_function("myFunc")
except Exception as e:
    print(f"Parsing error: {e}")
    # The file might have syntax errors or use C++20+ features
```

### Issue: Insertion happens at wrong location

**Cause:** Text-based "after" matching is too simple (finds first occurrence)

**Solution:**
```python
# Use more specific markers
codebase.insert_function(
    "file.cpp",
    new_func,
    after="lastFunction() {\n}"  # More specific marker
)

# Or manually edit for complex cases
# Extract, modify, re-insert
```

### Issue: Build validation fails

**Cause:** Modified code doesn't compile

**Solution:**
```python
# Check build errors before committing
is_valid, message = codebase.validate()

if not is_valid:
    print("Build errors:")
    print(message)
    
    # Rollback changes
    codebase.modifier.rollback()
```

---

## Best Practices

1. **Always validate after modifications**
   ```python
   codebase.insert_function(...)
   codebase.generate()
   is_valid, msg = codebase.validate()
   ```

2. **Use dependencies to understand code**
   ```python
   deps = codebase.get_dependencies(func_name)
   # Plan refactorings with full dependency knowledge
   ```

3. **Extract with dependencies**
   ```python
   # Dependencies are automatically collected
   func = codebase.extract_function("myFunc")
   print(func.dependencies)  # All required types and functions
   ```

4. **Backup before large modifications**
   ```python
   import shutil
   shutil.copytree(project_dir, f"{project_dir}_backup")
   ```

5. **Use semantic analysis for refactoring**
   ```python
   # Don't just find-and-replace
   # Use the symbol table to understand scope
   symbol = codebase.get_symbol("name")
   ```

---

## Questions?

- Check GitHub: https://github.com/khalefa-ow/cpplib
- Run tests: `pytest tests/ -v`
- View source: `cpplib/` directory
