# ICLR paper draft

This directory contains an anonymous ICLR-style LaTeX draft for the SKILLS_EVO project.

- `main.tex`: paper draft.
- `references.bib`: bibliography with several entries still marked for metadata verification.
- `iclr2026_conference.sty`, `iclr2026_conference.bst`, `math_commands.tex`, `natbib.sty`, `fancyhdr.sty`: copied from the official ICLR 2026 template.

The official ICLR 2026 author guide currently points to:

```text
https://github.com/ICLR/Master-Template/raw/master/iclr2026.zip
```

As of this draft, an official ICLR 2027 template was not found. If the target submission year releases a new template, replace the style and bibliography files before submission.

Build:

```bash
cd paper
pdflatex main
bibtex main
pdflatex main
pdflatex main
```

The results tables are intentionally left as `TBD`. Fill them only after the GLM-5.2 Pi/Claude-harness runs and GPT-5.5 transfer runs finish.
