# Text Reporter Guide

The `TextReporter` and `AnalysisReporter` classes provide compact, formatted text output for cpplib analysis results.

## Quick Start

```python
from cpplib import CPPCodebase, TextReporter

# Analyze your C++ project
codebase = CPPCodebase("path/to/src", "path/to/cmake")

# Create a reporter (compact=True for short output)
reporter = TextReporter(compact=True)

# Generate various reports
functions = codebase.get_all_functions()
classes = codebase.get_all_classes()

print(reporter.symbols_summary(functions, classes))
```

## Output Modes

### Compact Mode (default)
```python
reporter = TextReporter(compact=True, max_width=100)
```
Output: Single-line summaries, perfect for CLI tools and logs.

### Expanded Mode
```python
reporter = TextReporter(compact=False)
```
Output: Multi-line detailed reports with aligned columns.

## Available Reports

### 1. Symbols Summary
List all functions and classes in the codebase.

```python
functions = codebase.get_all_functions()
classes = codebase.get_all_classes()
print(reporter.symbols_summary(functions, classes))
```

**Output:**
```
Functions (7): main, toUpperCase, toLowerCase, split, trim, isNumeric, reverse
Classes (1): StringUtil
```

### 2. Codebase Statistics
Overview of the project size.

```python
files = codebase.file_index.list_files()
print(reporter.codebase_statistics(
    len(functions), len(classes), len(files)
))
```

**Output:**
```
Stats: 3 files | 7 functions | 1 classes
```

### 3. Code Piece Summary
Details about a specific function or class.

```python
piece = codebase.extract_function("myFunction")
print(reporter.code_piece_summary(piece))
```

**Compact Output:**
```
FUNCTION: myFunction @ file.cpp:10-20 | deps:3
```

**Expanded Output:**
```
Name:       myFunction
Kind:       function
File:       /path/to/file.cpp
Lines:      10-20
Signature:  int myFunction(int a, int b)
Dependencies (3):
  - stdio.h
  - stdlib.h
  - myhelper.h
```

### 4. Dependency Report
Show what a symbol depends on.

```python
deps = codebase.get_dependencies("myFunction")
print(reporter.dependency_report("myFunction", deps))
```

**Output:**
```
Dependencies for 'myFunction':
  types: int, double
  calls: helper1, helper2
```

### 5. Validation Result
Report on compilation success/failure.

```python
success, msg = codebase.validate()
print(reporter.validation_result(success, msg))
```

**Output:**
```
✓ PASS: Build successful
```

or

```
✗ FAIL: Build failed
  Error: expected ';' before '}' token
```

### 6. Extraction Summary
List multiple extracted code pieces.

```python
pieces = [
    codebase.extract_function("func1"),
    codebase.extract_function("func2"),
    codebase.extract_class("MyClass"),
]
pieces = [p for p in pieces if p]
print(reporter.extraction_summary(pieces))
```

**Output:**
```
Extracted 3 pieces:
  • func1 (function) @ file.cpp:10
  • func2 (function) @ file.cpp:20
  • MyClass (class) @ file.h:5
```

### 7. Comparison Table
Display items in an aligned table.

```python
data = [
    {"Name": "func1", "Kind": "function", "Lines": "10"},
    {"Name": "func2", "Kind": "function", "Lines": "15"},
]
print(reporter.comparison_table(data, ["Name", "Kind", "Lines"]))
```

**Output:**
```
Name  | Kind     | Lines
------+----------+------
func1 | function | 10   
func2 | function | 15   
```

## High-Level Reporting

Use `AnalysisReporter` for complete analysis summaries.

```python
from cpplib import AnalysisReporter

analysis = AnalysisReporter(codebase, compact=True)

# Full analysis report
print(analysis.full_analysis())

# Detailed symbol report
print(analysis.symbol_details("myFunction"))
```

## Configuration

### Max Width
Control output width for compact mode:

```python
reporter = TextReporter(compact=True, max_width=120)
```

Text longer than `max_width` will be truncated with "...".

### Compact vs Expanded
Switch between modes based on use case:

- **Compact**: CLI tools, logs, one-liners
- **Expanded**: Human review, documentation, detailed analysis

## Example Workflow

```python
from cpplib import CPPCodebase, TextReporter

# 1. Analyze codebase
codebase = CPPCodebase("src", ".")
reporter = TextReporter(compact=True)

# 2. Print overview
print("📊 Analysis Summary:")
functions = codebase.get_all_functions()
classes = codebase.get_all_classes()
files = codebase.file_index.list_files()
print(reporter.codebase_statistics(len(functions), len(classes), len(files)))

# 3. Validate compilation
print("\n🔨 Build Validation:")
success, msg = codebase.validate()
print(reporter.validation_result(success, msg))

# 4. Extract and report
print("\n📦 Extracted Pieces:")
if success:
    piece = codebase.extract_function("main")
    if piece:
        print(reporter.code_piece_summary(piece))
        deps = codebase.get_dependencies("main")
        print(reporter.dependency_report("main", deps))
```

## See Also

- `demo_reporter.py` - Full usage examples
- `demo.py` - Analysis examples
- `demo_validation.py` - Validation examples
