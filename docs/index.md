
*or, what happened when a cyber defender red-teamed his own job hunt*

Open your professional feed of choice and time how long it takes to hit a post that begins with "I am humbled and honored." Then count the posts built out of single sentences sitting alone on their own lines.

For emphasis.

Because impact.

That house style has a source, and most of the time the source is a large language model running warm with its self-awareness switched off. So rather than feed the machine another generic profile, I pointed my day job at the problem. What follows is the writeup, delivered in the exact format every hiring manager swears they want.

## Situation

I needed to run a real job search while working full time in security operations. The obvious move in 2026 is to paste your resume and a job description into a chatbot and let it "tailor" the result. I tried it once. The output was fluent, confident, and about sixty percent invention. It gave me accomplishments I never had and a tone that belonged to no human I have ever met.

That is the core issue with the popular approach. It does not just risk sounding synthetic. It manufactures experience, and the person most likely to get caught by that is the candidate who trusted it.

In my field, a system that generates plausible output you cannot verify is called liability

## Task

I wanted the boring parts automated and the important part left alone. Specifically:

- Source roles from real employers, filter to my actual criteria, and never touch a staffing-agency repost.
- Match my genuine experience to each job description without inventing anything.
- Catch language that reads like a model wrote it, before a recruiter does.
- Keep a human, me, at every decision that matters.

The constraint was simple. Nothing in the final document could be something I did not actually do. Everything else was fair game for automation.

## Action

The project, named **gauntlet**, is built around one assumption: the AI output is the least trustworthy component in the pipeline, so the whole design exists to contain it.

**It does not write bullets. It selects them.** Every accomplishment lives in a library I wrote by hand from real work, each entry tagged with the evidence behind it. The system's job is retrieval and ranking against a specific job description, not composition. Generation invents. Selection cannot invent something that is not already in the library.

```yaml
# master_bullets.yaml  (representative shape - swap in your real entry)
- id: detect-tuning-01
  text: "Reduced false-positive volume on a high-noise detection by scoping cardinality to the queried record only."
  evidence: "change request CR-xxxx, validation run attached"
  tags: [detection-engineering, splunk, tuning]
```

**Then it tries to reject me.** A layer of adversarial agents reviews each tailored draft with one instruction: find the reason a recruiter would pass. One flags language patterns typical of machine writing. Others probe for weak fit, vague claims, and keyword theater aimed at an applicant tracking system rather than a person. Anything that trips a check comes back to me for a manual rewrite. I red-team my own application before anyone else gets the chance.

**Nothing auto-submits.** The pipeline stops at a human gate every time. This is the same principle as an approval-gated playbook in security orchestration, automation, and response tooling: let the machine do the tedious collection and enrichment, and keep a person on the trigger. I do not let automation make the call in the operations center, and I am not about to let it make the call on my own name.

## Result

I ran a real search on it. The volume of low-quality applications I sent dropped, because the filter gate killed the reposts and the agency spam before I ever saw them. The documents that went out were tighter, matched the role, and, most importantly, were true.

The part I did not expect was the interview conversation. When someone asks whether I used artificial intelligence on my application, I get to say yes, and then explain that the interesting part is everything I built to distrust it. That answer tends to land better than a denial would.

## What it is bad at

Full disclosure. I'm not perfect and I have never called myself a software engineer, nor do I want to.

It is still fragile at the sourcing layer. Employer career sites change their structure constantly, and an adapter that worked last month could die the next. The bullet library is only as good as the discipline I put into maintaining it. In life things that are not maintained will drift. And the language-pattern detector has its own false positives. Sometimes I write a plain sentence and it flags it because plain and synthetic occasionally look alike from the outside.

None of that bothers me much. A tool that fails loudly and stops for a human is the kind of failure I can work with.

## The point

The most defensible way to use a language model in a job hunt turned out to be building something that assumes the model is wrong until proven otherwise. That is not a hot take. For anyone who works in Cyber Defense this is just a regular tuesday.

The repository is here. It will not humble or honor you, but it might filter your inbox.
