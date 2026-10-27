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
### of AI-generated code

---

## What?
> Agile development is an iterative approach to software development emphasizing flexibility, customer collaboration, and rapid delivery of functional software

Notes:
Agile development is an iterative approach to software development emphasizing flexibility, customer collaboration, and rapid delivery of functional software. Agile methodologies, such as Scrum and Kanban, encourage adaptive planning, frequent reassessment of project goals, and regular feedback loops to refine the software continuously.

---

Cool but what are Agile *Code Reviews*?

---

## Code Reviews done Agilely
> Agile code review is a lightweight, iterative practice where developers examine small, incremental code changes to maintain quality and share knowledge without disrupting rapid development cycles

Notes:

Mention potential improvements
- Smaller code reviews
- Earlier start
- Earlier insights into potential design direction
- Possibly uniquely suited for PODs

We may qualify as being "Agile" but we are not doing Agile code reviews

The goal should be to get feedback as early into the dev process as possible

I wanted to investigate developing code and doing the review in parallel

---

## Task
Implement new tool based on Python, Django, and Nitro

---

## Split a commit
<video data-src="./gerrit.mp4#t=00:00:08,00:00:30" controls="true" onloadedmetadata="this.playbackRate = 1.0"></video>

Notes:
I'm using OpenCode, Jujutsu, and Gerrit
Gerrit is on the left, Opencode is on the right
Jujutsu is managing VCS (commits and transmission to Gerrit)

Recorded hour long video of me doing development

//

Point out that this is the first commit of the project

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

## Sold? Now what?
- Mutable commits
- Interdiff provider
- How to integrate in current processes

Notes:

- Mutable commits comes with the cost of education to the team
- For the demo, I've used Gerrit but it's support is still experimental in Jujutsu
- What does this look like in the greater SDLC?

---

## Conclusions
Agile Code Reviews give us
- Smaller code reviews
- Earlier insights into potential design direction
- Reduced time to deployment

---

## References
- https://graphite.com/guides/adapting-code-review-for-agile
