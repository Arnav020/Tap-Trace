"""Fill the organisers' AI Usage Disclosure form (LangAI3.0_AI_Disclosure.docx) -> submission/.

    python scripts/fill_disclosure.py --template "<path to LangAI3.0_AI_Disclosure.docx>"

Every line is written as "<bold label>: <answer>" so it is clear where the form's label ends and our answer
starts. The signature is intentionally left blank for the team representative to sign by hand.
"""
from __future__ import annotations

import argparse
import copy
from pathlib import Path

import docx
from docx.text.paragraph import Paragraph

ROOT = Path(__file__).resolve().parents[1]

# (label as printed in the template, cleaned label we write, answer)
FIELDS = [
    ("Team Name", "Team Name", "TapTrace (Thapar Institute of Engineering and Technology)"),
    ("Project / Product Name", "Project / Product Name", "TapTrace - Smart Guided Troubleshooting Engine (Theme 02)"),
    ("Organization / Institution (if any)", "Organization / Institution", "Thapar Institute of Engineering and Technology, Patiala"),
    ("Submission Date", "Submission Date", "30 September 2026"),
    ("Idea generation / brainstorming", "Idea generation / brainstorming",
     "Done by the team member. The choice of Theme 02, the problem framing and every final design decision were made by the "
     "team member; Claude Code (Anthropic) was used as a sounding board to discuss options."),
    ("Code generation or assistance", "Code generation or assistance",
     "Yes, assisted. The code was written with Claude Code (Anthropic) as a coding assistant, working under the team member's "
     "direction. The team member set the requirements, reviewed and tested the changes, and decided what was kept."),
    ("UI / UX design", "UI / UX design",
     "Design direction, visual style and all review feedback by the team member; the page was implemented with Claude Code's help."),
    ("Content creation", "Content creation",
     "README, documentation and slides were drafted with Claude Code's help and revised by the team member. The cover photograph "
     "was generated with ChatGPT image generation (OpenAI) and the real product screenshot was placed on its phone. The demo "
     "video was recorded and narrated entirely by the team member."),
    ("Data analysis", "Data analysis",
     "The provided data files were audited with Claude Code's help; the team member reviewed and approved the findings."),
    ("Testing / debugging", "Testing / debugging",
     "Assisted by Claude Code. The team member ran the system, tested the demo and reported the issues that were fixed."),
    ("Other", "Other (AI inside the product)",
     "At runtime the product calls Groq openai/gpt-oss-120b (with openai/gpt-oss-20b as an automatic fallback) for query "
     "enrichment and plan structuring. Troubleshooting steps themselves are never written by the model."),
]

FEATURES = [
    ("Theme selection, requirements and final design decisions", "Self-Generated",
     "Done entirely by the team member: chose Theme 02, set the requirements and quality bar, and made or approved every "
     "design decision (for example which provided scenarios should return no_match). No AI tool was used for these decisions."),
    ("GitHub repository, deployment and demo video", "Self-Generated",
     "Done entirely by the team member: created and managed the GitHub repository (commits, release tag), deployed the "
     "service on Render (https://taptrace.onrender.com/), and recorded and narrated the demo video."),
    ("Troubleshooting engine (grounded step extraction, relevance gate, deeplink resolver, semantic cache)", "Both",
     "Tool: Claude Code (Anthropic), as a coding assistant. Prompts: the team member's requirements taken from the Theme 02 "
     "PDF, plus bug reports and fixes. Output: the taptrace/ package. Modification: reviewed and tested by the team member "
     "against the 20 provided scenarios and a 37-case deeplink benchmark; defects the team member found were fixed."),
    ("REST API, Docker setup, demo page, tests and evaluation scripts", "Both",
     "Tool: Claude Code, as a coding assistant. Output: taptrace/api.py, Dockerfile, demo/index.html, tests/ and scripts/. "
     "Modification: the team member tested the demo, gave the design feedback and approved the final version."),
    ("Presentation and documentation", "Both",
     "Tools: Claude Code for drafting, ChatGPT image generation for the cover photograph. Modification: structure, visual "
     "direction and final review by the team member. All numbers come from the evaluation scripts, not typed by hand."),
]

ETHICS = {"AI usage complies with guidelines and policies.": ("AI usage complies with guidelines and policies", "Yes"),
          "No proprietary or copyrighted data misused.": ("No proprietary or copyrighted data misused", "I Agree")}
SIGN = {"Name of Team Representative:": "Arnav Joshi", "Role:": "Team lead (solo participant)",
        "Signature:": "", "Date:": "30 September 2026"}


def write(p: Paragraph, label: str, value: str) -> None:
    """Replace the paragraph content with a bold label, a colon and the answer in normal weight."""
    for r in list(p.runs):
        r._r.getparent().remove(r._r)
    head = p.add_run(label if label.endswith("?") else label + ":")
    head.bold = True
    if value:
        body = p.add_run(" " + value)
        body.bold = False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    args = ap.parse_args()
    d = docx.Document(args.template)
    P = d.paragraphs

    for p in P:
        t = p.text.strip()
        for key, label, value in FIELDS:
            if t.startswith(key) and "_" in t:
                write(p, label, value)
                break

    for i, p in enumerate(P):
        if p.text.strip().startswith("Did your team use"):
            write(p, "Did your team use any Artificial Intelligence (AI) in developing this project?", "Yes")
            nxt = P[i + 1]
            if set(nxt.text.strip()) <= {"_"}:
                write(nxt, "Tools used", "Claude Code (Anthropic) as a coding and writing assistant under the team member's "
                                         "direction; ChatGPT image generation (OpenAI) for the cover photograph.")
            break

    # feature blocks: the template has 2 blocks of 3 paragraphs; clone the first block as needed
    idx = [i for i, p in enumerate(P) if p.text.strip().startswith("1. Feature Name")]
    blocks = [[P[i], P[i + 1], P[i + 2]] for i in idx]
    anchor = blocks[-1][2]._p
    proto = [copy.deepcopy(x._p) for x in blocks[0]]
    while len(blocks) < len(FEATURES):
        spacer = copy.deepcopy(P[idx[1] - 1]._p)  # the empty paragraph that separates two feature blocks
        anchor.addnext(spacer)
        anchor = spacer
        new = [copy.deepcopy(x) for x in proto]
        for el in new:
            anchor.addnext(el)
            anchor = el
        blocks.append([Paragraph(el, blocks[0][0]._parent) for el in new])
    for n, ((name, origin, desc), (p1, p2, p3)) in enumerate(zip(FEATURES, blocks), 1):
        write(p1, f"Feature {n} - Feature Name", name)
        write(p2, "Self-Generated / AI-Generated / Both", origin)
        write(p3, "Description (AI tools used, prompts, output summary, modification)", desc)
    for p in P:
        if len(p.text.strip()) == 1 and not p.text.strip().isalnum():  # stray symbol glyph in the template: drop it
            p._p.getparent().remove(p._p)

    for p in P:
        t = p.text.strip()
        for key, (label, value) in ETHICS.items():
            if t.startswith(key):
                write(p, label, value)
        if t in SIGN:
            write(p, t.rstrip(":"), SIGN[t])

    out = ROOT / "submission" / "TIET_TapTrace_AI_Disclosure.docx"
    out.parent.mkdir(exist_ok=True)
    d.save(out)
    print("saved", out)


if __name__ == "__main__":
    main()
