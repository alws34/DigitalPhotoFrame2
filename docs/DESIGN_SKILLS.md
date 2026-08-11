# Design Skills

Two frontend/design skill plugins installed for Claude Code:

## taste-skill (leonxlnx)
[GitHub](https://github.com/leonxlnx/taste-skill)

A collection of high-agency frontend skills focused on design taste and visual quality. Skills available:

| Skill name | Purpose |
|---|---|
| `design-taste-frontend` | Main skill — senior UI/UX engineer mode with configurable design variance, motion, and density |
| `minimalist-ui` | Clean, airy, minimal interfaces |
| `industrial-brutalist-ui` | Bold, high-contrast brutalist style |
| `high-end-visual-design` | Luxury/premium visual polish |
| `redesign-existing-projects` | Opinionated redesign of existing UIs |
| `stitch-design-taste` | Stitch together design patterns cohesively |
| `image-to-code` | Convert design screenshots to code |
| `imagegen-frontend-web` / `imagegen-frontend-mobile` | Generate frontend from image prompts |
| `brandkit` | Brand identity and design system creation |
| `gpt-taste` | GPT-style taste application |
| `full-output-enforcement` | Force complete code output (no truncation) |

**Usage:**
```
Use the design-taste-frontend skill to build this component
/design-taste-frontend
```

The main `design-taste-frontend` skill has three tunable globals:
- `DESIGN_VARIANCE` (1–10): symmetry vs artsy chaos, default 8
- `MOTION_INTENSITY` (1–10): static vs cinematic, default 6
- `VISUAL_DENSITY` (1–10): airy vs packed, default 4

Override them inline: *"build this with MOTION_INTENSITY 3"*

---

## emil-design-eng (emilkowalski)
[GitHub](https://github.com/emilkowalski/skill)

Encodes Emil Kowalski's design engineering philosophy: the invisible details that make interfaces feel right — animations, micro-interactions, and craft-level polish.

**Usage:**
```
Use the emil-design-eng skill for this animation
/emil-design-eng
```

Philosophy highlights: taste is trained not innate; animation serves function; every detail compounds. When invoked cold it will introduce itself, then answer questions.

---

## How to trigger skills

Skills activate when you tell Claude to use them — either by name in a message, or with `/skill-name` as a slash command (if configured). Both plugins are installed user-scope and available in all Claude Code sessions.

```
# Inline reference
"Use design-taste-frontend skill to build this settings panel with DESIGN_VARIANCE 6"

# Slash command
/design-taste-frontend

# Ask for Emil's take on an animation decision
/emil-design-eng — should this transition be 200ms or 350ms?
```
