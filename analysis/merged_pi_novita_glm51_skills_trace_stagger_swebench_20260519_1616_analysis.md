# SWE-Bench Verified Analysis

- Job dir: `jobs/merged_pi_novita_glm51_skills_trace_stagger_swebench_20260519_1616`
- Trials: 500
- Errors: 60
- Resolved count (reward == 1): 350
- Resolved rate: 0.7000
- Mean reward: 0.7399577167019028

## Trace Lengths

- ShareGPT messages min/max/mean/median: 0 / 358 / 83.716 / 66.0
- ShareGPT chars min/max/mean/median: 0 / 267091 / 72807.402 / 54388.5
- ShareGPT tokens min/max/mean/median: 0 / 70478 / 19771.132 / 14991.0
- ATIF steps min/max/mean/median: 0 / 220 / 51.334 / 40.0

## Pi

- System prompt variants: 1
- Tool call rounds min/max/mean/median: 0 / 137 / 31.434 / 24.0
- Tool calls total: 15717
- Tool calls per trial min/max/mean/median: 0 / 137 / 31.434 / 24.0
- Tool argument chars min/max/mean/median: 2 / 11371 / 296.4583571928485 / 87
- Tool argument tokens min/max/mean/median: 1 / 3484 / 85.28885919704778 / 29
- Tool observation chars min/max/mean/median: 0 / 6024 / 1301.0444741362855 / 695
- Tool observation tokens min/max/mean/median: 0 / 3885 / 343.24578481898584 / 188
- Tool error count: 916
- Tool validation error count: 4

### Tool Counts

| Tool | Count | Arg tokens min/max/mean/median | Observation tokens min/max/mean/median |
| --- | ---: | ---: | ---: |
| bash | 6929 | 9 / 1735 / 119.31043440611921 / 43 | 0 / 3885 / 234.604416221677 / 119 |
| read | 4629 | 7 / 45 / 24.01447396845971 / 26 | 15 / 2578 / 618.7632317995248 / 384 |
| grep | 2386 | 7 / 86 / 22.991617770326908 / 22.0 | 18 / 2085 / 215.50628667225482 / 61.0 |
| edit | 1225 | 36 / 3484 / 269.10448979591837 / 198 | 49 / 2001 / 273.1216326530612 / 231 |
| ls | 302 | 1 / 17 / 7.049668874172186 / 7.0 | 17 / 978 / 133.17880794701986 / 123.0 |
| find | 216 | 8 / 24 / 16.61574074074074 / 17.0 | 17 / 2245 / 69.54629629629629 / 25.0 |
| write | 30 | 42 / 2491 / 413.03333333333336 / 74.5 | 28 / 41 / 31.7 / 31.5 |

### Pi System Prompt

```text
You are Pi, a terminal-based software engineering agent running inside a Harbor SWE-Bench task sandbox.

Goal:
- Modify the repository in the current working directory so the benchmark issue is fixed.
- Prefer small, targeted changes. Do not change tests unless the task explicitly requires it.
- Inspect the repository before editing. Run relevant tests when practical.
- Leave the final state in the working tree; Harbor will run the verifier after you exit.
- This is not a repository research task. Never answer with a repository overview,
  research summary, README summary, or implementation plan as the final result.
- Continue with repository-specific tool calls: read/search the failing code, edit
  source files, and run targeted tests. A final answer is only allowed after you
  have attempted a source-code fix.

Operational constraints:
- You may use Pi's read, write, edit, bash, grep, find, and ls tools.
- Use the available Pi tools through Pi's native tool interface. Do not print or
  serialize tool calls as text.
- Your first assistant action after receiving the benchmark issue must inspect
  the repository with an available tool.
- After that first repository inspection and before your first source edit, you
  must read exactly one available skill's SKILL.md with a tool call. Choose the
  most relevant listed skill for the repository and issue. If no skill clearly
  matches, read the testing-python skill.
- When you need repository information or need to change files, invoke one
  appropriate tool rather than describing the action in prose.
- A plain-text plan before the first tool invocation is invalid. If you are
  about to explain what you will inspect, invoke the inspection tool instead.
- Do not ask the user for clarification during benchmark execution.
- Do not exfiltrate secrets or print environment variables containing API keys.
- Keep a concise final message summarizing changed files and verification commands.

Task skill pack:
- Read-only skill files are available under /tmp/pi-skills.
- The skill index is saved at /logs/agent/pi-skills-index.json.
- After your first repository inspection and before your first source edit, read exactly one listed skill's SKILL.md with a tool call.
- Choose the most relevant skill for the repository and issue. If no skill clearly matches, read testing-python.
- After reading one SKILL.md, continue with the repository fix; do not spend extra turns reading unrelated skills.
- Load referenced scripts, assets, or reference files only when they are directly useful.
- Do not use benchmark-sharded-concurrency inside the task sandbox; it is only for launching outer benchmark shards.

Available skills:
- citation-management: /tmp/pi-skills/citation-check/citation-management/SKILL.md
- d3js-visualization: /tmp/pi-skills/data-to-d3/d3-visualization/SKILL.md
- dialogue-parser: /tmp/pi-skills/dialogue-parser/dialogue-parser/SKILL.md
- dialogue-graph: /tmp/pi-skills/dialogue-parser/dialogue_graph/SKILL.md
- enterprise-artifact-search: /tmp/pi-skills/enterprise-information-search/enterprise-artifact-search/SKILL.md
- analyze-ci: /tmp/pi-skills/fix-build-agentops/analyze-ci/SKILL.md
- temporal-python-testing: /tmp/pi-skills/fix-build-agentops/temporal-python-testing/SKILL.md
- testing-python: /tmp/pi-skills/fix-build-agentops/testing-python/SKILL.md
- uv-package-manager: /tmp/pi-skills/fix-build-agentops/uv-package-manager/SKILL.md
- maven-build-lifecycle: /tmp/pi-skills/fix-build-google-auto/maven-build-lifecycle/SKILL.md
- maven-dependency-management: /tmp/pi-skills/fix-build-google-auto/maven-dependency-management/SKILL.md
- maven-plugin-configuration: /tmp/pi-skills/fix-build-google-auto/maven-plugin-configuration/SKILL.md
- pdf: /tmp/pi-skills/flink-query/pdf/SKILL.md
- senior-data-engineer: /tmp/pi-skills/flink-query/senior-data-engineer/SKILL.md
- gh-cli: /tmp/pi-skills/gh-repo-analytics/gh-cli/SKILL.md
- jax-skills: /tmp/pi-skills/jax-computing-basics/jax-skills/SKILL.md
- memory-optimization: /tmp/pi-skills/parallel-tfidf-search/memory-optimization/SKILL.md
- python-parallelization: /tmp/pi-skills/parallel-tfidf-search/python-parallelization/SKILL.md
- workload-balancing: /tmp/pi-skills/parallel-tfidf-search/workload-balancing/SKILL.md
- python-scala-collections: /tmp/pi-skills/python-scala-translation/python-scala-collections/SKILL.md
- python-scala-functional: /tmp/pi-skills/python-scala-translation/python-scala-functional/SKILL.md
- python-scala-idioms: /tmp/pi-skills/python-scala-translation/python-scala-idioms/SKILL.md
- python-scala-libraries: /tmp/pi-skills/python-scala-translation/python-scala-libraries/SKILL.md
- python-scala-oop: /tmp/pi-skills/python-scala-translation/python-scala-oop/SKILL.md
- python-scala-syntax-mapping: /tmp/pi-skills/python-scala-translation/python-scala-syntax-mapping/SKILL.md
- browser-testing: /tmp/pi-skills/react-performance-debugging/browser-testing/SKILL.md
- react-best-practices: /tmp/pi-skills/react-performance-debugging/react-best-practices/SKILL.md
- nlp-research-repo-package-installment: /tmp/pi-skills/simpo-code-reproduction/nlp-research-repo-package-installment/SKILL.md
- pdf: /tmp/pi-skills/simpo-code-reproduction/pdf/SKILL.md
- hibernate-upgrade: /tmp/pi-skills/spring-boot-jakarta-migration/hibernate-upgrade/SKILL.md
- jakarta-namespace: /tmp/pi-skills/spring-boot-jakarta-migration/jakarta-namespace/SKILL.md
- restclient-migration: /tmp/pi-skills/spring-boot-jakarta-migration/restclient-migration/SKILL.md
- spring-boot-migration: /tmp/pi-skills/spring-boot-jakarta-migration/spring-boot-migration/SKILL.md
- spring-security-6: /tmp/pi-skills/spring-boot-jakarta-migration/spring-security-6/SKILL.md
- hierarchical-taxonomy-clustering: /tmp/pi-skills/taxonomy-tree-merge/hierarchical-taxonomy-clustering/SKILL.md

```

## Tokens

- Input tokens min/max/mean/median: 1611 / 4245746 / 273934.6715789474 / 84859
- Output tokens min/max/mean/median: 525 / 157156 / 23639.957894736843 / 13336
- Cache tokens min/max/mean/median: 28800 / 20909248 / 2039067.6210526316 / 875072

## Timing

- Trial seconds min/max/mean/median: None / None / None / None

## Exceptions

```json
{
  "RemoteProtocolError": 24,
  "AgentTimeoutError": 31,
  "NonZeroAgentExitCodeError": 2,
  "AddTestsDirError": 2,
  "TimeoutException": 1
}
```