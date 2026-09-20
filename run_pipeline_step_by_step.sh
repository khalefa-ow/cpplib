#!/bin/bash
# Interactive step-by-step runner for the agent pipeline

set -e

# Load environment variables from .env file
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Parse arguments
CONFIG="agent/examples/config.example.json"
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --dry)
            DRY_RUN=true
            shift
            ;;
        --config)
            CONFIG="$2"
            shift 2
            ;;
        *)
            CONFIG="$1"
            shift
            ;;
    esac
done
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Agent Pipeline Step-by-Step Runner ===${NC}\n"
echo "Config: $CONFIG"
echo ""

# Helper function to run a stage with user confirmation
run_stage() {
    local stage=$1
    local description=$2

    echo -e "${YELLOW}>>> Stage: $stage${NC}"
    echo "$description"
    echo ""

    read -p "Run this stage? (y/n) " -n 1 -r
    echo

    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${GREEN}Running $stage...${NC}\n"
        uv run python3 -m agent.cli run --config "$CONFIG" --stages "$stage"
        echo -e "\n${GREEN}✓ $stage completed${NC}\n"
        return 0
    else
        echo -e "${YELLOW}Skipped $stage${NC}\n"
        return 1
    fi
}

# Dry run first
echo -e "${YELLOW}>>> Pre-flight checks${NC}"
echo "Validating config and environment..."
echo ""
uv run python3 -m agent.cli doctor || { echo "Failed! Install missing tools and try again."; exit 1; }
echo ""

if [ "$DRY_RUN" = false ]; then
    read -p "Rebuild prompts from source? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        uv run python3 -m agent.cli prompts build
        echo ""
    fi
else
    echo "(skipped prompts rebuild - dry run mode)"
fi

# Show config
echo -e "${YELLOW}>>> Config Overview${NC}"
if [ "$DRY_RUN" = false ]; then
    uv run python3 -m agent.cli config show --config "$CONFIG" | head -100
    echo ""

    read -p "Show full config? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        uv run python3 -m agent.cli config show --config "$CONFIG"
        echo ""
    fi
else
    echo "Config: $CONFIG"
    echo "(full config display skipped - dry run mode)"
    echo ""
fi

# Dry run
if [ "$DRY_RUN" = true ]; then
    echo -e "${GREEN}Running dry-run (no API calls)...${NC}\n"
    uv run python3 -m agent.cli run --config "$CONFIG" --dry-run
    echo ""
    echo "Dry-run complete. Exiting."
    exit 0
else
    read -p "Run dry-run first? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${GREEN}Running dry-run (no API calls)...${NC}\n"
        uv run python3 -m agent.cli run --config "$CONFIG" --dry-run
        echo ""
    fi
fi

# Stage 1: storage_plan
run_stage "storage_plan" \
    "1️⃣  Storage Planning - Analyzes schema and queries to design storage layout
   Input: schema.txt, queries.txt
   Output: artifacts/storage_plan.json"
if [ $? -eq 0 ]; then
    read -p "View the storage plan? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        uv run python3 -m agent.cli config show --config "$CONFIG" | grep artifacts_dir
        # Extract artifacts dir from config
        artifacts_dir=$(uv run python3 -c "import json; c=json.load(open('$CONFIG')); print(c.get('common', {}).get('artifacts_dir', 'out/artifacts'))")
        if [ -f "$artifacts_dir/storage_plan.json" ]; then
            echo ""
            cat "$artifacts_dir/storage_plan.json" | jq . 2>/dev/null | head -80
        fi
    fi
fi
echo ""

# Stage 2: divide
run_stage "divide" \
    "2️⃣  Schema Division - Splits storage plan into levels for ablation study
   (no hints → organization hints → all hints)
   Input: storage_plan.json
   Output: artifacts/schema_levels.json"
if [ $? -eq 0 ]; then
    read -p "View schema levels? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        artifacts_dir=$(uv run python3 -c "import json; c=json.load(open('$CONFIG')); print(c.get('common', {}).get('artifacts_dir', 'out/artifacts'))")
        if [ -f "$artifacts_dir/schema_levels.json" ]; then
            cat "$artifacts_dir/schema_levels.json" | jq . 2>/dev/null | head -80
        fi
    fi
fi
echo ""

# Stage 3: hppgen
run_stage "hppgen" \
    "3️⃣  Header Generation - Generates C++ storage layout headers and compiles them
   Input: schema_levels.json
   Output: storage_layout_*.hpp in gen_project_root/include/"
if [ $? -eq 0 ]; then
    read -p "View generated header? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        gen_root=$(uv run python3 -c "import json; c=json.load(open('$CONFIG')); print(c.get('common', {}).get('gen_project_root', 'out/gen'))")
        header_file=$(ls "$gen_root/include/storage_layout_"*.hpp 2>/dev/null | head -1)
        if [ -f "$header_file" ]; then
            echo "File: $header_file"
            head -80 "$header_file"
        fi
    fi
fi
echo ""

# Stage 4: query_codegen
run_stage "query_codegen" \
    "4️⃣  Query Code Generation - Generates and verifies C++ query implementations using DuckDB
   Input: Headers from hppgen, schema levels
   Output: Generated .cpp files, correctness_report.json"
if [ $? -eq 0 ]; then
    read -p "View correctness report? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        artifacts_dir=$(uv run python3 -c "import json; c=json.load(open('$CONFIG')); print(c.get('common', {}).get('artifacts_dir', 'out/artifacts'))")
        if [ -f "$artifacts_dir/correctness_report.json" ]; then
            cat "$artifacts_dir/correctness_report.json" | jq . 2>/dev/null | head -100
        fi
    fi
fi
echo ""

# Stage 5: optimize
run_stage "optimize" \
    "5️⃣  Optimization - Proposes and measures performance improvements using query traces
   Input: Verified queries from query_codegen
   Output: optimization_report.json"
if [ $? -eq 0 ]; then
    read -p "View optimization report? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        artifacts_dir=$(uv run python3 -c "import json; c=json.load(open('$CONFIG')); print(c.get('common', {}).get('artifacts_dir', 'out/artifacts'))")
        if [ -f "$artifacts_dir/optimization_report.json" ]; then
            cat "$artifacts_dir/optimization_report.json" | jq . 2>/dev/null | head -100
        fi
    fi
fi
echo ""

# Summary
echo -e "${GREEN}=== Pipeline Overview ===${NC}"
echo "Artifacts directory:"
artifacts_dir=$(uv run python3 -c "import json; c=json.load(open('$CONFIG')); print(c.get('common', {}).get('artifacts_dir', 'out/artifacts'))")
ls -lh "$artifacts_dir" 2>/dev/null || echo "(not yet created)"
echo ""
echo "Generated C++ directory:"
gen_root=$(uv run python3 -c "import json; c=json.load(open('$CONFIG')); print(c.get('common', {}).get('gen_project_root', 'out/gen'))")
ls -lh "$gen_root" 2>/dev/null || echo "(not yet created)"
echo ""

echo -e "${GREEN}Pipeline complete! Next steps:${NC}"
echo "  1. Review the generated artifacts in: $artifacts_dir"
echo "  2. Check the correctness and optimization reports"
echo "  3. Iterate on storage layout and query optimization"
echo ""
echo "📚 Documentation:"
echo "  - Full guide: STEP_BY_STEP.md"
echo "  - Detailed docs: agent/USAGE.md"
echo "  - Configuration: agent/examples/config.example.json"
