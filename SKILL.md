---
name: create-skill
description: 'Help create a reusable workspace skill (SKILL.md) from a multi-step workflow or task.'
argument-hint: Describe the workflow or final outcome the new skill should capture.
disable-model-invocation: true
---

This skill helps convert a repeated development workflow into a reusable skill document.

## When to use
- You want to capture a repeatable process as a skill.
- You need a workspace-scoped helper for debugging, review, or implementation workflows.
- You want to turn a conversational task into a structured `SKILL.md` artifact.

## What this skill does
1. Review the conversation and current workspace context.
2. Identify the workflow steps, decision points, and quality checks.
3. Ask clarifying questions if the goal is unclear.
4. Draft and save a `SKILL.md` file that describes the workflow.
5. Summarize the resulting skill and suggest example prompts.

## Output
- A skill with a clear name, description, and argument hint.
- A structured workflow summary.
- Suggested example prompts for using the new skill.

## Example prompts
- "Create a SKILL.md for reviewing and refactoring a trading bot function."
- "Help me define a skill that captures my bug triage and fix process."
- "Generate a reusable skill for end-to-end Python test creation."

## Notes
- If the workflow is not clearly defined, ask the user for the intended outcome.
- Prefer workspace-scoped skill definitions when the workflow is specific to this repository.
- Keep the final skill concise and actionable.