---
slides:
    title: Agile Code Reviews
    separator_vertical: ^\s*-v-\s*$
revealjs:
    viewDistance: 2
plugins:
    - name: RevealMermaid
      extra_javascript:
          - https://cdn.jsdelivr.net/npm/reveal.js-mermaid-plugin/plugin/mermaid/mermaid.min.js
---

# Agile Code Reviews

---

## What?
> Agile development is an iterative approach to software development emphasizing flexibility, customer collaboration, and rapid delivery of functional software

Notes:
Agile development is an iterative approach to software development emphasizing flexibility, customer collaboration, and rapid delivery of functional software. Agile methodologies, such as Scrum and Kanban, encourage adaptive planning, frequent reassessment of project goals, and regular feedback loops to refine the software continuously.

---

Cool but what about Agile *Code Reviews*?

---

## Code Reviews done Agilely
> Integrating code reviews into Agile workflows enhances software quality and accelerates learning among team members

Notes:
Integrating code reviews into Agile workflows enhances software quality and accelerates learning among team members. Agile code review practices help teams maintain high-quality codebases, reduce technical debt, and respond swiftly to changing requirements.

---

## Task
Implement new tool based on Python, Django, and Nitro

---

## Split a commit
<video data-src="./gerrit.mp4#t=00:00:08,00:00:30" controls="true" onloadedmetadata="this.playbackRate = 1.0"></video>

Notes:
Commit was too big. Asked agent to split it.

---

## re: Split a commit
<video data-src="./gerrit.mp4#t=00:07:35,00:07:55" controls="true" onloadedmetadata="this.playbackRate = 1.0"></video>

Notes:
Commit was too big. Asked agent to split it.

---

## Clarification
<video data-src="./gerrit.mp4#t=00:09:00,00:09:30" controls="true" onloadedmetadata="this.playbackRate = 1.0"></video>

Notes:
Clarification requested on Django version

---

## Refactor
<video data-src="./gerrit.mp4#t=00:12:20,00:15:45" controls="true" onloadedmetadata="this.playbackRate = 3.0"></video>

Notes:
Requested to refactor a couple of methods

---

## re: Clarification & Upgrade
<video data-src="./gerrit.mp4#t=00:18:54,00:19:50" controls="true" onloadedmetadata="this.playbackRate = 3.0"></video>

Notes:
Clarification requested on Django version

Requested upgrade to Django 6

-v-

## Upgrade
<video data-src="./gerrit.mp4#t=00:19:22,00:19:50" controls="true" onloadedmetadata="this.playbackRate = 1.0"></video>

Notes:
Requested upgrade to Django 6

---

## Pause

---

## What is happening???
- Migrate Django 5 -> Django 6
- Refactor jj module
- Ongoing development

Notes:

Editing past, present, and future simultaneously

---


## re: Upgrade
<video data-src="./gerrit.mp4#t=00:39:30,00:40:12" controls="true" onloadedmetadata="this.playbackRate = 1.0"></video>

Notes:
Requested upgrade to Django 6

---

## re: Refactor
<video data-src="./gerrit.mp4#t=00:51:45,00:52:15" controls="true" onloadedmetadata="this.playbackRate = 1.0"></video>

Notes:
Requested to refactor a couple of methods

-v-

## Full Video
<video data-src="./gerrit.mp4#t=00:51:45" controls="true" onloadedmetadata="this.playbackRate = 3.0"></video>

---

## References
- https://graphite.com/guides/adapting-code-review-for-agile
