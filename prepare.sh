# 1. Clean up old bundle files if they exist
rm -f python_codebase.md project_context.md data_schemas.md

# 2. Bundle all active Python source files into a single annotated Markdown document
find . -maxdepth 1 -type f -name "*.py" ! -name ".*" -exec sh -c 'echo "=== File: {} ===" && cat "{}" && echo -e "\n"' \; > python_codebase.md

# 3. Bundle architecture documentation, rules, and skills
{
  [ -f CLAUDE.md ] && echo "=== File: CLAUDE.md ===" && cat CLAUDE.md && echo -e "\n"
  [ -f requirements.txt ] && echo "=== File: requirements.txt ===" && cat requirements.txt && echo -e "\n"
  [ -f .claude/rules/shopping.md ] && echo "=== File: .claude/rules/shopping.md ===" && cat .claude/rules/shopping.md && echo -e "\n"
  [ -f .claude/skills/openrouter-model-choice/SKILL.md ] && echo "=== File: .claude/skills/openrouter-model-choice/SKILL.md ===" && cat .claude/skills/openrouter-model-choice/SKILL.md && echo -e "\n"
} > project_context.md

# 4. Generate structural schema previews for JSON configuration and state files (first 35 lines each)
{
  for json_file in config.json models.json week_plan.json meal_history.json; do
    if [ -f "$json_file" ]; then
      echo "=== Sample Structure: $json_file ==="
      head -n 35 "$json_file"
      echo -e "\n"
    fi
  done

  # Pydantic models whose fields are optional or nested past the head -35
  # cutoff above (e.g. week_plan.json's sunday_prep_session) never appear in
  # the sample previews, so dump their real schema straight from the source
  # of truth instead of hoping a sample file happens to populate them.
  echo "=== Model Schema: WeekPlan.sunday_prep_session (planner.SundayPrepSession) ==="
  python3 -c "import json, planner; print(json.dumps(planner.SundayPrepSession.model_json_schema(), indent=2))"
  echo -e "\n"
} > data_schemas.md
